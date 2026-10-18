"""Fresh chunk generations: audit before writes, verify every point before ready."""

from collections.abc import Sequence
from copy import deepcopy
from math import hypot, isfinite
from time import monotonic
from typing import cast
from uuid import UUID

from src.components.corpus.json import JSONVacancyReader
from src.components.embedding.chunks import ChunkVacancyText, VacancyPassage
from src.components.embedding.encoder import TextEmbedder
from src.components.reporting.artifacts.vector import VectorIndexArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.components.reporting.exporters.json import JSONExporter
from src.domain import JSONScalar, JSONValue
from src.domain.document.vacancy import Vacancy
from src.domain.serialization import canonical_bytes
from src.experiments.indexing.vector import VectorIndexExperiment, corpus_digest
from src.integrations.qdrant.chunks import ChunkQdrantIntegration, ChunkQdrantSource


class ChunkIndexExperiment(VectorIndexExperiment):
    def __init__(
        self,
        reader: JSONVacancyReader,
        renderer: ChunkVacancyText,
        embedder: TextEmbedder,
        integration: ChunkQdrantIntegration,
        source: ChunkQdrantSource,
        collection: str,
        exporters: Sequence[BaseExporter] = (),
    ) -> None:
        if (
            not isinstance(renderer, ChunkVacancyText)
            or not isinstance(source, ChunkQdrantSource)
            or not isinstance(integration, ChunkQdrantIntegration)
        ):
            raise ValueError("Chunk indexing requires explicit v2 components")
        super().__init__(
            reader, renderer, embedder, integration, source, collection, exporters
        )
        self.renderer: ChunkVacancyText = renderer
        self.source: ChunkQdrantSource = source
        self.integration: ChunkQdrantIntegration = integration

    def _chunk_preflight(
        self,
    ) -> tuple[
        tuple[Vacancy, ...],
        tuple[tuple[Vacancy, VacancyPassage], ...],
        tuple[tuple[str, ...], ...],
        list[dict[str, JSONValue]],
    ]:
        for exporter in self.exporters:
            if isinstance(exporter, JSONExporter):
                if exporter.path.exists():
                    raise FileExistsError("Chunk receipt already exists")
                if not exporter.path.parent.is_dir():
                    raise ValueError("Chunk receipt directory does not exist")
        documents = tuple(deepcopy(self.reader.read()))
        if not documents:
            raise ValueError("Chunk corpus cannot be empty")
        seen_documents, seen_points = set(), set()
        chunks: list[tuple[Vacancy, VacancyPassage]] = []
        audits = []
        for document in documents:
            if (
                not isinstance(document, Vacancy)
                or not isinstance(document.id, str)
                or not document.id.strip()
                or document.id in seen_documents
            ):
                raise ValueError("Invalid or duplicate chunk corpus document")
            seen_documents.add(document.id)
            self.source.validate_fields(document.fields)
            passages, audit = self.renderer.render_chunks(document, self.embedder)
            self._coverage(document, passages)
            audits.append(audit)
            for passage in passages:
                payload = self._payload(document, passage)
                self.source.validate_payload(payload)
                self.integration.validate_payload(payload)
                identifier = self.source.point_id(document.id, passage.fragment())
                if identifier in seen_points:
                    raise ValueError("Colliding fragment point identities")
                seen_points.add(identifier)
                chunks.append((document, passage))
        batches = tuple(
            self.embedder.bounded_document_batches(p.text for _, p in chunks)
        )
        return documents, tuple(chunks), batches, audits

    def _payload(
        self, document: Vacancy, passage: VacancyPassage
    ) -> dict[str, JSONValue]:
        return {
            "document_id": document.id,
            "fields": document.fields,
            "fragment": passage.fragment(),
        }

    def _coverage(self, document: Vacancy, passages: Sequence[VacancyPassage]) -> None:
        _, body = self.renderer.texts(document)
        if (
            not passages
            or passages[0].start != 0
            or passages[-1].stop != len(body.encode("utf-8"))
            or any(p.ordinal != i for i, p in enumerate(passages))
        ):
            raise ValueError("Incomplete chunk coverage or ordinals")
        for previous, current in zip(passages, passages[1:]):
            if (
                not previous.start < current.start <= previous.stop
                or current.stop <= previous.stop
            ):
                raise ValueError("Chunk coverage has gap or failed progress")

    def _chunk_read_back(
        self, chunks: Sequence[tuple[Vacancy, VacancyPassage]]
    ) -> tuple[float, ...]:
        first_vector = None
        for offset in range(0, len(chunks), 8):
            batch = chunks[offset : offset + 8]
            expected = {
                self.source.point_id(d.id, p.fragment()): self._payload(d, p)
                for d, p in batch
            }
            records = self.integration.retrieve(self.collection, list(expected))
            if len(records) != len(expected):
                raise RuntimeError("Incomplete full fragment read-back")
            seen = set()
            for record in records:
                identifier = str(UUID(str(record.get("id"))))
                if identifier in seen or identifier not in expected:
                    raise RuntimeError("Unexpected full fragment read-back id")
                seen.add(identifier)
                payload = record.get("payload")
                if canonical_bytes(payload) != canonical_bytes(expected[identifier]):
                    raise RuntimeError(
                        "Stored fragment differs from audited corpus snapshot"
                    )
                self.source.validate_payload(cast(dict, payload))
                vector = record.get("vector")
                if (
                    not isinstance(vector, list)
                    or len(vector) != self.source.profile.dimensions
                    or any(
                        type(value) not in (int, float)
                        or not isfinite(cast(float, value))
                        or abs(cast(float, value)) > 3.4028234663852886e38
                        for value in vector
                    )
                    or abs(hypot(*cast(list[float], vector)) - 1) > 1e-3
                ):
                    raise RuntimeError("Invalid stored chunk vector")
                if first_vector is None:
                    first_vector = tuple(float(v) for v in cast(list[float], vector))
        if first_vector is None:
            raise RuntimeError("Chunk collection contains no vector")
        return first_vector

    def _chunk_smoke(
        self, documents: Sequence[Vacancy], vector: tuple[float, ...]
    ) -> None:
        body: dict[str, JSONValue] = {
            "query": list(vector),
            "params": {"exact": True, "indexed_only": False},
            "limit": 1,
            "with_payload": True,
            "with_vector": False,
        }
        selected = None
        for field in sorted(self.source.filter_fields):
            for document in sorted(documents, key=lambda item: item.id.encode("utf-8")):
                value = self.source.field_value(document.fields, field)
                if value is not None:
                    selected = (field, value)
                    body["filter"] = {"must": [self.source.condition(field, value)]}
                    break
            if selected:
                break
        expect_match = True
        if selected is None and self.source.filter_fields:
            field = sorted(self.source.filter_fields)[0]
            declaration = cast(dict, self.source.filter_fields[field])
            placeholders: dict[str, JSONScalar] = {
                "keyword": "recsys-chunk-smoke-v2",
                "bool": True,
                "integer": 0,
                "float": 0.0,
            }
            body["filter"] = {
                "must": [
                    self.source.condition(field, placeholders[declaration["type"]])
                ]
            }
            expect_match = False
        points = self.integration.query_points(self.collection, body, self.source)
        if bool(points) != expect_match:
            raise RuntimeError(
                "Exact chunk filter smoke returned unexpected match count"
            )
        snapshot = {item.id: item for item in documents}
        for point in points:
            document, _ = self.source.hit(point)
            if (
                document.id not in snapshot
                or canonical_bytes(document.fields)
                != canonical_bytes(snapshot[document.id].fields)
                or (
                    selected
                    and self.source.field_value(document.fields, selected[0])
                    != selected[1]
                )
            ):
                raise RuntimeError("Exact chunk filter smoke failed")
        # A separate unfiltered test cannot pass merely due to a nonmatching scalar.
        # One excluded id keeps this smoke bounded independently of corpus size.
        excluded_id = min(snapshot)
        body["filter"] = {
            "must_not": [{"key": "document_id", "match": {"any": [excluded_id]}}]
        }
        remaining = self.integration.query_points(self.collection, body, self.source)
        if bool(remaining) != (len(documents) > 1):
            raise RuntimeError(
                "Exact chunk exclusion smoke returned unexpected match count"
            )
        for point in remaining:
            document, _ = self.source.hit(point)
            if (
                document.id == excluded_id
                or document.id not in snapshot
                or canonical_bytes(document.fields)
                != canonical_bytes(snapshot[document.id].fields)
            ):
                raise RuntimeError("Exact chunk exclusion smoke failed")

    def run(self) -> VectorIndexArtifact:
        started = monotonic()
        self._phase("chunk preflight; tokenize is read-only")
        runtime_info = self.embedder.check_runtime()
        documents, chunks, batches, audits = self._chunk_preflight()
        qdrant_version = self.integration.version()
        # Runtime capacity and actual bounded document encoding are checked before create.
        if self.embedder.limits["batch_inputs"] > cast(
            int, runtime_info["max_client_batch_size"]
        ) or self.embedder.limits[
            "batch_inputs"
        ] * self.embedder.profile.max_tokens > cast(
            int, runtime_info["max_batch_tokens"]
        ):
            raise ValueError("Configured chunk batch exceeds TEI capacity")
        self.embedder.encode_query("проверка")
        self.embedder.encode_bounded_documents((chunks[0][1].text,))
        runtime = {
            **self.source.runtime,
            "inference": runtime_info,
            "qdrant_version": qdrant_version,
        }
        metadata_args = {
            "expected_points": len(chunks),
            "expected_documents": len(documents),
            "corpus_sha256": corpus_digest(documents),
            "runtime": runtime,
        }
        building = self.source.metadata(self.collection, **metadata_args)
        timings: dict[str, JSONValue] = {"preflight_seconds": monotonic() - started}
        self._phase("create and upload chunk generation")
        upload_started = monotonic()
        self.integration.create(self.collection, building)
        self.integration.create_indexes(self.collection, self.source)
        offset = 0
        for batch in batches:
            embeddings = self.embedder.encode_bounded_documents(batch)
            points: list[dict[str, JSONValue]] = []
            for (document, passage), embedding in zip(
                chunks[offset : offset + len(batch)], embeddings, strict=True
            ):
                points.append(
                    {
                        "id": self.source.point_id(document.id, passage.fragment()),
                        "payload": self._payload(document, passage),
                        "vector": list(embedding.values),
                    }
                )
            for bounded in self.integration.point_batches(points):
                self.integration.upsert(self.collection, bounded)
            offset += len(batch)
        timings["upload_seconds"] = monotonic() - upload_started
        self._phase("full M verification")
        verification_started = monotonic()
        if self.integration.count(self.collection) != len(chunks):
            raise RuntimeError("Exact chunk count differs from audited M")
        vector = self._chunk_read_back(chunks)
        actual = self._healthy()
        self._chunk_smoke(documents, vector)
        verification = {
            "protocol": "full-fragment-readback-v2",
            "exact_count": len(chunks),
            "read_back_count": len(chunks),
            "documents_count": len(documents),
            "status": "green",
            "optimizer_status": "ok",
            "indexes": dict(self.source.indexes),
            "smoke": True,
            "exclusion_smoke": True,
            "coverage": True,
            "ordinals": True,
            "fields_consistent": True,
        }
        timings["verification_seconds"] = monotonic() - verification_started
        publication_started = monotonic()
        ready = self.source.metadata(
            self.collection, state="ready", verification=verification, **metadata_args
        )
        self.integration.update_metadata(self.collection, ready)
        confirmed = self.integration.check_collection(self.collection, self.source)
        descriptor = self.source.validate_descriptor(self.collection, confirmed)
        if canonical_bytes(descriptor) != canonical_bytes(ready["recsys"]):
            raise RuntimeError("Remote chunk ready differs after publication")
        timings["publication_seconds"] = monotonic() - publication_started
        timings["total_seconds"] = monotonic() - started
        configuration = cast(dict[str, JSONValue], confirmed["config"])
        return VectorIndexArtifact(
            {
                "schema_version": 2,
                "artifact_type": "chunk_index",
                "descriptor": descriptor,
                "reader": self.reader.identity,
                "expected_documents": len(documents),
                "expected_points": len(chunks),
                "verified_points": len(chunks),
                "chunk_audit": cast(list[JSONValue], audits),
                "actual_config": {
                    key: configuration[key]
                    for key in (
                        "params",
                        "hnsw_config",
                        "optimizer_config",
                        "quantization_config",
                    )
                    if key in configuration
                },
                "payload_schema": actual["payload_schema"],
                "timings": timings,
            }
        )

import logging
from collections.abc import Sequence
from copy import deepcopy
from hashlib import sha256
from math import hypot, isfinite
from time import monotonic, sleep
from typing import cast
from uuid import UUID

from src.components.corpus.json import JSONVacancyReader
from src.components.embedding.encoder import TextEmbedder
from src.components.embedding.vacancy import VacancyText
from src.components.reporting.artifacts.vector import VectorIndexArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.components.reporting.exporters.json import JSONExporter
from src.domain import JSONScalar, JSONValue
from src.domain.document.vacancy import Vacancy
from src.domain.serialization import canonical_bytes
from src.experiments.abstract import BaseExperiment
from src.integrations.qdrant.client import QdrantIntegration, validate_collection_name
from src.integrations.qdrant.source import QdrantSource


def corpus_digest(documents: Sequence[Vacancy]) -> str:
    digest = sha256(b"recsys-corpus-v1\n")
    for document in sorted(documents, key=lambda item: item.id.encode("utf-8")):
        digest.update(canonical_bytes({"id": document.id, "fields": document.fields}))
        digest.update(b"\n")
    return digest.hexdigest()


class VectorIndexExperiment(BaseExperiment):
    """Build one fresh collection; remote ready precedes local receipt export."""

    def __init__(
        self,
        reader: JSONVacancyReader,
        renderer: VacancyText,
        embedder: TextEmbedder,
        integration: QdrantIntegration,
        source: QdrantSource,
        collection: str,
        exporters: Sequence[BaseExporter] = (),
    ) -> None:
        super().__init__(exporters)
        validate_collection_name(collection)
        if embedder.profile.id != source.profile.id or renderer.id != source.recipe.id:
            raise ValueError("Index dependencies disagree on profile or recipe")
        # Even the largest allowed payload must leave room for its vector/envelope.
        if integration.upload_batch_bytes < (
            integration.payload_bytes + source.profile.dimensions * 25 + 512
        ):
            raise ValueError("Upload budget cannot hold one allowed point")
        self.reader, self.renderer, self.embedder = reader, renderer, embedder
        self.integration, self.source, self.collection = integration, source, collection

    def _phase(self, name: str) -> None:
        logging.getLogger(__name__).info(
            "Vector collection %s: %s", self.collection, name
        )

    def _preflight(self) -> tuple[tuple[Vacancy, ...], tuple[tuple[str, ...], ...]]:
        for exporter in self.exporters:
            if isinstance(exporter, JSONExporter):
                if exporter.path.exists():
                    raise FileExistsError("Index receipt already exists")
                if not exporter.path.parent.is_dir():
                    raise ValueError("Index receipt directory does not exist")
        documents = tuple(deepcopy(self.reader.read()))
        if not documents:
            raise ValueError("Index corpus cannot be empty")
        ids, points, texts = set(), set(), []
        for document in documents:
            if (
                not isinstance(document, Vacancy)
                or not isinstance(document.id, str)
                or not document.id.strip()
                or document.id in ids
            ):
                raise ValueError("Invalid or duplicate corpus document")
            point_id = self.source.point_id(document.id)
            if point_id in points:
                raise ValueError("Colliding corpus point ids")
            ids.add(document.id)
            points.add(point_id)
            self.source.validate_fields(document.fields)
            self.integration.validate_payload(
                {"document_id": document.id, "fields": document.fields}
            )
            texts.append(self.renderer.render(document))
        # Materialization validates every input and batch before collection create.
        return documents, tuple(self.embedder.document_batches(texts))

    def _healthy(self) -> dict[str, JSONValue]:
        deadline = monotonic() + self.integration.verification_deadline
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Collection verification deadline exceeded")
            info = self.integration.collection(
                self.collection,
                timeout=min(self.integration.timeout, remaining),
                deadline=deadline,
            )
            self.source.validate_descriptor(self.collection, info, state="building")
            if monotonic() >= deadline:
                raise TimeoutError("Collection verification deadline exceeded")
            if info.get("status") == "red" or isinstance(
                info.get("optimizer_status"), dict
            ):
                raise RuntimeError("Collection optimizer failure")
            payload_schema = info.get("payload_schema")
            if not isinstance(payload_schema, dict):
                raise ValueError("Collection payload schema missing")
            indexes = all(
                isinstance(index := payload_schema.get(path), dict)
                and index.get("data_type") == kind
                for path, kind in self.source.indexes
            )
            if (
                info.get("status") == "green"
                and info.get("optimizer_status") == "ok"
                and indexes
            ):
                return info
            sleep(min(5.0, max(0.0, deadline - monotonic())))

    def _read_back(self, documents: Sequence[Vacancy]) -> tuple[float, ...]:
        first_vector: tuple[float, ...] | None = None
        for offset in range(0, len(documents), 8):
            batch = documents[offset : offset + 8]
            expected = {
                self.source.point_id(document.id): document for document in batch
            }
            records = self.integration.retrieve(self.collection, list(expected))
            seen: set[str] = set()
            if len(records) != len(expected):
                raise RuntimeError("Incomplete collection read-back")
            for record in records:
                point_id = str(UUID(str(record.get("id"))))
                if point_id not in expected or point_id in seen:
                    raise RuntimeError("Unexpected collection read-back id")
                seen.add(point_id)
                document = expected[point_id]
                payload = {"document_id": document.id, "fields": document.fields}
                if canonical_bytes(record.get("payload")) != canonical_bytes(payload):
                    raise RuntimeError(
                        "Collection payload differs from corpus snapshot"
                    )
                vector = record.get("vector")
                if (
                    not isinstance(vector, list)
                    or len(vector) != self.source.profile.dimensions
                ):
                    raise RuntimeError("Invalid stored collection vector")
                values: list[float] = []
                for value in vector:
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise RuntimeError("Invalid stored collection vector")
                    if not isfinite(value) or abs(value) > 3.4028234663852886e38:
                        raise RuntimeError("Invalid stored collection vector")
                    values.append(float(value))
                if abs(hypot(*values) - 1.0) > 1e-3:
                    raise RuntimeError("Stored collection vector must have unit norm")
                if first_vector is None:
                    first_vector = tuple(values)
        if first_vector is None:
            raise RuntimeError("Collection contains no vector")
        return first_vector

    def _smoke(self, documents: Sequence[Vacancy], vector: tuple[float, ...]) -> None:
        body: dict[str, JSONValue] = {
            "query": list(vector),
            "params": {"exact": True, "indexed_only": False},
            "limit": 1,
            "with_payload": True,
            "with_vector": False,
        }
        condition, expect_match = None, True
        selected_field: str | None = None
        selected_value: JSONScalar = None
        filter_fields = self.source.filter_fields
        for name in sorted(filter_fields):
            for document in sorted(documents, key=lambda item: item.id.encode("utf-8")):
                value = self.source.field_value(document.fields, name)
                if value is not None:
                    condition = self.source.condition(name, value)
                    selected_field, selected_value = name, value
                    break
            if condition is not None:
                break
        if condition is None and filter_fields:
            name = sorted(filter_fields)[0]
            declaration = cast(dict[str, JSONValue], filter_fields[name])
            values_by_type: dict[str, JSONScalar] = {
                "keyword": "recsys-smoke-v1",
                "bool": True,
                "integer": 0,
                "float": 0.0,
            }
            value = values_by_type[cast(str, declaration["type"])]
            condition = self.source.condition(name, value)
            expect_match = False
        if condition is not None:
            body["filter"] = {"must": [condition]}
        result = self.integration.query(self.collection, body, self.source)
        if bool(result.items) != expect_match:
            raise RuntimeError("Exact collection smoke failed")
        if selected_field is not None:
            snapshot = {document.id: document for document in documents}
            for item in result.items:
                document = snapshot.get(item.id)
                if (
                    document is None
                    or self.source.field_value(document.fields, selected_field)
                    != selected_value
                ):
                    raise RuntimeError("Exact collection filter smoke failed")

    def run(self) -> VectorIndexArtifact:
        timings: dict[str, JSONValue] = {}
        self._phase("preflight")
        run_started = started = monotonic()
        documents, batches = self._preflight()
        digest = corpus_digest(documents)
        qdrant_version = self.integration.version()
        info = self.embedder.check_runtime()
        if self.embedder.limits["batch_inputs"] > cast(
            int, info["max_client_batch_size"]
        ) or self.embedder.limits[
            "batch_inputs"
        ] * self.embedder.profile.max_tokens > cast(int, info["max_batch_tokens"]):
            raise ValueError("Configured indexing batch exceeds TEI capacity")
        self.embedder.encode_query("проверка")
        self.embedder.encode_documents(("проверка",))
        runtime = {
            **self.source.runtime,
            "inference": info,
            "qdrant_version": qdrant_version,
        }
        metadata = self.source.metadata(
            self.collection,
            expected_points=len(documents),
            corpus_sha256=digest,
            runtime=runtime,
        )
        timings["preflight_seconds"] = monotonic() - started

        self._phase("create and upload")
        started = monotonic()
        self.integration.create(self.collection, metadata)
        self.integration.create_indexes(self.collection, self.source)
        offset = 0
        for number, batch in enumerate(batches, 1):
            self._phase(f"upload batch {number}")
            embeddings = self.embedder.encode_documents(batch)
            points: list[dict[str, JSONValue]] = []
            for document, embedding in zip(
                documents[offset : offset + len(batch)], embeddings, strict=True
            ):
                points.append(
                    {
                        "id": self.source.point_id(document.id),
                        "vector": list(embedding.values),
                        "payload": {
                            "document_id": document.id,
                            "fields": document.fields,
                        },
                    }
                )
            for points_batch in self.integration.point_batches(points):
                self.integration.upsert(self.collection, points_batch)
            offset += len(batch)
        timings["upload_seconds"] = monotonic() - started

        self._phase("verify")
        started = monotonic()
        if self.integration.count(self.collection) != len(documents):
            raise RuntimeError("Exact collection count differs from corpus")
        vector = self._read_back(documents)
        actual = self._healthy()
        self._smoke(documents, vector)
        verification: dict[str, JSONValue] = {
            "protocol": "full-readback-v1",
            "exact_count": len(documents),
            "read_back_count": len(documents),
            "status": "green",
            "optimizer_status": "ok",
            "indexes": dict(self.source.indexes),
            "smoke": True,
        }
        timings["verification_seconds"] = monotonic() - started

        self._phase("publish remote ready")
        started = monotonic()
        ready = self.source.metadata(
            self.collection,
            expected_points=len(documents),
            corpus_sha256=digest,
            runtime=runtime,
            state="ready",
            verification=verification,
        )
        self.integration.update_metadata(self.collection, ready)
        confirmed = self.integration.check_collection(self.collection, self.source)
        descriptor = self.source.validate_descriptor(self.collection, confirmed)
        if canonical_bytes(descriptor) != canonical_bytes(ready["recsys"]):
            raise RuntimeError("Remote ready metadata differs after publication")
        configuration = cast(dict[str, JSONValue], confirmed["config"])
        timings["publication_seconds"] = monotonic() - started
        timings["total_seconds"] = monotonic() - run_started
        self._phase("ready; return receipt")
        return VectorIndexArtifact(
            {
                "schema_version": 1,
                "artifact_type": "vector_index",
                "descriptor": descriptor,
                "reader": self.reader.identity,
                "expected_count": len(documents),
                "verified_count": len(documents),
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

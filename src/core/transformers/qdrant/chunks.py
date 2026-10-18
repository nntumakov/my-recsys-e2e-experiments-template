"""Retrieve vacancy maxima with native point pages and exact document exclusions."""

from copy import deepcopy
from time import monotonic
from typing import cast

from src.components.embedding.encoder import TextEmbedder
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.qdrant.search import VectorSearchTransformer
from src.core.transformers.study import StudySearchTransformer
from src.domain import JSONValue
from src.domain.document.vacancy import Vacancy
from src.domain.serialization import canonical_bytes, fingerprint, load_json
from src.domain.study import StudySERP
from src.integrations.qdrant.chunks import ChunkQdrantIntegration, ChunkQdrantSource


def _matches(fields: dict[str, JSONValue], condition: dict[str, JSONValue]) -> bool:
    current: JSONValue = fields
    for segment in cast(str, condition["key"]).split(".")[1:]:
        current = current.get(segment) if isinstance(current, dict) else None
    if "match" in condition:
        expected = cast(dict, condition["match"])["value"]
        return type(current) is type(expected) and current == expected
    bounds = cast(dict, condition["range"])
    return type(current) in (int, float) and bounds["gte"] <= current <= bounds["lte"]


class ChunkSearchTransformer(StudySearchTransformer, VectorSearchTransformer):
    def __init__(
        self,
        integration: ChunkQdrantIntegration,
        embedder: TextEmbedder,
        source: ChunkQdrantSource,
        collection: str,
        *,
        candidate_depth: int = 100,
        final_k: int = 20,
        exact: bool = False,
        indexed_only: bool = False,
        hnsw_ef: int = 128,
        page_size: int = 100,
    ) -> None:
        if (
            not isinstance(integration, ChunkQdrantIntegration)
            or not isinstance(source, ChunkQdrantSource)
            or type(page_size) is not int
            or page_size != 100
        ):
            raise ValueError("E5 requires native v2 chunk components and page size 100")
        VectorSearchTransformer.__init__(
            self,
            integration,
            embedder,
            source,
            collection,
            candidate_depth=candidate_depth,
            final_k=final_k,
            exact=exact,
            indexed_only=indexed_only,
            hnsw_ef=hnsw_ef,
        )
        self.integration: ChunkQdrantIntegration = integration
        self.source: ChunkQdrantSource = source
        self.final_k, self.page_size = final_k, page_size

    def describe(self) -> dict[str, JSONValue]:
        return {
            "variant": "e5",
            "family": "embedding",
            "label": "Passage max",
            "source_id": self.source.id,
            "collection": self.collection,
            "title_field": self.source.recipe.title_field,
            "description_field": self.source.recipe.description_field,
            "parameters": {
                "candidate_depth": self.candidate_depth,
                "final_k": self.final_k,
                "page_size": self.page_size,
                "exact": self.exact,
                "hnsw_ef": self.hnsw_ef,
                "recipe": self.source.recipe.to_dict(),
            },
        }

    def preview(self, data: PipelineData) -> dict[str, JSONValue]:
        return self.prepare(data).to_dict() | {
            "mechanism": "vacancy-max-native-exclusions",
            "page_size": self.page_size,
            "max_calls": self.candidate_depth,
            "final_k": self.final_k,
        }

    def admit(self, data: PipelineData) -> list[dict[str, JSONValue]]:
        self.preview(data)
        started = monotonic()
        info = self.integration.check_collection(self.collection, self.source)
        return [
            {
                "stage": "admission",
                "http_calls": 2,
                "collection": self.collection,
                "elapsed_ms": (monotonic() - started) * 1000,
                "descriptor": self.source.validate_descriptor(self.collection, info),
            }
        ]

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        started = monotonic()
        plan = self.prepare(data)
        calls = self.admit(data)
        encode_started = monotonic()
        embedding = self.embedder.encode_query(plan.original_query)
        vector_sha256 = fingerprint(list(embedding.values))
        calls.append(
            {
                "stage": "inference",
                "http_calls": 2,
                "model_input": plan.model_input,
                "vector_sha256": vector_sha256,
                "vector_dimensions": len(embedding.values),
                "profile_id": embedding.profile_id,
                "elapsed_ms": (monotonic() - encode_started) * 1000,
            }
        )
        winners: dict[str, tuple[Vacancy, dict[str, JSONValue]]] = {}
        fields_by_id: dict[str, bytes] = {}
        native_ids = set()
        exhausted = False
        for iteration in range(1, self.candidate_depth + 1):
            body = plan.body(embedding)
            body["limit"] = self.page_size
            conditions = [load_json(c) for c in plan.conditions]
            exclusions = sorted(winners)
            native_filter: dict[str, JSONValue] = {}
            if conditions:
                native_filter["must"] = conditions
            if exclusions:
                native_filter["must_not"] = [
                    {
                        "key": "document_id",
                        "match": {"any": cast(list[JSONValue], exclusions)},
                    }
                ]
            if native_filter:
                body["filter"] = native_filter
            safe_body = deepcopy(body)
            safe_body.pop("query")
            retrieval_started = monotonic()
            points = self.integration.query_points(self.collection, body, self.source)
            # Check the entire page, including discarded hits, before committing its winners.
            page: dict[str, tuple[Vacancy, dict[str, JSONValue]]] = {}
            for point in points:
                document, fragment = self.source.hit(point)
                if point["id"] in native_ids or document.id in exclusions:
                    raise ValueError(
                        "Chunk search violated document exclusion or repeated native id"
                    )
                native_ids.add(point["id"])
                if any(
                    not _matches(document.fields, cast(dict, condition))
                    for condition in conditions
                ):
                    raise ValueError(
                        "Chunk search returned a document outside hard filters"
                    )
                fields = canonical_bytes(document.fields)
                if document.id in fields_by_id and fields_by_id[document.id] != fields:
                    raise ValueError("Chunks of one document have inconsistent fields")
                fields_by_id[document.id] = fields
                old = page.get(document.id)
                if (
                    old is None
                    or cast(float, document.score) > cast(float, old[0].score)
                    or (
                        document.score == old[0].score
                        and cast(int, fragment["ordinal"])
                        < cast(int, old[1]["ordinal"])
                    )
                ):
                    page[document.id] = document, fragment
            calls.append(
                {
                    "stage": "retrieval",
                    "http_calls": 1,
                    "iteration": iteration,
                    "collection": self.collection,
                    "body": safe_body,
                    "vector_sha256": vector_sha256,
                    "vector_dimensions": len(embedding.values),
                    "profile_id": embedding.profile_id,
                    "excluded_ids": cast(list[JSONValue], exclusions),
                    "elapsed_ms": (monotonic() - retrieval_started) * 1000,
                    "points": len(points),
                    "new_documents": len(page),
                    "excluded_documents": len(exclusions),
                }
            )
            if not points:
                exhausted = True
                break
            if not page:
                raise RuntimeError("Chunk search made no progress")
            winners.update(page)
            if len(winners) >= self.candidate_depth:
                break
        if len(winners) < self.candidate_depth and not exhausted:
            raise RuntimeError(
                "Chunk search call budget exhausted before complete depth or exhaustion"
            )
        ordered = sorted(
            winners.values(),
            key=lambda pair: (
                -cast(float, pair[0].score),
                pair[0].id,
                cast(int, pair[1]["ordinal"]),
            ),
        )[: self.candidate_depth]
        hits: dict[str, JSONValue] = {}
        for document, fragment in ordered:
            passage = self.source.passage(document, fragment)
            hits[document.id] = {
                "winner_fragment": deepcopy(fragment),
                "winner_passage": passage,
                "model_input": self.source.profile.document_prefix + passage,
                "max_score": document.score,
            }
        data.items = StudySERP(
            [document for document, _ in ordered[: self.final_k]],
            evidence={
                "variant": "e5",
                "active_path": "passage-max",
                "score_type": "max_native_cosine",
                "plan": self.preview(data),
                "calls": cast(list[JSONValue], calls),
                "retrieval_calls": len([c for c in calls if c["stage"] == "retrieval"]),
                "hits": hits,
                "candidate_documents": len(ordered),
                "exhausted": exhausted,
                "exact_vacancy_max": self.exact,
                "tie_boundary": "equal maxima may select equivalent depth membership",
                "source": self.describe(),
                "end_to_end_ms": (monotonic() - started) * 1000,
            },
        )
        return data

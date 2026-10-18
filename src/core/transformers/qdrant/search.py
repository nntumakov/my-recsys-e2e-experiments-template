from __future__ import annotations

from dataclasses import dataclass

from src.components.embedding.encoder import TextEmbedder
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.domain import JSONValue
from src.domain.embedding import Embedding
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter
from src.domain.request.request import Request
from src.domain.serialization import canonical_bytes, load_json
from src.integrations.qdrant.client import QdrantIntegration, validate_collection_name
from src.integrations.qdrant.source import QdrantSource


@dataclass(frozen=True)
class VectorQueryPlan:
    original_query: str
    model_input: str
    profile_id: str
    source_id: str
    conditions: tuple[bytes, ...]
    candidate_depth: int
    exact: bool
    hnsw_ef: int

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "original_query": self.original_query,
            "model_input": self.model_input,
            "profile_id": self.profile_id,
            "source_id": self.source_id,
            "conditions": [load_json(c) for c in self.conditions],
            "candidate_depth": self.candidate_depth,
            "params": {
                "exact": self.exact,
                "indexed_only": False,
                "hnsw_ef": self.hnsw_ef,
            },
        }

    def body(self, embedding: Embedding) -> dict[str, JSONValue]:
        if embedding.profile_id != self.profile_id:
            raise ValueError("Query embedding does not match its search plan")
        body: dict[str, JSONValue] = {
            "query": list(embedding.values),
            "params": {
                "exact": self.exact,
                "indexed_only": False,
                "hnsw_ef": self.hnsw_ef,
            },
            "limit": self.candidate_depth,
            "with_payload": True,
            "with_vector": False,
        }
        if self.conditions:
            body["filter"] = {"must": [load_json(c) for c in self.conditions]}
        return body


class VectorSearchTransformer(BaseTransformer):
    def __init__(
        self,
        integration: QdrantIntegration,
        embedder: TextEmbedder,
        source: QdrantSource,
        collection: str,
        *,
        candidate_depth: int = 100,
        final_k: int = 1,
        exact: bool = False,
        indexed_only: bool = False,
        hnsw_ef: int = 128,
    ) -> None:
        validate_collection_name(collection)
        if (
            type(candidate_depth) is not int
            or candidate_depth <= 0
            or type(final_k) is not int
            or not 0 < final_k <= candidate_depth
            or type(hnsw_ef) is not int
            or hnsw_ef <= 0
            or type(exact) is not bool
            or indexed_only is not False
            or embedder.profile.id != source.profile.id
        ):
            raise ValueError("Invalid vector search configuration")
        self.integration, self.embedder, self.source = integration, embedder, source
        self.collection, self.candidate_depth = collection, candidate_depth
        self.exact, self.hnsw_ef = exact, hnsw_ef

    def prepare(self, data: PipelineData) -> VectorQueryPlan:
        queries: list[str] = []
        conditions: list[bytes] = []
        for condition in Request(data.filters).filters:
            if isinstance(condition, QueryFilter):
                queries.append(condition.text)
                continue
            if isinstance(condition, TermFilter):
                field, value = condition.field, condition.value
            elif isinstance(condition, UserPropertyFilter):
                if condition.property not in data.context:
                    raise ValueError("Missing user property")
                field, value = condition.field, data.context[condition.property]
            else:
                raise ValueError("Unsupported vector search filter")
            conditions.append(canonical_bytes(self.source.condition(field, value)))
        if len(queries) != 1 or not queries[0].strip():
            raise ValueError("Vector search requires one nonempty QueryFilter")
        return VectorQueryPlan(
            queries[0],
            self.embedder.prepare_query(queries[0]),
            self.source.profile.id,
            self.source.id,
            tuple(conditions),
            self.candidate_depth,
            self.exact,
            self.hnsw_ef,
        )

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        plan = self.prepare(data)
        self.integration.check_collection(self.collection, self.source)
        embedding = self.embedder.encode_query(plan.original_query)
        result = self.integration.query(
            self.collection, plan.body(embedding), self.source
        )
        data.items = result
        return data

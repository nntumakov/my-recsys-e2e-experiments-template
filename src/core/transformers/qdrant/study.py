"""Whole-query, preference, multi-query and title/body vector retrieval."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from math import isfinite
from time import perf_counter
from typing import cast

from src.components.embedding.encoder import TextEmbedder
from src.components.embedding.representations import SingleFieldText
from src.components.query.jobboard.parser import ModularQueryParser
from src.components.query.study import StudyQuery, decompose_query
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.aggregation.rrf import ReciprocalRankFusionTransformer
from src.core.transformers.data import PipelineData
from src.core.transformers.qdrant.search import VectorQueryPlan, VectorSearchTransformer
from src.core.transformers.study import StudySearchTransformer
from src.domain import JSONValue
from src.domain.collection.serp import SERP
from src.domain.embedding import Embedding
from src.domain.serialization import canonical_bytes, fingerprint
from src.domain.study import StudySERP
from src.integrations.qdrant.client import QdrantIntegration
from src.integrations.qdrant.source import QdrantSource


def cosine_utility(score: float | None) -> float:
    if (
        type(score) not in (float, int)
        or not isfinite(cast(float, score))
        or not -1.00001 <= cast(float, score) <= 1.00001
    ):
        raise ValueError("Dense study requires cosine scores in [-1,1]")
    return (min(1.0, max(-1.0, cast(float, score))) + 1) / 2


class WholeQueryStudySearch(StudySearchTransformer):
    variant = "e1"
    label = "Whole-query cosine"

    def __init__(
        self,
        integration: QdrantIntegration,
        embedder: TextEmbedder,
        source: QdrantSource,
        collection: str,
        *,
        candidate_depth: int = 100,
        final_k: int = 20,
        exact: bool = True,
        hnsw_ef: int = 128,
    ) -> None:
        if source.to_dict().get("payload_schema_version") != 1:
            raise ValueError("Whole/title/body retrieval requires a v1 vacancy source")
        self.leaf = VectorSearchTransformer(
            integration,
            embedder,
            source,
            collection,
            candidate_depth=candidate_depth,
            final_k=final_k,
            exact=exact,
            hnsw_ef=hnsw_ef,
        )
        self.final_k = final_k

    def describe(self) -> dict[str, JSONValue]:
        source = self.leaf.source
        description: dict[str, JSONValue] = {
            "variant": self.variant,
            "family": "embedding",
            "label": self.label,
            "source_id": source.id,
            "parameters": {
                "candidate_depth": self.leaf.candidate_depth,
                "final_k": self.final_k,
                "exact": self.leaf.exact,
                "hnsw_ef": self.leaf.hnsw_ef,
                "profile_id": source.profile.id,
                "recipe_id": source.recipe.id,
                "collection": self.leaf.collection,
                "corpus_identity": source.corpus_identity,
            },
        }
        if isinstance(source.recipe, SingleFieldText):
            description[
                "title_field"
                if source.recipe.purpose == "title"
                else "description_field"
            ] = source.recipe.fields[0].name
        elif len(source.recipe.fields) == 2:
            description["title_field"] = source.recipe.fields[0].name
            description["description_field"] = source.recipe.fields[1].name
        return description

    def preview(self, data: PipelineData) -> dict[str, JSONValue]:
        return self.leaf.prepare(data).to_dict()

    def admit(self, data: PipelineData) -> list[dict[str, JSONValue]]:
        self.preview(data)
        calls: list[JSONValue] = []
        self._admission(calls)
        return cast(list[dict[str, JSONValue]], calls)

    def _admission(self, calls: list[JSONValue]) -> dict[str, JSONValue]:
        start = perf_counter()
        info = self.leaf.integration.check_collection(
            self.leaf.collection, self.leaf.source
        )
        descriptor = self.leaf.source.validate_descriptor(self.leaf.collection, info)
        calls.append(
            {
                "stage": "admission",
                "http_calls": 2,
                "collection": self.leaf.collection,
                "elapsed_ms": (perf_counter() - start) * 1000,
                "descriptor": descriptor,
            }
        )
        return descriptor

    def _encode(self, text: str, calls: list[JSONValue]) -> Embedding:
        start = perf_counter()
        vector = self.leaf.embedder.encode_query(text)
        calls.append(
            {
                "stage": "inference",
                "http_calls": 2,
                "model_input": self.leaf.embedder.prepare_query(text),
                "vector_sha256": fingerprint(list(vector.values)),
                "vector_dimensions": len(vector.values),
                "profile_id": vector.profile_id,
                "elapsed_ms": (perf_counter() - start) * 1000,
            }
        )
        return vector

    def _query(
        self,
        plan: VectorQueryPlan,
        vector: Embedding,
        calls: list[JSONValue],
        *,
        body: dict[str, JSONValue] | None = None,
        source: QdrantSource | None = None,
        collection: str | None = None,
    ) -> SERP:
        body = plan.body(vector) if body is None else body
        source, collection = (
            source or self.leaf.source,
            collection or self.leaf.collection,
        )
        start = perf_counter()
        result = self.leaf.integration.query(collection, body, source)
        if len(result.items) > cast(int, body["limit"]):
            raise ValueError("Dense source returned more than the requested limit")
        for document in result.items:
            cosine_utility(document.score)
        safe_body = deepcopy(body)
        safe_body.pop("query")
        calls.append(
            {
                "stage": "retrieval",
                "http_calls": 1,
                "collection": collection,
                "body": safe_body,
                "vector_sha256": fingerprint(list(vector.values)),
                "vector_dimensions": len(vector.values),
                "profile_id": vector.profile_id,
                "elapsed_ms": (perf_counter() - start) * 1000,
            }
        )
        return result

    def _finish(
        self,
        data: PipelineData,
        result: SERP,
        *,
        plan: dict[str, JSONValue],
        calls: list[JSONValue],
        descriptors: dict[str, JSONValue],
        active_path: str,
        score_type: str = "cosine",
        hits: dict[str, JSONValue] | None = None,
    ) -> PipelineData:
        data.items = StudySERP(
            deepcopy(result.items[: self.final_k]),
            {
                "variant": self.variant,
                "active_path": active_path,
                "score_type": score_type,
                "plan": plan,
                "calls": calls,
                "sources": descriptors,
                "hits": hits
                or {
                    item.id: {"native_score": item.score, "rank": rank}
                    for rank, item in enumerate(result.items, 1)
                },
            },
        )
        return data

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        plan, calls = self.leaf.prepare(data), []
        descriptor = self._admission(calls)
        vector = self._encode(plan.original_query, calls)
        result = self._query(plan, vector, calls)
        return self._finish(
            data,
            result,
            plan=plan.to_dict(),
            calls=calls,
            descriptors={self.leaf.collection: descriptor},
            active_path="original",
        )


class TopicPreferenceStudySearch(WholeQueryStudySearch):
    variant = "e2"
    label = "Topic pool with bounded preference utility"

    def __init__(
        self,
        *args,
        parser: ModularQueryParser,
        alpha: float = 0.2,
        preference_aggregation: str = "mean",
        preference_bindings: Mapping[str, Mapping[str, JSONValue]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        if (
            type(alpha) not in (int, float)
            or not isfinite(alpha)
            or not 0 <= alpha <= 0.3
        ):
            raise ValueError("Preference alpha must be finite and in [0,.3]")
        self.parser, self.alpha = parser, float(alpha)
        if not isinstance(
            preference_aggregation, str
        ) or preference_aggregation not in {"mean", "min"}:
            raise ValueError("Preference aggregation must be mean or min")
        self.preference_aggregation = preference_aggregation
        self.preference_bindings = deepcopy(dict(preference_bindings or {}))
        for rule_id, binding in self.preference_bindings.items():
            if (
                not isinstance(rule_id, str)
                or not rule_id.strip()
                or not isinstance(binding, Mapping)
                or set(binding) != {"field", "value"}
                or not isinstance(binding["field"], str)
                or not binding["field"].strip()
                or type(binding["value"]) not in {str, int, float, bool}
            ):
                raise ValueError("Invalid preference scalar binding")
        canonical_bytes(self.preference_bindings)

    def describe(self) -> dict[str, JSONValue]:
        description = super().describe()
        cast(dict, description["parameters"])["alpha"] = self.alpha
        cast(dict, description["parameters"])["preference_bindings"] = (
            self.preference_bindings
        )
        cast(dict, description["parameters"])["derived_recipe"] = "topic-soft-span-v1"
        cast(dict, description["parameters"])["preference_aggregation"] = (
            self.preference_aggregation
        )
        return description

    def _decompose(self, data: PipelineData) -> tuple[VectorQueryPlan, StudyQuery]:
        plan = self.leaf.prepare(data)
        return plan, decompose_query(
            plan.original_query,
            self.parser,
            preference_bindings=self.preference_bindings,
        )

    def _text_plan(self, plan: VectorQueryPlan, text: str) -> VectorQueryPlan:
        return replace(
            plan,
            original_query=text,
            model_input=self.leaf.embedder.prepare_query(text),
        )

    def preview(self, data: PipelineData) -> dict[str, JSONValue]:
        plan, query = self._decompose(data)
        # Preparing every derived input enforces all byte budgets before any HTTP.
        return {
            "original": plan.to_dict(),
            "topic": self._text_plan(plan, query.topic_text).to_dict(),
            "preferences": [
                self._text_plan(plan, text).to_dict()
                for text in query.preference_texts
                if self.alpha
            ],
            "extracted_preferences": [
                {
                    "rule_id": hint.rule_id,
                    "kind": hint.kind,
                    "span": list(hint.span),
                    "original_span": query.original_text[slice(*hint.span)],
                    "canonical": hint.canonical,
                }
                for hint in query.preferences
            ],
            "abstention": query.abstention,
            "alpha": self.alpha,
            "preference_aggregation": self.preference_aggregation,
        }

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        preview = self.preview(data)
        plan, query = self._decompose(data)
        calls: list[JSONValue] = []
        descriptor = self._admission(calls)
        topic_plan = self._text_plan(plan, query.topic_text)
        vector = self._encode(topic_plan.original_query, calls)
        pool = self._query(topic_plan, vector, calls)
        hits: dict[str, JSONValue] = {
            item.id: {
                "topic_cosine": item.score,
                "topic_rank": rank,
                "preferences": [],
                "bonus": 0.0,
                "aggregation": self.preference_aggregation,
            }
            for rank, item in enumerate(pool.items, 1)
        }
        preferences = query.preference_texts if self.alpha else ()
        by_id = {item.id: item for item in pool.items}
        weight = self.alpha / len(preferences) if preferences else 0.0
        # Encode unique prepared inputs once within this request, never globally.
        vectors = {topic_plan.model_input: vector}
        for text in preferences if pool.items else ():
            preference_plan = self._text_plan(plan, text)
            vector = vectors.get(preference_plan.model_input)
            if vector is None:
                vector = self._encode(text, calls)
                vectors[preference_plan.model_input] = vector
            body = preference_plan.body(vector)
            cast(dict, body["params"])["exact"] = True
            body["limit"] = len(pool.items)
            body["filter"] = {
                "must": [
                    *cast(
                        list,
                        cast(dict, topic_plan.body(vector).get("filter", {})).get(
                            "must", []
                        ),
                    ),
                    {
                        "has_id": [
                            self.leaf.source.point_id(item.id) for item in pool.items
                        ]
                    },
                ]
            }
            scored = self._query(preference_plan, vector, calls, body=body)
            if {item.id for item in scored.items} != set(by_id):
                raise ValueError(
                    "Preference scoring must return the complete fixed pool"
                )
            for item in scored.items:
                if canonical_bytes(item.fields) != canonical_bytes(
                    by_id[item.id].fields
                ):
                    raise ValueError("Preference scoring source fields changed")
                contribution = weight * cosine_utility(item.score)
                hit = cast(dict, hits[item.id])
                cast(list, hit["preferences"]).append(
                    {
                        "query": text,
                        "cosine": item.score,
                        "utility": cosine_utility(item.score),
                        "weight": weight
                        if self.preference_aggregation == "mean"
                        else None,
                        "contribution": contribution
                        if self.preference_aggregation == "mean"
                        else None,
                    }
                )
                hit["bonus"] += contribution
        for item in pool.items:
            if preferences and self.preference_aggregation == "min":
                hit = cast(dict, hits[item.id])
                hit["bonus"] = self.alpha * min(
                    cosine_utility(p["cosine"]) for p in hit["preferences"]
                )
                hit["aggregation"] = "min"
            item.score = cast(float, item.score) + cast(dict, hits[item.id])["bonus"]
        pool.items.sort(key=lambda item: cast(float, item.score), reverse=True)
        return self._finish(
            data,
            pool,
            plan=preview,
            calls=calls,
            descriptors={self.leaf.collection: descriptor},
            active_path="original"
            if query.abstention or not query.preferences
            else "topic_only"
            if not self.alpha
            else "topic_preference",
            score_type="cosine_plus_utility"
            if preferences and pool.items
            else "cosine",
            hits=hits,
        )


class MultiQueryStudySearch(TopicPreferenceStudySearch):
    variant = "e3"
    label = "Original/topic query RRF"

    def __init__(self, *args, rrf_constant: float = 60, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fusion = ReciprocalRankFusionTransformer(rrf_constant)

    def describe(self) -> dict[str, JSONValue]:
        description = super().describe()
        cast(dict, description["parameters"]).pop("alpha")
        cast(dict, description["parameters"]).pop("preference_aggregation")
        cast(dict, description["parameters"])["rrf_constant"] = (
            self.fusion.rank_constant
        )
        return description

    def preview(self, data: PipelineData) -> dict[str, JSONValue]:
        plan, query = self._decompose(data)
        topic = self._text_plan(plan, query.topic_text)
        active = bool(
            query.preferences
            and not query.abstention
            and topic.model_input != plan.model_input
        )
        return {
            "original": plan.to_dict(),
            "topic": topic.to_dict() if active else None,
            "abstention": query.abstention,
            "rrf_constant": self.fusion.rank_constant,
            "duplicate_vote_suppressed": not active,
        }

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        preview = self.preview(data)
        plan, query = self._decompose(data)
        calls: list[JSONValue] = []
        descriptor = self._admission(calls)
        plans = [plan]
        topic = self._text_plan(plan, query.topic_text)
        if (
            query.preferences
            and not query.abstention
            and topic.model_input != plan.model_input
        ):
            plans.append(topic)
        results = [
            self._query(p, self._encode(p.original_query, calls), calls) for p in plans
        ]
        hits: dict[str, JSONValue] = {}
        fields: dict[str, bytes] = {}
        for name, result in zip(("original", "topic"), results):
            for rank, item in enumerate(result.items, 1):
                if item.id in fields and fields[item.id] != canonical_bytes(
                    item.fields
                ):
                    raise ValueError("Original/topic source fields disagree")
                fields[item.id] = canonical_bytes(item.fields)
                cast(dict, hits.setdefault(item.id, {}))[name] = {
                    "rank": rank,
                    "cosine": item.score,
                }
                if len(results) == 2:
                    cast(dict, hits[item.id])[name]["rrf_contribution"] = 1 / (
                        self.fusion.rank_constant + rank
                    )
        if len(results) == 1:
            result = results[0]
        else:
            fused = PipelineData(
                branches={
                    name: PipelineData(items=result)
                    for name, result in zip(("original", "topic"), results)
                }
            )
            self.fusion.transform(fused, registry=registry)
            result = cast(SERP, fused.items)
            result.items = result.items[: self.leaf.candidate_depth]
        return self._finish(
            data,
            result,
            plan=preview,
            calls=calls,
            descriptors={self.leaf.collection: descriptor},
            active_path="original_topic_rrf" if len(results) == 2 else "original",
            score_type="rrf" if len(results) == 2 else "cosine",
            hits=hits,
        )


class TitleBodyStudySearch(WholeQueryStudySearch):
    variant = "e4"
    label = "Independent title/body RRF"

    def __init__(
        self,
        integration: QdrantIntegration,
        embedder: TextEmbedder,
        title_source: QdrantSource,
        title_collection: str,
        body_source: QdrantSource,
        body_collection: str,
        *,
        rrf_constant: float = 60,
        **kwargs,
    ) -> None:
        super().__init__(
            integration, embedder, title_source, title_collection, **kwargs
        )
        self.body_leaf = VectorSearchTransformer(
            integration,
            embedder,
            body_source,
            body_collection,
            candidate_depth=self.leaf.candidate_depth,
            final_k=self.final_k,
            exact=self.leaf.exact,
            hnsw_ef=self.leaf.hnsw_ef,
        )
        if (
            title_collection == body_collection
            or not isinstance(title_source.recipe, SingleFieldText)
            or title_source.recipe.purpose != "title"
            or not isinstance(body_source.recipe, SingleFieldText)
            or body_source.recipe.purpose != "description"
            or title_source.recipe.id == body_source.recipe.id
            or title_source.corpus_identity != body_source.corpus_identity
            or canonical_bytes(title_source.filter_fields)
            != canonical_bytes(body_source.filter_fields)
            or canonical_bytes(title_source.runtime)
            != canonical_bytes(body_source.runtime)
        ):
            raise ValueError(
                "Title/body search requires distinct representations of one cohort"
            )
        self.fusion = ReciprocalRankFusionTransformer(rrf_constant)

    def describe(self) -> dict[str, JSONValue]:
        description = super().describe()
        description["description_field"] = self.body_leaf.source.recipe.fields[0].name
        cast(dict, description["parameters"]).update(
            {
                "body_collection": self.body_leaf.collection,
                "body_source_id": self.body_leaf.source.id,
                "rrf_constant": self.fusion.rank_constant,
            }
        )
        return description

    def preview(self, data: PipelineData) -> dict[str, JSONValue]:
        return {
            "title": self.leaf.prepare(data).to_dict(),
            "body": self.body_leaf.prepare(data).to_dict(),
        }

    def _admission(self, calls: list[JSONValue]) -> dict[str, JSONValue]:
        title = super()._admission(calls)
        start = perf_counter()
        info = self.body_leaf.integration.check_collection(
            self.body_leaf.collection, self.body_leaf.source
        )
        body = self.body_leaf.source.validate_descriptor(
            self.body_leaf.collection, info
        )
        calls.append(
            {
                "stage": "admission",
                "http_calls": 2,
                "collection": self.body_leaf.collection,
                "elapsed_ms": (perf_counter() - start) * 1000,
                "descriptor": body,
            }
        )
        if (
            title["corpus_sha256"] != body["corpus_sha256"]
            or title["expected_points"] != body["expected_points"]
        ):
            raise ValueError("Title/body generations must contain the same cohort")
        return {self.leaf.collection: title, self.body_leaf.collection: body}

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        preview, calls = self.preview(data), []
        descriptors = self._admission(calls)
        plan = self.leaf.prepare(data)
        vector = self._encode(plan.original_query, calls)
        title = self._query(plan, vector, calls)
        body = self._query(
            self.body_leaf.prepare(data),
            vector,
            calls,
            source=self.body_leaf.source,
            collection=self.body_leaf.collection,
        )
        hits: dict[str, JSONValue] = {}
        fields = {}
        for name, result in (("title", title), ("body", body)):
            for rank, item in enumerate(result.items, 1):
                if item.id in fields and fields[item.id] != canonical_bytes(
                    item.fields
                ):
                    raise ValueError("Title/body source fields disagree")
                fields[item.id] = canonical_bytes(item.fields)
                cast(dict, hits.setdefault(item.id, {}))[name] = {
                    "rank": rank,
                    "cosine": item.score,
                    "rrf_contribution": 1 / (self.fusion.rank_constant + rank),
                }
        fused = PipelineData(
            branches={
                "title": PipelineData(items=title),
                "body": PipelineData(items=body),
            }
        )
        self.fusion.transform(fused, registry=registry)
        fused.items.items = fused.items.items[: self.leaf.candidate_depth]
        return self._finish(
            data,
            cast(SERP, fused.items),
            plan=preview,
            calls=calls,
            descriptors=descriptors,
            active_path="title_body_rrf",
            score_type="rrf",
            hits=hits,
        )

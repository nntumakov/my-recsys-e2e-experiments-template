"""Literal, concept, preference, tiered and fused fulltext retrieval."""

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from math import ceil, isfinite
from time import perf_counter
from typing import cast

from src.components.query.jobboard.parser import ModularQueryParser
from src.components.query.study import StudyQuery, decompose_query
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.aggregation.rrf import ReciprocalRankFusionTransformer
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.jobboard.query import (
    SourceProfile,
    _fields,
    _forms,
    _group,
    _leaves,
    _multi,
    _residual_group,
    _term_filters,
)
from src.core.transformers.opensearch.jobboard.slots import (
    SlotQueryConfig,
    slot_residual,
)
from src.core.transformers.study import StudySearchTransformer
from src.domain import JSONValue
from src.domain.collection.serp import SERP
from src.domain.request.filters import QueryFilter, TermFilter
from src.domain.request.request import Request
from src.domain.study import StudySERP
from src.integrations.opensearch.client import OpenSearchIntegration
from src.integrations.opensearch.evidence import observe


class FulltextStudyTransformer(StudySearchTransformer):
    def __init__(
        self,
        integration: OpenSearchIntegration,
        parser: ModularQueryParser,
        source: SourceProfile,
        query: SlotQueryConfig | None = None,
        variant: str = "f1",
        candidate_depth: int = 100,
        final_k: int = 20,
        preference_budget: float = 0.2,
        preference_bindings: Mapping[str, Mapping[str, JSONValue]] | None = None,
        rank_constant: float = 60,
        similarity_evidence: Mapping[str, JSONValue] | None = None,
        *,
        description_field: str = "description",
    ) -> None:
        if not isinstance(description_field, str) or not description_field.strip():
            raise ValueError("Declare a nonempty raw source description field")
        self.description_field = description_field
        if variant not in {"f1", "f2", "f3", "f4", "f5"}:
            raise ValueError("Unknown fulltext study variant")
        if (
            any(type(v) is not int or v <= 0 for v in (candidate_depth, final_k))
            or final_k > candidate_depth
        ):
            raise ValueError("Invalid study candidate depth or final k")
        if (
            type(preference_budget) not in (int, float)
            or not isfinite(preference_budget)
            or not 0 <= preference_budget <= 0.3
        ):
            raise ValueError("Preference budget must be finite in [0,.3]")
        self.integration, self.parser, self.source = integration, parser, source
        self.query = query or SlotQueryConfig(role_phrase_boost=1)
        self.variant, self.candidate_depth, self.final_k = (
            variant,
            candidate_depth,
            final_k,
        )
        self.preference_budget = preference_budget
        self.fusion = ReciprocalRankFusionTransformer(rank_constant)
        self.bindings = deepcopy(dict(preference_bindings or {}))
        self.similarity_evidence = deepcopy(dict(similarity_evidence or {}))
        if self.similarity_evidence and (
            set(self.similarity_evidence)
            != {"similarity", "provenance", "engine_version", "analyzers", "parameters"}
            or self.similarity_evidence["similarity"] != "BM25"
            or any(
                not isinstance(self.similarity_evidence[key], str)
                or not self.similarity_evidence[key]
                for key in ("provenance", "engine_version")
            )
            or not isinstance(self.similarity_evidence["analyzers"], dict)
            or set(self.similarity_evidence["analyzers"]) != set(source.fields)
            or not isinstance(self.similarity_evidence["parameters"], dict)
        ):
            raise ValueError("Invalid BM25 source declaration")
        _fields(source, self.query)
        for weights in self.query.field_weights_by_kind.values():
            if not set(weights) <= set(source.fields):
                raise ValueError("Undeclared core routing field")
        for concept, binding in self.bindings.items():
            if not concept or set(binding) != {"field", "value"}:
                raise ValueError("Preference binding requires concept, field and value")
            name, value = binding["field"], binding["value"]
            if not isinstance(name, str):
                raise ValueError("Preference field must be a declared scalar field")
            # Use the same boundary validation as all explicit hard filters.

            _term_filters(
                Request(
                    (
                        QueryFilter("validation"),
                        TermFilter(name, cast(str | bool | int | float, value)),
                    )
                ),
                {},
                source,
            )

    def describe(self) -> dict[str, JSONValue]:
        labels = {
            "f1": "BM25 по лучшему полю",
            "f2": "Обязательное тематическое ядро",
            "f3": "Ограниченный бонус пожеланий",
            "f4": "Группы по пожеланиям",
            "f5": "RRF буквального и тематического поиска",
        }
        return {
            "variant": self.variant,
            "family": "fulltext",
            "source_id": self.source.index,
            "title_field": self.source.title_field,
            "description_field": self.description_field,
            "label": labels[self.variant],
            "parameters": cast(
                dict[str, JSONValue],
                {
                    "candidate_depth": self.candidate_depth,
                    "final_k": self.final_k,
                    "preference_budget": self.preference_budget,
                    "rank_constant": self.fusion.rank_constant,
                    "fields": list(self.source.fields),
                    "title_field": self.source.title_field,
                    "query": asdict(self.query),
                    "preference_bindings": self.bindings,
                    "similarity_evidence": self.similarity_evidence,
                },
            ),
        }

    def _literal(
        self, plan: StudyQuery, filters: list[dict[str, object]]
    ) -> dict[str, JSONValue]:
        fields = _fields(self.source, self.query)
        clauses: dict[str, object] = {
            "must": [_multi(plan.parsed.normalized_text, fields, self.query)],
            "filter": filters,
        }
        if self.source.title_field and self.query.title_phrase_boost:
            clauses["should"] = [
                {
                    "match_phrase": {
                        self.source.title_field: {
                            "query": plan.parsed.normalized_text,
                            "slop": self.query.phrase_slop,
                            "boost": self.query.title_phrase_boost,
                        }
                    }
                }
            ]
            clauses["minimum_should_match"] = 0
        return cast(
            dict[str, JSONValue],
            {"size": self.candidate_depth, "query": {"bool": clauses}},
        )

    def _core(
        self, plan: StudyQuery, filters: list[dict[str, object]]
    ) -> dict[str, JSONValue]:
        fields = _fields(self.source, self.query)
        hints = [
            h
            for h in plan.parsed.hints
            if h.status == "unambiguous"
            and h.kind
            not in {
                "experience",
                "schedule",
                "employment",
                "work_condition",
                "location",
            }
        ]
        residual = slot_residual(plan.parsed)
        if len(hints) + len(residual) > self.query.max_concept_groups:
            raise ValueError("Query exceeds concept budget")
        remaining = self.query.max_alternatives_total - len(hints) - len(residual)
        if remaining < 0:
            raise ValueError("Query exceeds alternatives budget")
        must, coverage = [], []
        for hint in hints:
            extra = min(
                remaining,
                min(
                    len(_forms(hint, self.query)),
                    self.query.max_alternatives_per_concept,
                )
                - 1,
            )
            remaining -= extra
            weights = (
                self.query.field_weights_by_kind.get(hint.kind)
                if self.query.route_by_kind
                else None
            )
            if self.query.route_by_kind and not weights and hint.kind == "skill":
                weights = {
                    name: self.query.skill_title_weight
                    if name == self.source.title_field
                    else self.query.default_field_weight
                    for name in self.source.fields
                }
            routed = (
                [f"{name}^{weight:g}" for name, weight in weights.items()]
                if weights
                else fields
            )
            clause = _group(hint, routed, self.query, 1 + extra)
            (must if hint.kind in {"role", "qualification"} else coverage).append(
                clause
            )
        coverage.extend(
            _residual_group(word, fields, self.query) for word, _ in residual
        )
        if coverage:
            count = (
                len(coverage)
                if len(coverage) <= 2
                else ceil(self.query.concept_coverage * len(coverage))
            )
            must.append({"bool": {"should": coverage, "minimum_should_match": count}})
        if not must:
            raise ValueError("Empty core cannot compile")
        return cast(
            dict[str, JSONValue],
            {
                "size": self.candidate_depth,
                "query": {"bool": {"must": must, "filter": filters}},
            },
        )

    def _plan(
        self, data: PipelineData
    ) -> tuple[StudyQuery, list[dict[str, JSONValue]], dict[str, JSONValue]]:
        request = Request(data.filters)
        for condition in request.filters:
            if (
                isinstance(condition, QueryFilter)
                and len(condition.text) > self.query.max_input_characters
            ):
                raise ValueError("Query exceeds character budget")
        text, filters = _term_filters(request, data.context, self.source)
        if len(text) > self.query.max_input_characters:
            raise ValueError("Query exceeds character budget")
        plan = decompose_query(text, self.parser, self.bindings, semantic=False)
        predicates: list[dict[str, JSONValue]] = []
        for i, hint in enumerate(plan.preferences):
            binding = self.bindings.get(hint.rule_id)
            if binding:
                name = cast(str, binding["field"])
                predicate = {"term": {name: binding["value"]}}
            else:
                predicate = {
                    "bool": {
                        "should": [
                            {
                                "match_phrase": {
                                    name: {"query": hint.canonical, "slop": 0}
                                }
                            }
                            for name in self.source.fields
                        ],
                        "minimum_should_match": 1,
                    }
                }
            predicates.append(
                cast(
                    dict[str, JSONValue],
                    {"bool": {"filter": [predicate], "_name": f"preference_{i}"}},
                )
            )
        original = self._literal(plan, filters)
        core = (
            original
            if self.variant == "f1" or plan.abstention
            else self._core(plan, filters)
        )
        bodies: list[dict[str, object]] = [cast(dict[str, object], core)]
        if self.variant == "f1":
            bodies = [cast(dict[str, object], original)]
        elif self.variant == "f3" and predicates and not plan.abstention:
            weight = self.preference_budget / len(predicates)
            bodies = [
                {
                    "size": self.candidate_depth,
                    "query": {
                        "function_score": {
                            "query": core["query"],
                            "functions": [
                                {"filter": {"match_all": {}}, "weight": 1},
                                *[{"filter": p, "weight": weight} for p in predicates],
                            ],
                            "score_mode": "sum",
                            "boost_mode": "multiply",
                            "max_boost": 1 + self.preference_budget,
                        }
                    },
                }
            ]
        elif self.variant == "f4" and predicates and not plan.abstention:
            all_preferences: dict[str, object] = {"bool": {"filter": predicates}}
            any_preferences: dict[str, object] = {
                "bool": {"should": predicates, "minimum_should_match": 1}
            }
            tier_filters = [all_preferences]
            if len(predicates) > 1:
                tier_filters.append(
                    {
                        "bool": {
                            "filter": [any_preferences],
                            "must_not": [all_preferences],
                        }
                    }
                )
            tier_filters.append({"bool": {"must_not": [any_preferences]}})
            bodies = [
                {
                    "size": self.candidate_depth,
                    "query": {"bool": {"must": [core["query"]], "filter": [tier]}},
                }
                for tier in tier_filters
            ]
        elif self.variant == "f5":
            bodies = (
                [cast(dict[str, object], original)]
                if plan.abstention
                else [cast(dict[str, object], original), cast(dict[str, object], core)]
            )
        for body in bodies:
            if _leaves(cast(JSONValue, body)) > self.query.max_query_leaves:
                raise ValueError("Query exceeds DSL leaf budget")
        diagnostic = cast(
            dict[str, JSONValue],
            {
                "variant": self.variant,
                "active_path": "f1"
                if self.variant == "f1" or plan.abstention
                else "f2"
                if self.variant in {"f3", "f4"} and not plan.preferences
                else self.variant,
                "abstention": None if self.variant == "f1" else plan.abstention,
                "original_text": plan.original_text,
                "topic_text": plan.topic_text,
                "core_groups": [
                    {
                        "kind": h.kind,
                        "span": list(h.span),
                        "surface": h.surface,
                        "concept": h.rule_id,
                        "status": h.status,
                    }
                    for h in plan.parsed.hints
                    if h.kind in {"role", "skill", "qualification"}
                ],
                "residual": [
                    {"text": word, "span": list(span)}
                    for word, span in slot_residual(plan.parsed)
                ],
                "parse_decisions": [
                    {"code": d.code, "span": list(d.span), "details": d.details}
                    for d in plan.parsed.decisions
                ],
                "similarity_status": "declared"
                if self.similarity_evidence
                else "unknown",
                "similarity_evidence": self.similarity_evidence,
                "preferences": [
                    {
                        "kind": h.kind,
                        "span": list(h.span),
                        "concept": h.rule_id,
                        "surface": h.surface,
                        "origin": "scalar_value"
                        if h.rule_id in self.bindings
                        else "positive_text_phrase",
                        "predicate": p,
                    }
                    for h, p in zip(plan.preferences, predicates, strict=True)
                ],
                "requests": bodies,
            },
        )
        return plan, cast(list[dict[str, JSONValue]], bodies), diagnostic

    def preview(self, data: PipelineData) -> dict[str, JSONValue]:
        return self._plan(data)[2]

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        plan, bodies, diagnostic = self._plan(data)
        results: list[SERP] = []
        calls: list[JSONValue] = []
        hits: dict[str, JSONValue] = {}
        for i, body in enumerate(bodies):
            started = perf_counter()
            response = self.integration.search(index=self.source.index, body=body)
            elapsed_ms = (perf_counter() - started) * 1000
            result = observe(response, body, diagnostic, elapsed_ms)
            if self.variant == "f5" and len(bodies) == 2:
                branch_label = ("literal", "core")[i]
            elif self.variant == "f4" and plan.preferences and not plan.abstention:
                branch_label = (
                    ("all", "none")[i]
                    if len(bodies) == 2
                    else ("all", "partial", "none")[i]
                )
            else:
                branch_label = cast(str, diagnostic["active_path"])
            calls.append(
                {
                    "stage": "retrieval",
                    "branch": branch_label,
                    "branch_index": i,
                    "http_calls": 1,
                    "elapsed_ms": result.client_elapsed_ms,
                    "server_took_ms": result.server_took_ms,
                    "returned": len(result.items),
                }
            )
            if len(result.items) > self.candidate_depth:
                raise ValueError("Source exceeded candidate depth")
            for rank, item in enumerate(result.items, 1):
                previous = hits.get(item.id)
                if previous is not None and isinstance(previous, dict):
                    if json.dumps(
                        previous["fields"],
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    ) != json.dumps(
                        item.fields, ensure_ascii=False, sort_keys=True, allow_nan=False
                    ):
                        raise ValueError("Source inconsistency between searches")
                    if self.variant == "f4":
                        raise ValueError("Repeated document across disjoint tiers")
                else:
                    hits[item.id] = {"fields": deepcopy(item.fields), "branches": []}
                info = cast(dict[str, JSONValue], hits[item.id])
                cast(list[JSONValue], info["branches"]).append(
                    {
                        "branch": i,
                        "rank": rank,
                        "native_score": item.score,
                        "rrf_contribution": 1 / (self.fusion.rank_constant + rank)
                        if self.variant == "f5" and len(bodies) > 1
                        else None,
                        "tier": (
                            0 if i == 0 else 2 if len(plan.preferences) == 1 else i
                        )
                        if self.variant == "f4"
                        and plan.preferences
                        and not plan.abstention
                        else None,
                    }
                )
            # Only the response can attest which named queries matched.
            native_hits = cast(dict[str, JSONValue], response.response["hits"])["hits"]
            for native in cast(list[JSONValue], native_hits):
                native = cast(dict[str, JSONValue], native)
                identifier = cast(str, native["_id"])
                info = cast(dict[str, JSONValue], hits[identifier])
                if "matched_queries" in native:
                    names = native["matched_queries"]
                    if not isinstance(names, list) or any(
                        not isinstance(name, str) for name in names
                    ):
                        raise ValueError("Malformed named query evidence")
                    branch_info = cast(
                        dict[str, JSONValue],
                        cast(list[JSONValue], info["branches"])[-1],
                    )
                    branch_info["matched_queries"] = deepcopy(names)
                    if (
                        self.variant == "f3"
                        and plan.preferences
                        and not plan.abstention
                    ):
                        matched = sum(
                            f"preference_{j}" in names
                            for j in range(len(plan.preferences))
                        )
                        branch_info["preference_multiplier"] = (
                            1 + self.preference_budget * matched / len(plan.preferences)
                        )
            results.append(result)
        if self.variant == "f5" and len(results) == 2:
            merged = PipelineData(
                branches={
                    str(i): PipelineData(items=result)
                    for i, result in enumerate(results)
                }
            )
            items = self.fusion.transform(merged, registry=registry).items.items
        else:
            items = [item for result in results for item in result.items]
        score_type = (
            "rrf"
            if self.variant == "f5" and len(results) == 2
            else "native_lexical_preference"
            if self.variant == "f3" and plan.preferences and not plan.abstention
            else "native_lexical"
        )
        evidence: dict[str, JSONValue] = {
            "variant": self.variant,
            "active_path": diagnostic["active_path"],
            "score_type": score_type,
            "plan": diagnostic,
            "calls": calls,
            "hits": hits,
            "candidate_count": min(len(items), self.candidate_depth),
        }
        # Assignment only after every request, response and fusion has succeeded.
        data.items = StudySERP(
            items[: self.candidate_depth][: self.final_k], evidence=evidence
        )
        return data

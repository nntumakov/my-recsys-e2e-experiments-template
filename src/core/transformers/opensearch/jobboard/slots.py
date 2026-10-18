"""Concept-centric eligibility and field routing, opt-in alongside legacy lexical."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from src.core.transformers.opensearch.jobboard.query import (
    LexicalQueryConfig,
    SourceProfile,
    _fields,
    _forms,
    _group,
    _leaves,
    _multi,
    _residual,
    _residual_group,
    _term_filters,
)
from src.domain import JSONValue
from src.domain.request.jobboard import HINT_KINDS, ParsedQuery, QueryHint
from src.domain.request.request import Request


@dataclass(frozen=True)
class SlotQueryConfig(LexicalQueryConfig):
    field_weights_by_kind: Mapping[str, Mapping[str, float]] = field(
        default_factory=dict
    )
    attribute_boosts: Mapping[str, float] = field(default_factory=dict)
    role_phrase_boost: float = 2.0
    route_by_kind: bool = True
    skill_title_weight: float = 2.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.route_by_kind) is not bool:
            raise ValueError("Routing flag must be boolean")
        if set(self.field_weights_by_kind) - HINT_KINDS or set(
            self.attribute_boosts
        ) - (HINT_KINDS - {"role", "skill", "qualification"}):
            raise ValueError("Unknown slot or non-attribute boost")
        for weights in self.field_weights_by_kind.values():
            if not weights or any(not name for name in weights):
                raise ValueError("Slot requires explicit fields")
        values = [
            self.role_phrase_boost,
            self.skill_title_weight,
            *self.attribute_boosts.values(),
            *(
                v
                for weights in self.field_weights_by_kind.values()
                for v in weights.values()
            ),
        ]
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values
        ):
            raise ValueError("Invalid slot weights")
        object.__setattr__(
            self,
            "field_weights_by_kind",
            {k: dict(v) for k, v in self.field_weights_by_kind.items()},
        )
        object.__setattr__(self, "attribute_boosts", dict(self.attribute_boosts))


def slot_residual(parsed: ParsedQuery) -> list[tuple[str, tuple[int, int]]]:
    return _residual(
        parsed.normalized_text,
        parsed.hints,
        parsed.protected_spans,
        parsed.ignored_spans,
    )


def compile_slots(
    parsed: ParsedQuery,
    request: Request,
    context: Mapping[str, JSONValue],
    source: SourceProfile,
    config: SlotQueryConfig,
    size: int,
) -> dict[str, JSONValue]:
    raw, filters = _term_filters(request, context, source)
    if parsed.original_text.strip() != raw or not parsed.normalized_text.strip():
        raise ValueError("Parsed query does not match request")
    return slot_body(parsed, source, config, size, filters)


def slot_body(
    parsed: ParsedQuery,
    source: SourceProfile,
    config: SlotQueryConfig,
    size: int,
    filters: list[dict[str, object]],
) -> dict[str, JSONValue]:
    if (
        len(parsed.original_text) > config.max_input_characters
        or type(size) is not int
        or size <= 0
    ):
        raise ValueError("Invalid query size")
    fields = _fields(source, config)
    for weights in config.field_weights_by_kind.values():
        if not set(weights) <= set(source.fields):
            raise ValueError("Slot routing contains undeclared text fields")
    literal = _multi(parsed.normalized_text, fields, config)
    paths = [literal]
    must: list[dict[str, object]] = []
    if parsed.structured_enabled:
        hints = [h for h in parsed.hints if h.status == "unambiguous"]
        topics = [h for h in hints if h.kind in {"role", "skill"}]
        qualifications = [h for h in hints if h.kind == "qualification"]
        attributes = [
            h for h in hints if h.kind not in {"role", "skill", "qualification"}
        ]
        residual = slot_residual(parsed)
        if len(hints) + len(residual) > config.max_concept_groups:
            raise ValueError("Query exceeds concept budget")
        remaining = config.max_alternatives_total - len(hints) - len(residual)
        if remaining < 0:
            raise ValueError("Query exceeds alternatives budget")
        limits: dict[QueryHint, int] = {}
        for hint in hints:
            extra = min(
                remaining,
                min(len(_forms(hint, config)), config.max_alternatives_per_concept) - 1,
            )
            limits[hint] = 1 + extra
            remaining -= extra

        def group(hint: QueryHint) -> dict[str, object]:
            weights = config.field_weights_by_kind.get(hint.kind)
            if config.route_by_kind and not weights:
                if hint.kind == "skill":
                    weights = {
                        name: config.skill_title_weight
                        if name == source.title_field
                        else config.default_field_weight
                        for name in source.fields
                    }
                elif hint.kind not in {"role", "qualification"}:
                    description_fields = [
                        name for name in source.fields if name != source.title_field
                    ] or list(source.fields)
                    weights = {
                        name: config.default_field_weight for name in description_fields
                    }
            if not config.route_by_kind:
                weights = None

            routed = (
                [f"{name}^{weight:g}" for name, weight in weights.items()]
                if weights
                else fields
            )
            return _group(
                hint,
                routed,
                config,
                limits[hint],
                config.role_phrase_boost if hint.kind == "role" else 1.0,
            )

        groups = [group(h) for h in topics]
        groups.extend(_residual_group(word, fields, config) for word, _ in residual)
        if groups and (topics or qualifications or attributes):
            structured: dict[str, object] = {
                "should": groups,
                "minimum_should_match": len(groups)
                if len(groups) <= 2
                else math.ceil(config.concept_coverage * len(groups)),
            }
            if qualifications:
                structured["must"] = [group(h) for h in qualifications]
            # Attribute boosts are outside the coverage bool so they cannot satisfy it.
            optional = [
                {
                    "bool": {
                        "must": [group(h)],
                        "boost": config.attribute_boosts.get(h.kind, 0.2),
                    }
                }
                for h in attributes
                if config.attribute_boosts.get(h.kind, 0.2) > 0
            ]
            paths.append(
                {
                    "bool": {
                        "must": [{"bool": structured}],
                        "should": optional,
                        "minimum_should_match": 0,
                    }
                }
            )
        if topics and config.topical_gate == "any_confirmed_topic":
            # Gate is only an eligibility witness; synonyms need not be expanded twice.
            must.append(
                {
                    "constant_score": {
                        "filter": {
                            "bool": {
                                "should": [group(h) for h in topics],
                                "minimum_should_match": 1,
                            }
                        },
                        "boost": 0,
                    }
                }
            )
    must.append({"dis_max": {"queries": paths, "tie_breaker": 0}})
    clauses: dict[str, object] = {"must": must}
    if filters:
        clauses["filter"] = filters
    body = cast(dict[str, JSONValue], {"size": size, "query": {"bool": clauses}})
    if _leaves(body) > config.max_query_leaves:
        raise ValueError("Query exceeds DSL leaf budget")
    return body

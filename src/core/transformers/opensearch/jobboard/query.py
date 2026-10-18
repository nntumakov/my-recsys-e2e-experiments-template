from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from src.domain import JSONValue
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter
from src.domain.request.jobboard import ParsedQuery, QueryHint
from src.domain.request.request import Request

_TERM_TYPES = {"keyword", "boolean", "integer", "float"}
_TOKEN = re.compile(r"\.[\w]+|[\w]+(?:[.+#][\w]+)*[+#]*", re.UNICODE)
_MINIMUM_SHOULD_MATCH = re.compile(r"(?:-?\d+%?|\d+<-?\d+%?(?: +\d+<-?\d+%?)*)")


@dataclass(frozen=True)
class SourceProfile:
    index: str
    fields: Sequence[str]
    title_field: str | None = None
    term_fields: Mapping[str, str] = field(default_factory=dict)
    ranking_fields: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.index, str)
            or not self.index.strip()
            or self.index in {".", ".."}
            or isinstance(self.fields, str)
            or not self.fields
            or any(
                not isinstance(name, str)
                or not name.strip()
                or "*" in name
                or "^" in name
                for name in self.fields
            )
            or len(set(self.fields)) != len(self.fields)
            or self.title_field is not None
            and self.title_field not in self.fields
            or any(
                not isinstance(name, str) or not name.strip() or kind not in _TERM_TYPES
                for name, kind in self.term_fields.items()
            )
        ):
            raise ValueError("Invalid source capabilities")
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "term_fields", dict(self.term_fields))
        for role, definition in self.ranking_fields.items():
            if (
                role not in {"published_at", "quality", "category", "stable_id"}
                or set(definition) != {"field", "type"}
                or not isinstance(definition["field"], str)
                or not definition["field"].strip()
                or definition["type"] not in {"date", "numeric", "keyword"}
            ):
                raise ValueError("Invalid ranking field declaration")
        object.__setattr__(
            self, "ranking_fields", {k: dict(v) for k, v in self.ranking_fields.items()}
        )


@dataclass(frozen=True)
class FuzzyQueryConfig:
    enabled: bool = False
    distance: int = 1
    minimum_length: int = 5
    prefix_length: int = 1
    max_expansions: int = 25

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or self.distance > 2
            or any(
                type(value) is not int or value < minimum
                for value, minimum in (
                    (self.distance, 0),
                    (self.minimum_length, 1),
                    (self.prefix_length, 0),
                    (self.max_expansions, 1),
                )
            )
        ):
            raise ValueError("Invalid fuzzy settings")


@dataclass(frozen=True)
class LexicalQueryConfig:
    field_weights: Mapping[str, float] = field(default_factory=dict)
    default_field_weight: float = 1.0
    title_field_weight: float = 4.0
    tie_breaker: float = 0.1
    literal_minimum_should_match: str = "2<75%"
    concept_coverage: float = 0.75
    aliases_enabled: bool = True
    alias_weight: float = 0.7
    title_phrase_boost: float = 2.0
    phrase_slop: int = 0
    seniority_boost: float = 0.2
    work_mode_boost: float = 0.2
    location_boost: float = 0.2
    topical_gate: str = "any_confirmed_topic"
    max_input_characters: int = 512
    max_concept_groups: int = 16
    max_alternatives_per_concept: int = 4
    max_alternatives_total: int = 48
    max_query_leaves: int = 128
    fuzzy: FuzzyQueryConfig | None = None

    def __post_init__(self) -> None:
        boosts = (
            *self.field_weights.values(),
            self.default_field_weight,
            self.title_field_weight,
            self.alias_weight,
            self.title_phrase_boost,
            self.seniority_boost,
            self.work_mode_boost,
            self.location_boost,
        )
        budgets = (
            self.max_input_characters,
            self.max_concept_groups,
            self.max_alternatives_per_concept,
            self.max_alternatives_total,
            self.max_query_leaves,
        )
        if (
            any(not isinstance(name, str) or not name for name in self.field_weights)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                for value in boosts
            )
            or isinstance(self.tie_breaker, bool)
            or not 0 <= self.tie_breaker <= 1
            or not math.isfinite(self.tie_breaker)
            or isinstance(self.concept_coverage, bool)
            or not 0 < self.concept_coverage <= 1
            or not math.isfinite(self.concept_coverage)
            or type(self.aliases_enabled) is not bool
            or self.topical_gate not in {"off", "any_confirmed_topic"}
            or type(self.phrase_slop) is not int
            or self.phrase_slop < 0
            or any(type(value) is not int or value <= 0 for value in budgets)
            or not isinstance(self.literal_minimum_should_match, str)
            or not _MINIMUM_SHOULD_MATCH.fullmatch(self.literal_minimum_should_match)
            or self.fuzzy is not None
            and not isinstance(self.fuzzy, FuzzyQueryConfig)
        ):
            raise ValueError("Invalid lexical query settings")
        object.__setattr__(self, "field_weights", dict(self.field_weights))


def _fields(source: SourceProfile, config: LexicalQueryConfig) -> list[str]:
    if not set(config.field_weights) <= set(source.fields):
        raise ValueError("Query weights contain unknown source fields")
    return [f"{name}^{_field_weight(name, source, config):g}" for name in source.fields]


def _field_weight(
    name: str, source: SourceProfile, config: LexicalQueryConfig
) -> float:
    default = (
        config.title_field_weight
        if name == source.title_field
        else config.default_field_weight
    )
    return config.field_weights.get(name, default)


def _multi(
    text: str, fields: list[str], config: LexicalQueryConfig
) -> dict[str, object]:
    return {
        "multi_match": {
            "query": text,
            "fields": fields,
            "type": "best_fields",
            "tie_breaker": config.tie_breaker,
            "minimum_should_match": config.literal_minimum_should_match,
            "zero_terms_query": "none",
        }
    }


def _term_filters(
    request: Request, context: Mapping[str, JSONValue], source: SourceProfile
) -> tuple[str, list[dict[str, object]]]:
    queries: list[str] = []
    filters: list[dict[str, object]] = []
    for condition in request.filters:
        if isinstance(condition, QueryFilter):
            queries.append(condition.text.strip())
            continue
        if isinstance(condition, TermFilter):
            name, value = condition.field, condition.value
        elif isinstance(condition, UserPropertyFilter):
            if condition.property not in context:
                raise ValueError("Missing user property")
            name, value = condition.field, context[condition.property]
        else:
            raise ValueError("Unsupported search filter")
        kind = source.term_fields.get(name)
        if kind is None:
            raise ValueError("Unsupported term field")
        valid = (
            kind == "keyword"
            and isinstance(value, str)
            or kind == "boolean"
            and type(value) is bool
            or kind == "integer"
            and type(value) is int
            or kind == "float"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
        if not valid:
            raise ValueError("Term value does not match source field type")
        filters.append({"term": {name: value}})
    if len(queries) != 1 or not queries[0]:
        raise ValueError("Search requires exactly one non-empty QueryFilter")
    return queries[0], filters


def _group(
    hint: QueryHint,
    fields: list[str],
    config: LexicalQueryConfig,
    limit: int | None = None,
    phrase_boost: float = 1.0,
) -> dict[str, object]:
    forms = _forms(hint, config)[: limit or config.max_alternatives_per_concept]
    queries: list[dict[str, object]] = []
    for i, form in enumerate(forms):
        if " " in form:
            alternatives = [
                {
                    "match_phrase": {
                        name.partition("^")[0]: {
                            "query": form,
                            "slop": 0,
                            "boost": float(name.partition("^")[2]) * phrase_boost,
                        }
                    }
                }
                for name in fields
            ]
            clause: dict[str, object] = {
                "dis_max": {"queries": alternatives, "tie_breaker": 0}
            }
        else:
            clause = _multi(form, fields, config)
        if i and config.alias_weight != 1:
            clause = {"bool": {"must": [clause], "boost": config.alias_weight}}
        queries.append(clause)
    return {"dis_max": {"queries": queries, "tie_breaker": 0}}


def _forms(hint: QueryHint, config: LexicalQueryConfig) -> list[str]:
    candidates = [hint.surface]
    if config.aliases_enabled and config.alias_weight > 0:
        candidates.extend(hint.alternatives)
    seen: set[str] = set()
    forms: list[str] = []
    for candidate in candidates:
        key = candidate.casefold()
        if candidate and key not in seen:
            seen.add(key)
            forms.append(candidate)
    return forms


def _residual_group(
    text: str, fields: list[str], config: LexicalQueryConfig
) -> dict[str, object]:
    if " " not in text:
        return _multi(text, fields, config)
    return {
        "dis_max": {
            "queries": [
                {
                    "match_phrase": {
                        name.partition("^")[0]: {
                            "query": text,
                            "slop": 0,
                            "boost": float(name.partition("^")[2]),
                        }
                    }
                }
                for name in fields
            ],
            "tie_breaker": 0,
        }
    }


def _residual(
    text: str,
    hints: Sequence[QueryHint],
    protected_spans: Sequence[tuple[int, int]],
    ignored_spans: Sequence[tuple[int, int]] = (),
) -> list[tuple[str, tuple[int, int]]]:
    occupied = [False] * len(text)
    for start, end in ignored_spans:
        occupied[start:end] = [True] * (end - start)
    for hint in hints:
        if hint.status != "unambiguous":
            continue
        for index in range(*hint.span):
            if 0 <= index < len(occupied):
                occupied[index] = True
    atomic = sorted(
        (
            *protected_spans,
            *(hint.span for hint in hints if hint.status == "ambiguous"),
        ),
        key=lambda span: (span[0], -(span[1] - span[0])),
    )
    residual: list[tuple[str, tuple[int, int]]] = []
    for start, end in atomic:
        if not any(occupied[start:end]) and text[start:end].strip():
            residual.append((text[start:end], (start, end)))
            occupied[start:end] = [True] * (end - start)
    residual.extend(
        (match.group(), match.span())
        for match in _TOKEN.finditer(text)
        if not any(occupied[match.start() : match.end()])
    )
    return sorted(residual, key=lambda item: item[1])


def _leaves(value: JSONValue) -> int:
    if isinstance(value, list):
        return sum(_leaves(item) for item in value)
    if isinstance(value, dict):
        if any(
            key in value for key in ("multi_match", "match", "match_phrase", "term")
        ):
            return 1
        return sum(_leaves(item) for item in value.values())
    return 0


def compile_query(
    parsed: ParsedQuery,
    request: Request,
    context: Mapping[str, JSONValue],
    source: SourceProfile,
    config: LexicalQueryConfig,
    size: int,
) -> dict[str, JSONValue]:
    if type(size) is not int or size <= 0:
        raise ValueError("Search size must be positive")
    raw, filters = _term_filters(request, context, source)
    if len(parsed.original_text) > config.max_input_characters:
        raise ValueError("Query exceeds character budget")
    if parsed.original_text.strip() != raw or not parsed.normalized_text.strip():
        raise ValueError("Parsed query does not match request")
    fields = _fields(source, config)
    literal = _multi(parsed.normalized_text, fields, config)
    paths: list[dict[str, object]] = [literal]
    should: list[dict[str, object]] = []
    must: list[dict[str, object]] = []
    if parsed.structured_enabled:
        hints = [hint for hint in parsed.hints if hint.status == "unambiguous"]
        topics = [hint for hint in hints if hint.kind in {"role", "skill"}]
        attributes = [
            hint
            for hint in hints
            if hint.kind in {"seniority", "work_mode", "location"}
        ]
        residual = _residual(
            parsed.normalized_text, parsed.hints, parsed.protected_spans
        )
        recognized = (*topics, *attributes)
        if len(recognized) + len(residual) > config.max_alternatives_total:
            raise ValueError("Query exceeds alternatives budget")
        remaining = config.max_alternatives_total - len(recognized) - len(residual)
        limits: dict[QueryHint, int] = {}
        for hint in recognized:
            available = min(
                len(_forms(hint, config)), config.max_alternatives_per_concept
            )
            extra = min(available - 1, remaining)
            limits[hint] = 1 + extra
            remaining -= extra
        groups = [_group(hint, fields, config, limits[hint]) for hint in topics]
        groups.extend(_residual_group(word, fields, config) for word, _ in residual)
        if len(recognized) + len(residual) > config.max_concept_groups:
            raise ValueError("Query exceeds concept budget")
        if (topics or attributes) and groups:
            coverage = (
                len(groups)
                if len(groups) <= 2
                else math.ceil(config.concept_coverage * len(groups))
            )
            paths.append({"bool": {"should": groups, "minimum_should_match": coverage}})
        if topics and config.topical_gate == "any_confirmed_topic":
            must.append(
                {
                    "constant_score": {
                        "filter": {
                            "bool": {
                                "should": [
                                    _group(hint, fields, config, limits[hint])
                                    for hint in topics
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                        "boost": 0,
                    }
                }
            )
        boost_by_kind = {
            "seniority": config.seniority_boost,
            "work_mode": config.work_mode_boost,
            "location": config.location_boost,
        }
        if topics or residual:
            for hint in attributes:
                boost = boost_by_kind[hint.kind]
                if boost:
                    should.append(
                        {
                            "bool": {
                                "must": [_group(hint, fields, config, limits[hint])],
                                "boost": boost,
                            }
                        }
                    )
        if config.fuzzy is not None and config.fuzzy.enabled:
            eligible = {
                span
                for word, span in residual
                if word.isalpha()
                and len(word) >= config.fuzzy.minimum_length
                and not any(
                    start < span[1] and span[0] < end
                    for start, end in parsed.protected_spans
                )
                and not any(
                    hint.status == "ambiguous"
                    and hint.span[0] < span[1]
                    and span[0] < hint.span[1]
                    for hint in parsed.hints
                )
            }
            if eligible:
                fuzzy_groups: list[dict[str, object]] = []
                for word, span in residual:
                    if span in eligible:
                        fuzzy_groups.append(
                            {
                                "dis_max": {
                                    "queries": [
                                        {
                                            "match": {
                                                name: {
                                                    "query": word,
                                                    "fuzziness": config.fuzzy.distance,
                                                    "prefix_length": config.fuzzy.prefix_length,
                                                    "max_expansions": config.fuzzy.max_expansions,
                                                    "zero_terms_query": "none",
                                                    "boost": _field_weight(
                                                        name, source, config
                                                    ),
                                                }
                                            }
                                        }
                                        for name in source.fields
                                    ],
                                    "tie_breaker": 0,
                                }
                            }
                        )
                    else:
                        fuzzy_groups.append(_residual_group(word, fields, config))
                fuzzy_groups.extend(
                    _group(hint, fields, config, limits[hint]) for hint in topics
                )
                coverage = (
                    len(fuzzy_groups)
                    if len(fuzzy_groups) <= 2
                    else math.ceil(config.concept_coverage * len(fuzzy_groups))
                )
                paths.append(
                    {"bool": {"should": fuzzy_groups, "minimum_should_match": coverage}}
                )
    must.append({"dis_max": {"queries": paths, "tie_breaker": 0}})
    duplicate_phrase = any(
        hint.kind == "role"
        and hint.status == "unambiguous"
        and hint.span == (0, len(parsed.normalized_text))
        and " " in hint.surface
        for hint in parsed.hints
    )
    if (
        parsed.structured_enabled
        and not duplicate_phrase
        and source.title_field
        and config.title_phrase_boost
    ):
        should.append(
            {
                "match_phrase": {
                    source.title_field: {
                        "query": parsed.normalized_text,
                        "slop": config.phrase_slop,
                        "boost": config.title_phrase_boost,
                    }
                }
            }
        )
    body_bool: dict[str, object] = {"must": must, "minimum_should_match": 0}
    if filters:
        body_bool["filter"] = filters
    if should:
        body_bool["should"] = should
    body: dict[str, object] = {"size": size, "query": {"bool": body_bool}}
    if _leaves(cast(JSONValue, body)) > config.max_query_leaves:
        raise ValueError("Query exceeds DSL leaf budget")
    return cast(dict[str, JSONValue], body)

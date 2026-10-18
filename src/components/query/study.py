"""Conservative shared decomposition for the search-study mechanisms."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from src.components.query.jobboard.parser import ModularQueryParser
from src.domain import JSONValue
from src.domain.request.jobboard import ParsedQuery, QueryHint

SOFT_KINDS = frozenset(
    {"experience", "schedule", "employment", "work_condition", "location"}
)


@dataclass(frozen=True)
class StudyQuery:
    parsed: ParsedQuery
    original_text: str
    topic_text: str
    preferences: tuple[QueryHint, ...]
    preference_texts: tuple[str, ...]
    abstention: str | None = None


def decompose_query(
    text: str,
    parser: ModularQueryParser,
    preference_bindings: Mapping[str, Mapping[str, JSONValue]] | None = None,
    *,
    semantic: bool = True,
) -> StudyQuery:
    # Validate raw input before normalizing; errors are never abstentions.
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Query text must be nonempty")
    if len(text) > parser.max_input_characters:
        raise ValueError("Query exceeds character budget")
    original = " ".join(unicodedata.normalize("NFC", text).split())
    parsed = parser.parse(original)
    preferences = tuple(
        h for h in parsed.hints if h.kind in SOFT_KINDS and h.status == "unambiguous"
    )
    occupied = [False] * len(parsed.normalized_text)
    for start, end in (
        *parsed.ignored_spans,
        *(h.span for h in parsed.hints if h.status == "unambiguous"),
    ):
        occupied[start:end] = [True] * (end - start)
    has_residual = any(
        not any(occupied[m.start() : m.end()])
        for m in re.finditer(r"[\w]+", parsed.normalized_text)
    )
    reason = None
    if not parsed.structured_enabled:
        reason = "unsupported_syntax"
    elif semantic and any(h.status != "unambiguous" for h in parsed.hints):
        reason = "ambiguous_query"
    elif len(preferences) > 4:
        reason = "preference_budget_exceeded"
    elif any(
        left.span[0] < right.span[1] and right.span[0] < left.span[1]
        for i, left in enumerate(parsed.hints)
        for right in parsed.hints[i + 1 :]
        if left.status == right.status == "unambiguous"
    ):
        reason = "overlapping_spans"
    elif (
        not any(h.kind in {"role", "skill"} for h in parsed.hints) and not has_residual
    ):
        reason = "empty_core"
    scalar_values: dict[str, JSONValue] = {}
    for hint in preferences:
        binding = (preference_bindings or {}).get(hint.rule_id)
        if binding:
            name = binding["field"]
            if not isinstance(name, str):
                raise ValueError("Preference field must be a string")
            value = binding["value"]
            if name in scalar_values and scalar_values[name] != value:
                reason = "conflicting_preferences"
            scalar_values[name] = value
    mapped = [unicodedata.normalize("NFKC", char).casefold() for char in original]
    mapping_available = (
        not any(len(char) != 1 for char in mapped)
        and "".join(mapped) == parsed.normalized_text
    )
    if reason is None and semantic and not mapping_available:
        reason = "span_mapping_unavailable"
    if reason:
        return StudyQuery(parsed, original, original, (), (), reason)
    span_text = original if mapping_available else parsed.normalized_text
    retained = list(span_text)
    for hint in preferences:
        retained[hint.span[0] : hint.span[1]] = " " * (hint.span[1] - hint.span[0])
    topic = " ".join("".join(retained).split())
    if not topic:
        return StudyQuery(parsed, original, original, (), (), "empty_core")
    inputs = tuple(topic + ". " + span_text[slice(*h.span)] for h in preferences)
    return StudyQuery(parsed, original, topic, preferences, inputs)

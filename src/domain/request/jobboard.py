from dataclasses import dataclass
from typing import Literal

type HintKind = Literal[
    "role",
    "skill",
    "seniority",
    "work_mode",
    "location",
    "experience",
    "schedule",
    "employment",
    "qualification",
    "work_condition",
]
HINT_KINDS = frozenset(
    (
        "role",
        "skill",
        "seniority",
        "work_mode",
        "location",
        "experience",
        "schedule",
        "employment",
        "qualification",
        "work_condition",
    )
)
type HintStatus = Literal["unambiguous", "ambiguous"]


@dataclass(frozen=True)
class QueryHint:
    kind: HintKind
    span: tuple[int, int]
    surface: str
    canonical: str
    alternatives: tuple[str, ...]
    rule_id: str
    status: HintStatus

    def __post_init__(self) -> None:
        if (
            not isinstance(self.span, tuple)
            or len(self.span) != 2
            or not all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in self.span
            )
        ):
            raise ValueError("query hint span must be a pair of integers")
        if not isinstance(self.alternatives, tuple) or not all(
            isinstance(value, str) and value.strip() for value in self.alternatives
        ):
            raise ValueError("query hint alternatives must be immutable strings")
        if self.kind not in HINT_KINDS:
            raise ValueError("invalid query hint kind")
        if self.status not in ("unambiguous", "ambiguous"):
            raise ValueError("invalid query hint status")
        if self.span[0] < 0 or self.span[1] <= self.span[0]:
            raise ValueError("invalid query hint span")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.surface, self.canonical, self.rule_id)
        ):
            raise ValueError("query hint requires surface, canonical and rule id")


@dataclass(frozen=True)
class ParseDecision:
    code: str
    span: tuple[int, int]
    details: str


@dataclass(frozen=True)
class ParsedQuery:
    original_text: str
    normalized_text: str
    hints: tuple[QueryHint, ...]
    structured_enabled: bool = True
    protected_spans: tuple[tuple[int, int], ...] = ()
    ignored_spans: tuple[tuple[int, int], ...] = ()
    decisions: tuple[ParseDecision, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.original_text, str)
            or not isinstance(self.normalized_text, str)
            or type(self.structured_enabled) is not bool
        ):
            raise ValueError("query requires text and an explicit syntax policy")
        if not isinstance(self.hints, tuple) or not all(
            isinstance(hint, QueryHint) for hint in self.hints
        ):
            raise ValueError("query hints must be immutable values")
        if not isinstance(self.protected_spans, tuple) or not all(
            isinstance(span, tuple)
            and len(span) == 2
            and all(type(value) is int for value in span)
            for span in self.protected_spans
        ):
            raise ValueError("protected spans must be immutable pairs")
        for hint in self.hints:
            if self.normalized_text[hint.span[0] : hint.span[1]] != hint.surface:
                raise ValueError("query hint span does not match normalized text")
        if not isinstance(self.ignored_spans, tuple) or not isinstance(
            self.decisions, tuple
        ):
            raise ValueError("Parse diagnostics must be immutable")
        for start, end in (*self.protected_spans, *self.ignored_spans):
            if start < 0 or end <= start or end > len(self.normalized_text):
                raise ValueError("invalid protected span")

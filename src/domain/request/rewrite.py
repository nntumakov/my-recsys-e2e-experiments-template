"""Immutable evidence of query repair, distinct from the original request."""

from dataclasses import dataclass

from src.domain.request.jobboard import ParseDecision, ParsedQuery


@dataclass(frozen=True)
class RewriteCandidate:
    source_span: tuple[int, int]
    source_text: str
    target_text: str
    concept_id: str
    mechanisms: tuple[str, ...]
    edit_distance: int | None
    decision: str
    reason: str


@dataclass(frozen=True)
class QueryRewrite:
    original: ParsedQuery
    candidates: tuple[RewriteCandidate, ...] = ()
    rewritten: ParsedQuery | None = None
    decisions: tuple[ParseDecision, ...] = ()

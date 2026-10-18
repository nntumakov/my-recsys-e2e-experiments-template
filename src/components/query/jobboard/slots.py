"""Recognize roles, qualifications and preferences in vacancy queries."""

import re
from collections.abc import Sequence
from dataclasses import replace

from src.components.query.jobboard.parser import ModularQueryParser
from src.components.query.normalization import bounded_matches
from src.domain.request.jobboard import ParseDecision, ParsedQuery, QueryHint


class SlotQueryParser(ModularQueryParser):
    def __init__(self, *args, introductions: Sequence[str] = (), **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.introductions = tuple(self.normalizer.normalize(s) for s in introductions)
        if any(not s for s in self.introductions):
            raise ValueError("Introductions must be nonempty")

    @staticmethod
    def _nested(hint: QueryHint, hints: Sequence[QueryHint]) -> bool:
        return hint.kind in {"role", "skill"} and any(
            other.kind == "role"
            and other.status == "unambiguous"
            and other.span != hint.span
            and other.span[0] <= hint.span[0] < hint.span[1] <= other.span[1]
            for other in hints
        )

    def _merge(self, hints: Sequence[QueryHint]) -> tuple[QueryHint, ...]:
        return super()._merge([h for h in hints if not self._nested(h, hints)])

    def parse(self, raw_text: str) -> ParsedQuery:
        parsed = super().parse(raw_text)
        text = parsed.normalized_text
        ignored = tuple(
            sorted(
                {
                    span
                    for phrase in self.introductions
                    for span in bounded_matches(text, phrase)
                }
            )
        )
        decisions = [
            ParseDecision("introduction", span, text[slice(*span)]) for span in ignored
        ]
        if not parsed.structured_enabled:
            decisions.append(ParseDecision("unsupported_syntax", (0, len(text)), text))
        # Preserve numbers, schedules and qualifications as atomic residual values.
        protected = set(parsed.protected_spans)
        protected.update(m.span() for m in re.finditer(r"\d+(?:[/.,]\d+)*", text))
        if re.search(
            r"\d[\d\s]*\s*(?:тыс|руб|₽)|(?:от|до)\s+\d{4,}|на руки|за смену|ежедневно\s+\d",
            text,
        ):
            decisions.append(
                ParseDecision("unsupported_salary_constraint", (0, len(text)), text)
            )
        raw_hints = tuple(h for stage in self.stages for h in stage.parse(text))
        decisions.extend(
            ParseDecision("contained_in_role", h.span, h.rule_id)
            for h in raw_hints
            if self._nested(h, raw_hints)
        )
        return replace(
            parsed,
            ignored_spans=ignored,
            decisions=tuple(decisions),
            protected_spans=tuple(sorted(protected)),
        )

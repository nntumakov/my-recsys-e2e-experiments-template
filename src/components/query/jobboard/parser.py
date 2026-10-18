from collections.abc import Sequence
from dataclasses import replace

from src.components.query.jobboard.abstract import BaseQueryStage
from src.components.query.normalization import NormalizeText, bounded_matches
from src.domain.request.jobboard import ParsedQuery, QueryHint


class ModularQueryParser:
    def __init__(
        self,
        normalizer: NormalizeText,
        stages: Sequence[BaseQueryStage],
        unsupported_markers: Sequence[str] = (),
        syntax_whitelist: Sequence[str] = (),
        max_input_characters: int = 512,
        max_concept_groups: int = 16,
        max_alternatives_per_concept: int = 4,
        max_alternatives_total: int = 48,
    ) -> None:
        for value in (
            max_input_characters,
            max_concept_groups,
            max_alternatives_per_concept,
            max_alternatives_total,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("parser budgets must be positive integers")
        self.normalizer = normalizer
        self.stages = tuple(stages)
        self.unsupported_markers = tuple(
            normalizer.normalize(value) for value in unsupported_markers
        )
        self.syntax_whitelist = tuple(
            normalizer.normalize(value) for value in syntax_whitelist
        )
        if any(not value for value in self.unsupported_markers + self.syntax_whitelist):
            raise ValueError("syntax markers must be nonempty")
        self.max_input_characters = max_input_characters
        self.max_concept_groups = max_concept_groups
        self.max_alternatives_per_concept = max_alternatives_per_concept
        self.max_alternatives_total = max_alternatives_total

    def _structured_enabled(self, text: str) -> bool:
        allowed = tuple(
            span
            for phrase in self.syntax_whitelist
            for span in bounded_matches(text, phrase)
        )
        return not any(
            not any(start >= left and end <= right for left, right in allowed)
            for marker in self.unsupported_markers
            for start, end in bounded_matches(text, marker)
        )

    def _merge(self, hints: Sequence[QueryHint]) -> tuple[QueryHint, ...]:
        unique = {(hint.kind, hint.span, hint.rule_id): hint for hint in hints}
        ordered = sorted(
            unique.values(),
            key=lambda hint: (
                hint.status != "unambiguous",
                -(hint.span[1] - hint.span[0]),
                hint.span[0],
                hint.kind,
                hint.rule_id,
            ),
        )
        selected: list[QueryHint] = []
        for hint in ordered:
            if any(
                hint.kind == other.kind
                and hint.span != other.span
                and hint.span[0] < other.span[1]
                and other.span[0] < hint.span[1]
                for other in selected
            ):
                continue
            selected.append(hint)
        merged: list[QueryHint] = []
        for hint in sorted(
            selected,
            key=lambda value: (value.span[0], value.span[1], value.kind, value.rule_id),
        ):
            conflict = any(
                other is not hint
                and hint.span[0] < other.span[1]
                and other.span[0] < hint.span[1]
                and (other.span != hint.span or other.rule_id != hint.rule_id)
                for other in selected
            )
            alternatives: list[str] = []
            seen: set[str] = set()
            for value in hint.alternatives:
                normalized = self.normalizer.normalize(value)
                if normalized not in seen:
                    alternatives.append(value)
                    seen.add(normalized)
                if len(alternatives) == self.max_alternatives_per_concept:
                    break
            merged.append(
                replace(
                    hint,
                    alternatives=tuple(alternatives),
                    status="ambiguous" if conflict else hint.status,
                )
            )
        if len({hint.span for hint in merged}) > self.max_concept_groups:
            raise ValueError("query concept group budget exceeded")
        if len(merged) > self.max_alternatives_total:
            raise ValueError("query alternatives budget cannot preserve literals")
        limits = [1] * len(merged)
        remaining = self.max_alternatives_total - len(merged)
        for depth in range(1, self.max_alternatives_per_concept):
            for index, hint in enumerate(merged):
                if remaining and len(hint.alternatives) > depth:
                    limits[index] += 1
                    remaining -= 1
        return tuple(
            replace(hint, alternatives=hint.alternatives[: limits[index]])
            for index, hint in enumerate(merged)
        )

    def parse(self, raw_text: str) -> ParsedQuery:
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("query text must be nonempty")
        if len(raw_text) > self.max_input_characters:
            raise ValueError("query input character budget exceeded")
        normalized = self.normalizer.normalize(raw_text)
        stage_hints = tuple(
            hint for stage in self.stages for hint in stage.parse(normalized)
        )
        for hint in stage_hints:
            if (
                not isinstance(hint, QueryHint)
                or normalized[hint.span[0] : hint.span[1]] != hint.surface
            ):
                raise ValueError("stage returned a hint outside normalized text")
        hints = self._merge(stage_hints)
        return ParsedQuery(
            original_text=raw_text,
            normalized_text=normalized,
            hints=hints,
            structured_enabled=self._structured_enabled(normalized),
            protected_spans=self.normalizer.protected_spans(normalized, raw_text),
        )

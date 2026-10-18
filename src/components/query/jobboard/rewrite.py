"""Bounded dictionary repair. Candidate ambiguity is resolved before budgets."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from src.components.query.jobboard.lexicon import LexiconQueryStage
from src.components.query.jobboard.parser import ModularQueryParser
from src.components.query.normalization import bounded_matches
from src.domain.request.jobboard import ParseDecision, ParsedQuery
from src.domain.request.rewrite import QueryRewrite, RewriteCandidate


def one_edit(left: str, right: str) -> bool:
    """Exactly one insertion, deletion, substitution or adjacent transposition."""
    if abs(len(left) - len(right)) > 1 or left == right:
        return False
    if len(left) == len(right):
        differences = [
            i for i, (a, b) in enumerate(zip(left, right, strict=True)) if a != b
        ]
        return len(differences) == 1 or (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and left[differences[0]] == right[differences[1]]
            and left[differences[1]] == right[differences[0]]
        )
    short, long = (left, right) if len(left) < len(right) else (right, left)
    first = next((i for i, (a, b) in enumerate(zip(short, long)) if a != b), len(short))
    return short[first:] == long[first + 1 :]


@dataclass(frozen=True)
class RewriteRule:
    source: str
    target: str
    requires_any: tuple[str, ...] = ()
    mechanism: str = "curated"


class ConservativeRewriter:
    def __init__(
        self,
        parser: ModularQueryParser,
        rules: Sequence[Mapping[str, object]] = (),
        *,
        keyboard: Mapping[str, str] | None = None,
        spelling: bool = True,
        layout: bool = True,
        curated: bool = True,
        min_length: int = 5,
        max_length: int = 64,
        max_replaced_spans: int = 2,
        denylist: Sequence[str] = (),
    ) -> None:
        if any(type(v) is not bool for v in (spelling, layout, curated)) or (
            type(min_length) is not int
            or type(max_length) is not int
            or not 1 <= min_length <= max_length <= 64
            or type(max_replaced_spans) is not int
            or not 1 <= max_replaced_spans <= 2
        ):
            raise ValueError("Invalid rewrite policy")
        self.parser = parser
        self.spelling, self.layout, self.curated = spelling, layout, curated
        self.min_length, self.max_length = min_length, max_length
        self.max_replaced_spans = max_replaced_spans
        normalize = parser.normalizer.normalize
        self.denylist = frozenset(normalize(v) for v in denylist)
        self.rules: tuple[RewriteRule, ...] = tuple(self._rule(r) for r in rules)
        pairs = [(r.source, r.target) for r in self.rules]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Duplicate rewrite rule")
        # Cycles are disallowed even though runtime never cascades replacements.
        graph: dict[str, set[str]] = {}
        for source, target in pairs:
            graph.setdefault(source, set()).add(target)

        def visit(word: str, path: frozenset[str]) -> None:
            if word in path:
                raise ValueError("Rewrite cycle")
            for target in graph.get(word, ()):
                visit(target, path | {word})

        for word in graph:
            visit(word, frozenset())
        keyboard = dict(keyboard or {})
        if keyboard and (
            set(keyboard) != {"ru", "en"}
            or len(keyboard["ru"]) != len(keyboard["en"])
            or len(set(keyboard["ru"])) != len(keyboard["ru"])
            or len(set(keyboard["en"])) != len(keyboard["en"])
        ):
            raise ValueError("Keyboard map must be bijective")
        self.tables = (
            tuple(
                str.maketrans(keyboard[a], keyboard[b])
                for a, b in (("ru", "en"), ("en", "ru"))
            )
            if keyboard
            else ()
        )
        vocabulary: set[str] = set()
        for stage in parser.stages:
            if isinstance(stage, LexiconQueryStage) and stage.kind in {"role", "skill"}:
                for entry in stage.entries:
                    for surface in entry.surfaces:
                        vocabulary.update(re.findall(r"[^\W\d_]+", surface))
        self.words: dict[int, tuple[str, ...]] = {}
        for length in {len(w) for w in vocabulary}:
            self.words[length] = tuple(
                sorted(w for w in vocabulary if len(w) == length)
            )
        if any(r.target not in vocabulary for r in self.rules):
            raise ValueError("Rewrite target is absent from recognition vocabulary")

    def _rule(self, raw: Mapping[str, object]) -> RewriteRule:
        if set(raw) - {"source", "target", "requires_any", "provenance", "mechanism"}:
            raise ValueError("Unknown rewrite rule field")
        source, target = raw.get("source"), raw.get("target")
        mechanism = raw.get("mechanism", "curated")
        if mechanism not in {"curated", "layout"}:
            raise ValueError("Unknown rewrite mechanism")
        requires = raw.get("requires_any", ())
        if (
            not isinstance(source, str)
            or not source.strip()
            or not isinstance(target, str)
            or not target.isalpha()
            or isinstance(requires, str)
            or not isinstance(requires, Sequence)
            or any(not isinstance(s, str) or not s.strip() for s in requires)
        ):
            raise ValueError("Invalid curated rewrite rule")
        normalizer = self.parser.normalizer
        return RewriteRule(
            normalizer.normalize(source),
            normalizer.normalize(target),
            tuple(normalizer.normalize(str(s)) for s in requires),
            str(mechanism),
        )

    def rewrite(self, parsed: ParsedQuery) -> QueryRewrite:
        text = parsed.normalized_text
        if not parsed.structured_enabled:
            return QueryRewrite(
                parsed,
                decisions=(ParseDecision("unsupported_syntax", (0, len(text)), text),),
            )
        candidates: list[RewriteCandidate] = []
        decisions: list[ParseDecision] = []
        chosen: list[RewriteCandidate] = []
        ambiguous = False
        for match in re.finditer(r"[^\s]+", text):
            word = match.group().strip('!?():"')
            start = match.start() + match.group().find(word)
            span = (start, start + len(word))
            protected = any(
                a < span[1] and span[0] < b for a, b in parsed.protected_spans
            )
            known = any(
                h.span[0] < span[1] and span[0] < h.span[1] for h in parsed.hints
            )
            latin = bool(re.search("[a-z]", word))
            cyrillic = bool(re.search("[а-яё]", word))
            if (
                protected
                or known
                or word in self.denylist
                or not self.min_length <= len(word) <= self.max_length
                or (latin and cyrillic)
                or any(c.isdigit() for c in word)
            ):
                decisions.append(ParseDecision("protected_or_known", span, word))
                continue
            proposals: list[tuple[str, str, int | None]] = []
            if self.curated or self.layout:
                proposals.extend(
                    (r.target, r.mechanism, None)
                    for r in self.rules
                    if r.source == word
                    and (self.curated if r.mechanism == "curated" else self.layout)
                    and (
                        not r.requires_any
                        or any(bounded_matches(text, v) for v in r.requires_any)
                    )
                )
            if self.layout:
                for table in self.tables:
                    target = word.translate(table)
                    if (
                        target != word
                        and target.isalpha()
                        and target in self.words.get(len(target), ())
                    ):
                        proposals.append((target, "layout", None))
            if self.spelling and word.isalpha():
                proposals.extend(
                    (target, "spelling", 1)
                    for size in (len(word) - 1, len(word), len(word) + 1)
                    for target in self.words.get(size, ())
                    if one_edit(word, target)
                )
            evidence: dict[tuple[str, str], RewriteCandidate] = {}
            for target, mechanism, distance in proposals:
                trial = self.parser.parse(text[: span[0]] + target + text[span[1] :])
                end = span[0] + len(target)
                hints = [
                    h
                    for h in trial.hints
                    if h.kind in {"role", "skill"}
                    and h.span[0] <= span[0]
                    and end <= h.span[1]
                ]
                longest = max((h.span[1] - h.span[0] for h in hints), default=0)
                for hint in hints:
                    if hint.span[1] - hint.span[0] != longest:
                        continue
                    key = (hint.rule_id, target)
                    previous = evidence.get(key)
                    evidence[key] = RewriteCandidate(
                        span,
                        word,
                        target,
                        hint.rule_id,
                        (*previous.mechanisms, mechanism)
                        if previous and mechanism not in previous.mechanisms
                        else previous.mechanisms
                        if previous
                        else (mechanism,),
                        previous.edit_distance if previous else distance,
                        "candidate",
                        "ambiguous_target"
                        if hint.status == "ambiguous"
                        else "confirmed_concept",
                    )
            values = list(evidence.values())
            if not values:
                decisions.append(ParseDecision("no_candidate", span, word))
                continue
            if len({v.concept_id for v in values}) != 1 or any(
                v.reason == "ambiguous_target" for v in values
            ):
                ambiguous = True
                decisions.append(ParseDecision("ambiguous_candidates", span, word))
            else:
                chosen.append(
                    min(
                        values,
                        key=lambda v: (
                            min(
                                ("curated", "layout", "spelling").index(m)
                                for m in v.mechanisms
                            ),
                            v.edit_distance or 0,
                            v.target_text,
                        ),
                    )
                )
            candidates.extend(values)
        reason = (
            "ambiguous_candidates"
            if ambiguous
            else "rewrite_budget_exceeded"
            if len(chosen) > self.max_replaced_spans
            else "accepted"
        )
        if reason != "accepted":
            decisions.append(ParseDecision(reason, (0, len(text)), text))
            return QueryRewrite(
                parsed,
                tuple(
                    replace(c, decision="rejected", reason=reason) for c in candidates
                ),
                decisions=tuple(decisions),
            )
        rewritten = text
        for candidate in sorted(chosen, key=lambda c: c.source_span, reverse=True):
            a, b = candidate.source_span
            rewritten = rewritten[:a] + candidate.target_text + rewritten[b:]
        final = tuple(
            replace(
                c,
                decision="accepted" if c in chosen else "rejected",
                reason="confirmed_concept"
                if c in chosen
                else "same_concept_alternative",
            )
            for c in candidates
        )
        return QueryRewrite(
            parsed,
            final,
            self.parser.parse(rewritten) if chosen else None,
            tuple(decisions),
        )

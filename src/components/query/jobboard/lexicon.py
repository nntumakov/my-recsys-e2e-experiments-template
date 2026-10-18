from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from src.components.query.jobboard.abstract import BaseQueryStage
from src.components.query.normalization import NormalizeText, bounded_matches
from src.domain.request.jobboard import HINT_KINDS, HintKind, QueryHint

_KINDS = HINT_KINDS
_RELATIONS = {"curated_equivalent", "curated_translation"}
_PREFIXES = {
    "role": "role.",
    "skill": "skill.",
    "seniority": "level.",
    "work_mode": "mode.",
    "location": "place.",
    "experience": "experience.",
    "schedule": "schedule.",
    "employment": "employment.",
    "qualification": "qualification.",
    "work_condition": "condition.",
}


@dataclass(frozen=True)
class LexiconAlternative:
    text: str
    relation: str


@dataclass(frozen=True)
class LexiconEntry:
    id: str
    canonical: str
    surfaces: tuple[str, ...]
    alternatives: tuple[LexiconAlternative, ...]
    ambiguous_surfaces: tuple[str, ...]
    requires_any: tuple[str, ...]
    surface_prefixes: Mapping[str, tuple[str, ...]]


class LexiconQueryStage(BaseQueryStage):
    def __init__(self, kind: HintKind, entries: Sequence[Mapping[str, object]]) -> None:
        if kind not in _KINDS:
            raise ValueError("invalid lexicon kind")
        self.kind = kind
        self._normalizer = NormalizeText()
        parsed: list[LexiconEntry] = []
        seen_ids: set[str] = set()
        for raw in entries:
            entry = self._entry(raw)
            if not entry.id.startswith(_PREFIXES[kind]) or entry.id in seen_ids:
                raise ValueError("duplicate or mismatched lexicon id")
            seen_ids.add(entry.id)
            parsed.append(entry)
        self.entries = tuple(parsed)

    def _strings(self, value: object, name: str) -> tuple[str, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError(f"{name} must be a list")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{name} must contain nonempty strings")
            normalized = self._normalizer.normalize(item)
            if normalized not in result:
                result.append(normalized)
        return tuple(result)

    def _entry(self, raw: Mapping[str, object]) -> LexiconEntry:
        expected = {
            "id",
            "canonical",
            "surfaces",
            "alternatives",
            "ambiguous_surfaces",
            "requires_any",
        }
        if set(raw) - {"surface_prefixes"} != expected:
            raise ValueError("lexicon entry fields do not match schema")
        identifier, canonical = raw["id"], raw["canonical"]
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("lexicon entry requires id")
        if not isinstance(canonical, str) or not canonical.strip():
            raise ValueError("lexicon entry requires canonical label")
        surfaces = self._strings(raw["surfaces"], "surfaces")
        if not surfaces or self._normalizer.normalize(canonical) not in surfaces:
            raise ValueError("lexicon surfaces must include canonical")
        ambiguous = self._strings(raw["ambiguous_surfaces"], "ambiguous_surfaces")
        if any(surface not in surfaces for surface in ambiguous):
            raise ValueError("ambiguous surface is not a recognition form")
        requirements = self._strings(raw["requires_any"], "requires_any")
        alternatives_raw = raw["alternatives"]
        if not isinstance(alternatives_raw, Sequence) or isinstance(
            alternatives_raw, (str, bytes)
        ):
            raise ValueError("alternatives must be a list")
        alternatives: list[LexiconAlternative] = []
        for item in alternatives_raw:
            if not isinstance(item, Mapping) or set(item) != {"text", "relation"}:
                raise ValueError("invalid lexicon alternative")
            value, relation = item["text"], item["relation"]
            if (
                not isinstance(value, str)
                or not value.strip()
                or relation not in _RELATIONS
            ):
                raise ValueError("invalid lexicon alternative")
            normalized = self._normalizer.normalize(value)
            if normalized in ambiguous:
                raise ValueError("ambiguous surface cannot be an expansion alternative")
            if normalized not in {
                self._normalizer.normalize(a.text) for a in alternatives
            }:
                alternatives.append(LexiconAlternative(value, cast(str, relation)))
        prefixes = raw.get("surface_prefixes", {})
        if not isinstance(prefixes, Mapping):
            raise ValueError("Surface prefixes must be a mapping")
        normalized_prefixes = {}
        for surface, values in prefixes.items():
            if not isinstance(surface, str):
                raise ValueError("Invalid context surface")
            surface = self._normalizer.normalize(surface)
            if surface not in surfaces:
                raise ValueError("Context rule requires a recognition surface")
            normalized_prefixes[surface] = self._strings(values, "surface_prefixes")
            if not normalized_prefixes[surface]:
                raise ValueError("Surface context must be nonempty")
        return LexiconEntry(
            id=identifier,
            canonical=canonical,
            surfaces=surfaces,
            alternatives=tuple(alternatives),
            ambiguous_surfaces=ambiguous,
            requires_any=requirements,
            surface_prefixes=normalized_prefixes,
        )

    def parse(self, normalized_text: str) -> tuple[QueryHint, ...]:
        hints: list[QueryHint] = []
        for entry in self.entries:
            for surface in entry.surfaces:
                for span in bounded_matches(normalized_text, surface):
                    prefixes = entry.surface_prefixes.get(surface)
                    before = normalized_text[: span[0]].rstrip()
                    if prefixes and not any(
                        any(
                            end == len(before)
                            for _, end in bounded_matches(before, prefix)
                        )
                        for prefix in prefixes
                    ):
                        continue

                    has_context = not entry.requires_any or any(
                        bounded_matches(normalized_text, required)
                        for required in entry.requires_any
                    )
                    ambiguous = surface in entry.ambiguous_surfaces and (
                        not entry.requires_any or not has_context
                    )
                    alternatives = tuple(
                        dict.fromkeys(
                            (normalized_text[span[0] : span[1]], entry.canonical)
                            + tuple(alt.text for alt in entry.alternatives)
                        )
                    )
                    hints.append(
                        QueryHint(
                            kind=self.kind,
                            span=span,
                            surface=normalized_text[span[0] : span[1]],
                            canonical=entry.canonical,
                            alternatives=alternatives,
                            rule_id=entry.id,
                            status="ambiguous" if ambiguous else "unambiguous",
                        )
                    )
        return tuple(hints)

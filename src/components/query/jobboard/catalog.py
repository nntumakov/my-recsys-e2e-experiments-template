"""Build query parsers from Hydra lexicons."""

from collections.abc import Mapping, Sequence

from src.components.query.jobboard.lexicon import _PREFIXES, LexiconQueryStage
from src.components.query.jobboard.slots import SlotQueryParser
from src.components.query.normalization import NormalizeText
from src.domain.request.jobboard import HINT_KINDS


def work_parser(
    entries: Mapping[str, Sequence[Mapping[str, object]]],
    protected: Sequence[str],
    introductions: Sequence[str],
    unsupported_markers: Sequence[str],
    syntax_whitelist: Sequence[str],
) -> SlotQueryParser:
    stages = []
    for kind, records in entries.items():
        if kind not in HINT_KINDS:
            raise ValueError("Unknown dictionary kind")
        expanded = []
        for record in records:
            if set(record) - {
                "id",
                "canonical",
                "forms",
                "aliases",
                "ambiguous",
                "requires",
                "provenance",
                "surface_prefixes",
            }:
                raise ValueError("Unknown dictionary field")
            identifier, canonical = record.get("id"), record.get("canonical")
            if not isinstance(identifier, str) or not isinstance(canonical, str):
                raise ValueError("Dictionary entry requires id and canonical")
            forms, aliases = record.get("forms", []), record.get("aliases", [])
            if (
                not isinstance(forms, Sequence)
                or isinstance(forms, str)
                or not isinstance(aliases, Sequence)
                or isinstance(aliases, str)
                or any(
                    not isinstance(v, str) or not v.strip() for v in (*forms, *aliases)
                )
            ):
                raise ValueError("Dictionary forms must be lists of nonempty strings")
            provenance = record.get("provenance", "curated-project-seed")
            if not isinstance(provenance, str) or not provenance.strip():
                raise ValueError("Dictionary provenance must be nonempty")
            expanded.append(
                {
                    "id": _PREFIXES[kind] + identifier,
                    "canonical": canonical,
                    "surfaces": list(dict.fromkeys([canonical, *forms])),
                    "alternatives": [
                        {"text": a, "relation": "curated_equivalent"} for a in aliases
                    ],
                    "ambiguous_surfaces": record.get("ambiguous", []),
                    "requires_any": record.get("requires", []),
                    "surface_prefixes": record.get("surface_prefixes", {}),
                }
            )
        stages.append(LexiconQueryStage(kind, expanded))
    return SlotQueryParser(
        NormalizeText(protected),
        stages,
        unsupported_markers=unsupported_markers,
        syntax_whitelist=syntax_whitelist,
        introductions=introductions,
    )

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest
from omegaconf import ListConfig, OmegaConf

from src.components.query.jobboard.abstract import BaseQueryStage
from src.components.query.jobboard.lexicon import LexiconQueryStage
from src.components.query.jobboard.parser import ModularQueryParser
from src.components.query.normalization import NormalizeText
from src.domain.request.jobboard import QueryHint


def entry(
    identifier: str,
    canonical: str,
    surfaces: list[str],
    *,
    aliases: list[str] | None = None,
    ambiguous: list[str] | None = None,
    requires: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": identifier,
        "canonical": canonical,
        "surfaces": surfaces,
        "alternatives": [
            {"text": text, "relation": "curated_equivalent"} for text in aliases or []
        ],
        "ambiguous_surfaces": ambiguous or [],
        "requires_any": requires or [],
    }


def parser(
    *stages: BaseQueryStage, max_input_characters: int = 512
) -> ModularQueryParser:
    return ModularQueryParser(
        NormalizeText(["C++", "C#", ".NET", "1C", "1С", "Go", "R", "JS"]),
        stages,
        unsupported_markers=["или", "or", "не", "not", "без", "without", "no"],
        syntax_whitelist=["без опыта", "without experience", "no experience required"],
        max_input_characters=max_input_characters,
    )


def test_unicode_offsets_and_protected_technical_terms() -> None:
    stage = LexiconQueryStage(
        "skill",
        [
            entry("skill.cpp", "C++", ["C++"]),
            entry("skill.csharp", "C#", ["C#"]),
            entry("skill.dotnet", ".NET", [".NET"]),
            entry("skill.onec", "1С", ["1С", "1C"]),
        ],
    )
    result = parser(stage).parse("  Ｃ＋＋   C# .NET 1С  и  1C  ")
    assert result.normalized_text == "c++ c# .net 1с и 1c"
    assert [hint.rule_id for hint in result.hints] == [
        "skill.cpp",
        "skill.csharp",
        "skill.dotnet",
        "skill.onec",
        "skill.onec",
    ]
    assert all(
        result.normalized_text[hint.span[0] : hint.span[1]] == hint.surface
        for hint in result.hints
    )
    assert [
        result.normalized_text[start:end] for start, end in result.protected_spans
    ] == ["c++", "c#", ".net", "1с", "1c"]


def test_sentence_punctuation_is_a_boundary_but_technical_suffix_is_not() -> None:
    stage = LexiconQueryStage(
        "skill",
        [
            entry("skill.python", "Python", ["Python"]),
            entry("skill.c", "C", ["C"]),
            entry("skill.nodejs", "Node.js", ["Node.js"]),
        ],
    )
    result = parser(stage).parse("(Python.), C++ C# Node.js")
    assert [hint.rule_id for hint in result.hints] == ["skill.python", "skill.nodejs"]


def test_ambiguity_context_boundaries_and_longest_span() -> None:
    roles = LexiconQueryStage(
        "role",
        [
            entry("role.project_manager", "project manager", ["project manager", "pm"]),
            entry("role.product_manager", "product manager", ["product manager", "pm"]),
            entry(
                "role.backend_developer",
                "backend developer",
                ["backend developer", "backend"],
            ),
        ],
    )
    skills = LexiconQueryStage(
        "skill",
        [
            entry(
                "skill.go",
                "Go",
                ["Go", "golang"],
                ambiguous=["go"],
                requires=["developer"],
            ),
            entry("skill.java", "Java", ["Java"]),
        ],
    )
    query_parser = parser(roles, skills)
    assert {hint.status for hint in query_parser.parse("PM developer").hints} == {
        "ambiguous"
    }
    assert query_parser.parse("Go").hints[0].status == "ambiguous"
    assert query_parser.parse("Go developer").hints[0].status == "unambiguous"
    always_ambiguous = LexiconQueryStage(
        "role",
        [entry("role.manual_qa", "manual qa", ["manual qa", "qa"], ambiguous=["qa"])],
    )
    assert parser(always_ambiguous).parse("qa").hints[0].status == "ambiguous"
    assert [
        hint.rule_id for hint in query_parser.parse("Google JavaScript").hints
    ] == []
    assert [hint.rule_id for hint in query_parser.parse("backend developer").hints] == [
        "role.backend_developer"
    ]


def test_unsupported_syntax_bypasses_entire_structured_path() -> None:
    stage = LexiconQueryStage(
        "seniority", [entry("level.entry", "entry level", ["entry level", "без опыта"])]
    )
    query_parser = parser(stage)
    assert query_parser.parse("без опыта аналитик").structured_enabled
    assert not query_parser.parse("без ночных смен").structured_enabled
    assert not query_parser.parse("python или java").structured_enabled
    assert not query_parser.parse("не продажи").structured_enabled
    assert not query_parser.parse("без опыта или стажировка").structured_enabled
    assert query_parser.parse("без опыта аналитик").hints[0].rule_id == "level.entry"


def test_independent_stage_inputs_and_budgets() -> None:
    class RecordingStage(BaseQueryStage):
        def __init__(self) -> None:
            self.seen: list[str] = []

        def parse(self, normalized_text: str) -> tuple[QueryHint, ...]:
            self.seen.append(normalized_text)
            return ()

    first, second = RecordingStage(), RecordingStage()
    result = parser(first, second).parse(" Ｐｙｔｈｏｎ  remote ")
    assert first.seen == second.seen == ["python remote"]
    assert result.hints == ()
    with pytest.raises(ValueError, match="character budget"):
        parser(max_input_characters=512).parse("x" * 513)
    with pytest.raises(ValueError, match="nonempty"):
        parser().parse(" \t ")


def test_stage_wrong_span_fails_before_overlap_resolution() -> None:
    class BrokenStage(BaseQueryStage):
        def parse(self, normalized_text: str) -> tuple[QueryHint, ...]:
            return (
                QueryHint(
                    "skill", (0, 3), "wrong", "Wrong", (), "skill.wrong", "ambiguous"
                ),
            )

    valid = LexiconQueryStage("skill", [entry("skill.python", "Python", ["Python"])])
    with pytest.raises(ValueError, match="stage returned"):
        parser(valid, BrokenStage()).parse("python")


def test_unknown_uppercase_acronym_is_protected_from_fuzzy() -> None:
    result = parser().parse("ABCDE developer")
    assert [
        result.normalized_text[start:end] for start, end in result.protected_spans
    ] == ["abcde"]
    custom = ModularQueryParser(NormalizeText(["data science"]), [])
    result = custom.parse("data science, Python")
    assert [
        result.normalized_text[start:end] for start, end in result.protected_spans
    ] == ["data science"]


def test_invalid_entries_and_immutable_result() -> None:
    bad = entry("skill.python", "Python", ["Python"])
    bad["alternatives"] = [{"text": "Java", "relation": "related_skill"}]
    with pytest.raises(ValueError, match="alternative"):
        LexiconQueryStage("skill", [bad])
    result = parser().parse("python")
    with pytest.raises(FrozenInstanceError):
        setattr(result, "normalized_text", "changed")
    with pytest.raises(ValueError, match="immutable"):
        QueryHint(
            "skill",
            (0, 6),
            "python",
            "Python",
            cast(tuple[str, ...], ["python"]),
            "skill.python",
            "unambiguous",
        )  # type: ignore[arg-type]


def test_global_alias_budget_keeps_literals_and_order() -> None:
    stage = LexiconQueryStage(
        "skill",
        [
            entry("skill.python", "Python", ["Python"], aliases=["питон", "пайтон"]),
            entry("skill.java", "Java", ["Java"], aliases=["джава", "язык java"]),
        ],
    )
    query_parser = ModularQueryParser(
        NormalizeText(),
        [stage],
        max_alternatives_per_concept=4,
        max_alternatives_total=3,
    )
    hints = query_parser.parse("python java").hints
    assert [hint.alternatives for hint in hints] == [("python", "питон"), ("java",)]


def test_shipped_lexicon_parses_jobboard_cases() -> None:
    root = Path(__file__).resolve().parents[1] / "configs/jobboard/lexical/lexicons"
    stages = [
        LexiconQueryStage(kind, OmegaConf.load(root / name).entries)
        for kind, name in (
            ("role", "roles/ru-en.yaml"),
            ("skill", "skills/ru-en.yaml"),
            ("seniority", "seniority/ru-en.yaml"),
            ("work_mode", "work_modes/ru-en.yaml"),
            ("location", "locations/ru-en.yaml"),
        )
    ]
    syntax = OmegaConf.load(root / "syntax/conservative.yaml")
    protected = OmegaConf.load(root / "protected/default.yaml")
    query_parser = ModularQueryParser(
        NormalizeText(protected.terms),
        stages,
        unsupported_markers=syntax.unsupported_markers,
        syntax_whitelist=syntax.whitelist,
    )
    assert {hint.rule_id for hint in query_parser.parse("C# .NET backend").hints} == {
        "skill.csharp",
        "skill.dotnet",
        "role.backend_developer",
    }
    assert {hint.status for hint in query_parser.parse("PM").hints} == {"ambiguous"}
    assert query_parser.parse("Go").hints[0].status == "ambiguous"
    assert query_parser.parse("Go developer").hints[0].status == "unambiguous"
    assert not query_parser.parse("python или java").structured_enabled
    assert query_parser.parse("без опыта аналитик").structured_enabled
    assert query_parser.parse("без коммерческого опыта аналитик").structured_enabled
    assert query_parser.parse("no experience required").structured_enabled
    assert not query_parser.parse("без ночных смен").structured_enabled
    assert query_parser.parse("R").hints[0].status == "ambiguous"
    assert query_parser.parse("R data analyst").hints[0].status == "unambiguous"
    assert not query_parser.parse("Google").hints
    assert {hint.rule_id for hint in query_parser.parse("JavaScript").hints} == {
        "skill.javascript"
    }
    assert {hint.rule_id for hint in query_parser.parse("C#").hints} == {"skill.csharp"}
    assert not any(
        hint.rule_id == "skill.c" and hint.status == "unambiguous"
        for hint in query_parser.parse("водитель категории C").hints
    )

    expected_confirmed = {
        "q01": {"skill.python", "role.backend_developer"},
        "q02": {"skill.java"},
        "q03": {"skill.javascript", "role.frontend_developer"},
        "q04": {"skill.cpp"},
        "q05": {"skill.csharp", "skill.dotnet", "role.backend_developer"},
        "q06": {"skill.go", "role.backend_developer"},
        "q07": {"role.project_manager"},
        "q08": {"role.product_manager"},
        "q09": {"role.data_analyst", "level.entry"},
        "q10": {"role.accountant", "skill.onec"},
        "q11": {"role.driver"},
        "q12": {"role.nurse", "place.moscow"},
        "q13": {"role.sales_manager", "mode.remote"},
        "q14": {"skill.python"},
        "q15": {"level.senior", "role.recruiter", "mode.remote", "place.moscow"},
        "q16": {"role.data_scientist", "skill.python"},
        "q17": {"skill.linux"},
        "q18": {"role.electrician", "place.novosibirsk"},
        "q19": {"role.ux_designer", "place.london"},
        "q20": {"level.entry"},
        "q21": set(),
        "q22": {"skill.python"},
        "q23": {"skill.python", "mode.remote"},
        "q24": set(),
    }
    cases = cast(ListConfig, OmegaConf.load(root.parent / "cases/synthetic.yaml"))
    for case in cases:
        if case.key not in expected_confirmed:
            continue
        query = next(
            condition.text
            for condition in case.data.filters
            if condition._target_.endswith("QueryFilter")
        )
        parsed = query_parser.parse(query)
        assert {
            hint.rule_id for hint in parsed.hints if hint.status == "unambiguous"
        } == expected_confirmed[case.key], case.key


def test_domain_query_values_reject_mutable_or_invalid_scalar_fields():
    from src.domain.request.jobboard import ParsedQuery

    with pytest.raises(ValueError, match="surface, canonical"):
        QueryHint(
            "skill",
            (0, 6),
            "python",
            cast(str, ["Python"]),
            (),
            "skill.python",
            "unambiguous",
        )
    with pytest.raises(ValueError, match="immutable pairs"):
        ParsedQuery("python", "python", (), protected_spans=((False, 6),))
    with pytest.raises(ValueError, match="syntax policy"):
        ParsedQuery("python", "python", (), structured_enabled=cast(bool, "false"))


def test_uppercase_protection_tracks_normalized_occurrence_offsets():
    result = parser().parse("  ＤＥＶＥＬＯＰＲ\t developr   DEVELOPR")
    assert result.normalized_text == "developr developr developr"
    assert result.protected_spans == ((0, 8), (18, 26))

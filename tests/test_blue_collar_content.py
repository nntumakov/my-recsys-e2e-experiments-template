from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest
from hydra.utils import instantiate
from omegaconf import OmegaConf
from test_research_ranking import configured

ROOT = Path(__file__).resolve().parents[1]
CASES = cast(
    list[dict[str, Any]],
    OmegaConf.to_container(
        OmegaConf.load(ROOT / "configs/jobboard/lexical/cases/blue-collar/queries.yaml")
    ),
)


@pytest.fixture(scope="module")
def experiment():
    return instantiate(
        configured("http://localhost:9200", "experiment.variants=[c4,c5]").experiment,
        _convert_="all",
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"] + ":" + c["text"])
def test_authored_understanding_cases(experiment, case):
    plans = experiment.preview(case["text"])
    trace = plans["c4"].understanding
    expected = case["expected"]
    if "concepts" in expected:
        recognized = {
            h["rule_id"] for h in trace["hints"] if h["status"] == "unambiguous"
        }
        assert set(expected["concepts"]) <= recognized
    if "structured" in expected:
        assert trace["syntax_enabled"] is expected["structured"]
    if "rewrite" in expected:
        assert plans["c5"].understanding["accepted_rewrite"] == expected["rewrite"]


def test_content_volume_is_not_just_empty_groups(experiment):
    parser = experiment.registry["c4"].parser
    by_kind = {stage.kind: stage.entries for stage in parser.stages}
    assert len(by_kind["role"]) == 64
    assert sum(len(e.surfaces) for e in by_kind["role"]) >= 320
    assert len(by_kind["skill"]) >= 40
    assert len(by_kind["qualification"]) >= 16
    assert len(by_kind["location"]) >= 20
    assert (
        sum(
            len(by_kind[k])
            for k in ("experience", "schedule", "employment", "work_condition")
        )
        >= 32
    )
    groups = Counter(case["group"] for case in CASES)
    assert groups["concepts"] >= 80 and groups["rewrite"] >= 60
    assert groups["guards"] >= 30 and groups["ranking"] >= 30
    assert len({c["text"].casefold() for c in CASES}) == len(CASES)
    rules = experiment.registry["c5"].rewriter.rules
    assert len(rules) >= 40
    assert any(r.requires_any for r in rules)
    assert [h.rule_id for h in parser.parse("водитель погрузчика").hints] == [
        "role.forklift_driver"
    ]


def test_fictional_corpus_and_contrasts_are_complete():
    import json

    vacancies = json.loads(
        (ROOT / "tests/fixtures/blue-collar/vacancies.json").read_text()
    )
    contrasts = OmegaConf.to_container(
        OmegaConf.load(
            ROOT / "configs/jobboard/lexical/cases/blue-collar/contrasts.yaml"
        )
    )
    assert isinstance(contrasts, list)
    assert len(vacancies) >= 96 and len(contrasts) >= 32
    by_id = {v["id"]: v["fields"] for v in vacancies}
    assert len(by_id) == len(vacancies)
    assert len({f["description"] for f in by_id.values()}) >= 64
    assert all(v["fields"]["vacancy_id"] == v["id"] for v in vacancies)
    for group in contrasts:
        assert len(set(group["documents"])) >= 2
        assert set(group["documents"]) <= set(by_id)
        assert group["rationale"]
    for city in ["Москва", "Казань", "Тула", "Самара"]:
        assert any(f["city"] == city for f in by_id.values())
    assert any("published_at" not in f for f in by_id.values())
    assert any(f.get("card_completeness") is None for f in by_id.values())
    assert any(
        f.get("card_completeness", 0) is not None and f.get("card_completeness", 0) < 0
        for f in by_id.values()
    )


def test_each_curated_rule_respects_negation_and_token_boundaries(experiment):
    rewriter = experiment.registry["c5"].rewriter
    parser = experiment.registry["c5"].parser
    for rule in rewriter.rules:
        suffix = " офиса" if rule.target == "уборщик" else ""
        assert (
            rewriter.rewrite(parser.parse("не " + rule.source + suffix)).rewritten
            is None
        )
        assert rewriter.rewrite(parser.parse(rule.source + "xyz")).rewritten is None


def test_lexicon_expansions_do_not_merge_neighbor_professions(experiment):
    parser = experiment.registry["c4"].parser
    pairs = [
        ("грузчик", "водитель погрузчика"),
        ("оператор ЧПУ", "наладчик ЧПУ"),
        ("повар", "помощник повара"),
        ("уборщик помещений", "дворник"),
        ("кладовщик", "комплектовщик"),
        ("охранник", "контролёр КПП"),
    ]
    for left, right in pairs:
        a = parser.parse(left).hints
        b = parser.parse(right).hints
        assert {h.rule_id for h in a if h.kind == "role"}.isdisjoint(
            h.rule_id for h in b if h.kind == "role"
        )
        assert right.casefold() not in {form for h in a for form in h.alternatives}
        assert left.casefold() not in {form for h in b for form in h.alternatives}
    for query in ["работа с проживанием", "грузчик в Москве", "оператор станков с ЧПУ"]:
        assert all(h.kind != "qualification" for h in parser.parse(query).hints)


def test_prepositions_and_salary_units_do_not_become_false_requirements(experiment):
    plans = experiment.preview("ищу работу грузчиком в Москве")
    assert plans["c4"].understanding["residual"] == []
    for text in [
        "от 80000",
        "80 тысяч на руки",
        "3500 за смену",
        "до 120000",
        "ежедневно 4000",
    ]:
        trace = experiment.preview(text)["c4"].understanding
        assert any(
            d["code"] == "unsupported_salary_constraint" for d in trace["decisions"]
        )
        assert trace["residual"]

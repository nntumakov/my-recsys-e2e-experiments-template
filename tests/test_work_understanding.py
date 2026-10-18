from copy import deepcopy
from typing import Any, cast

import pytest

from src.components.query.jobboard.catalog import work_parser
from src.components.query.jobboard.rewrite import ConservativeRewriter, one_edit
from src.core.transformers.opensearch.jobboard.query import SourceProfile
from src.core.transformers.opensearch.jobboard.slots import (
    SlotQueryConfig,
    compile_slots,
)
from src.domain.request.filters import QueryFilter, TermFilter
from src.domain.request.request import Request


@pytest.fixture
def parser():
    return work_parser(
        entries={
            "role": [
                {"id": "driver", "canonical": "водитель", "forms": ["водителем"]},
                {"id": "forklift", "canonical": "водитель погрузчика"},
                {
                    "id": "picker",
                    "canonical": "комплектовщик",
                    "aliases": ["сборщик заказов"],
                },
                {"id": "loader", "canonical": "грузчик"},
                {"id": "stock", "canonical": "кладовщик"},
                {"id": "cook", "canonical": "повар"},
                {
                    "id": "cleaner",
                    "canonical": "уборщик помещений",
                    "forms": ["уборщик"],
                    "ambiguous": ["уборщик"],
                    "requires": ["офиса"],
                },
            ],
            "skill": [
                {"id": "forklift", "canonical": "погрузчик", "forms": ["погрузчика"]},
                {
                    "id": "scanner",
                    "canonical": "ТСД",
                    "aliases": ["терминал сбора данных"],
                },
            ],
            "experience": [{"id": "none", "canonical": "без опыта"}],
            "schedule": [{"id": "two", "canonical": "2/2"}],
            "qualification": [{"id": "ce", "canonical": "категории CE"}],
        },
        protected=["ТСД", "ЧПУ", "CE", "C++"],
        introductions=["ищу работу"],
        unsupported_markers=["не", "без", "или"],
        syntax_whitelist=["без опыта"],
    )


def test_containment_is_opt_in_and_introductions_do_not_erase_roles(parser):
    parsed = parser.parse("ищу работу водителем без опыта 2/2")
    assert [h.kind for h in parsed.hints] == ["role", "experience", "schedule"]
    assert parsed.ignored_spans == ((0, 10),)
    parsed = parser.parse("водитель погрузчика")
    assert [h.rule_id for h in parsed.hints] == ["role.forklift"]
    assert any(d.code == "contained_in_role" for d in parsed.decisions)
    assert not parser.parse("грузчик не вахта").structured_enabled
    assert any(
        d.code == "unsupported_salary_constraint"
        for d in parser.parse("водитель от 100000 на руки").decisions
    )


@pytest.mark.parametrize(
    "left,right",
    [
        ("грузчк", "грузчик"),
        ("грузччик", "грузчик"),
        ("грузщик", "грузчик"),
        ("грузичк", "грузчик"),
    ],
)
def test_single_edit_operations(left, right):
    assert one_edit(left, right)
    assert one_edit(right, left)
    assert not one_edit(left, "кладовщик")
    assert not one_edit(right, right)


def test_rewrite_preserves_original_and_uses_all_channels(parser):
    rewrite = ConservativeRewriter(
        parser,
        [{"source": "gruzchik", "target": "грузчик"}],
        keyboard={
            "ru": "йцукенгшщзхъфывапролджэячсмитьбюё",
            "en": "qwertyuiop[]asdfghjkl;'zxcvbnm,.`",
        },
    )
    original = parser.parse("грузщик rkfljdobr")
    result = rewrite.rewrite(original)
    assert result.original is original
    assert original.original_text == "грузщик rkfljdobr"
    assert result.rewritten is not None
    assert result.rewritten.normalized_text == "грузчик кладовщик"
    assert {c.mechanisms for c in result.candidates} == {("spelling",), ("layout",)}
    translit = rewrite.rewrite(parser.parse("gruzchik")).rewritten
    assert translit is not None and translit.normalized_text == "грузчик"
    assert rewrite.rewrite(parser.parse("грузщик rkfljdobr вадитель")).rewritten is None
    assert rewrite.rewrite(parser.parse("не грузщик")).rewritten is None
    assert rewrite.rewrite(parser.parse("ТСД C++ 2/2")).rewritten is None
    assert rewrite.rewrite(parser.parse("e,jhobr")).rewritten is None
    layout = rewrite.rewrite(parser.parse("e,jhobr офиса")).rewritten
    assert layout is not None and layout.normalized_text == "уборщик офиса"


def test_all_channels_checked_before_accepting_and_no_cascade(parser):
    rewriter = ConservativeRewriter(
        parser, [{"source": "грузщик", "target": "кладовщик"}]
    )
    result = rewriter.rewrite(parser.parse("грузщик вадитель"))
    assert result.rewritten is None
    assert {c.concept_id for c in result.candidates} >= {"role.loader", "role.stock"}
    assert any(d.code == "ambiguous_candidates" for d in result.decisions)
    with pytest.raises(ValueError, match="cycle"):
        ConservativeRewriter(
            parser,
            [
                {"source": "грузчик", "target": "кладовщик"},
                {"source": "кладовщик", "target": "грузчик"},
            ],
        )


def test_slot_routing_qualification_and_filters(parser):
    source = SourceProfile("jobs", ["title", "body"], "title", {"active": "boolean"})
    config = SlotQueryConfig(
        field_weights_by_kind={"role": {"title": 4}, "skill": {"body": 3}},
        attribute_boosts={"schedule": 0},
    )
    text = "комплектовщик ТСД 2/2"
    filters = Request((QueryFilter(text), TermFilter("active", True)))
    before = deepcopy(parser.parse(text))
    body = compile_slots(before, filters, {}, source, config, 20)
    outer = cast(dict[str, Any], body["query"])["bool"]
    assert outer["filter"] == [{"term": {"active": True}}]
    concept = outer["must"][-1]["dis_max"]["queries"][1]["bool"]
    coverage = concept["must"][0]["bool"]
    assert coverage["minimum_should_match"] == 2
    assert "title^4" in str(coverage["should"][0])
    assert "body^3" in str(coverage["should"][1])
    assert concept["should"] == []
    assert before == parser.parse(text)
    text = "водитель категории CE"
    body = compile_slots(
        parser.parse(text), Request((QueryFilter(text),)), {}, source, config, 20
    )
    concept = cast(dict[str, Any], body["query"])["bool"]["must"][-1]["dis_max"][
        "queries"
    ][1]["bool"]
    assert "категории ce" in str(concept["must"][0]["bool"]["must"])
    with pytest.raises(ValueError, match="undeclared"):
        compile_slots(
            parser.parse(text),
            Request((QueryFilter(text),)),
            {},
            source,
            SlotQueryConfig(field_weights_by_kind={"role": {"missing": 1}}),
            20,
        )

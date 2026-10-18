"""Contracts and controlled rank oracles, never claims about fixture BM25."""

import json
from copy import deepcopy

import pytest

from src.components.query.jobboard.catalog import work_parser
from src.components.query.study import decompose_query
from src.core.registry import BasicTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.jobboard.query import SourceProfile
from src.core.transformers.opensearch.jobboard.slots import SlotQueryConfig
from src.core.transformers.opensearch.study import FulltextStudyTransformer
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter, TermFilter
from src.domain.study import StudySERP
from src.integrations.opensearch.client import OpenSearchIntegration


@pytest.fixture
def parser():
    return work_parser(
        entries={
            "role": [
                {
                    "id": "picker",
                    "canonical": "комплектовщик",
                    "aliases": ["сборщик заказов"],
                },
                {"id": "driver", "canonical": "водитель"},
            ],
            "skill": [{"id": "scanner", "canonical": "ТСД"}],
            "qualification": [{"id": "ce", "canonical": "категории CE"}],
            "experience": [{"id": "none", "canonical": "без опыта"}],
            "schedule": [
                {"id": "two", "canonical": "2/2"},
                {"id": "five", "canonical": "5/2"},
            ],
            "location": [{"id": "moscow", "canonical": "Москва"}],
            "work_condition": [{"id": "home", "canonical": "проживание"}],
        },
        protected=["ТСД", "CE"],
        introductions=["ищу работу"],
        unsupported_markers=["не", "без", "или"],
        syntax_whitelist=["без опыта"],
    )


def payload(text="комплектовщик ТСД без опыта 2/2"):
    return PipelineData(
        filters=(
            QueryFilter(text),
            TermFilter("city", "Москва"),
            TermFilter("active", True),
        )
    )


def variant(parser, url, name, **kwargs):
    return FulltextStudyTransformer(
        OpenSearchIntegration(url),
        parser,
        SourceProfile(
            "vacancies",
            ["title", "description"],
            "title",
            {"city": "keyword", "active": "boolean", "schedule": "keyword"},
        ),
        variant=name,
        **kwargs,
    )


def response(*pairs):
    return {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {
            "hits": [
                {"_id": identifier, "_score": score, "_source": {"title": identifier}}
                for identifier, score in pairs
            ]
        },
    }


def execute(leaf, data):
    return leaf.transform(data, registry=BasicTransformerRegistry({})).items


def test_literal_and_core_change_cross_field_eligibility_without_soft_score(
    parser, search_server
):
    url, requests, _ = search_server
    literal = variant(parser, url, "f1").preview(payload())["requests"][0]
    core = variant(parser, url, "f2").preview(payload())["requests"][0]
    assert literal["query"]["bool"]["must"][0]["multi_match"]["type"] == "best_fields"
    assert (
        literal["query"]["bool"]["must"][0]["multi_match"]["query"]
        == "комплектовщик тсд без опыта 2/2"
    )
    serialized = json.dumps(core, ensure_ascii=False)
    assert "без опыта" not in serialized and "2/2" not in serialized
    # Two independently mandatory groups permit role and skill in different fields.
    must = core["query"]["bool"]["must"]
    assert len(must) == 2
    assert "сборщик заказов" in json.dumps(must[0], ensure_ascii=False)
    assert must[1]["bool"]["minimum_should_match"] == 1
    assert must[1]["bool"]["should"][0]["dis_max"]["queries"][0]["multi_match"][
        "fields"
    ] == ["title^2", "description^1"]
    assert core["query"]["bool"]["filter"] == [
        {"term": {"city": "Москва"}},
        {"term": {"active": True}},
    ]
    assert not requests


def test_roles_and_qualification_cannot_be_satisfied_by_coverage(parser, search_server):
    url, _, _ = search_server
    core = variant(parser, url, "f2").preview(
        payload("водитель комплектовщик категории CE ТСД красный синий зеленый")
    )["requests"][0]
    must = core["query"]["bool"]["must"]
    assert len(must) == 4  # two roles + qualification + coverage
    assert "категории ce" in json.dumps(must[2], ensure_ascii=False)
    assert must[3]["bool"]["minimum_should_match"] == 3


def test_bounded_bonus_has_identical_eligibility_and_scalar_evidence(
    parser, search_server
):
    url, _, _ = search_server
    core = variant(parser, url, "f2").preview(payload())["requests"][0]["query"]
    plan = variant(
        parser,
        url,
        "f3",
        preference_bindings={"schedule.two": {"field": "schedule", "value": "2/2"}},
    ).preview(payload())
    score = plan["requests"][0]["query"]["function_score"]
    assert score["query"] == core
    assert score["score_mode"] == "sum" and score["boost_mode"] == "multiply"
    assert score["max_boost"] == 1.2
    assert score["functions"][0] == {"filter": {"match_all": {}}, "weight": 1}
    assert [f["weight"] for f in score["functions"][1:]] == [0.1, 0.1]
    assert plan["preferences"][1]["origin"] == "scalar_value"
    assert score["functions"][2]["filter"]["bool"]["filter"] == [
        {"term": {"schedule": "2/2"}}
    ]
    # Arithmetic oracle distinguishes the bounded bonus from strict tiers.
    assert 9 * (1 + 0.2) > 10 and 9 * (1 + 0.2) < 15


def test_tiers_partition_in_filter_context_and_keep_unpreferred_results(
    parser, search_server
):
    url, requests, replies = search_server
    leaf = variant(parser, url, "f4")
    preview = leaf.preview(payload())
    bodies = preview["requests"]
    assert len(bodies) == 3
    assert (
        bodies[1]["query"]["bool"]["filter"][0]["bool"]["filter"][0]["bool"][
            "minimum_should_match"
        ]
        == 1
    )
    assert "must_not" in bodies[1]["query"]["bool"]["filter"][0]["bool"]
    assert "must_not" in bodies[2]["query"]["bool"]["filter"][0]["bool"]
    for body in bodies:
        assert body["query"]["bool"]["must"][0]["bool"]["filter"] == [
            {"term": {"city": "Москва"}},
            {"term": {"active": True}},
        ]
    replies[:] = [
        (200, response(("all", 9)), {}),
        (200, response(("partial", 12)), {}),
        (200, response(("none", 15)), {}),
    ]
    result = execute(leaf, payload())
    assert [item.id for item in result.items] == ["all", "partial", "none"]
    assert [item.score for item in result.items] == [9, 12, 15]
    assert len(requests) == 3
    assert result.evidence["hits"]["none"]["branches"][0]["tier"] == 2


def test_one_preference_omits_empty_partial_tier_and_no_preferences_is_core(
    parser, search_server
):
    url, _, _ = search_server
    assert (
        len(
            variant(parser, url, "f4").preview(payload("комплектовщик 2/2"))["requests"]
        )
        == 2
    )
    core = variant(parser, url, "f2").preview(payload("комплектовщик"))["requests"]
    for name in ("f3", "f4"):
        assert (
            variant(parser, url, name).preview(payload("комплектовщик"))["requests"]
            == core
        )


def test_rrf_full_depth_union_and_known_arithmetic(parser, search_server):
    url, requests, replies = search_server
    left = [("a", 10), ("b", 9)] + [(f"x{i}", 8 - i / 10) for i in range(23)]
    right = (
        [("b", 11)]
        + [(f"y{i}", 10 - i / 10) for i in range(8)]
        + [("a", 5), ("outside", 4)]
    )
    replies[:] = [(200, response(*left), {}), (200, response(*right), {})]
    result = execute(variant(parser, url, "f5", final_k=30), payload())
    assert [item.id for item in result.items[:2]] == ["b", "a"]
    assert result.items[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert result.items[1].score == pytest.approx(1 / 61 + 1 / 70)
    assert "outside" in [item.id for item in result.items]
    assert [r["body"]["size"] for r in requests] == [100, 100]
    assert result.evidence["hits"]["x22"]["branches"][0]["rank"] == 25


@pytest.mark.parametrize(
    "text,reason",
    [
        ("комплектовщик не вахта", "unsupported_syntax"),
        ("без опыта", "empty_core"),
        (
            "комплектовщик без опыта 2/2 5/2 Москва проживание",
            "preference_budget_exceeded",
        ),
    ],
)
def test_declared_abstention_single_literal_vote(parser, search_server, text, reason):
    url, requests, _ = search_server
    leaf = variant(parser, url, "f5")
    plan = leaf.preview(payload(text))
    assert plan["abstention"] == reason
    assert len(plan["requests"]) == 1
    result = execute(leaf, payload(text))
    assert len(requests) == 1 and result.evidence["score_type"] == "native_lexical"


def test_shared_decomposition_preserves_case_yo_unknown_numbers_and_conflicts(parser):
    plan = decompose_query("  ВОДИТЕЛЬ\tкатегории CE ёлка 100000 без опыта  ", parser)
    assert plan.topic_text == "ВОДИТЕЛЬ категории CE ёлка 100000"
    assert plan.preference_texts == ("ВОДИТЕЛЬ категории CE ёлка 100000. без опыта",)
    plan = decompose_query(
        "комплектовщик 2/2 5/2",
        parser,
        {
            "schedule.two": {"field": "schedule", "value": "2/2"},
            "schedule.five": {"field": "schedule", "value": "5/2"},
        },
    )
    assert plan.abstention == "conflicting_preferences"


@pytest.mark.parametrize("variant_name", ["f4", "f5"])
def test_last_call_failure_cannot_publish_partial_result(
    parser, search_server, variant_name
):
    url, requests, replies = search_server
    replies[:] = [
        (200, response(("ok", 2)), {}),
        (200, {"timed_out": True, "_shards": {"failed": 0}, "hits": {"hits": []}}, {}),
    ]
    data = payload()
    original = SERP([Vacancy("old", {"nested": [1]}, 1)])
    data.items = original
    filters, context = data.filters, deepcopy(data.context)
    with pytest.raises(ValueError, match="Incomplete"):
        execute(variant(parser, url, variant_name), data)
    assert (
        data.items is original and data.filters == filters and data.context == context
    )
    assert len(requests) == 2


def test_duplicate_tier_and_changed_branch_fields_fail_atomically(
    parser, search_server
):
    url, _, replies = search_server
    replies[:] = [(200, response(("same", 2)), {}), (200, response(("same", 1)), {})]
    data = payload()
    with pytest.raises(ValueError, match="disjoint"):
        execute(variant(parser, url, "f4"), data)
    assert not isinstance(data.items, StudySERP)
    changed = response(("same", 1))
    changed["hits"]["hits"][0]["_source"] = {"title": "changed"}
    replies[:] = [(200, response(("same", 2)), {}), (200, changed, {})]
    with pytest.raises(ValueError, match="inconsistency"):
        execute(variant(parser, url, "f5"), payload())


def test_validation_and_preview_have_no_side_effects(parser, search_server):
    url, requests, _ = search_server
    with pytest.raises(ValueError, match="Unsupported term field"):
        variant(
            parser,
            url,
            "f3",
            preference_bindings={"schedule.two": {"field": "missing", "value": "x"}},
        )
    with pytest.raises(ValueError, match="Preference budget"):
        variant(parser, url, "f3", preference_budget=float("nan"))
    with pytest.raises(ValueError, match="leaf budget"):
        variant(parser, url, "f4", query=SlotQueryConfig(max_query_leaves=1)).preview(
            payload()
        )
    with pytest.raises(ValueError, match="Unsupported term field"):
        variant(parser, url, "f1").preview(
            PipelineData(
                filters=(QueryFilter("комплектовщик"), TermFilter("unknown", "x"))
            )
        )
    assert not requests


def test_lexical_ambiguity_is_retained_residual_and_semantic_abstains(search_server):
    parser = work_parser(
        entries={
            "role": [{"id": "picker", "canonical": "комплектовщик"}],
            "skill": [
                {"id": "scanner", "canonical": "сканер", "ambiguous": ["сканер"]}
            ],
        },
        protected=[],
        introductions=[],
        unsupported_markers=[],
        syntax_whitelist=[],
    )
    url, _, _ = search_server
    text = "комплектовщик сканер"
    semantic = decompose_query(text, parser)
    assert semantic.abstention == "ambiguous_query"
    fulltext = variant(parser, url, "f2").preview(payload(text))
    assert fulltext["abstention"] is None
    core = fulltext["requests"][0]
    assert "сканер" in json.dumps(core, ensure_ascii=False)
    assert fulltext["residual"] == [{"text": "сканер", "span": [14, 20]}]


def test_unicode_expansion_never_slices_semantic_but_lexical_core_stays_available(
    parser, search_server
):
    text = "комплектовщик ß без опыта"
    assert decompose_query(text, parser).abstention == "span_mapping_unavailable"
    url, _, _ = search_server
    fulltext = variant(parser, url, "f2").preview(payload(text))
    assert fulltext["abstention"] is None
    assert fulltext["topic_text"] == "комплектовщик ss"
    assert "без опыта" not in json.dumps(fulltext["requests"][0], ensure_ascii=False)


def test_named_query_evidence_is_actual_and_not_inferred_from_negated_text(
    parser, search_server
):
    url, _, replies = search_server
    reply = response(("negative", 10.8))
    reply["hits"]["hits"][0]["_source"] = {
        "title": "Комплектовщик",
        "description": "Кандидатов без опыта не рассматриваем",
    }
    reply["hits"]["hits"][0]["matched_queries"] = ["preference_0"]
    replies[:] = [(200, reply, {})]
    result = execute(variant(parser, url, "f3"), payload("комплектовщик без опыта"))
    info = result.evidence["hits"]["negative"]
    assert info["branches"][0]["matched_queries"] == ["preference_0"]
    assert info["branches"][0]["preference_multiplier"] == 1.2
    assert "core_score" not in info  # the source did not return it
    assert result.evidence["plan"]["preferences"][0]["origin"] == "positive_text_phrase"


def test_raw_character_budget_counts_outer_whitespace_before_http(
    parser, search_server
):
    url, requests, _ = search_server
    with pytest.raises(ValueError, match="character budget"):
        variant(parser, url, "f1").preview(payload(" " * 510 + "комплектовщик"))
    assert not requests


def test_fusion_source_consistency_distinguishes_bool_and_integer(
    parser, search_server
):
    url, _, replies = search_server
    first, second = response(("same", 3)), response(("same", 2))
    first["hits"]["hits"][0]["_source"]["active"] = True
    second["hits"]["hits"][0]["_source"]["active"] = 1
    replies[:] = [(200, first, {}), (200, second, {})]
    data = payload()
    old = data.items
    with pytest.raises(ValueError, match="inconsistency"):
        execute(variant(parser, url, "f5"), data)
    assert data.items is old


@pytest.mark.parametrize("variant_name", ["f4", "f5"])
@pytest.mark.parametrize("bad_took", [None, True, -1, 1.5, "1"])
def test_invalid_last_server_timing_preserves_payload(
    parser, search_server, variant_name, bad_took
):
    url, requests, replies = search_server
    last = response(("last", 1))
    last["took"] = bad_took
    replies[:] = [(200, response(("first", 2)), {}), (200, last, {})]
    data = payload()
    original = data.items
    with pytest.raises(ValueError, match="server timing"):
        execute(variant(parser, url, variant_name), data)
    assert data.items is original and len(requests) == 2


def test_retrieval_calls_preserve_missing_and_reported_server_time(
    parser, search_server
):
    url, _, replies = search_server
    first, second = response(("literal", 2)), response(("core", 1))
    second["took"] = 7
    replies[:] = [(200, first, {}), (200, second, {})]
    result = execute(variant(parser, url, "f5"), payload())
    calls = result.evidence["calls"]
    assert [call["stage"] for call in calls] == ["retrieval", "retrieval"]
    assert [call["branch"] for call in calls] == ["literal", "core"]
    assert [call["branch_index"] for call in calls] == [0, 1]
    assert [call["http_calls"] for call in calls] == [1, 1]
    assert [call["server_took_ms"] for call in calls] == [None, 7]
    assert all(call["elapsed_ms"] >= 0 for call in calls)


def test_literal_does_not_compile_unused_core_budget(parser, search_server):
    url, requests, _ = search_server
    text = " ".join(f"неизвестно{i}" for i in range(17))
    literal = variant(parser, url, "f1").preview(payload(text))
    assert (
        literal["requests"][0]["query"]["bool"]["must"][0]["multi_match"]["query"]
        == text
    )
    with pytest.raises(ValueError, match="concept budget"):
        variant(parser, url, "f2").preview(payload(text))
    assert not requests


def test_explicit_full_card_body_never_guessed_from_searched_field_order(
    parser, search_server
):
    from src.experiments.research.filters import StudyFilterBindings
    from src.experiments.research.study import StudyExperiment

    url, _, replies = search_server
    fields = {
        "title": "Комплектовщик",
        "company": "ООО Пример",
        "body": "Полное описание\n" + "Условия и обязанности. " * 100,
    }
    reply = response(("vacancy", 5))
    reply["hits"]["hits"][0]["_source"] = fields
    replies[:] = [(200, reply, {})]
    source = SourceProfile("vacancies", ["title", "company", "body"], "title")
    leaf = FulltextStudyTransformer(
        OpenSearchIntegration(url),
        parser,
        source,
        variant="f1",
        description_field="body",
    )
    study = StudyExperiment(
        BasicTransformerRegistry({"f1": leaf}),
        ["f1"],
        StudyFilterBindings({}),
        checkout={"revision": "fixture", "dirty": False},
    )
    artifact = study.compare("комплектовщик")
    assert leaf.describe()["description_field"] == "body"
    html = artifact.card_html("vacancy")
    assert fields["body"] in html
    assert (
        '<div style="white-space:pre-wrap;overflow-wrap:anywhere">'
        + fields["body"]
        + "</div>"
        in html
    )
    # Display binding is independent of scored text fields; raw body may be unscored.
    unscored = FulltextStudyTransformer(
        OpenSearchIntegration(url),
        parser,
        SourceProfile("vacancies", ["title", "company"], "title"),
        description_field="body",
    )
    assert unscored.describe()["description_field"] == "body"
    default = FulltextStudyTransformer(OpenSearchIntegration(url), parser, source)
    assert default.describe()["description_field"] == "description"
    with pytest.raises(ValueError, match="description field"):
        FulltextStudyTransformer(
            OpenSearchIntegration(url), parser, source, description_field=" "
        )

from typing import Any, cast
from unittest.mock import Mock

import pytest

from src.core.registry import BasicTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.jobboard.query import (
    FuzzyQueryConfig,
    LexicalQueryConfig,
    SourceProfile,
    compile_query,
)
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter
from src.domain.request.jobboard import ParsedQuery, QueryHint
from src.domain.request.request import Request
from src.integrations.opensearch.client import OpenSearchIntegration, OpenSearchResult


@pytest.fixture
def source():
    return SourceProfile(
        index="jobs",
        fields=["title", "description"],
        title_field="title",
        term_fields={"city": "keyword", "remote": "boolean"},
    )


@pytest.fixture
def config():
    return LexicalQueryConfig(field_weights={"title": 4, "description": 1})


def _hint(kind, text, surface, canonical, alternatives=()):
    start = text.index(surface)
    return QueryHint(
        kind,
        (start, start + len(surface)),
        surface,
        canonical,
        alternatives,
        kind + ".test",
        "unambiguous",
    )


def _compile(
    text, source, config, hints=(), *, structured_enabled=True, filters=(), context=None
):
    parsed = ParsedQuery(text, text, tuple(hints), structured_enabled)
    return compile_query(
        parsed, Request((QueryFilter(text), *filters)), context or {}, source, config, 7
    )


def test_literal_concept_gate_and_attributes(source, config):
    text = "senior python remote Moscow"
    hints = (
        _hint("seniority", text, "senior", "senior"),
        _hint("skill", text, "python", "Python", ("питон",)),
        _hint("work_mode", text, "remote", "remote"),
        _hint("location", text, "Moscow", "Moscow"),
    )
    body = _compile(text, source, config, hints, filters=(TermFilter("remote", True),))
    clauses = body["query"]["bool"]
    assert clauses["filter"] == [{"term": {"remote": True}}]
    assert clauses["minimum_should_match"] == 0
    assert len(clauses["must"]) == 2
    assert clauses["must"][0]["constant_score"]["boost"] == 0
    paths = clauses["must"][1]["dis_max"]
    assert paths["tie_breaker"] == 0
    assert len(paths["queries"]) == 2
    assert paths["queries"][0]["multi_match"]["fields"] == ["title^4", "description^1"]
    assert paths["queries"][1]["bool"]["minimum_should_match"] == 1
    assert len(clauses["should"]) == 4
    for signal in clauses["should"][:3]:
        assert signal["bool"]["boost"] == 0.2
        assert len(signal["bool"]["must"]) == 1
        assert "constant_score" not in signal


def test_no_aliases_prevents_canonical_expansion(source, config):
    text = "питон"
    hint = _hint("skill", text, text, "Python", ("python",))
    no_aliases = LexicalQueryConfig(
        field_weights=config.field_weights, aliases_enabled=False
    )
    body = _compile(text, source, no_aliases, (hint,))
    assert "Python" not in str(body)
    assert "python" not in str(body)


def test_ambiguous_and_protected_remain_literal_without_fuzzy(source, config):
    text = "PM C++"
    ambiguous = QueryHint(
        "role", (0, 2), "PM", "project manager", (), "role.pm", "ambiguous"
    )
    parsed = ParsedQuery(text, text, (ambiguous,), protected_spans=((3, 6),))
    fuzzy = LexicalQueryConfig(
        field_weights=config.field_weights, fuzzy=FuzzyQueryConfig(enabled=True)
    )
    body = compile_query(parsed, Request((QueryFilter(text),)), {}, source, fuzzy, 7)
    assert "C++" in str(body)
    assert "project manager" not in str(body)
    assert "fuzziness" not in str(body)


def test_multiword_protected_and_ambiguous_spans_remain_one_group(source, config):
    text = "remote senior analyst special phrase"
    ambiguous = QueryHint(
        "role",
        (7, 21),
        "senior analyst",
        "data analyst",
        (),
        "role.ambiguous",
        "ambiguous",
    )
    protected = (22, len(text))
    attribute = _hint("work_mode", text, "remote", "remote")
    parsed = ParsedQuery(
        text, text, (ambiguous, attribute), protected_spans=(protected,)
    )
    body = compile_query(parsed, Request((QueryFilter(text),)), {}, source, config, 7)
    groups = cast(Any, body)["query"]["bool"]["must"][0]["dis_max"]["queries"][1][
        "bool"
    ]["should"]
    assert len(groups) == 2
    assert "senior analyst" in str(groups[0])
    assert "special phrase" in str(groups[1])


def test_attribute_with_unknown_role_has_residual_anchor(source, config):
    text = "энтомолог remote"
    hint = _hint("work_mode", text, "remote", "remote")
    body = _compile(text, source, config, (hint,))
    clauses = body["query"]["bool"]
    assert len(clauses["must"][0]["dis_max"]["queries"]) == 2
    assert "энтомолог" in str(clauses["must"][0]["dis_max"]["queries"][1])
    assert "remote" in str(clauses["should"])


def test_aliases_trim_after_literal_and_source_weights_are_derived(source):
    text = "backend developer python"
    hints = (
        _hint(
            "role",
            text,
            "backend developer",
            "backend engineer",
            ("backend developer", "backend engineer", "разработчик бэкенда"),
        ),
        _hint("skill", text, "python", "Python", ("python", "Python", "питон")),
    )
    config = LexicalQueryConfig(max_alternatives_total=3)
    body = _compile(text, source, config, hints)
    clauses = body["query"]["bool"]
    assert clauses["must"][1]["dis_max"]["queries"][0]["multi_match"]["fields"] == [
        "title^4",
        "description^1",
    ]
    assert "разработчик бэкенда" not in str(body)
    assert "backend developer" in str(body)
    assert "python" in str(body)


def test_attribute_alternatives_count_toward_total_budget(source):
    text = "senior python remote"
    hints = (
        _hint("seniority", text, "senior", "senior"),
        _hint("skill", text, "python", "Python"),
        _hint("work_mode", text, "remote", "remote"),
    )
    config = LexicalQueryConfig(max_alternatives_total=2)
    with pytest.raises(ValueError, match="alternatives budget"):
        _compile(text, source, config, hints)


def test_full_query_role_phrase_is_not_scored_twice(source):
    text = "backend developer"
    hint = _hint("role", text, text, text, (text,))
    body = _compile(text, source, LexicalQueryConfig(), (hint,))
    assert "should" not in body["query"]["bool"]


def test_unsupported_syntax_is_literal_only(source, config):
    text = "python или java"
    hint = _hint("skill", text, "python", "Python")
    body = _compile(text, source, config, (hint,), structured_enabled=False)
    clauses = body["query"]["bool"]
    assert len(clauses["must"]) == 1
    assert len(clauses["must"][0]["dis_max"]["queries"]) == 1
    assert "should" not in clauses


def test_bounded_fuzzy_path_excludes_protected_and_known_words(source, config):
    text = "разработчки python C++"
    hint = _hint("skill", text, "python", "Python")
    fuzzy = LexicalQueryConfig(
        field_weights=config.field_weights, fuzzy=FuzzyQueryConfig(enabled=True)
    )
    parsed = ParsedQuery(
        text, text, (hint,), protected_spans=((text.index("C++"), len(text)),)
    )
    body = compile_query(parsed, Request((QueryFilter(text),)), {}, source, fuzzy, 7)
    paths = cast(Any, body)["query"]["bool"]["must"][1]["dis_max"]["queries"]
    assert len(paths) == 3
    assert "fuzziness" in str(paths[-1])
    assert "разработчки" in str(paths[-1])
    assert "fuzziness" not in str(paths[1])


@pytest.mark.parametrize(
    "filter_,context",
    [
        (TermFilter("unknown", "x"), {}),
        (TermFilter("remote", "true"), {}),
        (TermFilter("city", None), {}),
        (UserPropertyFilter("city", "selected"), {}),
        (UserPropertyFilter("city", "selected"), {"selected": []}),
    ],
)
def test_filters_fail_before_search(source, config, filter_, context):
    from src.core.transformers.opensearch.jobboard.search import JobSearchTransformer

    integration = Mock(spec=OpenSearchIntegration)
    parser = Mock()
    parser.parse.return_value = ParsedQuery("python", "python", ())
    transformer = JobSearchTransformer(integration, parser, source, config, 7)
    previous = SERP([Vacancy("old")])
    data = PipelineData(
        items=previous, context=context, filters=[QueryFilter("python"), filter_]
    )
    with pytest.raises(ValueError):
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    integration.search.assert_not_called()
    assert data.items is previous


def test_transformer_publishes_only_complete_response(source, config, search_response):
    from src.core.transformers.opensearch.jobboard.search import JobSearchTransformer

    integration = Mock(spec=OpenSearchIntegration)
    parser = Mock()
    parser.parse.return_value = ParsedQuery("python", "python", ())
    transformer = JobSearchTransformer(integration, parser, source, config, 7)
    previous = SERP([Vacancy("old")])
    data = PipelineData(items=previous, filters=[QueryFilter("python")])
    integration.search.return_value = OpenSearchResult(
        search_response | {"hits": {"hits": [None]}}
    )
    with pytest.raises(ValueError):
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    assert data.items is previous
    integration.search.return_value = OpenSearchResult(search_response)
    assert transformer.transform(data, registry=BasicTransformerRegistry({})) is data
    assert data.items is not previous


def test_character_budget_is_checked_before_parser_and_search(source, config):
    from src.core.transformers.opensearch.jobboard.search import JobSearchTransformer

    integration = Mock(spec=OpenSearchIntegration)
    parser = Mock()
    transformer = JobSearchTransformer(integration, parser, source, config, 7)
    data = PipelineData(filters=[QueryFilter("x" * 513)])
    with pytest.raises(ValueError, match="character budget"):
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    parser.parse.assert_not_called()
    integration.search.assert_not_called()


@pytest.mark.parametrize(
    "text, settings, failure",
    [
        ("python", {"max_query_leaves": 1}, "DSL leaf budget"),
        ("one two three", {"max_concept_groups": 2}, "concept budget"),
    ],
)
def test_compiler_budget_failure_preserves_payload_and_skips_http(
    source, text, settings, failure
):
    from src.core.transformers.opensearch.jobboard.search import JobSearchTransformer

    integration = Mock(spec=OpenSearchIntegration)
    parser = Mock()
    parser.parse.return_value = ParsedQuery(text, text, ())
    config = LexicalQueryConfig(**settings)
    transformer = JobSearchTransformer(integration, parser, source, config, 7)
    previous = SERP([Vacancy("old")])
    data = PipelineData(
        context={"selected": "moscow"}, items=previous, filters=[QueryFilter(text)]
    )
    before = data.copy(deep=True)
    with pytest.raises(ValueError, match=failure):
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    integration.search.assert_not_called()
    assert data.items is previous
    assert data.context == before.context and data.filters == before.filters


def test_parser_failure_preserves_payload_and_skips_http(source, config):
    from src.core.transformers.opensearch.jobboard.search import JobSearchTransformer

    integration = Mock(spec=OpenSearchIntegration)
    parser = Mock()
    failure = RuntimeError("stage failed")
    parser.parse.side_effect = failure
    transformer = JobSearchTransformer(integration, parser, source, config, 7)
    previous = SERP([Vacancy("old")])
    data = PipelineData(
        context={"selected": "moscow"}, items=previous, filters=[QueryFilter("python")]
    )
    before = data.copy(deep=True)
    with pytest.raises(RuntimeError) as caught:
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    assert caught.value is failure
    integration.search.assert_not_called()
    assert data.items is previous
    assert data.context == before.context and data.filters == before.filters


@pytest.mark.parametrize(
    "settings",
    [
        {"alias_weight": float("nan")},
        {"title_phrase_boost": -1},
        {"title_field_weight": True},
        {"tie_breaker": float("nan")},
    ],
)
def test_invalid_query_settings_rejected_before_http(settings):
    integration = Mock(spec=OpenSearchIntegration)
    with pytest.raises(ValueError, match="Invalid lexical query settings"):
        LexicalQueryConfig(**settings)
    integration.search.assert_not_called()


def test_invalid_source_capability_rejected_before_http():
    integration = Mock(spec=OpenSearchIntegration)
    with pytest.raises(ValueError, match="Invalid source capabilities"):
        SourceProfile(index="jobs", fields=[])
    integration.search.assert_not_called()


def test_unknown_weighted_field_rejected_before_http_and_mutation(source):
    from src.core.transformers.opensearch.jobboard.search import JobSearchTransformer

    integration = Mock(spec=OpenSearchIntegration)
    parser = Mock()
    parser.parse.return_value = ParsedQuery("python", "python", ())
    config = LexicalQueryConfig(field_weights={"missing": 2})
    transformer = JobSearchTransformer(integration, parser, source, config, 7)
    previous = SERP([Vacancy("old")])
    data = PipelineData(items=previous, filters=[QueryFilter("python")])
    with pytest.raises(ValueError, match="unknown source fields"):
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    integration.search.assert_not_called()
    assert data.items is previous


def test_fuzzy_rejects_edit_distance_unsupported_by_opensearch():
    with pytest.raises(ValueError, match="fuzzy settings"):
        FuzzyQueryConfig(enabled=True, distance=3)


@pytest.mark.parametrize("value", ["garbage", "75%%", "2<", "2<75% OR 1", " "])
def test_invalid_minimum_should_match_is_rejected(value):
    with pytest.raises(ValueError, match="lexical query settings"):
        LexicalQueryConfig(literal_minimum_should_match=value)


@pytest.mark.parametrize("value", ["2<75%", "2", "-2", "75%", "-25%", "2<-25% 9<-3"])
def test_documented_minimum_should_match_forms_are_supported(value):
    assert (
        LexicalQueryConfig(
            literal_minimum_should_match=value
        ).literal_minimum_should_match
        == value
    )


def test_repeated_word_fuzzy_eligibility_is_per_occurrence(source):
    from src.components.query.jobboard.parser import ModularQueryParser
    from src.components.query.normalization import NormalizeText

    text = "developr DEVELOPR"
    parsed = ModularQueryParser(NormalizeText(), []).parse(text)
    assert parsed.protected_spans == ((9, 17),)
    body = compile_query(
        parsed,
        Request((QueryFilter(text),)),
        {},
        source,
        LexicalQueryConfig(fuzzy=FuzzyQueryConfig(enabled=True)),
        10,
    )
    paths = cast(Any, body)["query"]["bool"]["must"][0]["dis_max"]["queries"]
    assert len(paths) == 2
    groups = paths[1]["bool"]["should"]
    assert "fuzziness" in str(groups[0])
    assert "fuzziness" not in str(groups[1])

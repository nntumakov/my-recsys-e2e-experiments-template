"""Local HTTP evidence for dense mechanisms; scores are arithmetic fixtures."""

from copy import deepcopy
from math import inf, nan

import pytest

from src.components.embedding.encoder import TextEmbedder
from src.components.embedding.representations import SingleFieldText
from src.components.embedding.vacancy import VacancyText
from src.components.query.jobboard.catalog import work_parser
from src.core.registry import BasicTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.qdrant.study import (
    MultiQueryStudySearch,
    TitleBodyStudySearch,
    TopicPreferenceStudySearch,
    WholeQueryStudySearch,
    cosine_utility,
)
from src.domain.document.vacancy import Vacancy
from src.domain.embedding import EmbeddingProfile
from src.domain.request.filters import QueryFilter, TermFilter
from src.domain.study import StudySERP
from src.integrations.embedding.client import TEIIntegration
from src.integrations.qdrant.client import QdrantIntegration
from src.integrations.qdrant.source import QdrantSource


@pytest.fixture
def dense(vector_stack):
    def source(recipe):
        return QdrantSource(
            EmbeddingProfile(),
            recipe,
            "study-cohort",
            {
                "city": {"path": ["city"], "type": "keyword"},
                "active": {"path": ["active"], "type": "bool"},
            },
            runtime={"version": "1.9.4", "image": "tei@sha256:" + "a" * 64},
            vector={"size": 768, "distance": "Cosine"},
        )

    whole = source(VacancyText())
    title = source(SingleFieldText("title", "title"))
    body = source(SingleFieldText("description", "description"))
    for name, bound in (("whole", whole), ("title", title), ("body", body)):
        vector_stack.collections[name] = {
            "status": "green",
            "optimizer_status": "ok",
            "payload_schema": {key: {"data_type": kind} for key, kind in bound.indexes},
            "config": {
                "params": {
                    "vectors": {"size": 768, "distance": "Cosine"},
                    "shard_number": 1,
                    "replication_factor": 1,
                    "write_consistency_factor": 1,
                },
                "metadata": bound.metadata(
                    name,
                    expected_points=2,
                    corpus_sha256="b" * 64,
                    runtime=bound.runtime
                    | {"qdrant_version": "1.19.2", "inference": vector_stack.tei_info},
                    state="ready",
                    verification={
                        "protocol": "full-readback-v1",
                        "exact_count": 2,
                        "read_back_count": 2,
                        "status": "green",
                        "optimizer_status": "ok",
                        "indexes": dict(bound.indexes),
                        "smoke": True,
                    },
                ),
            },
        }
    parser = work_parser(
        {
            "role": [{"id": "picker", "canonical": "комплектовщик"}],
            "experience": [{"id": "none", "canonical": "без опыта"}],
            "schedule": [{"id": "2_2", "canonical": "2/2"}],
        },
        protected=(),
        introductions=(),
        unsupported_markers=("не", "без", "или"),
        syntax_whitelist=("без опыта",),
    )
    integration = QdrantIntegration(vector_stack.url)
    embedder = TextEmbedder(EmbeddingProfile(), TEIIntegration(vector_stack.url), {})
    args = (integration, embedder, whole, "whole")
    return args, parser, title, body


def payload(text="комплектовщик без опыта"):
    return PipelineData(
        context={"unchanged": [1]},
        filters=(
            QueryFilter(text),
            TermFilter("city", "Москва"),
            TermFilter("active", True),
        ),
    )


def scripted_queries(stack, sources, lists):
    original = stack.respond
    queue = list(lists)

    def respond(method, path, body):
        if method == "POST" and path.endswith("/points/query"):
            source = sources[path.split("/")[2]]
            points = []
            for identifier, score in queue.pop(0):
                fields = {
                    "title": "Комплектовщик",
                    "description": "Полное описание " + identifier,
                    "city": "Москва",
                    "active": True,
                }
                points.append(
                    {
                        "id": source.point_id(identifier),
                        "version": 0,
                        "score": score,
                        "payload": {"document_id": identifier, "fields": fields},
                    }
                )
            return stack.ok({"points": points})
        return original(method, path, body)

    stack.respond = respond


def run(search, data):
    return search.transform(data, registry=BasicTransformerRegistry({})).items


def calls(stack, ending):
    return [r for r in stack.requests if r["path"].endswith(ending)]


def test_e1_preview_pure_http_search_full_cards_and_native_scores(vector_stack, dense):
    args, _, _, _ = dense
    search = WholeQueryStudySearch(*args, final_k=1, candidate_depth=2)
    data = payload()
    plan = search.preview(data)
    assert plan["model_input"] == "query: комплектовщик без опыта"
    assert vector_stack.requests == []
    scripted_queries(vector_stack, {"whole": args[2]}, [[("A", 0.9), ("B", 0.8)]])
    result = run(search, data)
    assert isinstance(result, StudySERP)
    assert [(i.id, i.score) for i in result.items] == [("A", 0.9)]
    assert result.items[0].fields["description"] == "Полное описание A"
    query = calls(vector_stack, "/points/query")[0]["body"]
    assert query["params"]["exact"] is True
    assert query["filter"]["must"] == [
        {"key": "fields.city", "match": {"value": "Москва"}},
        {"key": "fields.active", "match": {"value": True}},
    ]
    assert "query" not in result.evidence["calls"][-1]["body"]
    assert result.evidence["calls"][-1]["profile_id"] == args[2].profile.id
    assert result.evidence["calls"][-1]["vector_dimensions"] == 768
    assert data.context == {"unchanged": [1]}


def test_e2_fixed_pool_bonus_reorders_and_exact_uuid_restriction(vector_stack, dense):
    args, parser, _, _ = dense
    search = TopicPreferenceStudySearch(
        *args, parser=parser, candidate_depth=2, final_k=2, exact=False
    )
    scripted_queries(
        vector_stack,
        {"whole": args[2]},
        [[("A", 0.85), ("B", 0.82)], [("B", 0.9), ("A", 0.4)]],
    )
    result = run(search, payload())
    assert [i.id for i in result.items] == ["B", "A"]
    assert [i.score for i in result.items] == pytest.approx([1.01, 0.99])
    native = calls(vector_stack, "/points/query")
    assert native[0]["body"]["params"]["exact"] is False
    assert native[1]["body"]["params"]["exact"] is True
    assert native[1]["body"]["limit"] == 2
    assert native[1]["body"]["filter"]["must"][-1] == {
        "has_id": [args[2].point_id("A"), args[2].point_id("B")]
    }
    assert [c["body"]["inputs"] for c in calls(vector_stack, "/embed")] == [
        ["query: комплектовщик"],
        ["query: комплектовщик. без опыта"],
    ]
    assert result.evidence["hits"]["A"]["topic_cosine"] == 0.85


@pytest.mark.parametrize("wrong", [[], [("A", 0.2)], [("A", 0.2), ("C", 0.4)]])
def test_e2_missing_or_extra_pool_is_atomic_error(vector_stack, dense, wrong):
    args, parser, _, _ = dense
    search = TopicPreferenceStudySearch(
        *args, parser=parser, candidate_depth=2, final_k=2
    )
    scripted_queries(
        vector_stack, {"whole": args[2]}, [[("A", 0.8), ("B", 0.7)], wrong]
    )
    data = payload()
    data.items.items = [Vacancy("previous")]
    before = deepcopy(data.items)
    with pytest.raises(ValueError, match="complete fixed pool"):
        run(search, data)
    assert data.items == before
    assert data.context == {"unchanged": [1]}


@pytest.mark.parametrize(
    "text,alpha,expected",
    [
        ("комплектовщик без опыта", 0.2, "query: комплектовщик"),
        ("комплектовщик без опыта", 0, "query: комплектовщик"),
        ("комплектовщик не вахта", 0.2, "query: комплектовщик не вахта"),
    ],
)
def test_e2_empty_pool_no_preference_inference(
    vector_stack, dense, text, alpha, expected
):
    args, parser, _, _ = dense
    search = TopicPreferenceStudySearch(*args, parser=parser, alpha=alpha)
    scripted_queries(vector_stack, {"whole": args[2]}, [[]])
    result = run(search, payload(text))
    assert result.items == []
    assert [r["body"]["inputs"] for r in calls(vector_stack, "/embed")] == [[expected]]
    assert len(calls(vector_stack, "/points/query")) == 1


def test_e2_multiple_preferences_budget_does_not_grow(vector_stack, dense):
    args, parser, _, _ = dense
    search = TopicPreferenceStudySearch(
        *args, parser=parser, final_k=2, candidate_depth=2
    )
    scripted_queries(
        vector_stack,
        {"whole": args[2]},
        [[("A", 0.95), ("B", 0.7)], [("A", -1), ("B", 1)], [("A", -1), ("B", 1)]],
    )
    result = run(search, payload("комплектовщик без опыта 2/2"))
    assert [i.id for i in result.items] == ["A", "B"]
    assert result.evidence["hits"]["B"]["bonus"] == pytest.approx(0.2)


@pytest.mark.parametrize("score", [None, nan, inf, -1.1, 1.1, True])
def test_cosine_rejects_invalid_values(score):
    with pytest.raises(ValueError):
        cosine_utility(score)


def test_cosine_tiny_overshoot_is_only_clamped_for_utility():
    assert cosine_utility(1.000001) == 1
    assert cosine_utility(-1.000001) == 0


def test_e3_union_and_existing_rrf_semantics(vector_stack, dense):
    args, parser, _, _ = dense
    search = MultiQueryStudySearch(*args, parser=parser, final_k=3, candidate_depth=3)
    scripted_queries(
        vector_stack,
        {"whole": args[2]},
        [[("B", 0.9), ("A", 0.8)], [("A", 0.85), ("C", 0.7)]],
    )
    result = run(search, payload())
    assert [i.id for i in result.items] == ["A", "B", "C"]
    assert result.items[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert result.evidence["hits"]["B"] == {
        "original": {"rank": 1, "cosine": 0.9, "rrf_contribution": 1 / 61}
    }


def test_e3_no_preferences_one_vote_native_score(vector_stack, dense):
    args, parser, _, _ = dense
    search = MultiQueryStudySearch(*args, parser=parser)
    scripted_queries(vector_stack, {"whole": args[2]}, [[("A", 0.9)]])
    result = run(search, payload("комплектовщик"))
    assert result.items[0].score == 0.9
    assert result.evidence["active_path"] == "original"
    assert "rrf_contribution" not in result.evidence["hits"]["A"]["original"]
    assert len(calls(vector_stack, "/embed")) == 1
    assert len(calls(vector_stack, "/points/query")) == 1


def test_e4_both_ready_before_one_encode_shared_vector_rrf(vector_stack, dense):
    args, _, title, body = dense
    search = TitleBodyStudySearch(
        args[0], args[1], title, "title", body, "body", candidate_depth=3, final_k=3
    )
    scripted_queries(
        vector_stack,
        {"title": title, "body": body},
        [[("A", 0.9), ("B", 0.7)], [("B", 0.9), ("C", 0.8)]],
    )
    result = run(search, payload())
    assert [i.id for i in result.items] == ["B", "A", "C"]
    hit = result.evidence["hits"]["B"]
    assert hit["title"]["rrf_contribution"] == pytest.approx(1 / 62)
    assert hit["body"]["rrf_contribution"] == pytest.approx(1 / 61)
    assert len(calls(vector_stack, "/embed")) == 1
    queries = calls(vector_stack, "/points/query")
    assert queries[0]["body"]["query"] == queries[1]["body"]["query"]
    paths = [r["path"] for r in vector_stack.requests]
    assert paths.index("/collections/title") < paths.index("/embed")
    assert paths.index("/collections/body") < paths.index("/embed")


def test_e4_mismatched_cohort_fails_before_inference(vector_stack, dense):
    args, _, title, body = dense
    search = TitleBodyStudySearch(args[0], args[1], title, "title", body, "body")
    vector_stack.collections["body"]["config"]["metadata"]["recsys"][
        "corpus_sha256"
    ] = "c" * 64
    with pytest.raises(ValueError, match="same cohort"):
        run(search, payload())
    assert not calls(vector_stack, "/embed")


def test_single_field_missing_required_has_explicit_identity():
    title = SingleFieldText("heading", "title")
    assert title.id != VacancyText(fields=({"name": "heading", "required": True},)).id
    assert title.render(Vacancy("A", {"heading": "  Токарь\t CE "})) == "Токарь CE"
    with pytest.raises(ValueError, match="Required"):
        title.render(Vacancy("A", {"description": "body"}))


@pytest.mark.parametrize("variant", ["E2", "E3", "E4"])
def test_changed_fields_across_queries_rejected_without_payload_mutation(
    vector_stack, dense, variant
):
    args, parser, title, body = dense
    if variant == "E2":
        search = TopicPreferenceStudySearch(*args, parser=parser)
    elif variant == "E3":
        search = MultiQueryStudySearch(*args, parser=parser)
    else:
        search = TitleBodyStudySearch(args[0], args[1], title, "title", body, "body")
    scripted_queries(
        vector_stack,
        {"whole": args[2], "title": title, "body": body},
        [[("A", 0.8)], [("A", 0.9)]],
    )
    original = vector_stack.respond
    query_count = 0

    def changed(method, path, body):
        nonlocal query_count
        response = original(method, path, body)
        if path.endswith("/points/query"):
            query_count += 1
            if query_count == 2:
                response[1]["result"]["points"][0]["payload"]["fields"][
                    "description"
                ] = "Changed"
        return response

    vector_stack.respond = changed
    data = payload()
    data.items.items = [Vacancy("previous")]
    before = deepcopy(data.items)
    with pytest.raises(ValueError, match="fields"):
        run(search, data)
    assert data.items == before


def test_e2_duplicate_scoring_hit_is_atomic_error(vector_stack, dense):
    args, parser, _, _ = dense
    search = TopicPreferenceStudySearch(*args, parser=parser)
    scripted_queries(
        vector_stack, {"whole": args[2]}, [[("A", 0.8)], [("A", 0.9), ("A", 0.9)]]
    )
    data = payload()
    before = data.items
    with pytest.raises(ValueError, match="Duplicate"):
        run(search, data)
    assert data.items is before


def test_derived_query_budget_checked_before_readiness(vector_stack, dense):
    args, parser, _, _ = dense
    args[1].limits = {**args[1].limits, "query_bytes": 60}
    search = TopicPreferenceStudySearch(*args, parser=parser)
    # Original fits while punctuation in an anchored preference crosses the budget.
    original = "комплектовщик без опыта"
    args[1].limits = {
        **args[1].limits,
        "query_bytes": len(("query: " + original).encode()),
    }
    with pytest.raises(ValueError, match="byte budget"):
        search.preview(payload(original))
    assert vector_stack.requests == []


@pytest.mark.parametrize("alpha", [True, nan, inf, -0.1, 0.31])
def test_bad_preference_budget_rejected_before_http(vector_stack, dense, alpha):
    args, parser, _, _ = dense
    with pytest.raises(ValueError, match="alpha"):
        TopicPreferenceStudySearch(*args, parser=parser, alpha=alpha)
    assert vector_stack.requests == []


def test_min_preference_coverage_control_differs_from_mean(vector_stack, dense):
    args, parser, _, _ = dense
    search = TopicPreferenceStudySearch(
        *args, parser=parser, preference_aggregation="min", candidate_depth=2, final_k=2
    )
    scripted_queries(
        vector_stack,
        {"whole": args[2]},
        [[("A", 0.8), ("B", 0.78)], [("A", 1), ("B", 0.5)], [("A", -1), ("B", 0.5)]],
    )
    result = run(search, payload("комплектовщик без опыта 2/2"))
    assert [item.id for item in result.items] == ["B", "A"]
    assert result.evidence["hits"]["B"]["bonus"] == pytest.approx(0.15)
    assert result.evidence["hits"]["A"]["bonus"] == 0
    assert result.evidence["hits"]["A"]["preferences"][0]["contribution"] is None
    parameters = search.describe()["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["preference_aggregation"] == "min"


def test_e4_whole_recipe_cannot_replace_title_representation(vector_stack, dense):
    args, _, _, body = dense
    with pytest.raises(ValueError, match="distinct representations"):
        TitleBodyStudySearch(args[0], args[1], args[2], "whole", body, "body")
    assert vector_stack.requests == []

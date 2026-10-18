"""Composed research workflow on controlled HTTP fixtures, without BM25/ML claims."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate

from src.components.reporting.artifacts.study import SearchStudyArtifact
from src.domain.request.filters import QueryFilter, TermFilter
from src.domain.study import StudySERP
from src.run import run

QUERY = "комплектовщик ТСД без опыта 2/2"


def filters(study, city="Москва"):
    return study.bindings.terms({"city": city, "active": True})


def test_all_ten_hydra_preview_http_and_artifact(study_stack):
    state = study_stack
    study = state.study
    assert study.variants == tuple(
        [*(f"f{i}" for i in range(1, 6)), *(f"e{i}" for i in range(1, 6))]
    )
    preview = study.preview(QUERY, filters=filters(study))
    assert set(preview["plans"]) == set(study.variants)
    assert not state.stack.requests and not state.os_requests
    artifact = study.compare(QUERY, filters=filters(study), reference_variant="f2")
    payload = artifact.to_dict()
    assert payload["artifact_type"] == "search-study"
    assert len(payload["results"]) == 10
    assert payload["session"]["reference_variant"] == "f2"
    assert payload["session"]["comparability"]["comparable"] is False
    for result in payload["results"]:
        assert result["inputs"]["original_text"] == QUERY
        assert result["inputs"]["effective_filters"] == [
            {"field": "city", "value": "Москва"},
            {"field": "active", "value": True},
        ]
        assert [hit["id"] for hit in result["hits"]] and {
            hit["id"] for hit in result["hits"]
        } <= {"A", "B"}
        assert all(
            hit["fields"]["city"] == "Москва" and hit["fields"]["active"] is True
            for hit in result["hits"]
        )
        assert result["evidence"]["run_id"] == artifact.run_id
        assert (
            result["evidence"]["prepared_plan"]
            == preview["plans"][result["variant_id"]]
        )
        assert result["evidence"]["end_to_end_ms"] >= 0
    # Controlled fixture scores (A=8,B=7) distinguish soft policies; no inferred BM25.
    assert artifact.serp("manual", "f2").items[0].id == "A"
    assert artifact.serp("manual", "f3").items[0].id == "B"
    assert artifact.serp("manual", "f4").items[0].id == "B"
    assert artifact.serp("manual", "f3").items[0].score == pytest.approx(8.4)
    summaries = {row["variant"]: row for row in artifact.summary()}
    assert summaries["f5"]["retrieval_calls"] == 2
    assert summaries["f4"]["retrieval_calls"] == 3
    assert summaries["e4"]["retrieval_calls"] == 2
    assert all(
        row["http_calls"] >= row["retrieval_calls"] for row in summaries.values()
    )
    assert {row["group"] for row in artifact.rank_matrix()} == {"fulltext", "embedding"}


def test_repeated_calls_have_no_filter_accumulation_and_previous_snapshot_is_independent(
    study_stack,
):
    state = study_stack
    study = state.study
    first = study.compare(QUERY, variants=["f1", "e1"], filters=filters(study))
    frozen = deepcopy(first.to_dict())
    second = study.compare(
        "комплектовщик",
        variants=["f1", "e1"],
        filters=filters(study, "Санкт-Петербург"),
    )
    third = study.compare("комплектовщик", variants=["f1", "e1"])
    assert first.run_id != second.run_id != third.run_id
    assert {
        hit["id"] for result in second.to_dict()["results"] for hit in result["hits"]
    } == {"C"}
    assert {
        hit["id"] for result in third.to_dict()["results"] for hit in result["hits"]
    } == {"A", "B", "C"}
    assert third.to_dict()["results"][0]["inputs"]["effective_filters"] == []
    assert first.to_dict() == frozen
    clone = first.serp("manual", "f1")
    clone.items[0].fields["extra"]["typed"].append("mutated")
    clone.evidence["calls"].clear()
    exported = first.to_dict()
    exported["session"]["variants"].clear()
    assert first.to_dict() == frozen


def test_every_selected_collection_is_admitted_before_any_inference(study_stack):
    state = study_stack
    state.study.compare(QUERY, filters=filters(state.study))
    requests = state.stack.requests
    first_embed = next(i for i, call in enumerate(requests) if call["path"] == "/embed")
    admitted = {
        call["path"] for call in requests[:first_embed] if call["method"] == "GET"
    }
    assert {
        "/collections/whole",
        "/collections/title",
        "/collections/body",
        "/collections/chunks",
    } <= admitted
    retrievals = [call for call in requests if call["path"].endswith("/points/query")]
    assert retrievals
    for call in retrievals:
        terms = call["body"]["filter"]["must"]
        assert {"key": "fields.city", "match": {"value": "Москва"}} in terms
        assert {"key": "fields.active", "match": {"value": True}} in terms


def test_later_selected_local_validation_error_has_zero_http(study_stack):
    state = study_stack
    state.study.registry["f5"].query = replace(
        state.study.registry["f5"].query, max_input_characters=3
    )
    with pytest.raises(ValueError, match="character budget"):
        state.study.compare(
            QUERY, variants=["f1", "e1", "f5"], filters=filters(state.study)
        )
    assert not state.stack.requests and not state.os_requests


def test_late_batch_local_error_preflights_all_queries_before_http(study_stack):
    state = study_stack
    with pytest.raises(ValueError, match="nonempty query"):
        state.study.compare_queries(
            [("first", QUERY), ("second", "  ")], variants=["f1", "e1"]
        )
    assert not state.stack.requests and not state.os_requests


def test_later_missing_generation_stops_before_first_selected_search(study_stack):
    state = study_stack
    state.stack.collections.pop("body")
    with pytest.raises((RuntimeError, ValueError)):
        state.study.compare(
            QUERY, variants=["f1", "e1", "e4"], filters=filters(state.study)
        )
    assert not state.os_requests
    assert all(
        call["path"] != "/embed" and not call["path"].endswith("/points/query")
        for call in state.stack.requests
    )


def test_late_native_failure_does_not_return_partial_artifact_and_next_call_recovers(
    study_stack,
):
    state = study_stack
    prior = state.study.compare(
        QUERY, variants=["f1", "e1"], filters=filters(state.study)
    )
    frozen = prior.to_dict()
    state.stack.fail_query_number = state.stack.query_calls + 1
    result = None
    with pytest.raises(ValueError) as error:
        result = state.study.compare(
            QUERY, variants=["f1", "e1"], filters=filters(state.study)
        )
    assert result is None and prior.to_dict() == frozen
    assert any(
        "e1" in note and "search" in note and "manual" in note
        for note in error.value.__notes__
    )
    recovered = state.study.compare(
        QUERY, variants=["f1", "e1"], filters=filters(state.study)
    )
    assert recovered.run_id != prior.run_id and len(recovered.to_dict()["results"]) == 2


def test_rendering_all_views_never_searches_and_escapes_full_card(
    study_stack, monkeypatch
):
    state = study_stack
    query = QUERY + " <img src=x onerror=boom>"
    artifact = state.study.compare(
        query, variants=["f2", "f3", "f4", "e1", "e2"], filters=filters(state.study)
    )
    requests, native = deepcopy(state.stack.requests), deepcopy(state.os_requests)

    def prohibited(*args, **kwargs):
        pytest.fail("Snapshot rendering cannot call study.search/compare")

    monkeypatch.setattr(state.study, "search", prohibited)
    monkeypatch.setattr(state.study, "compare", prohibited)
    views = [
        artifact.summary_html(),
        artifact.rank_lists_html(),
        artifact.matrix_html(),
        artifact.card_html("A"),
        artifact.diagnostics_html(),
        artifact._repr_html_(),
    ]
    for html in views:
        assert "<script>" not in html and "<img src=x" not in html
        assert artifact.run_id in html
        assert "&lt;img src=x onerror=boom&gt;" in html
    card = views[3]
    assert "&lt;script&gt;bad()&lt;/script&gt;" in card
    assert state.records[0]["fields"]["description"] in card
    assert "Все исходные fields" in card and "Диагностика варианта" in card
    assert "city: &quot;Москва&quot;" in card and "active: true" in card
    snapshot = artifact.to_dict()
    snapshot["session"]["filter_bindings"]["schedule"] = {
        "type": "keyword",
        "opensearch_field": "schedule.keyword",
        "opensearch_source_path": ["schedule"],
        "qdrant_path": ["schedule"],
    }
    missing_optional = SearchStudyArtifact(snapshot["session"], artifact.results())
    assert "schedule: не указано" in missing_optional.card_html("A")
    assert state.stack.requests == requests and state.os_requests == native
    summary = artifact.summary()
    assert len(summary) == 5
    assert artifact.card("A")["occurrences"][0]["fields"]["extra"]["typed"] == [
        1,
        True,
        1.0,
    ]
    with pytest.raises(ValueError, match="present"):
        artifact.rank_lists_html(variants=["f5"])


def test_empty_results_are_completed_and_can_be_read(study_stack):
    state = study_stack
    artifact = state.study.compare(
        QUERY, variants=["f1", "e2", "e5"], filters=filters(state.study, "Иваново")
    )
    assert all(not artifact.serp("manual", name).items for name in ["f1", "e2", "e5"])
    assert "0 результатов" in artifact.rank_lists_html()
    assert "0 результатов" in artifact.matrix_html()
    assert artifact.rank_matrix() == []
    assert all(row["returned_vacancies"] == 0 for row in artifact.summary())
    with pytest.raises(KeyError):
        artifact.card("A")


def test_filter_errors_reference_and_selection_fail_before_http(study_stack):
    state = study_stack
    for kwargs in (
        {"variants": ["f1", "f1"]},
        {"variants": ["unknown"]},
        {"variants": ["f1"], "reference_variant": "e1"},
        {"filters": [TermFilter("unknown", "x")]},
        {"filters": [TermFilter("active", 1)]},
        {"filters": [QueryFilter("another")]},
    ):
        with pytest.raises(ValueError):
            state.study.compare(QUERY, **kwargs)
    assert not state.stack.requests and not state.os_requests


def test_hydra_registers_only_requested_variants_and_does_not_resolve_other_generations(
    study_stack,
):
    state = study_stack
    config = deepcopy(state.cfg)
    config.study.registered = ["f1"]
    config.study.collections.whole = None
    config.study.collections.title = None
    config.study.collections.body = None
    config.study.collections.chunk = None
    study = instantiate(config.experiment, _convert_="all")
    assert study.variants == ("f1",)
    assert isinstance(
        study.search(QUERY, variant="f1", filters=filters(study)), StudySERP
    )
    assert state.os_requests and all(
        call["path"] == "/vacancies/_search" for call in state.stack.requests
    )
    assert "f1" in study.describe()["variants"]
    assert "e1" not in study.describe()["variants"]
    # Connection locations and auth never leak through the serialized result.
    artifact = study.compare(QUERY, filters=filters(study))
    encoded = json.dumps(artifact.to_dict(), ensure_ascii=False)
    assert state.stack.url not in encoded and "authorization" not in encoded


def test_false_comparable_declaration_cannot_admit_or_encode(study_stack):
    state = study_stack
    config = deepcopy(state.cfg)
    config.study.comparability = {
        "comparable": True,
        "cohort_sha256": "f" * 64,
        "evidence": "fictional owner declaration for failure test",
    }
    study = instantiate(config.experiment, _convert_="all")
    with pytest.raises(ValueError, match="cohort|Cohort|declaration"):
        study.compare(QUERY, variants=["f1", "e1"], filters=filters(study))
    assert not state.os_requests
    assert all(
        call["path"] != "/embed" and not call["path"].endswith("/points/query")
        for call in state.stack.requests
    )


def test_selected_dense_generations_must_share_corpus_digest_before_inference(
    study_stack,
):
    state = study_stack
    # E4 title/body agree internally, but their cohort differs from selected E1.
    for name in ("title", "body"):
        state.stack.collections[name]["config"]["metadata"]["recsys"][
            "corpus_sha256"
        ] = "e" * 64
    with pytest.raises(ValueError, match="cohort|Cohort|corpus"):
        state.study.compare(
            QUERY, variants=["f1", "e1", "e4"], filters=filters(state.study)
        )
    assert not state.os_requests
    assert all(
        call["path"] != "/embed" and not call["path"].endswith("/points/query")
        for call in state.stack.requests
    )


def interactive_config(url, destination):
    root = Path(__file__).resolve().parents[1]
    with initialize_config_dir(config_dir=str(root / "configs"), version_base="1.3"):
        return compose(
            config_name="study",
            overrides=[
                f"connection.url={url}",
                "study.fulltext_source.index=vacancies",
                "study.registered=[f1]",
                "experiments/vacancy-search/study@experiment=interactive",
                f"experiment.exporters.0.path={destination}",
            ],
        )


def feed_input(monkeypatch, values):
    inputs = iter(values)

    def read(prompt):
        value = next(inputs, EOFError())
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", read)


def test_interactive_eof_exports_complete_snapshot_once(
    search_server, tmp_path, monkeypatch, capsys
):
    url, requests, _ = search_server
    destination = tmp_path / "study.json"
    cfg = interactive_config(url, destination)
    feed_input(monkeypatch, ["", QUERY, "водитель"])
    run.__wrapped__(cfg)
    payload = json.loads(destination.read_text())
    assert [result["query_id"] for result in payload["results"]] == ["1", "2"]
    assert [result["inputs"]["original_text"] for result in payload["results"]] == [
        QUERY,
        "водитель",
    ]
    assert len(requests) == 2
    assert {result["evidence"]["run_id"] for result in payload["results"]} == {
        payload["session"]["run_id"]
    }
    assert (
        len({result["evidence"]["comparison_run_id"] for result in payload["results"]})
        == 2
    )
    assert (
        payload["session"]["session_elapsed_ms"]
        >= payload["session"]["comparison_elapsed_ms"]
        >= 0
    )
    assert "search-study" in capsys.readouterr().out
    assert sorted(path.name for path in tmp_path.iterdir()) == ["study.json"]


def test_interactive_empty_eof_has_valid_empty_artifact(
    search_server, tmp_path, monkeypatch
):
    url, requests, _ = search_server
    destination = tmp_path / "empty.json"
    feed_input(monkeypatch, [])
    run.__wrapped__(interactive_config(url, destination))
    assert json.loads(destination.read_text())["results"] == []
    assert not requests


def test_interactive_ctrl_c_after_search_has_exit130_without_export(
    search_server, tmp_path, monkeypatch
):
    url, requests, _ = search_server
    destination = tmp_path / "interrupted.json"
    feed_input(monkeypatch, [QUERY, KeyboardInterrupt()])
    with pytest.raises(SystemExit) as error:
        run.__wrapped__(interactive_config(url, destination))
    assert error.value.code == 130
    assert len(requests) == 1 and not destination.exists()


def test_interactive_late_http_failure_exits_without_partial_export(
    search_server, tmp_path, monkeypatch
):
    url, requests, replies = search_server
    first = deepcopy(replies[0])
    replies[:] = [first, (500, b"fixture server failure", {})]
    destination = tmp_path / "failed.json"
    feed_input(monkeypatch, [QUERY, "водитель", "third"])
    with pytest.raises(SystemExit) as error:
        run.__wrapped__(interactive_config(url, destination))
    assert error.value.code == 1
    assert len(requests) == 2 and not destination.exists()


def test_interactive_export_never_overwrites_existing_file(
    search_server, tmp_path, monkeypatch
):
    url, requests, _ = search_server
    destination = tmp_path / "existing.json"
    destination.write_text("original result\n")
    feed_input(monkeypatch, [QUERY])
    with pytest.raises(SystemExit) as error:
        run.__wrapped__(interactive_config(url, destination))
    assert error.value.code == 1
    assert destination.read_text() == "original result\n"
    assert len(requests) == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == ["existing.json"]


def test_interactive_and_batch_wrappers_keep_the_study_factory(search_server, tmp_path):
    url, requests, _ = search_server
    cfg = interactive_config(url, tmp_path / "result.json")
    assert (
        cfg.experiment._target_
        == "src.experiments.research.composition.study_experiment"
    )
    assert len(cfg.experiment.exporters) == 1
    root = Path(__file__).resolve().parents[1]
    with initialize_config_dir(config_dir=str(root / "configs"), version_base="1.3"):
        batch = compose(
            config_name="study",
            overrides=["experiments/vacancy-search/study@experiment=batch"],
        )
    assert batch.experiment._target_ == cfg.experiment._target_
    assert batch.experiment.input_path is None
    assert len(batch.experiment.exporters) == 1
    assert (
        batch.experiment.exporters[0]._target_
        == "src.components.reporting.exporters.json.JSONExporter"
    )
    assert not requests


def test_static_user_property_resolves_per_call_and_preserves_and_without_accumulation(
    study_stack,
):
    state = study_stack
    cfg = deepcopy(state.cfg)
    cfg.study.registered = ["f1"]
    cfg.study.context = {}
    cfg.study.static_filters = [
        {
            "_target_": "src.domain.request.filters.UserPropertyFilter",
            "field": "city",
            "property": "requested_city",
        },
        {
            "_target_": "src.domain.request.filters.TermFilter",
            "field": "active",
            "value": True,
        },
    ]
    study = instantiate(cfg.experiment, _convert_="all")
    first = study.compare(QUERY, context={"requested_city": "Москва"})
    assert {hit["id"] for hit in first.to_dict()["results"][0]["hits"]} == {"A", "B"}
    second = study.compare(QUERY, context={"requested_city": "Санкт-Петербург"})
    assert {hit["id"] for hit in second.to_dict()["results"][0]["hits"]} == {"C"}
    contradictory = study.compare(
        QUERY,
        context={"requested_city": "Москва"},
        filters=[TermFilter("city", "Санкт-Петербург")],
    )
    assert not contradictory.serp("manual", "f1").items
    fourth = study.compare(QUERY, context={"requested_city": "Москва"})
    assert {hit["id"] for hit in fourth.to_dict()["results"][0]["hits"]} == {"A", "B"}
    count = len(state.os_requests)
    for context in ({}, {"requested_city": True}):
        with pytest.raises(ValueError):
            study.compare(QUERY, context=context)
    assert len(state.os_requests) == count
    assert all(call["path"] == "/vacancies/_search" for call in state.stack.requests)

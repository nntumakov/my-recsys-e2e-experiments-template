import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate
from omegaconf import OmegaConf

from src.components.reporting.exporters.json import JSONExporter
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.jobboard.query import SourceProfile
from src.core.transformers.opensearch.jobboard.ranking import RankingConfig
from src.domain import JSONValue
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter
from src.integrations.opensearch.client import OpenSearchResult
from src.integrations.opensearch.evidence import observe

ROOT = Path(__file__).resolve().parents[1]


def configured(url="http://localhost:9200", *overrides):
    with initialize_config_dir(config_dir=str(ROOT / "configs"), version_base="1.3"):
        return compose(
            config_name="main",
            overrides=[
                "~experiments/vacancy-search/interactive@experiment",
                "+experiments/vacancy-search/jobboard/research@experiment=001-compare",
                "jobboard/lexical/source@jobboard.source=work-fixture",
                "jobboard.session.now_ms=1790640000000",
                f"connection.url={url}",
                "experiment.exporters=[]",
                *overrides,
            ],
        )


def test_all_candidates_preview_and_real_http_evidence(
    search_server, search_response, tmp_path
):
    url, requests, replies = search_server
    response = deepcopy(search_response)
    response["took"] = 7
    for i, hit in enumerate(response["hits"]["hits"]):
        hit["sort"] = [i, hit["_score"], hit["_id"]]
    replies[:] = [(200, response, {})]
    experiment = instantiate(configured(url).experiment, _convert_="all")
    text = "комплектовшик ТСД без опыта"
    plans = experiment.preview(text)
    assert requests == []
    assert (
        plans["c5"].understanding["accepted_rewrite"] == "комплектовщик тсд без опыта"
    )
    artifact = experiment.compare(text)
    assert len(requests) == 5
    for name, request in zip(experiment.variants, requests, strict=True):
        assert request["body"] == plans[name].body
    records = artifact.to_dict()["results"]
    assert len(records) == 5
    assert records[2]["hits"][0]["sort_values"] == [0, 3.5, "second"]
    assert records[2]["hits"][1]["score"] is None
    assert records[2]["hits"][1]["sort_values"] == [1, None, "first"]
    assert records[0]["hits"][0]["fields"] == response["hits"]["hits"][0]["_source"]
    assert records[0]["timing"]["server_took_ms"] == 7
    records[0]["hits"].clear()
    assert len(artifact.to_dict()["results"][0]["hits"]) == 2
    assert len(artifact.to_rows()) == 10
    assert len(artifact.rank_changes()) == 2
    output = tmp_path / "research.json"
    JSONExporter(str(output)).export(artifact)
    stored = json.loads(output.read_text())
    assert stored["schema_version"] == 1
    assert "authorization" not in output.read_text().lower()
    with pytest.raises(FileExistsError):
        JSONExporter(str(output)).export(artifact)


def test_text_candidates_do_not_require_ranking_metadata():
    cfg = configured(
        "http://localhost:9200",
        "experiment.variants=[c1,c4,c5]",
        "jobboard.source.ranking_fields={}",
    )
    # Hydra mapping merge preserves existing keys; explicitly remove them.
    cfg.jobboard.source.ranking_fields = {}
    exp = instantiate(cfg.experiment, _convert_="all")
    assert set(exp.preview("грузчик")) == {"c1", "c4", "c5"}
    cfg = configured("http://localhost:9200", "experiment.variants=[c2]")
    cfg.jobboard.source.ranking_fields = {}
    with pytest.raises(Exception, match="published_at"):
        instantiate(cfg.experiment, _convert_="all")


@pytest.mark.parametrize(
    "preset,used,absent",
    [
        ("freshness-only", "date_field", "quality_field"),
        ("quality-only", "quality_field", "date_field"),
        ("phrase-only", "phrase_pivot", "date_field"),
    ],
)
def test_single_signal_sources_do_not_read_disabled_fields(preset, used, absent):
    cfg = configured(
        "http://localhost:9200",
        "experiment.variants=[c2]",
        f"jobboard/lexical/ranking@jobboard.rankings.signals={preset}",
    )
    if preset == "freshness-only":
        cfg.jobboard.source.ranking_fields = {
            "published_at": dict(cfg.jobboard.source.ranking_fields["published_at"])
        }
    if preset == "quality-only":
        cfg.jobboard.source.ranking_fields = {
            "quality": dict(cfg.jobboard.source.ranking_fields["quality"])
        }
    if preset == "phrase-only":
        cfg.jobboard.source.ranking_fields = {}
    exp = instantiate(cfg.experiment, _convert_="all")
    body = exp.preview("водитель погрузчика")["c2"].body
    assert used in json.dumps(body)
    assert absent not in json.dumps(body)
    if preset == "phrase-only":
        assert body["rescore"]["query"]["query_weight"] == 1
        assert body["rescore"]["query"]["score_mode"] == "total"
        assert "sort" not in body


def test_bad_params_rejected():
    cases: list[dict[str, JSONValue]] = [
        {"alpha": 0.6},
        {"half_life_days": 0},
        {"beta": -1},
        {"alpha": True},
    ]
    for params in cases:
        with pytest.raises(ValueError):
            RankingConfig(
                "script_score",
                {"lang": "painless", "source": "return _score;", "params": params},
            )
    with pytest.raises(ValueError):
        RankingConfig("relevance", {"source": "not allowed"})


def test_failure_is_atomic_and_preview_checks_all_queries(
    search_server, search_response
):
    url, requests, replies = search_server
    exp = instantiate(
        configured(url, "experiment.variants=[c1,c4,c5]").experiment, _convert_="all"
    )
    with pytest.raises(ValueError, match="character budget"):
        exp.compare_queries([("good", "грузчик"), ("bad", "я" * 513)])
    assert requests == []
    replies[:] = [(200, search_response, {}), (500, {}, {})]
    with pytest.raises(RuntimeError):
        exp.compare("грузчик")
    assert len(requests) == 2
    leaf = exp.registry["c4"]
    data = PipelineData(filters=(QueryFilter("грузчик"),))
    before = deepcopy(data.items)
    replies[:] = [(200, {**search_response, "timed_out": True}, {})]
    with pytest.raises(ValueError, match="Incomplete"):
        leaf.transform(data, registry=exp.registry)
    assert data.items == before


def test_sort_keys_not_disguised_as_score_and_missing_key_fails(search_server):
    url, _, _ = search_server
    exp = instantiate(
        configured(url, "experiment.variants=[c3]").experiment, _convert_="all"
    )
    with pytest.raises(ValueError, match="Tier sorting"):
        exp.compare("грузчик")


def test_research_batch_cli(search_server, search_response, tmp_path):
    import subprocess
    import sys

    url, requests, replies = search_server
    replies[:] = [(200, search_response, {})]
    input_path = tmp_path / "queries.csv"
    input_path.write_text("id,text\nq1,грузчик\nq2,кладовшик\n")
    output = tmp_path / "batch"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.run",
            "~experiments/vacancy-search/interactive@experiment",
            "+experiments/vacancy-search/jobboard/research@experiment=002-batch",
            "jobboard/lexical/source@jobboard.source=work-fixture",
            "jobboard.session.now_ms=1790640000000",
            "experiment.variants=[c1,c4,c5]",
            f"experiment.input_path={input_path}",
            f"connection.url={url}",
            f"hydra.run.dir={output}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    records = json.loads((output / "research.json").read_text())["results"]
    assert [(r["query_id"], r["variant_id"]) for r in records] == [
        (query, variant) for query in ("q1", "q2") for variant in ("c1", "c4", "c5")
    ]
    assert len(requests) == 6


def test_research_cli_and_notebook_cells_twice(
    search_server, search_response, tmp_path, monkeypatch
):
    import subprocess
    import sys

    url, requests, replies = search_server
    response = deepcopy(search_response)
    for i, hit in enumerate(response["hits"]["hits"]):
        hit["sort"] = [i, hit["_score"], hit["_id"]]
    replies[:] = [(200, response, {})]
    output = tmp_path / "cli"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.run",
            "~experiments/vacancy-search/interactive@experiment",
            "+experiments/vacancy-search/jobboard/research@experiment=001-compare",
            "jobboard/lexical/source@jobboard.source=work-fixture",
            "jobboard.session.now_ms=1790640000000",
            f"connection.url={url}",
            f"hydra.run.dir={output}",
            'experiment.text="вадитель категории C"',
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert len(json.loads((output / "research.json").read_text())["results"]) == 5
    notebook = json.loads(
        (ROOT / "src/experiments/notebooks/blue-collar.ipynb").read_text()
    )
    input_csv = tmp_path / "queries.csv"
    input_csv.write_text("id,text\nq1,грузчик\nq2,кладовшик\n")
    monkeypatch.chdir(ROOT / "src/experiments/notebooks")
    namespace: dict[str, Any] = {}
    for run in range(2):
        for cell in notebook["cells"]:
            if cell["cell_type"] != "code":
                continue
            assert not cell["outputs"] and cell["execution_count"] is None
            exec(
                compile("".join(cell["source"]), "blue-collar.ipynb", "exec"), namespace
            )
            if "parameters" in cell["metadata"].get("tags", []):
                namespace.update(
                    source_overrides=[
                        f"connection.url={url}",
                        "jobboard/lexical/source@jobboard.source=work-fixture",
                    ],
                    input_path=input_csv,
                    output_dir=tmp_path / f"notebook-{run}",
                    now_ms=1790640000000,
                )
        assert (tmp_path / f"notebook-{run}" / "research.json").is_file()
        assert (tmp_path / f"notebook-{run}" / "searches.csv").is_file()
        html = namespace["artifact"]._repr_html_()
        assert "<table>" in html and "Полные поля" in html
    assert len(requests) == 5 + 2 * (5 + 10 + 2)


@pytest.mark.parametrize(
    "ablation",
    [
        "none",
        "no-phrase",
        "title-2",
        "title-8",
        "coverage-strict",
        "no-aliases",
        "no-attributes",
        "strict-coverage",
        "shared-fields",
        "no-spelling",
        "no-layout",
        "no-curated-rewrites",
        "rewrite-off",
        "rewrite-weight-0.4",
    ],
)
def test_all_ablation_groups_compose(ablation):
    exp = instantiate(
        configured(
            "http://localhost:9200",
            f"jobboard/lexical/research/ablation@_global_={ablation}",
        ).experiment,
        _convert_="all",
    )
    plans = exp.preview("rkfljdobr ТСД")
    assert set(plans) == {"c1", "c2", "c3", "c4", "c5"}
    if ablation == "rewrite-off":
        assert plans["c4"].body == plans["c5"].body
    if ablation == "no-layout":
        assert plans["c5"].understanding["accepted_rewrite"] is None


def test_numeric_oracles_for_authored_formulas():
    """Validate the documented arithmetic, not execution of the Painless source."""
    from math import exp, log

    def fresh(age):
        return exp(-log(2) * max(0, age) / 14)

    def quality(value):
        return max(0, value) / (max(0, value) + 10)

    assert fresh(0) == 1
    assert fresh(14) == pytest.approx(0.5)
    assert fresh(28) == pytest.approx(0.25)
    assert fresh(-7) == 1
    assert quality(0) == quality(-5) == 0
    assert quality(10) == 0.5
    assert quality(90) == 0.9
    assert 12 * (1 + 0.2 * fresh(28)) == pytest.approx(12.6)
    assert 11 * (1 + 0.2 * fresh(0) + 0.2 * quality(10)) == pytest.approx(14.3)
    assert 7 * (1 + 0.2 * fresh(0) + 0.2 * quality(90)) == pytest.approx(9.66)
    for score in (0, 0.01, 10, 100):
        for age in (0, 14, 1000):
            for x in (-10, 0, 10, 1e9):
                result = score * (1 + 0.2 * fresh(age) + 0.2 * quality(x))
                assert score <= result <= 1.4 * score
                assert score * (1 + 0 * fresh(age) + 0 * quality(x)) == score
    phrase = 2 / (2 + 2)
    assert 2 * phrase * (1 + 0.2 * 0.5 + 0.2 * 0.5) == pytest.approx(1.2)


def test_context_is_snapshotted_and_ranked_query_keeps_filters(search_server):
    url, requests, _ = search_server
    cfg = configured(url, "experiment.variants=[c1,c2,c4,c5]")
    cfg.experiment.filters = [
        {
            "_target_": "src.domain.request.filters.UserPropertyFilter",
            "field": "city",
            "property": "city",
        }
    ]
    cfg.experiment.context = {"city": "Москва"}
    exp = instantiate(cfg.experiment, _convert_="all")
    cfg.experiment.context["city"] = "Казань"
    plans = exp.preview("кладовшик")
    assert requests == []
    for plan in plans.values():
        text = json.dumps(plan.body, ensure_ascii=False)
        assert "Москва" in text and "Казань" not in text
        assert '"term": {"city": "Москва"}' in text


def test_source_metadata_anomalies_are_labelled_as_local_evidence(
    search_server, search_response
):
    url, _, replies = search_server
    response = deepcopy(search_response)
    response["hits"]["hits"][0]["_source"].update(
        published_at="2099-01-01T00:00:00Z", card_completeness=-3
    )
    replies[:] = [(200, response, {})]
    exp = instantiate(
        configured(url, "experiment.variants=[c2]").experiment, _convert_="all"
    )
    artifact = exp.compare("грузчик")
    metadata = artifact.to_dict()["results"][0]["hits"][0]["metadata"]
    assert metadata["flags"] == ["future_source_date", "negative_quality"]
    assert metadata["origin"] == "returned_source_not_doc_values"
    assert "metadata" in artifact.to_rows()[0]


@pytest.fixture
def quality_ranking():
    return instantiate(
        OmegaConf.load(ROOT / "configs/jobboard/lexical/ranking/quality-only.yaml"),
        _convert_="all",
    )


@pytest.mark.parametrize(
    "name,fields,value,flags",
    [
        ("quality", {"quality": -3}, -3, ["negative_quality"]),
        ("ranking.quality", {"ranking": {"quality": -3}}, -3, ["negative_quality"]),
        ("a.b.quality", {"a": {"b": {"quality": -3}}}, -3, ["negative_quality"]),
        ("ranking.quality", {"ranking.quality": -2}, -2, ["negative_quality"]),
        ("ranking.quality", {"ranking.quality": 0, "ranking": {"quality": -3}}, 0, []),
        (
            "ranking.quality",
            {"ranking.quality": None, "ranking": {"quality": -3}},
            None,
            ["missing_source_quality"],
        ),
        (
            "ranking.quality",
            {"ranking": {"quality": None}},
            None,
            ["missing_source_quality"],
        ),
        ("ranking.quality", {}, None, ["missing_source_quality"]),
        ("ranking.quality", {"ranking": 5}, None, ["missing_source_quality"]),
        (
            "ranking.quality",
            {"ranking": [{"quality": -3}]},
            None,
            ["missing_source_quality"],
        ),
        ("ranking.quality", {"ranking": {"quality": False}}, False, []),
        ("ranking.quality", {"ranking": {"quality": ""}}, "", []),
        ("ranking.quality", {"ranking": {"quality": [-3]}}, [-3], []),
        ("ranking.quality", {"ranking": {"quality": {"value": -3}}}, {"value": -3}, []),
        ("quality.keyword", {"quality": "low"}, None, ["missing_source_quality"]),
        ("ranking.0", {"ranking": {"0": -3}}, -3, ["negative_quality"]),
        ("ranking.0", {"ranking": [-3]}, None, ["missing_source_quality"]),
        ("a.b.quality", {"a": {"b.quality": -3}}, None, ["missing_source_quality"]),
    ],
)
def test_source_diagnostics_resolve_paths(quality_ranking, name, fields, value, flags):
    source = SourceProfile(
        "test",
        ["title"],
        ranking_fields={"quality": {"field": name, "type": "numeric"}},
    )
    item = Vacancy("v1", deepcopy(fields))
    metadata = quality_ranking.source_diagnostics(item, source, 1790640000000)
    assert metadata == {
        "values": {"quality": value},
        "flags": flags,
        "origin": "returned_source_not_doc_values",
    }
    assert type(metadata["values"]["quality"]) is type(value)
    assert item.fields == fields


def test_source_diagnostics_snapshot_nested_values(quality_ranking):
    source = SourceProfile(
        "test",
        ["title"],
        ranking_fields={"quality": {"field": "ranking.quality", "type": "numeric"}},
    )
    item = Vacancy("v1", {"ranking": {"quality": [{"value": -3}]}})
    metadata = quality_ranking.source_diagnostics(item, source, 0)
    metadata["values"]["quality"][0]["value"] = 12
    assert item.fields == {"ranking": {"quality": [{"value": -3}]}}
    assert quality_ranking.source_diagnostics(item, source, 0)["values"] == {
        "quality": [{"value": -3}]
    }


@pytest.fixture
def freshness_ranking():
    return instantiate(
        OmegaConf.load(ROOT / "configs/jobboard/lexical/ranking/freshness-only.yaml"),
        _convert_="all",
    )


@pytest.mark.parametrize(
    "value,flags",
    [
        (4070908800000, ["future_source_date"]),
        (1790640000001, ["future_source_date"]),
        (1790640000000.5, ["future_source_date"]),
        (1790640000000, []),
        (1790640000000.0, []),
        (1790639999999.5, []),
        (1790640000, []),
        (-1, []),
        (1e100, ["future_source_date"]),
        ("2099-01-01T00:00:00Z", ["future_source_date"]),
        ("2099-01-01T03:00:00+03:00", ["future_source_date"]),
        ("2026-09-29T00:00:00Z", []),
        ("2026-09-29T03:00:00+03:00", []),
        ("2026-09-28T23:59:59Z", []),
        ("2099-01-01T00:00:00", ["source_date_without_timezone"]),
        ("invalid", ["unparsed_source_date"]),
        ("4070908800000", ["unparsed_source_date"]),
        (None, ["missing_source_published_at"]),
        (True, []),
        (False, []),
        ([4070908800000], []),
    ],
)
def test_source_dates_preserve_representation_and_fixed_time(
    freshness_ranking, value, flags
):
    sources: tuple[tuple[str, dict[str, JSONValue]], ...] = (
        ("published_at", {"published_at": value}),
        ("ranking.published_at", {"ranking": {"published_at": value}}),
    )
    for name, fields in sources:
        source = SourceProfile(
            "test",
            ["title"],
            ranking_fields={"published_at": {"field": name, "type": "date"}},
        )
        item = Vacancy("v1", deepcopy(fields))
        before = deepcopy(freshness_ranking.script)
        expected = {
            "values": {"published_at": value},
            "flags": flags,
            "origin": "returned_source_not_doc_values",
        }
        assert (
            freshness_ranking.source_diagnostics(item, source, 1790640000000)
            == expected
        )
        assert (
            freshness_ranking.source_diagnostics(item, source, 1790640000000)
            == expected
        )
        assert item.fields == fields
        assert freshness_ranking.script == before


@pytest.mark.parametrize("score", [0, 1, 1.25, None])
def test_research_sort_score_preserves_numbers_and_null(search_response, score):
    for i, hit in enumerate(search_response["hits"]["hits"]):
        hit["sort"] = [float(i), score, hit["_id"]]
    serp = observe(OpenSearchResult(search_response), {"sort": []}, {}, 5)
    assert serp.sort_values == {
        "second": [0.0, score, "second"],
        "first": [1.0, score, "first"],
    }
    assert [item.score for item in serp.items] == [3.5, None]
    assert serp.server_took_ms is None


@pytest.mark.parametrize(
    "score",
    [
        "invalid_score",
        "1.0",
        True,
        False,
        [],
        {},
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_research_sort_score_rejects_invalid_values(search_response, score):
    for i, hit in enumerate(search_response["hits"]["hits"]):
        hit["sort"] = [i, hit["_score"], hit["_id"]]
    search_response["hits"]["hits"][-1]["sort"][1] = score
    with pytest.raises(ValueError, match="sort|Tier"):
        observe(OpenSearchResult(search_response), {"sort": []}, {}, 5)


@pytest.mark.parametrize("took", [None, True, False, "1", 1.0, -1, [], {}])
def test_research_timing_rejects_present_invalid_values(search_response, took):
    search_response["took"] = took
    search_response["hits"]["hits"] = []
    with pytest.raises(ValueError, match="server timing"):
        observe(OpenSearchResult(search_response), {}, {}, 5)


@pytest.mark.parametrize("timing", [{}, {"took": 0}, {"took": 7}])
def test_research_timing_and_empty_sorted_results(search_response, timing):
    search_response.update(timing)
    search_response["hits"]["hits"] = []
    serp = observe(OpenSearchResult(search_response), {"sort": []}, {}, 5)
    assert serp.items == [] and serp.sort_values == {}
    assert serp.server_took_ms == timing.get("took")
    assert serp.client_elapsed_ms == 5


def test_research_without_sort_keeps_generic_evidence(search_response):
    search_response["hits"]["hits"][0]["sort"] = ["custom"]
    serp = observe(OpenSearchResult(search_response), {}, {}, 5)
    assert serp.sort_values == {"second": ["custom"], "first": []}


def test_boolean_source_date_is_not_epoch_one(freshness_ranking):
    source = SourceProfile(
        "test",
        ["title"],
        ranking_fields={"published_at": {"field": "published_at", "type": "date"}},
    )
    assert (
        freshness_ranking.source_diagnostics(
            Vacancy("v1", {"published_at": True}), source, 0
        )["flags"]
        == []
    )


@pytest.mark.parametrize(
    "preset,roles,flags",
    [
        (
            "balanced",
            ["published_at", "quality"],
            ["future_source_date", "negative_quality"],
        ),
        ("freshness-only", ["published_at"], ["future_source_date"]),
        ("quality-only", ["quality"], ["negative_quality"]),
    ],
)
def test_nested_metadata_http_export_preserves_request_and_source(
    search_server, search_response, tmp_path, preset, roles, flags
):
    url, requests, replies = search_server
    cfg = configured(
        url,
        "experiment.variants=[c2]",
        f"jobboard/lexical/ranking@jobboard.rankings.signals={preset}",
    )
    cfg.jobboard.source.ranking_fields = {
        role: {
            "field": f"ranking.{role}",
            "type": "date" if role == "published_at" else "numeric",
        }
        for role in roles
    }
    cfg.experiment.context = {"city": "Москва"}
    cfg.experiment.filters = [
        {
            "_target_": "src.domain.request.filters.UserPropertyFilter",
            "field": "city",
            "property": "city",
        }
    ]
    exp = instantiate(cfg.experiment, _convert_="all")
    original_fields = {
        "title": "Грузчик",
        "ranking": {"published_at": 4070908800000, "quality": -3},
        "extra": {"tags": ["склад"]},
    }
    search_response["hits"]["hits"][0]["_source"] = deepcopy(original_fields)
    replies[:] = [(200, search_response, {})]
    plan = exp.preview("грузчик")["c2"].body
    script_before = deepcopy(exp.registry["c2"].ranking.script)
    assert requests == []
    artifact = exp.compare("грузчик")
    output = tmp_path / "research.json"
    JSONExporter(str(output)).export(artifact)
    stored = json.loads(output.read_text())
    record = stored["results"][0]
    assert stored["schema_version"] == 1
    assert record["request_body"] == requests[0]["body"] == plan
    assert plan["query"]["script_score"]["query"]["bool"]["filter"] == [
        {"term": {"city": "Москва"}}
    ]
    params = plan["query"]["script_score"]["script"]["params"]
    assert {name: params[name] for name in exp.registry["c2"].ranking.field_params} == {
        name: f"ranking.{role}"
        for name, role in exp.registry["c2"].ranking.field_params.items()
    }
    hit = record["hits"][0]
    assert hit["fields"] == original_fields
    assert hit["score"] == 3.5
    assert hit["metadata"] == {
        "values": {role: original_fields["ranking"][role] for role in roles},
        "flags": flags,
        "origin": "returned_source_not_doc_values",
    }
    assert record["hits"][1]["metadata"]["flags"] == [
        f"missing_source_{role}" for role in roles
    ]
    assert search_response["hits"]["hits"][0]["_source"] == original_fields
    hit["fields"]["extra"]["tags"].clear()
    hit["metadata"]["values"].clear()
    assert artifact.to_dict()["results"][0]["hits"][0]["fields"] == original_fields
    assert artifact.to_dict()["results"][0]["hits"][0]["metadata"]["flags"] == flags
    assert exp.registry["c2"].ranking.script == script_before
    assert exp.preview("грузчик")["c2"].body == plan


@pytest.mark.parametrize("failure", ["sort", "took"])
def test_malformed_evidence_preserves_payload_and_allows_retry(
    search_server, search_response, failure
):
    from src.domain.collection.serp import SERP
    from src.domain.request.filters import TermFilter

    url, requests, replies = search_server
    exp = instantiate(
        configured(url, "experiment.variants=[c3]").experiment, _convert_="all"
    )
    for i, hit in enumerate(search_response["hits"]["hits"]):
        hit["sort"] = [i, hit["_score"], hit["_id"]]
    broken = deepcopy(search_response)
    if failure == "sort":
        broken["hits"]["hits"][-1]["sort"][1] = "invalid_score"
    else:
        broken["took"] = None
    replies[:] = [(200, broken, {})]
    data = PipelineData(
        context={"session": {"values": [1]}},
        items=SERP([Vacancy("old", {"title": "Старая выдача"}, 10)]),
        filters=(QueryFilter("грузчик"), TermFilter("active", True)),
        branches={"saved": PipelineData(context={"marker": [1]})},
    )
    before = data.copy(deep=True)
    original_items = data.items
    with pytest.raises(ValueError, match="Tier|server timing"):
        exp.registry["c3"].transform(data, registry=exp.registry)
    assert data.items is original_items and data.items == before.items
    assert data.context == before.context
    assert data.filters == before.filters
    assert set(data.branches) == set(before.branches)
    assert data.branches["saved"].context == before.branches["saved"].context
    replies[:] = [(200, broken, {}), (200, search_response, {})]
    with pytest.raises(ValueError, match="Tier|server timing"):
        exp.searches["c3"].search("грузчик")
    recovered = exp.searches["c3"].search("грузчик")
    assert len(requests) == 3
    assert [item.id for item in recovered.items] == ["second", "first"]
    assert recovered.sort_values["first"] == [1, None, "first"]
    assert recovered.server_took_ms is None
    assert recovered.metadata["second"]["flags"] == ["missing_source_category"]
    recovered.items[0].fields.clear()
    again = exp.searches["c3"].search("грузчик")
    assert again.items[0].fields == search_response["hits"]["hits"][0]["_source"]


@pytest.mark.parametrize("failure", ["sort", "took"])
def test_malformed_second_batch_response_stops_without_export(
    search_server, search_response, tmp_path, monkeypatch, failure
):
    from unittest.mock import Mock

    url, requests, replies = search_server
    for i, hit in enumerate(search_response["hits"]["hits"]):
        hit["sort"] = [i, hit["_score"], hit["_id"]]
    broken = deepcopy(search_response)
    if failure == "sort":
        broken["hits"]["hits"][-1]["sort"][1] = "invalid_score"
    else:
        broken["took"] = None
    input_path = tmp_path / "queries.csv"
    input_path.write_text(
        "id,text\nq1,грузчик\nq2,кладовщик\nq3,повар\n", encoding="utf-8"
    )
    cfg = configured(
        url,
        "experiment.variants=[c3]",
        "experiment.text=null",
        f"experiment.input_path={input_path}",
    )
    new_path, existing_path = tmp_path / "new.json", tmp_path / "existing.json"
    existing_path.write_bytes(b"previous result\n")
    # Each real exporter is first once, so either accidental publication is visible.
    for path in (new_path, existing_path):
        cfg.experiment.exporters = [
            {
                "_target_": "src.components.reporting.exporters.json.JSONExporter",
                "path": str(path),
            }
        ]
        exp = instantiate(cfg.experiment, _convert_="all")
        exporter = exp.exporters[0]
        export = Mock(wraps=exporter.export)
        monkeypatch.setattr(exporter, "export", export)
        replies[:] = [
            (200, search_response, {}),
            (200, broken, {}),
            (200, search_response, {}),
        ]
        requests.clear()
        with pytest.raises(ValueError, match="Tier|server timing"):
            exp._wrapped_run()
        assert len(requests) == 2
        assert len(replies) == 1
        export.assert_not_called()
        assert not new_path.exists()
        assert existing_path.read_bytes() == b"previous result\n"


def test_nonfinite_sort_score_fails_at_http_boundary(search_server, search_response):
    url, requests, replies = search_server
    exp = instantiate(
        configured(url, "experiment.variants=[c3]").experiment, _convert_="all"
    )
    for i, hit in enumerate(search_response["hits"]["hits"]):
        hit["sort"] = [i, hit["_score"], hit["_id"]]
    search_response["hits"]["hits"][-1]["sort"][1] = float("nan")
    replies[:] = [(200, json.dumps(search_response).encode(), {})]
    with pytest.raises(RuntimeError, match="transport or JSON"):
        exp.compare("грузчик")
    assert len(requests) == 1

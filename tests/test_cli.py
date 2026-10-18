import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate
from omegaconf import OmegaConf

from src.components.reporting.exporters.abstract import BaseExporter
from src.core.registry import BasicTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.domain.collection.abstract import BaseDocumentCollections
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter
from src.experiments.direct.search import SearchExperiment
from src.run import run

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "Bearer TEST_ONLY_NOT_A_REAL_SECRET"


def cli(url, directory, text, *overrides):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "src.run",
            f"connection.url={url}",
            "search.index=jobs",
            "search.fields=[title,text]",
            "search.size=5",
            f"hydra.run.dir={directory}",
            *overrides,
        ],
        input=text,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env={**os.environ, "OPENSEARCH_AUTHORIZATION": TOKEN},
        timeout=20,
    )


def test_cli_two_queries_and_hydra_secret_handling(search_server, tmp_path):
    url, requests, _ = search_server
    result = cli(url, tmp_path, "python\n\njava\n")
    assert result.returncode == 0, result.stderr + result.stdout
    assert len(requests) == 2
    assert [
        r["body"]["query"]["bool"]["must"][0]["multi_match"]["query"] for r in requests
    ] == ["python", "java"]
    assert all(r["authorization"] == TOKEN for r in requests)
    artifact = json.loads((tmp_path / "searches.json").read_text())
    assert [r["query"] for r in artifact["searches"]] == ["python", "java"]
    for record in artifact["searches"]:
        assert [i["id"] for i in record["items"]] == ["second", "first"]
        assert record["items"][0]["fields"]["extra"] == {"tags": ["python"]}
        assert record["items"][1]["score"] is None
        assert json.dumps(record, ensure_ascii=False, indent=2) in result.stdout
    assert TOKEN not in result.stdout + result.stderr
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text(), path


def test_failed_second_request_stops_session_without_artifact(
    search_server, search_response, tmp_path
):
    url, requests, replies = search_server
    replies[:] = [(200, search_response, {}), (500, TOKEN.encode(), {})]
    result = cli(url, tmp_path, "first\nsecond\nthird\n")
    assert result.returncode == 1
    assert len(requests) == 2
    assert '"query": "first"' in result.stdout
    assert not (tmp_path / "searches.json").exists()
    assert TOKEN not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_empty_session_saves_and_invalid_config_never_searches(search_server, tmp_path):
    url, requests, _ = search_server
    result = cli(url, tmp_path / "empty", "")
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "empty/searches.json").read_text()) == {
        "searches": []
    }
    result = cli(url, tmp_path / "invalid", "python\n", "search.size=0")
    assert result.returncode == 1
    assert requests == []
    assert not (tmp_path / "invalid/searches.json").exists()


def test_real_hydra_composition(search_server, tmp_path):
    url, requests, _ = search_server
    with initialize_config_dir(config_dir=str(ROOT / "configs"), version_base="1.3"):
        cfg = compose(
            config_name="main",
            overrides=[
                f"connection.url={url}",
                "search.index=jobs",
                "search.fields=[title]",
                "search.size=1",
                f"experiment.exporters.0.path={tmp_path}/result.json",
            ],
        )
    experiment = instantiate(cfg.experiment, _convert_="all")
    assert isinstance(experiment, SearchExperiment)
    assert experiment.registry["search_pipeline"] is experiment.pipeline
    assert requests == []


def test_ctrl_c_exit_and_no_export(monkeypatch):
    exporter = Mock(spec=BaseExporter)
    pipeline = Mock(spec=BaseTransformer)
    experiment = SearchExperiment(
        BasicTransformerRegistry({"p": pipeline}), "p", [exporter]
    )
    monkeypatch.setattr("src.run.instantiate", lambda *args, **kwargs: experiment)

    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    with pytest.raises(SystemExit) as error:
        run.__wrapped__(OmegaConf.create({"experiment": {}}))
    assert error.value.code == 130
    exporter.export.assert_not_called()
    pipeline.transform.assert_not_called()


def test_each_request_has_independent_context_and_results(monkeypatch):
    class MutatingSearch(BaseTransformer):
        def __init__(self):
            self.previous = None

        def transform(self, data, *, registry):
            assert data.context == {"profile": {"city": "Moscow"}}
            data.context["profile"]["city"] = "changed"
            if self.previous is not None:
                self.previous.fields["title"] = "mutated later"
            self.previous = Vacancy("v", {"title": data.filters[0].text})
            data.items = SERP([self.previous])
            return data

    values = iter(["first", "second"])

    def read(prompt):
        try:
            return next(values)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", read)
    registry = BasicTransformerRegistry({"p": MutatingSearch()})
    experiment = SearchExperiment(
        registry, "p", [], context={"profile": {"city": "Moscow"}}
    )
    result = experiment.run().to_dict()
    assert result == {
        "searches": [
            {
                "query": query,
                "items": [{"id": "v", "score": None, "fields": {"title": query}}],
            }
            for query in ("first", "second")
        ]
    }


def test_static_query_rejected():
    registry = BasicTransformerRegistry({"p": Mock(spec=BaseTransformer)})
    with pytest.raises(ValueError):
        SearchExperiment(registry, "p", [], filters=[QueryFilter("static")])


@pytest.mark.parametrize("unmerged", [False, True])
def test_incomplete_search_pipeline_never_prints_or_exports(
    monkeypatch, capsys, unmerged
):
    result = PipelineData(
        items=SERP() if unmerged else BaseDocumentCollections(),
        branches={"pending": PipelineData(items=SERP([Vacancy("v")]))}
        if unmerged
        else {},
    )
    pipeline = Mock(spec=BaseTransformer)
    pipeline.transform.return_value = result
    exporter = Mock(spec=BaseExporter)
    experiment = SearchExperiment(
        BasicTransformerRegistry({"search": pipeline}), "search", [exporter]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "python")
    with pytest.raises(ValueError, match="merged SERP"):
        experiment._wrapped_run()
    assert capsys.readouterr().out == ""
    pipeline.transform.assert_called_once()
    exporter.export.assert_not_called()


def test_configuration_failure_does_not_print_resolved_authorization(
    search_server, tmp_path
):
    url, requests, _ = search_server
    result = cli(url, tmp_path, "python\n", "search.fields=[]")
    assert result.returncode == 1
    assert requests == []
    assert TOKEN not in result.stdout + result.stderr
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text()


def test_comparison_cli_replaces_interactive_experiment(search_server, tmp_path):
    url, requests, _ = search_server
    result = cli(
        url,
        tmp_path,
        "",
        "~experiments/vacancy-search/interactive@experiment",
        "+experiments/vacancy-ranking/local@experiment=001-comparison",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert requests == []
    assert not (tmp_path / "searches.json").exists()
    artifact = json.loads((tmp_path / "comparison.json").read_text())
    assert [row["pipeline"] for row in artifact["results"]] == ["first", "fused"]

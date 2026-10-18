import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate

from src.components.reporting.artifacts.batch import BatchSearchArtifact
from src.components.reporting.exporters.csv import SearchCSVExporter
from src.core.registry import BasicTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.domain import JSONValue
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter
from src.experiments.direct.search import SearchExperiment

ROOT = Path(__file__).resolve().parents[1]
JOBBOARD = "experiments/vacancy-search/jobboard/lexical@experiment"


def compose_experiment(url, *overrides, batch=False):
    with initialize_config_dir(config_dir=str(ROOT / "configs"), version_base="1.3"):
        cfg = compose(
            config_name="main",
            overrides=[
                "~experiments/vacancy-search/interactive@experiment",
                f"+{JOBBOARD}={'003-batch' if batch else '001-interactive'}",
                f"connection.url={url}",
                "connection.authorization=null",
                "jobboard/lexical/source@jobboard.source=fixture",
                "experiment.exporters=[]",
                *overrides,
            ],
        )
    return instantiate(cfg.experiment, _convert_="all")


def write_queries(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "text"])
        writer.writerows(rows)


def test_notebook_search_reuses_configuration_without_print_or_export(
    search_server, capsys
):
    url, requests, _ = search_server
    for _ in range(2):
        experiment = compose_experiment(url)
        # A notebook can choose another composed pipeline by rebuilding the config.
        fused = compose_experiment(url, "experiment.pipeline=fused")
        first = experiment.search(" python ", filters=[TermFilter("city", "Москва")])
        second = fused.search("java")
        assert first.items[0].fields["extra"] == {"tags": ["python"]}
        assert [item.id for item in second.items] == ["second", "first"]
        assert requests[-3]["body"]["query"]["bool"]["filter"] == [
            {"term": {"city": "Москва"}}
        ]
    assert len(requests) == 6
    assert capsys.readouterr().out == ""


def test_manual_search_context_filters_snapshots_and_failure_recovery():
    class Search(BaseTransformer):
        def __init__(self):
            self.data = []
            self.tags: list[JSONValue] = []
            self.document = Vacancy("v", {"nested": self.tags})

        def transform(self, data, *, registry):
            self.data.append(data)
            if data.filters[-1].text == "fail":
                raise RuntimeError("failed search")
            self.tags.append(data.filters[-1].text)
            data.context["nested"]["values"].append("mutated")
            data.items = SERP([self.document])
            return data

    search = Search()
    context: dict[str, JSONValue] = {"city": "Москва", "nested": {"values": []}}
    experiment = SearchExperiment(
        BasicTransformerRegistry({"search": search}),
        "search",
        context=context,
        filters=[UserPropertyFilter("city", "city")],
    )
    extra_context: dict[str, JSONValue] = {"city": "Казань", "nested": {"values": []}}
    first = experiment.search(
        "first", context=extra_context, filters=[TermFilter("x", 1)]
    )
    with pytest.raises(RuntimeError):
        experiment.search("fail")
    experiment.search("second")
    assert first.items[0].fields == {"nested": ["first"]}
    assert context == {"city": "Москва", "nested": {"values": []}}
    assert extra_context == {"city": "Казань", "nested": {"values": []}}
    assert search.data[0].context["city"] == "Казань"
    assert search.data[-1].context["city"] == "Москва"
    assert search.data[-1].context["nested"] == {"values": ["mutated"]}
    assert search.data[0].filters == (
        UserPropertyFilter("city", "city"),
        TermFilter("x", 1),
        QueryFilter("first"),
    )
    assert search.data[-1].filters == (
        UserPropertyFilter("city", "city"),
        QueryFilter("second"),
    )


@pytest.mark.parametrize("text,filters", [(" ", []), ("x", [QueryFilter("y")])])
def test_manual_search_rejects_invalid_input_before_pipeline(text, filters):
    pipeline = Mock(spec=BaseTransformer)
    experiment = SearchExperiment(BasicTransformerRegistry({"p": pipeline}), "p")
    with pytest.raises(ValueError):
        experiment.search(text, filters=filters)
    pipeline.transform.assert_not_called()


@pytest.mark.parametrize("leaf_name", ["baseline_leaf", "parsed_leaf", "lexical_leaf"])
def test_query_preview_matches_actual_http_without_mutating_payload(
    search_server, leaf_name
):
    url, requests, _ = search_server
    experiment = compose_experiment(url)
    leaf = experiment.registry[leaf_name]
    data = PipelineData(
        filters=[QueryFilter("python developer"), TermFilter("city", "Москва")]
    )
    previous_items = data.items
    query = leaf.build_query(data)
    assert requests == []
    assert data.items is previous_items
    assert data.filters == (
        QueryFilter("python developer"),
        TermFilter("city", "Москва"),
    )
    leaf.transform(data, registry=experiment.registry)
    assert requests[0]["body"] == query


@pytest.mark.parametrize("jobboard", [False, True])
def test_batch_cli_csv_roundtrip(search_server, search_response, tmp_path, jobboard):
    url, requests, replies = search_server
    path = tmp_path / "queries.csv"
    text = 'Разработчик, "Python"\nудалённо'
    write_queries(path, [("q1", text), ("q2", "java")])
    empty = {"timed_out": False, "_shards": {"failed": 0}, "hits": {"hits": []}}
    replies[:] = [(200, search_response, {})] * (2 if jobboard else 1) + [
        (200, empty, {})
    ]
    output = tmp_path / "run"
    selection = (
        [
            f"+{JOBBOARD}=003-batch",
            "jobboard/lexical/source@jobboard.source=fixture",
            "experiment.search.pipeline=fused",
        ]
        if jobboard
        else [
            "+experiments/vacancy-search/batch@experiment=001-basic",
            "search.index=jobs",
            "search.fields=[title]",
            "search.size=5",
        ]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.run",
            "~experiments/vacancy-search/interactive@experiment",
            *selection,
            f"connection.url={url}",
            f"experiment.input_path={path}",
            f"hydra.run.dir={output}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "OPENSEARCH_AUTHORIZATION": "Bearer RESEARCH_TEST"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with (output / "searches.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(requests) == (4 if jobboard else 2)
    assert len(rows) == 3
    assert [row["rank"] for row in rows] == ["1", "2", ""]
    assert [row["query_id"] for row in rows] == ["q1", "q1", "q2"]
    assert rows[0]["query"] == text
    assert (
        json.loads(rows[0]["fields"]) == search_response["hits"]["hits"][0]["_source"]
    )
    assert rows[1]["score"] == (str(2 / 62) if jobboard else "")
    assert rows[2] == dict.fromkeys(BatchSearchArtifact.columns, "") | {
        "query_id": "q2",
        "query": "java",
    }
    assert "search>" not in result.stdout
    for file in output.rglob("*"):
        if file.is_file():
            assert "Bearer RESEARCH_TEST" not in file.read_text()


@pytest.mark.parametrize(
    "content",
    [
        "id,text\n",
        "id,query\na,b\n",
        "id,text,extra\na,b,c\n",
        "id,text\na,first\na,second\n",
        "id,text\na,first\nb, \n",
        "id,text\na,first\n,second\n",
        "id,text\na,first\nb,second,extra\n",
        'id,text\na,first\nb,"unterminated\n',
    ],
)
def test_batch_validates_whole_csv_before_search(search_server, tmp_path, content):
    url, requests, _ = search_server
    source = tmp_path / "queries.csv"
    source.write_text(content)
    experiment = compose_experiment(url, f"experiment.input_path={source}", batch=True)
    with pytest.raises((ValueError, csv.Error)):
        experiment.run()
    assert requests == []


def test_batch_second_failure_never_exports_and_can_be_rerun(
    search_server, search_response, tmp_path
):
    url, requests, replies = search_server
    source = tmp_path / "queries.csv"
    target = tmp_path / "searches.csv"
    write_queries(source, [("a", "python"), ("b", "java")])
    experiment = compose_experiment(url, f"experiment.input_path={source}", batch=True)
    experiment.exporters = (SearchCSVExporter(str(target)),)
    replies[:] = [(200, search_response, {}), (500, b"failure", {})]
    with pytest.raises(RuntimeError):
        experiment._wrapped_run()
    assert len(requests) == 2
    assert not target.exists()
    replies[:] = [(200, search_response, {})]
    artifact = experiment.run()
    assert not target.exists()
    experiment.exporters[0].export(artifact)
    saved = target.read_bytes()
    with pytest.raises(FileExistsError):
        experiment.exporters[0].export(artifact)
    assert target.read_bytes() == saved
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "queries.csv",
        "searches.csv",
    ]


def test_batch_artifact_snapshot_and_serialization_failure_before_write(tmp_path):
    nested: list[JSONValue] = ["original"]
    serp = SERP([Vacancy("v", {"nested": nested}, None)])
    artifact = BatchSearchArtifact([("q", "query", serp), ("empty", "no hits", SERP())])
    nested.append("changed")
    searches = artifact.to_dict()["searches"]
    assert isinstance(searches, list)
    searches.clear()
    rows = artifact.to_rows()
    fields = rows[0]["fields"]
    assert isinstance(fields, str)
    assert json.loads(fields) == {"nested": ["original"]}
    assert rows[1]["document_id"] is None
    invalid = BatchSearchArtifact(
        [("q", "query", SERP([Vacancy("v", {"bad": float("nan")})]))]
    )
    with pytest.raises(ValueError):
        SearchCSVExporter(str(tmp_path / "result.csv")).export(invalid)
    assert list(tmp_path.iterdir()) == []


def test_shipped_notebook_cells_execute_repeatedly_with_local_http(
    search_server, tmp_path, monkeypatch
):
    url, requests, _ = search_server
    path = ROOT / "src/experiments/notebooks/search.ipynb"
    notebook = json.loads(path.read_text())
    monkeypatch.chdir(path.parent)
    for iteration in range(2):
        output_path = tmp_path / f"notebook-{iteration}.csv"
        namespace = {}
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            assert cell["outputs"] == []
            assert cell["execution_count"] is None
            exec(
                compile("".join(cell["source"]), f"{path}:cell-{index}", "exec"),
                namespace,
            )
            if "parameters" in cell["metadata"].get("tags", []):
                namespace.update(
                    source_overrides=[
                        f"connection.url={url}",
                        "connection.authorization=null",
                        "jobboard/lexical/source@jobboard.source=fixture",
                    ],
                    output_path=output_path,
                )
        with output_path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 8
        assert {row["query_id"] for row in rows} == {"q01", "q02", "q03", "q04"}
        assert requests[-5]["body"] == namespace["dsl"]
    assert len(requests) == 10


def test_batch_inherits_static_context_and_filters(search_server, tmp_path):
    url, requests, _ = search_server
    source = tmp_path / "queries.csv"
    source.write_text("text,id\npython,a\njava,b\n", encoding="utf-8")
    experiment = compose_experiment(
        url,
        f"experiment.input_path={source}",
        '+experiment.search.context.city="Москва"',
        "experiment.search.filters=[{_target_:src.domain.request.filters.UserPropertyFilter,field:city,property:city}]",
        batch=True,
    )
    experiment.run()
    assert len(requests) == 2
    assert all(
        request["body"]["query"]["bool"]["filter"] == [{"term": {"city": "Москва"}}]
        for request in requests
    )

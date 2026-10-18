"""Controlled HTTP contract evidence; this fixture does not execute BM25."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate

from src.core.transformers.data import PipelineData
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter

ROOT = Path(__file__).resolve().parents[1]
SELECT = [
    "~experiments/vacancy-search/interactive@experiment",
    "+experiments/vacancy-search/jobboard/lexical@experiment=001-interactive",
    "jobboard/lexical/source@jobboard.source=fixture",
]


def cli(url, directory, text="", *, comparison=False, overrides=()):
    selection = list(SELECT)
    if comparison:
        selection[1] = selection[1].replace("001-interactive", "002-comparison")
        selection.append("jobboard/lexical/cases@jobboard.cases=fixture-smoke")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "src.run",
            *selection,
            f"connection.url={url}",
            f"hydra.run.dir={directory}",
            "jobboard.final_k=2",
            "jobboard.evaluation_k=2",
            *overrides,
        ],
        input=text,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env={**os.environ, "OPENSEARCH_AUTHORIZATION": "Bearer SYNTHETIC_TEST"},
        timeout=30,
    )


def response(*rows):
    documents = json.loads(
        (ROOT / "tests/fixtures/jobboard/vacancies.json").read_text()
    )
    # Authored corpus stores complete OpenSearch hits, without invented scores.
    by_id = {document["_id"]: document["_source"] for document in documents}
    return {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {
            "hits": [
                {"_id": identifier, "_score": score, "_source": by_id[identifier]}
                for identifier, score in rows
            ]
        },
    }


def setup_replies(replies, *bodies):
    replies[:] = [(200, body, {}) for body in bodies]


def test_comparison_cli_known_rankings_and_statistics(search_server, tmp_path):
    url, requests, replies = search_server
    lexical1 = response(("j14", 5), ("j01", 4))
    parsed1 = response(("j01", 9), ("j16", 7))
    lexical2 = response(("j03", 5), ("j02", 4))
    parsed2 = response(("j02", 9), ("j05", 7))
    setup_replies(
        replies,
        lexical1,
        parsed1,
        lexical1,
        parsed1,
        lexical2,
        parsed2,
        lexical2,
        parsed2,
    )
    result = cli(
        url,
        tmp_path,
        comparison=True,
        overrides=(
            "~experiment.pipelines",
            "+experiment.pipelines={lexical:lexical,parsed:parsed,fused:fused}",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    artifact = json.loads((tmp_path / "comparison.json").read_text())
    assert len(requests) == 8
    assert all(
        request["path"] == "/proxy/fixture-vacancies/_search" for request in requests
    )
    assert all(request["body"]["size"] == 100 for request in requests)
    for index, request in enumerate(requests):
        must = request["body"]["query"]["bool"]["must"]
        literal = must[-1]["dis_max"]["queries"][0]["multi_match"]
        expected_query = "python backend developer" if index < 4 else "java developer"
        assert literal["query"] == expected_query
        assert literal["fields"] == ["title^4", "description^1"]
        assert literal["minimum_should_match"] == "2<75%"
        assert len(must) == (1 if index % 2 == 0 else 2)
    assert requests[0]["body"] != requests[1]["body"]
    assert requests[0]["body"] == requests[2]["body"]
    assert requests[1]["body"] == requests[3]["body"]
    assert requests[4]["body"] == requests[6]["body"]
    assert requests[5]["body"] == requests[7]["body"]
    for name, expected in (("lexical", 1 / math.log2(3)), ("parsed", 1), ("fused", 1)):
        summary = artifact["summaries"][name]["ndcg"]
        assert summary["count"] == 2
        assert summary["mean"] == pytest.approx(expected)
        assert summary["stddev"] == 0
    fused = [row for row in artifact["results"] if row["pipeline"] == "fused"]
    assert [[item["id"] for item in row["items"]] for row in fused] == [
        ["j01", "j14"],
        ["j02", "j03"],
    ]
    assert fused[0]["items"][0]["fields"] == parsed1["hits"]["hits"][0]["_source"]
    assert fused[0]["items"][0]["score"] == pytest.approx(1 / 61 + 1 / 62)


def test_cli_fusion_keeps_full_candidate_depth(search_server, tmp_path):
    url, requests, replies = search_server
    setup_replies(
        replies,
        response(("j14", 6), ("j16", 5), ("j01", 4)),
        response(("j02", 9), ("j05", 8), ("j01", 7)),
    )
    result = cli(
        url,
        tmp_path,
        "python backend developer\n",
        overrides=(
            "experiment.pipeline=fused",
            "jobboard.candidate_depth=3",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(requests) == 2
    assert all(request["body"]["size"] == 3 for request in requests)
    items = json.loads((tmp_path / "searches.json").read_text())["searches"][0]["items"]
    assert [item["id"] for item in items] == ["j01", "j14"]
    assert [item["score"] for item in items] == pytest.approx([2 / 63, 1 / 61])


@pytest.mark.parametrize("failure", ["http", "timeout", "malformed", "unjudged"])
def test_comparison_failure_preserves_previous_retrieval(
    search_server, tmp_path, failure
):
    url, requests, replies = search_server
    setup_replies(replies, response(("j01", 9)))
    retrieval = tmp_path / "retrieval"
    result = cli(url, retrieval, "python backend developer\n")
    assert result.returncode == 0, result.stdout + result.stderr
    saved = (retrieval / "searches.json").read_bytes()
    requests.clear()
    broken = response(("j02", 9))
    status = 200
    if failure == "http":
        status = 500
    elif failure == "timeout":
        broken["timed_out"] = True
    elif failure == "malformed":
        broken["hits"]["hits"][0].pop("_source")
    else:
        broken["hits"]["hits"][0]["_id"] = "outside-fixture"
    replies[:] = [(200, response(("j01", 9)), {}), (status, broken, {})]
    directory = tmp_path / "comparison"
    result = cli(
        url,
        directory,
        comparison=True,
        overrides=(
            "~experiment.pipelines",
            "+experiment.pipelines={parsed:parsed}",
        ),
    )
    assert result.returncode == 1
    assert len(requests) == 2
    assert not (directory / "comparison.json").exists()
    assert (retrieval / "searches.json").read_bytes() == saved


def experiment_for(url, tmp_path, *overrides):
    with initialize_config_dir(config_dir=str(ROOT / "configs"), version_base="1.3"):
        cfg = compose(
            config_name="main",
            overrides=[
                *SELECT,
                f"connection.url={url}",
                f"experiment.exporters.0.path={tmp_path}/searches.json",
                *overrides,
            ],
        )
    return instantiate(cfg.experiment, _convert_="all")


@pytest.mark.parametrize(
    "condition",
    [
        UserPropertyFilter("city", "missing"),
        TermFilter("undeclared", "x"),
        QueryFilter("x" * 513),
    ],
)
def test_preflight_failure_does_not_mutate_or_call_http(
    search_server, tmp_path, condition
):
    url, requests, _ = search_server
    experiment = experiment_for(url, tmp_path)
    filters = (
        [condition]
        if isinstance(condition, QueryFilter)
        else [QueryFilter("python"), condition]
    )
    items = SERP([Vacancy("prior", {"title": "kept"})])
    data = PipelineData(items=items, filters=filters, context={"sentinel": [1]})
    with pytest.raises(ValueError):
        experiment.pipeline.transform(data, registry=experiment.registry)
    assert requests == []
    assert data.items is items
    assert data.context == {"sentinel": [1]}
    assert data.filters == tuple(filters)
    assert data.branches == {}


def test_explicit_filters_reach_both_fused_leaves(search_server, tmp_path):
    url, requests, replies = search_server
    setup_replies(replies, response(("j01", 9)))
    experiment = experiment_for(url, tmp_path, "experiment.pipeline=fused")
    conditions = [
        QueryFilter("python"),
        TermFilter("remote", True),
        UserPropertyFilter("city", "selected_city"),
    ]
    data = PipelineData(filters=conditions, context={"selected_city": "moscow"})
    result = experiment.pipeline.transform(data, registry=experiment.registry)
    assert len(requests) == 2
    for request in requests:
        clauses = request["body"]["query"]["bool"]["filter"]
        assert {"term": {"remote": True}} in clauses
        assert {"term": {"city": "moscow"}} in clauses
    assert result.filters == tuple(conditions)
    assert result.context == {"selected_city": "moscow"}
    assert result.branches == {}


def test_second_branch_failure_leaves_payload_unpublished(search_server, tmp_path):
    url, requests, replies = search_server
    replies[:] = [(200, response(("j01", 9)), {}), (500, {}, {})]
    experiment = experiment_for(url, tmp_path, "experiment.pipeline=fused")
    items = SERP([Vacancy("prior")])
    condition = QueryFilter("python")
    data = PipelineData(items=items, filters=[condition])
    with pytest.raises(RuntimeError, match="HTTP failure"):
        experiment.pipeline.transform(data, registry=experiment.registry)
    assert len(requests) == 2
    assert data.items is items
    assert data.branches == {}
    assert data.filters == (condition,)


def test_baseline_keeps_legacy_body(search_server, tmp_path):
    url, requests, replies = search_server
    setup_replies(replies, response(("j01", 9)))
    result = cli(
        url,
        tmp_path,
        "Python backend developer\n",
        overrides=("experiment.pipeline=baseline",),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert requests[0]["body"] == {
        "size": 100,
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "Python backend developer",
                            "fields": ["title", "description"],
                        }
                    }
                ]
            }
        },
    }


def test_opt_in_fuzzy_cli_and_successful_empty_result(search_server, tmp_path):
    url, requests, replies = search_server
    setup_replies(replies, response())
    result = cli(
        url,
        tmp_path,
        "разработчки python\n",
        overrides=(
            "jobboard/lexical/graph@jobboard.registry=fuzzy",
            "experiment.pipeline=fuzzy",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(requests) == 1
    body = requests[0]["body"]
    assert '"fuzziness": 1' in json.dumps(body)
    assert '"max_expansions": 25' in json.dumps(body)
    # Fuzzy retrieval belongs inside required lexical evidence.
    assert '"fuzziness"' in json.dumps(body["query"]["bool"]["must"])
    artifact = json.loads((tmp_path / "searches.json").read_text())
    assert artifact == {"searches": [{"query": "разработчки python", "items": []}]}


def test_basic_one_field_cli_without_legacy_search_config(search_server, tmp_path):
    url, requests, replies = search_server
    setup_replies(replies, response())
    result = cli(
        url,
        tmp_path,
        "unknown profession\n",
        overrides=(
            "jobboard/lexical/source@jobboard.source=basic",
            "jobboard.source.index=only-text",
            "jobboard.source.fields=[content]",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert requests[0]["path"] == "/proxy/only-text/_search"
    body = requests[0]["body"]["query"]["bool"]
    assert "should" not in body
    assert body["must"][0]["dis_max"]["queries"][0]["multi_match"]["fields"] == [
        "content^1"
    ]

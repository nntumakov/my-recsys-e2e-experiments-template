import json
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate
from omegaconf import open_dict

from src.core.registry import BasicTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.transformers.score_sort import ScoreSortTransformer
from src.domain.collection.serp import SERP
from src.domain.document.abstract import BaseDocument
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter
from src.experiments.examples.ranking import compare

ROOT = Path(__file__).resolve().parents[1]


def test_score_sort_preserves_ties_documents_collection_type_and_payload():
    documents: list[BaseDocument] = [
        Vacancy("a", {"nested": [1]}, score=2),
        Vacancy("b", score=-1),
        Vacancy("c", score=2),
        Vacancy("d", score=3),
    ]
    original = SERP(documents)
    data = PipelineData(
        items=original, context={"city": "Москва"}, filters=[QueryFilter("склад")]
    )
    data.branches["untouched"] = PipelineData()
    original_branches = dict(data.branches)
    before = deepcopy(data)
    registry = BasicTransformerRegistry({})
    transformer = ScoreSortTransformer()
    assert transformer.transform(data, registry=registry) is data
    assert isinstance(data.items, SERP) and data.items is not original
    assert [item.id for item in data.items.items] == ["d", "a", "c", "b"]
    assert original.items == documents
    assert all(data.items.items[i] is documents[j] for i, j in enumerate([3, 0, 2, 1]))
    assert data.context == before.context and data.filters == before.filters
    assert data.branches == original_branches
    assert documents[0].fields == {"nested": [1]} and documents[0].score == 2
    transformer.transform(data, registry=registry)
    assert [item.id for item in data.items.items] == ["d", "a", "c", "b"]
    assert not transformer.transform(PipelineData(), registry=registry).items.items


@pytest.mark.parametrize(
    "invalid", [None, True, float("nan"), float("inf"), -float("inf"), "bad"]
)
def test_invalid_last_score_does_not_reorder_or_replace_collection(invalid):
    original = SERP(
        [
            Vacancy("a", score=1),
            Vacancy("b", score=2),
            Vacancy("bad", score=cast(Any, invalid)),
        ]
    )
    data = PipelineData(items=original)
    with pytest.raises(ValueError, match="finite numeric scores"):
        ScoreSortTransformer().transform(data, registry=BasicTransformerRegistry({}))
    assert data.items is original
    assert [item.id for item in original.items] == ["a", "b", "bad"]
    assert [item.score for item in original.items[:2]] == [1, 2]
    assert original.items[-1].score is invalid


def test_python_example_is_repeatable_without_network_and_uses_full_rrf_depth(
    monkeypatch,
):
    def unexpected_network(*args, **kwargs):
        pytest.fail("The offline example attempted a network call")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    artifact = compare()
    result = cast(Any, artifact.to_dict())
    assert compare().to_dict() == result
    assert len(result["results"]) == 9
    warehouse = {
        row["pipeline"]: row for row in result["results"] if row["case"] == "warehouse"
    }
    assert [hit["id"] for hit in warehouse["fused"]["items"]] == [
        "warehouse-trainee",
        "packer",
        "warehouse-night",
    ]
    assert warehouse["fused"]["items"][0]["score"] == pytest.approx(2 / 62)
    assert warehouse["fused"]["items"][1]["score"] == pytest.approx(1 / 64 + 1 / 61)
    assert warehouse["fused"]["metrics"]["ndcg"]["value"] == pytest.approx(0.7984848581)
    assert result["summaries"]["text"]["ndcg"]["mean"] == pytest.approx(0.90282827898)
    assert (
        result["summaries"]["fused"]["ndcg"]["mean"]
        < result["summaries"]["text"]["ndcg"]["mean"]
    )
    assert all(row["items"][0]["fields"]["description"] for row in result["results"])
    result["results"][0]["items"][0]["fields"].clear()
    assert cast(Any, artifact.to_dict())["results"][0]["items"][0]["fields"]
    adjusted = cast(Any, compare(["ranking.k=2", "ranking.rank_constant=0"]).to_dict())
    assert all(
        len(row["items"]) == 2 and row["metrics"]["ndcg"]["k"] == 2
        for row in adjusted["results"]
    )
    assert (
        adjusted["results"][2]["items"][0]["score"]
        != warehouse["fused"]["items"][0]["score"]
    )


def test_documented_cli_matches_python_and_preserves_existing_export(tmp_path):
    command = [
        sys.executable,
        "-m",
        "src.run",
        "--config-name",
        "ranking",
        f"hydra.run.dir={tmp_path}",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    path = tmp_path / "comparison.json"
    content = path.read_bytes()
    assert json.loads(content) == compare().to_dict()
    repeated = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert repeated.returncode == 1
    assert path.read_bytes() == content


def test_missing_judgment_prevents_example_export(tmp_path):
    path = tmp_path / "comparison.json"
    with initialize_config_dir(config_dir=str(ROOT / "configs"), version_base="1.3"):
        cfg = compose(config_name="ranking")
        cfg.experiment.exporters[0].path = str(path)
        with open_dict(cfg.ranking.cases[0].relevance):
            del cfg.ranking.cases[0].relevance["warehouse-trainee"]
        experiment = instantiate(cfg.experiment, _convert_="all")
    with pytest.raises(ValueError, match="Missing relevance"):
        experiment._wrapped_run()
    assert not path.exists()

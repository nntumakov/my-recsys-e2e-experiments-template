import json
from math import log2
from typing import Any, cast

import pytest
from hydra.utils import instantiate
from omegaconf import OmegaConf

from src.components.evaluation.metrics.listwise.ndcg import ListWiseNDCG
from src.components.evaluation.stats.summary import summarize
from src.core.registry import BasicTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.transformers.top_k import TopKTransformer
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.experiments.testing.compare import CompareExperiment, ComparisonCase


def test_ndcg_uses_all_judgments_and_not_search_score():
    serp = SERP([Vacancy("b", score=999), Vacancy("a", score=-1)])
    report = ListWiseNDCG(2).eval(serp, {"a": 3, "b": 1, "missing": 2})
    assert report.dcg == pytest.approx(1 + 7 / log2(3))
    assert report.idcg == pytest.approx(7 + 3 / log2(3))
    assert report.value == pytest.approx(report.dcg / report.idcg)
    assert report.to_dict()["k"] == 2
    assert ListWiseNDCG(1).eval(serp, {"b": 0}).value == 0
    assert ListWiseNDCG(2).eval(SERP(), {}).value == 0


@pytest.mark.parametrize(
    "grades",
    [{}, {"a": -1}, {"a": float("nan")}, {"a": float("inf")}, {"a": True}, {"a": 1024}],
)
def test_ndcg_rejects_missing_invalid_or_overflowing_grades(grades):
    with pytest.raises(ValueError):
        ListWiseNDCG(1).eval(SERP([Vacancy("a")]), grades)


def test_duplicate_ids_and_invalid_cutoff():
    with pytest.raises(ValueError):
        ListWiseNDCG(1).eval(SERP([Vacancy("a"), Vacancy("a")]), {"a": 1})
    for cutoff in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            ListWiseNDCG(cast(Any, cutoff))


def test_statistics():
    assert summarize([]).to_dict() == {
        "count": 0,
        "mean": None,
        "stddev": None,
        "minimum": None,
        "maximum": None,
    }
    summary = summarize([1, 3])
    assert summary.mean == 2 and summary.stddev == 1
    assert summary.minimum == 1 and summary.maximum == 3
    with pytest.raises(ValueError):
        summarize([float("nan")])


def test_small_positive_relevance_remains_distinct_from_zero():
    report = ListWiseNDCG(1).eval(SERP([Vacancy("a")]), {"a": 1e-20})
    assert report.dcg > 0 and report.idcg > 0
    assert report.value == 1


def test_statistics_avoid_intermediate_overflow():
    identical = summarize([1e308, 1e308])
    assert identical.mean == 1e308 and identical.stddev == 0
    opposite = summarize([-1e308, 1e308])
    assert opposite.mean == 0 and opposite.stddev == 1e308


def test_unrepresentable_relevance_is_rejected_as_value_error():
    with pytest.raises(ValueError):
        ListWiseNDCG(1).eval(SERP([Vacancy("a")]), {"a": 10**400})


def test_comparison_isolation_snapshots_and_repeat_run():
    original = PipelineData(items=SERP([Vacancy("a", {"nested": [1]}), Vacancy("b")]))
    experiment = CompareExperiment(
        BasicTransformerRegistry(
            {"one": TopKTransformer(1), "two": TopKTransformer(2)}
        ),
        {"short": "one", "full": "two"},
        [ComparisonCase("query", original, {"a": 1, "b": 1})],
        {"ndcg": ListWiseNDCG(2)},
    )
    artifact = experiment.run()
    result = cast(Any, artifact.to_dict())
    assert [len(row["items"]) for row in result["results"]] == [1, 2]
    assert result["summaries"]["full"]["ndcg"]["mean"] == 1
    assert experiment.run().to_dict() == result
    original.items.items.clear()
    result["results"][0]["items"][0]["fields"].clear()
    assert cast(Any, artifact.to_dict())["results"][0]["items"][0]["fields"] == {
        "nested": [1]
    }
    assert len(cast(Any, experiment.run().to_dict())["results"][1]["items"]) == 2


def test_local_hydra_comparison_exports_complete_artifact(tmp_path):
    config = OmegaConf.load(
        "configs/experiments/vacancy-ranking/local/001-comparison.yaml"
    )
    config.exporters[0].path = str(tmp_path / "comparison.json")
    experiment = instantiate(config, _convert_="all")
    experiment._wrapped_run()
    result = json.loads((tmp_path / "comparison.json").read_text())
    assert len(result["results"]) == 2
    assert result["results"][1]["items"][0]["fields"] == {"quality": 3}
    assert result["summaries"]["fused"]["ndcg@2"]["count"] == 1


def test_comparison_validation_and_failure_prevent_export(tmp_path):
    from src.components.reporting.exporters.json import JSONExporter

    registry = BasicTransformerRegistry({"top": TopKTransformer(1)})
    case = ComparisonCase("q", PipelineData(items=SERP([Vacancy("a")])), {})
    metrics = {"ndcg": ListWiseNDCG(1)}
    for pipelines, cases, selected_metrics, error in [
        ({}, [case], metrics, ValueError),
        ({"a": "top"}, [], metrics, ValueError),
        ({"a": "top"}, [case], {}, ValueError),
        ({"a": "top"}, [case, case], metrics, ValueError),
        ({"a": "missing"}, [case], metrics, KeyError),
    ]:
        with pytest.raises(error):
            CompareExperiment(registry, pipelines, cases, selected_metrics)
    path = tmp_path / "result.json"
    experiment = CompareExperiment(
        registry, {"a": "top"}, [case], metrics, [JSONExporter(path)]
    )
    with pytest.raises(ValueError, match="Missing relevance"):
        experiment._wrapped_run()
    assert not path.exists()

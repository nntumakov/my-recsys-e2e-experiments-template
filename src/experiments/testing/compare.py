from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass

from src.components.evaluation.metrics.listwise.abstract import BaseListWiseMetric
from src.components.evaluation.stats.summary import summarize
from src.components.reporting.artifacts.comparison import ComparisonArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.data import PipelineData
from src.domain import JSONValue
from src.domain.collection.serp import SERP
from src.experiments.abstract import BaseExperiment


@dataclass(frozen=True)
class ComparisonCase:
    key: str
    data: PipelineData
    relevance: Mapping[str, float]


class CompareExperiment(BaseExperiment):
    def __init__(
        self,
        registry: BaseTransformerRegistry,
        pipelines: Mapping[str, str],
        cases: Sequence[ComparisonCase],
        metrics: Mapping[str, BaseListWiseMetric],
        exporters: Sequence[BaseExporter] = (),
    ) -> None:
        super().__init__(exporters)
        if not pipelines or not cases or not metrics:
            raise ValueError("Comparison requires pipelines, cases and metrics")
        keys = [case.key for case in cases]
        if any(not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("Comparison case keys must be nonempty and unique")
        if any(not name for name in (*pipelines, *metrics)):
            raise ValueError("Comparison names must be nonempty")
        self.registry = registry
        self.pipelines = {name: registry[target] for name, target in pipelines.items()}
        self.cases = tuple(
            ComparisonCase(case.key, case.data.copy(deep=True), dict(case.relevance))
            for case in cases
        )
        self.metrics = dict(metrics)

    def run(self) -> ComparisonArtifact:
        values: dict[str, dict[str, list[float]]] = {
            name: {metric: [] for metric in self.metrics} for name in self.pipelines
        }
        results: list[Mapping[str, JSONValue]] = []
        for case in self.cases:
            for name, pipeline in self.pipelines.items():
                data = pipeline.transform(
                    case.data.copy(deep=True), registry=self.registry
                )
                if data.branches or not isinstance(data.items, SERP):
                    raise ValueError("Comparison pipeline must return a merged SERP")
                reports: dict[str, JSONValue] = {}
                for metric_name, metric in self.metrics.items():
                    report = metric.eval(data.items, case.relevance)
                    reports[metric_name] = report.to_dict()
                    values[name][metric_name].append(report.value)
                results.append(
                    deepcopy(
                        {
                            "case": case.key,
                            "pipeline": name,
                            "items": [
                                {
                                    "id": item.id,
                                    "fields": item.fields,
                                    "score": item.score,
                                }
                                for item in data.items.items
                            ],
                            "metrics": reports,
                        }
                    )
                )
        summaries: dict[str, JSONValue] = {
            name: {
                metric: summarize(samples).to_dict()
                for metric, samples in metrics.items()
            }
            for name, metrics in values.items()
        }
        return ComparisonArtifact(results, summaries)

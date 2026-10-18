"""Preview query plans and compare named search pipelines."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from src.components.reporting.artifacts.research import ResearchArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.jobboard.research import (
    ResearchSearchTransformer,
    SearchPlan,
)
from src.domain import JSONValue
from src.domain.request.abstract import BaseFilter
from src.domain.request.filters import QueryFilter
from src.experiments.abstract import BaseExperiment
from src.experiments.batch.search import read_queries
from src.experiments.direct.search import SearchExperiment
from src.integrations.opensearch.evidence import ObservedSERP


class ResearchExperiment(BaseExperiment):
    def __init__(
        self,
        registry: BaseTransformerRegistry,
        variants: Sequence[str],
        now_ms: int,
        source_id: str,
        exporters: Sequence[BaseExporter] = (),
        *,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
        text: str | None = None,
        input_path: str | None = None,
    ) -> None:
        super().__init__(exporters)
        if (
            not variants
            or len(set(variants)) != len(variants)
            or (text is not None and input_path is not None)
        ):
            raise ValueError("Select unique variants and either text or CSV")
        if type(now_ms) is not int or now_ms < 0 or not source_id:
            raise ValueError("Research requires source identity and fixed time")
        self.registry, self.variants = registry, tuple(variants)
        self.now_ms, self.source_id, self.text, self.input_path = (
            now_ms,
            source_id,
            text,
            input_path,
        )
        self.searches: dict[str, SearchExperiment] = {}
        self.title_field = "title"
        for variant in variants:
            leaf = registry[variant]
            if not isinstance(leaf, ResearchSearchTransformer):
                raise ValueError("Research variants require observed search leaves")
            if leaf.now_ms != now_ms or leaf.source.index != source_id:
                raise ValueError("Research variants must share session time and source")
            self.title_field = leaf.source.title_field or "title"
            self.searches[variant] = SearchExperiment(
                registry, variant, context=context, filters=filters
            )
        self._context, self._filters = deepcopy(dict(context or {})), tuple(filters)

    def preview(self, text: str) -> dict[str, SearchPlan]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Preview needs a nonempty query")
        data = PipelineData(
            context=self._context, filters=(*self._filters, QueryFilter(text.strip()))
        )
        plans = {}
        for name in self.variants:
            leaf = self.registry[name]
            assert isinstance(leaf, ResearchSearchTransformer)
            plans[name] = leaf.plan(data.copy(deep=True))
        return plans

    def compare(self, text: str, query_id: str = "manual") -> ResearchArtifact:
        return self.compare_queries([(query_id, text)])

    def compare_queries(self, queries: Sequence[tuple[str, str]]) -> ResearchArtifact:
        if (
            not queries
            or any(not k.strip() or not t.strip() for k, t in queries)
            or len({k.strip() for k, _ in queries}) != len(queries)
        ):
            raise ValueError("Research queries need unique nonempty ids and texts")
        # Complete local compilation before the first external side effect.
        for _, text in queries:
            self.preview(text)
        results = []
        for key, text in queries:
            for variant in self.variants:
                serp = self.searches[variant].search(text)
                if not isinstance(serp, ObservedSERP):
                    raise ValueError("Research search lost its evidence")
                results.append((key.strip(), text.strip(), variant, serp))
        return ResearchArtifact(
            self.now_ms, self.source_id, self.variants, results, self.title_field
        )

    def run(self) -> ResearchArtifact:
        if self.input_path is not None:
            return self.compare_queries(read_queries(Path(self.input_path)))
        if self.text is not None:
            return self.compare(self.text)
        queries: list[tuple[str, str]] = []
        while True:
            try:
                text = input("Исследование> ").strip()
            except EOFError:
                break
            if text:
                queries.append((str(len(queries) + 1), text))
        return self.compare_queries(queries)

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy

from src.components.reporting.artifacts.search import SearchArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.data import PipelineData
from src.domain import JSONValue
from src.domain.collection.serp import SERP
from src.domain.request.abstract import BaseFilter
from src.domain.request.filters import QueryFilter
from src.experiments.abstract import BaseExperiment


class SearchExperiment(BaseExperiment):
    def __init__(
        self,
        registry: BaseTransformerRegistry,
        pipeline: str,
        exporters: Sequence[BaseExporter] = (),
        *,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
    ) -> None:
        super().__init__(exporters)
        self.registry, self.pipeline = registry, registry[pipeline]
        if any(isinstance(value, QueryFilter) for value in filters):
            raise ValueError("Static experiment filters cannot contain QueryFilter")
        self._context, self._filters = deepcopy(dict(context or {})), tuple(filters)

    def search(
        self,
        text: str,
        *,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
    ) -> SERP:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Search requires non-empty text")
        if any(isinstance(value, QueryFilter) for value in filters):
            raise ValueError("Additional filters cannot contain QueryFilter")
        data = PipelineData(
            context=deepcopy({**self._context, **(context or {})}),
            filters=(*self._filters, *filters, QueryFilter(text.strip())),
        )
        result = self.pipeline.transform(data, registry=self.registry)
        if result.branches or not isinstance(result.items, SERP):
            raise ValueError("Search pipeline must return a merged SERP")
        return deepcopy(result.items)

    def run(self) -> SearchArtifact:
        searches: list[dict[str, JSONValue]] = []
        while True:
            try:
                text = input("search> ").strip()
            except EOFError:
                print()
                return SearchArtifact(searches)
            if not text:
                continue
            result = self.search(text)
            record: dict[str, JSONValue] = {
                "query": text,
                "items": [
                    {"id": item.id, "score": item.score, "fields": item.fields}
                    for item in result.items
                ],
            }
            print(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False))
            searches.append(deepcopy(record))

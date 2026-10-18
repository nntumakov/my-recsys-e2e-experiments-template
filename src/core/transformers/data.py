from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import copy, deepcopy

from src.domain import JSONValue
from src.domain.collection.abstract import BaseDocumentCollections
from src.domain.request.abstract import BaseFilter


class PipelineData:
    def __init__(
        self,
        *,
        context: Mapping[str, JSONValue] | None = None,
        items: BaseDocumentCollections | None = None,
        filters: Sequence[BaseFilter] = (),
        branches: Mapping[str, PipelineData] | None = None,
    ) -> None:
        self.context = dict(context or {})
        self.items = items if items is not None else BaseDocumentCollections()
        self._filters = tuple(filters)
        self.branches = dict(branches or {})

    @property
    def filters(self) -> tuple[BaseFilter, ...]:
        return self._filters

    def add_filter(self, value: BaseFilter) -> None:
        self._filters += (value,)

    def copy(self, *, deep: bool = False) -> PipelineData:
        if deep:
            context, items = deepcopy((self.context, self.items))
        else:
            context, items = self.context, copy(self.items)
            items.items = list(self.items.items)
        branches = (
            {name: branch.copy(deep=True) for name, branch in self.branches.items()}
            if deep
            else self.branches
        )
        return PipelineData(
            context=context, items=items, filters=self.filters, branches=branches
        )

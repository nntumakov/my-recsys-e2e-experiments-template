from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData

if TYPE_CHECKING:
    from src.core.registry import BaseTransformerRegistry


class BranchTransformer(BaseTransformer):
    def __init__(self, branches: Mapping[str, str]) -> None:
        if not branches or any(
            not name or not target for name, target in branches.items()
        ):
            raise ValueError("Branches require nonempty names and targets")
        self.branches = dict(branches)

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        if data.branches:
            raise ValueError("Merge existing branches before branching again")
        targets = [(name, registry[target]) for name, target in self.branches.items()]
        inputs = [data.copy(deep=True) for _ in targets]
        results: dict[str, PipelineData] = {}
        for (name, transformer), branch in zip(targets, inputs, strict=True):
            result = transformer.transform(branch, registry=registry)
            if result.branches:
                raise ValueError("Nested branches must be merged before returning")
            results[name] = result
        data.branches = results
        return data

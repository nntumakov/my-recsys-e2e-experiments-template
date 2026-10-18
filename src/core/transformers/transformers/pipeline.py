from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData

if TYPE_CHECKING:
    from src.core.registry import BaseTransformerRegistry


class PipelineTransformer(BaseTransformer):
    def __init__(self, steps: Sequence[str]) -> None:
        self.steps = tuple(steps)

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        for name in self.steps:
            data = registry[name].transform(data, registry=registry)
        return data

from copy import copy

from src.core.registry import BaseTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData


class TopKTransformer(BaseTransformer):
    def __init__(self, k: int) -> None:
        if type(k) is not int or k < 0:
            raise ValueError("Top-k must be a nonnegative integer")
        self.k = k

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        items = copy(data.items)
        items.items = items.items[: self.k]
        data.items = items
        return data

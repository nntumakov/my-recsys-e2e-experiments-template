from copy import copy
from math import isfinite

from src.core.registry import BaseTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData


class ScoreSortTransformer(BaseTransformer):
    """Order finite scores descending, preserving input order for ties."""

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        scored = []
        for item in data.items.items:
            score = item.score
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not isfinite(score)
            ):
                raise ValueError("Sorting requires finite numeric scores")
            scored.append((score, item))
        items = copy(data.items)
        items.items = [
            item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)
        ]
        data.items = items
        return data

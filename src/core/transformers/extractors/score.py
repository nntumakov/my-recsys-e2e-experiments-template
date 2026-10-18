from math import isfinite

from src.core.registry import BaseTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData


class FieldScoreExtractor(BaseTransformer):
    def __init__(self, field: str) -> None:
        if not field:
            raise ValueError("Score field must be nonempty")
        self.field = field

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        scores: list[float] = []
        for item in data.items.items:
            value = item.fields.get(self.field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Missing or nonnumeric score field")
            score = float(value)
            if not isfinite(score):
                raise ValueError("Score field must be finite")
            scores.append(score)
        for item, score in zip(data.items.items, scores, strict=True):
            item.score = score
        return data

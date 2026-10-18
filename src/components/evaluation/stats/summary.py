from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from statistics import mean, pstdev

from src.domain import JSONValue


@dataclass(frozen=True)
class MetricSummary:
    count: int
    mean: float | None
    stddev: float | None
    minimum: float | None
    maximum: float | None

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "count": self.count,
            "mean": self.mean,
            "stddev": self.stddev,
            "minimum": self.minimum,
            "maximum": self.maximum,
        }


def summarize(values: Sequence[float]) -> MetricSummary:
    if any(not isfinite(value) for value in values):
        raise ValueError("Statistics require finite values")
    if not values:
        return MetricSummary(0, None, None, None, None)
    return MetricSummary(
        len(values), mean(values), pstdev(values), min(values), max(values)
    )

from abc import ABC, abstractmethod
from collections.abc import Mapping

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.domain.collection.serp import SERP


class BaseListWiseMetricReport(BaseArtifact):
    value: float


class BaseListWiseMetric(ABC):
    @abstractmethod
    def eval(
        self, serp: SERP, relevance: Mapping[str, float]
    ) -> BaseListWiseMetricReport: ...

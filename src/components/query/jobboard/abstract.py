from abc import ABC, abstractmethod

from src.domain.request.jobboard import QueryHint


class BaseQueryStage(ABC):
    @abstractmethod
    def parse(self, normalized_text: str) -> tuple[QueryHint, ...]: ...

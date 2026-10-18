"""Search transformer with local planning and remote source validation."""

from abc import abstractmethod

from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.domain import JSONValue


class StudySearchTransformer(BaseTransformer):
    @abstractmethod
    def preview(self, data: PipelineData) -> dict[str, JSONValue]:
        """Validate and describe the same search plan, without external calls."""

    @abstractmethod
    def describe(self) -> dict[str, JSONValue]:
        """Describe variant, family, source and effective parameter identities."""

    def admit(self, data: PipelineData) -> list[dict[str, JSONValue]]:
        """Check remote prerequisites before a comparison starts its searches."""
        return []

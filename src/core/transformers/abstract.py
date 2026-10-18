from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.core.transformers.data import PipelineData

if TYPE_CHECKING:
    from src.core.registry import BaseTransformerRegistry


class BaseTransformer(ABC):
    @abstractmethod
    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData: ...

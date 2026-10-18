from abc import ABC, abstractmethod

from src.components.reporting.artifacts.abstract import BaseArtifact


class BaseExporter(ABC):
    @abstractmethod
    def export(self, artifact: BaseArtifact) -> None: ...

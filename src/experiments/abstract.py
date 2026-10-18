import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.components.reporting.exporters.abstract import BaseExporter


class BaseExperiment(ABC):
    def __init__(self, exporters: Sequence[BaseExporter]) -> None:
        self.exporters = tuple(exporters)

    @abstractmethod
    def run(self) -> BaseArtifact: ...

    def _wrapped_run(self) -> None:
        artifact = self.run()
        logging.getLogger(__name__).info("Saving experiment artifact")
        for exporter in self.exporters:
            exporter.export(artifact)

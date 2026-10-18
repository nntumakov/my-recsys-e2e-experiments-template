import csv
from io import StringIO
from pathlib import Path

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.components.reporting.artifacts.batch import BatchSearchArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.components.reporting.exporters.publication import publish_text


class SearchCSVExporter(BaseExporter):
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def export(self, artifact: BaseArtifact) -> None:
        if not isinstance(artifact, BatchSearchArtifact):
            raise TypeError("Search CSV export requires BatchSearchArtifact")
        stream = StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=artifact.columns)
        writer.writeheader()
        writer.writerows(artifact.to_rows())
        publish_text(self.path, stream.getvalue())

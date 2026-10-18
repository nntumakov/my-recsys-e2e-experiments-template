import json
from pathlib import Path

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.components.reporting.exporters.publication import publish_text


class JSONExporter(BaseExporter):
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def export(self, artifact: BaseArtifact) -> None:
        content = json.dumps(
            artifact.to_dict(), ensure_ascii=False, indent=2, allow_nan=False
        )
        publish_text(self.path, content + "\n")

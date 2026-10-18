import csv
from collections.abc import Sequence
from pathlib import Path

from src.components.reporting.artifacts.batch import BatchSearchArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.experiments.abstract import BaseExperiment
from src.experiments.direct.search import SearchExperiment


def read_queries(path: Path) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        columns = next(reader, [])
        if sorted(columns) != ["id", "text"]:
            raise ValueError("Query CSV requires exactly id,text columns")
        id_column, text_column = columns.index("id"), columns.index("text")
        for row in reader:
            if len(row) != 2:
                raise ValueError(f"Invalid query CSV row at line {reader.line_num}")
            key, text = row[id_column].strip(), row[text_column].strip()
            if not key or not text or key in seen:
                raise ValueError(f"Invalid query CSV values at line {reader.line_num}")
            seen.add(key)
            queries.append((key, text))
    if not queries:
        raise ValueError("Query CSV must contain at least one query")
    return queries


class BatchSearchExperiment(BaseExperiment):
    def __init__(
        self,
        search: SearchExperiment,
        input_path: str,
        exporters: Sequence[BaseExporter] = (),
    ) -> None:
        super().__init__(exporters)
        self.search = search
        self.input_path = Path(input_path)

    def run(self) -> BatchSearchArtifact:
        queries = read_queries(self.input_path)
        return BatchSearchArtifact(
            [(key, text, self.search.search(text)) for key, text in queries]
        )

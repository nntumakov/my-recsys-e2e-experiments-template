from collections.abc import Mapping, Sequence
from copy import deepcopy

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.domain import JSONValue


class ComparisonArtifact(BaseArtifact):
    def __init__(
        self,
        results: Sequence[Mapping[str, JSONValue]],
        summaries: Mapping[str, JSONValue],
    ) -> None:
        self._data: dict[str, JSONValue] = deepcopy(
            {
                "results": [dict(result) for result in results],
                "summaries": dict(summaries),
            }
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return deepcopy(self._data)

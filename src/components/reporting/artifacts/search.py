from collections.abc import Mapping, Sequence
from copy import deepcopy

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.domain import JSONValue


class SearchArtifact(BaseArtifact):
    def __init__(self, searches: Sequence[Mapping[str, JSONValue]]) -> None:
        self._searches: list[JSONValue] = deepcopy(
            [dict(record) for record in searches]
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {"searches": deepcopy(self._searches)}

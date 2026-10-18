from collections.abc import Mapping
from copy import deepcopy

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.domain import JSONValue


class VectorIndexArtifact(BaseArtifact):
    """Receipt of a verified remote generation, independent of live clients."""

    def __init__(self, record: Mapping[str, JSONValue]) -> None:
        self._record = deepcopy(dict(record))

    def to_dict(self) -> dict[str, JSONValue]:
        return deepcopy(self._record)

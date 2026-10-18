from abc import ABC, abstractmethod

from src.domain import JSONValue


class BaseArtifact(ABC):
    @abstractmethod
    def to_dict(self) -> dict[str, JSONValue]: ...

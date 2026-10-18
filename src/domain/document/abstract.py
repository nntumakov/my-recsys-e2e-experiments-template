from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.domain import JSONValue


@dataclass
class BaseDocument:
    id: str
    fields: dict[str, JSONValue] = field(default_factory=dict)
    score: float | None = None


class BaseExternalDocument(ABC):
    @abstractmethod
    def to_document(self) -> BaseDocument: ...

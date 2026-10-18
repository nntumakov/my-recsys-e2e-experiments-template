from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.domain.document.abstract import BaseDocument


@dataclass
class BaseDocumentCollections:
    items: list[BaseDocument] = field(default_factory=list)


class BaseExternalDocumentCollections(ABC):
    @abstractmethod
    def to_document_collections(self) -> BaseDocumentCollections: ...

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import TYPE_CHECKING, cast
from uuid import UUID

from src.domain import JSONValue
from src.domain.collection.abstract import BaseExternalDocumentCollections
from src.domain.collection.serp import SERP
from src.domain.document.abstract import BaseDocument, BaseExternalDocument
from src.domain.document.vacancy import Vacancy

if TYPE_CHECKING:
    from src.integrations.qdrant.source import QdrantSource


class VacancyQdrantDocument(BaseExternalDocument):
    def __init__(self, point: Mapping[str, JSONValue], source: QdrantSource) -> None:
        self.point, self.source = point, source

    def to_document(self) -> Vacancy:
        payload, score = self.point.get("payload"), self.point.get("score")
        version = self.point.get("version")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"document_id", "fields"}
            or not isinstance(payload["document_id"], str)
            or not payload["document_id"].strip()
            or not isinstance(payload["fields"], dict)
            or type(version) is not int
            or version < 0
            or type(score) not in (int, float)
            or not isfinite(cast(float, score))
        ):
            raise ValueError("Invalid Qdrant vacancy hit")
        self.source.validate_point_id(self.point.get("id"), payload["document_id"])
        self.source.validate_fields(payload["fields"])
        return Vacancy(
            payload["document_id"], deepcopy(payload["fields"]), cast(float, score)
        )


class QdrantResult(BaseExternalDocumentCollections):
    def __init__(self, response: Mapping[str, JSONValue], source: QdrantSource) -> None:
        self.response, self.source = response, source

    def to_document_collections(self) -> SERP:
        result = self.response.get("result")
        if (
            self.response.get("status") != "ok"
            or not isinstance(result, dict)
            or not isinstance(result.get("points"), list)
        ):
            raise ValueError("Malformed Qdrant query response")
        documents: list[BaseDocument] = []
        seen: set[str] = set()
        native_ids: set[UUID] = set()
        for point in cast(list[JSONValue], result["points"]):
            if not isinstance(point, dict):
                raise ValueError("Malformed Qdrant point")
            document = VacancyQdrantDocument(point, self.source).to_document()
            native_id = UUID(cast(str, point["id"]))
            if document.id in seen or native_id in native_ids:
                raise ValueError("Duplicate Qdrant document id")
            seen.add(document.id)
            native_ids.add(native_id)
            documents.append(document)
        return SERP(documents)

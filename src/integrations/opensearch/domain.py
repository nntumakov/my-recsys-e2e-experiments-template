from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import cast

from src.domain import JSONValue
from src.domain.document.abstract import BaseExternalDocument
from src.domain.document.vacancy import Vacancy


class BaseOpenSearchDocument(BaseExternalDocument):
    """OpenSearch hit converted by document-specific adapters."""

    def __init__(self, hit: Mapping[str, JSONValue]) -> None:
        self.hit = hit


class VacancyOpenSearchDocument(BaseOpenSearchDocument):
    def to_document(self) -> Vacancy:
        identifier = self.hit.get("_id")
        source = self.hit.get("_source")
        score = self.hit.get("_score")
        if not isinstance(identifier, str) or not isinstance(source, dict):
            raise ValueError("Invalid vacancy hit: expected _id and _source")
        if "_score" not in self.hit or (
            score is not None
            and (type(score) not in (int, float) or not isfinite(cast(float, score)))
        ):
            raise ValueError("Invalid vacancy score")
        return Vacancy(identifier, deepcopy(source), cast(float | None, score))

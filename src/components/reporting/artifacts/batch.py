import json
from collections.abc import Sequence
from copy import deepcopy

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.domain import JSONScalar, JSONValue
from src.domain.collection.serp import SERP


class BatchSearchArtifact(BaseArtifact):
    columns = ("query_id", "query", "rank", "document_id", "score", "fields")

    def __init__(self, searches: Sequence[tuple[str, str, SERP]]) -> None:
        self._searches = deepcopy(tuple(searches))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "searches": [
                {
                    "query_id": key,
                    "query": text,
                    "items": [
                        {
                            "id": item.id,
                            "score": item.score,
                            "fields": deepcopy(item.fields),
                        }
                        for item in serp.items
                    ],
                }
                for key, text, serp in self._searches
            ]
        }

    def to_rows(self) -> list[dict[str, JSONScalar]]:
        rows: list[dict[str, JSONScalar]] = []
        for key, text, serp in self._searches:
            for rank, item in enumerate(serp.items, start=1):
                rows.append(
                    {
                        "query_id": key,
                        "query": text,
                        "rank": rank,
                        "document_id": item.id,
                        "score": item.score,
                        "fields": json.dumps(
                            item.fields, ensure_ascii=False, allow_nan=False
                        ),
                    }
                )
            if not serp.items:
                rows.append(
                    {
                        "query_id": key,
                        "query": text,
                        "rank": None,
                        "document_id": None,
                        "score": None,
                        "fields": None,
                    }
                )
        return rows

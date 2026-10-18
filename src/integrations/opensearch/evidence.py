"""A search snapshot retains technical evidence without changing document fields."""

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
from typing import cast

from src.domain import JSONValue
from src.domain.collection.serp import SERP
from src.integrations.opensearch.client import OpenSearchResult


@dataclass
class ObservedSERP(SERP):
    request_body: dict[str, JSONValue] = field(default_factory=dict)
    understanding: dict[str, JSONValue] = field(default_factory=dict)
    sort_values: dict[str, list[JSONValue]] = field(default_factory=dict)
    client_elapsed_ms: float = 0.0
    server_took_ms: int | None = None
    metadata: dict[str, dict[str, JSONValue]] = field(default_factory=dict)


def observe(
    result: OpenSearchResult,
    body: dict[str, JSONValue],
    understanding: dict[str, JSONValue],
    elapsed_ms: float,
) -> ObservedSERP:
    serp = result.to_document_collections()
    took = result.response.get("took")
    if "took" in result.response and (type(took) is not int or took < 0):
        raise ValueError("Invalid server timing")
    if not isfinite(elapsed_ms) or elapsed_ms < 0:
        raise ValueError("Invalid client timing")
    hits = cast(dict[str, list[dict[str, JSONValue]]], result.response["hits"])["hits"]
    keys: dict[str, list[JSONValue]] = {}
    for item, hit in zip(serp.items, hits, strict=True):
        values = hit.get("sort", [])
        if not isinstance(values, list) or any(
            type(v) not in (str, int, float, type(None))
            or type(v) is float
            and not isfinite(v)
            for v in values
        ):
            raise ValueError("Malformed sort keys")
        if "sort" in body and (
            len(values) != 3
            or type(values[0]) not in (int, float)
            or type(values[1]) not in (int, float, type(None))
            or type(values[-1]) is not str
            or not values[-1]
        ):
            raise ValueError(
                "Tier sorting requires tier, score and a nonempty stable id"
            )
        if item.id in keys:
            raise ValueError("Research results require unique document ids")
        keys[item.id] = values
    if "sort" in body and len({v[-1] for v in keys.values()}) != len(keys):
        raise ValueError("Stable sort ids must be unique")
    return ObservedSERP(
        deepcopy(serp.items),
        deepcopy(body),
        deepcopy(understanding),
        deepcopy(keys),
        elapsed_ms,
        cast(int | None, took),
    )

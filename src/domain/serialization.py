"""Canonical JSON values and fingerprints for vector collection contracts."""

import json
from hashlib import sha256
from math import isfinite
from typing import cast

from src.domain import JSONValue


def validate_json(value: object) -> JSONValue:
    ancestors: set[int] = set()

    def visit(item: object) -> None:
        if item is None or type(item) is bool:
            return
        if type(item) is str:
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("JSON requires valid Unicode") from exc
        elif type(item) is int:
            if not -(2**63) <= item <= 2**64 - 1:
                raise ValueError("JSON integer exceeds the supported range")
        elif type(item) is float:
            if not isfinite(item):
                raise ValueError("JSON requires finite numbers")
        elif type(item) in (list, dict):
            if id(item) in ancestors:
                raise ValueError("JSON cannot contain cycles")
            ancestors.add(id(item))
            if isinstance(item, dict):
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError("JSON object keys must be strings")
                    visit(key)
                    visit(child)
            else:
                for child in cast(list[object], item):
                    visit(child)
            ancestors.remove(id(item))
        else:
            raise ValueError("Unsupported JSON value")

    visit(value)
    return cast(JSONValue, value)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        validate_json(value),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _unique_object(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def load_json(data: bytes) -> JSONValue:
    return validate_json(
        json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    )

"""Translate explicit study equality conditions into source-owned bindings."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from math import isfinite
from typing import cast

from src.domain import JSONScalar, JSONValue
from src.domain.request.abstract import BaseFilter
from src.domain.request.filters import TermFilter, UserPropertyFilter
from src.domain.serialization import validate_json


class StudyFilterBindings:
    def __init__(self, declarations: Mapping[str, Mapping[str, object]]) -> None:
        self._declarations = deepcopy(dict(declarations))
        for name, declaration in self._declarations.items():
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(declaration, Mapping)
                or set(declaration)
                - {"type", "opensearch_field", "opensearch_source_path", "qdrant_path"}
                or declaration.get("type")
                not in {"keyword", "boolean", "integer", "float"}
            ):
                raise ValueError("Invalid study filter declaration")
            field, path = (
                declaration.get("opensearch_field"),
                declaration.get("qdrant_path"),
            )
            if field is not None and (not isinstance(field, str) or not field.strip()):
                raise ValueError("Invalid OpenSearch study filter binding")
            for declared_path in (path, declaration.get("opensearch_source_path")):
                if declared_path is not None and (
                    not isinstance(declared_path, (list, tuple))
                    or not declared_path
                    or any(
                        not isinstance(p, str) or not p.strip() for p in declared_path
                    )
                ):
                    raise ValueError("Invalid study filter document path")
        validate_json(self.to_dict())

    def to_dict(self) -> dict[str, JSONValue]:
        return cast(dict[str, JSONValue], deepcopy(self._declarations))

    def _value(self, name: str, value: JSONValue) -> JSONScalar:
        if name not in self._declarations:
            raise ValueError(f"Unknown study filter: {name}")
        kind = self._declarations[name]["type"]
        valid = (
            kind == "keyword"
            and type(value) is str
            or kind == "boolean"
            and type(value) is bool
            or kind == "integer"
            and type(value) is int
            and -(2**63) <= value < 2**63
            or kind == "float"
            and type(value) in (int, float)
            and isfinite(cast(float, value))
            and (type(value) is float or int(float(cast(int, value))) == value)
        )
        if not valid:
            raise ValueError(f"Invalid value for study filter: {name}")
        validate_json(value)
        return cast(JSONScalar, value)

    def terms(self, values: Mapping[str, JSONValue]) -> tuple[TermFilter, ...]:
        return tuple(
            TermFilter(name, self._value(name, value)) for name, value in values.items()
        )

    def resolve(
        self, filters: Sequence[BaseFilter], context: Mapping[str, JSONValue]
    ) -> tuple[TermFilter, ...]:
        resolved = []
        for condition in filters:
            if isinstance(condition, TermFilter):
                name, value = condition.field, condition.value
            elif isinstance(condition, UserPropertyFilter):
                if condition.property not in context:
                    raise ValueError("Missing user property for study filter")
                name, value = condition.field, context[condition.property]
            else:
                raise ValueError("Study input accepts equality filters only")
            resolved.append(TermFilter(name, self._value(name, value)))
        return tuple(resolved)

    def describe(self, filters: Sequence[BaseFilter]) -> list[dict[str, JSONValue]]:
        """Validate static bindings while retaining call-time property references."""
        declarations: list[dict[str, JSONValue]] = []
        for condition in filters:
            if isinstance(condition, TermFilter):
                declarations.append(
                    {
                        "field": condition.field,
                        "value": self._value(condition.field, condition.value),
                    }
                )
            elif isinstance(condition, UserPropertyFilter):
                if condition.field not in self._declarations:
                    raise ValueError(f"Unknown study filter: {condition.field}")
                declarations.append(
                    {"field": condition.field, "property": condition.property}
                )
            else:
                raise ValueError("Study input accepts equality filters only")
        return declarations

    def physical(
        self, filters: Sequence[TermFilter], family: str
    ) -> tuple[TermFilter, ...]:
        result = []
        for condition in filters:
            declaration = self._declarations[condition.field]
            if family == "fulltext":
                name = declaration.get("opensearch_field")
                if not isinstance(name, str):
                    raise ValueError("Filter unavailable for OpenSearch")
            elif family == "embedding":
                if declaration.get("qdrant_path") is None:
                    raise ValueError("Filter unavailable for Qdrant")
                name = condition.field
            else:
                raise ValueError("Unknown study search family")
            result.append(TermFilter(name, condition.value))
        return tuple(result)


def opensearch_filter_fields(
    declarations: Mapping[str, Mapping[str, object]],
) -> dict[str, str]:
    bindings = StudyFilterBindings(declarations)
    return {
        cast(str, d["opensearch_field"]): cast(str, d["type"])
        for d in bindings._declarations.values()
        if d.get("opensearch_field") is not None
    }


def qdrant_filter_fields(
    declarations: Mapping[str, Mapping[str, object]],
) -> dict[str, JSONValue]:
    bindings = StudyFilterBindings(declarations)
    return {
        name: {
            "path": list(cast(Sequence[str], d["qdrant_path"])),
            "type": "bool" if d["type"] == "boolean" else cast(str, d["type"]),
        }
        for name, d in bindings._declarations.items()
        if d.get("qdrant_path") is not None
    }

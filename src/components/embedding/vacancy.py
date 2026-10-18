"""Deterministic, fieldwise vacancy text representation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.components.embedding.text import normalize_text
from src.domain import JSONValue
from src.domain.document.vacancy import Vacancy
from src.domain.serialization import fingerprint, validate_json


@dataclass(frozen=True)
class TextField:
    name: str
    required: bool


class VacancyText:
    def __init__(
        self,
        fields: Sequence[Mapping[str, object]] = (
            {"name": "title", "required": False},
            {"name": "description", "required": False},
        ),
        separator: str = "\n\n",
        schema_version: int = 1,
        renderer: str = "vacancy-fields-v1",
    ) -> None:
        if type(schema_version) is not int or schema_version != 1:
            raise ValueError("Unsupported document recipe schema")
        if renderer != "vacancy-fields-v1":
            raise ValueError("Unsupported vacancy renderer")
        if not isinstance(separator, str):
            raise ValueError("Document separator must be text")
        validate_json(separator)
        parsed = []
        for field in fields:
            if not isinstance(field, Mapping) or set(field) != {"name", "required"}:
                raise ValueError("Text fields require name and required")
            name, required = field["name"], field["required"]
            if (
                not isinstance(name, str)
                or not name.strip()
                or type(required) is not bool
            ):
                raise ValueError("Invalid text field declaration")
            validate_json(name)
            parsed.append(TextField(name, required))
        if not parsed or len({field.name for field in parsed}) != len(parsed):
            raise ValueError("Text fields must be nonempty and unique")
        self.fields = tuple(parsed)
        self.separator = separator
        self.schema_version = schema_version
        self.renderer = renderer

    def render(self, vacancy: Vacancy) -> str:
        parts = []
        for field in self.fields:
            value = vacancy.fields.get(field.name)
            if value is not None and not isinstance(value, str):
                raise ValueError("Vacancy text field must be a string or null")
            text = normalize_text(value) if isinstance(value, str) else ""
            if not text and field.required:
                raise ValueError("Required vacancy text field is empty")
            if text:
                parts.append(text)
        if not parts:
            raise ValueError("Vacancy has no nonempty text representation")
        return self.separator.join(parts)

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": self.schema_version,
            "renderer": self.renderer,
            "fields": [
                {"name": field.name, "required": field.required}
                for field in self.fields
            ],
            "separator": self.separator,
        }

    @property
    def id(self) -> str:
        return fingerprint(self.to_dict())

"""Versioned single-field ablations for independent title/body retrieval."""

from src.components.embedding.vacancy import VacancyText
from src.domain import JSONValue


class SingleFieldText(VacancyText):
    def __init__(self, field: str, purpose: str) -> None:
        if purpose not in {"title", "description"}:
            raise ValueError(
                "Single-field representation requires a title/body purpose"
            )
        super().__init__(fields=({"name": field, "required": True},))
        self.purpose = purpose
        self.schema_version = 2
        self.renderer = "vacancy-single-field-v2"

    def to_dict(self) -> dict[str, JSONValue]:
        return super().to_dict() | {"purpose": self.purpose}

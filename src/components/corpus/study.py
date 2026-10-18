"""Validate a shared corpus with nonempty titles and descriptions."""

from pathlib import Path

from src.components.corpus.json import JSONVacancyReader
from src.components.embedding.text import normalize_text
from src.domain import JSONValue
from src.domain.document.vacancy import Vacancy


class StudyCohortReader(JSONVacancyReader):
    identity = "study-cohort-json-v1"

    def __init__(
        self,
        path: str | Path,
        title_field: str = "title",
        description_field: str = "description",
    ) -> None:
        super().__init__(path)
        if (
            not isinstance(title_field, str)
            or not title_field.strip()
            or not isinstance(description_field, str)
            or not description_field.strip()
            or title_field == description_field
        ):
            raise ValueError(
                "Study cohort requires distinct title and description field names"
            )
        self.title_field, self.description_field = title_field, description_field

    def _audit(self, documents: tuple[Vacancy, ...]) -> dict[str, JSONValue]:
        issues: list[JSONValue] = []
        for document in documents:
            for field in (self.title_field, self.description_field):
                value = document.fields.get(field)
                reason = (
                    "missing_or_null"
                    if value is None
                    else "not_string"
                    if not isinstance(value, str)
                    else "empty_after_normalization"
                    if not normalize_text(value)
                    else None
                )
                if reason:
                    issues.append(
                        {"document_id": document.id, "field": field, "reason": reason}
                    )
        return {
            "reader": self.identity,
            "count": len(documents),
            "ids": [d.id for d in documents],
            "title_field": self.title_field,
            "description_field": self.description_field,
            "valid": not issues,
            "issues": issues,
        }

    def audit(self) -> dict[str, JSONValue]:
        """Inspect missing/invalid text without requiring a usable study cohort."""
        return self._audit(super().read())

    def read(self) -> tuple[Vacancy, ...]:
        documents = super().read()
        audit = self._audit(documents)
        if not audit["valid"]:
            raise ValueError(f"Study cohort text audit failed: {audit['issues']}")
        return documents

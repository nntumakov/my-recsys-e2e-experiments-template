"""Strict JSON vacancy array reader."""

from pathlib import Path

from src.domain.document.vacancy import Vacancy
from src.domain.serialization import load_json


class JSONVacancyReader:
    identity = "json-vacancies-v1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self) -> tuple[Vacancy, ...]:
        records = load_json(self.path.read_bytes())
        if not isinstance(records, list) or not records:
            raise ValueError("Corpus must be a nonempty JSON array")
        documents = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {"id", "fields"}:
                raise ValueError("Corpus records require exactly id and fields")
            identifier, fields = record["id"], record["fields"]
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError("Corpus requires nonblank document ids")
            if identifier in seen:
                raise ValueError("Duplicate corpus document id")
            if not isinstance(fields, dict):
                raise ValueError("Corpus document fields must be an object")
            seen.add(identifier)
            documents.append(Vacancy(identifier, fields))
        return tuple(documents)

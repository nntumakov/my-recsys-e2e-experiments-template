"""Versioned diagnostic artifact; legacy result formats remain unchanged."""

import json
from collections.abc import Sequence
from copy import deepcopy
from html import escape

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.domain import JSONValue
from src.integrations.opensearch.evidence import ObservedSERP


class ResearchArtifact(BaseArtifact):
    def __init__(
        self,
        now_ms: int,
        source_id: str,
        variants: Sequence[str],
        results: Sequence[tuple[str, str, str, ObservedSERP]],
        title_field: str = "title",
    ) -> None:
        self._session: dict[str, JSONValue] = {
            "fixed_now_ms": now_ms,
            "configured_source_id": source_id,
            "variants": list(variants),
        }
        self._results = deepcopy(tuple(results))
        self._title_field = title_field

    def to_dict(self) -> dict[str, JSONValue]:
        records: list[JSONValue] = []
        for key, text, variant, serp in self._results:
            records.append(
                {
                    "query_id": key,
                    "original_text": text,
                    "variant_id": variant,
                    "request_body": deepcopy(serp.request_body),
                    "understanding": deepcopy(serp.understanding),
                    "timing": {
                        "client_elapsed_ms": serp.client_elapsed_ms,
                        "server_took_ms": serp.server_took_ms,
                    },
                    "hits": [
                        {
                            "id": item.id,
                            "rank": rank,
                            "score": item.score,
                            "sort_values": deepcopy(serp.sort_values[item.id]),
                            "metadata": deepcopy(serp.metadata.get(item.id, {})),
                            "fields": deepcopy(item.fields),
                        }
                        for rank, item in enumerate(serp.items, 1)
                    ],
                }
            )
        return {
            "schema_version": 1,
            "session": deepcopy(self._session),
            "results": records,
        }

    def to_rows(self, title_field: str | None = None) -> list[dict[str, JSONValue]]:
        title_field = title_field or self._title_field
        rows: list[dict[str, JSONValue]] = []
        for key, text, variant, serp in self._results:
            for rank, item in enumerate(serp.items, 1):
                rows.append(
                    {
                        "query_id": key,
                        "query": text,
                        "variant": variant,
                        "rank": rank,
                        "id": item.id,
                        "title": deepcopy(item.fields.get(title_field)),
                        "score": item.score,
                        "sort": deepcopy(serp.sort_values[item.id]),
                        "metadata": deepcopy(serp.metadata.get(item.id, {})),
                        "fields": deepcopy(item.fields),
                    }
                )
            if not serp.items:
                rows.append(
                    {
                        "query_id": key,
                        "query": text,
                        "variant": variant,
                        "rank": None,
                        "id": None,
                        "title": None,
                        "score": None,
                        "sort": [],
                        "metadata": {},
                        "fields": {},
                    }
                )
        return rows

    def rank_changes(self) -> list[dict[str, JSONValue]]:
        """Positions per variant are diagnostics, not a quality metric."""
        grouped: dict[tuple[str, str], dict[str, JSONValue]] = {}
        for key, _text, variant, serp in self._results:
            for rank, item in enumerate(serp.items, 1):
                grouped.setdefault((key, item.id), {})[variant] = rank
        return [
            {"query_id": key, "id": identifier, "ranks": positions}
            for (key, identifier), positions in grouped.items()
        ]

    def _repr_html_(self) -> str:
        columns = (
            "variant",
            "rank",
            "id",
            "title",
            "score",
            "sort",
            "metadata",
            "fields",
        )
        heading = "<tr>" + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr>"
        rows = []
        for row in self.to_rows():
            cells = []
            for column in columns:
                value = row[column]
                text = (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else str(value if value is not None else "")
                )
                safe = escape(text)
                cells.append(
                    "<td>"
                    + (
                        "<details><summary>Полные поля</summary><pre>"
                        + safe
                        + "</pre></details>"
                        if column == "fields"
                        else safe
                    )
                    + "</td>"
                )
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return (
            "<table><thead>"
            + heading
            + "</thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

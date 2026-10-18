"""Snapshots and read-only comparison views for a search study session."""

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from html import escape
from typing import cast

from src.components.reporting.artifacts.abstract import BaseArtifact
from src.domain import JSONValue
from src.domain.serialization import canonical_bytes, validate_json
from src.domain.study import StudySERP

type StudyResult = tuple[str, dict[str, JSONValue], str, StudySERP]


class SearchStudyArtifact(BaseArtifact):
    def __init__(
        self, session: Mapping[str, JSONValue], results: Sequence[StudyResult]
    ) -> None:
        validate_json(dict(session))
        self._session = deepcopy(dict(session))
        self._results = deepcopy(tuple(results))
        seen = set()
        for key, inputs, variant, serp in self._results:
            if (key, variant) in seen:
                raise ValueError("Duplicate study query/variant result")
            seen.add((key, variant))
            if len({item.id for item in serp.items}) != len(serp.items):
                raise ValueError("Study artifact requires unique vacancy ids")
            validate_json(inputs)
            validate_json(serp.evidence)
            for item in serp.items:
                validate_json(
                    {"id": item.id, "fields": item.fields, "score": item.score}
                )

    @property
    def run_id(self) -> str:
        return cast(str, self._session["run_id"])

    def results(self) -> tuple[StudyResult, ...]:
        return deepcopy(self._results)

    def serp(self, query_id: str, variant: str) -> StudySERP:
        for key, _inputs, name, serp in self._results:
            if key == query_id and name == variant:
                return deepcopy(serp)
        raise KeyError("Query/variant is absent from this snapshot")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": 1,
            "artifact_type": "search-study",
            "session": deepcopy(self._session),
            "results": [
                {
                    "query_id": key,
                    "inputs": deepcopy(inputs),
                    "variant_id": variant,
                    "evidence": deepcopy(serp.evidence),
                    "hits": [
                        {
                            "id": item.id,
                            "rank": rank,
                            "score": item.score,
                            "fields": deepcopy(item.fields),
                        }
                        for rank, item in enumerate(serp.items, 1)
                    ],
                }
                for key, inputs, variant, serp in self._results
            ],
        }

    def _description(self, variant: str) -> dict[str, JSONValue]:
        descriptions = cast(
            dict[str, dict[str, JSONValue]], self._session["descriptions"]
        )
        return descriptions[variant]

    def _selected(
        self, query_id: str, variants: Sequence[str] | None
    ) -> tuple[str, ...]:
        available = tuple(name for key, _, name, _ in self._results if key == query_id)
        selected = available if variants is None else tuple(variants)
        if (
            not selected
            or isinstance(variants, str)
            or len(set(selected)) != len(selected)
            or not set(selected) <= set(available)
        ):
            raise ValueError("Choose unique variants present in this query snapshot")
        return selected

    def _caption(self, query_id: str) -> str:
        records = [inputs for key, inputs, _, _ in self._results if key == query_id]
        if not records:
            raise KeyError("Query is absent from this snapshot")
        inputs = records[0]
        return (
            f"<p><strong>Run {escape(self.run_id)}</strong> · Query {escape(query_id)}: "
            f"{escape(str(inputs['original_text']))}<br>Hard filters: "
            f"{escape(json.dumps(inputs['effective_filters'], ensure_ascii=False))}</p>"
        )

    def summary(self, query_id: str = "manual") -> list[dict[str, JSONValue]]:
        rows = []
        for key, _inputs, name, serp in self._results:
            if key != query_id:
                continue
            evidence = serp.evidence
            calls = cast(list[dict[str, JSONValue]], evidence.get("calls", [])) + cast(
                list[dict[str, JSONValue]], evidence.get("study_admission", [])
            )
            rows.append(
                {
                    "variant": name,
                    "label": self._description(name).get("label"),
                    "active_path": evidence.get("active_path"),
                    "abstention": evidence.get("abstention"),
                    "returned_vacancies": len(serp.items),
                    "score_type": evidence.get("score_type"),
                    "end_to_end_ms": evidence.get("end_to_end_ms"),
                    "inference_calls": sum(
                        c.get("stage") in ("inference", "encode") for c in calls
                    ),
                    "retrieval_calls": sum(
                        c.get("stage") == "retrieval" for c in calls
                    ),
                    "admission_calls": sum(
                        c.get("stage") == "admission" for c in calls
                    ),
                    "http_calls": sum(cast(int, c.get("http_calls", 0)) for c in calls),
                }
            )
        return rows

    def summary_html(self, query_id: str = "manual") -> str:
        columns = (
            "variant",
            "label",
            "active_path",
            "abstention",
            "returned_vacancies",
            "score_type",
            "end_to_end_ms",
            "inference_calls",
            "retrieval_calls",
            "admission_calls",
            "http_calls",
        )
        heading = "<tr>" + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr>"
        rows = "".join(
            "<tr>"
            + "".join(
                f"<td>{escape(str(row.get(c) if row.get(c) is not None else '—'))}</td>"
                for c in columns
            )
            + "</tr>"
            for row in self.summary(query_id)
        )
        comparability = cast(dict[str, JSONValue], self._session["comparability"])
        status = (
            "Сопоставимый cohort: декларация и её provenance сохранены"
            if comparability["comparable"]
            else "Несопоставимые источники"
        )
        return (
            self._caption(query_id)
            + f"<p>{escape(status)} · Composition {escape(str(self._session['composition_id']))}</p>"
            + '<div style="overflow-x:auto"><table>'
            + heading
            + rows
            + "</table></div>"
        )

    def rank_matrix(
        self,
        query_id: str = "manual",
        *,
        variants: Sequence[str] | None = None,
        reference_variant: str | None = None,
    ) -> list[dict[str, JSONValue]]:
        selected = self._selected(query_id, variants)
        reference = reference_variant or cast(str, self._session["reference_variant"])
        if reference not in selected:
            if reference_variant is not None:
                raise ValueError("Reference must be a selected snapshot variant")
            reference = selected[0]
        comparability = cast(dict[str, JSONValue], self._session["comparability"])
        group_names = (
            ("shared",)
            if comparability["comparable"]
            else tuple(
                dict.fromkeys(
                    cast(str, self._description(name)["family"]) for name in selected
                )
            )
        )
        rows = []
        for group in group_names:
            members = tuple(
                name
                for name in selected
                if group == "shared" or self._description(name)["family"] == group
            )
            group_ref = reference if reference in members else members[0]
            ranks = {}
            fields = {}
            for name in (group_ref, *(v for v in members if v != group_ref)):
                for rank, item in enumerate(self.serp(query_id, name).items, 1):
                    ranks.setdefault(item.id, {})[name] = rank
                    fields.setdefault(item.id, {})[name] = item.fields
            for identifier, positions in ranks.items():
                versions = fields[identifier]
                initial = next(iter(versions.values()))
                rows.append(
                    {
                        "group": group,
                        "id": identifier,
                        "fields": deepcopy(initial),
                        "ranks": dict(positions),
                        "reference_variant": group_ref,
                        "deltas": {
                            name: positions[group_ref] - positions[name]
                            if group_ref in positions and name in positions
                            else None
                            for name in members
                        },
                        "source_difference": len(
                            {canonical_bytes(f) for f in versions.values()}
                        )
                        > 1,
                        "field_versions": deepcopy(versions),
                    }
                )
        return rows

    def card(
        self,
        identifier: str,
        *,
        query_id: str = "manual",
        variant: str | None = None,
    ) -> dict[str, JSONValue]:
        occurrences = []
        for key, _inputs, name, serp in self._results:
            if key != query_id or variant is not None and name != variant:
                continue
            for rank, item in enumerate(serp.items, 1):
                if item.id == identifier:
                    occurrences.append(
                        {
                            "variant": name,
                            "family": self._description(name)["family"],
                            "rank": rank,
                            "score": item.score,
                            "fields": deepcopy(item.fields),
                            "score_type": serp.evidence.get("score_type"),
                            "evidence": deepcopy(serp.evidence),
                            "hit_evidence": deepcopy(
                                cast(
                                    dict[str, JSONValue], serp.evidence.get("hits", {})
                                ).get(identifier, {})
                            ),
                        }
                    )
        if not occurrences:
            raise KeyError("Vacancy is absent from this snapshot")
        return {
            "run_id": self.run_id,
            "query_id": query_id,
            "id": identifier,
            "occurrences": occurrences,
        }

    def _conditions(self, fields: dict[str, JSONValue], family: str) -> str:
        bindings = cast(
            dict[str, dict[str, JSONValue]], self._session.get("filter_bindings", {})
        )
        values = []
        for name, declaration in bindings.items():
            path = declaration.get(
                "opensearch_source_path" if family == "fulltext" else "qdrant_path"
            )
            if path is None:
                values.append(f"{name}: путь исходных данных не объявлен")
                continue
            value: JSONValue = fields
            present = True
            for part in cast(list[str], path):
                if not isinstance(value, dict) or part not in value:
                    present = False
                    break
                value = value[part]
            values.append(
                f"{name}: {json.dumps(value, ensure_ascii=False) if present else 'не указано'}"
            )
        return " · ".join(values)

    def rank_lists_html(
        self,
        query_id: str = "manual",
        *,
        variants: Sequence[str] | None = None,
    ) -> str:
        selected = self._selected(query_id, variants)
        results = {name: self.serp(query_id, name) for name in selected}
        header = (
            "<tr><th>Rank</th>"
            + "".join(
                f"<th>{escape(name)} — {escape(str(self._description(name).get('label', name)))}</th>"
                for name in selected
            )
            + "</tr>"
        )
        rows = []
        for offset in range(max((len(s.items) for s in results.values()), default=0)):
            cells = []
            for name in selected:
                items = results[name].items
                if offset >= len(items):
                    cells.append(
                        "<td>0 результатов</td>"
                        if not items and offset == 0
                        else "<td>—</td>"
                    )
                    continue
                item = items[offset]
                description = self._description(name)
                title = str(
                    item.fields.get(
                        cast(str, description.get("title_field", "title")), ""
                    )
                )
                body = str(
                    item.fields.get(
                        cast(str, description.get("description_field", "description")),
                        "",
                    )
                )
                cells.append(
                    f"<td><strong>{escape(title)}</strong><br>{escape(item.id)}"
                    f"<br>{escape(body[:240])}<br>"
                    f"{escape(str(results[name].evidence.get('score_type', 'unknown')))}: "
                    f"{escape(str(item.score))}</td>"
                )
            rows.append(f"<tr><th>{offset + 1}</th>{''.join(cells)}</tr>")
        if not rows:
            rows.append(
                "<tr><td>—</td>" + "<td>0 результатов</td>" * len(selected) + "</tr>"
            )
        return (
            self._caption(query_id)
            + '<div style="overflow-x:auto"><table>'
            + header
            + "".join(rows)
            + "</table></div>"
        )

    def matrix_html(
        self,
        query_id: str = "manual",
        *,
        variants: Sequence[str] | None = None,
        reference_variant: str | None = None,
    ) -> str:
        selected = self._selected(query_id, variants)
        rows = self.rank_matrix(
            query_id, variants=selected, reference_variant=reference_variant
        )
        parts = [self._caption(query_id)]
        comparability = cast(dict[str, JSONValue], self._session["comparability"])
        if not comparability["comparable"]:
            parts.append(
                "<p>Несопоставимые источники: отдельные fulltext/dense матрицы.</p>"
            )
        for group in dict.fromkeys(cast(str, r["group"]) for r in rows):
            members = tuple(
                name
                for name in selected
                if group == "shared" or self._description(name)["family"] == group
            )
            parts.append(
                f"<h4>{escape(group)}</h4><table><tr><th>Vacancy</th>"
                + "".join(f"<th>{escape(name)}</th>" for name in members)
                + "</tr>"
            )
            for row in (r for r in rows if r["group"] == group):
                fields = cast(dict[str, JSONValue], row["fields"])
                ranks = cast(dict[str, JSONValue], row["ranks"])
                deltas = cast(dict[str, JSONValue], row["deltas"])
                title_field = cast(
                    str, self._description(members[0]).get("title_field", "title")
                )
                title = str(fields.get(title_field, ""))
                note = (
                    " · source difference; inspect field versions"
                    if row["source_difference"]
                    else ""
                )
                parts.append(
                    f"<tr><td>{escape(str(row['id']))}<br>{escape(title + note)}</td>"
                    + "".join(
                        f"<td>{escape(str(ranks[name]))}"
                        f"{f' (Δ {deltas[name]:+})' if deltas[name] is not None else ''}</td>"
                        if name in ranks
                        else "<td>—: не попала в top-k</td>"
                        for name in members
                    )
                    + "</tr>"
                )
            parts.append("</table>")
        if not rows:
            parts.append("<p>0 результатов</p>")
        return "".join(parts)

    def card_html(
        self, identifier: str, *, query_id: str = "manual", variant: str | None = None
    ) -> str:
        card = self.card(identifier, query_id=query_id, variant=variant)
        parts = [self._caption(query_id), f"<h3>{escape(identifier)}</h3>"]
        for occurrence in cast(list[dict[str, JSONValue]], card["occurrences"]):
            name = cast(str, occurrence["variant"])
            fields = cast(dict[str, JSONValue], occurrence["fields"])
            description = self._description(name)
            title = str(
                fields.get(
                    cast(str, description.get("title_field", "title")), "не указано"
                )
            )
            body = str(
                fields.get(
                    cast(str, description.get("description_field", "description")),
                    "не указано",
                )
            )
            hit_evidence = cast(dict[str, JSONValue], occurrence["hit_evidence"])
            parts.extend(
                [
                    f"<h4>{escape(name)} · Rank {escape(str(occurrence['rank']))} · {escape(str(occurrence['score_type']))}: {escape(str(occurrence['score']))}</h4>",
                    f"<strong>{escape(title)}</strong>",
                    "<p>"
                    + escape(self._conditions(fields, cast(str, occurrence["family"])))
                    + "</p>",
                    f'<div style="white-space:pre-wrap;overflow-wrap:anywhere">{escape(body)}</div>',
                    "<details><summary>Все исходные fields</summary><pre>"
                    + escape(json.dumps(fields, ensure_ascii=False, indent=2))
                    + "</pre></details>",
                    "<details><summary>Диагностика варианта</summary><pre>"
                    + escape(
                        json.dumps(occurrence["evidence"], ensure_ascii=False, indent=2)
                    )
                    + "</pre></details>",
                ]
            )
            if "winner_passage" in hit_evidence:
                parts.append(
                    '<h5>Победивший passage E5</h5><div style="white-space:pre-wrap">'
                    + escape(str(hit_evidence["winner_passage"]))
                    + "</div>"
                )
            if hit_evidence:
                parts.append(
                    "<details><summary>Provenance этой вакансии</summary><pre>"
                    + escape(json.dumps(hit_evidence, ensure_ascii=False, indent=2))
                    + "</pre></details>"
                )
        return "".join(parts)

    def diagnostics_html(
        self, query_id: str = "manual", variant: str | None = None
    ) -> str:
        selected = self._selected(query_id, [variant] if variant is not None else None)
        diagnostics = {name: self.serp(query_id, name).evidence for name in selected}
        return (
            self._caption(query_id)
            + "<details><summary>Диагностика</summary><pre>"
            + escape(json.dumps(diagnostics, ensure_ascii=False, indent=2))
            + "</pre></details>"
        )

    def _repr_html_(self) -> str:
        query_ids = tuple(dict.fromkeys(key for key, _, _, _ in self._results))
        return "".join(
            self.rank_lists_html(key) + self.matrix_html(key) for key in query_ids
        )

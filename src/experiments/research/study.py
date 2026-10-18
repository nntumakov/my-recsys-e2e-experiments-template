"""Compare search variants for one query or a CSV query set."""

import json
import re
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import cast
from uuid import uuid4

from src.components.reporting.artifacts.study import SearchStudyArtifact
from src.components.reporting.exporters.abstract import BaseExporter
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.study import StudySearchTransformer
from src.domain import JSONValue
from src.domain.request.abstract import BaseFilter
from src.domain.request.filters import QueryFilter, TermFilter
from src.domain.serialization import fingerprint, validate_json
from src.domain.study import StudySERP
from src.experiments.abstract import BaseExperiment
from src.experiments.batch.search import read_queries
from src.experiments.direct.search import SearchExperiment
from src.experiments.research.filters import StudyFilterBindings


def checkout_identity() -> dict[str, JSONValue]:
    root = Path(__file__).resolve().parents[3]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {"revision": revision, "dirty": bool(dirty)}


@contextmanager
def _stage(variant: str, stage: str) -> Iterator[None]:
    try:
        yield
    except Exception as error:
        error.add_note(f"Study variant={variant}, stage={stage}")
        raise


class StudyExperiment(BaseExperiment):
    def __init__(
        self,
        registry: BaseTransformerRegistry,
        variants: Sequence[str],
        bindings: StudyFilterBindings,
        exporters: Sequence[BaseExporter] = (),
        *,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
        comparability: Mapping[str, JSONValue] | None = None,
        checkout: Mapping[str, JSONValue] | None = None,
        text: str | None = None,
        input_path: str | None = None,
        configuration_identity: str | None = None,
    ) -> None:
        super().__init__(exporters)
        if (
            not variants
            or isinstance(variants, str)
            or len(set(variants)) != len(variants)
        ):
            raise ValueError("Select a nonempty unique study variant list")
        if text is not None and input_path is not None:
            raise ValueError("Select either one study query or a CSV")
        self.registry, self.variants, self.bindings = (
            registry,
            tuple(variants),
            bindings,
        )
        self._context, self._filters = deepcopy(dict(context or {})), tuple(filters)
        validate_json(self._context)
        static_filter_declarations = bindings.describe(self._filters)
        self._comparability = deepcopy(dict(comparability or {"comparable": False}))
        if type(self._comparability.get("comparable")) is not bool:
            raise ValueError("Declare study source comparability explicitly")
        if self._comparability["comparable"] and (
            not isinstance(self._comparability.get("cohort_sha256"), str)
            or not re.fullmatch(
                r"[0-9a-f]{64}", cast(str, self._comparability["cohort_sha256"])
            )
            or not isinstance(self._comparability.get("evidence"), str)
            or not cast(str, self._comparability["evidence"]).strip()
        ):
            raise ValueError("Comparable sources require cohort identity and evidence")
        self._checkout = (
            deepcopy(dict(checkout)) if checkout is not None else checkout_identity()
        )
        validate_json(self._comparability)
        validate_json(self._checkout)
        self._descriptions = {
            name: self._leaf(name).describe() for name in self.variants
        }
        validate_json(self._descriptions)
        for name, description in self._descriptions.items():
            if description.get("family") not in ("fulltext", "embedding"):
                raise ValueError(f"Study variant has no declared family: {name}")
        self.composition_id = fingerprint(
            {
                "variants": self._descriptions,
                "bindings": bindings.to_dict(),
                "static_context": self._context,
                "static_filters": static_filter_declarations,
                "comparability": self._comparability,
                "configuration_identity": configuration_identity,
            }
        )
        self.text, self.input_path = text, input_path

    def _leaf(self, name: str) -> StudySearchTransformer:
        if name not in self.variants:
            raise ValueError(f"Study variant is not registered: {name}")
        leaf = self.registry[name]
        if not isinstance(leaf, StudySearchTransformer):
            raise ValueError("Study requires a search with preview and admission")
        return leaf

    def describe(self) -> dict[str, JSONValue]:
        return {
            "composition_id": self.composition_id,
            "variants": cast(dict[str, JSONValue], deepcopy(self._descriptions)),
            "filters": self.bindings.to_dict(),
            "comparability": deepcopy(self._comparability),
            "readiness": "configured; remote prerequisites not checked",
        }

    def _inputs(
        self,
        text: str,
        variants: Sequence[str] | None,
        context: Mapping[str, JSONValue] | None,
        filters: Sequence[BaseFilter],
    ) -> tuple[tuple[str, ...], dict[str, PipelineData], dict[str, JSONValue]]:
        selected = tuple(self.variants if variants is None else variants)
        if (
            not isinstance(text, str)
            or not text.strip()
            or not selected
            or isinstance(variants, str)
            or len(set(selected)) != len(selected)
        ):
            raise ValueError(
                "Study needs a nonempty query and unique selected variants"
            )
        merged = deepcopy({**self._context, **(context or {})})
        validate_json(merged)
        effective = self.bindings.resolve((*self._filters, *filters), merged)
        data = {}
        for name in selected:
            self._leaf(name)
            physical = self.bindings.physical(
                effective, cast(str, self._descriptions[name]["family"])
            )
            data[name] = PipelineData(
                context=deepcopy(merged), filters=(*physical, QueryFilter(text))
            )
        inputs: dict[str, JSONValue] = {
            "original_text": text,
            "variants": list(selected),
            "effective_filters": [
                {"field": f.field, "value": f.value} for f in effective
            ],
            "static_filters": [
                {"field": f.field, "value": f.value}
                for f in self.bindings.resolve(self._filters, merged)
            ],
            "per_call_filters": [
                {"field": f.field, "value": f.value}
                for f in self.bindings.resolve(filters, merged)
            ],
        }
        return selected, data, inputs

    def preview(
        self,
        text: str,
        *,
        variants: Sequence[str] | None = None,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
    ) -> dict[str, JSONValue]:
        selected, data, inputs = self._inputs(text, variants, context, filters)
        plans = {}
        for name in selected:
            with _stage(name, "preview"):
                plans[name] = self._leaf(name).preview(data[name])
        return deepcopy(
            {
                "inputs": inputs,
                "plans": cast(dict[str, JSONValue], plans),
                "composition_id": self.composition_id,
            }
        )

    def search(
        self,
        text: str,
        *,
        variant: str,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
    ) -> StudySERP:
        artifact = self.compare(
            text, variants=[variant], context=context, filters=filters
        )
        return artifact.serp("manual", variant)

    def compare(
        self,
        text: str,
        *,
        variants: Sequence[str] | None = None,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
        reference_variant: str | None = None,
    ) -> SearchStudyArtifact:
        return self.compare_queries(
            [("manual", text)],
            variants=variants,
            context=context,
            filters=filters,
            reference_variant=reference_variant,
        )

    def compare_queries(
        self,
        queries: Sequence[tuple[str, str]],
        *,
        variants: Sequence[str] | None = None,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
        reference_variant: str | None = None,
    ) -> SearchStudyArtifact:
        if (
            not queries
            or any(not isinstance(key, str) or not key.strip() for key, _ in queries)
            or len({key for key, _ in queries}) != len(queries)
        ):
            raise ValueError("Study queries require unique nonempty ids")
        prepared = []
        for key, text in queries:
            selected, data, inputs = self._inputs(text, variants, context, filters)
            reference = reference_variant or selected[0]
            if reference not in selected:
                raise ValueError("Reference must be a selected variant")
            plans = {}
            for name in selected:
                with _stage(name, f"preview query={key}"):
                    plans[name] = self._leaf(name).preview(data[name])
            prepared.append((key, text, selected, data, inputs, plans))
        run_id, started_at = uuid4().hex, datetime.now(UTC).isoformat()
        comparison_started = monotonic()
        results = []
        for key, text, selected, data, inputs, plans in prepared:
            admission = {}
            for name in selected:
                with _stage(name, f"admission query={key}"):
                    admission[name] = self._leaf(name).admit(data[name].copy(deep=True))
            self._validate_cohorts(admission)
            for name in selected:
                physical = tuple(
                    f for f in data[name].filters if isinstance(f, TermFilter)
                )
                started = monotonic()
                with _stage(name, f"search query={key}"):
                    serp = SearchExperiment(self.registry, name).search(
                        text,
                        context=data[name].context,
                        filters=physical,
                    )
                if not isinstance(serp, StudySERP):
                    raise ValueError("Study search lost its completed evidence")
                serp.evidence["study_admission"] = cast(
                    list[JSONValue], deepcopy(admission[name])
                )
                serp.evidence["search_elapsed_ms"] = (monotonic() - started) * 1000
                serp.evidence["end_to_end_ms"] = serp.evidence[
                    "search_elapsed_ms"
                ] + sum(
                    cast(float, call.get("elapsed_ms", 0)) for call in admission[name]
                )
                serp.evidence["run_id"] = run_id
                serp.evidence["prepared_plan"] = deepcopy(plans[name])
                results.append((key, deepcopy(inputs), name, serp))
        session: dict[str, JSONValue] = {
            "run_id": run_id,
            "started_at": started_at,
            "checkout": self._checkout,
            "composition_id": self.composition_id,
            "variants": list(selected),
            "reference_variant": reference,
            "descriptions": cast(dict[str, JSONValue], self._descriptions),
            "comparability": self._comparability,
            "filter_bindings": self.bindings.to_dict(),
            "comparison_elapsed_ms": (monotonic() - comparison_started) * 1000,
        }
        return SearchStudyArtifact(session, results)

    def _validate_cohorts(
        self, admission: Mapping[str, Sequence[Mapping[str, JSONValue]]]
    ) -> None:
        digests = set()
        for name, calls in admission.items():
            if self._descriptions[name]["family"] != "embedding":
                continue
            for call in calls:
                descriptor = call.get("descriptor")
                if not isinstance(descriptor, dict):
                    raise ValueError(
                        "Embedding admission must expose its corpus descriptor"
                    )
                digests.add(descriptor["corpus_sha256"])
        if len(digests) > 1:
            raise ValueError("Selected embedding generations contain different cohorts")
        if (
            digests
            and self._comparability["comparable"]
            and digests != {self._comparability["cohort_sha256"]}
        ):
            raise ValueError(
                "Declared comparison cohort differs from admitted generations"
            )

    def run(self) -> SearchStudyArtifact:
        if self.input_path is not None:
            return self.compare_queries(read_queries(Path(self.input_path)))
        if self.text is not None:
            return self.compare(self.text)
        if len(self.variants) != 1:
            raise ValueError("Interactive CLI selects exactly one study variant")
        run_id = uuid4().hex
        started_at = datetime.now(UTC).isoformat()
        started = monotonic()
        comparison_elapsed_ms = 0.0
        results = []
        session: dict[str, JSONValue] | None = None
        while True:
            try:
                text = input("search> ").strip()
            except EOFError:
                break
            if not text:
                continue
            artifact = self.compare(text)
            print(json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2))
            current = artifact.to_dict()
            if session is None:
                session = deepcopy(cast(dict[str, JSONValue], current["session"]))
            comparison_elapsed_ms += cast(
                float,
                cast(dict[str, JSONValue], current["session"])["comparison_elapsed_ms"],
            )
            query_id = str(len(results) + 1)
            for _, inputs, name, serp in artifact.results():
                serp.evidence["comparison_run_id"] = serp.evidence["run_id"]
                serp.evidence["run_id"] = run_id
                results.append((query_id, inputs, name, serp))
        if session is None:
            session = {
                "composition_id": self.composition_id,
                "variants": list(self.variants),
                "reference_variant": self.variants[0],
                "descriptions": cast(dict[str, JSONValue], self._descriptions),
                "checkout": self._checkout,
                "comparability": self._comparability,
                "filter_bindings": self.bindings.to_dict(),
            }
        session.update(
            {
                "run_id": run_id,
                "started_at": started_at,
                "comparison_elapsed_ms": comparison_elapsed_ms,
                "session_elapsed_ms": (monotonic() - started) * 1000,
            }
        )
        return SearchStudyArtifact(session, results)

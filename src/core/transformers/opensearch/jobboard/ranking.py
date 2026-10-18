"""Apply ranking modes to search bodies; Painless scripts come from Hydra."""

import json
import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from src.core.transformers.opensearch.jobboard.query import SourceProfile
from src.domain import JSONValue
from src.domain.document.abstract import BaseDocument

_TYPES = {
    "published_at": "date",
    "quality": "numeric",
    "category": "keyword",
    "stable_id": "keyword",
}


@dataclass(frozen=True)
class RankingConfig:
    mode: str = "relevance"
    script: Mapping[str, JSONValue] = field(default_factory=dict)
    field_params: Mapping[str, str] = field(default_factory=dict)
    now_param: str | None = None
    window_size: int = 200
    phrase_slop: int = 1
    rescore_weight: float = 2.0

    def __post_init__(self) -> None:
        if self.mode not in {
            "relevance",
            "script_score",
            "script_sort",
            "phrase_rescore",
        }:
            raise ValueError("Unsupported ranking mode")
        json.dumps(dict(self.script), allow_nan=False)
        if self.mode == "relevance":
            if self.script or self.field_params or self.now_param:
                raise ValueError("Relevance ranking cannot contain a script")
        elif (
            set(self.script) != {"lang", "source", "params"}
            or self.script["lang"] != "painless"
            or not isinstance(self.script["source"], str)
            or not self.script["source"].strip()
            or not isinstance(self.script["params"], dict)
        ):
            raise ValueError("Ranking needs a complete inline Painless script")
        if any(
            not key or role not in _TYPES for key, role in self.field_params.items()
        ):
            raise ValueError("Invalid script field binding")
        if (
            type(self.window_size) is not int
            or self.window_size < 1
            or type(self.phrase_slop) is not int
            or self.phrase_slop < 0
            or type(self.rescore_weight) not in (int, float)
            or not math.isfinite(self.rescore_weight)
            or self.rescore_weight < 0
            or self.now_param is not None
            and not self.now_param
        ):
            raise ValueError("Invalid rescore or time settings")
        params = cast(dict[str, JSONValue], self.script.get("params", {}))
        if (
            set(params) & set(self.field_params)
            or self.now_param in params
            or self.now_param in self.field_params
        ):
            raise ValueError("Script params have more than one source")
        for key in ("half_life_days", "quality_pivot", "phrase_pivot"):
            if key in params and (
                type(params[key]) not in (int, float) or cast(float, params[key]) <= 0
            ):
                raise ValueError("Script scale must be positive")
        weights = [params.get(key, 0) for key in ("alpha", "beta")]
        if (
            any(type(v) not in (int, float) or cast(float, v) < 0 for v in weights)
            or sum(cast(list[float], weights)) > 0.5
        ):
            raise ValueError("Metadata bonus must be between zero and 0.5")
        if self.mode == "script_sort":
            tiers, other = params.get("tiers"), params.get("other_tier")
            if (
                not isinstance(tiers, dict)
                or not tiers
                or any(
                    not k or type(v) is not int or not 0 <= v <= 1000
                    for k, v in tiers.items()
                )
                or type(other) is not int
                or not max(cast(dict[str, int], tiers).values()) <= other <= 1000
            ):
                raise ValueError("Invalid category tiers")
        object.__setattr__(self, "script", deepcopy(dict(self.script)))
        object.__setattr__(self, "field_params", dict(self.field_params))

    def prepare(
        self, source: SourceProfile, now_ms: int, size: int
    ) -> dict[str, JSONValue]:
        script = deepcopy(dict(self.script))
        if self.mode == "relevance":
            return script
        params = cast(dict[str, JSONValue], script["params"])
        for parameter, role in self.field_params.items():
            params[parameter] = self._field(source, role)
        if self.now_param:
            if type(now_ms) is not int or now_ms < 0:
                raise ValueError(
                    "Session timestamp must be nonnegative epoch milliseconds"
                )
            params[self.now_param] = now_ms
        if self.mode == "script_sort":
            self._field(source, "category")
            self._field(source, "stable_id")
        if self.mode == "phrase_rescore" and (
            not source.title_field or self.window_size < size
        ):
            raise ValueError("Phrase rescore requires title and window >= final size")
        return script

    @staticmethod
    def _field(source: SourceProfile, role: str) -> str:
        declaration = source.ranking_fields.get(role)
        if not declaration or declaration["type"] != _TYPES[role]:
            raise ValueError(
                f"Ranking requires declared {role} field of type {_TYPES[role]}"
            )
        return declaration["field"]

    def apply(
        self,
        body: Mapping[str, JSONValue],
        source: SourceProfile,
        now_ms: int,
        text: str,
    ) -> dict[str, JSONValue]:
        if "sort" in body or "rescore" in body:
            raise ValueError("Ranking has a single owner")
        size = body.get("size")
        if type(size) is not int:
            raise ValueError("Search requires integer size")
        script = self.prepare(source, now_ms, size)
        result = deepcopy(dict(body))
        if self.mode == "script_score":
            result["query"] = {
                "script_score": {"query": result["query"], "script": script}
            }
        elif self.mode == "script_sort":
            result["track_scores"] = True
            result["sort"] = [
                {"_script": {"type": "number", "order": "asc", "script": script}},
                {"_score": "desc"},
                {self._field(source, "stable_id"): "asc"},
            ]
        elif self.mode == "phrase_rescore":
            result["rescore"] = {
                "window_size": self.window_size,
                "query": {
                    "query_weight": 1.0,
                    "rescore_query_weight": self.rescore_weight,
                    "score_mode": "total",
                    "rescore_query": {
                        "script_score": {
                            "query": {
                                "match_phrase": {
                                    cast(str, source.title_field): {
                                        "query": text,
                                        "slop": self.phrase_slop,
                                    }
                                }
                            },
                            "script": script,
                        }
                    },
                },
            }
        return result

    def source_diagnostics(
        self, item: BaseDocument, source: SourceProfile, now_ms: int
    ) -> dict[str, JSONValue]:
        """Read diagnostics from _source, independently of server-side doc values."""
        values: dict[str, JSONValue] = {}
        flags: list[JSONValue] = []
        for role in sorted(set(self.field_params.values())):
            name = source.ranking_fields[role]["field"]
            value = item.fields.get(name)
            # Preserve literal dotted keys, including explicit null, before paths.
            if name not in item.fields:
                value = item.fields
                for segment in name.split("."):
                    if not isinstance(value, dict):
                        value = None
                        break
                    value = value.get(segment)
            values[role] = deepcopy(value)
            if value is None:
                flags.append(f"missing_source_{role}")
            elif (
                role == "quality"
                and type(value) in (int, float)
                and cast(float, value) < 0
            ):
                flags.append("negative_quality")
            elif role == "published_at" and (
                type(value) is int or type(value) is float and math.isfinite(value)
            ):
                if value > now_ms:
                    flags.append("future_source_date")
            elif role == "published_at" and isinstance(value, str):
                try:
                    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    flags.append("unparsed_source_date")
                else:
                    if date.tzinfo is None:
                        flags.append("source_date_without_timezone")
                    elif date.timestamp() * 1000 > now_ms:
                        flags.append("future_source_date")
        return {
            "values": values,
            "flags": sorted(flags, key=str),
            "origin": "returned_source_not_doc_values",
        }

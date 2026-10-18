"""Fragment identity and payload schema v2 for passage retrieval."""

import re
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from math import isfinite
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from src.components.embedding.chunks import ChunkVacancyText
from src.domain import JSONValue
from src.domain.document.vacancy import Vacancy
from src.domain.embedding import EmbeddingProfile
from src.domain.serialization import canonical_bytes, validate_json
from src.integrations.qdrant.client import QdrantIntegration
from src.integrations.qdrant.source import QdrantSource

_FRAGMENT_NAMESPACE = uuid5(
    NAMESPACE_URL, "urn:recsys-experiments:vacancy-fragments:v2"
)


class ChunkQdrantSource(QdrantSource):
    def __init__(
        self,
        profile: Mapping | EmbeddingProfile,
        recipe: Mapping | ChunkVacancyText,
        corpus_identity: str,
        filter_fields: Mapping | None = None,
        *,
        runtime: Mapping,
        vector: Mapping,
    ) -> None:
        recipe = (
            recipe
            if isinstance(recipe, ChunkVacancyText)
            else ChunkVacancyText(**dict(recipe))
        )
        super().__init__(
            profile,
            recipe,
            corpus_identity,
            filter_fields,
            runtime=runtime,
            vector=vector,
        )
        self.recipe: ChunkVacancyText = recipe
        self._namespace = uuid5(_FRAGMENT_NAMESPACE, corpus_identity)

    @property
    def indexes(self) -> tuple[tuple[str, str], ...]:
        return (*super().indexes, ("document_id", "keyword"))

    @property
    def id_schema(self) -> dict[str, JSONValue]:
        return {
            "algorithm": "uuid5-fragment-v2",
            "project_namespace": str(_FRAGMENT_NAMESPACE),
            "corpus_namespace": str(self._namespace),
            "name": ["recipe_id", "document_id", "ordinal", "model_input_sha256"],
        }

    def to_dict(self) -> dict[str, JSONValue]:
        return super().to_dict() | {
            "schema_version": 2,
            "payload_schema_version": 2,
            "fragment_schema_version": 2,
            "payload_indexes": dict(self.indexes),
        }

    def point_id(self, domain_id: str, fragment: Mapping | None = None) -> str:
        if fragment is None:
            raise ValueError("Chunk identity requires a fragment")
        self.validate_fragment(fragment)
        if not isinstance(domain_id, str) or not domain_id.strip():
            raise ValueError("Chunk identity requires exact nonblank document id")
        name = canonical_bytes(
            {
                "recipe_id": self.recipe.id,
                "document_id": domain_id,
                "ordinal": fragment["ordinal"],
                "model_input_sha256": fragment["model_input_sha256"],
            }
        ).decode("utf-8")
        return str(uuid5(self._namespace, name))

    def validate_fragment(self, fragment: Mapping) -> None:
        if not isinstance(fragment, Mapping) or set(fragment) != {
            "ordinal",
            "start",
            "stop",
            "title_stop",
            "model_input_sha256",
        }:
            raise ValueError("Invalid chunk fragment envelope")
        ordinal, start, stop, title_stop, digest = (
            fragment[name]
            for name in ("ordinal", "start", "stop", "title_stop", "model_input_sha256")
        )
        if (
            any(type(value) is not int for value in (ordinal, start, stop, title_stop))
            or not (
                0 <= ordinal < self.recipe.max_chunks
                and 0 <= start <= stop
                and title_stop >= 0
            )
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            raise ValueError("Invalid chunk fragment values")
        validate_json(dict(fragment))

    def validate_payload(self, payload: Mapping) -> None:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"document_id", "fields", "fragment"}
            or not isinstance(payload["document_id"], str)
            or not payload["document_id"].strip()
            or not isinstance(payload["fields"], dict)
        ):
            raise ValueError("Invalid chunk payload envelope")
        self.validate_fields(payload["fields"])
        fragment = payload["fragment"]
        self.validate_fragment(fragment)
        title, body = self.recipe.texts(
            Vacancy(payload["document_id"], payload["fields"])
        )
        raw = body.encode("utf-8")
        start, stop = fragment["start"], fragment["stop"]
        if (
            stop > len(raw)
            or (body and start == stop)
            or (not body and (start != 0 or stop != 0))
        ):
            raise ValueError("Chunk offsets outside normalized description")
        try:
            raw[:start].decode("utf-8")
            raw[start:stop].decode("utf-8")
            raw[stop:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Chunk fragment splits Unicode") from exc
        if (start and not chr(raw[start - 1]).isspace()) or (
            stop < len(raw) and not chr(raw[stop - 1]).isspace()
        ):
            raise ValueError("Chunk fragment splits a word")
        title_raw = title.encode("utf-8")
        title_stop = fragment["title_stop"]
        if title_stop > len(title_raw) or (
            self.recipe.title_tokens == 0 and title_stop != 0
        ):
            raise ValueError("Chunk title cutoff outside normalized title")
        try:
            context = title_raw[:title_stop].decode("utf-8")
            title_raw[title_stop:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Chunk title cutoff splits Unicode") from exc
        if context != context.rstrip() or (
            title_stop < len(title_raw)
            and title_stop
            and title_raw[title_stop : title_stop + 1] != b" "
        ):
            raise ValueError("Chunk title cutoff splits a word")
        model_input = (
            self.profile.document_prefix
            + context
            + "\n"
            + raw[start:stop].decode("utf-8")
        )
        if (
            sha256(model_input.encode("utf-8")).hexdigest()
            != fragment["model_input_sha256"]
        ):
            raise ValueError(
                "Chunk model input digest differs from reconstructable passage"
            )

    def passage(self, document: Vacancy, fragment: Mapping) -> str:
        """Reconstruct the exact audited passage locally, including title context."""
        self.validate_payload(
            {
                "document_id": document.id,
                "fields": document.fields,
                "fragment": dict(fragment),
            }
        )
        title, body = self.recipe.texts(document)
        return (
            title.encode("utf-8")[: fragment["title_stop"]].decode("utf-8")
            + "\n"
            + body.encode("utf-8")[fragment["start"] : fragment["stop"]].decode("utf-8")
        )

    def metadata(
        self, collection: str, *, expected_documents: int | None = None, **kwargs
    ) -> dict[str, JSONValue]:
        result = super().metadata(collection, **kwargs)
        descriptor = cast(dict[str, JSONValue], result["recsys"])
        descriptor.update(
            {
                "schema_version": 2,
                "payload_schema_version": 2,
                "fragment_schema_version": 2,
            }
        )
        if expected_documents is not None:
            if type(
                expected_documents
            ) is not int or not 0 < expected_documents <= cast(
                int, kwargs["expected_points"]
            ):
                raise ValueError("Invalid chunk generation document count")
            descriptor["expected_documents"] = expected_documents
        return result

    def _validate_verification(
        self, verification: Mapping | None, expected_points: int
    ) -> None:
        if not isinstance(verification, Mapping):
            raise ValueError("Missing chunk build verification")
        expected = {
            "protocol": "full-fragment-readback-v2",
            "exact_count": expected_points,
            "read_back_count": expected_points,
            "status": "green",
            "optimizer_status": "ok",
            "indexes": dict(self.indexes),
            "smoke": True,
            "exclusion_smoke": True,
            "coverage": True,
            "ordinals": True,
            "fields_consistent": True,
        }
        if any(
            key not in verification
            or canonical_bytes(verification[key]) != canonical_bytes(value)
            for key, value in expected.items()
        ):
            raise ValueError("Incomplete chunk build verification")
        if (
            type(verification.get("documents_count")) is not int
            or not 0 < verification["documents_count"] <= expected_points
        ):
            raise ValueError("Invalid verified chunk document count")

    def validate_descriptor(
        self, collection: str, info: Mapping, *, state: str = "ready"
    ) -> dict[str, JSONValue]:
        descriptor = super().validate_descriptor(collection, info, state=state)
        count = descriptor.get("expected_documents")
        if type(count) is not int or not 0 < count <= cast(
            int, descriptor["expected_points"]
        ):
            raise ValueError("Missing chunk generation document count")
        if (
            state == "ready"
            and cast(dict, descriptor["verification"])["documents_count"] != count
        ):
            raise ValueError(
                "Chunk descriptor document count differs from verification"
            )
        return descriptor

    def hit(self, point: Mapping) -> tuple[Vacancy, dict[str, JSONValue]]:
        payload, score, version = (
            point.get("payload"),
            point.get("score"),
            point.get("version"),
        )
        if not isinstance(payload, dict):
            raise ValueError("Missing chunk payload")
        self.validate_payload(payload)
        if (
            type(score) not in (int, float)
            or not isfinite(cast(float, score))
            or not -1.00001 <= cast(float, score) <= 1.00001
            or type(version) is not int
            or version < 0
        ):
            raise ValueError("Invalid native chunk score or version")
        identifier = point.get("id")
        if not isinstance(identifier, str) or str(UUID(identifier)) != self.point_id(
            payload["document_id"], payload["fragment"]
        ):
            raise ValueError("Chunk point identity mismatch")
        return Vacancy(
            payload["document_id"],
            deepcopy(payload["fields"]),
            float(cast(float, score)),
        ), deepcopy(payload["fragment"])


class ChunkQdrantIntegration(QdrantIntegration):
    def validate_payload(self, payload: Mapping[str, JSONValue]) -> None:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"document_id", "fields", "fragment"}
            or not isinstance(payload["document_id"], str)
            or not payload["document_id"].strip()
            or not isinstance(payload["fields"], dict)
            or not isinstance(payload["fragment"], dict)
        ):
            raise ValueError("Invalid v2 fragment payload envelope")
        if len(canonical_bytes(payload)) > self.payload_bytes:
            raise ValueError("Chunk payload exceeds byte budget")

    def query_points(
        self, name: str, body: Mapping[str, JSONValue], source: ChunkQdrantSource
    ) -> list[dict[str, JSONValue]]:
        if len(canonical_bytes(dict(body))) > self.upload_batch_bytes:
            raise ValueError("Chunk query exceeds request byte budget")
        response = self._request("POST", self._path(name) + "/points/query", body)
        result = response.get("result")
        if (
            response.get("status") != "ok"
            or not isinstance(result, dict)
            or not isinstance(result.get("points"), list)
        ):
            raise ValueError("Malformed native chunk query response")
        points = cast(list[JSONValue], result["points"])
        seen = set()
        previous = float("inf")
        if len(points) > cast(int, body["limit"]):
            raise ValueError("Chunk response exceeds requested page")
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("Malformed chunk hit")
            document, _ = source.hit(point)
            identifier = str(UUID(cast(str, point["id"])))
            if identifier in seen or cast(float, document.score) > previous:
                raise ValueError("Duplicate or unsorted native chunk points")
            seen.add(identifier)
            previous = cast(float, document.score)
        return cast(list[dict[str, JSONValue]], points)

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from src.components.embedding.vacancy import VacancyText
from src.domain import JSONScalar, JSONValue
from src.domain.embedding import EmbeddingProfile
from src.domain.serialization import canonical_bytes, fingerprint, validate_json

_PROJECT_NAMESPACE = uuid5(NAMESPACE_URL, "urn:recsys-experiments:vacancy:v1")
_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class QdrantSource:
    """Map domain documents and scalar filters to Qdrant points."""

    def __init__(
        self,
        profile: Mapping | EmbeddingProfile,
        recipe: Mapping | VacancyText,
        corpus_identity: str,
        filter_fields: Mapping | None = None,
        *,
        runtime: Mapping,
        vector: Mapping,
    ) -> None:
        self.profile = (
            profile
            if isinstance(profile, EmbeddingProfile)
            else EmbeddingProfile(**dict(profile))
        )
        self.recipe = (
            recipe if isinstance(recipe, VacancyText) else VacancyText(**dict(recipe))
        )
        if not isinstance(vector, Mapping) or canonical_bytes(
            dict(vector)
        ) != canonical_bytes({"size": self.profile.dimensions, "distance": "Cosine"}):
            raise ValueError(
                "Qdrant vector settings disagree with the embedding profile"
            )
        if not isinstance(corpus_identity, str) or not corpus_identity.strip():
            raise ValueError("Qdrant source requires a corpus identity")
        validate_json(corpus_identity)
        self.corpus_identity = corpus_identity
        self._namespace = uuid5(_PROJECT_NAMESPACE, corpus_identity)
        self._fields = deepcopy(dict(filter_fields or {}))
        paths: dict[tuple[str, ...], str] = {}
        for name, declaration in self._fields.items():
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(declaration, dict)
                or set(declaration) != {"path", "type"}
            ):
                raise ValueError("Invalid Qdrant filter declaration")
            path, kind = declaration["path"], declaration["type"]
            if (
                not isinstance(path, list)
                or not path
                or any(
                    not isinstance(p, str) or not _SEGMENT.fullmatch(p) for p in path
                )
                or kind not in ("keyword", "bool", "integer", "float")
            ):
                raise ValueError("Invalid Qdrant scalar path or type")
            if tuple(path) in paths and paths[tuple(path)] != kind:
                raise ValueError("Conflicting types for one Qdrant payload path")
            paths[tuple(path)] = kind
        self._runtime = deepcopy(dict(runtime))
        image = self._runtime.get("image")
        if (
            set(self._runtime) != {"version", "image"}
            or self._runtime["version"] != "1.9.4"
            or not isinstance(image, str)
            or "@sha256:" not in image
            or not _SHA256.fullmatch(image.rsplit("@sha256:", 1)[1])
        ):
            raise ValueError("Qdrant source requires pinned TEI version and image")
        validate_json(self._fields)
        validate_json(self._runtime)

    @property
    def filter_fields(self) -> dict[str, JSONValue]:
        return deepcopy(self._fields)

    @property
    def runtime(self) -> dict[str, JSONValue]:
        return deepcopy(self._runtime)

    @property
    def indexes(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            dict.fromkeys(
                ("fields." + ".".join(d["path"]), d["type"])
                for d in self._fields.values()
            )
        )

    @property
    def id_schema(self) -> dict[str, JSONValue]:
        return {
            "algorithm": "uuid5-v1",
            "project_namespace": str(_PROJECT_NAMESPACE),
            "corpus_namespace": str(self._namespace),
        }

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": 1,
            "embedding_profile_id": self.profile.id,
            "corpus_identity": self.corpus_identity,
            "recipe_id": self.recipe.id,
            "filter_fields": self.filter_fields,
            "vector": {
                "size": self.profile.dimensions,
                "distance": "Cosine",
                "datatype": "float32",
            },
            "vector_name": None,
            "payload_schema_version": 1,
            "id_schema": self.id_schema,
        }

    @property
    def id(self) -> str:
        return fingerprint(self.to_dict())

    def point_id(self, domain_id: str) -> str:
        if not isinstance(domain_id, str) or not domain_id.strip():
            raise ValueError("Qdrant document id must be nonblank text")
        validate_json(domain_id)
        return str(uuid5(self._namespace, domain_id))

    def validate_point_id(self, native_id: JSONValue, domain_id: str) -> str:
        if not isinstance(native_id, str):
            raise ValueError("Qdrant point id must be a UUID")
        identifier = UUID(native_id)
        if str(identifier) != self.point_id(domain_id):
            raise ValueError("Qdrant point id does not match the original document")
        return str(identifier)

    def _scalar(self, kind: str, value: JSONValue) -> None:
        valid = False
        if kind == "keyword":
            valid = isinstance(value, str)
        elif kind == "bool":
            valid = type(value) is bool
        elif kind == "integer":
            valid = type(value) is int and -(2**63) <= value < 2**63
        elif kind == "float":
            valid = (
                type(value) in (int, float)
                and isfinite(cast(float, value))
                and (type(value) is float or int(float(cast(int, value))) == value)
            )
        if not valid:
            raise ValueError("Value does not match declared Qdrant scalar type")

    def validate_fields(self, fields: Mapping[str, JSONValue]) -> None:
        validate_json(fields)
        for field in self._fields:
            self.field_value(fields, field)

    def field_value(self, fields: Mapping[str, JSONValue], field: str) -> JSONScalar:
        if field not in self._fields:
            raise ValueError("Unknown Qdrant filter field")
        declaration = self._fields[field]
        current: JSONValue = dict(fields)
        for part in declaration["path"]:
            if current is None:
                return None
            if not isinstance(current, dict):
                raise ValueError("Qdrant filter path requires JSON objects")
            current = current.get(part)
        if current is not None:
            self._scalar(declaration["type"], current)
        return cast(JSONScalar, current)

    def condition(self, field: str, value: JSONValue) -> dict[str, JSONValue]:
        if field not in self._fields:
            raise ValueError("Unknown Qdrant filter field")
        validate_json(value)
        declaration = self._fields[field]
        self._scalar(declaration["type"], value)
        result: dict[str, JSONValue] = {
            "key": "fields." + ".".join(declaration["path"])
        }
        if declaration["type"] == "float":
            result["range"] = {"gte": value, "lte": value}
        else:
            result["match"] = {"value": value}
        return result

    def metadata(
        self,
        collection: str,
        *,
        expected_points: int,
        corpus_sha256: str,
        runtime: Mapping,
        state: str = "building",
        verification: Mapping | None = None,
    ) -> dict[str, JSONValue]:
        if (
            type(expected_points) is not int
            or expected_points <= 0
            or not isinstance(corpus_sha256, str)
            or not _SHA256.fullmatch(corpus_sha256)
            or state not in ("building", "ready")
            or not isinstance(runtime, Mapping)
        ):
            raise ValueError("Invalid Qdrant generation metadata")
        for name, value in self._runtime.items():
            if runtime.get(name) != value:
                raise ValueError("Qdrant generation runtime does not match its source")
        if state == "ready":
            self._validate_verification(verification, expected_points)
        inference = runtime.get("inference")
        expected_inference = {
            "model_id": self.profile.model_id,
            "model_sha": self.profile.revision,
            "model_dtype": self.profile.inference_dtype,
            "model_type": {"embedding": {"pooling": self.profile.pooling}},
            "version": self._runtime["version"],
            "max_input_length": self.profile.max_tokens,
            "auto_truncate": False,
        }
        if (
            runtime.get("qdrant_version") != "1.19.2"
            or not isinstance(inference, Mapping)
            or any(
                key not in inference
                or canonical_bytes(inference[key]) != canonical_bytes(value)
                for key, value in expected_inference.items()
            )
        ):
            raise ValueError("Missing or incompatible checked runtime provenance")
        descriptor: dict[str, JSONValue] = {
            "schema_version": 1,
            "serialization_version": 1,
            "generation_id": collection,
            "physical_collection_name": collection,
            "state": state,
            "expected_points": expected_points,
            "corpus_identity": self.corpus_identity,
            "corpus_sha256": corpus_sha256,
            "embedding_profile": self.profile.to_dict(),
            "embedding_profile_sha256": self.profile.id,
            "document_recipe": self.recipe.to_dict(),
            "recipe_sha256": self.recipe.id,
            "source_profile": self.to_dict(),
            "source_profile_sha256": self.id,
            "id_schema": self.id_schema,
            "payload_schema_version": 1,
            "runtime": deepcopy(dict(runtime)),
        }
        if verification is not None:
            descriptor["verification"] = deepcopy(dict(verification))
        validate_json(descriptor)
        return {"recsys": descriptor}

    def _validate_verification(
        self, verification: Mapping | None, expected_points: int
    ) -> None:
        if not isinstance(verification, Mapping):
            raise ValueError("Missing Qdrant build verification")
        expected = {
            "protocol": "full-readback-v1",
            "exact_count": expected_points,
            "read_back_count": expected_points,
            "status": "green",
            "optimizer_status": "ok",
            "indexes": dict(self.indexes),
            "smoke": True,
        }
        for key, value in expected.items():
            if key not in verification or canonical_bytes(
                verification[key]
            ) != canonical_bytes(value):
                raise ValueError("Incomplete Qdrant build verification")

    def validate_descriptor(
        self, collection: str, info: Mapping, *, state: str = "ready"
    ) -> dict[str, JSONValue]:
        config = info.get("config")
        if not isinstance(config, dict):
            raise ValueError("Missing Qdrant collection configuration")
        metadata = config.get("metadata")
        descriptor = metadata.get("recsys") if isinstance(metadata, dict) else None
        if not isinstance(descriptor, dict):
            raise ValueError("Missing Qdrant generation descriptor")
        expected = self.metadata(
            collection,
            expected_points=descriptor.get("expected_points"),
            corpus_sha256=descriptor.get("corpus_sha256"),
            runtime=descriptor.get("runtime", {}),
            state=state,
            verification=descriptor.get("verification") if state == "ready" else None,
        )["recsys"]
        assert isinstance(expected, dict)
        for key, value in expected.items():
            if key not in descriptor or canonical_bytes(
                descriptor[key]
            ) != canonical_bytes(value):
                raise ValueError("Incompatible Qdrant generation descriptor")
        params = config.get("params")
        vectors = params.get("vectors") if isinstance(params, dict) else None
        if (
            not isinstance(vectors, dict)
            or type(vectors.get("size")) is not int
            or vectors["size"] != self.profile.dimensions
            or vectors.get("distance") != "Cosine"
            or vectors.get("datatype") not in (None, "float32")
            or vectors.get("multivector_config") is not None
            or vectors.get("quantization_config") is not None
            or config.get("quantization_config") is not None
        ):
            raise ValueError("Incompatible Qdrant vector schema")
        for name in ("shard_number", "replication_factor", "write_consistency_factor"):
            if type(params.get(name)) is not int or params[name] != 1:
                raise ValueError("Qdrant source requires a standalone collection")
        return deepcopy(descriptor)

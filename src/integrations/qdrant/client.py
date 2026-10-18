from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from http.client import HTTPException
from math import isfinite
from time import monotonic
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from src.domain import JSONValue
from src.domain.collection.serp import SERP
from src.domain.serialization import canonical_bytes, load_json, validate_json
from src.integrations.abstract import BaseIntegration
from src.integrations.http_response import read_response_body
from src.integrations.qdrant.domain import QdrantResult
from src.integrations.qdrant.source import QdrantSource


def validate_collection_name(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value
    ):
        raise ValueError("Invalid Qdrant collection name")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class QdrantIntegration(BaseIntegration):
    """Native Qdrant HTTP operations; the experiment controls write ordering."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        version: str = "1.19.2",
        timeout: float = 120.0,
        operation_timeout: int = 60,
        verification_deadline: float = 600.0,
        response_bytes: int = 33554432,
        payload_bytes: int = 262144,
        upload_batch_bytes: int = 4194304,
        shards: int = 1,
        replicas: int = 1,
        write_consistency: int = 1,
    ) -> None:
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "?" in url
            or "#" in url
            or any(char.isspace() or ord(char) < 32 for char in url)
        ):
            raise ValueError("Invalid Qdrant URL")
        parts.port
        if api_key is not None and (
            not isinstance(api_key, str)
            or not api_key
            or any(ord(char) < 32 or ord(char) > 126 for char in api_key)
        ):
            raise ValueError("Invalid Qdrant API key header")
        if (
            version != "1.19.2"
            or type(timeout) not in (int, float)
            or not isfinite(timeout)
            or type(operation_timeout) is not int
            or operation_timeout <= 0
            or timeout < operation_timeout + 10
            or type(verification_deadline) not in (int, float)
            or not isfinite(verification_deadline)
            or verification_deadline <= 0
            or any(
                type(value) is not int or value <= 0
                for value in (response_bytes, payload_bytes, upload_batch_bytes)
            )
            or any(
                type(value) is not int or value != 1
                for value in (shards, replicas, write_consistency)
            )
        ):
            raise ValueError("Invalid Qdrant version, limits or standalone settings")
        self.url, self.expected_version = url.rstrip("/"), version
        self.timeout, self.operation_timeout = timeout, operation_timeout
        self.verification_deadline = verification_deadline
        self.response_bytes, self.payload_bytes = response_bytes, payload_bytes
        self.upload_batch_bytes = upload_batch_bytes
        self.shards, self.replicas = shards, replicas
        self.write_consistency = write_consistency
        self._headers = {"Content-Type": "application/json"}
        if api_key is not None:
            self._headers["api-key"] = api_key
        self._opener = build_opener(_NoRedirect())

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, JSONValue] | None = None,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> dict[str, JSONValue]:
        if timeout is not None and (
            type(timeout) not in (int, float) or not isfinite(timeout) or timeout <= 0
        ):
            raise ValueError("Invalid Qdrant request timeout")
        if deadline is not None and (
            type(deadline) not in (int, float) or not isfinite(deadline)
        ):
            raise ValueError("Invalid Qdrant request deadline")
        request_timeout = (
            self.timeout if timeout is None else min(timeout, self.timeout)
        )
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Qdrant request deadline expired")
            request_timeout = min(request_timeout, remaining)
        request = Request(
            self.url + path,
            data=canonical_bytes(dict(body)) if body is not None else None,
            headers=self._headers,
            method=method,
        )
        try:
            with self._opener.open(
                request,
                timeout=request_timeout,
            ) as response:
                declared_length = response.headers.get("Content-Length")
                expected = int(declared_length) if declared_length is not None else None
                if expected is not None and not 0 <= expected <= self.response_bytes:
                    raise ValueError("Qdrant response exceeds byte budget")
                content = read_response_body(
                    response,
                    self.response_bytes,
                    deadline=deadline,
                )
                if len(content) > self.response_bytes:
                    raise ValueError("Qdrant response exceeds byte budget")
                if expected is not None and len(content) != expected:
                    raise ValueError("Incomplete Qdrant response")
                result = load_json(content)
                if deadline is not None and monotonic() >= deadline:
                    raise TimeoutError("Qdrant response deadline expired")
            if not isinstance(result, dict):
                raise ValueError("Expected a Qdrant JSON object")
        except HTTPError as exc:
            exc.close()
            raise RuntimeError(f"Qdrant HTTP failure ({exc.code})") from exc
        except (URLError, OSError, HTTPException, ValueError) as exc:
            raise RuntimeError("Qdrant transport or JSON failure") from exc
        return result

    def _path(self, name: str) -> str:
        validate_collection_name(name)
        return "/collections/" + quote(name, safe="")

    def _result(self, response: Mapping[str, JSONValue]) -> JSONValue:
        if response.get("status") != "ok" or "result" not in response:
            raise ValueError("Unsuccessful Qdrant operation")
        return response["result"]

    def _completed(self, response: Mapping[str, JSONValue]) -> None:
        result = self._result(response)
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise ValueError("Qdrant operation is not completed")

    def version(self) -> str:
        result = self._request("GET", "/")
        if result.get("version") != self.expected_version:
            raise ValueError("Unsupported Qdrant server version")
        return self.expected_version

    def collection(
        self,
        name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> dict[str, JSONValue]:
        result = self._result(
            self._request("GET", self._path(name), timeout=timeout, deadline=deadline)
        )
        if not isinstance(result, dict):
            raise ValueError("Malformed Qdrant collection result")
        return result

    def create(self, name: str, metadata: Mapping[str, JSONValue]) -> None:
        path = self._path(name)
        validate_json(metadata)
        descriptor = metadata.get("recsys")
        profile = (
            descriptor.get("embedding_profile")
            if isinstance(descriptor, dict)
            else None
        )
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("state") != "building"
            or descriptor.get("physical_collection_name") != name
            or not isinstance(profile, dict)
            or type(profile.get("dimensions")) is not int
            or cast(int, profile["dimensions"]) <= 0
        ):
            raise ValueError("Invalid Qdrant create descriptor")
        response = self._request(
            "PUT",
            f"{path}?timeout={self.operation_timeout}",
            {
                "vectors": {
                    "size": profile["dimensions"],
                    "distance": "Cosine",
                    "datatype": "float32",
                },
                "shard_number": self.shards,
                "replication_factor": self.replicas,
                "write_consistency_factor": self.write_consistency,
                "metadata": dict(metadata),
            },
        )
        if self._result(response) is not True:
            raise ValueError("Qdrant did not create the collection")

    def create_indexes(self, name: str, source: QdrantSource) -> None:
        path = self._path(name)
        for field, kind in source.indexes:
            response = self._request(
                "PUT",
                f"{path}/index?wait=true&timeout={self.operation_timeout}",
                {"field_name": field, "field_schema": kind},
            )
            self._completed(response)

    def validate_payload(self, payload: Mapping[str, JSONValue]) -> None:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"document_id", "fields"}
            or not isinstance(payload["document_id"], str)
            or not payload["document_id"].strip()
            or not isinstance(payload["fields"], dict)
        ):
            raise ValueError("Invalid Qdrant payload envelope")
        if len(canonical_bytes(payload)) > self.payload_bytes:
            raise ValueError("Qdrant payload exceeds byte budget")

    def point_batches(
        self, points: Sequence[dict[str, JSONValue]]
    ) -> list[list[dict[str, JSONValue]]]:
        batches: list[list[dict[str, JSONValue]]] = []
        batch: list[dict[str, JSONValue]] = []
        seen: set[UUID] = set()
        overhead = len(canonical_bytes({"points": []}))
        size = overhead
        for point in points:
            payload = point.get("payload")
            vector = point.get("vector")
            identifier = point.get("id")
            if not isinstance(identifier, str) or not isinstance(payload, dict):
                raise ValueError("Invalid Qdrant point")
            native_id = UUID(identifier)
            if native_id in seen:
                raise ValueError("Duplicate Qdrant point id")
            seen.add(native_id)
            self.validate_payload(payload)
            if (
                not isinstance(vector, list)
                or not vector
                or any(
                    type(v) not in (int, float) or not isfinite(cast(float, v))
                    for v in vector
                )
            ):
                raise ValueError("Invalid Qdrant dense vector")
            point_size = len(canonical_bytes(point))
            if point_size + overhead > self.upload_batch_bytes:
                raise ValueError("Qdrant point exceeds upload byte budget")
            if batch and size + 1 + point_size > self.upload_batch_bytes:
                batches.append(batch)
                batch, size = [], overhead
            size += point_size + (1 if batch else 0)
            batch.append(point)
        if batch:
            batches.append(batch)
        return batches

    def upsert(self, name: str, points: Sequence[dict[str, JSONValue]]) -> None:
        path = self._path(name)
        batches = self.point_batches(points)
        if len(batches) != 1:
            raise ValueError("Upsert requires one nonempty bounded batch")
        response = self._request(
            "PUT",
            f"{path}/points?wait=true&timeout={self.operation_timeout}",
            {"points": list(points)},
        )
        self._completed(response)

    def count(self, name: str) -> int:
        result = self._result(
            self._request("POST", self._path(name) + "/points/count", {"exact": True})
        )
        value = result.get("count") if isinstance(result, dict) else None
        if type(value) is not int or value < 0:
            raise ValueError("Invalid Qdrant exact count")
        return value

    def retrieve(self, name: str, ids: Sequence[str]) -> list[dict[str, JSONValue]]:
        if not ids or any(not isinstance(value, str) for value in ids):
            raise ValueError("Retrieve requires point UUIDs")
        for value in ids:
            UUID(value)
        result = self._result(
            self._request(
                "POST",
                self._path(name) + "/points",
                {"ids": list(ids), "with_payload": True, "with_vector": True},
            )
        )
        if not isinstance(result, list) or any(not isinstance(p, dict) for p in result):
            raise ValueError("Malformed Qdrant retrieved records")
        return cast(list[dict[str, JSONValue]], result)

    def query(
        self, name: str, body: Mapping[str, JSONValue], source: QdrantSource
    ) -> SERP:
        response = self._request("POST", self._path(name) + "/points/query", body)
        return QdrantResult(response, source).to_document_collections()

    def update_metadata(self, name: str, metadata: Mapping[str, JSONValue]) -> None:
        response = self._request(
            "PATCH",
            f"{self._path(name)}?timeout={self.operation_timeout}",
            {"metadata": dict(metadata)},
        )
        if self._result(response) is not True:
            raise ValueError("Qdrant metadata update was not confirmed")

    def check_collection(self, name: str, source: QdrantSource) -> dict[str, JSONValue]:
        validate_collection_name(name)
        self.version()
        info = self.collection(name)
        source.validate_descriptor(name, info)
        indexes = info.get("payload_schema")
        if (
            info.get("status") != "green"
            or info.get("optimizer_status") != "ok"
            or not isinstance(indexes, dict)
        ):
            raise ValueError("Qdrant collection is not ready")
        for field, kind in source.indexes:
            index = indexes.get(field)
            if not isinstance(index, dict) or index.get("data_type") != kind:
                raise ValueError("Missing or incompatible Qdrant payload index")
        return info

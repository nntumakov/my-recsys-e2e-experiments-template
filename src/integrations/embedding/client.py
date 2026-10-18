"""Bounded native TEI HTTP calls with no implicit prompts or retries."""

from collections.abc import Sequence
from http.client import HTTPException
from math import isfinite
from time import monotonic
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.domain import JSONValue
from src.domain.serialization import canonical_bytes, load_json
from src.integrations.abstract import BaseIntegration
from src.integrations.http_response import read_response_body


def embedding_body(inputs: Sequence[str], *, truncate: bool) -> dict[str, JSONValue]:
    return {
        "inputs": list(inputs),
        "normalize": True,
        "truncate": truncate,
        "truncation_direction": "right",
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TEIIntegration(BaseIntegration):
    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        response_bytes: int = 1048576,
        tokenize_response_bytes: int = 16777216,
        deadline: float | None = None,
    ) -> None:
        if not isinstance(url, str):
            raise ValueError("TEI URL must be a string")
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "?" in url
            or "#" in url
            or any(character.isspace() or ord(character) < 32 for character in url)
            or type(timeout) not in (int, float)
            or not isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Invalid TEI URL or timeout")
        parts.port
        if api_key is not None and (
            not isinstance(api_key, str)
            or not api_key
            or any(ord(character) < 32 or ord(character) > 126 for character in api_key)
        ):
            raise ValueError("Invalid TEI API key")
        if any(
            type(limit) is not int or limit <= 0
            for limit in (response_bytes, tokenize_response_bytes)
        ):
            raise ValueError("TEI response limits must be positive integers")
        if deadline is not None and (
            type(deadline) not in (int, float) or not isfinite(deadline)
        ):
            raise ValueError("TEI deadline must be finite monotonic time")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.deadline = deadline
        self.response_bytes = response_bytes
        self.tokenize_response_bytes = tokenize_response_bytes
        self._headers = {"Content-Type": "application/json"}
        if api_key is not None:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._opener = build_opener(_NoRedirect())

    def _request(
        self,
        path: str,
        body: dict[str, JSONValue] | None = None,
        *,
        limit: int | None = None,
        json_response: bool = True,
    ) -> JSONValue:
        request = Request(
            self.url + path,
            data=None if body is None else canonical_bytes(body),
            headers=self._headers,
            method="GET" if body is None else "POST",
        )
        maximum = self.response_bytes if limit is None else limit
        timeout = self.timeout
        if self.deadline is not None:
            remaining = self.deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("TEI request deadline expired")
            timeout = min(timeout, remaining)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                declared_length = response.headers.get("Content-Length")
                expected = int(declared_length) if declared_length is not None else None
                if expected is not None and not 0 <= expected <= maximum:
                    raise ValueError("TEI response exceeds byte budget")
                payload = read_response_body(response, maximum, deadline=self.deadline)
            if len(payload) > maximum:
                raise ValueError("TEI response exceeds byte budget")
            if expected is not None and len(payload) != expected:
                raise ValueError("Incomplete TEI response")
            result = load_json(payload) if json_response else None
            if self.deadline is not None and monotonic() >= self.deadline:
                raise TimeoutError("TEI response deadline expired")
            return result
        except HTTPError as exc:
            exc.close()
            raise RuntimeError(f"TEI HTTP failure ({exc.code})") from exc
        except (URLError, OSError, HTTPException, ValueError) as exc:
            raise RuntimeError("TEI transport or JSON failure") from exc

    def info(self) -> dict[str, JSONValue]:
        result = self._request("/info")
        if not isinstance(result, dict):
            raise ValueError("TEI info must be an object")
        return result

    def health(self) -> None:
        self._request("/health", json_response=False)

    def embed(self, inputs: Sequence[str], *, truncate: bool) -> list[list[float]]:
        if (
            isinstance(inputs, str)
            or not inputs
            or any(not isinstance(text, str) or not text.strip() for text in inputs)
            or type(truncate) is not bool
        ):
            raise ValueError("TEI embedding requires nonempty input texts")
        result = self._request("/embed", embedding_body(inputs, truncate=truncate))
        if not isinstance(result, list) or len(result) != len(inputs):
            raise ValueError("Malformed TEI embedding batch")
        if any(
            not isinstance(row, list)
            or not row
            or any(type(value) not in (int, float) for value in row)
            for row in result
        ):
            raise ValueError("Malformed TEI embedding vector")
        return cast(list[list[float]], result)

    def tokenize(self, inputs: Sequence[str]) -> list[list[dict[str, JSONValue]]]:
        if (
            isinstance(inputs, str)
            or len(inputs) != 1
            or not isinstance(inputs[0], str)
            or not inputs[0].strip()
        ):
            raise ValueError("TEI audit requires exactly one input text")
        result = self._request(
            "/tokenize",
            {"inputs": list(inputs), "add_special_tokens": True},
            limit=self.tokenize_response_bytes,
        )
        if not isinstance(result, list) or len(result) != 1:
            raise ValueError("Malformed TEI tokenization batch")
        tokens = result[0]
        if not isinstance(tokens, list) or not tokens:
            raise ValueError("Malformed TEI token list")
        for token in tokens:
            if (
                not isinstance(token, dict)
                or type(token.get("id")) is not int
                or cast(int, token["id"]) < 0
                or not isinstance(token.get("text"), str)
                or type(token.get("special")) is not bool
                or not {"start", "stop"} <= token.keys()
            ):
                raise ValueError("Malformed TEI token")
            start, stop = token["start"], token["stop"]
            if not (
                start is None
                and stop is None
                or type(start) is int
                and type(stop) is int
                and 0 <= start <= stop
            ):
                raise ValueError("Malformed TEI token offsets")
        return cast(list[list[dict[str, JSONValue]]], result)

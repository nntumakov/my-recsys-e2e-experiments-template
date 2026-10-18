import json
from collections.abc import Mapping
from http.client import HTTPException
from math import isfinite
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.domain import JSONValue
from src.domain.collection.abstract import BaseExternalDocumentCollections
from src.domain.collection.serp import SERP
from src.integrations.abstract import BaseIntegration
from src.integrations.opensearch.domain import VacancyOpenSearchDocument


def _finite_number(value: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


class OpenSearchResult(BaseExternalDocumentCollections):
    def __init__(self, response: Mapping[str, JSONValue]) -> None:
        self.response = response

    def to_document_collections(self) -> SERP:
        shards = self.response.get("_shards")
        timed_out = self.response.get("timed_out")
        hits = self.response.get("hits")
        if (
            type(timed_out) is not bool
            or not isinstance(shards, dict)
            or type(shards.get("failed")) is not int
            or cast(int, shards["failed"]) < 0
            or not isinstance(hits, dict)
            or not isinstance(hits.get("hits"), list)
        ):
            raise ValueError("Malformed OpenSearch response")
        if timed_out or shards["failed"] != 0:
            raise ValueError("Incomplete OpenSearch response")
        documents = []
        for hit in cast(list[JSONValue], hits["hits"]):
            if not isinstance(hit, dict):
                raise ValueError("Malformed OpenSearch hit")
            documents.append(VacancyOpenSearchDocument(hit).to_document())
        return SERP(documents)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenSearchIntegration(BaseIntegration):
    def __init__(
        self, url: str, *, authorization: str | None = None, timeout: float = 30.0
    ) -> None:
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "?" in url
            or "#" in url
            or any(char.isspace() for char in url)
            or not isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Invalid OpenSearch URL or timeout")
        parts.port  # Validate the optional port before the session starts.
        if authorization is not None and any(c in authorization for c in "\r\n"):
            raise ValueError("Invalid Authorization header")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if authorization is not None:
            self._headers["Authorization"] = authorization
        self._opener = build_opener(_NoRedirect())

    def search(self, *, index: str, body: Mapping[str, JSONValue]) -> OpenSearchResult:
        if not index.strip() or index in (".", ".."):
            raise ValueError("Invalid search index")
        request = Request(
            f"{self.url}/{quote(index, safe='')}/_search",
            data=json.dumps(dict(body), ensure_ascii=False, allow_nan=False).encode(),
            headers=self._headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = json.loads(
                    response.read().decode("utf-8"),
                    parse_float=_finite_number,
                    parse_constant=_finite_number,
                )
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
        except HTTPError as exc:
            exc.close()
            raise RuntimeError(f"OpenSearch HTTP failure ({exc.code})") from exc
        except (URLError, OSError, HTTPException, ValueError) as exc:
            raise RuntimeError("OpenSearch transport or JSON failure") from exc
        return OpenSearchResult(payload)

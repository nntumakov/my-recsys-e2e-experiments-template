import json
import re
from collections.abc import Iterator
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate

from src.domain.embedding import EmbeddingProfile


@pytest.fixture
def search_response() -> dict[str, Any]:
    return {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {
            "hits": [
                {
                    "_id": "second",
                    "_score": 3.5,
                    "_source": {"title": "Разработчик", "extra": {"tags": ["python"]}},
                },
                {"_id": "first", "_score": None, "_source": {"score": "source field"}},
            ]
        },
    }


@pytest.fixture
def search_server(search_response) -> Iterator[tuple[str, list, list]]:
    requests: list[dict] = []
    replies = [(200, search_response, {})]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                }
            )
            status, body, headers = replies.pop(0) if len(replies) > 1 else replies[0]
            content = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/proxy", requests, replies
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


class VectorHTTPState:
    """Stateful native TEI/Qdrant HTTP fixture, with explicit failure boundaries."""

    def __init__(self):
        self.url = ""
        self.requests: list[dict] = []
        self.collections: dict[str, dict] = {}
        self.points: dict[str, dict] = {}
        self.extra_metadata: dict = {}
        self.failure: str | None = None
        self.query_calls = 0
        self.upload_calls = 0
        self.fail_query_number: int | None = None
        self.vector = [1.0] + [0.0] * 767
        profile = EmbeddingProfile()
        self.tei_info = {
            "model_id": profile.model_id,
            "model_sha": profile.revision,
            "model_dtype": "float16",
            "model_type": {"embedding": {"pooling": "mean"}},
            "version": "1.9.4",
            "max_input_length": 512,
            "auto_truncate": False,
            "max_batch_tokens": 4096,
            "max_client_batch_size": 8,
            "max_concurrent_requests": 32,
        }

    @staticmethod
    def ok(result):
        return 200, {"status": "ok", "result": result, "time": 0.001}

    @staticmethod
    def matches(point, condition):
        value = point["payload"]
        for segment in condition["key"].split("."):
            value = value.get(segment) if isinstance(value, dict) else None
        if value is None:
            return False
        if "match" in condition:
            expected = condition["match"]["value"]
            return type(value) is type(expected) and value == expected
        return (
            type(value) in (int, float)
            and condition["range"]["gte"] <= value <= condition["range"]["lte"]
        )

    def respond(self, method, path, body):
        path = urlsplit(path).path
        if method == "GET" and path == "/":
            return 200, {"version": "1.19.2"}
        if method == "GET" and path == "/info":
            return 200, self.tei_info
        if method == "GET" and path == "/health":
            return 200, {}
        if method == "POST" and path == "/embed":
            if self.collections and (
                self.failure == "inference"
                or self.failure == "second_inference"
                and any(self.points.values())
            ):
                return 200, [self.vector, [0.0]]
            return 200, [self.vector for _ in body["inputs"]]
        if method == "POST" and path == "/tokenize":
            return 200, [
                [
                    {
                        "id": 0,
                        "text": "<s>",
                        "special": True,
                        "start": None,
                        "stop": None,
                    }
                ]
                for _ in body["inputs"]
            ]

        parts = path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != "collections":
            return 404, {"status": "error"}
        name = parts[1]
        operation = "/".join(parts[2:])
        if method == "PUT" and not operation:
            if name in self.collections:
                return 409, {"status": "already exists"}
            self.collections[name] = {
                "status": "green",
                "optimizer_status": "ok",
                # Intentionally approximate; readiness must use exact count instead.
                "points_count": 999,
                "indexed_vectors_count": 0,
                "payload_schema": {},
                "config": {
                    "params": {k: v for k, v in body.items() if k != "metadata"},
                    "metadata": {**deepcopy(self.extra_metadata), **body["metadata"]},
                    "hnsw_config": {"m": 16},
                    "optimizer_config": {"indexing_threshold": 10000},
                    "quantization_config": None,
                },
            }
            self.points[name] = {}
            return self.ok(True)
        if name not in self.collections:
            return 404, {"status": "not found"}
        collection = self.collections[name]
        ready = collection["config"]["metadata"]["recsys"]["state"] == "ready"
        if method == "GET" and not operation:
            if self.failure == "ready_readback" and ready:
                return 500, {"status": "failed after publication"}
            result = deepcopy(collection)
            if self.failure == "health":
                result["status"] = "red"
                result["optimizer_status"] = {"error": "optimizer failed"}
            if self.failure == "yellow":
                result["status"] = "yellow"
            return self.ok(result)
        if method == "PUT" and operation == "index":
            if self.points[name]:
                return 400, {"status": "indexes must precede points"}
            collection["payload_schema"][body["field_name"]] = {
                "data_type": body["field_schema"]
            }
            return self.ok({"status": "completed", "operation_id": 1})
        if method == "PUT" and operation == "points":
            self.upload_calls += 1
            if self.failure == "upsert" or (
                self.failure == "second_upsert" and self.upload_calls == 2
            ):
                return 500, {"status": "failed upsert"}
            self.points[name].update({p["id"]: deepcopy(p) for p in body["points"]})
            return self.ok({"status": "completed", "operation_id": 2})
        if method == "POST" and operation == "points/count":
            assert body == {"exact": True}
            count = len(self.points[name]) + (self.failure == "count")
            return self.ok({"count": count})
        if method == "POST" and operation == "points":
            assert body["with_payload"] is True and body["with_vector"] is True
            points = [deepcopy(self.points[name][key]) for key in body["ids"]]
            if self.failure == "readback":
                points[-1]["payload"]["fields"]["title"] = "corrupted"
            elif self.failure == "readback_type":
                points[-1]["payload"]["fields"]["grade"] = True
            elif self.failure == "readback_missing":
                points.pop()
            elif self.failure == "readback_vector":
                points[-1]["vector"] = [0.0] * 768
            return self.ok(points)
        if method == "POST" and operation == "points/query":
            self.query_calls += 1
            conditions = body.get("filter", {}).get("must", [])
            points = [
                deepcopy(point) | {"version": 0, "score": 0.75}
                for point in sorted(
                    self.points[name].values(),
                    key=lambda point: point["payload"]["document_id"].encode("utf-8"),
                )
                if all(self.matches(point, c) for c in conditions)
            ][: body["limit"]]
            if self.failure == "smoke":
                points = []
            elif self.failure == "smoke_wrong_filter":
                points = [
                    deepcopy(next(iter(self.points[name].values())))
                    | {"version": 0, "score": 0.75}
                ]
            if self.query_calls == self.fail_query_number:
                points[-1]["payload"]["fields"] = []
            if not body.get("with_vector"):
                for point in points:
                    point.pop("vector")
            return self.ok({"points": points})
        if method == "PATCH" and not operation:
            if self.failure == "ready":
                return 500, {"status": "failed publication"}
            # Native metadata PATCH merges at the top level only.
            collection["config"]["metadata"].update(deepcopy(body["metadata"]))
            return self.ok(True)
        return 405, {"status": "unsupported operation"}


@pytest.fixture
def vector_stack():
    state = VectorHTTPState()

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            state.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "body": body,
                    "api_key": self.headers.get("api-key"),
                    "authorization": self.headers.get("Authorization"),
                }
            )
            status, payload = state.respond(self.command, self.path, body)
            content = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = do_POST = do_PUT = do_PATCH = respond

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    worker.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.fixture
def study_stack(vector_stack, tmp_path, monkeypatch):
    """Composed study HTTP fixture: controlled scores, no analyzer/model execution."""
    records = [
        {
            "id": "A",
            "fields": {
                "title": "Комплектовщик <script>bad()</script>",
                "description": "ТСД сбор заказов\n" + "Полная карточка " * 90,
                "city": "Москва",
                "active": True,
                "extra": {"typed": [1, True, 1.0]},
            },
        },
        {
            "id": "B",
            "fields": {
                "title": "Комплектовщик",
                "description": "ТСД без опыта 2/2",
                "city": "Москва",
                "active": True,
            },
        },
        {
            "id": "C",
            "fields": {
                "title": "Комплектовщик",
                "description": "ТСД без опыта 2/2",
                "city": "Санкт-Петербург",
                "active": True,
            },
        },
    ]
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    site = tmp_path / "study-site.yaml"
    site.write_text(
        (
            Path(__file__).resolve().parents[1] / "examples/study/site.example.yaml"
        ).read_text()
    )
    original = vector_stack.respond
    os_requests = []

    def predicate(query, fields):
        # Only explicit boolean preference/equality predicates are evaluated here.
        if "term" in query:
            name, value = next(iter(query["term"].items()))
            name = name.removesuffix(".keyword")  # This fixture declares this mapping.
            return type(fields.get(name)) is type(value) and fields.get(name) == value
        if "match_phrase" in query:
            name, spec = next(iter(query["match_phrase"].items()))
            text = spec["query"] if isinstance(spec, dict) else spec
            return text.casefold() in str(fields.get(name, "")).casefold()
        if "bool" in query:
            clause = query["bool"]
            if not all(predicate(q, fields) for q in clause.get("filter", [])):
                return False
            if any(predicate(q, fields) for q in clause.get("must_not", [])):
                return False
            if not all(predicate(q, fields) for q in clause.get("must", [])):
                return False
            should = clause.get("should", [])
            minimum = clause.get("minimum_should_match", 0)
            if type(minimum) is int:
                return sum(predicate(q, fields) for q in should) >= minimum
        # The fixture controls topical admission/scores; it does not implement BM25.
        return True

    def names(query, fields):
        found = []
        if isinstance(query, dict):
            if "_name" in query and predicate(
                {"bool": query} if "filter" in query else query, fields
            ):
                found.append(query["_name"])
            for value in query.values():
                found.extend(names(value, fields))
        elif isinstance(query, list):
            for value in query:
                found.extend(names(value, fields))
        return found

    def respond(method, native_path, body):
        if method == "POST" and native_path == "/vacancies/_search":
            os_requests.append({"body": deepcopy(body), "path": native_path})
            query = body["query"]
            functions = query.get("function_score")
            core = functions["query"] if functions else query
            hits = []
            for offset, record in enumerate(records):
                fields = record["fields"]
                if predicate(core, fields):
                    score = 8.0 - offset
                    if functions:
                        score *= sum(
                            f["weight"]
                            for f in functions["functions"]
                            if predicate(f["filter"], fields)
                        )
                    hits.append(
                        {
                            "_id": record["id"],
                            "_score": score,
                            "_source": deepcopy(fields),
                            "matched_queries": names(query, fields),
                        }
                    )
            hits.sort(key=lambda h: -h["_score"])
            return 200, {
                "timed_out": False,
                "_shards": {"failed": 0},
                "took": 1,
                "hits": {"hits": hits[: body["size"]]},
            }
        if method == "POST" and native_path == "/tokenize":
            results = []
            for text in body["inputs"]:
                tokens = [
                    {
                        "id": 0,
                        "text": "<s>",
                        "special": True,
                        "start": None,
                        "stop": None,
                    }
                ]
                for match in re.finditer(r"\S+", text):
                    tokens.append(
                        {
                            "id": 1,
                            "text": "synthetic",
                            "special": False,
                            "start": len(text[: match.start()].encode("utf-8")),
                            "stop": len(text[: match.end()].encode("utf-8")),
                        }
                    )
                tokens.append(
                    {
                        "id": 2,
                        "text": "</s>",
                        "special": True,
                        "start": None,
                        "stop": None,
                    }
                )
                results.append(tokens)
            return 200, results
        if method == "POST" and native_path.endswith("/points/query"):
            vector_stack.query_calls += 1
            collection = native_path.split("/")[2]
            excluded = {
                identifier
                for c in body.get("filter", {}).get("must_not", [])
                for identifier in c["match"]["any"]
            }
            candidates = []
            for point in vector_stack.points[collection].values():
                if point["payload"]["document_id"] in excluded:
                    continue
                conditions = body.get("filter", {}).get("must", [])
                if not all(
                    point["id"] in c["has_id"]
                    if "has_id" in c
                    else vector_stack.matches(point, c)
                    for c in conditions
                ):
                    continue
                hit = deepcopy(point) | {"version": 0, "score": 0.75}
                if not body.get("with_vector"):
                    hit.pop("vector", None)
                candidates.append(hit)
            candidates.sort(key=lambda p: p["payload"]["document_id"])
            if (
                vector_stack.query_calls == vector_stack.fail_query_number
                and candidates
            ):
                candidates[-1]["payload"]["fields"] = []
            return vector_stack.ok({"points": candidates[: body["limit"]]})
        return original(method, native_path, body)

    monkeypatch.setattr(vector_stack, "respond", respond)
    overrides = [
        f"connection.url={vector_stack.url}",
        "study.fulltext_source.index=vacancies",
        f"qdrant.url={vector_stack.url}",
        f"embedding.client.url={vector_stack.url}",
        "embedding.runtime.image=fixture@sha256:" + "a" * 64,
        f"corpus.path={path}",
        "corpus.identity=fixture-study-v1",
        "study.collections.whole=whole",
        "study.collections.title=title",
        "study.collections.body=body",
        "study.collections.chunk=chunks",
        "study.registered=[f1,f2,f3,f4,f5,e1,e2,e3,e4,e5]",
        "experiment.exporters=[]",
    ]
    root = Path(__file__).resolve().parents[1]
    build_configs = {}
    for kind in ("whole", "title", "body", "chunk"):
        selection = "chunk-index" if kind == "chunk" else "index"
        with initialize_config_dir(
            config_dir=str(root / "configs"), version_base="1.3"
        ):
            config = compose(
                config_name="study",
                overrides=[
                    *overrides,
                    f"experiments/vacancy-search/study@experiment={selection}",
                    f"study.build_kind={kind}",
                ],
            )
        index = instantiate(config.experiment, _convert_="all")
        index.run()
        build_configs[kind] = config
    with initialize_config_dir(config_dir=str(root / "configs"), version_base="1.3"):
        cfg = compose(config_name="study", overrides=overrides)
    study = instantiate(cfg.experiment, _convert_="all")
    vector_stack.requests.clear()
    os_requests.clear()
    return SimpleNamespace(
        study=study,
        cfg=cfg,
        site_config=site,
        overrides=overrides,
        os_requests=os_requests,
        stack=vector_stack,
        build_configs=build_configs,
        records=records,
    )

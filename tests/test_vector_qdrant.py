import json
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import FrozenInstanceError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Event, Thread
from time import monotonic
from typing import cast
from unittest.mock import Mock
from uuid import NAMESPACE_URL, uuid5

import pytest

from src.components.embedding.encoder import TextEmbedder
from src.components.embedding.vacancy import VacancyText
from src.core.registry import BasicTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.qdrant.search import VectorSearchTransformer
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.embedding import Embedding, EmbeddingProfile
from src.domain.request.abstract import BaseFilter
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter
from src.domain.serialization import canonical_bytes
from src.experiments.direct.search import SearchExperiment
from src.integrations.embedding.client import TEIIntegration
from src.integrations.qdrant.client import QdrantIntegration, validate_collection_name
from src.integrations.qdrant.domain import QdrantResult
from src.integrations.qdrant.source import QdrantSource


@pytest.mark.parametrize("integration", ["tei", "qdrant"])
@pytest.mark.parametrize("chunked", [False, True])
def test_readiness_deadline_interrupts_a_continuously_streaming_body(
    integration, chunked
):
    stopped = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = canonical_bytes({"status": "ok", "result": {}}) + b" " * 100
            self.send_response(200)
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
                # A valid chunk extension keeps read1's internal readline busy.
                body = b"1;extension=" + b"a" * 100 + b"\r\n \r\n0\r\n\r\n"
            else:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    if stopped.wait(0.01):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}"
    started = monotonic()
    deadline = started + 0.15
    try:
        with pytest.raises(RuntimeError, match="transport or JSON") as error:
            if integration == "tei":
                TEIIntegration(url, deadline=deadline).health()
            else:
                QdrantIntegration(url).collection("jobs", deadline=deadline)
        assert isinstance(error.value.__cause__, TimeoutError)
        assert monotonic() - started < 0.8
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.fixture
def vector_source():
    return QdrantSource(
        EmbeddingProfile(),
        VacancyText(),
        "vacancies-test-v1",
        {
            "city": {"path": ["location", "city"], "type": "keyword"},
            "active": {"path": ["active"], "type": "bool"},
            "grade": {"path": ["grade"], "type": "integer"},
            "rate": {"path": ["rate"], "type": "float"},
        },
        runtime={"version": "1.9.4", "image": "tei:1.9.4@sha256:" + "a" * 64},
        vector={"size": 768, "distance": "Cosine"},
    )


def collection_info(source, *, state="ready", name="vacancies-g1"):
    verification = {
        "protocol": "full-readback-v1",
        "exact_count": 2,
        "read_back_count": 2,
        "status": "green",
        "optimizer_status": "ok",
        "indexes": dict(source.indexes),
        "smoke": True,
    }
    return {
        "status": "green",
        "optimizer_status": "ok",
        "points_count": 999,
        "indexed_vectors_count": 0,
        "payload_schema": {key: {"data_type": kind} for key, kind in source.indexes},
        "config": {
            "params": {
                "vectors": {"size": 768, "distance": "Cosine"},
                "shard_number": 1,
                "replication_factor": 1,
                "write_consistency_factor": 1,
            },
            "metadata": source.metadata(
                name,
                expected_points=2,
                corpus_sha256="b" * 64,
                runtime=source.runtime
                | {"qdrant_version": "1.19.2", "inference": tei_info()},
                state=state,
                verification=verification if state == "ready" else None,
            ),
        },
    }


def vacancy_point(source, identifier=" w001 ", score=0.7):
    return {
        "id": source.point_id(identifier),
        "version": 0,
        "score": score,
        "payload": {
            "document_id": identifier,
            "fields": {
                "title": "Комплектовщик",
                "location": {"city": "Москва"},
                "location.city": "literal key is independent",
                "active": True,
                "grade": 2**63 - 1,
                "rate": 0.75,
                "extra": [None, {"unsigned": 2**64 - 1, "integer": 1, "float": 1.0}],
            },
        },
    }


def ok(result):
    return {"status": "ok", "time": 0.002, "result": result}


def tei_info():
    profile = EmbeddingProfile()
    return {
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


@pytest.fixture
def vector_http() -> Iterator[tuple[str, list, list]]:
    requests, replies = [], []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            size = int(self.headers.get("Content-Length", "0"))
            requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "api_key": self.headers.get("api-key"),
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(size)) if size else None,
                }
            )
            status, payload, headers = replies.pop(0)
            data = (
                payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            )
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            if "Content-Length" not in headers:
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_PATCH = respond

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/prefix", requests, replies
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_http_composed_search_preserves_fields_and_checks_readiness_every_time(
    vector_source, vector_http
):
    url, requests, replies = vector_http
    integration = QdrantIntegration(url, api_key="qdrant-key")
    embedder = TextEmbedder(
        EmbeddingProfile(), TEIIntegration(url, api_key="tei-key"), limits={}
    )
    leaf = VectorSearchTransformer(
        integration, embedder, vector_source, "vacancies-g1", candidate_depth=7
    )
    registry = BasicTransformerRegistry({"vector": leaf})
    experiment = SearchExperiment(registry, "vector")
    vector = [1.0] + [0.0] * 767
    point = vacancy_point(vector_source)
    for points in ([point], []):
        replies.extend(
            (200, payload, {})
            for payload in (
                {"version": "1.19.2"},
                ok(collection_info(vector_source)),
                tei_info(),
                [vector],
                ok({"points": points}),
            )
        )
    result = experiment.search(
        "  Комплектовщик  ТСД ",
        context={"city": "Москва"},
        filters=(
            UserPropertyFilter("city", "city"),
            TermFilter("active", True),
            TermFilter("grade", 2**63 - 1),
            TermFilter("rate", 0.75),
        ),
    )
    assert result.items[0].id == " w001 "
    assert result.items[0].fields == point["payload"]["fields"]
    assert result.items[0].score == 0.7
    assert experiment.search("токарь").items == []
    assert [r["path"] for r in requests] == [
        "/prefix/",
        "/prefix/collections/vacancies-g1",
        "/prefix/info",
        "/prefix/embed",
        "/prefix/collections/vacancies-g1/points/query",
    ] * 2
    assert requests[0]["api_key"] == "qdrant-key"
    assert requests[0]["authorization"] is None
    assert requests[2]["authorization"] == "Bearer tei-key"
    assert requests[2]["api_key"] is None
    assert requests[3]["body"]["inputs"] == ["query: Комплектовщик ТСД"]
    assert requests[4]["body"] == {
        "query": vector,
        "params": {"exact": False, "indexed_only": False, "hnsw_ef": 128},
        "limit": 7,
        "with_payload": True,
        "with_vector": False,
        "filter": {
            "must": [
                {"key": "fields.location.city", "match": {"value": "Москва"}},
                {"key": "fields.active", "match": {"value": True}},
                {"key": "fields.grade", "match": {"value": 2**63 - 1}},
                {"key": "fields.rate", "range": {"gte": 0.75, "lte": 0.75}},
            ]
        },
    }
    assert "filter" not in requests[9]["body"]
    assert not replies


@pytest.mark.parametrize(
    "filters, context",
    [
        ([], {}),
        ([QueryFilter(" ")], {}),
        ([QueryFilter("a"), QueryFilter("b")], {}),
        ([QueryFilter("a"), BaseFilter()], {}),
        ([QueryFilter("a"), TermFilter("unknown", "v")], {}),
        ([QueryFilter("a"), TermFilter("grade", True)], {}),
        ([QueryFilter("a"), TermFilter("grade", 1.0)], {}),
        ([QueryFilter("a"), TermFilter("grade", 2**63)], {}),
        ([QueryFilter("a"), TermFilter("city", None)], {}),
        ([QueryFilter("a"), TermFilter("rate", 2**53 + 1)], {}),
        ([QueryFilter("a"), TermFilter("rate", float("nan"))], {}),
        ([QueryFilter("a"), UserPropertyFilter("city", "city")], {}),
        ([QueryFilter("a"), UserPropertyFilter("city", "city")], {"city": []}),
    ],
)
def test_invalid_request_does_not_call_network_or_mutate(
    vector_source, filters, context
):
    integration = Mock(spec=QdrantIntegration)
    embedder = TextEmbedder(vector_source.profile, Mock(spec=TEIIntegration), {})
    leaf = VectorSearchTransformer(integration, embedder, vector_source, "vacancies-g1")
    previous = SERP([Vacancy("previous")])
    data = PipelineData(context=context, items=previous, filters=filters)
    before = data.copy(deep=True)
    with pytest.raises(ValueError):
        leaf.transform(data, registry=BasicTransformerRegistry({}))
    assert data.items is previous
    assert data.context == before.context and data.filters == before.filters
    assert data.branches == before.branches
    assert (
        not integration.mock_calls and not cast(Mock, embedder.integration).mock_calls
    )


def test_pure_immutable_plan_and_explicit_embedding_body(vector_source):
    integration = Mock(spec=QdrantIntegration)
    embedder = TextEmbedder(vector_source.profile, Mock(spec=TEIIntegration), {})
    leaf = VectorSearchTransformer(integration, embedder, vector_source, "vacancies-g1")
    data = PipelineData(
        filters=[QueryFilter(" Комплектовщик   ТСД "), TermFilter("rate", 8)]
    )
    plan = leaf.prepare(data)
    assert plan.original_query == " Комплектовщик   ТСД "
    assert plan.model_input == "query: Комплектовщик ТСД"
    with pytest.raises(FrozenInstanceError):
        setattr(plan, "model_input", "changed")
    view = cast(dict, plan.to_dict())
    view["conditions"][0]["range"]["gte"] = 99
    assert cast(dict, plan.to_dict())["conditions"][0]["range"]["gte"] == 8
    embedding = Embedding(vector_source.profile.id, (1.0,) + (0.0,) * 767)
    assert plan.body(embedding)["query"] == list(embedding.values)
    with pytest.raises(ValueError, match="embedding"):
        plan.body(Embedding("0" * 64, (1.0,)))
    assert (
        not integration.mock_calls and not cast(Mock, embedder.integration).mock_calls
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"score": True},
        {"score": None},
        {"score": float("inf")},
        {"version": True},
        {"version": -1},
        {"id": 42},
        {"id": "00000000-0000-0000-0000-000000000000"},
        {"payload": {"document_id": "w003", "fields": {}}},
        {"payload": {"document_id": " ", "fields": {}}},
        {"payload": {"document_id": "w002", "fields": []}},
    ],
)
def test_malformed_last_hit_is_atomic_and_manual_retry_works(vector_source, patch):
    good = vacancy_point(vector_source)
    malformed = vacancy_point(vector_source, "w002") | patch
    integration = Mock(spec=QdrantIntegration)
    integration.query.side_effect = lambda name, body, source: QdrantResult(
        ok({"points": [good, malformed]}), source
    ).to_document_collections()
    embedder = Mock(spec=TextEmbedder)
    embedder.profile = vector_source.profile
    embedder.prepare_query.return_value = "query: vacancy"
    embedder.encode_query.return_value = Embedding(vector_source.profile.id, (1.0,))
    leaf = VectorSearchTransformer(integration, embedder, vector_source, "vacancies-g1")
    previous = SERP([Vacancy("previous")])
    data = PipelineData(items=previous, filters=[QueryFilter("vacancy")])
    with pytest.raises(ValueError):
        leaf.transform(data, registry=BasicTransformerRegistry({}))
    assert data.items is previous
    integration.query.side_effect = None
    integration.query.return_value = QdrantResult(
        ok({"points": [good]}), vector_source
    ).to_document_collections()
    assert (
        leaf.transform(data, registry=BasicTransformerRegistry({})).items.items[0].id
        == " w001 "
    )


def test_duplicates_and_response_shape_are_not_empty_success(vector_source):
    point = vacancy_point(vector_source)
    for response in (
        ok([point]),
        ok({}),
        {"result": {"points": []}},
        ok({"points": [point, point]}),
    ):
        with pytest.raises(ValueError):
            QdrantResult(cast(dict, response), vector_source).to_document_collections()
    result = QdrantResult(
        ok({"points": [point]}), vector_source
    ).to_document_collections()
    cast(dict, result.items[0].fields["location"])["city"] = "changed"
    assert point["payload"]["fields"]["location"]["city"] == "Москва"


def test_source_identity_paths_and_json_types(vector_source):
    expected_namespace = uuid5(
        uuid5(NAMESPACE_URL, "urn:recsys-experiments:vacancy:v1"), "vacancies-test-v1"
    )
    assert vector_source.point_id(" w001 ") == str(uuid5(expected_namespace, " w001 "))
    assert vector_source.point_id(" w001 ") != vector_source.point_id("w001")
    original = vector_source.id
    declarations = vector_source.filter_fields
    declarations["city"]["path"] = ["other"]
    assert vector_source.id == original
    fields = {"location": None, "active": None, "rate": 2**53, "x.y": [1, True]}
    vector_source.validate_fields(fields)
    assert vector_source.field_value(fields, "city") is None
    assert vector_source.field_value(fields, "rate") == 2**53
    for wrong in (
        {"location": []},
        {"grade": 1.0},
        {"active": 1},
        {"rate": 2**53 + 1},
        {"rate": True},
    ):
        with pytest.raises(ValueError):
            vector_source.validate_fields(wrong)


@pytest.mark.parametrize("path", [[], ["a.b"], ["a[]"], ["ё"], [0], ["a", "*"]])
def test_unsupported_filter_paths_rejected(path):
    with pytest.raises(ValueError):
        QdrantSource(
            {},
            {},
            "corpus",
            {"x": {"path": path, "type": "keyword"}},
            runtime={},
            vector={"size": 768, "distance": "Cosine"},
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(state="building"),
        lambda d: d.update(physical_collection_name="alias"),
        lambda d: d.update(schema_version=True),
        lambda d: d.update(expected_points=True),
        lambda d: d.update(embedding_profile_sha256="0" * 64),
        lambda d: d["embedding_profile"].update(query_prefix="different: "),
        lambda d: d["source_profile"].update(corpus_identity="other"),
        lambda d: d["runtime"].update(image="different@sha256:" + "a" * 64),
        lambda d: d["runtime"].pop("inference"),
        lambda d: d["runtime"].pop("qdrant_version"),
        lambda d: d["runtime"].update(qdrant_version="1.16.0"),
        lambda d: d["runtime"]["inference"].update(model_sha="0" * 40),
        lambda d: d["runtime"]["inference"].update(max_input_length=True),
        lambda d: d["runtime"]["inference"].update(model_dtype="float32"),
        lambda d: d["runtime"]["inference"].update(
            model_type={"embedding": {"pooling": "cls"}}
        ),
        lambda d: d["verification"].update(read_back_count=1),
        lambda d: d["verification"].update(smoke=1),
        lambda d: d.pop("verification"),
    ],
)
def test_descriptor_mismatch_fails_before_encode(vector_source, change, vector_http):
    url, requests, replies = vector_http
    info = collection_info(vector_source)
    change(info["config"]["metadata"]["recsys"])
    replies.extend([(200, {"version": "1.19.2"}, {}), (200, ok(info), {})])
    embedder = TextEmbedder(vector_source.profile, Mock(spec=TEIIntegration), {})
    leaf = VectorSearchTransformer(
        QdrantIntegration(url), embedder, vector_source, "vacancies-g1"
    )
    data = PipelineData(items=SERP([Vacancy("old")]), filters=[QueryFilter("vacancy")])
    with pytest.raises(ValueError):
        leaf.transform(data, registry=BasicTransformerRegistry({}))
    assert len(requests) == 2 and not cast(Mock, embedder.integration).mock_calls
    assert data.items.items[0].id == "old"


@pytest.mark.parametrize(
    "change",
    [
        lambda i: i["config"]["params"].update(
            vectors={"text": {"size": 768, "distance": "Cosine"}}
        ),
        lambda i: i["config"]["params"]["vectors"].update(size=384),
        lambda i: i["config"]["params"]["vectors"].update(distance="Dot"),
        lambda i: i["config"]["params"]["vectors"].update(datatype="float16"),
        lambda i: i["config"].update(quantization_config={"scalar": {"type": "int8"}}),
        lambda i: i["config"]["params"].update(replication_factor=2),
        lambda i: i.update(status="yellow"),
        lambda i: i.update(optimizer_status={"error": "failed"}),
        lambda i: i["payload_schema"].pop("fields.grade"),
        lambda i: i["payload_schema"]["fields.grade"].update(data_type="float"),
    ],
)
def test_collection_schema_and_health_are_required(vector_source, vector_http, change):
    url, requests, replies = vector_http
    info = collection_info(vector_source)
    change(info)
    replies.extend([(200, {"version": "1.19.2"}, {}), (200, ok(info), {})])
    with pytest.raises(ValueError):
        QdrantIntegration(url).check_collection("vacancies-g1", vector_source)
    assert len(requests) == 2


def test_ready_descriptor_requires_verified_state_before_publication(vector_source):
    with pytest.raises(ValueError, match="verification"):
        vector_source.metadata(
            "vacancies-g1",
            expected_points=2,
            corpus_sha256="b" * 64,
            runtime=vector_source.runtime,
            state="ready",
        )


def test_native_id_collision_in_response_is_rejected(vector_source, monkeypatch):
    first, second = vacancy_point(vector_source), vacancy_point(vector_source, "second")
    second["id"] = first["id"]
    monkeypatch.setattr(vector_source, "point_id", lambda identifier: first["id"])
    with pytest.raises(ValueError, match="Duplicate"):
        QdrantResult(
            ok({"points": [first, second]}), vector_source
        ).to_document_collections()


def test_conflicting_declarations_for_same_native_path_fail(vector_source):
    with pytest.raises(ValueError, match="Conflicting"):
        QdrantSource(
            vector_source.profile,
            vector_source.recipe,
            "corpus",
            {
                "one": {"path": ["value"], "type": "keyword"},
                "other": {"path": ["value"], "type": "integer"},
            },
            runtime=vector_source.runtime,
            vector={"size": vector_source.profile.dimensions, "distance": "Cosine"},
        )


def test_native_collection_operations_and_full_metadata_patch(
    vector_source, vector_http
):
    url, requests, replies = vector_http
    integration = QdrantIntegration(url)
    info = collection_info(vector_source, state="building")
    metadata = info["config"]["metadata"]
    point = vacancy_point(vector_source)
    point.pop("version")
    point.pop("score")
    point["vector"] = [1.0] + [0.0] * 767
    replies.extend([(200, ok(True), {})])
    integration.create("vacancies-g1", metadata)
    replies.extend([(200, ok({"status": "completed"}), {})] * 4)
    integration.create_indexes("vacancies-g1", vector_source)
    replies.extend([(200, ok({"status": "completed", "operation_id": 5}), {})])
    integration.upsert("vacancies-g1", [point])
    replies.extend([(200, ok({"count": 2}), {}), (200, ok([point]), {})])
    assert integration.count("vacancies-g1") == 2
    assert integration.retrieve("vacancies-g1", [point["id"]]) == [point]
    ready = collection_info(vector_source)["config"]["metadata"]
    replies.extend([(200, ok(True), {})])
    integration.update_metadata("vacancies-g1", ready)
    assert requests[0]["body"]["vectors"] == {
        "size": 768,
        "distance": "Cosine",
        "datatype": "float32",
    }
    assert requests[1]["body"] == {
        "field_name": "fields.location.city",
        "field_schema": "keyword",
    }
    assert requests[5]["path"].endswith("/points?wait=true&timeout=60")
    assert requests[6]["body"] == {"exact": True}
    assert requests[7]["body"] == {
        "ids": [point["id"]],
        "with_payload": True,
        "with_vector": True,
    }
    assert requests[8]["method"] == "PATCH"
    assert requests[8]["body"] == {"metadata": ready}
    assert "wait=" not in requests[8]["path"]
    assert (
        vector_source.validate_descriptor("vacancies-g1", info, state="building")[
            "state"
        ]
        == "building"
    )


@pytest.mark.parametrize("result", [{"status": "acknowledged"}, True, {}, None])
def test_noncompleted_mutation_fails(vector_source, vector_http, result):
    url, _, replies = vector_http
    replies.append((200, ok(result), {}))
    with pytest.raises(ValueError):
        QdrantIntegration(url).create_indexes("vacancies-g1", vector_source)


def test_point_wire_batches_and_payload_bounds(vector_source):
    point = vacancy_point(vector_source)
    point: dict = {"id": point["id"], "payload": point["payload"], "vector": [1.0, 0.0]}
    second = deepcopy(point)
    second["id"] = vector_source.point_id("w002")
    second["payload"]["document_id"] = "w002"
    budget = max(len(canonical_bytes({"points": [p]})) for p in (point, second))
    client = QdrantIntegration("http://example.invalid", upload_batch_bytes=budget)
    assert client.point_batches([point, second]) == [[point], [second]]
    with pytest.raises(ValueError, match="Duplicate"):
        client.point_batches([point, point])
    with pytest.raises(ValueError, match="payload"):
        QdrantIntegration("http://example.invalid", payload_bytes=10).validate_payload(
            point["payload"]
        )
    with pytest.raises(ValueError, match="upload"):
        QdrantIntegration(
            "http://example.invalid", upload_batch_bytes=10
        ).point_batches([point])


@pytest.mark.parametrize(
    "body",
    [
        b"SECRET",
        b'{"result":NaN}',
        b'{"version":"1.19.2","version":"other"}',
        b"[]",
        b"x" * 200,
    ],
)
def test_bounded_strict_json_failure_is_safe(vector_http, body):
    url, _, replies = vector_http
    replies.append((200, body, {}))
    with pytest.raises(RuntimeError) as error:
        QdrantIntegration(url, response_bytes=100).version()
    assert "SECRET" not in str(error.value)
    assert error.value.__cause__ is not None


@pytest.mark.parametrize("declared", ["1001", "999", "-1", "not-an-integer"])
def test_declared_response_size_and_truncated_json_body_fail(vector_http, declared):
    url, requests, replies = vector_http
    replies.append((200, {"version": "1.19.2"}, {"Content-Length": declared}))
    with pytest.raises(RuntimeError, match="transport or JSON") as error:
        QdrantIntegration(url, response_bytes=1000).version()
    assert isinstance(error.value.__cause__, ValueError)
    assert len(requests) == 1


def test_redirect_not_followed_and_deadline_timeout_closes_response(
    vector_http, monkeypatch
):
    url, requests, replies = vector_http
    replies.append((307, {}, {"Location": url + "/other"}))
    with pytest.raises(RuntimeError, match="307"):
        QdrantIntegration(url, api_key="secret").version()
    assert len(requests) == 1
    response = BytesIO(canonical_bytes(ok({})))
    setattr(response, "headers", {})
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(
        "src.integrations.qdrant.client.build_opener", lambda *args: opener
    )
    client = QdrantIntegration("http://example.invalid")
    assert client.collection("jobs", timeout=0.25) == {}
    assert response.closed
    assert opener.open.call_args.kwargs["timeout"] == 0.25


@pytest.mark.parametrize(
    "name", ["", "../jobs", "alias space", "a/b", "a?x=y", "a" * 129]
)
def test_invalid_collection_name(name):
    with pytest.raises(ValueError):
        validate_collection_name(name)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 30},
        {"operation_timeout": True},
        {"response_bytes": True},
        {"replicas": 2},
        {"shards": True},
        {"version": "1.16.0"},
        {"api_key": "key\ninjected"},
    ],
)
def test_invalid_connection_settings_before_http(kwargs):
    with pytest.raises(ValueError):
        QdrantIntegration("http://example.invalid", **kwargs)

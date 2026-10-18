import json
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from typing import cast
from unittest.mock import Mock

import pytest

from src.components.corpus.json import JSONVacancyReader
from src.components.embedding.encoder import TextEmbedder
from src.components.embedding.vacancy import VacancyText
from src.domain.document.vacancy import Vacancy
from src.domain.embedding import Embedding, EmbeddingProfile
from src.domain.serialization import (
    canonical_bytes,
    fingerprint,
    load_json,
    validate_json,
)
from src.integrations.embedding.client import TEIIntegration, embedding_body


def runtime_info():
    profile = EmbeddingProfile()
    return {
        "model_id": profile.model_id,
        "model_sha": profile.revision,
        "model_dtype": "float16",
        "model_type": {"embedding": {"pooling": "mean"}},
        "version": "1.9.4",
        "max_input_length": 512,
        "max_batch_tokens": 4096,
        "max_client_batch_size": 8,
        "max_concurrent_requests": 32,
        "auto_truncate": False,
    }


def unit_vector(index=0):
    return [float(i == index) for i in range(768)]


@pytest.fixture
def inference():
    client = Mock(spec=TEIIntegration)
    client.info.return_value = runtime_info()
    client.embed.side_effect = lambda inputs, **kwargs: [unit_vector() for _ in inputs]
    return client


@pytest.fixture
def tei_server() -> Iterator[tuple[str, list, dict]]:
    requests: list[dict] = []
    replies = {
        "/info": (200, runtime_info(), {}),
        "/health": (200, b"", {}),
        "/embed": (200, [unit_vector()], {}),
        "/tokenize": (
            200,
            [[{"id": 0, "text": "<s>", "special": True, "start": None, "stop": None}]],
            {},
        ),
    }

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append(
                {
                    "path": self.path,
                    "method": self.command,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(raw) if raw else None,
                    "raw": raw,
                }
            )
            status, body, headers = replies[self.path.removeprefix("/proxy")]
            content = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            if "Content-Length" not in headers:
                self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/proxy", requests, replies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_canonical_json_preserves_types_unicode_and_array_order():
    value = {"z": [1, 1.0, True], "а": "е\u0308", "a": None}
    encoded = canonical_bytes(value)
    assert encoded == '{"a":null,"z":[1,1.0,true],"а":"е\u0308"}'.encode()
    assert load_json(encoded) == value
    assert fingerprint(dict(reversed(list(value.items())))) == fingerprint(value)
    assert fingerprint([1, 2]) != fingerprint([2, 1])
    assert fingerprint(1) != fingerprint(1.0) != fingerprint(True)
    assert fingerprint("е\u0308") != fingerprint("ё")


@pytest.mark.parametrize(
    "data",
    [
        b'{"id": 1, "id": 2}',
        b'{"nested": {"a": 1, "a": 1}}',
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b"1e999",
        b'"\\ud800"',
        b'{"\\udfff": 1}',
        b'"\xff"',
        b"18446744073709551616",
        b"-9223372036854775809",
        b"\xef\xbb\xbf[]",
    ],
)
def test_strict_json_rejects_invalid_wire_values(data):
    with pytest.raises(ValueError):
        load_json(data)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), 2**64, -(2**63) - 1, "\ud800", {1: "a"}, (), b"a"],
)
def test_json_boundary_rejects_non_json_python_values(value):
    with pytest.raises(ValueError):
        canonical_bytes(value)


def test_json_boundary_rejects_cycles_but_allows_shared_values():
    shared = {"integer": 2**64 - 1, "negative": -(2**63)}
    assert validate_json([shared, shared]) == [shared, shared]
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="cycles"):
        canonical_bytes(cycle)


def test_reader_snapshot_preserves_exact_ids_and_all_fields(tmp_path):
    path = tmp_path / "corpus.json"
    records = [
        {
            "id": " a ",
            "fields": {"title": "ЧПУ", "a.b": [1, 1.0, True], "extra": {"x": None}},
        },
        {"id": "a", "fields": {"title": "ё"}},
    ]
    path.write_bytes(canonical_bytes(records))
    reader = JSONVacancyReader(path)
    snapshot = reader.read()
    assert reader.identity == "json-vacancies-v1"
    assert [v.id for v in snapshot] == [" a ", "a"]
    assert snapshot[0].fields == records[0]["fields"]
    assert all(v.score is None for v in snapshot)
    path.write_text("[]")
    assert snapshot[0].fields["title"] == "ЧПУ"


@pytest.mark.parametrize(
    "records",
    [
        [],
        {},
        [{"id": "a", "fields": {}}, {"id": "a", "fields": {}}],
        [{"id": " ", "fields": {}}],
        [{"id": 1, "fields": {}}],
        [{"id": "a", "fields": []}],
        [{"id": "a", "fields": {}, "score": 1}],
        [{"id": "a", "fields": {}}, {"id": "last"}],
    ],
)
def test_reader_validates_entire_shape_and_identity(tmp_path, records):
    path = tmp_path / "corpus.json"
    path.write_bytes(canonical_bytes(records))
    with pytest.raises(ValueError):
        JSONVacancyReader(path).read()


def test_reader_does_not_silently_accept_duplicate_field_keys(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_bytes(b'[{"id":"a","fields":{"title":"A","title":"B"}}]')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        JSONVacancyReader(path).read()


def test_profile_and_embedding_are_immutable_values():
    profile = EmbeddingProfile()
    snapshot = profile.to_dict()
    snapshot["dimensions"] = 5
    assert profile.dimensions == 768
    assert len(profile.id) == 64
    assert EmbeddingProfile(**cast(dict, profile.to_dict())).id == profile.id
    assert replace(profile, revision="a" * 40).id != profile.id
    with pytest.raises(FrozenInstanceError):
        setattr(profile, "dimensions", 1)
    values = unit_vector()
    embedding = Embedding(profile.id, values)
    values[0] = 0
    assert embedding.values[0] == 1
    with pytest.raises(FrozenInstanceError):
        setattr(embedding, "values", ())


@pytest.mark.parametrize(
    "patch",
    [
        {"dimensions": True},
        {"max_tokens": 0},
        {"revision": "main"},
        {"schema_version": 2},
    ],
)
def test_profile_rejects_invalid_contract(patch):
    with pytest.raises(ValueError):
        EmbeddingProfile(**patch)


def test_vacancy_text_preserves_field_boundaries_and_original_fields():
    vacancy = Vacancy(
        "id",
        {
            "title": "  Электромонте\u0308р   4 разряда ",
            "description": "ЧПУ\n  ТСД 2/2",
            "other": [1],
        },
    )
    before = deepcopy(vacancy)
    text = VacancyText()
    assert text.render(vacancy) == "Электромонтёр 4 разряда\n\nЧПУ ТСД 2/2"
    assert vacancy == before
    custom = VacancyText(fields=[{"name": "a.b", "required": True}])
    assert (
        custom.render(Vacancy("id", {"a.b": "literal", "a": {"b": "nested"}}))
        == "literal"
    )
    assert VacancyText(**cast(dict, text.to_dict())).id == text.id
    assert VacancyText(separator="\n").id != text.id
    assert (
        VacancyText(
            fields=[
                {"name": "description", "required": False},
                {"name": "title", "required": False},
            ]
        ).id
        != text.id
    )


@pytest.mark.parametrize("value", [None, "", " \t", 0, False, [], {}])
def test_required_field_rejects_missing_blank_and_nonstring(value):
    renderer = VacancyText(fields=[{"name": "title", "required": True}])
    with pytest.raises(ValueError):
        renderer.render(Vacancy("a", {"title": value, "description": "fallback"}))


def test_optional_text_and_html_are_explicit():
    renderer = VacancyText()
    assert (
        renderer.render(
            Vacancy("a", {"title": None, "description": "<p>Без опыта</p>"})
        )
        == "<p>Без опыта</p>"
    )
    with pytest.raises(ValueError):
        renderer.render(Vacancy("a", {"title": False, "description": "text"}))
    with pytest.raises(ValueError):
        renderer.render(Vacancy("a", {}))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Комплектовщик   ТСД  2/2 ", "Комплектовщик ТСД 2/2"),
        ("Водитель категории C без опыта", "Водитель категории C без опыта"),
        ("оператор ЧПУ не наладчик", "оператор ЧПУ не наладчик"),
        ("электромонте\u0308р 4 разряда", "электромонтёр 4 разряда"),
        ("комплетовщик Москва", "комплетовщик Москва"),
        ("C++ .NET 1С", "C++ .NET 1С"),
        ("Ａ №①", "Ａ №①"),
    ],
)
def test_query_policy_is_conservative_and_pure(inference, raw, expected):
    embedder = TextEmbedder(EmbeddingProfile(), inference, {})
    assert embedder.prepare_query(raw) == "query: " + expected
    inference.info.assert_not_called()
    inference.embed.assert_not_called()


def test_document_preparation_preserves_newlines_and_adds_one_prefix(inference):
    embedder = TextEmbedder(EmbeddingProfile(), inference, {})
    assert embedder.prepare_documents(["Title\n\nDescription"]) == (
        "passage: Title\n\nDescription",
    )
    inference.info.assert_not_called()


@pytest.mark.parametrize("raw", ["", "  ", "\ud800", 1])
def test_invalid_query_is_rejected_before_runtime_http(inference, raw):
    embedder = TextEmbedder(EmbeddingProfile(), inference, {})
    with pytest.raises(ValueError):
        embedder.encode_query(raw)
    inference.info.assert_not_called()
    inference.embed.assert_not_called()


def test_query_byte_limit_includes_prefix_and_utf8(inference):
    embedder = TextEmbedder(EmbeddingProfile(), inference, {"query_bytes": 9})
    assert embedder.prepare_query("я") == "query: я"
    with pytest.raises(ValueError, match="byte budget"):
        embedder.encode_query("яя")
    inference.info.assert_not_called()


def test_query_body_budget_includes_json_escaping_and_is_checked_by_prepare(inference):
    query = 'ё " \\'
    prepared = "query: " + query
    size = len(canonical_bytes(embedding_body((prepared,), truncate=False)))
    embedder = TextEmbedder(EmbeddingProfile(), inference, {"batch_bytes": size})
    assert embedder.prepare_query(query) == prepared
    embedder = TextEmbedder(EmbeddingProfile(), inference, {"batch_bytes": size - 1})
    with pytest.raises(ValueError, match="inference body byte budget"):
        embedder.prepare_query(query)
    inference.info.assert_not_called()
    inference.embed.assert_not_called()


def test_exact_body_batches_include_json_escaping_and_prefixes(inference):
    documents = ['"\n\\ё', "second", "third"]
    limit = max(
        len(canonical_bytes(embedding_body(["passage: " + text], truncate=True)))
        for text in documents
    )
    embedder = TextEmbedder(EmbeddingProfile(), inference, {"batch_bytes": limit})
    assert tuple(embedder.document_batches(documents)) == tuple(
        (text,) for text in documents
    )
    assert tuple(embedder.document_batches(documents)) == tuple(
        (text,) for text in documents
    )
    inference.info.assert_not_called()
    assert tuple(
        TextEmbedder(
            EmbeddingProfile(), inference, {"batch_inputs": 2}
        ).document_batches(documents)
    ) == (tuple(documents[:2]), (documents[2],))
    with pytest.raises(ValueError, match="body byte budget"):
        tuple(
            TextEmbedder(
                EmbeddingProfile(), inference, {"batch_bytes": 1}
            ).document_batches(["a"])
        )


def test_preflight_materializes_bad_final_document_without_http(inference):
    embedder = TextEmbedder(
        EmbeddingProfile(), inference, {"batch_inputs": 1, "document_bytes": 12}
    )
    with pytest.raises(ValueError, match="byte budget"):
        tuple(embedder.document_batches(["a", "b", "too long"]))
    inference.info.assert_not_called()
    inference.embed.assert_not_called()


@pytest.mark.parametrize(
    "patch",
    [
        {"model_id": "other"},
        {"model_sha": "main"},
        {"model_dtype": "float32"},
        {"model_type": {"embedding": {"pooling": "cls"}}},
        {"version": "1.9.3"},
        {"max_input_length": 511},
        {"max_input_length": 512.0},
        {"auto_truncate": True},
        {"max_batch_tokens": 511},
        {"max_client_batch_size": True},
        {"max_concurrent_requests": 0},
    ],
)
def test_runtime_mismatch_prevents_inference(inference, patch):
    inference.info.return_value.update(patch)
    embedder = TextEmbedder(EmbeddingProfile(), inference, {})
    with pytest.raises(ValueError):
        embedder.encode_query("ЧПУ")
    inference.embed.assert_not_called()


def test_query_needs_only_one_input_capacity(inference):
    inference.info.return_value.update(max_batch_tokens=512, max_client_batch_size=1)
    embedder = TextEmbedder(EmbeddingProfile(), inference, {})
    assert len(embedder.encode_query("ЧПУ").values) == 768
    inference.embed.reset_mock()
    with pytest.raises(ValueError, match="runtime capacity"):
        embedder.encode_documents(["one", "two"])
    inference.embed.assert_not_called()


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [unit_vector(), unit_vector()],
        [[1.0]],
        [[0.0] * 768],
        [[True] + [0.0] * 767],
        [[float("nan")] + [0.0] * 767],
        [[float("inf")] + [0.0] * 767],
        [[2.0] + [0.0] * 767],
        [["1"] + [0.0] * 767],
    ],
)
def test_encoder_rejects_invalid_vector_or_batch(inference, bad):
    inference.embed.side_effect = None
    inference.embed.return_value = bad
    with pytest.raises(ValueError):
        TextEmbedder(EmbeddingProfile(), inference, {}).encode_query("ЧПУ")


def test_encoder_rejects_bad_last_vector_and_checks_every_runtime(inference):
    inference.embed.side_effect = [[unit_vector(), [0.0] * 768], [unit_vector()]]
    embedder = TextEmbedder(EmbeddingProfile(), inference, {})
    with pytest.raises(ValueError, match="L2"):
        embedder.encode_documents(["one", "two"])
    assert embedder.encode_query("ЧПУ").values == tuple(unit_vector())
    assert inference.info.call_count == 2


def test_real_http_query_and_document_contract(tei_server):
    url, requests, replies = tei_server
    client = TEIIntegration(url + "/", api_key="test-token")
    embedder = TextEmbedder(EmbeddingProfile().to_dict(), client, {})
    client.health()
    query = embedder.encode_query("  Электромонте\u0308р  ЧПУ ")
    replies["/embed"] = (200, [unit_vector(1), unit_vector(2)], {})
    vectors = embedder.encode_documents(["Title\n\nDescription", "Другая вакансия"])
    assert query.profile_id == embedder.profile.id
    assert vectors[0].values[1] == vectors[1].values[2] == 1
    assert [r["path"] for r in requests] == [
        "/proxy/health",
        "/proxy/info",
        "/proxy/embed",
        "/proxy/info",
        "/proxy/embed",
    ]
    assert all(r["authorization"] == "Bearer test-token" for r in requests)
    assert requests[2]["body"] == {
        "inputs": ["query: Электромонтёр ЧПУ"],
        "normalize": True,
        "truncate": False,
        "truncation_direction": "right",
    }
    assert requests[4]["body"] == {
        "inputs": ["passage: Title\n\nDescription", "passage: Другая вакансия"],
        "normalize": True,
        "truncate": True,
        "truncation_direction": "right",
    }
    assert requests[4]["raw"] == canonical_bytes(requests[4]["body"])


def test_tokenize_has_independent_budget_and_one_input_policy(tei_server):
    url, requests, replies = tei_server
    client = TEIIntegration(url, response_bytes=32, tokenize_response_bytes=1024)
    assert client.tokenize(["passage: ЧПУ"])[0][0]["special"] is True
    assert requests[0]["body"] == {
        "inputs": ["passage: ЧПУ"],
        "add_special_tokens": True,
    }
    with pytest.raises(ValueError, match="exactly one"):
        client.tokenize(["one", "two"])
    assert len(requests) == 1
    replies["/tokenize"] = (200, b" " * 1025, {})
    with pytest.raises(RuntimeError):
        client.tokenize(["passage: ЧПУ"])


@pytest.mark.parametrize(
    "status, body, headers",
    [
        (500, b"SECRET", {}),
        (200, b"invalid SECRET", {}),
        (200, b'{"secret":NaN}', {}),
        (200, b'{"a":1,"a":2}', {}),
        (200, b"[]", {"Content-Length": "3"}),
        (302, b"", {"Location": "https://example.org/secret"}),
    ],
)
def test_tei_http_failures_are_bounded_safe_and_not_redirected(
    tei_server, status, body, headers
):
    url, requests, replies = tei_server
    replies["/embed"] = (status, body, headers)
    with pytest.raises(RuntimeError) as error:
        TEIIntegration(url, api_key="secret").embed(["query: ЧПУ"], truncate=False)
    assert "SECRET" not in str(error.value)
    assert error.value.__cause__ is not None
    assert len(requests) == 1


@pytest.mark.parametrize("body", [{}, [], [1], [[True]], [[None]], [["1"]]])
def test_tei_native_response_shape_is_checked(tei_server, body):
    url, _, replies = tei_server
    replies["/embed"] = (200, body, {})
    with pytest.raises(ValueError):
        TEIIntegration(url).embed(["query: ЧПУ"], truncate=False)


def test_tei_response_read_rejects_size_before_parse(tei_server):
    url, _, replies = tei_server
    replies["/embed"] = (200, b" " * 33, {})
    with pytest.raises(RuntimeError, match="transport"):
        TEIIntegration(url, response_bytes=32).embed(["query: ЧПУ"], truncate=False)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.org",
        "http://name:secret@example.org",
        "http://example.org?a=b",
        "http://example.org#part",
        "http://example.org:bad",
        "http://exa\nmple.org",
    ],
)
def test_invalid_tei_configuration_fails_before_http(url):
    with pytest.raises(ValueError):
        TEIIntegration(url)


def test_unsupported_profile_limits_and_headers_are_not_silently_accepted(inference):
    with pytest.raises(ValueError, match="pinned"):
        TextEmbedder(replace(EmbeddingProfile(), model_id="other"), inference, {})
    for limits in ({"batch_inputs": True}, {"batch_inputs": 0}, {"unknown": 1}):
        with pytest.raises(ValueError):
            TextEmbedder(EmbeddingProfile(), inference, limits)
    with pytest.raises(ValueError):
        TEIIntegration("http://example.org", api_key="secret\r\nX: y")
    inference.info.assert_not_called()


def test_deadline_caps_each_request_and_expires_before_http(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("src.integrations.embedding.client.monotonic", lambda: now[0])
    monkeypatch.setattr("src.integrations.http_response.time.monotonic", lambda: now[0])
    client = TEIIntegration("http://localhost", timeout=60, deadline=15)

    class Response(BytesIO):
        headers: dict[str, str] = {}
        fp = Mock()

    responses = [Response(b""), Response(canonical_bytes(runtime_info()))]
    opener = Mock()
    opener.open.side_effect = responses
    client._opener = opener
    client.health()
    now[0] = 14.0
    assert client.info() == runtime_info()
    now[0] = 16.0
    with pytest.raises(TimeoutError, match="deadline"):
        client.embed(["query: ЧПУ"], truncate=False)
    assert [call.kwargs["timeout"] for call in opener.open.call_args_list] == [5, 1]
    assert all(response.closed for response in responses)


@pytest.mark.parametrize(
    "token",
    [
        {},
        {"id": True, "text": "a", "special": False, "start": 0, "stop": 1},
        {"id": 1, "text": 4, "special": False, "start": 0, "stop": 1},
        {"id": 1, "text": "a", "special": 0, "start": 0, "stop": 1},
        {"id": 1, "text": "a", "special": False, "start": 2, "stop": 1},
        {"id": 1, "text": "a", "special": False, "start": None, "stop": 1},
        {"id": 1, "text": "a", "special": False},
    ],
)
def test_tokenize_rejects_malformed_last_token(tei_server, token):
    url, _, replies = tei_server
    valid = {"id": 0, "text": "<s>", "special": True, "start": None, "stop": None}
    replies["/tokenize"] = (200, [[valid, token]], {})
    with pytest.raises(ValueError):
        TEIIntegration(url).tokenize(["passage: ЧПУ"])


def test_l2_tolerance_accepts_rounding_without_renormalization(inference):
    inference.embed.side_effect = None
    inference.embed.return_value = [[0.9995, *([0.0] * 767)]]
    result = TextEmbedder(EmbeddingProfile(), inference, {}).encode_query("ЧПУ")
    assert result.values[0] == 0.9995

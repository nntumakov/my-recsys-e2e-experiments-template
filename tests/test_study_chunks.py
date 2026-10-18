"""Local HTTP protocol evidence for E5, with explicit synthetic token/score fixtures."""

import json
import re
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from src.components.corpus.json import JSONVacancyReader
from src.components.embedding.chunks import ChunkVacancyText, token_offsets
from src.components.embedding.encoder import TextEmbedder
from src.components.embedding.vacancy import VacancyText
from src.components.reporting.exporters.json import JSONExporter
from src.core.transformers.data import PipelineData
from src.core.transformers.qdrant.chunks import ChunkSearchTransformer
from src.domain.document.vacancy import Vacancy
from src.domain.embedding import EmbeddingProfile
from src.domain.request.filters import QueryFilter, TermFilter
from src.domain.serialization import fingerprint
from src.domain.study import StudySERP
from src.experiments.indexing.chunks import ChunkIndexExperiment
from src.integrations.embedding.client import TEIIntegration
from src.integrations.qdrant.chunks import ChunkQdrantIntegration, ChunkQdrantSource
from src.integrations.qdrant.source import QdrantSource

IMAGE = "fixture@sha256:" + "a" * 64


def tokens(text, *, repeats=1):
    result = [{"id": 0, "text": "<s>", "special": True, "start": None, "stop": None}]
    for match in re.finditer(r"\S+", text):
        start = len(text[: match.start()].encode("utf-8"))
        stop = len(text[: match.end()].encode("utf-8"))
        result.extend(
            {
                "id": 1,
                "text": "ignored synthetic token spelling",
                "special": False,
                "start": start,
                "stop": stop,
            }
            for _ in range(repeats)
        )
    result.append(
        {"id": 2, "text": "</s>", "special": True, "start": None, "stop": None}
    )
    return result


@pytest.fixture
def chunk_stack(vector_stack, monkeypatch):
    respond = vector_stack.respond

    def chunk_respond(method, path, body):
        if path == "/tokenize":
            return 200, [tokens(text) for text in body["inputs"]]
        if path.endswith("/points/query"):
            vector_stack.query_calls += 1
            name = path.split("/")[2]
            conditions = body.get("filter", {}).get("must", [])
            excluded = set()
            for condition in body.get("filter", {}).get("must_not", []):
                excluded.update(condition["match"]["any"])
            points = [
                deepcopy(point)
                | {"version": 0, "score": point.get("fixture_score", 0.75)}
                for point in vector_stack.points[name].values()
                if point["payload"]["document_id"] not in excluded
                and all(
                    vector_stack.matches(point, condition) for condition in conditions
                )
            ]
            points.sort(key=lambda item: -item["score"])
            points = points[: body["limit"]]
            for point in points:
                point.pop("vector", None)
                point.pop("fixture_score", None)
            return vector_stack.ok({"points": points})
        return respond(method, path, body)

    monkeypatch.setattr(vector_stack, "respond", chunk_respond)
    return vector_stack


def components(stack, recipe=None, collection="chunks", *, depth=100, final_k=20):
    recipe = recipe or ChunkVacancyText()
    profile = EmbeddingProfile()
    source = ChunkQdrantSource(
        profile,
        recipe,
        "cohort",
        {
            "city": {"path": ["city"], "type": "keyword"},
            "active": {"path": ["active"], "type": "bool"},
        },
        runtime={"version": "1.9.4", "image": IMAGE},
        vector={"size": 768, "distance": "Cosine"},
    )
    embedder = TextEmbedder(profile, TEIIntegration(stack.url), {})
    integration = ChunkQdrantIntegration(stack.url)
    search = ChunkSearchTransformer(
        integration,
        embedder,
        source,
        collection,
        candidate_depth=depth,
        final_k=final_k,
        exact=True,
    )
    return SimpleNamespace(
        recipe=recipe,
        source=source,
        embedder=embedder,
        integration=integration,
        search=search,
    )


def build(stack, tmp_path, records, recipe=None, collection="chunks"):
    objects = components(stack, recipe, collection)
    path = tmp_path / f"{collection}.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    index = ChunkIndexExperiment(
        JSONVacancyReader(str(path)),
        objects.recipe,
        objects.embedder,
        objects.integration,
        objects.source,
        collection,
    )
    return objects, index


def record(identifier="a", body="ТСД без опыта", title="Комплектовщик"):
    return {
        "id": identifier,
        "fields": {
            "title": title,
            "description": body,
            "city": "Москва",
            "active": True,
            "extra": [1, 1.0, True],
        },
    }


def data():
    return PipelineData(
        filters=(
            QueryFilter("комплектовщик ТСД"),
            TermFilter("city", "Москва"),
            TermFilter("active", True),
        )
    )


def test_utf8_whole_word_coverage_overlap_title_cutoff_and_full_input_audit(
    chunk_stack,
):
    objects = components(
        chunk_stack, ChunkVacancyText(title_tokens=2, window_tokens=3, overlap_tokens=1)
    )
    vacancy = Vacancy(
        "ё",
        record(body="Первое ёжик 🦊 четвертое пятое шестое", title="Раз два три")[
            "fields"
        ],
    )
    passages, audit = objects.recipe.render_chunks(vacancy, objects.embedder)
    raw = "Первое ёжик 🦊 четвертое пятое шестое".encode()
    assert audit["title_shortened"] is True and audit["title_context"] == "Раз два"
    assert passages[0].start == 0 and passages[-1].stop == len(raw)
    assert len(passages) == 3
    for previous, current in zip(passages, passages[1:]):
        assert previous.start < current.start < previous.stop < current.stop
    for passage in passages:
        assert passage.text == "Раз два\n" + raw[passage.start : passage.stop].decode()
        actual = objects.embedder.prepare_documents((passage.text,))[0]
        assert passage.model_input_sha256 == sha256(actual.encode()).hexdigest()
        assert passage.token_count == len(tokens(actual))
    objects.embedder.encode_bounded_documents([p.text for p in passages])
    assert all(
        request["body"]["truncate"] is False
        for request in chunk_stack.requests
        if request["path"] == "/embed"
    )


@pytest.mark.parametrize("offsets", [[(0, 1)], [(1, 2)], [(0, 2), (0, 1)], [(0, 2)]])
def test_bad_byte_offsets_or_missing_tail_are_errors(offsets):
    malformed = [
        {"id": 1, "text": "x", "special": False, "start": start, "stop": stop}
        for start, stop in offsets
    ]
    with pytest.raises(ValueError):
        token_offsets("ёx", malformed)


def test_repeated_unicode_offsets_preserve_one_chunk(chunk_stack, monkeypatch):
    objects = components(chunk_stack)
    tokenize = objects.embedder.integration.tokenize
    monkeypatch.setattr(
        objects.embedder.integration,
        "tokenize",
        lambda inputs: [tokens(inputs[0], repeats=2)],
    )
    passages, _ = objects.recipe.render_chunks(
        Vacancy("a", record(body="ё 🦊")["fields"]), objects.embedder
    )
    assert len(passages) == 1 and passages[0].text.endswith("ё 🦊")
    monkeypatch.setattr(objects.embedder.integration, "tokenize", tokenize)


def test_complete_input_overflow_shrinks_body_on_words(chunk_stack, monkeypatch):
    objects = components(
        chunk_stack,
        ChunkVacancyText(title_tokens=1, window_tokens=400, overlap_tokens=0),
    )
    original = objects.embedder.integration.tokenize

    def prefixed_large(inputs):
        result = original(inputs)
        if inputs[0].startswith("passage:"):
            prefix = result[0][1]
            result[0][1:1] = [deepcopy(prefix) for _ in range(130)]
        return result

    monkeypatch.setattr(objects.embedder.integration, "tokenize", prefixed_large)
    passages, _ = objects.recipe.render_chunks(
        Vacancy(
            "a",
            record(body=" ".join(f"w{i}" for i in range(450)), title="Role")["fields"],
        ),
        objects.embedder,
    )
    assert len(passages) == 2
    assert all(p.token_count <= 512 for p in passages)
    assert passages[-1].stop == len(" ".join(f"w{i}" for i in range(450)).encode())


@pytest.mark.parametrize("case", ["cap", "word", "title"])
def test_preflight_errors_do_not_create_collection(chunk_stack, tmp_path, case):
    recipe = ChunkVacancyText(
        window_tokens=1, overlap_tokens=0, max_chunks=2, title_tokens=1
    )
    body, title = "a b c", "Role"
    objects, index = build(
        chunk_stack, tmp_path, [record(body=body, title=title)], recipe
    )
    if case != "cap":
        original = objects.embedder.integration.tokenize
        objects.embedder.integration.tokenize = lambda inputs: (
            [tokens(inputs[0], repeats=3)]
            if not inputs[0].startswith("passage:")
            else original(inputs)
        )
        if case == "word":
            index.renderer = objects.recipe = ChunkVacancyText(
                title_tokens=0, window_tokens=1, overlap_tokens=0
            )
    with pytest.raises(ValueError):
        index.run()
    assert not any(
        request["method"] in ("PUT", "PATCH", "DELETE")
        for request in chunk_stack.requests
    )
    assert chunk_stack.collections == {}


def test_chunk_build_full_m_readback_ready_receipt_and_v1_v2_rejection(
    chunk_stack, tmp_path
):
    objects, index = build(
        chunk_stack,
        tmp_path,
        [record("ё", "a b c d e f g h i"), record(" exact id ", "j k l m n o p q r")],
        ChunkVacancyText(window_tokens=2, overlap_tokens=0),
    )
    receipt = index.run().to_dict()
    assert receipt["expected_documents"] == 2
    assert receipt["expected_points"] == receipt["verified_points"] == 10
    descriptor = receipt["descriptor"]
    assert descriptor["schema_version"] == descriptor["payload_schema_version"] == 2
    assert descriptor["state"] == "ready"
    assert descriptor["verification"]["read_back_count"] == 10
    assert descriptor["verification"]["documents_count"] == 2
    reads = [
        r
        for r in chunk_stack.requests
        if r["path"] == "/collections/chunks/points" and r["method"] == "POST"
    ]
    assert [len(r["body"]["ids"]) for r in reads] == [8, 2]
    assert chunk_stack.collections["chunks"]["payload_schema"]["document_id"] == {
        "data_type": "keyword"
    }
    assert all(
        r["body"]["truncate"] is False
        for r in chunk_stack.requests
        if r["path"] == "/embed"
    )
    v1 = QdrantSource(
        objects.source.profile,
        VacancyText(),
        "cohort",
        objects.source.filter_fields,
        runtime=objects.source.runtime,
        vector={"size": 768, "distance": "Cosine"},
    )
    with pytest.raises(ValueError):
        v1.validate_descriptor("chunks", chunk_stack.collections["chunks"])
    info = deepcopy(chunk_stack.collections["chunks"])
    info["config"]["metadata"]["recsys"]["payload_schema_version"] = 1
    with pytest.raises(ValueError):
        objects.source.validate_descriptor("chunks", info)
    assert objects.source.point_id(
        " exact id ",
        {
            "ordinal": 0,
            "start": 0,
            "stop": 1,
            "title_stop": 0,
            "model_input_sha256": "a" * 64,
        },
    ) != objects.source.point_id(
        "exact id",
        {
            "ordinal": 0,
            "start": 0,
            "stop": 1,
            "title_stop": 0,
            "model_input_sha256": "a" * 64,
        },
    )


def test_bad_last_chunk_prevents_publication_and_receipt(
    chunk_stack, tmp_path, monkeypatch
):
    objects, index = build(
        chunk_stack,
        tmp_path,
        [record(body="a b c d e f g h i")],
        ChunkVacancyText(window_tokens=1, overlap_tokens=0),
    )
    path = tmp_path / "receipt.json"
    index.exporters = (JSONExporter(str(path)),)
    original = objects.integration.retrieve

    def corrupted(name, ids):
        result = original(name, ids)
        if len(ids) == 1:
            result[-1]["payload"]["fragment"]["stop"] -= 1
        return result

    monkeypatch.setattr(objects.integration, "retrieve", corrupted)
    with pytest.raises(RuntimeError, match="differs"):
        index._wrapped_run()
    assert (
        chunk_stack.collections["chunks"]["config"]["metadata"]["recsys"]["state"]
        == "building"
    )
    assert not path.exists() and not any(
        r["method"] == "PATCH" for r in chunk_stack.requests
    )


def test_three_concentrated_vacancies_first_100_points_then_100_unique(
    chunk_stack, tmp_path
):
    records = [
        record(f"d{i:03d}", " ".join(f"w{j}" for j in range(64)) if i < 3 else "short")
        for i in range(100)
    ]
    objects, index = build(
        chunk_stack,
        tmp_path,
        records,
        ChunkVacancyText(window_tokens=1, overlap_tokens=0),
    )
    index.run()
    for point in chunk_stack.points["chunks"].values():
        identifier = point["payload"]["document_id"]
        point["fixture_score"] = 0.99 - int(identifier[1:]) / 1000
    chunk_stack.requests.clear()
    result = objects.search.transform(data(), registry=Mock()).items
    assert len(result.items) == 20
    assert result.evidence["candidate_documents"] == 100
    retrievals = [
        r for r in chunk_stack.requests if r["path"].endswith("/points/query")
    ]
    assert len(retrievals) == 3
    assert len(result.evidence["hits"]) == 100
    assert retrievals[0]["body"]["limit"] == 100
    assert retrievals[1]["body"]["filter"]["must_not"] == [
        {"key": "document_id", "match": {"any": ["d000", "d001"]}}
    ]
    assert all(
        r["body"]["filter"]["must"] == retrievals[0]["body"]["filter"]["must"]
        for r in retrievals
    )
    assert len([r for r in chunk_stack.requests if r["path"] == "/embed"]) == 1
    inference = next(
        call for call in result.evidence["calls"] if call["stage"] == "inference"
    )
    assert inference["model_input"] == objects.embedder.prepare_query(
        "комплектовщик ТСД"
    )
    expected_hash = fingerprint(chunk_stack.vector)
    assert inference["vector_sha256"] == expected_hash
    evidence_calls = [
        call for call in result.evidence["calls"] if call["stage"] == "retrieval"
    ]
    assert len(evidence_calls) == len(retrievals)
    for evidence, request in zip(evidence_calls, retrievals, strict=True):
        assert "query" not in evidence["body"]
        assert "vector" not in evidence
        assert evidence["body"] == {
            key: value for key, value in request["body"].items() if key != "query"
        }
        assert evidence["vector_sha256"] == expected_hash
        assert evidence["vector_dimensions"] == 768
        assert evidence["profile_id"] == objects.source.profile.id
        native_exclusions = request["body"]["filter"].get("must_not", [])
        assert evidence["excluded_ids"] == (
            native_exclusions[0]["match"]["any"] if native_exclusions else []
        )
        assert evidence["body"]["filter"]["must"] == [
            {"key": "fields.city", "match": {"value": "Москва"}},
            {"key": "fields.active", "match": {"value": True}},
        ]


@pytest.mark.parametrize("unrelated", [0, 5, 20])
def test_max_has_no_direct_chunk_count_bonus(chunk_stack, tmp_path, unrelated):
    objects, index = build(
        chunk_stack,
        tmp_path,
        [
            record(
                "long",
                " ".join(["relevant", *[f"unrelated{i}" for i in range(unrelated)]]),
            ),
            record("short", "relevant"),
        ],
        ChunkVacancyText(window_tokens=1, overlap_tokens=0),
    )
    index.run()
    for point in chunk_stack.points["chunks"].values():
        point["fixture_score"] = (
            0.8 if point["payload"]["fragment"]["ordinal"] == 0 else 0.1
        )
    result = objects.search.transform(data(), registry=Mock()).items
    assert [(item.id, item.score) for item in result.items] == [
        ("long", 0.8),
        ("short", 0.8),
    ]
    assert result.evidence["exhausted"] is True
    assert result.evidence["hits"]["long"]["winner_fragment"]["ordinal"] == 0


@pytest.mark.parametrize(
    "failure", ["excluded", "malformed_last", "hard_filter", "fields", "timeout"]
)
def test_later_page_failure_is_atomic_and_retry_is_explicit(
    chunk_stack, tmp_path, monkeypatch, failure
):
    records = [
        record("many", " ".join(f"w{i}" for i in range(64))),
        record("second", " ".join(f"w{i}" for i in range(64))),
        record("third", "single"),
    ]
    objects, index = build(
        chunk_stack,
        tmp_path,
        records,
        ChunkVacancyText(window_tokens=1, overlap_tokens=0),
    )
    index.run()
    original = objects.integration.query_points
    previous = []

    def fail(name, body, source):
        points = original(name, body, source)
        if "must_not" in body.get("filter", {}):
            if failure == "timeout":
                raise TimeoutError("fixture timeout")
            if failure == "excluded":
                points.append(previous[0])
            elif failure == "malformed_last":
                points[-1]["payload"]["fields"] = []
            elif failure == "hard_filter":
                points[-1]["payload"]["fields"]["city"] = "Тверь"
            elif failure == "fields":
                if points:
                    points.append(deepcopy(points[-1]))
                    points[-1]["id"] = "wrong"
            return points
        previous[:] = points
        return points

    monkeypatch.setattr(objects.integration, "query_points", fail)
    incoming = data()
    old_items = incoming.items
    before = incoming.copy(deep=True)
    with pytest.raises((ValueError, TimeoutError)):
        objects.search.transform(incoming, registry=Mock())
    assert (
        incoming.items is old_items
        and incoming.context == before.context
        and incoming.filters == before.filters
    )
    monkeypatch.setattr(objects.integration, "query_points", original)
    assert len(objects.search.transform(incoming, registry=Mock()).items.items) == 3


def test_bounded_encoder_rejects_full_input_overflow_before_embed(chunk_stack):
    objects = components(chunk_stack)
    with pytest.raises(ValueError, match="complete model token budget"):
        objects.embedder.encode_bounded_documents((" ".join(["word"] * 512),))
    assert not any(request["path"] == "/embed" for request in chunk_stack.requests)


def test_preflight_batch_byte_budget_failure_precedes_remote_writes(
    chunk_stack, tmp_path
):
    objects, index = build(chunk_stack, tmp_path, [record(body="word " * 30)])
    index.embedder = TextEmbedder(
        objects.source.profile, TEIIntegration(chunk_stack.url), {"batch_bytes": 100}
    )
    with pytest.raises(ValueError, match="inference body byte budget"):
        index.run()
    assert chunk_stack.collections == {}
    assert not any(
        request["method"] in ("PUT", "PATCH", "DELETE")
        for request in chunk_stack.requests
    )


def test_all_missing_scalar_smoke_and_unfiltered_exclusion_are_independent(
    chunk_stack, tmp_path
):
    original = record()
    original["fields"].pop("city")
    original["fields"].pop("active")
    _, index = build(chunk_stack, tmp_path, [original])
    index.run()
    calls = [r["body"] for r in chunk_stack.requests if r["path"].endswith("/query")]
    assert calls[0]["filter"] == {
        "must": [{"key": "fields.active", "match": {"value": True}}]
    }
    assert calls[1]["filter"] == {
        "must_not": [{"key": "document_id", "match": {"any": ["a"]}}]
    }


def test_inconsistent_chunk_fields_reject_entire_first_page(
    chunk_stack, tmp_path, monkeypatch
):
    objects, index = build(
        chunk_stack,
        tmp_path,
        [record(body="a b")],
        ChunkVacancyText(window_tokens=1, overlap_tokens=0),
    )
    index.run()
    original = objects.integration.query_points

    def corrupt(name, body, source):
        points = original(name, body, source)
        points[-1]["payload"]["fields"]["extra"] = ["changed"]
        return points

    monkeypatch.setattr(objects.integration, "query_points", corrupt)
    incoming = data()
    original_items = incoming.items
    with pytest.raises(ValueError, match="inconsistent fields"):
        objects.search.transform(incoming, registry=Mock())
    assert incoming.items is original_items


def test_explicit_empty_page_exhaustion_returns_completed_smaller_serp(
    chunk_stack, tmp_path, monkeypatch
):
    objects, index = build(
        chunk_stack,
        tmp_path,
        [record(body="a b")],
        ChunkVacancyText(window_tokens=1, overlap_tokens=0),
    )
    index.run()
    # An explicit empty page completes retrieval of an undersized eligible cohort.
    search = ChunkSearchTransformer(
        objects.integration,
        objects.embedder,
        objects.source,
        "chunks",
        candidate_depth=3,
        final_k=2,
        exact=True,
    )
    original = objects.integration.query_points
    monkeypatch.setattr(
        objects.integration,
        "query_points",
        lambda name, body, source: (
            original(name, body, source)
            if "must_not" not in body.get("filter", {})
            else []
        ),
    )
    result = cast(StudySERP, search.transform(data(), registry=Mock()).items)
    assert result.evidence["exhausted"] is True and len(result.items) == 1


def test_invalid_bounded_batch_is_rejected_before_any_http(chunk_stack):
    objects = components(chunk_stack)
    with pytest.raises(ValueError, match="batch exceeds"):
        objects.embedder.encode_bounded_documents(["word"] * 9)
    assert chunk_stack.requests == []


def test_long_unicode_title_winner_reconstructs_exact_audited_input_without_http(
    chunk_stack, tmp_path
):
    objects, index = build(
        chunk_stack,
        tmp_path,
        [
            record(
                title="Ёжик 🦊 лишнее название",
                body="Целевой навык потом",
                identifier="ё",
            )
        ],
        ChunkVacancyText(title_tokens=2, window_tokens=2, overlap_tokens=0),
    )
    receipt = index.run().to_dict()
    assert receipt["chunk_audit"][0]["title_stop"] == len("Ёжик 🦊".encode())
    assert receipt["descriptor"]["fragment_schema_version"] == 2
    points = list(chunk_stack.points["chunks"].values())
    for point in points:
        fragment = point["payload"]["fragment"]
        assert fragment["title_stop"] == len("Ёжик 🦊".encode())
        document = Vacancy(point["payload"]["document_id"], point["payload"]["fields"])
        before = len(chunk_stack.requests)
        passage = objects.source.passage(document, fragment)
        assert len(chunk_stack.requests) == before
        assert passage.startswith("Ёжик 🦊\n")
        model_input = objects.source.profile.document_prefix + passage
        assert (
            sha256(model_input.encode("utf-8")).hexdigest()
            == fragment["model_input_sha256"]
        )
    result = objects.search.transform(data(), registry=Mock()).items
    winner = result.evidence["hits"]["ё"]
    assert winner["winner_passage"].startswith("Ёжик 🦊\n")
    assert (
        winner["model_input"]
        == objects.source.profile.document_prefix + winner["winner_passage"]
    )
    assert (
        sha256(winner["model_input"].encode("utf-8")).hexdigest()
        == winner["winner_fragment"]["model_input_sha256"]
    )
    assert all(
        call["http_calls"] == (1 if call["stage"] == "retrieval" else 2)
        for call in result.evidence["calls"]
    )
    assert not any(call["stage"] == "encode" for call in result.evidence["calls"])


@pytest.mark.parametrize(
    "title_stop", [-1, 1, len("Комплектовщик".encode()) - 1, 99999]
)
def test_malformed_title_cutoff_rejects_hit_and_full_readback(
    chunk_stack, tmp_path, monkeypatch, title_stop
):
    objects, index = build(chunk_stack, tmp_path, [record()])
    index.run()
    point = deepcopy(next(iter(chunk_stack.points["chunks"].values())))
    point["payload"]["fragment"]["title_stop"] = title_stop
    point.update({"version": 0, "score": 0.8})
    with pytest.raises(ValueError):
        objects.source.hit(point)
    chunk_stack.collections.clear()
    chunk_stack.points.clear()
    original = objects.integration.retrieve

    def bad_readback(name, ids):
        values = original(name, ids)
        values[-1]["payload"]["fragment"]["title_stop"] = title_stop
        return values

    monkeypatch.setattr(objects.integration, "retrieve", bad_readback)
    with pytest.raises(RuntimeError, match="differs"):
        index.run()
    assert (
        chunk_stack.collections["chunks"]["config"]["metadata"]["recsys"]["state"]
        == "building"
    )


def test_changed_title_or_input_digest_rejects_native_fragment(chunk_stack, tmp_path):
    objects, index = build(chunk_stack, tmp_path, [record()])
    index.run()
    point = deepcopy(next(iter(chunk_stack.points["chunks"].values())))
    point.update({"version": 0, "score": 0.8})
    point["payload"]["fields"]["title"] = "Иная профессия"
    with pytest.raises(ValueError, match="cutoff|digest"):
        objects.source.hit(point)

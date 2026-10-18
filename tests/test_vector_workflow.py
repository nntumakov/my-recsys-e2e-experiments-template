import csv
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from hydra import compose, initialize_config_dir
from hydra.errors import InstantiationException
from hydra.utils import instantiate

from src.components.reporting.exporters.abstract import BaseExporter
from src.components.reporting.exporters.csv import SearchCSVExporter
from src.components.reporting.exporters.json import JSONExporter
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import TermFilter, UserPropertyFilter
from src.domain.serialization import canonical_bytes
from src.experiments.indexing.vector import corpus_digest

ROOT = Path(__file__).resolve().parents[1]
GROUP = "experiments/vacancy-search/vector@experiment"
IMAGE = "ghcr.io/huggingface/text-embeddings-inference:1.9.4@sha256:" + "a" * 64
DECLARATIONS = (
    "+qdrant.filter_fields={city:{path:[location,city],type:keyword},"
    "rate:{path:[rate],type:float},grade:{path:[grade],type:integer},"
    "active:{path:[active],type:bool}}"
)
RECORDS: list[dict] = [
    {
        "id": "z2",
        "fields": {
            "title": "  Комплектовщик\tТСД ",
            "description": "Без   опыта\nработы",
            "location": {"city": "Москва"},
            "location.city": "original literal key",
            "active": False,
            "grade": 2,
            "rate": 3.5,
            "extra": [None, True, 1, 1.0, {"unsigned": 2**64 - 1}],
        },
    },
    {
        "id": " a1 ",
        "fields": {
            "title": "Токарь",
            "description": None,
            "location": {"city": "Тверь"},
            "active": True,
            "grade": 1,
            "rate": 1.0,
        },
    },
]


@pytest.fixture
def corpus_file(tmp_path):
    path = tmp_path / "vacancies.json"
    path.write_text(json.dumps(RECORDS, ensure_ascii=False), encoding="utf-8")
    return path


def site_overrides(url):
    return [
        f"qdrant.url={url}",
        "qdrant.api_key=null",
        "qdrant.collection=vacancies-g1",
        f"embedding.client.url={url}",
        "embedding.client.api_key=null",
        f"embedding.runtime.image={IMAGE}",
        "retrieval.candidate_depth=3",
        "retrieval.final_k=2",
        DECLARATIONS,
    ]


def vector_experiment(stack, corpus_file, selection="001-index", *overrides):
    with initialize_config_dir(config_dir=str(ROOT / "configs"), version_base="1.3"):
        cfg = compose(
            config_name="vector",
            overrides=[
                f"{GROUP}={selection}",
                *site_overrides(stack.url),
                f"corpus.path={corpus_file}",
                "experiment.exporters=[]",
                *overrides,
            ],
        )
    return instantiate(cfg.experiment, _convert_="all")


def writes(stack):
    return [r for r in stack.requests if r["method"] in ("PUT", "PATCH", "DELETE")]


def descriptor(stack):
    return stack.collections["vacancies-g1"]["config"]["metadata"]["recsys"]


@pytest.mark.parametrize("query", ["x" * 100, '"' * 40, "\\" * 40])
def test_oversized_query_body_fails_before_either_integration(
    vector_stack, corpus_file, query
):
    experiment = vector_experiment(
        vector_stack, corpus_file, "002-interactive", "embedding.limits.batch_bytes=100"
    )
    with pytest.raises(ValueError, match="inference body byte budget"):
        experiment.search(query)
    assert vector_stack.requests == []


def test_hydra_http_index_search_batch_and_json_csv(
    vector_stack, corpus_file, tmp_path
):
    vector_stack.extra_metadata = {"another_owner": {"retain": [1, "yes"]}}
    index = vector_experiment(vector_stack, corpus_file)
    receipt_path = tmp_path / "vector-index.json"
    index.exporters = (JSONExporter(str(receipt_path)),)
    index._wrapped_run()

    receipt = json.loads(receipt_path.read_text())
    assert receipt["artifact_type"] == "vector_index"
    assert receipt["descriptor"] == descriptor(vector_stack)
    assert descriptor(vector_stack)["verification"] == {
        "protocol": "full-readback-v1",
        "exact_count": 2,
        "read_back_count": 2,
        "status": "green",
        "optimizer_status": "ok",
        "indexes": {
            "fields.location.city": "keyword",
            "fields.active": "bool",
            "fields.grade": "integer",
            "fields.rate": "float",
        },
        "smoke": True,
    }
    metadata = vector_stack.collections["vacancies-g1"]["config"]["metadata"]
    assert metadata["another_owner"] == vector_stack.extra_metadata["another_owner"]
    assert "metadata" not in receipt["actual_config"]
    native_writes = writes(vector_stack)
    assert [r["method"] for r in native_writes] == ["PUT"] * 6 + ["PATCH"]
    assert all("/index?wait=true" in r["path"] for r in native_writes[1:5])
    assert native_writes[5]["path"].endswith("/points?wait=true&timeout=60")
    assert set(native_writes[-1]["body"]["metadata"]) == {"recsys"}
    for record, point in zip(RECORDS, native_writes[5]["body"]["points"], strict=True):
        assert point["payload"] == {
            "document_id": record["id"],
            "fields": record["fields"],
        }
        assert point["id"] == index.source.point_id(record["id"])
        assert point["vector"] == vector_stack.vector
    passage_inputs = [
        r["body"]["inputs"]
        for r in vector_stack.requests
        if r["path"] == "/embed" and len(r["body"]["inputs"]) == 2
    ]
    assert passage_inputs == [
        ["passage: Комплектовщик ТСД\n\nБез опыта работы", "passage: Токарь"]
    ]

    # Search needs the expected source contract, but never reads corpus.path.
    search = vector_experiment(
        vector_stack, tmp_path / "absent.json", "002-interactive", "retrieval.final_k=1"
    )
    result = search.search(
        "  склад   ТСД ",
        context={"city": "Москва"},
        filters=(
            UserPropertyFilter("city", "city"),
            TermFilter("active", False),
            TermFilter("grade", 2),
            TermFilter("rate", 3.5),
        ),
    )
    assert [item.id for item in result.items] == ["z2"]
    assert result.items[0].fields == RECORDS[0]["fields"]
    assert vector_stack.requests[-2]["body"]["inputs"] == ["query: склад ТСД"]
    query = vector_stack.requests[-1]["body"]
    assert query["limit"] == 3
    assert query["filter"]["must"] == [
        {"key": "fields.location.city", "match": {"value": "Москва"}},
        {"key": "fields.active", "match": {"value": False}},
        {"key": "fields.grade", "match": {"value": 2}},
        {"key": "fields.rate", "range": {"gte": 3.5, "lte": 3.5}},
    ]
    assert len(search.search("токарь").items) == 1

    queries = tmp_path / "queries.csv"
    with queries.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "text"])
        writer.writerows([("q1", 'Работа, "смены"\nрядом'), ("q2", "Токарь")])
    batch = vector_experiment(
        vector_stack, corpus_file, "003-batch", f"experiment.input_path={queries}"
    )
    batch.exporters = (
        SearchCSVExporter(str(tmp_path / "searches.csv")),
        JSONExporter(str(tmp_path / "batch.json")),
    )
    batch._wrapped_run()
    with (tmp_path / "searches.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["query_id"] for row in rows] == ["q1", "q1", "q2", "q2"]
    assert rows[0]["query"] == 'Работа, "смены"\nрядом'
    assert [row["document_id"] for row in rows] == [" a1 ", "z2"] * 2
    assert json.loads(rows[1]["fields"]) == RECORDS[0]["fields"]
    assert (
        json.loads((tmp_path / "batch.json").read_text())["searches"][0]["items"][1][
            "fields"
        ]
        == RECORDS[0]["fields"]
    )
    assert len(writes(vector_stack)) == len(native_writes)


@pytest.mark.parametrize("invalid", ["duplicate", "text", "filter", "payload", "json"])
def test_invalid_last_corpus_record_stops_before_all_http(
    vector_stack, corpus_file, tmp_path, invalid
):
    records = deepcopy(RECORDS)
    overrides = []
    if invalid == "duplicate":
        records[-1]["id"] = records[0]["id"]
    elif invalid == "text":
        records[-1]["fields"]["title"] = 7
    elif invalid == "filter":
        records[-1]["fields"]["active"] = 1
    elif invalid == "payload":
        records[-1]["fields"]["extra"] = "x" * 2000
        overrides.append("qdrant.payload_bytes=1000")
    elif invalid == "json":
        records[-1]["fields"]["extra"] = 2**64
    corpus_file.write_text(json.dumps(records), encoding="utf-8")
    index = vector_experiment(vector_stack, corpus_file, "001-index", *overrides)
    exporter = Mock(spec=BaseExporter)
    index.exporters = (exporter,)
    with pytest.raises(ValueError):
        index._wrapped_run()
    assert vector_stack.requests == []
    exporter.export.assert_not_called()


@pytest.mark.parametrize("occupied", [False, True])
def test_receipt_destination_is_checked_before_network(
    vector_stack, corpus_file, tmp_path, occupied
):
    receipt = tmp_path / (
        "vector-index.json" if occupied else "missing/vector-index.json"
    )
    if occupied:
        receipt.write_text("previous receipt", encoding="utf-8")
    index = vector_experiment(vector_stack, corpus_file)
    index.exporters = (JSONExporter(str(receipt)),)
    with pytest.raises((ValueError, FileExistsError)):
        index._wrapped_run()
    assert not vector_stack.requests
    if occupied:
        assert receipt.read_text() == "previous receipt"


@pytest.mark.parametrize(
    "failure,uploaded,remote_ready",
    [
        ("inference", False, False),
        ("upsert", False, False),
        ("count", True, False),
        ("readback", True, False),
        ("readback_type", True, False),
        ("readback_missing", True, False),
        ("readback_vector", True, False),
        ("health", True, False),
        ("yellow", True, False),
        ("smoke", True, False),
        ("smoke_wrong_filter", True, False),
        ("ready", True, False),
        ("ready_readback", True, True),
    ],
)
def test_failed_index_phase_never_exports_receipt(
    vector_stack, corpus_file, tmp_path, failure, uploaded, remote_ready
):
    index = vector_experiment(
        vector_stack, corpus_file, "001-index", "qdrant.verification_deadline=0.02"
    )
    receipt_path = tmp_path / "vector-index.json"
    exporter = Mock(spec=BaseExporter)
    index.exporters = (JSONExporter(str(receipt_path)), exporter)
    vector_stack.failure = failure
    with pytest.raises((ValueError, RuntimeError, TimeoutError)):
        index._wrapped_run()
    assert bool(vector_stack.points["vacancies-g1"]) is uploaded
    assert (descriptor(vector_stack)["state"] == "ready") is remote_ready
    assert not receipt_path.exists()
    exporter.export.assert_not_called()
    patch_calls = [r for r in writes(vector_stack) if r["method"] == "PATCH"]
    assert len(patch_calls) == (1 if failure in ("ready", "ready_readback") else 0)
    if failure == "inference":
        assert not any("/points" in r["path"] for r in writes(vector_stack))


@pytest.mark.parametrize("failure", ["second_inference", "second_upsert"])
def test_later_batch_failure_leaves_previous_batch_unpublished(
    vector_stack, corpus_file, failure
):
    index = vector_experiment(
        vector_stack, corpus_file, "001-index", "embedding.limits.batch_inputs=1"
    )
    vector_stack.failure = failure
    exporter = Mock(spec=BaseExporter)
    index.exporters = (exporter,)
    with pytest.raises((ValueError, RuntimeError)):
        index._wrapped_run()
    assert list(vector_stack.points["vacancies-g1"]) == [index.source.point_id("z2")]
    assert descriptor(vector_stack)["state"] == "building"
    assert all(r["method"] == "PUT" for r in writes(vector_stack))
    exporter.export.assert_not_called()


def test_existing_collection_is_never_adopted(vector_stack, corpus_file):
    first = vector_experiment(vector_stack, corpus_file)
    first.run()
    snapshot = deepcopy(vector_stack.collections)
    points = deepcopy(vector_stack.points)
    vector_stack.requests.clear()
    second = vector_experiment(vector_stack, corpus_file)
    exporter = Mock(spec=BaseExporter)
    second.exporters = (exporter,)
    with pytest.raises(RuntimeError, match="409"):
        second._wrapped_run()
    assert len(writes(vector_stack)) == 1
    assert vector_stack.collections == snapshot
    assert vector_stack.points == points
    exporter.export.assert_not_called()


def test_export_failure_preserves_remote_ready_and_search(vector_stack, corpus_file):
    index = vector_experiment(vector_stack, corpus_file)
    exporter = Mock(spec=BaseExporter)
    exporter.export.side_effect = OSError("receipt destination failed")
    index.exporters = (exporter,)
    with pytest.raises(OSError, match="receipt destination"):
        index._wrapped_run()
    assert descriptor(vector_stack)["state"] == "ready"
    search = vector_experiment(vector_stack, corpus_file, "002-interactive")
    assert [item.id for item in search.search("работа").items] == [" a1 ", "z2"]


@pytest.mark.parametrize("case", ["unfiltered", "value", "all_missing", "all_null"])
def test_smoke_selection_is_deterministic_and_covers_empty_values(
    vector_stack, corpus_file, case
):
    records = deepcopy(RECORDS)
    overrides = []
    if case == "unfiltered":
        overrides.extend(("~qdrant.filter_fields", "+qdrant.filter_fields={}"))
    elif case in ("all_missing", "all_null"):
        for record in records:
            for field in ("active", "rate", "grade", "location"):
                if case == "all_null":
                    record["fields"][field] = None
                else:
                    record["fields"].pop(field)
    corpus_file.write_text(json.dumps(records), encoding="utf-8")
    vector_experiment(vector_stack, corpus_file, "001-index", *overrides)._wrapped_run()
    query = next(
        r["body"] for r in vector_stack.requests if r["path"].endswith("/query")
    )
    assert query["params"] == {"exact": True, "indexed_only": False}
    assert query["limit"] == 1 and query["query"] == vector_stack.vector
    if case == "unfiltered":
        assert "filter" not in query
    else:
        assert query["filter"] == {
            "must": [{"key": "fields.active", "match": {"value": True}}]
        }
    assert descriptor(vector_stack)["verification"]["smoke"] is True


@pytest.mark.parametrize(
    "kind,condition",
    [
        ("keyword", {"match": {"value": "recsys-smoke-v1"}}),
        ("bool", {"match": {"value": True}}),
        ("integer", {"match": {"value": 0}}),
        ("float", {"range": {"gte": 0.0, "lte": 0.0}}),
    ],
)
def test_missing_filter_smoke_uses_typed_placeholder(
    vector_stack, corpus_file, kind, condition
):
    index = vector_experiment(
        vector_stack,
        corpus_file,
        "001-index",
        "~qdrant.filter_fields",
        f"+qdrant.filter_fields={{missing:{{path:[missing],type:{kind}}}}}",
    )
    index.run()
    query = next(
        r["body"] for r in vector_stack.requests if r["path"].endswith("/query")
    )
    assert query["filter"] == {"must": [{"key": "fields.missing", **condition}]}
    assert descriptor(vector_stack)["state"] == "ready"


def test_all_missing_smoke_rejects_nonempty_result(vector_stack, corpus_file):
    index = vector_experiment(
        vector_stack,
        corpus_file,
        "001-index",
        "~qdrant.filter_fields",
        "+qdrant.filter_fields={missing:{path:[missing],type:keyword}}",
    )
    vector_stack.failure = "smoke_wrong_filter"
    exporter = Mock(spec=BaseExporter)
    index.exporters = (exporter,)
    with pytest.raises(RuntimeError, match="smoke"):
        index._wrapped_run()
    assert descriptor(vector_stack)["state"] == "building"
    assert not any(r["method"] == "PATCH" for r in vector_stack.requests)
    exporter.export.assert_not_called()


def test_remote_mismatch_precedes_encode_and_manual_retry_works(
    vector_stack, corpus_file
):
    vector_experiment(vector_stack, corpus_file).run()
    search = vector_experiment(vector_stack, corpus_file, "002-interactive")
    source = descriptor(vector_stack)
    original_image = source["runtime"]["image"]
    source["runtime"]["image"] = "changed@sha256:" + "b" * 64
    vector_stack.requests.clear()
    with pytest.raises(ValueError, match="runtime"):
        search.search("работа")
    assert [r["path"] for r in vector_stack.requests] == [
        "/",
        "/collections/vacancies-g1",
    ]
    source["runtime"]["image"] = original_image
    assert len(search.search("работа").items) == 2


def test_malformed_second_batch_result_stops_without_any_export(
    vector_stack, corpus_file, tmp_path
):
    vector_experiment(vector_stack, corpus_file).run()
    queries = tmp_path / "queries.csv"
    queries.write_text("id,text\nq1,first\nq2,second\nq3,third\n", encoding="utf-8")
    batch = vector_experiment(
        vector_stack, corpus_file, "003-batch", f"experiment.input_path={queries}"
    )
    exporter = Mock(spec=BaseExporter)
    batch.exporters = (exporter,)
    before = vector_stack.query_calls
    vector_stack.fail_query_number = before + 2
    with pytest.raises(ValueError):
        batch._wrapped_run()
    assert vector_stack.query_calls == before + 2
    exporter.export.assert_not_called()


@pytest.mark.parametrize("depth,final_k", [(2, 3), (3, 0), (3, -1), (3, True)])
def test_retrieval_depth_contract_fails_before_http(
    vector_stack, corpus_file, depth, final_k
):
    with pytest.raises(InstantiationException, match="Invalid vector search"):
        vector_experiment(
            vector_stack,
            corpus_file,
            "002-interactive",
            f"retrieval.candidate_depth={depth}",
            f"retrieval.final_k={str(final_k).lower()}",
        )
    assert vector_stack.requests == []


def run_cli(stack, output, selection, *overrides, input_text=""):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "src.run",
            "--config-name",
            "vector",
            f"{GROUP}={selection}",
            *(
                value
                for value in site_overrides(stack.url)
                if not value.endswith("api_key=null")
            ),
            f"hydra.run.dir={output}",
            *overrides,
        ],
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "TEI_API_KEY": "TEI_NOT_LOGGED",
            "QDRANT_API_KEY": "QDRANT_NOT_LOGGED",
        },
    )


def test_native_vector_cli_indexes_interactive_and_batch(
    vector_stack, corpus_file, tmp_path
):
    index_dir, search_dir, batch_dir = (
        tmp_path / name for name in ("index", "search", "batch")
    )
    result = run_cli(vector_stack, index_dir, "001-index", f"corpus.path={corpus_file}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        json.loads((index_dir / "vector-index.json").read_text())["descriptor"]["state"]
        == "ready"
    )
    result = run_cli(
        vector_stack, search_dir, "002-interactive", input_text="Токарь\nСклад\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    sessions = json.loads((search_dir / "searches.json").read_text())["searches"]
    assert [session["query"] for session in sessions] == ["Токарь", "Склад"]
    assert sessions[0]["items"][1]["fields"] == RECORDS[0]["fields"]
    queries = tmp_path / "queries.csv"
    queries.write_text("id,text\nq1,Токарь\n", encoding="utf-8")
    result = run_cli(
        vector_stack, batch_dir, "003-batch", f"experiment.input_path={queries}"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (batch_dir / "searches.csv").is_file()
    assert "TEI_NOT_LOGGED" not in result.stdout + result.stderr
    assert "QDRANT_NOT_LOGGED" not in result.stdout + result.stderr
    assert (
        next(r for r in vector_stack.requests if r["path"] == "/")["api_key"]
        == "QDRANT_NOT_LOGGED"
    )
    assert (
        next(r for r in vector_stack.requests if r["path"] == "/info")["authorization"]
        == "Bearer TEI_NOT_LOGGED"
    )


@pytest.mark.parametrize("failure", ["upsert", "ready_readback"])
def test_failed_vector_index_cli_exits_without_receipt(
    vector_stack, corpus_file, tmp_path, failure
):
    vector_stack.failure = failure
    output = tmp_path / "failed"
    result = run_cli(vector_stack, output, "001-index", f"corpus.path={corpus_file}")
    assert result.returncode == 1
    assert not (output / "vector-index.json").exists()
    assert "Failed during experiment or export" in result.stdout + result.stderr
    assert "TEI_NOT_LOGGED" not in result.stdout + result.stderr
    assert "QDRANT_NOT_LOGGED" not in result.stdout + result.stderr


def test_corpus_uuid_collision_fails_before_all_http_and_export(
    vector_stack, corpus_file, monkeypatch
):
    index = vector_experiment(vector_stack, corpus_file)
    colliding_id = index.source.point_id(RECORDS[0]["id"])
    monkeypatch.setattr(index.source, "point_id", lambda identifier: colliding_id)
    exporter = Mock(spec=BaseExporter)
    index.exporters = (exporter,)
    with pytest.raises(ValueError, match="Colliding corpus point ids"):
        index._wrapped_run()
    assert vector_stack.requests == []
    assert vector_stack.collections == {}
    exporter.export.assert_not_called()


@pytest.fixture
def ten_record_corpus(corpus_file):
    records = []
    for number in range(10):
        record = deepcopy(RECORDS[number % len(RECORDS)])
        record["id"] = f" id-{number:02d} "
        record["fields"]["audit"] = [
            None,
            True,
            1,
            1.0,
            {"original_sequence": number, "unsigned": 2**64 - 1},
        ]
        records.append(record)
    corpus_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return corpus_file


def test_full_readback_covers_eight_then_two_records_before_ready(
    vector_stack, ten_record_corpus, tmp_path, monkeypatch
):
    records = json.loads(ten_record_corpus.read_text())
    index = vector_experiment(
        vector_stack, ten_record_corpus, "001-index", "embedding.limits.batch_inputs=8"
    )
    receipt_path = tmp_path / "receipt.json"
    index.exporters = (JSONExporter(str(receipt_path)),)
    retrieve = index.integration.retrieve
    received = []

    def capture(name, ids):
        result = retrieve(name, ids)
        received.extend(deepcopy(result))
        return result

    monkeypatch.setattr(index.integration, "retrieve", capture)
    index._wrapped_run()
    readbacks = [
        request
        for request in vector_stack.requests
        if request["method"] == "POST"
        and request["path"] == "/collections/vacancies-g1/points"
    ]
    assert [len(request["body"]["ids"]) for request in readbacks] == [8, 2]
    assert [
        identifier for request in readbacks for identifier in request["body"]["ids"]
    ] == [index.source.point_id(record["id"]) for record in records]
    assert canonical_bytes([point["payload"] for point in received]) == canonical_bytes(
        [
            {"document_id": record["id"], "fields": record["fields"]}
            for record in records
        ]
    )
    assert all(point["vector"] == vector_stack.vector for point in received)
    uploads = [
        request for request in writes(vector_stack) if "/points?" in request["path"]
    ]
    assert [len(request["body"]["points"]) for request in uploads] == [8, 2]
    receipt = json.loads(receipt_path.read_text())
    assert receipt["expected_count"] == receipt["verified_count"] == 10
    assert receipt["descriptor"]["verification"]["read_back_count"] == 10
    assert descriptor(vector_stack)["state"] == "ready"


@pytest.mark.parametrize("failure", ["missing", "duplicate", "payload_type", "vector"])
def test_second_readback_batch_failure_prevents_ready_and_receipt(
    vector_stack, ten_record_corpus, tmp_path, monkeypatch, failure
):
    index = vector_experiment(
        vector_stack, ten_record_corpus, "001-index", "embedding.limits.batch_inputs=8"
    )
    receipt_path = tmp_path / "receipt.json"
    exporter = Mock(spec=BaseExporter)
    index.exporters = (JSONExporter(str(receipt_path)), exporter)
    respond = vector_stack.respond
    batch_sizes = []

    def corrupt_second_batch(method, path, body):
        status, response = respond(method, path, body)
        if method == "POST" and path == "/collections/vacancies-g1/points":
            batch_sizes.append(len(body["ids"]))
            if len(batch_sizes) == 2:
                points = response["result"]
                if failure == "missing":
                    points.pop()
                elif failure == "duplicate":
                    points[-1] = deepcopy(points[0])
                elif failure == "payload_type":
                    points[-1]["payload"]["fields"]["audit"][2] = 1.0
                else:
                    points[-1]["vector"] = [0.0] * 768
        return status, response

    monkeypatch.setattr(vector_stack, "respond", corrupt_second_batch)
    with pytest.raises(RuntimeError):
        index._wrapped_run()
    assert batch_sizes == [8, 2]
    assert len(vector_stack.points["vacancies-g1"]) == 10
    assert descriptor(vector_stack)["state"] == "building"
    assert vector_stack.query_calls == 0
    assert not any(request["method"] == "PATCH" for request in writes(vector_stack))
    assert not receipt_path.exists()
    exporter.export.assert_not_called()


def test_corpus_digest_contract_preserves_exact_source_values_and_ignores_order():
    documents = [
        Vacancy(
            " ё ",
            {
                "title": "Welder",
                "extra": None,
                "nested": {"integer": 1, "float": 1.0, "boolean": True},
            },
        ),
        Vacancy("a", {"title": "Turner"}),
    ]
    # Published v1 bytes: header, UTF-8 ID order, canonical {id, fields}, LF per record.
    expected = "e18fe275f65aafb4a0359c494bf3b62a9577c10d3290839b72498db6509ed1d2"
    assert corpus_digest(documents) == expected
    reordered = deepcopy(list(reversed(documents)))
    reordered[-1].fields = dict(reversed(list(reordered[-1].fields.items())))
    reordered[-1].fields["nested"] = {"boolean": True, "float": 1.0, "integer": 1}
    assert corpus_digest(reordered) == expected
    for identifier in ("ё", " Ё ", " е\u0308 "):
        changed = deepcopy(documents)
        changed[0].id = identifier
        assert corpus_digest(changed) != expected
    for value in ("original extra field changed", 1, 1.0, True):
        changed = deepcopy(documents)
        changed[0].fields["extra"] = value
        assert corpus_digest(changed) != expected
    variants = []
    for value in (1, 1.0, True):
        changed = deepcopy(documents)
        changed[0].fields["extra"] = value
        variants.append(corpus_digest(changed))
    assert len(set(variants)) == 3


def test_late_green_readiness_does_not_publish_ready(
    vector_stack, corpus_file, monkeypatch
):
    from time import monotonic

    index = vector_experiment(vector_stack, corpus_file)
    now = [monotonic()]
    monkeypatch.setattr("src.experiments.indexing.vector.monotonic", lambda: now[0])
    original_collection = index.integration.collection

    def late_collection(name, **kwargs):
        result = original_collection(name, **kwargs)
        now[0] = kwargs["deadline"] + 1
        return result

    monkeypatch.setattr(index.integration, "collection", late_collection)
    exporter = Mock(spec=BaseExporter)
    index.exporters = (exporter,)
    with pytest.raises(TimeoutError, match="verification deadline"):
        index._wrapped_run()
    assert descriptor(vector_stack)["state"] == "building"
    assert not any(request["method"] == "PATCH" for request in writes(vector_stack))
    exporter.export.assert_not_called()

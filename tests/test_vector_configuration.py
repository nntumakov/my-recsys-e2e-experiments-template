import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from hydra import compose, initialize_config_dir
from hydra.errors import InstantiationException
from hydra.utils import instantiate
from omegaconf import OmegaConf
from omegaconf.errors import MissingMandatoryValue

from src.core.transformers.data import PipelineData
from src.domain.request.filters import QueryFilter
from src.experiments.batch.search import BatchSearchExperiment
from src.experiments.direct.search import SearchExperiment
from src.experiments.indexing.vector import VectorIndexExperiment

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (
    "ghcr.io/huggingface/text-embeddings-inference:turing-1.9.4@sha256:"
    "f78d17832f00f59f7d52e4b3b8495264257de3c18b773357a00ee341b2af852d"
)


def vector_config(wrapper="002-interactive", *overrides):
    with initialize_config_dir(config_dir=str(ROOT / "configs"), version_base="1.3"):
        return compose(
            config_name="vector",
            overrides=[
                f"experiments/vacancy-search/vector@experiment={wrapper}",
                f"embedding.runtime.image={IMAGE}",
                "qdrant.url=http://127.0.0.1:6333",
                "qdrant.collection=vacancies-local-g1",
                "experiment.exporters=[]",
                *overrides,
            ],
        )


@pytest.mark.parametrize(
    ("wrapper", "expected"),
    [
        ("001-index", VectorIndexExperiment),
        ("002-interactive", SearchExperiment),
        ("003-batch", BatchSearchExperiment),
    ],
)
def test_hydra_wrappers_instantiate_shared_components_without_http(wrapper, expected):
    overrides = []
    if wrapper == "001-index":
        overrides.append(
            f"corpus.path={ROOT / 'tests/fixtures/blue-collar/vacancies.json'}"
        )
    elif wrapper == "003-batch":
        overrides.append(
            f"experiment.input_path={ROOT / 'examples/blue-collar/queries.csv'}"
        )
    cfg = vector_config(wrapper, *overrides)
    experiment = instantiate(cfg.experiment, _convert_="all")
    assert isinstance(experiment, expected)
    if isinstance(experiment, VectorIndexExperiment):
        assert experiment.source.profile.id == experiment.embedder.profile.id
        assert experiment.source.recipe.id == experiment.renderer.id
    else:
        search = (
            experiment.search
            if isinstance(experiment, BatchSearchExperiment)
            else experiment
        )
        leaf = search.registry["vector_search"]
        assert leaf.source.profile.id == leaf.embedder.profile.id
        plan = leaf.prepare(
            PipelineData(filters=[QueryFilter("  СВАРЩИК  без опыта  ")])
        )
        assert plan.model_input == "query: СВАРЩИК без опыта"
        assert leaf.candidate_depth == 100
        assert search.registry["top_k"].k == 20
    assert OmegaConf.is_missing(cfg.embedding.runtime, "gpu_uuid")
    assert OmegaConf.is_missing(cfg.embedding.runtime, "cache_dir")


def test_search_does_not_require_corpus_path_but_index_does():
    cfg = vector_config()
    assert OmegaConf.is_missing(cfg.corpus, "path")
    instantiate(cfg.experiment, _convert_="all")
    index = vector_config("001-index")
    with pytest.raises(MissingMandatoryValue, match="reader.path"):
        instantiate(index.experiment, _convert_="all")


@pytest.mark.parametrize(
    "override",
    [
        "retrieval.final_k=0",
        "retrieval.final_k=true",
        "retrieval.final_k=101",
        "retrieval.candidate_depth=0",
        "retrieval.hnsw_ef=0",
        "retrieval.indexed_only=true",
    ],
)
def test_retrieval_contract_rejects_invalid_composition(override):
    cfg = vector_config("002-interactive", override)
    with pytest.raises(InstantiationException):
        instantiate(cfg.experiment, _convert_="all")


@pytest.mark.parametrize(
    "override",
    [
        "qdrant.vector.size=3",
        "qdrant.vector.size=true",
        "qdrant.vector.distance=Dot",
        "qdrant.vector=null",
    ],
)
def test_vector_schema_configuration_is_validated_before_http(override):
    cfg = vector_config("002-interactive", override)
    with pytest.raises(InstantiationException, match="vector settings disagree"):
        instantiate(cfg.experiment, _convert_="all")


def test_non_object_vector_configuration_is_rejected_before_http():
    cfg = vector_config()
    cfg.qdrant.vector = []
    with pytest.raises(InstantiationException, match="vector settings disagree"):
        instantiate(cfg.experiment, _convert_="all")


def test_host_port_and_response_limits_are_interpolated_from_one_config():
    cfg = vector_config(
        "002-interactive",
        "embedding.runtime.host_port=8099",
        "embedding.limits.response_bytes=2048",
        "embedding.limits.tokenize_response_bytes=4096",
    )
    experiment = instantiate(cfg.experiment, _convert_="all")
    encoder = experiment.registry["vector_search"].embedder
    assert encoder.integration.url == "http://127.0.0.1:8099"
    assert (
        encoder.integration.response_bytes == encoder.limits["response_bytes"] == 2048
    )
    assert encoder.integration.tokenize_response_bytes == 4096
    assert cfg.qdrant.vector.size == encoder.profile.dimensions


@pytest.mark.parametrize("wrapper", [None, "001-index", "002-interactive", "003-batch"])
def test_common_site_import_and_cli_group_package_overrides(tmp_path, wrapper):
    site = {
        "defaults": ["vector", "_self_"],
        "embedding": {"runtime": {"image": IMAGE}},
        "corpus": {"path": "/srv/recsys/corpus/vacancies.json"},
        "qdrant": {"url": "http://127.0.0.1:6333", "collection": "vacancies-site-g1"},
    }
    OmegaConf.save(OmegaConf.create(site), tmp_path / "vector-site.yaml")
    command = [
        sys.executable,
        "-m",
        "src.run" if wrapper else "src.integrations.embedding.launch",
        "--config-dir",
        str(tmp_path),
        "--config-name",
        "vector-site",
        "--cfg",
        "job",
    ]
    if wrapper:
        command.append(f"experiments/vacancy-search/vector@experiment={wrapper}")
    if wrapper == "003-batch":
        command.append("experiment.input_path=/srv/recsys/queries.csv")
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, cwd=ROOT, timeout=20
    )
    cfg = OmegaConf.create(result.stdout)
    assert cfg.qdrant.source.runtime.image == IMAGE
    assert cfg.qdrant.collection == "vacancies-site-g1"
    assert cfg.embedding.profile.dimensions == 768
    if wrapper == "001-index":
        assert cfg.experiment._target_.endswith("VectorIndexExperiment")
    elif wrapper == "003-batch":
        assert cfg.experiment._target_.endswith("BatchSearchExperiment")
        assert cfg.experiment.search._target_.endswith("SearchExperiment")
        assert cfg.experiment.input_path == "/srv/recsys/queries.csv"
    else:
        assert cfg.experiment._target_.endswith("SearchExperiment")


def test_vector_notebook_executes_twice_with_local_http(
    vector_stack, tmp_path, monkeypatch
):
    notebook_path = ROOT / "src/experiments/notebooks/vector.ipynb"
    notebook = json.loads(notebook_path.read_text())
    corpus = tmp_path / "vacancies.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "welder",
                    "fields": {
                        "title": "Сварщик",
                        "description": "Без опыта",
                        "nested": [1, True],
                    },
                },
                {
                    "id": "driver",
                    "fields": {"title": "Водитель", "description": "Категория C"},
                },
            ],
            ensure_ascii=False,
        )
    )
    queries = tmp_path / "queries.csv"
    queries.write_text(
        "id,text\nq1,Сварщик без опыта\nq2,Водитель C\n", encoding="utf-8"
    )
    respond = vector_stack.respond

    def audit_tokens(method, path, body):
        if path == "/tokenize":
            count = 513 if "Сварщик" in body["inputs"][0] else 512
            token = {"id": 0, "text": "x", "special": False, "start": 0, "stop": 1}
            return 200, [[token] * count]
        return respond(method, path, body)

    monkeypatch.setattr(vector_stack, "respond", audit_tokens)
    monkeypatch.chdir(notebook_path.parent)
    for iteration in range(2):
        name = f"notebook-g{iteration}"
        site = {
            "defaults": ["vector", "_self_"],
            "embedding": {
                "runtime": {"image": IMAGE},
                "client": {"url": vector_stack.url},
            },
            "corpus": {"path": str(corpus)},
            "qdrant": {"url": vector_stack.url, "collection": name},
            "retrieval": {"candidate_depth": 2, "final_k": 1},
        }
        site_path = tmp_path / "vector-site.yaml"
        OmegaConf.save(OmegaConf.create(site), site_path)
        output_dir = tmp_path / f"output-{iteration}"
        namespace: dict[str, Any] = {}
        request_offset = len(vector_stack.requests)
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            assert cell["execution_count"] is None and cell["outputs"] == []
            source = "".join(cell["source"])
            before = len(vector_stack.requests)
            exec(compile(source, f"{notebook_path}:cell-{index}", "exec"), namespace)
            if "parameters" in cell["metadata"].get("tags", []):
                namespace.update(
                    site_config=site_path,
                    input_path=queries,
                    output_dir=output_dir,
                    build_collection=True,
                )
            if "plan = leaf.prepare" in source:
                assert len(vector_stack.requests) == before
            if "candidates = leaf.integration.query" in source:
                assert vector_stack.requests[-1]["body"] == namespace["native_body"]
                assert len(namespace["candidates"].items) == 2
        requests = vector_stack.requests[request_offset:]
        tokenize = [r for r in requests if r["path"] == "/tokenize"]
        assert len(tokenize) == 2
        assert all(
            len(r["body"]["inputs"]) == 1 and r["body"]["add_special_tokens"] is True
            for r in tokenize
        )
        assert tokenize[0]["body"]["inputs"] == ["passage: Сварщик\n\nБез опыта"]
        assert namespace["audit_report"]["documents"] == 2
        assert namespace["audit_report"]["truncated_documents"] == 1
        assert namespace["audit_report"]["truncation_share"] == 0.5
        assert namespace["audit_report"]["max_tokens"] == 513
        assert "tokens" not in namespace
        assert len(namespace["token_preview"]) == 8
        assert len(namespace["serp"].items) == 1
        assert namespace["batch"].search is namespace["experiment"]
        assert (
            vector_stack.collections[name]["config"]["metadata"]["recsys"]["state"]
            == "ready"
        )
        receipt = json.loads((output_dir / "vector-index.json").read_text())
        assert receipt["verified_count"] == 2
        with (output_dir / "searches.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 2
        assert {row["query_id"] for row in rows} == {"q1", "q2"}


def test_notebook_audit_failure_has_no_success_report_or_collection_write(
    vector_stack, tmp_path, monkeypatch
):
    notebook_path = ROOT / "src/experiments/notebooks/vector.ipynb"
    notebook = json.loads(notebook_path.read_text())
    corpus = tmp_path / "vacancies.json"
    corpus.write_text('[{"id":"a","fields":{"title":"Vacancy"}}]')
    site = {
        "defaults": ["vector", "_self_"],
        "embedding": {
            "runtime": {"image": IMAGE},
            "client": {"url": vector_stack.url},
            "limits": {"tokenize_response_bytes": 16},
        },
        "corpus": {"path": str(corpus)},
        "qdrant": {"url": vector_stack.url, "collection": "audit-failure"},
    }
    site_path = tmp_path / "vector-site.yaml"
    OmegaConf.save(OmegaConf.create(site), site_path)
    namespace: dict[str, Any] = {}
    monkeypatch.chdir(notebook_path.parent)
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        compiled = compile(source, f"{notebook_path}:cell-{index}", "exec")
        if "audit_report = None" in source:
            with pytest.raises(RuntimeError) as caught:
                exec(compiled, namespace)
            assert isinstance(caught.value.__cause__, ValueError)
            break
        exec(compiled, namespace)
        if "parameters" in cell["metadata"].get("tags", []):
            namespace.update(site_config=site_path, build_collection=True)
    else:
        pytest.fail("Notebook does not contain tokenizer audit")
    assert namespace["audit_report"] is None
    assert vector_stack.collections == {}
    assert not any(
        request["method"] in ("PUT", "PATCH") for request in vector_stack.requests
    )

import json
import sys
from html import escape
from pathlib import Path
from types import ModuleType

import pytest

from src.experiments.research.notebook import (
    StudyNotebookSession,
    compose_site_experiment,
)
from src.experiments.research.study import StudyExperiment

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "src/experiments/notebooks/search-study.ipynb"


def notebook_cells():
    return {
        cell["id"]: "".join(cell["source"])
        for cell in json.loads(NOTEBOOK.read_text())["cells"]
        if cell["cell_type"] == "code"
    }


@pytest.fixture
def presentation(monkeypatch):
    rendered = []
    ipython = ModuleType("IPython")
    display = ModuleType("IPython.display")

    class HTML:
        def __init__(self, data):
            self.data = data

    def present(value):
        rendered.append(value.data if isinstance(value, HTML) else value)

    setattr(display, "HTML", HTML)
    setattr(display, "display", present)
    monkeypatch.setitem(sys.modules, "IPython", ipython)
    monkeypatch.setitem(sys.modules, "IPython.display", display)
    return rendered


def prepare_notebook(monkeypatch, site):
    monkeypatch.setenv("RECSYS_STUDY_SITE", str(site.site_config))
    monkeypatch.setenv("RECSYS_STUDY_OVERRIDES", json.dumps(site.overrides))
    return {"__name__": "__notebook__"}


def test_all_cells_twice_execute_real_local_http_and_false_guards(
    monkeypatch, study_stack, presentation
):
    namespace = prepare_notebook(monkeypatch, study_stack)
    cells = notebook_cells()
    run_ids = []
    before_outputs = (ROOT / "outputs/search-study").exists()
    for _ in range(2):
        for name, source in cells.items():
            before_network = len(study_stack.stack.requests) + len(
                study_stack.os_requests
            )
            exec(compile(source, f"{NOTEBOOK}:{name}", "exec"), namespace)
            after_network = len(study_stack.stack.requests) + len(
                study_stack.os_requests
            )
            if name != "compare":
                assert after_network == before_network, name
        snapshot = namespace["session"].require_current()
        run_ids.append(snapshot.run_id)
        assert tuple(namespace["study"].variants) == tuple(study_stack.study.variants)
        assert [row["variant"] for row in snapshot.summary()] == [
            "f2",
            "f3",
            "f4",
            "e1",
            "e2",
        ]
        assert namespace["preview"]["inputs"] == snapshot.results()[0][1]
        rows = snapshot.rank_matrix(reference_variant="f2")
        topical_a = next(
            row for row in rows if row["group"] == "fulltext" and row["id"] == "A"
        )
        assert topical_a["ranks"]["f2"] == 1
        assert topical_a["ranks"]["f3"] == 2
        assert topical_a["deltas"]["f3"] == -1
        assert namespace["SAVE_CURRENT"] is False
        assert all(
            namespace[name] is False
            for name in (
                "RUN_CORPUS_AUDIT",
                "RUN_TOKEN_AUDIT",
                "RUN_NATIVE_EMBEDDING",
                "RUN_BATCH",
                "RUN_BUILD",
            )
        )
    assert run_ids[0] != run_ids[1]
    assert (ROOT / "outputs/search-study").exists() == before_outputs
    assert not [
        r
        for r in study_stack.stack.requests
        if r["method"] in {"PUT", "PATCH", "DELETE"}
    ]
    assert any(
        "Несопоставимые" in value for value in presentation if isinstance(value, str)
    )
    assert any(run_ids[0] in value for value in presentation if isinstance(value, str))
    assert any(run_ids[1] in value for value in presentation if isinstance(value, str))


def test_manual_city_variant_reference_and_card_changes_are_snapshot_reads(
    monkeypatch, study_stack, presentation
):
    namespace = prepare_notebook(monkeypatch, study_stack)
    cells = notebook_cells()
    for name in ("setup-inputs", "compose", "search-inputs", "preview", "compare"):
        exec(cells[name], namespace)
    old = namespace["session"].require_current()
    old_json = old.to_dict()
    namespace["text"] = "комплектовщик ТСД"
    namespace["filter_values"] = {"city": "Санкт-Петербург", "active": False}
    namespace["variants"] = ["f1", "e1"]
    namespace["reference_variant"] = "e1"
    exec(cells["preview"], namespace)
    exec(cells["compare"], namespace)
    current = namespace["session"].require_current()
    assert namespace["preview"]["inputs"] == current.results()[0][1]
    assert current.results()[0][1]["effective_filters"] == [
        {"field": "city", "value": "Санкт-Петербург"},
        {"field": "active", "value": False},
    ]
    assert old.to_dict() == old_json
    assert "Москва" in old.rank_lists_html()
    assert "Санкт-Петербург" in current.rank_lists_html()
    before = len(study_stack.stack.requests), len(study_stack.os_requests)
    for name in ("summary", "rank-lists", "matrix", "card", "diagnostics"):
        exec(cells[name], namespace)
    assert before == (len(study_stack.stack.requests), len(study_stack.os_requests))
    assert namespace["session"].require_current().run_id == current.run_id


def test_new_compare_validation_failure_invalidates_current_and_retry_works(
    study_stack, tmp_path
):
    session = StudyNotebookSession(study_stack.study)
    old = session.compare(
        "комплектовщик", variants=["f1"], filter_values={"city": "Москва"}
    )
    before = len(study_stack.stack.requests), len(study_stack.os_requests)
    with pytest.raises(ValueError):
        session.compare(
            "комплектовщик", variants=["f1"], filter_values={"unknown": True}
        )
    assert before == (len(study_stack.stack.requests), len(study_stack.os_requests))
    assert session.current is None
    with pytest.raises(RuntimeError, match="No successful current"):
        session.require_current()
    with pytest.raises(RuntimeError, match="No successful current"):
        session.save(tmp_path / "unpublished")
    assert not (tmp_path / "unpublished").exists()
    assert old.run_id in old.rank_lists_html()
    retried = session.compare("комплектовщик", variants=["f1"])
    assert retried.run_id != old.run_id


def test_explicit_save_owned_run_no_http_no_overwrite_credentials(
    study_stack, tmp_path
):
    session = StudyNotebookSession(study_stack.study)
    snapshot = session.compare(
        "комплектовщик", variants=["f1", "e1"], filter_values={"active": True}
    )
    before = len(study_stack.stack.requests), len(study_stack.os_requests)
    path = session.save(tmp_path)
    assert path == tmp_path / snapshot.run_id / "search-study.json"
    saved = json.loads(path.read_text())
    assert saved == snapshot.to_dict()
    assert "authorization" not in path.read_text()
    assert "api_key" not in path.read_text()
    with pytest.raises(FileExistsError):
        session.save(tmp_path)
    assert json.loads(path.read_text()) == saved
    assert list(path.parent.iterdir()) == [path]
    assert before == (len(study_stack.stack.requests), len(study_stack.os_requests))


def test_later_engine_failure_leaves_no_current_partial_save_and_manual_retry(
    study_stack, tmp_path
):
    session = StudyNotebookSession(study_stack.study)
    previous = session.compare("комплектовщик", variants=["f1"])
    study_stack.stack.fail_query_number = study_stack.stack.query_calls + 1
    with pytest.raises(ValueError):
        session.compare(
            "комплектовщик", variants=["f1", "e1"], filter_values={"city": "Москва"}
        )
    assert session.current is None
    with pytest.raises(RuntimeError):
        session.save(tmp_path / "partial")
    assert not (tmp_path / "partial").exists()
    assert previous.run_id in previous.rank_lists_html()
    study_stack.stack.fail_query_number = None
    retry = session.compare("комплектовщик", variants=["f1", "e1"])
    assert retry.run_id != previous.run_id


def test_recompose_parameter_change_identity_old_snapshot_and_no_build(study_stack):
    first = compose_site_experiment(study_stack.site_config, study_stack.overrides)
    second = compose_site_experiment(
        study_stack.site_config, [*study_stack.overrides, "study.preference_budget=0.1"]
    )
    assert isinstance(first, StudyExperiment)
    assert isinstance(second, StudyExperiment)
    assert first.composition_id != second.composition_id
    initial = StudyNotebookSession(first).compare(
        "комплектовщик без опыта", variants=["f2", "e2"]
    )
    old_json = initial.to_dict()
    changed = StudyNotebookSession(second).compare(
        "комплектовщик без опыта", variants=["f2", "e2"]
    )
    assert initial.to_dict() == old_json
    initial_session, changed_session = (
        initial.to_dict()["session"],
        changed.to_dict()["session"],
    )
    assert isinstance(initial_session, dict) and isinstance(changed_session, dict)
    assert initial_session["composition_id"] != changed_session["composition_id"]
    assert not [
        r
        for r in study_stack.stack.requests
        if r["method"] in {"PUT", "PATCH", "DELETE"}
    ]


def test_read_full_description_escaping_and_diagnostics_no_network(study_stack):
    long_body = (
        "Описание целиком " + "требование " * 80 + '<script>alert("source")</script>'
    )
    study_stack.records[0]["fields"].update(
        {"title": "Карточка <script>", "description": long_body}
    )
    artifact = StudyNotebookSession(study_stack.study).compare(
        "комплектовщик", variants=["f1", "e1"]
    )
    before = len(study_stack.stack.requests), len(study_stack.os_requests)
    identifier = artifact.serp("manual", "f1").items[0].id
    card = artifact.card_html(identifier, variant="f1")
    assert escape(long_body) in card
    assert '<script>alert("source")</script>' not in card
    assert "Москва" in card
    assert "active: true" in card
    artifact.matrix_html(reference_variant="f1")
    artifact.rank_lists_html(variants=["f1"])
    artifact.diagnostics_html()
    with pytest.raises(KeyError):
        artifact.card_html("absent-id")
    assert before == (len(study_stack.stack.requests), len(study_stack.os_requests))

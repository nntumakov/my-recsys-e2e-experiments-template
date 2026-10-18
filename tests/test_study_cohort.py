import json

import pytest

from src.components.corpus.study import StudyCohortReader


def test_cohort_audit_reports_all_missing_invalid_without_filtering(tmp_path):
    path = tmp_path / "cohort.json"
    records = [
        {"id": "good", "fields": {"title": " Токарь ", "description": " задачи\n "}},
        {"id": "missing", "fields": {}},
        {"id": "invalid", "fields": {"title": 3, "description": " \t\n"}},
    ]
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    reader = StudyCohortReader(path)
    audit = reader.audit()
    assert audit["count"] == 3
    assert audit["ids"] == ["good", "missing", "invalid"]
    assert audit["issues"] == [
        {"document_id": "missing", "field": "title", "reason": "missing_or_null"},
        {"document_id": "missing", "field": "description", "reason": "missing_or_null"},
        {"document_id": "invalid", "field": "title", "reason": "not_string"},
        {
            "document_id": "invalid",
            "field": "description",
            "reason": "empty_after_normalization",
        },
    ]
    with pytest.raises(ValueError, match="text audit failed"):
        reader.read()
    assert json.loads(path.read_text()) == records


def test_cohort_custom_bindings_preserve_raw_fields_and_exact_ids(tmp_path):
    path = tmp_path / "cohort.json"
    fields = {"heading": "  Токарь\t ", "body": " Деталь  Е\u0308 ", "other": [1, None]}
    path.write_text(json.dumps([{"id": " A ", "fields": fields}]), encoding="utf-8")
    reader = StudyCohortReader(path, "heading", "body")
    assert reader.audit()["valid"] is True
    (vacancy,) = reader.read()
    assert vacancy.id == " A "
    assert vacancy.fields == fields


@pytest.mark.parametrize(
    "records", [[], [{"id": "A", "fields": {}}] * 2, [{"id": "A", "fields": []}]]
)
def test_structural_errors_remain_errors_in_audit(tmp_path, records):
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    with pytest.raises(ValueError):
        StudyCohortReader(path).audit()


@pytest.mark.parametrize(
    "title,body", [("title", "title"), ("", "body"), ("title", None)]
)
def test_invalid_field_bindings_rejected_before_read(title, body):
    with pytest.raises(ValueError, match="distinct"):
        StudyCohortReader("missing.json", title, body)

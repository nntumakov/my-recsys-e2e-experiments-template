"""The research pack preserves contracts without pretending to rank live data."""

import csv
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from src.components.query.jobboard.catalog import work_parser
from src.components.query.study import decompose_query
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.jobboard.query import SourceProfile
from src.core.transformers.opensearch.study import FulltextStudyTransformer
from src.domain.request.filters import QueryFilter, TermFilter
from src.integrations.opensearch.client import OpenSearchIntegration

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "configs/study/cases"


def read(name):
    return yaml.safe_load((CASES / name).read_text())


@pytest.fixture
def parser():
    lexicons = ROOT / "configs/jobboard/lexical/lexicons/blue-collar"
    settings = yaml.safe_load((lexicons / "ru.yaml").read_text())
    names = {
        "role": "roles",
        "skill": "skills",
        "qualification": "qualifications",
        "experience": "experience",
        "schedule": "schedules",
        "employment": "employment",
        "work_condition": "conditions",
        "location": "locations",
    }
    return work_parser(
        entries={
            kind: yaml.safe_load((lexicons / "ru" / f"{name}.yaml").read_text())
            for kind, name in names.items()
        },
        protected=settings["protected"],
        introductions=settings["introductions"],
        unsupported_markers=settings["unsupported_markers"],
        syntax_whitelist=settings["syntax_whitelist"],
    )


def test_pack_has_independent_described_pairs_and_all_five_slices():
    scenarios = read("scenarios.yaml")
    assert len(scenarios) >= 40
    assert Counter(s["slice"] for s in scenarios) == {
        "roles": 8,
        "phrasing": 8,
        "soft": 8,
        "long": 8,
        "hard": 8,
    }
    assert len({s["id"] for s in scenarios}) == len(scenarios)
    documents = {
        d["id"]: d["fields"]
        for d in json.loads((ROOT / "tests/fixtures/study/vacancies.json").read_text())
    }
    for scenario in scenarios:
        assert (
            scenario["query"]
            and scenario["human_rationale"]
            and scenario["expected_contract"]
        )
        assert scenario["quality_claim"] == "hypothesis_requires_live_source"
        assert (
            scenario["positive_document"] in documents
            and scenario["negative_document"] in documents
        )
        assert (
            documents[scenario["positive_document"]]
            != documents[scenario["negative_document"]]
            or scenario["id"] == "s40"
        )
        assert set(scenario["filters"]) >= {"city", "active"}
    assert len({s["human_rationale"] for s in scenarios}) == len(scenarios)


def test_every_main_mechanism_has_difference_control_and_negative():
    mechanisms = read("mechanisms.yaml")
    assert set(mechanisms) == {
        *(f"f{i}" for i in range(1, 6)),
        *(f"e{i}" for i in range(1, 6)),
    }
    identifiers = {s["id"] for s in read("scenarios.yaml")}
    for item in mechanisms.values():
        assert item["differentiating_scenario"] in identifiers
        assert item["negative_scenario"] in identifiers
        assert item["differentiating_scenario"] != item["negative_scenario"]
        assert item["mechanism_off_control"] and item["observable_rule"]


def test_pack_preview_and_expected_decomposition_no_http(parser, monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("Offline pack preview cannot invoke search")

    integration = OpenSearchIntegration("http://127.0.0.1:1")
    monkeypatch.setattr(integration, "search", prohibited)
    source = SourceProfile(
        "vacancies",
        ["title", "description"],
        "title",
        {
            "city": "keyword",
            "active": "boolean",
            "schedule": "keyword",
            "employment": "keyword",
        },
    )
    leaves = [
        FulltextStudyTransformer(integration, parser, source, variant=f"f{i}")
        for i in range(1, 6)
    ]
    for case in read("scenarios.yaml"):
        filters = case["filters"]
        terms = [
            TermFilter(k, v) for k, v in filters.items() if k != "additional_terms"
        ]
        terms.extend(
            TermFilter(term["field"], term["value"])
            for term in filters.get("additional_terms", [])
        )
        data = PipelineData(filters=(QueryFilter(case["query"]), *terms))
        for leaf in leaves:
            plan = leaf.preview(data)
            assert plan["original_text"] == case["query"]
            assert plan["requests"]
        parsed = decompose_query(case["query"], parser)
        assert [h.rule_id for h in parsed.parsed.hints] == case["expected_parse"][
            "concepts"
        ]
        assert parsed.abstention == case["expected_parse"]["abstention"]
        assert parsed.topic_text == case["expected_parse"]["topic"]
    assert not data.items.items


def test_all_existing_249_query_outcomes_remain_and_study_previews(parser, monkeypatch):
    existing = yaml.safe_load(
        (ROOT / "configs/jobboard/lexical/cases/blue-collar/queries.yaml").read_text()
    )
    assert len(existing) == 249
    integration = OpenSearchIntegration("http://127.0.0.1:1")
    monkeypatch.setattr(
        integration,
        "search",
        lambda **kwargs: pytest.fail("No HTTP in preview regression"),
    )
    source = SourceProfile("vacancies", ["title", "description"], "title")
    leaves = [
        FulltextStudyTransformer(integration, parser, source, variant=f"f{i}")
        for i in range(1, 6)
    ]
    for case in existing:
        parsed = parser.parse(case["text"])
        if "concepts" in case["expected"]:
            assert [h.rule_id for h in parsed.hints] == case["expected"]["concepts"]
        if "structured" in case["expected"]:
            assert parsed.structured_enabled == case["expected"]["structured"]
        for leaf in leaves:
            assert leaf.preview(PipelineData(filters=(QueryFilter(case["text"]),)))[
                "requests"
            ]


def test_boundary_pack_is_explicit_and_csv_preserves_query_identity():
    boundaries = {b["id"]: b for b in read("boundaries.yaml")}
    assert set(boundaries) >= {
        "unicode-byte-offsets",
        "unicode-expansion",
        "title-missing",
        "description-missing",
        "scalar-array",
        "scalar-bool-integer",
        "title-cutoff",
        "body-tail",
        "chunk-cap",
        "concentration",
        "opportunity-bias",
    }
    assert boundaries["concentration"]["documents"] == 3
    assert boundaries["concentration"]["chunks_per_document"] == 64
    assert boundaries["opportunity-bias"]["extra_unrelated_chunks"] == [0, 5, 20]
    with (ROOT / "examples/study/queries.csv").open() as file:
        rows = list(csv.DictReader(file))
    assert [(r["id"], r["text"]) for r in rows] == [
        (s["id"], s["query"]) for s in read("scenarios.yaml")
    ]

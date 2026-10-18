from copy import deepcopy

from src.components.reporting.artifacts.study import SearchStudyArtifact
from src.domain import JSONValue
from src.domain.document.vacancy import Vacancy
from src.domain.study import StudySERP
from src.experiments.research.filters import StudyFilterBindings


def artifact(fields, *, comparable=False):
    bindings = {
        "city": {
            "type": "keyword",
            "opensearch_field": "location.city.keyword",
            "opensearch_source_path": ["location", "city"],
            "qdrant_path": ["location", "city"],
        },
        "active": {
            "type": "boolean",
            "opensearch_field": "status.active",
            "opensearch_source_path": ["status", "active"],
            "qdrant_path": ["status", "active"],
        },
        "schedule": {
            "type": "keyword",
            "opensearch_field": "schedule.keyword",
            "opensearch_source_path": ["schedule"],
            "qdrant_path": ["schedule"],
        },
    }
    session: dict[str, JSONValue] = {
        "run_id": "snapshot",
        "composition_id": "composition",
        "reference_variant": "f1",
        "comparability": {"comparable": comparable},
        "filter_bindings": StudyFilterBindings(bindings).to_dict(),
        "descriptions": {"f1": {"family": "fulltext"}, "e5": {"family": "embedding"}},
    }
    inputs = {"original_text": "запрос", "effective_filters": []}
    return SearchStudyArtifact(
        session,
        [
            (
                "manual",
                inputs,
                "f1",
                StudySERP(
                    [Vacancy("A", deepcopy(fields), 1.0)],
                    {"score_type": "native_lexical"},
                ),
            ),
            (
                "manual",
                inputs,
                "e5",
                StudySERP(
                    [Vacancy("A", deepcopy(fields), 0.8)],
                    {
                        "score_type": "max_native_cosine",
                        "hits": {
                            "A": {
                                "winner_passage": "Title\nBody <script>",
                                "winner_fragment": {"ordinal": 2},
                            }
                        },
                    },
                ),
            ),
        ],
    )


def test_card_uses_explicit_source_paths_and_distinguishes_missing_null():
    result = artifact(
        {
            "title": "Title",
            "description": "Body",
            "location": {"city": "Москва"},
            "status": {"active": None},
        }
    )
    for variant in ("f1", "e5"):
        card = result.card_html("A", variant=variant)
        assert "city: &quot;Москва&quot;" in card
        assert "active: null" in card
        assert "schedule: не указано" in card
        assert "city: не указано" not in card
    winner = result.card_html("A", variant="e5")
    assert "Победивший passage E5" in winner
    assert "Title\nBody &lt;script&gt;" in winner
    assert (
        result.card("A", variant="e5")["occurrences"][0]["hit_evidence"][
            "winner_fragment"
        ]["ordinal"]
        == 2
    )


def test_incomparable_sources_keep_distinct_fields_and_reference_ranks():
    result = artifact({"title": "Title", "description": "Body"})
    rows = result.rank_matrix()
    assert [row["group"] for row in rows] == ["fulltext", "embedding"]
    assert [row["reference_variant"] for row in rows] == ["f1", "e5"]
    assert all(set(row["ranks"]) == {row["reference_variant"]} for row in rows)
    assert all(row["deltas"][row["reference_variant"]] == 0 for row in rows)
    assert {
        row["group"]
        for row in artifact({"title": "Title"}, comparable=True).rank_matrix()
    } == {"shared"}


def test_explicit_reference_cannot_silently_select_another_variant():
    import pytest

    result = artifact({"title": "Title", "description": "Body"})
    with pytest.raises(ValueError, match="Reference"):
        result.rank_matrix(variants=["f1"], reference_variant="e5")
    assert result.rank_matrix(variants=["e5"])[0]["reference_variant"] == "e5"

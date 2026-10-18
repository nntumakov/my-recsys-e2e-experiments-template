import pytest

from src.core.registry import BasicTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.aggregation.rrf import ReciprocalRankFusionTransformer
from src.core.transformers.data import PipelineData
from src.core.transformers.extractors.score import FieldScoreExtractor
from src.core.transformers.transformers.branch import BranchTransformer
from src.core.transformers.transformers.pipeline import PipelineTransformer
from src.core.transformers.transformers.top_k import TopKTransformer
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter, TermFilter


class Mutate(BaseTransformer):
    def __init__(self, calls, fail=False):
        self.calls, self.fail = calls, fail

    def transform(self, data, *, registry):
        self.calls.append(data)
        data.context["changed"] = True
        data.items.items[0].fields["nested"].append("changed")
        data.add_filter(TermFilter("city", "Moscow"))
        if self.fail:
            raise RuntimeError("branch failure")
        return data


def test_branch_isolation_and_copy():
    calls = []
    query = QueryFilter("python")
    data = PipelineData(items=SERP([Vacancy("v", {"nested": []})]), filters=[query])
    registry = BasicTransformerRegistry({"mutate": Mutate(calls)})
    assert (
        BranchTransformer({"a": "mutate", "b": "mutate"}).transform(
            data, registry=registry
        )
        is data
    )
    assert data.context == {} and data.items.items[0].fields == {"nested": []}
    assert data.filters == (query,)
    assert calls[0] is not calls[1]
    assert all(branch.filters[0] is query for branch in calls)
    assert all(
        branch.items.items[0].fields == {"nested": ["changed"]} for branch in calls
    )
    shallow, deep = data.copy(), data.copy(deep=True)
    assert shallow.branches is not data.branches
    assert shallow.branches["a"] is data.branches["a"]
    assert deep.branches["a"].filters[0] is query
    deep.branches["a"].items.items[0].fields.clear()
    assert data.branches["a"].items.items[0].fields
    with pytest.raises(ValueError, match="Merge existing"):
        BranchTransformer({"c": "mutate"}).transform(data, registry=registry)


def test_branch_failure_does_not_publish_and_missing_target_does_not_execute():
    calls = []
    data = PipelineData(items=SERP([Vacancy("v", {"nested": []})]))
    registry = BasicTransformerRegistry(
        {"ok": Mutate(calls), "fail": Mutate(calls, True)}
    )
    with pytest.raises(KeyError):
        BranchTransformer({"a": "ok", "b": "missing"}).transform(
            data, registry=registry
        )
    assert not calls
    with pytest.raises(RuntimeError, match="branch failure"):
        BranchTransformer({"a": "ok", "b": "fail", "c": "ok"}).transform(
            data, registry=registry
        )
    assert len(calls) == 2 and not data.branches
    assert data.items.items[0].fields == {"nested": []}


def test_registry_validates_branch_edges():
    with pytest.raises(KeyError):
        BasicTransformerRegistry({"b": BranchTransformer({"a": "missing"})})
    with pytest.raises(ValueError, match="Cyclic"):
        BasicTransformerRegistry(
            {"b": BranchTransformer({"a": "p"}), "p": PipelineTransformer(["b"])}
        )


def test_unmerged_nested_branch_does_not_publish_parent():
    registry = BasicTransformerRegistry(
        {
            "leaf": TopKTransformer(1),
            "nested": BranchTransformer({"inner": "leaf"}),
        }
    )
    data = PipelineData(items=SERP([Vacancy("a")]))
    with pytest.raises(ValueError, match="Nested branches"):
        BranchTransformer({"outer": "nested"}).transform(data, registry=registry)
    assert not data.branches
    assert [item.id for item in data.items.items] == ["a"]


def test_all_branch_copies_precede_execution():
    class UncopyableVacancy(Vacancy):
        def __deepcopy__(self, memo):
            raise ValueError("cannot copy")

    calls = []
    registry = BasicTransformerRegistry({"step": Mutate(calls)})
    data = PipelineData(items=SERP([UncopyableVacancy("a")]))
    with pytest.raises(ValueError, match="cannot copy"):
        BranchTransformer({"a": "step"}).transform(data, registry=registry)
    assert not calls and not data.branches


def test_rrf_ranking_ties_and_field_ownership():
    first = Vacancy("a", {"source": "first"}, 99)
    data = PipelineData(
        branches={
            "one": PipelineData(items=SERP([first, Vacancy("b")])),
            "two": PipelineData(
                items=SERP([Vacancy("b"), Vacancy("a", {"source": "second"})])
            ),
        }
    )
    ReciprocalRankFusionTransformer().transform(
        data, registry=BasicTransformerRegistry({})
    )
    assert [item.id for item in data.items.items] == ["a", "b"]
    assert data.items.items[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert data.items.items[0].fields == {"source": "first"}
    assert first.score == 99 and data.items.items[0] is not first
    assert not data.branches


def test_rrf_duplicate_failure_preserves_parent():
    data = PipelineData(
        branches={"a": PipelineData(items=SERP([Vacancy("v"), Vacancy("v")]))}
    )
    items, branches = data.items, data.branches
    with pytest.raises(ValueError, match="Duplicate"):
        ReciprocalRankFusionTransformer().transform(
            data, registry=BasicTransformerRegistry({})
        )
    assert data.items is items and data.branches is branches


def test_extractor_atomicity_and_top_k():
    first, second = Vacancy("a", {"quality": 3}, 9), Vacancy("b", {}, 8)
    data = PipelineData(items=SERP([first, second]))
    registry = BasicTransformerRegistry({})
    with pytest.raises(ValueError):
        FieldScoreExtractor("quality").transform(data, registry=registry)
    assert [first.score, second.score] == [9, 8]
    second.fields["quality"] = 2
    FieldScoreExtractor("quality").transform(data, registry=registry)
    assert [first.score, second.score] == [3, 2]
    TopKTransformer(1).transform(data, registry=registry)
    assert isinstance(data.items, SERP) and data.items.items == [first]
    assert data.items.items[0] is first
    TopKTransformer(0).transform(data, registry=registry)
    assert data.items.items == []

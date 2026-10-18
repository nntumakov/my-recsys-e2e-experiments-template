from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from src.core.registry import BasicTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.core.transformers.transformers.pipeline import PipelineTransformer
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter
from src.domain.request.request import Request


def test_copy_ownership_and_append_only_filters():
    query = QueryFilter("python")
    original = PipelineData(
        context={"profile": {"languages": ["ru"]}},
        items=SERP([Vacancy("v", {"tags": ["python"]}, 1.0)]),
        filters=[query],
    )
    shallow, deep = original.copy(), original.copy(deep=True)
    assert isinstance(shallow.items, SERP)
    assert isinstance(deep.items, SERP)
    assert shallow.context is not original.context
    assert shallow.context["profile"] is original.context["profile"]
    assert shallow.items is not original.items
    assert shallow.items.items is not original.items.items
    assert shallow.items.items[0] is original.items.items[0]
    assert deep.items.items[0] is not original.items.items[0]
    tags = deep.items.items[0].fields["tags"]
    profile = deep.context["profile"]
    assert isinstance(tags, list) and isinstance(profile, dict)
    languages = profile["languages"]
    assert isinstance(languages, list)
    tags.append("new")
    languages.append("en")
    assert original.items.items[0].fields == {"tags": ["python"]}
    assert original.context == {"profile": {"languages": ["ru"]}}
    for branch in (shallow, deep):
        assert branch.filters[0] is query
        branch.add_filter(TermFilter("city", "Moscow"))
        assert original.filters == (query,)
        with pytest.raises(AttributeError):
            setattr(branch, "filters", ())
        with pytest.raises(AttributeError):
            delattr(branch, "filters")
    with pytest.raises(FrozenInstanceError):
        setattr(query, "text", "java")


@pytest.mark.parametrize(
    "create",
    [
        lambda: QueryFilter(cast(Any, [])),
        lambda: TermFilter("a", cast(Any, [])),
        lambda: UserPropertyFilter("a", cast(Any, [])),
    ],
)
def test_filters_reject_mutable_parameters(create):
    with pytest.raises(ValueError):
        create()


def test_request_snapshot_and_independent_defaults():
    values = [QueryFilter("python")]
    request = Request(cast(Any, values))
    values.clear()
    assert len(request.filters) == 1
    with pytest.raises(FrozenInstanceError):
        setattr(request, "filters", ())
    first, second = PipelineData(), PipelineData()
    first.items.items.append(Vacancy("v"))
    first.context["city"] = "Moscow"
    assert second.items.items == []
    assert second.context == {}


class Step(BaseTransformer):
    def __init__(self, name, calls, *, fail=False, copy=False):
        self.name, self.calls, self.fail, self.copy = name, calls, fail, copy

    def transform(self, data, *, registry):
        self.calls.append((self.name, data, registry))
        if self.fail:
            raise RuntimeError("step failure")
        return data.copy() if self.copy else data


def test_nested_pipeline_passes_returned_payload_and_same_registry():
    calls = []
    first, second = Step("first", calls, copy=True), Step("second", calls)
    registry = BasicTransformerRegistry(
        {"first": first, "second": second, "nested": PipelineTransformer(["first"])}
    )
    original = PipelineData()
    result = PipelineTransformer(["nested", "second"]).transform(
        original, registry=registry
    )
    assert registry["first"] is first
    assert [entry[0] for entry in calls] == ["first", "second"]
    assert calls[0][1] is original and calls[1][1] is result
    assert result is not original
    assert all(entry[2] is registry for entry in calls)
    assert PipelineTransformer([]).transform(original, registry=registry) is original


def test_pipeline_failure_stops_downstream():
    calls = []
    registry = BasicTransformerRegistry(
        {"fail": Step("fail", calls, fail=True), "after": Step("after", calls)}
    )
    with pytest.raises(RuntimeError, match="step failure"):
        PipelineTransformer(["fail", "after"]).transform(
            PipelineData(), registry=registry
        )
    assert [entry[0] for entry in calls] == ["fail"]


@pytest.mark.parametrize(
    "entries, error",
    [
        ({"p": PipelineTransformer(["missing"])}, KeyError),
        ({"p": PipelineTransformer(["p"])}, ValueError),
        (
            {"p": PipelineTransformer(["q"]), "q": PipelineTransformer(["p"])},
            ValueError,
        ),
    ],
)
def test_registry_rejects_invalid_graph_before_execution(entries, error):
    with pytest.raises(error):
        BasicTransformerRegistry(entries)

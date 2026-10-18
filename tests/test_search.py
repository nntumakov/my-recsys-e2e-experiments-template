from copy import deepcopy
from http.client import IncompleteRead
from io import BytesIO
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from src.core.registry import BasicTransformerRegistry
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.search import OpenSearchTransformer
from src.domain import JSONValue
from src.domain.collection.serp import SERP
from src.domain.document.vacancy import Vacancy
from src.domain.request.abstract import BaseFilter
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter
from src.integrations.opensearch.client import OpenSearchIntegration, OpenSearchResult


def test_real_http_dsl_and_complete_serp(search_server):
    url, requests, _ = search_server
    integration = OpenSearchIntegration(url + "/", authorization="Bearer test-token")
    transformer = OpenSearchTransformer(
        integration, index="jobs alias", fields=["title^2", "text"], size=7
    )
    original = PipelineData(
        context={"city": "Москва"},
        filters=[
            QueryFilter(" python "),
            TermFilter("remote", True),
            UserPropertyFilter("city", "city"),
        ],
    )
    result = transformer.transform(original, registry=BasicTransformerRegistry({}))
    assert result is original
    assert requests == [
        {
            "path": "/proxy/jobs%20alias/_search",
            "authorization": "Bearer test-token",
            "body": {
                "size": 7,
                "query": {
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": "python",
                                    "fields": ["title^2", "text"],
                                }
                            }
                        ],
                        "filter": [
                            {"term": {"remote": True}},
                            {"term": {"city": "Москва"}},
                        ],
                    }
                },
            },
        }
    ]
    assert isinstance(result.items, SERP)
    assert [item.id for item in result.items.items] == ["second", "first"]
    assert [item.score for item in result.items.items] == [3.5, None]
    assert result.items.items[0].fields["extra"] == {"tags": ["python"]}
    assert result.items.items[1].fields == {"score": "source field"}


@pytest.mark.parametrize(
    "filters, context",
    [
        ([], {}),
        ([QueryFilter(" ")], {}),
        ([QueryFilter("a"), QueryFilter("b")], {}),
        ([QueryFilter("a"), BaseFilter()], {}),
        ([QueryFilter("a"), TermFilter("", 1)], {}),
        ([QueryFilter("a"), TermFilter("field", None)], {}),
        ([QueryFilter("a"), TermFilter("field", float("nan"))], {}),
        ([QueryFilter("a"), UserPropertyFilter("city", "city")], {}),
        ([QueryFilter("a"), UserPropertyFilter("city", "city")], {"city": []}),
    ],
)
def test_invalid_conditions_do_not_call_search_or_mutate(filters, context):
    integration = Mock(spec=OpenSearchIntegration)
    transformer = OpenSearchTransformer(
        integration, index="jobs", fields=["title"], size=2
    )
    data = PipelineData(context=context, items=SERP([Vacancy("old")]), filters=filters)
    before = data.copy(deep=True)
    with pytest.raises(ValueError):
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    integration.search.assert_not_called()
    assert data.context == before.context and data.items == before.items
    assert data.filters == before.filters


@pytest.mark.parametrize(
    "patch",
    [
        {"timed_out": True},
        {"_shards": {"failed": 1}},
        {"hits": {}},
        {"hits": {"hits": [None]}},
        {"hits": {"hits": [{"_id": "1", "_score": 1}]}},
        {"hits": {"hits": [{"_id": "1", "_source": {}, "_score": True}]}},
        {"hits": {"hits": [{"_id": "1", "_source": {}, "_score": float("inf")}]}},
    ],
)
def test_bad_response_keeps_previous_items(search_response, patch):
    integration = Mock(spec=OpenSearchIntegration)
    integration.search.return_value = OpenSearchResult(search_response | patch)
    transformer = OpenSearchTransformer(
        integration, index="jobs", fields=["title"], size=2
    )
    previous = SERP([Vacancy("old")])
    data = PipelineData(items=previous, filters=[QueryFilter("a")])
    with pytest.raises(ValueError):
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    assert data.items is previous


def test_empty_hits_and_document_ownership(search_response):
    result = OpenSearchResult(search_response).to_document_collections()
    extra = result.items[0].fields["extra"]
    assert isinstance(extra, dict)
    tags = extra["tags"]
    assert isinstance(tags, list)
    tags.append("changed")
    assert search_response["hits"]["hits"][0]["_source"]["extra"] == {
        "tags": ["python"]
    }
    search_response["hits"]["hits"] = []
    assert OpenSearchResult(search_response).to_document_collections() == SERP()


@pytest.mark.parametrize(
    "status, body", [(403, b"SECRET"), (200, b"invalid JSON SECRET"), (200, [])]
)
def test_http_and_json_errors_are_safe(search_server, status, body):
    url, _, replies = search_server
    replies[:] = [(status, body, {})]
    with pytest.raises(RuntimeError) as error:
        OpenSearchIntegration(url).search(index="jobs", body={})
    assert "SECRET" not in str(error.value)
    assert error.value.__cause__ is not None


def test_redirect_is_not_followed(search_server):
    url, requests, replies = search_server
    replies[:] = [(307, {}, {"Location": url + "/other"})]
    with pytest.raises(RuntimeError, match="307"):
        OpenSearchIntegration(url, authorization="Bearer secret").search(
            index="jobs", body={}
        )
    assert len(requests) == 1


def test_arbitrary_dsl_timeout_and_response_close(monkeypatch):
    response = BytesIO(b'{"aggregations": {"total": 3}}')
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(
        "src.integrations.opensearch.client.build_opener", lambda *args: opener
    )
    integration = OpenSearchIntegration("https://example.invalid/prefix", timeout=4.5)
    body: dict[str, JSONValue] = {
        "_source": False,
        "aggs": {"total": {"value_count": {"field": "id"}}},
    }
    before = deepcopy(body)
    result = integration.search(index="jobs", body=body)
    assert result.response == {"aggregations": {"total": 3}}
    assert response.closed
    assert body == before
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://example.invalid/prefix/jobs/_search"
    assert request.get_method() == "POST"
    assert opener.open.call_args.kwargs == {"timeout": 4.5}
    assert not request.has_header("Authorization")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://host",
        "https://user:password@host",
        "https://host/?x=1",
        "https://host/#x",
        "http://host:wrong",
    ],
)
def test_invalid_connection_fails_before_request(url):
    with pytest.raises(ValueError):
        OpenSearchIntegration(url)


@pytest.mark.parametrize("suffix", ["?", "#"])
def test_empty_url_delimiters_are_rejected_before_http(suffix, monkeypatch):
    opener = Mock()
    monkeypatch.setattr("src.integrations.opensearch.client.build_opener", opener)
    with pytest.raises(ValueError):
        OpenSearchIntegration("https://example.invalid/proxy" + suffix)
    opener.assert_not_called()


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_nonfinite_response_numbers_fail_before_mutation(number, monkeypatch):
    wire = (
        '{"timed_out":false,"_shards":{"failed":0},"hits":{"hits":'
        '[{"_id":"new","_score":1,"_source":{"salary":' + number + "}}]}}"
    )
    response = BytesIO(wire.encode())
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(
        "src.integrations.opensearch.client.build_opener", lambda *args: opener
    )
    transformer = OpenSearchTransformer(
        OpenSearchIntegration("https://example.invalid"),
        index="jobs",
        fields=["title"],
        size=1,
    )
    previous = SERP([Vacancy("old")])
    data = PipelineData(items=previous, filters=[QueryFilter("python")])
    with pytest.raises(RuntimeError):
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    assert data.items is previous
    assert response.closed


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("private"), URLError("private"), IncompleteRead(b"private", 20)],
)
def test_transport_failure_preserves_cause_and_previous_result(monkeypatch, failure):
    opener = Mock()
    opener.open.side_effect = failure
    monkeypatch.setattr(
        "src.integrations.opensearch.client.build_opener", lambda *args: opener
    )
    transformer = OpenSearchTransformer(
        OpenSearchIntegration("http://example.invalid"),
        index="jobs",
        fields=["title"],
        size=1,
    )
    previous = SERP([Vacancy("previous")])
    data = PipelineData(items=previous, filters=[QueryFilter("python")])
    with pytest.raises(RuntimeError) as error:
        transformer.transform(data, registry=BasicTransformerRegistry({}))
    assert error.value.__cause__ is failure
    assert "private" not in str(error.value)
    assert data.items is previous


def test_response_is_closed_on_json_failure(monkeypatch):
    response = BytesIO(b"invalid")
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(
        "src.integrations.opensearch.client.build_opener", lambda *args: opener
    )
    with pytest.raises(RuntimeError):
        OpenSearchIntegration("http://example.invalid").search(index="jobs", body={})
    assert response.closed


@pytest.mark.parametrize(
    "settings",
    [
        {"index": "", "fields": ["title"], "size": 1},
        {"index": "jobs", "fields": [], "size": 1},
        {"index": "jobs", "fields": [" "], "size": 1},
        {"index": "jobs", "fields": "title", "size": 1},
        {"index": "jobs", "fields": ["title"], "size": 0},
    ],
)
def test_invalid_search_configuration_does_not_call_integration(settings):
    integration = Mock(spec=OpenSearchIntegration)
    with pytest.raises(ValueError):
        OpenSearchTransformer(integration, **settings)
    integration.search.assert_not_called()

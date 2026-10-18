from collections.abc import Mapping, Sequence
from math import isfinite

from src.core.registry import BaseTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.domain import JSONValue
from src.domain.request.filters import QueryFilter, TermFilter, UserPropertyFilter
from src.domain.request.request import Request
from src.integrations.opensearch.client import OpenSearchIntegration


def search_body(
    request: Request, context: Mapping[str, JSONValue], fields: Sequence[str], size: int
) -> dict[str, JSONValue]:
    queries: list[str] = []
    terms: list[JSONValue] = []
    for condition in request.filters:
        if isinstance(condition, QueryFilter):
            queries.append(condition.text.strip())
            continue
        if isinstance(condition, TermFilter):
            field, value = condition.field, condition.value
        elif isinstance(condition, UserPropertyFilter):
            if condition.property not in context:
                raise ValueError("Missing user property")
            field, value = condition.field, context[condition.property]
        else:
            raise ValueError("Unsupported search filter")
        if (
            not field.strip()
            or not isinstance(value, (str, int, float, bool))
            or (isinstance(value, float) and not isfinite(value))
        ):
            raise ValueError("Term requires a field and non-null scalar value")
        terms.append({"term": {field: value}})
    if len(queries) != 1 or not queries[0]:
        raise ValueError("Search requires exactly one non-empty QueryFilter")
    clauses: dict[str, JSONValue] = {
        "must": [{"multi_match": {"query": queries[0], "fields": list(fields)}}]
    }
    if terms:
        clauses["filter"] = terms
    return {"size": size, "query": {"bool": clauses}}


class OpenSearchTransformer(BaseTransformer):
    def __init__(
        self,
        integration: OpenSearchIntegration,
        *,
        index: str,
        fields: Sequence[str],
        size: int,
    ) -> None:
        if (
            not index.strip()
            or index in (".", "..")
            or isinstance(fields, str)
            or not fields
            or any(not field.strip() for field in fields)
            or type(size) is not int
            or size <= 0
        ):
            raise ValueError("Invalid search index, fields or size")
        self.integration = integration
        self.index, self.fields, self.size = index, tuple(fields), size

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        body = self.build_query(data)
        result = self.integration.search(index=self.index, body=body)
        data.items = result.to_document_collections()
        return data

    def build_query(self, data: PipelineData) -> dict[str, JSONValue]:
        return search_body(Request(data.filters), data.context, self.fields, self.size)

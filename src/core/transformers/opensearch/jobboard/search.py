from src.components.query.jobboard.parser import ModularQueryParser
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.jobboard.query import (
    LexicalQueryConfig,
    SourceProfile,
    compile_query,
)
from src.domain import JSONValue
from src.domain.request.filters import QueryFilter
from src.domain.request.request import Request
from src.integrations.opensearch.client import OpenSearchIntegration


class JobSearchTransformer(BaseTransformer):
    def __init__(
        self,
        integration: OpenSearchIntegration,
        parser: ModularQueryParser,
        source: SourceProfile,
        query: LexicalQueryConfig,
        size: int,
    ) -> None:
        if type(size) is not int or size <= 0:
            raise ValueError("Search size must be positive")
        self.integration = integration
        self.parser = parser
        self.source = source
        self.query = query
        self.size = size

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        body = self.build_query(data)
        result = self.integration.search(index=self.source.index, body=body)
        items = result.to_document_collections()
        data.items = items
        return data

    def build_query(self, data: PipelineData) -> dict[str, JSONValue]:
        request = Request(data.filters)
        queries = [
            condition.text
            for condition in request.filters
            if isinstance(condition, QueryFilter)
        ]
        if len(queries) != 1 or not queries[0].strip():
            raise ValueError("Search requires exactly one non-empty QueryFilter")
        if len(queries[0]) > self.query.max_input_characters:
            raise ValueError("Query exceeds character budget")
        parsed = self.parser.parse(queries[0])
        return compile_query(
            parsed, request, data.context, self.source, self.query, self.size
        )

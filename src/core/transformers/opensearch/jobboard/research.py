"""One compiler for preview and execution of the five research candidates."""

import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from time import perf_counter
from typing import cast

from src.components.query.jobboard.parser import ModularQueryParser
from src.components.query.jobboard.rewrite import ConservativeRewriter
from src.core.registry import BaseTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.core.transformers.opensearch.jobboard.query import (
    LexicalQueryConfig,
    SourceProfile,
    _leaves,
    _term_filters,
    compile_query,
)
from src.core.transformers.opensearch.jobboard.ranking import RankingConfig
from src.core.transformers.opensearch.jobboard.slots import (
    SlotQueryConfig,
    slot_body,
    slot_residual,
)
from src.domain import JSONValue
from src.domain.request.jobboard import ParsedQuery
from src.domain.request.request import Request
from src.domain.request.rewrite import QueryRewrite
from src.integrations.opensearch.client import OpenSearchIntegration
from src.integrations.opensearch.evidence import observe


@dataclass(frozen=True)
class SearchPlan:
    body: dict[str, JSONValue]
    understanding: dict[str, JSONValue]


def parse_trace(parsed: ParsedQuery) -> dict[str, JSONValue]:
    result = asdict(parsed)
    result["syntax_enabled"] = result.pop("structured_enabled")
    result["residual"] = [
        {"text": text, "span": span} for text, span in slot_residual(parsed)
    ]
    # Convert immutable tuples to JSON arrays at this presentation boundary.
    return json.loads(json.dumps(result, ensure_ascii=False))


class ResearchSearchTransformer(BaseTransformer):
    def __init__(
        self,
        integration: OpenSearchIntegration,
        parser: ModularQueryParser,
        source: SourceProfile,
        query: LexicalQueryConfig,
        ranking: RankingConfig,
        now_ms: int,
        *,
        mode: str = "literal",
        size: int = 20,
        rewriter: ConservativeRewriter | None = None,
        rewrite_weight: float = 0.65,
    ) -> None:
        if (
            mode not in {"literal", "slots", "rewrite"}
            or type(size) is not int
            or size <= 0
            or type(now_ms) is not int
            or now_ms < 0
            or type(rewrite_weight) not in (int, float)
            or not math.isfinite(rewrite_weight)
            or not 0 < rewrite_weight <= 1
        ):
            raise ValueError("Invalid research search configuration")
        if mode != "literal" and not isinstance(query, SlotQueryConfig):
            raise ValueError("Concept modes require slot configuration")
        if mode == "rewrite" and (rewriter is None or rewriter.parser is not parser):
            raise ValueError("Rewriter must use the same parser")
        self.integration, self.parser, self.source = integration, parser, source
        self.query, self.ranking, self.now_ms = query, ranking, now_ms
        self.mode, self.size, self.rewriter, self.rewrite_weight = (
            mode,
            size,
            rewriter,
            rewrite_weight,
        )
        # All selected modes are preflighted before the experiment's first HTTP.
        ranking.prepare(source, now_ms, size)

    def plan(self, data: PipelineData) -> SearchPlan:
        request = Request(data.filters)
        text, filters = _term_filters(request, data.context, self.source)
        parsed = self.parser.parse(text)
        config = (
            replace(self.query, title_phrase_boost=0)
            if self.ranking.mode == "phrase_rescore"
            else self.query
        )
        rewrite = QueryRewrite(parsed)
        if self.mode == "literal":
            literal = replace(parsed, hints=())
            body = compile_query(
                literal, request, data.context, self.source, config, self.size
            )
        else:
            slots = cast(SlotQueryConfig, config)
            body = slot_body(parsed, self.source, slots, self.size, filters)
            if self.mode == "rewrite":
                rewrite = cast(ConservativeRewriter, self.rewriter).rewrite(parsed)
                if rewrite.rewritten is not None:
                    alternative = slot_body(
                        rewrite.rewritten, self.source, slots, self.size, []
                    )
                    original = slot_body(parsed, self.source, slots, self.size, [])
                    body = cast(
                        dict[str, JSONValue],
                        {
                            "size": self.size,
                            "query": {
                                "bool": {
                                    "filter": filters,
                                    "must": [
                                        {
                                            "dis_max": {
                                                "queries": [
                                                    original["query"],
                                                    {
                                                        "bool": {
                                                            "must": [
                                                                alternative["query"]
                                                            ],
                                                            "boost": self.rewrite_weight,
                                                        }
                                                    },
                                                ],
                                                "tie_breaker": 0,
                                            }
                                        }
                                    ],
                                }
                            },
                        },
                    )
        body = self.ranking.apply(
            body, self.source, self.now_ms, parsed.normalized_text
        )
        leaves = _leaves(body)
        if leaves > config.max_query_leaves:
            raise ValueError("Query exceeds combined DSL leaf budget")
        trace = parse_trace(parsed)
        trace["rewrite_candidates"] = json.loads(
            json.dumps([asdict(c) for c in rewrite.candidates])
        )
        trace["accepted_rewrite"] = (
            rewrite.rewritten.normalized_text if rewrite.rewritten else None
        )
        trace["rewritten_parse"] = (
            parse_trace(rewrite.rewritten) if rewrite.rewritten else None
        )
        trace["decisions"] = cast(list[JSONValue], trace["decisions"]) + json.loads(
            json.dumps([asdict(d) for d in rewrite.decisions])
        )
        trace["budget_usage"] = {
            "query_leaves": leaves,
            "max_query_leaves": config.max_query_leaves,
            "concept_groups": len(parsed.hints) + len(slot_residual(parsed)),
        }
        trace["mode"] = self.mode
        return SearchPlan(body, trace)

    def build_query(self, data: PipelineData) -> dict[str, JSONValue]:
        return deepcopy(self.plan(data).body)

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        plan = self.plan(data)
        start = perf_counter()
        result = self.integration.search(index=self.source.index, body=plan.body)
        items = observe(
            result, plan.body, plan.understanding, (perf_counter() - start) * 1000
        )
        items.metadata = {
            item.id: self.ranking.source_diagnostics(item, self.source, self.now_ms)
            for item in items.items
        }
        data.items = items
        return data

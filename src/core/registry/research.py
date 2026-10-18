"""Hydra composition of research leaves, sharing parser, client and source."""

from collections.abc import Mapping, Sequence
from typing import TypedDict, cast

from src.components.query.jobboard.parser import ModularQueryParser
from src.components.query.jobboard.rewrite import ConservativeRewriter
from src.core.registry import BasicTransformerRegistry
from src.core.transformers.opensearch.jobboard.query import (
    LexicalQueryConfig,
    SourceProfile,
)
from src.core.transformers.opensearch.jobboard.ranking import RankingConfig
from src.core.transformers.opensearch.jobboard.research import ResearchSearchTransformer
from src.integrations.opensearch.client import OpenSearchIntegration


class RewriteOptions(TypedDict, total=False):
    rules: Sequence[Mapping[str, object]]
    keyboard: Mapping[str, str]
    spelling: bool
    layout: bool
    curated: bool
    min_length: int
    max_length: int
    max_replaced_spans: int
    denylist: Sequence[str]


def research_registry(
    integration: OpenSearchIntegration,
    parser: ModularQueryParser,
    source: SourceProfile,
    variants: Mapping[str, Mapping[str, object]],
    now_ms: int,
    size: int = 20,
    rewrite: RewriteOptions | None = None,
    selected: Sequence[str] | None = None,
) -> BasicTransformerRegistry:
    if selected is not None:
        if (
            not selected
            or len(set(selected)) != len(selected)
            or not set(selected) <= set(variants)
        ):
            raise ValueError("Invalid selected research variants")
        variants = {name: variants[name] for name in selected}
    rewriter = (
        ConservativeRewriter(parser, **(rewrite or {}))
        if any(v.get("mode") == "rewrite" for v in variants.values())
        else None
    )
    entries = {}
    if not variants:
        raise ValueError("Research requires at least one variant")
    for name, variant in variants.items():
        if not name or set(variant) - {"mode", "query", "ranking", "rewrite_weight"}:
            raise ValueError("Invalid research variant")
        query, ranking = variant.get("query"), variant.get("ranking")
        mode, weight = (
            variant.get("mode", "literal"),
            variant.get("rewrite_weight", 0.65),
        )
        if (
            not isinstance(query, LexicalQueryConfig)
            or not isinstance(ranking, RankingConfig)
            or not isinstance(mode, str)
            or type(weight) not in (int, float)
        ):
            raise ValueError("Variant requires typed query and ranking configuration")
        entries[name] = ResearchSearchTransformer(
            integration,
            parser,
            source,
            query,
            ranking,
            now_ms,
            mode=mode,
            size=size,
            rewriter=rewriter,
            rewrite_weight=cast(float, weight),
        )
    return BasicTransformerRegistry(entries)

from copy import deepcopy
from math import isfinite

from src.core.registry import BaseTransformerRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.data import PipelineData
from src.domain.collection.serp import SERP
from src.domain.document.abstract import BaseDocument


class ReciprocalRankFusionTransformer(BaseTransformer):
    def __init__(self, rank_constant: float = 60.0) -> None:
        if (
            isinstance(rank_constant, bool)
            or not isfinite(rank_constant)
            or rank_constant < 0
        ):
            raise ValueError("RRF rank constant must be finite and nonnegative")
        self.rank_constant = rank_constant

    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData:
        if not data.branches:
            raise ValueError("RRF requires branch results")
        documents: dict[str, BaseDocument] = {}
        scores: dict[str, float] = {}
        for branch in data.branches.values():
            if branch.branches:
                raise ValueError("Nested branches must be merged first")
            seen: set[str] = set()
            for rank, item in enumerate(branch.items.items, 1):
                if item.id in seen:
                    raise ValueError("Duplicate document id within branch")
                seen.add(item.id)
                if item.id not in documents:
                    documents[item.id] = deepcopy(item)
                    scores[item.id] = 0.0
                scores[item.id] += 1.0 / (self.rank_constant + rank)
        ranked = sorted(
            documents, key=lambda identifier: scores[identifier], reverse=True
        )
        for identifier, item in documents.items():
            item.score = scores[identifier]
        data.items = SERP([documents[identifier] for identifier in ranked])
        data.branches = {}
        return data

from collections.abc import Mapping

from src.core.registry.abstract import BaseRegistry
from src.core.registry.simple import SimpleRegistry
from src.core.transformers.abstract import BaseTransformer
from src.core.transformers.transformers.branch import BranchTransformer
from src.core.transformers.transformers.pipeline import PipelineTransformer


class BaseTransformerRegistry(BaseRegistry[BaseTransformer]):
    """Resolve named transformers within an experiment's composition."""


class BasicTransformerRegistry(
    SimpleRegistry[BaseTransformer], BaseTransformerRegistry
):
    def __init__(self, entries: Mapping[str, BaseTransformer]) -> None:
        super().__init__(entries)
        visited: set[str] = set()
        active: set[str] = set()

        def visit(name: str) -> None:
            if name in active:
                raise ValueError("Cyclic pipeline")
            if name in visited:
                return
            transformer = self[name]
            active.add(name)
            if isinstance(transformer, PipelineTransformer):
                for step in transformer.steps:
                    visit(step)
            elif isinstance(transformer, BranchTransformer):
                for target in transformer.branches.values():
                    visit(target)
            active.remove(name)
            visited.add(name)

        for name in self._entries:
            visit(name)

from collections.abc import Mapping

from src.core.registry.abstract import BaseRegistry


class SimpleRegistry[T](BaseRegistry[T]):
    def __init__(self, entries: Mapping[str, T]) -> None:
        self._entries = dict(entries)

    def __getitem__(self, name: str) -> T:
        return self._entries[name]

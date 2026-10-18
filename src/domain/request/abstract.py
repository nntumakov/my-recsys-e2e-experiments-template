from abc import ABC


class BaseRequest(ABC):
    """Domain request independent of an integration's wire representation."""


class BaseFilter(ABC):
    """Immutable condition appended to a pipeline's request constraints."""

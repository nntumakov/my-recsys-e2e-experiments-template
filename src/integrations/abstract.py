from abc import ABC


class BaseIntegration(ABC):
    """External system boundary; each integration owns its supported operations."""

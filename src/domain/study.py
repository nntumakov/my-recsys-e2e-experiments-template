"""A completed research search retains evidence independently of vacancy fields."""

from dataclasses import dataclass, field

from src.domain import JSONValue
from src.domain.collection.serp import SERP


@dataclass
class StudySERP(SERP):
    evidence: dict[str, JSONValue] = field(default_factory=dict)

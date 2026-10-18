from dataclasses import dataclass

from src.domain import JSONScalar
from src.domain.request.abstract import BaseFilter


@dataclass(frozen=True)
class QueryFilter(BaseFilter):
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("QueryFilter requires text")


@dataclass(frozen=True)
class TermFilter(BaseFilter):
    field: str
    value: JSONScalar

    def __post_init__(self) -> None:
        if not isinstance(self.field, str) or not isinstance(
            self.value, (str, int, float, bool, type(None))
        ):
            raise ValueError("TermFilter requires an immutable scalar")


@dataclass(frozen=True)
class UserPropertyFilter(BaseFilter):
    field: str
    property: str

    def __post_init__(self) -> None:
        if not isinstance(self.field, str) or not isinstance(self.property, str):
            raise ValueError("UserPropertyFilter requires field and property names")

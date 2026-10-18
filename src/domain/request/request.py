from dataclasses import dataclass

from src.domain.request.abstract import BaseFilter, BaseRequest


@dataclass(frozen=True)
class Request(BaseRequest):
    filters: tuple[BaseFilter, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "filters", tuple(self.filters))

"""Immutable embedding space and individual vectors."""

import re
from dataclasses import asdict, dataclass
from math import isfinite

from src.domain import JSONValue
from src.domain.serialization import fingerprint


@dataclass(frozen=True)
class EmbeddingProfile:
    schema_version: int = 1
    model_id: str = "intfloat/multilingual-e5-base"
    revision: str = "d128750597153bb5987e10b1c3493a34e5a4502a"
    tokenizer_model_id: str = "intfloat/multilingual-e5-base"
    tokenizer_revision: str = "d128750597153bb5987e10b1c3493a34e5a4502a"
    dimensions: int = 768
    pooling: str = "mean"
    normalization: str = "l2"
    query_prefix: str = "query: "
    document_prefix: str = "passage: "
    query_text_policy: str = "nfc-whitespace-v1"
    document_text_policy: str = "fieldwise-nfc-whitespace-v1"
    max_tokens: int = 512
    query_overflow: str = "reject"
    document_overflow: str = "truncate_right"
    inference_dtype: str = "float16"
    model_quantization: str = "none"

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if name in {"schema_version", "dimensions", "max_tokens"}:
                if type(value) is not int or value <= 0:
                    raise ValueError("Embedding profile requires positive integers")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError("Embedding profile requires nonempty strings")
        if self.schema_version != 1:
            raise ValueError("Unsupported embedding profile schema")
        if any(
            not re.fullmatch(r"[0-9a-f]{40}", value)
            for value in (self.revision, self.tokenizer_revision)
        ):
            raise ValueError("Embedding revisions must be immutable commit SHA values")

    def to_dict(self) -> dict[str, JSONValue]:
        return asdict(self)

    @property
    def id(self) -> str:
        return fingerprint(self.to_dict())


@dataclass(frozen=True)
class Embedding:
    profile_id: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.profile_id
        ):
            raise ValueError("Embedding requires a profile fingerprint")
        values = tuple(self.values)
        if not values or any(
            type(value) not in (int, float) or not isfinite(value) for value in values
        ):
            raise ValueError("Embedding requires finite numeric values")
        object.__setattr__(self, "values", tuple(float(value) for value in values))

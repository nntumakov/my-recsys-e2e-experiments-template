"""Model-specific text encoding shared by search and corpus preparation."""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from math import isfinite, sqrt
from types import MappingProxyType
from typing import cast

from src.components.embedding.text import normalize_text
from src.domain import JSONValue
from src.domain.embedding import Embedding, EmbeddingProfile
from src.domain.serialization import canonical_bytes
from src.integrations.embedding.client import TEIIntegration, embedding_body


class TextEmbedder:
    def __init__(
        self,
        profile: EmbeddingProfile | dict,
        integration: TEIIntegration,
        limits: Mapping[str, int],
        runtime_version: str = "1.9.4",
    ) -> None:
        self.profile = (
            profile
            if isinstance(profile, EmbeddingProfile)
            else EmbeddingProfile(**dict(profile))
        )
        if self.profile != EmbeddingProfile():
            raise ValueError(
                "Only the pinned multilingual E5-base profile is supported"
            )
        if runtime_version != "1.9.4":
            raise ValueError("Unsupported TEI runtime version")
        defaults = {
            "query_bytes": 4096,
            "document_bytes": 65536,
            "batch_inputs": 8,
            "batch_bytes": 262144,
            "response_bytes": 1048576,
            "tokenize_response_bytes": 16777216,
        }
        if set(limits) - defaults.keys() or any(
            type(value) is not int or value <= 0 for value in limits.values()
        ):
            raise ValueError("Embedding limits must be known positive integers")
        self.limits = MappingProxyType(defaults | dict(limits))
        self.integration = integration
        self.runtime_version = runtime_version

    def prepare_query(self, text: str) -> str:
        normalized = normalize_text(text)
        if not normalized:
            raise ValueError("Query must contain nonempty text")
        prepared = self.profile.query_prefix + normalized
        if len(prepared.encode("utf-8")) > self.limits["query_bytes"]:
            raise ValueError("Query exceeds UTF-8 byte budget")
        if (
            len(canonical_bytes(embedding_body((prepared,), truncate=False)))
            > self.limits["batch_bytes"]
        ):
            raise ValueError("Query exceeds inference body byte budget")
        return prepared

    def prepare_documents(self, texts: Sequence[str]) -> tuple[str, ...]:
        if isinstance(texts, str) or not texts:
            raise ValueError("Document encoding requires nonempty texts")
        result = []
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Document encoding requires nonempty text")
            prepared = self.profile.document_prefix + text
            try:
                size = len(prepared.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError("Document input requires valid Unicode") from exc
            if size > self.limits["document_bytes"]:
                raise ValueError("Document exceeds UTF-8 byte budget")
            result.append(prepared)
        return tuple(result)

    def document_batches(self, texts: Iterable[str]) -> Iterator[tuple[str, ...]]:
        if isinstance(texts, str):
            raise ValueError("Document batches require a sequence of texts")
        batch: list[str] = []
        prepared: list[str] = []
        for text in texts:
            model_input = self.prepare_documents((text,))[0]
            if (
                len(canonical_bytes(embedding_body((model_input,), truncate=True)))
                > (self.limits["batch_bytes"])
            ):
                raise ValueError("One document exceeds inference body byte budget")
            candidate = [*prepared, model_input]
            if batch and (
                len(batch) == self.limits["batch_inputs"]
                or len(canonical_bytes(embedding_body(candidate, truncate=True)))
                > self.limits["batch_bytes"]
            ):
                yield tuple(batch)
                batch, prepared = [], []
            batch.append(text)
            prepared.append(model_input)
        if batch:
            yield tuple(batch)

    def bounded_document_batches(
        self, texts: Iterable[str]
    ) -> Iterator[tuple[str, ...]]:
        """Bound batches without using the v1 right-truncation policy."""
        if isinstance(texts, str):
            raise ValueError("Passage batches require a sequence of texts")
        batch: list[str] = []
        for text in texts:
            prepared = self.prepare_documents((text,))[0]
            if (
                len(canonical_bytes(embedding_body((prepared,), truncate=False)))
                > self.limits["batch_bytes"]
            ):
                raise ValueError("One passage exceeds inference body byte budget")
            candidate = self.prepare_documents((*batch, text))
            if batch and (
                len(batch) >= self.limits["batch_inputs"]
                or len(canonical_bytes(embedding_body(candidate, truncate=False)))
                > self.limits["batch_bytes"]
            ):
                yield tuple(batch)
                batch = []
            batch.append(text)
        if batch:
            yield tuple(batch)

    def encode_bounded_documents(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        """Audit every actual model input; never silently truncate a passage."""
        return self._encode(
            self.prepare_documents(texts), truncate=False, audit_tokens=True
        )

    def check_runtime(self) -> dict[str, JSONValue]:
        info = self.integration.info()
        expected = {
            "model_id": self.profile.model_id,
            "model_sha": self.profile.revision,
            "model_dtype": self.profile.inference_dtype,
            "model_type": {"embedding": {"pooling": self.profile.pooling}},
            "version": self.runtime_version,
            "max_input_length": self.profile.max_tokens,
            "auto_truncate": False,
        }
        if any(
            type(info.get(name)) is not type(value) or info[name] != value
            for name, value in expected.items()
        ):
            raise ValueError("TEI runtime does not match the embedding profile")
        for name in (
            "max_batch_tokens",
            "max_client_batch_size",
            "max_concurrent_requests",
        ):
            value = info.get(name)
            if type(value) is not int or value <= 0:
                raise ValueError("Invalid TEI runtime capacity")
        if cast(int, info["max_batch_tokens"]) < self.profile.max_tokens:
            raise ValueError("TEI runtime cannot fit one model input")
        return info

    def encode_query(self, text: str) -> Embedding:
        return self._encode((self.prepare_query(text),), truncate=False)[0]

    def encode_documents(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        return self._encode(self.prepare_documents(texts), truncate=True)

    def _encode(
        self, inputs: tuple[str, ...], *, truncate: bool, audit_tokens: bool = False
    ) -> tuple[Embedding, ...]:
        if (
            len(inputs) > self.limits["batch_inputs"]
            or len(canonical_bytes(embedding_body(inputs, truncate=truncate)))
            > self.limits["batch_bytes"]
        ):
            raise ValueError("Embedding batch exceeds configured limits")
        info = self.check_runtime()
        if len(inputs) > cast(int, info["max_client_batch_size"]) or (
            len(inputs) * self.profile.max_tokens > cast(int, info["max_batch_tokens"])
        ):
            raise ValueError("Embedding batch exceeds TEI runtime capacity")
        if audit_tokens:
            from src.components.embedding.chunks import token_offsets

            for model_input in inputs:
                tokens = self.integration.tokenize((model_input,))[0]
                token_offsets(model_input, tokens)
                if len(tokens) > self.profile.max_tokens:
                    raise ValueError("Passage exceeds complete model token budget")
        rows = self.integration.embed(inputs, truncate=truncate)
        if not isinstance(rows, list) or len(rows) != len(inputs):
            raise ValueError("Unexpected number of embeddings")
        embeddings = []
        for row in rows:
            if not isinstance(row, list) or len(row) != self.profile.dimensions:
                raise ValueError("Embedding dimension does not match profile")
            if any(
                type(value) not in (int, float)
                or abs(value) > 3.4028234663852886e38
                or not isfinite(value)
                for value in row
            ):
                raise ValueError("Embedding requires finite float32 values")
            norm = sqrt(sum(float(value) ** 2 for value in row))
            if norm == 0 or abs(norm - 1) > 1e-3:
                raise ValueError("Embedding must have unit L2 norm")
            embeddings.append(Embedding(self.profile.id, tuple(row)))
        return tuple(embeddings)

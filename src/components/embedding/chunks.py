"""Split vacancy text into token-bounded passages using UTF-8 offsets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from src.components.embedding.text import normalize_text
from src.components.embedding.vacancy import VacancyText
from src.domain import JSONValue
from src.domain.document.vacancy import Vacancy

if TYPE_CHECKING:
    from src.components.embedding.encoder import TextEmbedder


@dataclass(frozen=True)
class VacancyPassage:
    ordinal: int
    start: int
    stop: int
    title_stop: int
    text: str
    model_input_sha256: str
    token_count: int

    def fragment(self) -> dict[str, JSONValue]:
        return {
            "ordinal": self.ordinal,
            "start": self.start,
            "stop": self.stop,
            "title_stop": self.title_stop,
            "model_input_sha256": self.model_input_sha256,
        }


def token_offsets(
    text: str, tokens: list[dict[str, JSONValue]]
) -> tuple[tuple[int, int], ...]:
    """Validate pinned TEI byte offsets; repeated subword offsets are legitimate."""
    raw = text.encode("utf-8")
    offsets = []
    previous = (0, 0)
    for token in tokens:
        if token["special"]:
            continue
        start, stop = token["start"], token["stop"]
        if (
            type(start) is not int
            or type(stop) is not int
            or not 0 <= start < stop <= len(raw)
        ):
            raise ValueError("Invalid tokenizer UTF-8 byte offsets")
        try:
            raw[:start].decode("utf-8")
            raw[start:stop].decode("utf-8")
            raw[stop:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Tokenizer offsets split a Unicode character") from exc
        if start < previous[0] or stop < previous[1]:
            raise ValueError("Tokenizer offsets must be monotone")
        offsets.append((start, stop))
        previous = (start, stop)
    if text and not offsets:
        raise ValueError("Tokenizer omitted nonempty text")
    # Every non-whitespace character must be represented; omitted tail is an error.
    cursor = 0
    for start, stop in offsets:
        if start > cursor and raw[cursor:start].decode("utf-8").strip():
            raise ValueError("Tokenizer offsets leave uncovered text")
        cursor = max(cursor, stop)
    if raw[cursor:].decode("utf-8").strip():
        raise ValueError("Tokenizer offsets leave uncovered text")
    return tuple(offsets)


def word_boundaries(text: str) -> tuple[int, ...]:
    return (
        0,
        *(
            len(text[: match.end()].encode("utf-8"))
            for match in re.finditer(r"\S+\s*", text)
        ),
    )


def token_count(offsets: tuple[tuple[int, int], ...], start: int, stop: int) -> int:
    return sum(left < stop and right > start for left, right in offsets)


class ChunkVacancyText(VacancyText):
    """Passage recipe v2; source fields remain complete."""

    def __init__(
        self,
        title_field: str = "title",
        description_field: str = "description",
        title_tokens: int = 64,
        window_tokens: int = 384,
        overlap_tokens: int = 64,
        max_chunks: int = 64,
        schema_version: int = 2,
        renderer: str = "vacancy-passages-v2",
    ) -> None:
        if (
            schema_version != 2
            or type(schema_version) is not int
            or renderer != "vacancy-passages-v2"
        ):
            raise ValueError("Unsupported chunk recipe discriminator")
        if any(
            type(v) is not int
            for v in (title_tokens, window_tokens, overlap_tokens, max_chunks)
        ) or not (
            0 <= title_tokens <= 64
            and window_tokens > 0
            and 0 <= overlap_tokens < window_tokens
            and 0 < max_chunks <= 64
        ):
            raise ValueError("Invalid chunk recipe limits")
        super().__init__(
            fields=(
                {"name": title_field, "required": False},
                {"name": description_field, "required": False},
            )
        )
        self.title_field, self.description_field = title_field, description_field
        self.title_tokens, self.window_tokens = title_tokens, window_tokens
        self.overlap_tokens, self.max_chunks = overlap_tokens, max_chunks
        self.schema_version, self.renderer = schema_version, renderer

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": 2,
            "renderer": self.renderer,
            "title_field": self.title_field,
            "description_field": self.description_field,
            "title_tokens": self.title_tokens,
            "window_tokens": self.window_tokens,
            "overlap_tokens": self.overlap_tokens,
            "max_chunks": self.max_chunks,
            "boundary_policy": "normalized-utf8-whole-word-v1",
            "chunker_version": 1,
            "separator": "\n",
            "overflow": "reject",
            "final_input_audit": "all-inputs-special-prefix-v1",
            "fragment_layout": "title-stop-bytes-v2",
        }

    def texts(self, vacancy: Vacancy) -> tuple[str, str]:
        values = []
        for field in (self.title_field, self.description_field):
            value = vacancy.fields.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError("Chunk source fields must be strings or null")
            values.append(normalize_text(value) if isinstance(value, str) else "")
        if not any(values):
            raise ValueError("Vacancy has no nonempty chunk text")
        return values[0], values[1]

    def title_context(self, title: str, embedder: TextEmbedder) -> tuple[str, bool]:
        if not title or self.title_tokens == 0:
            return "", bool(title)
        offsets = token_offsets(title, embedder.integration.tokenize((title,))[0])
        boundaries = word_boundaries(title)
        cutoff = max(
            (
                end
                for end in boundaries
                if token_count(offsets, 0, end) <= self.title_tokens
            ),
            default=0,
        )
        if cutoff == 0:
            raise ValueError("First title word exceeds chunk title budget")
        return title.encode("utf-8")[:cutoff].decode("utf-8").rstrip(), cutoff < len(
            title.encode("utf-8")
        )

    def render_chunks(
        self, vacancy: Vacancy, embedder: TextEmbedder
    ) -> tuple[tuple[VacancyPassage, ...], dict[str, JSONValue]]:
        title, body = self.texts(vacancy)
        context, shortened = self.title_context(title, embedder)
        raw = body.encode("utf-8")
        offsets = (
            token_offsets(body, embedder.integration.tokenize((body,))[0])
            if body
            else ()
        )
        boundaries = word_boundaries(body)
        start = 0
        passages = []
        while start < len(raw) or not passages:
            ends = [
                end
                for end in boundaries
                if end > start
                and token_count(offsets, start, end) <= self.window_tokens
            ]
            if body and not ends:
                raise ValueError("Indivisible description word exceeds window budget")
            stop = ends[-1] if ends else 0
            while True:
                text = context + "\n" + raw[start:stop].decode("utf-8")
                prepared = embedder.prepare_documents((text,))[0]
                tokens = embedder.integration.tokenize((prepared,))[0]
                token_offsets(prepared, tokens)
                if len(tokens) <= embedder.profile.max_tokens:
                    break
                ends = [end for end in ends if end < stop]
                if not ends:
                    raise ValueError(
                        "Title plus first body word exceeds full model token budget"
                    )
                stop = ends[-1]
            passages.append(
                VacancyPassage(
                    len(passages),
                    start,
                    stop,
                    len(context.encode("utf-8")),
                    text,
                    sha256(prepared.encode("utf-8")).hexdigest(),
                    len(tokens),
                )
            )
            if len(passages) > self.max_chunks:
                raise ValueError(
                    f"Vacancy {vacancy.id!r} needs at least {len(passages)} chunks; max_chunks={self.max_chunks}"
                )
            if stop == len(raw):
                break
            starts = [
                edge
                for edge in boundaries
                if start < edge <= stop
                and token_count(offsets, edge, stop) <= self.overlap_tokens
            ]
            next_start = starts[0] if starts else stop
            if next_start <= start:
                raise ValueError("Chunker failed to make whole-word progress")
            start = next_start
        return tuple(passages), {
            "document_id": vacancy.id,
            "chunks": len(passages),
            "description_bytes": len(raw),
            "title_shortened": shortened,
            "title_context": context,
            "title_stop": len(context.encode("utf-8")),
            "model_token_counts": [passage.token_count for passage in passages],
        }

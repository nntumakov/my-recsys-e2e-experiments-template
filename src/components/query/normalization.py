import re
import unicodedata
from collections.abc import Sequence


def bounded_matches(text: str, phrase: str) -> list[tuple[int, int]]:
    """Find literal phrases without matching inside words or technical symbols."""
    if not phrase:
        return []
    spans: list[tuple[int, int]] = []
    for match in re.finditer(re.escape(phrase), text):
        start, end = match.span()
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if before and (before.isalnum() or before == "_" or before in "+#/-"):
            continue
        if before == "." and start > 1 and text[start - 2].isalnum():
            continue
        if after and (after.isalnum() or after == "_" or after in "+#/-"):
            continue
        if after == "." and end + 1 < len(text) and text[end + 1].isalnum():
            continue
        spans.append((start, end))
    return spans


class NormalizeText:
    def __init__(self, protected_terms: Sequence[str] = ()) -> None:
        self.protected_terms = tuple(self.normalize(term) for term in protected_terms)
        if any(not term for term in self.protected_terms):
            raise ValueError("protected terms must be nonempty")

    def normalize(self, text: str) -> str:
        if not isinstance(text, str):
            raise ValueError("query text must be a string")
        return " ".join(unicodedata.normalize("NFKC", text).casefold().split())

    def protected_spans(
        self, normalized_text: str, raw_text: str = ""
    ) -> tuple[tuple[int, int], ...]:
        spans = {
            span
            for term in self.protected_terms
            for span in bounded_matches(normalized_text, term)
        }
        for match in re.finditer(r"\b[^\W\d_]+\b", raw_text):
            token = match.group()
            if len(token) >= 5 and token.isupper():
                # Normalizing prefixes accounts for Unicode expansion and collapsed
                # whitespace while protecting only this original occurrence.
                end = len(self.normalize(raw_text[: match.end()]))
                start = end - len(self.normalize(token))
                spans.add((start, end))
        return tuple(sorted(spans))

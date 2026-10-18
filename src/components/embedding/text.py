"""Conservative semantic text preparation, independent of lexical parsing."""

import unicodedata


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Embedding input must be text")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Embedding input requires valid Unicode") from exc
    return " ".join(unicodedata.normalize("NFC", text).split())

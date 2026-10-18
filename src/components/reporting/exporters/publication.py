import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def publish_text(path: Path, content: str) -> None:
    """Publish complete text atomically, without replacing an existing artifact."""
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                logging.getLogger(__name__).warning(
                    "Could not remove temporary export file %s", temporary
                )

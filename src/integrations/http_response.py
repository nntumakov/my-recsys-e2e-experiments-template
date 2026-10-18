"""Bound response-body reads by size and an optional readiness deadline."""

import time
from http.client import HTTPException, HTTPResponse
from socket import SHUT_RDWR
from threading import Timer


def read_response_body(
    response: HTTPResponse, maximum: int, *, deadline: float | None
) -> bytes:
    if deadline is None:
        return response.read(maximum + 1)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("HTTP response deadline expired")
    # urllib's HTTP and HTTPS responses use BufferedReader -> SocketIO -> socket.
    # Isolate the private stdlib socket access here. Shutdown interrupts reads of
    # both body data and chunk framing; socket inactivity timeouts cannot do that
    # within a total deadline when the server continuously trickles bytes.
    stream = response.fp
    connection = getattr(stream.raw, "_sock")

    def interrupt() -> None:
        try:
            connection.shutdown(SHUT_RDWR)
        except OSError:
            pass  # The completed response may already have closed its socket.

    timer = Timer(remaining, interrupt)
    timer.daemon = True
    timer.start()
    try:
        try:
            content = response.read(maximum + 1)
        except (OSError, HTTPException) as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError("HTTP response deadline expired") from exc
            raise
        if time.monotonic() >= deadline:
            raise TimeoutError("HTTP response deadline expired")
        return content
    finally:
        timer.cancel()
        timer.join()

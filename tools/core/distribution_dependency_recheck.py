"""Compare a provided fresh stream to held bytes; no paths or acquisition authority."""
from __future__ import annotations

import hashlib
from collections.abc import Callable

CHUNK_CEILING_BYTES = 65536


def compare_stream(read: Callable[[int], bytes], expected: bytes, *,
                   chunk_bytes: int = CHUNK_CEILING_BYTES) -> dict:
    """Hash independently read bytes only after exact comparison, including EOF.

    The caller owns the stream/descriptor and the origin of expected immutable
    bytes. This bounds returned payload requests, not inventory allocation,
    physical I/O, RSS, installed-input permission or snapshot authenticity.
    """
    if (type(expected) is not bytes or not callable(read) or type(chunk_bytes) is not int
            or not 0 < chunk_bytes <= CHUNK_CEILING_BYTES):
        raise ValueError("Unsupported dependency stream comparison request")
    hasher = hashlib.sha256()
    offset = seen = maximum = probes = 0

    def finish(status: str, reason: str | None = None) -> dict:
        return {"status": status, "sha256": hasher.hexdigest() if status == "MATCH" else None,
            "bytes_seen": seen, "max_chunk_bytes_seen": maximum,
            "eof_probe_reads": probes, "reason": reason}

    with memoryview(expected) as view:
        while True:
            requested = min(chunk_bytes, len(expected) - offset + 1)
            if offset == len(expected):
                probes += 1
            try:
                chunk = read(requested)
            except (OSError, ValueError, TypeError) as exc:
                return finish("UNAVAILABLE", f"stream_read:{type(exc).__name__}")
            if type(chunk) is not bytes:
                return finish("UNAVAILABLE", "stream_non_bytes")
            seen += len(chunk)
            maximum = max(maximum, len(chunk))
            if len(chunk) > requested:
                return finish("UNAVAILABLE", "stream_exceeded_request")
            if not chunk:
                return finish("MATCH") if offset == len(expected) else finish("CHANGED", "payload_shorter")
            if offset + len(chunk) > len(expected):
                return finish("CHANGED", "payload_longer")
            if chunk != view[offset:offset + len(chunk)]:
                return finish("CHANGED", "payload_bytes_changed")
            hasher.update(chunk)
            offset += len(chunk)

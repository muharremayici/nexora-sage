from __future__ import annotations

import builtins
import sys
from typing import Any


def configure_utf8_stdio(stdout: Any = None, stderr: Any = None) -> bool:
    """Configure SAGE-owned current-process text streams for deterministic UTF-8."""

    streams = (
        sys.stdout if stdout is None else stdout,
        sys.stderr if stderr is None else stderr,
    )
    configured = True
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            configured = False
    return configured


def best_effort_print(*args: Any, **kwargs: Any) -> bool:
    """Write non-authoritative console output without changing operation outcome."""

    try:
        builtins.print(*args, **kwargs)
        return True
    except (BrokenPipeError, OSError, ValueError):
        return False

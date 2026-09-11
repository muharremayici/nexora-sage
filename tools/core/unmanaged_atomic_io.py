"""Dependency-light atomic writers for files outside managed artifact storage."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def native_filesystem_path(path: str | Path) -> str:
    """Return an OS-native absolute path that preserves long Windows paths."""

    absolute = os.path.abspath(os.fspath(path))
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def save_unmanaged_json_atomic(
    path: str | Path,
    payload: Any,
    *,
    indent: int = 2,
) -> None:
    """Atomically write JSON without importing runtime config or SQLite proxies."""

    destination = Path(path)
    native_parent = native_filesystem_path(destination.parent)
    Path(native_parent).mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=native_parent,
        prefix="cm_tmp_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=indent, ensure_ascii=False)
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                os.replace(
                    native_filesystem_path(temporary_name),
                    native_filesystem_path(destination),
                )
                return
            except OSError as exc:
                last_error = exc
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        Path(native_filesystem_path(temporary_name)).unlink(missing_ok=True)

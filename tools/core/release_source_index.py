"""Local Python module inventory; no source execution or persistent cache."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Set
from pathlib import Path


def _python_paths(root: Path, excluded_parts: Set[str]) -> Iterator[Path]:
    # Preserve native glob/error/audit behavior on runtimes not yet reviewed.
    if (sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 14)
            or type(root) is not type(Path())):
        yield from root.rglob("*.py")
        return
    # CPython 3.14 rglob delegates to glob with this native-flavour pattern.
    # Keep audit refusals outside the OSError suppression used by glob.
    sys.audit("pathlib.Path.rglob", root, "*.py")
    sys.audit("pathlib.Path.glob", root, os.path.join("**", "*.py"))
    pending = [root]
    reported_errors: set[str] = set()
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(str(directory)) as stream:
                entries = list(stream)
        except OSError as exc:
            if "directory_scan" not in reported_errors:
                reported_errors.add("directory_scan")
                print("[release-source-index] directory_scan_unavailable: "
                      + type(exc).__name__[:64], file=sys.stderr)
            continue
        for entry in entries:
            if entry.name in excluded_parts:
                continue
            path = directory / entry.name
            # Native matching includes hidden/uppercase names and directories.
            if path.match("*.py"):
                yield path
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
            except OSError as exc:
                if "entry_type" not in reported_errors:
                    reported_errors.add("entry_type")
                    print("[release-source-index] entry_type_unavailable: "
                          + type(exc).__name__[:64], file=sys.stderr)


def build_local_python_module_index(root: Path, *, excluded_parts: Set[str]) -> dict[str, Path]:
    """Keep native path ordering and package precedence, pruning exclusions early."""
    result: dict[str, Path] = {}
    for path in sorted(_python_paths(root, excluded_parts)):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in excluded_parts for part in relative.parts):
            continue
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if parts:
            name = ".".join(parts)
            if name not in result or path.name == "__init__.py":
                result[name] = path
    return result

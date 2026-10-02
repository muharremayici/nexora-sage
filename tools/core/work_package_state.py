"""Shared read-only work-package state and confined dirty-source identity.

Receipt collection and transition orchestration consume this leaf service;
it must not import either consumer or execute a transition.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR
from tools.core.json_io import load_json_object_strict


LEDGER_PATH = CONFIG_DIR / "sage_active_work_package.json"


def load_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    return load_json_object_strict(path, label="SAGE active work package ledger")


def package_from_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    package = ledger.get("active_package")
    if not isinstance(package, dict):
        raise ValueError("SAGE active work package ledger must contain active_package")
    return package


def active_work_package(*, ledger_path: Path = LEDGER_PATH) -> dict[str, Any]:
    return package_from_ledger(load_ledger(ledger_path))


def _inherited_dirty_source_path(relative: str, root: Path) -> Path:
    pure = PurePosixPath(relative)
    if (
        "\\" in relative
        or pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or ":" in pure.parts[0]
    ):
        raise ValueError(f"Invalid inherited dirty path: {relative}")
    base = root.resolve()
    source = base.joinpath(*pure.parts)
    resolved = source.resolve()
    if base not in resolved.parents or source.is_symlink():
        raise ValueError(f"Inherited dirty path is linked or outside SAGE: {relative}")
    return source


def inherited_dirty_file_identity(relative: str, *, root: Path = CODE_MAPS_DIR) -> str:
    """Return a confined source hash or a stable deleted-file tombstone."""
    source = _inherited_dirty_source_path(relative, root)
    if not source.exists():
        return "missing"
    if not source.is_file():
        raise ValueError(f"Inherited dirty path is not a file: {relative}")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

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
LOOP_CONTRACT_PATH = CONFIG_DIR / "sage_development_loop_contract.json"


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


def _closure_contract() -> dict[str, Any]:
    loop = load_json_object_strict(LOOP_CONTRACT_PATH, label="SAGE development loop contract")
    contract = loop.get("package_closure")
    if not isinstance(contract, dict):
        raise ValueError("SAGE development loop must contain package_closure")
    return contract


def package_closure_is_complete(
    package: dict[str, Any],
    closure_contract: dict[str, Any] | None = None,
) -> bool:
    contract = closure_contract if closure_contract is not None else _closure_contract()
    required = [str(item) for item in contract.get("required", []) if str(item)]
    validation = contract.get("validation") if isinstance(contract.get("validation"), dict) else {}
    completion_statuses = {
        str(item)
        for item in validation.get("completion_evidence_statuses", [])
        if str(item)
    }
    closure = package.get("closure") if isinstance(package.get("closure"), dict) else {}
    evidence = closure.get("evidence") if isinstance(closure.get("evidence"), dict) else {}
    return bool(required) and bool(completion_statuses) and (
        package.get("status") == "closed"
        and closure.get("status") == "closed"
        and all(
            isinstance(evidence.get(item), dict)
            and str(evidence[item].get("status") or "") in completion_statuses
            for item in required
        )
    )


def successor_matches_selection(
    successor: dict[str, Any],
    selection: dict[str, Any],
) -> bool:
    """Require one exact machine-selected package seed before ledger transition."""
    if (
        selection.get("status") != "SELECTED"
        or selection.get("transition_validation_eligible") is not True
    ):
        return False
    selected_id = str(selection.get("selected_work_item_id") or "")
    seed = (
        selection.get("selected_package_seed")
        if isinstance(selection.get("selected_package_seed"), dict)
        else {}
    )
    seed_scope = seed.get("release_scope") if isinstance(seed.get("release_scope"), dict) else {}
    successor_scope = (
        successor.get("release_scope")
        if isinstance(successor.get("release_scope"), dict)
        else {}
    )
    scope_fields = (
        "mode",
        "roadmap_phase",
        "concrete_release",
        "does_not_expand_current_release_claims",
    )
    return (
        bool(selected_id)
        and [str(item) for item in successor.get("work_item_ids", []) if str(item)]
        == [selected_id]
        and [str(item) for item in seed.get("work_item_ids", []) if str(item)]
        == [selected_id]
        and str(successor.get("execution_wave") or "")
        == str(seed.get("execution_wave") or "")
        and all(successor_scope.get(field) == seed_scope.get(field) for field in scope_fields)
    )

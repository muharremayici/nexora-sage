from __future__ import annotations

from pathlib import Path
from typing import Iterable

from tools.core.honesty_telemetry import record_honesty_event
from tools.core.source_snapshot_reader import load_source_text
from tools.core.source_snapshot_integrity import source_text_hash


def atlas_file_paths(project_data: dict, extensions: Iterable[str] | None = None) -> list[str]:
    allowed = {str(item).lower() for item in extensions or []}
    files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
    return sorted(
        str(rel).replace("\\", "/")
        for rel in files
        if not allowed or Path(str(rel)).suffix.lower() in allowed
    )


def read_atlas_bound_source(
    *,
    component: str,
    project: str,
    project_root: Path,
    rel_path: str,
    atlas_entry: dict,
    reason: str,
) -> str | None:
    """Read raw source only when it is bound to a current Atlas node."""
    normalized = str(rel_path).replace("\\", "/").lstrip("/")
    path = (project_root / normalized).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        record_honesty_event(
            component=component,
            category="ssot_boundary_violation",
            operation="read_source",
            subject=f"{project}::{normalized}",
            severity="error",
            reason="source path escaped the registered project root",
            claim_impact="finding_suppressed",
            exception=exc,
        )
        return None
    if not atlas_entry:
        record_honesty_event(
            component=component,
            category="unknown_evidence",
            operation="read_source",
            subject=f"{project}::{normalized}",
            reason="raw source request has no committed Atlas node",
            claim_impact="finding_suppressed",
        )
        return None
    snapshot_content = load_source_text(
        project,
        normalized,
        fallback_path=path,
        component=component,
        allow_live_fallback=False,
    )
    try:
        content = snapshot_content if snapshot_content is not None else path.read_bytes().decode("utf-8", errors="replace")
        expected_hash = str(atlas_entry.get("hash") or "")
        actual_hash = source_text_hash(content, expected_hash)
    except (OSError, ValueError) as exc:
        record_honesty_event(
            component=component,
            category="caught_error",
            operation="read_source",
            subject=f"{project}::{normalized}",
            reason="Atlas-bound source could not be read",
            claim_impact="finding_suppressed",
            exception=exc,
        )
        return None
    if actual_hash is None or actual_hash != expected_hash:
        record_honesty_event(
            component=component,
            category="stale_evidence",
            operation="read_source",
            subject=f"{project}::{normalized}",
            reason="source content identity is unavailable or differs from the requested Atlas snapshot",
            fallback="rerun Atlas before downstream analysis",
            claim_impact="finding_suppressed",
            evidence_source="atlas_hash",
            details={"reason": reason},
        )
        return None
    return content

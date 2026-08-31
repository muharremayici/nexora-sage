from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from tools.core.honesty_telemetry import record_honesty_event
from tools.core.source_snapshot_reader import load_source_text


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
    if snapshot_content is not None:
        return snapshot_content
    try:
        stat = path.stat()
    except OSError as exc:
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
    expected_size = atlas_entry.get("size")
    expected_mtime = atlas_entry.get("mtime")
    if expected_size is not None and expected_mtime is not None:
        try:
            size_matches = int(expected_size) == int(stat.st_size)
            mtime_matches = abs(float(expected_mtime) - float(stat.st_mtime)) < 0.001
        except (TypeError, ValueError):
            size_matches = False
            mtime_matches = False
        if not (size_matches and mtime_matches):
            record_honesty_event(
                component=component,
                category="stale_evidence",
                operation="read_source",
                subject=f"{project}::{normalized}",
                reason="source metadata changed after the committed Atlas snapshot",
                fallback="rerun Atlas before downstream analysis",
                claim_impact="finding_suppressed",
                evidence_source="atlas_mtime_size",
                details={"reason": reason},
            )
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
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

    try:
        raw_content = path.read_bytes()
        content = raw_content.decode("utf-8", errors="replace")
    except OSError as exc:
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
    expected_hash = str(atlas_entry.get("hash") or "")
    if len(expected_hash) == 64:
        actual_hash = hashlib.sha256(raw_content).hexdigest()
        if actual_hash != expected_hash:
            record_honesty_event(
                component=component,
                category="stale_evidence",
                operation="read_source",
                subject=f"{project}::{normalized}",
                reason="source content changed after the committed Atlas snapshot",
                fallback="rerun Atlas before downstream analysis",
                claim_impact="finding_suppressed",
                evidence_source="atlas_hash",
                details={"reason": reason},
            )
            return None
    return content

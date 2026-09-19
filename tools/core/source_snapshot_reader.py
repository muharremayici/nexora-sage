from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import RAW_DIR, ROOT
from tools.core.db import SQLiteManager
from tools.core.honesty_telemetry import record_honesty_event
from tools.core.logger import logger
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.target_repository_trust import is_target_path_contained


DB_PATH = RAW_DIR / "codemaps.db"


def _normalize_rel_path(rel_path: str | Path) -> str:
    return str(rel_path or "").replace("\\", "/").strip().lstrip("/")


def _normalize_fallback_path(project_key: str, rel_path: str, fallback_path: Path | None) -> Path | None:
    """Prefer canonical live-source fallback paths before touching disk.

    Some downstream engines know the runtime project root while agent-facing
    paths stay repo-root relative, e.g. MAIN root is ``repo/src`` and the
    requested path is ``src/App.tsx``. Reading ``project_root / rel_path`` would
    incorrectly become ``repo/src/src/App.tsx``. Source snapshots remain the
    primary truth; this helper only makes the last-resort live-file fallback
    deterministic and repo-root aware.
    """

    if fallback_path is None:
        return None

    rel = _normalize_rel_path(rel_path)
    root = Path(ROOT).resolve()
    candidates: list[Path] = []

    def add(candidate: Path | None) -> None:
        if candidate is None:
            return
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if not is_target_path_contained(root, resolved):
            return
        if resolved not in candidates:
            candidates.append(resolved)

    add(Path(fallback_path))
    if rel:
        add(root / rel)

    project_root = resolve_runtime_projects(root).get(str(project_key or "").strip())
    if project_root is not None and rel:
        project_root = project_root.resolve()
        add(project_root / rel)
        root_name = project_root.name.replace("\\", "/").strip("/")
        if root_name and rel.startswith(f"{root_name}/"):
            add(project_root / rel[len(root_name) + 1 :])

    for candidate in candidates:
        if candidate.exists():
            return candidate

    if project_root is not None and rel:
        root_name = project_root.name.replace("\\", "/").strip("/")
        if root_name and rel.startswith(f"{root_name}/"):
            candidate = (project_root / rel[len(root_name) + 1 :]).resolve()
            if is_target_path_contained(root, candidate):
                return candidate
    if rel:
        candidate = (root / rel).resolve()
        if is_target_path_contained(root, candidate):
            return candidate
    return None


def load_source_text(
    project_key: str,
    rel_path: str | Path,
    *,
    fallback_path: Path | None = None,
    component: str = "source_snapshot_reader",
    allow_live_fallback: bool = True,
) -> str | None:
    """Return source text from SQLite source_snapshots before touching disk.

    The source_snapshots table is projected from the current Atlas file IDs.
    When the projected row is usable, downstream analyzers should consume that
    consistent Atlas-time source text. Disk fallback remains available for
    missing/error/oversized rows and is reported to honesty telemetry.
    """

    project = str(project_key or "").strip()
    rel = _normalize_rel_path(rel_path)
    if project and rel and DB_PATH.exists():
        try:
            manager = SQLiteManager(DB_PATH)
            with manager.get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT content, content_hash, status, error
                    FROM source_snapshots
                    WHERE project_key = ? AND rel_path = ?
                    LIMIT 1;
                    """,
                    (project, rel),
                ).fetchone()
            if row:
                status = str(row["status"] or "")
                content = row["content"]
                content_hash = str(row["content_hash"] or "")
                if status == "ok" and isinstance(content, str) and content and content_hash:
                    return content
                _record_snapshot_fallback(
                    component=component,
                    project=project,
                    rel=rel,
                    reason=f"source snapshot status={status or 'unknown'}",
                    fallback_path=fallback_path,
                    error=str(row["error"] or ""),
                )
        except Exception as exc:
            _record_snapshot_fallback(
                component=component,
                project=project,
                rel=rel,
                reason="source snapshot lookup failed",
                fallback_path=fallback_path,
                exception=exc,
            )

    if not allow_live_fallback:
        return None
    fallback_path = _normalize_fallback_path(project, rel, fallback_path)
    if fallback_path is None:
        return None
    try:
        return fallback_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        logger.warning("Failed to read source fallback %s: %s", fallback_path, exc)
        record_honesty_event(
            component=component,
            category="caught_error",
            operation="source_text_fallback_read",
            subject=str(fallback_path),
            reason="source snapshot and live source fallback were unavailable",
            fallback="missing_source_text",
            claim_impact="source_text_analysis_degraded",
            evidence_source="source_snapshot_reader",
            exception=exc,
        )
        return None


def _record_snapshot_fallback(
    *,
    component: str,
    project: str,
    rel: str,
    reason: str,
    fallback_path: Path | None,
    error: str = "",
    exception: BaseException | None = None,
) -> None:
    try:
        details: dict[str, Any] = {
            "project": project,
            "rel_path": rel,
            "fallback_path": str(fallback_path) if fallback_path else "",
        }
        if error:
            details["snapshot_error"] = error
        record_honesty_event(
            component=component,
            category="source_snapshot",
            operation="load_source_text",
            subject=f"{project}::{rel}",
            severity="info",
            reason=reason,
            fallback="live_source_file" if fallback_path else "missing_source_text",
            claim_impact="source_text_analysis_may_use_live_files",
            evidence_source="source_snapshot_reader",
            details=details,
            exception=exception,
        )
    except Exception as telemetry_exc:
        logger.warning("Source snapshot fallback telemetry failed: %s", telemetry_exc)

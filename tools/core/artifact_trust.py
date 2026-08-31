from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from tools.core.config import RAW_DIR
from tools.core.artifact_freshness_contract import (
    artifact_state_meta,
    chain_evaluation,
    evaluate_artifact_freshness_contract,
)
from tools.core.json_io import load_json_file


def _artifact_validation_epoch(raw_dir: Path, name: str) -> float:
    meta = artifact_state_meta(raw_dir, name)
    return float(meta.get("validation_mtime") or meta.get("mtime") or 0.0)


def _json_extract(conn: sqlite3.Connection, name: str, path: str) -> Any:
    row = conn.execute(
        "SELECT json_extract(payload, ?) AS value FROM state_payloads WHERE name = ?;",
        (path, name),
    ).fetchone()
    if not row:
        return None
    value = row["value"]
    if isinstance(value, str) and value[:1] in {"{", "["}:
        try:
            return __import__("json").loads(value)
        except Exception:
            return value
    return value


def _artifact_fact(conn: sqlite3.Connection, name: str, key: str) -> Any:
    row = conn.execute(
        "SELECT fact_value FROM artifact_facts WHERE artifact_name = ? AND fact_key = ?;",
        (name, key),
    ).fetchone()
    if not row:
        return None
    value = row["fact_value"]
    try:
        return __import__("json").loads(value)
    except Exception:
        return value


def _sqlite_truth_summary(raw_dir: Path) -> dict[str, Any] | None:
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            atlas_projects = {
                str(row["project_key"])
                for row in conn.execute("SELECT project_key FROM projects;").fetchall()
            }
            audit_scope = _artifact_fact(conn, "audit_report", "summary.audit_scope")
            if not isinstance(audit_scope, dict):
                audit_scope = _json_extract(conn, "audit_report", "$.summary.audit_scope")
            release_gate_status = _artifact_fact(conn, "quality_gate", "release_gate_status")
            if release_gate_status is None:
                release_gate_status = _json_extract(conn, "quality_gate", "$.release_gate_status")
            violation_projects = {
                str(row["project_key"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT f.project_key
                    FROM findings fi
                    JOIN files f ON f.file_id = fi.file_id
                    WHERE fi.engine_name = ?;
                    """,
                    ("audit_report",),
                ).fetchall()
            }
    except Exception:
        return None
    if not atlas_projects or not isinstance(audit_scope, dict):
        return None
    audit_projects = set(audit_scope.get("audited_projects") or [])
    return {
        "atlas_projects": atlas_projects,
        "audit_scope": audit_scope,
        "audit_projects": audit_projects,
        "violation_projects": violation_projects,
        "release_gate_status": release_gate_status,
    }


def _check(name: str, passed: bool, details: str, *, severity: str = "error") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "severity": severity,
        "details": details,
    }


def build_artifact_trust_summary(raw_dir: Path | None = None) -> dict[str, Any]:
    """Build a compact trust/freshness summary for agent-facing surfaces.

    This does not replace full artifact validation. It answers the question an
    agent needs before acting: are the Atlas, audit, and quality artifacts from
    the same current evidence chain, and is the audit scope explicit?
    """

    raw_dir = Path(raw_dir or RAW_DIR)
    sqlite_summary = _sqlite_truth_summary(raw_dir)
    if sqlite_summary is not None:
        atlas_projects = sqlite_summary["atlas_projects"]
        audit_scope = sqlite_summary["audit_scope"]
        audit_projects = sqlite_summary["audit_projects"]
        violation_projects = sqlite_summary["violation_projects"]
        release_gate_status = sqlite_summary["release_gate_status"]
    else:
        atlas = load_json_file(raw_dir / "atlas.json", {})
        audit = load_json_file(raw_dir / "audit_report.json", {})
        quality_gate = load_json_file(raw_dir / "quality_gate.json", {})
        atlas_projects = set(atlas.keys()) if isinstance(atlas, dict) else set()
        audit_summary = audit.get("summary", {}) if isinstance(audit, dict) else {}
        audit_scope = audit_summary.get("audit_scope", {}) if isinstance(audit_summary, dict) else {}
        audit_projects = set(audit_scope.get("audited_projects") or []) if isinstance(audit_scope, dict) else set()
        violation_projects = set((audit_summary.get("by_project") or {}).keys()) if isinstance(audit_summary, dict) else set()
        release_gate_status = quality_gate.get("release_gate_status") if isinstance(quality_gate, dict) else None

    checks: list[dict[str, Any]] = []
    for artifact_name in ("atlas", "audit_report", "quality_gate"):
        meta = artifact_state_meta(raw_dir, artifact_name)
        detail = "source=%s validation_mtime=%s content_mtime=%s" % (
            meta.get("source"),
            meta.get("validation_mtime", meta.get("mtime", 0.0)),
            meta.get("content_mtime", meta.get("mtime", 0.0)),
        )
        checks.append(
            _check(
                f"artifact_present:{artifact_name}",
                bool(meta.get("exists")),
                detail,
            )
        )
        checks.append(
            _check(
                f"sqlite_primary:{artifact_name}",
                meta.get("source") == "sqlite_state_payloads",
                detail,
                severity="warning",
            )
        )

    checks.append(
        _check(
            "audit_scope:present",
            isinstance(audit_scope, dict) and bool(audit_scope),
            f"audit_scope_keys={sorted(audit_scope.keys()) if isinstance(audit_scope, dict) else []}",
        )
    )
    checks.append(
        _check(
            "audit_scope:atlas_project_count_matches",
            isinstance(audit_scope, dict)
            and int(audit_scope.get("atlas_project_count") or -1) == len(atlas_projects),
            f"audit_scope={audit_scope.get('atlas_project_count') if isinstance(audit_scope, dict) else None} atlas={len(atlas_projects)}",
        )
    )
    checks.append(
        _check(
            "audit_scope:audited_projects_are_atlas_subset",
            bool(audit_projects) and audit_projects.issubset(atlas_projects),
            f"audited={sorted(audit_projects)} atlas={sorted(atlas_projects)}",
        )
    )
    checks.append(
        _check(
            "audit_scope:violation_projects_are_audited_subset",
            violation_projects.issubset(audit_projects),
            f"violations={sorted(violation_projects)} audited={sorted(audit_projects)}",
        )
    )

    atlas_mtime = _artifact_validation_epoch(raw_dir, "atlas")
    audit_mtime = _artifact_validation_epoch(raw_dir, "audit_report")
    quality_mtime = _artifact_validation_epoch(raw_dir, "quality_gate")
    checks.append(
        _check(
            "freshness:audit_not_older_than_atlas",
            audit_mtime >= atlas_mtime - 0.001,
            f"atlas_mtime={atlas_mtime} audit_mtime={audit_mtime}",
        )
    )
    checks.append(
        _check(
            "freshness:quality_gate_not_older_than_audit",
            quality_mtime >= audit_mtime - 0.001,
            f"audit_mtime={audit_mtime} quality_gate_mtime={quality_mtime}",
            severity="warning",
        )
    )
    checks.append(
        _check(
            "quality_gate:has_explicit_release_status",
            bool(release_gate_status),
            f"release_gate_status={release_gate_status}",
        )
    )
    system_scope = (
        "SAGE_ON_SAGE"
        if raw_dir.resolve() == RAW_DIR.resolve()
        else "SAGE_ON_REPOSITORY"
    )
    freshness_contract = evaluate_artifact_freshness_contract(
        raw_dir,
        system_scope=system_scope,
    )
    agent_chain = chain_evaluation(freshness_contract, "agent_surface_action_chain")
    checks.append(
        _check(
            "freshness_contract:agent_surface_action_chain_not_stale",
            agent_chain.get("status") != "FAIL",
            f"status={agent_chain.get('status')} refresh_plan={agent_chain.get('refresh_plan')}",
        )
    )

    failures = [row for row in checks if not row.get("passed") and row.get("severity") == "error"]
    warnings = [row for row in checks if not row.get("passed") and row.get("severity") == "warning"]
    return {
        "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "scope": {
            "atlas_project_count": len(atlas_projects),
            "audited_project_count": len(audit_projects),
            "violation_project_count": len(violation_projects),
            "audited_projects": sorted(audit_projects),
        },
        "freshness": {
            "atlas_mtime": atlas_mtime,
            "audit_report_mtime": audit_mtime,
            "quality_gate_mtime": quality_mtime,
        },
        "freshness_contract": {
            "system_scope": system_scope,
            "status": freshness_contract.get("summary", {}).get("status"),
            "agent_surface_action_chain": agent_chain,
        },
    }

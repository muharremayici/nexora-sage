from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.artifact_freshness_contract import load_artifact_freshness_contract
from tools.core.config import OUTPUT_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.source_evidence import read_atlas_bound_source
from tools.core.source_snapshot_reader import load_source_text


RAW_OUTPUT_PATH = RAW_DIR / "watchdog_snapshot_freshness_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "watchdog_snapshot_freshness_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any, *, severity: str = "error") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "severity": severity, "details": details}


def _first_ok_source_snapshot() -> dict[str, Any] | None:
    db_path = RAW_DIR / "codemaps.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT project_key, rel_path, content, content_hash, status
                FROM source_snapshots
                WHERE status = 'ok' AND content IS NOT NULL AND length(content) > 0
                ORDER BY
                  CASE WHEN project_key = 'MAIN' THEN 0 ELSE 1 END,
                  length(content) ASC
                LIMIT 1;
                """
            ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def _run_source_snapshot_sqlite_probe() -> dict[str, Any]:
    row = _first_ok_source_snapshot()
    if not row:
        return _check(
            "source_snapshot_sqlite_probe_available",
            False,
            "No usable source_snapshots row is available. This is acceptable before Atlas has populated SQLite, but agent action packets must not claim snapshot-backed source proof.",
            severity="warning",
        )
    loaded = load_source_text(
        str(row.get("project_key") or ""),
        str(row.get("rel_path") or ""),
        component="watchdog_snapshot_freshness_validation",
    )
    return _check(
        "source_snapshot_reader_uses_sqlite_snapshot",
        loaded == row.get("content"),
        {
            "project_key": row.get("project_key"),
            "rel_path": row.get("rel_path"),
            "snapshot_hash": row.get("content_hash"),
            "loaded_matches_sqlite_content": loaded == row.get("content"),
        },
    )


def _first_agent_packet_snapshot_target() -> dict[str, Any] | None:
    db_path = RAW_DIR / "codemaps.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT files.project_key, projects.path AS project_path, files.rel_path,
                       source_snapshots.content_hash, symbols.name, symbols.line,
                       symbols.end_line, symbols.source_lines
                FROM source_snapshots
                JOIN files
                  ON files.project_key = source_snapshots.project_key
                 AND files.rel_path = source_snapshots.rel_path
                LEFT JOIN projects ON projects.project_key = files.project_key
                JOIN symbols ON symbols.file_id = files.file_id
                WHERE source_snapshots.status = 'ok'
                  AND source_snapshots.content IS NOT NULL
                  AND length(source_snapshots.content) > 0
                  AND symbols.line > 0
                  AND symbols.end_line >= symbols.line
                  AND (symbols.end_line - symbols.line) <= 40
                ORDER BY
                  CASE WHEN files.project_key = 'MAIN' THEN 0 ELSE 1 END,
                  files.rel_path,
                  symbols.line
                LIMIT 1;
                """
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    rel_path = str(row["rel_path"] or "").replace("\\", "/").strip("/")
    project_path = str(row["project_path"] or "").replace("\\", "/").strip("/")
    if project_path and project_path not in {".", "./"} and not rel_path.startswith(f"{project_path}/"):
        agent_rel_path = f"{project_path}/{rel_path}".replace("//", "/")
    else:
        agent_rel_path = rel_path
    return {
        "project_key": str(row["project_key"] or ""),
        "rel_path": rel_path,
        "agent_rel_path": agent_rel_path,
        "content_hash": str(row["content_hash"] or ""),
        "symbol": str(row["name"] or ""),
        "line": int(row["line"] or 0),
        "end_line": int(row["end_line"] or row["line"] or 0),
        "source_lines": str(row["source_lines"] or ""),
    }


def _run_mcp_agent_source_snippet_probe() -> dict[str, Any]:
    target = _first_agent_packet_snapshot_target()
    if not target:
        return _check(
            "mcp_agent_packet_source_snapshot_probe_available",
            False,
            "No compact symbol-backed source snapshot was available for an MCP agent packet probe.",
            severity="warning",
        )
    try:
        from tools.mcp import server as mcp_server

        target_ref = f"{target['project_key']}::{target['agent_rel_path']}"
        brief = mcp_server.inspect_file(target_ref, format="brief")
    except Exception as exc:
        return _check(
            "mcp_agent_packet_uses_sqlite_snapshot",
            False,
            {"target": target, "error": str(exc)},
        )

    hash_prefix = str(target.get("content_hash") or "")[:12]
    passed = (
        "# Target Inspection Brief" in brief
        and "source_grounding:" in brief
        and 'source_snapshot_status: "ok"' in brief
        and f"source_snapshot_hash_prefix: {json.dumps(hash_prefix, ensure_ascii=False)}" in brief
        and 'drift_check_status: "match"' in brief
        and "target_source_snippets:" in brief
        and "snippet_status: \"included\"" in brief
        and "code: |-" in brief
        and f"target_ref: {json.dumps(target_ref, ensure_ascii=False)}" in brief
        and f"symbol: {json.dumps(target['symbol'], ensure_ascii=False)}" in brief
    )
    return _check(
        "mcp_agent_packet_uses_sqlite_snapshot",
        passed,
        {
            "target_ref": target_ref,
            "symbol": target.get("symbol"),
            "source_lines": target.get("source_lines"),
            "hash_prefix": hash_prefix,
            "brief_excerpt": brief[:1200],
        },
    )


def _run_atlas_bound_stale_probe() -> list[dict[str, Any]]:
    probe_dir = OUTPUT_DIR / ".tmp" / "watchdog_snapshot_freshness_probe"
    if probe_dir.exists():
        shutil.rmtree(probe_dir)
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_file = probe_dir / "sample.ts"
    original = "export const value = 1;\n"
    probe_file.write_text(original, encoding="utf-8")
    stat = probe_file.stat()
    atlas_entry = {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "hash": hashlib.sha256(probe_file.read_bytes()).hexdigest(),
    }
    fresh_read = read_atlas_bound_source(
        component="watchdog_snapshot_freshness_validation",
        project="PROBE",
        project_root=probe_dir,
        rel_path="sample.ts",
        atlas_entry=atlas_entry,
        reason="freshness validator fresh source probe",
    )
    probe_file.write_text(original + "export const changed = true;\n", encoding="utf-8")
    stale_read = read_atlas_bound_source(
        component="watchdog_snapshot_freshness_validation",
        project="PROBE",
        project_root=probe_dir,
        rel_path="sample.ts",
        atlas_entry=atlas_entry,
        reason="freshness validator stale source probe",
    )
    shutil.rmtree(probe_dir, ignore_errors=True)
    return [
        _check(
            "atlas_bound_source_reads_matching_snapshot",
            fresh_read == original,
            {"fresh_read_matches": fresh_read == original},
        ),
        _check(
            "atlas_bound_source_blocks_changed_snapshot",
            stale_read is None,
            {"stale_read_is_none": stale_read is None},
        ),
    ]


def build_validation() -> dict[str, Any]:
    contract = load_artifact_freshness_contract()
    default_policy = contract.get("default_policy", {}) if isinstance(contract, dict) else {}
    checks: list[dict[str, Any]] = [
        _check(
            "freshness_contract_declares_active_target_repository_rule",
            bool(default_policy.get("active_target_repository_rule")),
            default_policy.get("active_target_repository_rule"),
        ),
        _check(
            "freshness_contract_declares_snapshot_signal_sources",
            {"atlas_commit.snapshot_id", "source_snapshots.content_hash", "watchdog_session"}.issubset(
                {str(item) for item in default_policy.get("snapshot_signal_sources", [])}
            ),
            default_policy.get("snapshot_signal_sources"),
        ),
        _run_source_snapshot_sqlite_probe(),
        _run_mcp_agent_source_snippet_probe(),
    ]
    checks.extend(_run_atlas_bound_stale_probe())
    failures = [row for row in checks if not row.get("passed") and row.get("severity") == "error"]
    warnings = [row for row in checks if not row.get("passed") and row.get("severity") == "warning"]
    return {
        "meta": {
            "kind": "watchdog_snapshot_freshness_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_watchdog_snapshot_freshness",
        },
        "summary": {
            "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
            "total_checks": len(checks),
            "passed_checks": sum(1 for row in checks if row.get("passed")),
            "failed_checks": len(failures),
            "warning_checks": len(warnings),
        },
        "checks": checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Watchdog Snapshot Freshness Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- total_checks: `{summary.get('total_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        f"- warning_checks: `{summary.get('warning_checks')}`",
        "",
        "| Check | Result | Severity | Details |",
        "|---|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        details = details.replace("\n", " ")[:500]
        escaped_details = details.replace("|", "\\|")
        lines.append(
            f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | `{check.get('severity')}` | {escaped_details} |"
        )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

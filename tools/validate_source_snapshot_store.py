from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.atlas_io import load_atlas_data
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.runtime_project_scope import project_runtime_atlas, set_runtime_project_filter


DB_PATH = RAW_DIR / "codemaps.db"
REPORT_NAME = "source_snapshot_store_validation"
POLICY_PATH = CONFIG_DIR / "source_snapshot_store_policy.json"


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _load_validation_contract() -> tuple[dict[str, Any], list[str]]:
    payload = load_json_file(POLICY_PATH, default={})
    if not isinstance(payload, dict):
        return {}, ["policy_root_not_object"]
    contract = payload.get("validation_contract")
    if not isinstance(contract, dict):
        return {}, ["missing_validation_contract"]
    errors: list[str] = []
    if not _string_set(contract.get("required_columns")):
        errors.append("missing_required_columns")
    if not _string_set(contract.get("required_tables")):
        errors.append("missing_required_tables")
    return contract, errors


def _log(message: str) -> None:
    print(f"[source-snapshot-store] {message}", flush=True)


def _db_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table});").fetchall()}


def _atlas_file_count(atlas: dict[str, Any]) -> int:
    total = 0
    for project, pdata in atlas.items():
        if project == "symbols" or not isinstance(pdata, dict):
            continue
        files = pdata.get("files", {})
        if isinstance(files, dict):
            total += len(files)
    return total


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _scope_sql(projects: list[str], column: str = "project_key") -> tuple[str, tuple[str, ...]]:
    if not projects:
        return " AND 1 = 0", ()
    placeholders = ", ".join("?" for _ in projects)
    return f" AND {column} IN ({placeholders})", tuple(projects)


def validate_source_snapshot_store(projects: list[str] | None = None) -> dict[str, Any]:
    _log("START validation")
    checks: list[dict[str, Any]] = []
    validation_contract, contract_errors = _load_validation_contract()
    required_columns = _string_set(validation_contract.get("required_columns"))
    required_tables = _string_set(validation_contract.get("required_tables"))
    set_runtime_project_filter(projects)
    persisted_atlas = load_atlas_data()
    atlas, scope = project_runtime_atlas(
        persisted_atlas if isinstance(persisted_atlas, dict) else {}
    )
    selected_projects = list(scope.get("analyzed_projects") or [])
    atlas_count = _atlas_file_count(atlas)
    scope_clause, scope_params = _scope_sql(selected_projects)
    _log(
        "PASS load_atlas "
        f"files={atlas_count} projects={','.join(selected_projects) or 'NONE'} "
        f"preserved={len(scope.get('preserved_only_projects') or [])}"
    )

    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            }
            columns = _db_columns(conn, "source_snapshots") if "source_snapshots" in tables else set()
            snapshot_count = (
                int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM source_snapshots WHERE 1 = 1{scope_clause};",
                        scope_params,
                    ).fetchone()[0]
                )
                if "source_snapshots" in tables
                else 0
            )
            status_counts = (
                {
                    str(row["status"]): int(row["count"])
                    for row in conn.execute(
                        f"SELECT status, COUNT(*) AS count FROM source_snapshots "
                        f"WHERE 1 = 1{scope_clause} GROUP BY status ORDER BY status;",
                        scope_params,
                    ).fetchall()
                }
                if "source_snapshots" in tables
                else {}
            )
            ok_count = int(status_counts.get("ok", 0))
            stale_hash_rows = (
                int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM source_snapshots
                        WHERE status = 'ok'
                          AND (content_hash IS NULL OR content_hash = '' OR content = '')
                        """ + scope_clause + ";",
                        scope_params,
                    ).fetchone()[0]
                )
                if "source_snapshots" in tables
                else 0
            )
            orphan_rows = (
                int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM source_snapshots AS ss
                        LEFT JOIN files AS f ON f.file_id = ss.file_id
                        WHERE f.file_id IS NULL
                        """ + _scope_sql(selected_projects, "ss.project_key")[0] + ";",
                        _scope_sql(selected_projects, "ss.project_key")[1],
                    ).fetchone()[0]
                )
                if "source_snapshots" in tables
                else 0
            )
            hash_mismatch_rows = (
                int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM source_snapshots AS ss
                        JOIN files AS f ON f.file_id = ss.file_id
                        WHERE ss.status = 'ok' AND ss.content_hash != f.hash
                        """ + _scope_sql(selected_projects, "ss.project_key")[0] + ";",
                        _scope_sql(selected_projects, "ss.project_key")[1],
                    ).fetchone()[0]
                )
                if "source_snapshots" in tables and "files" in tables
                else 0
            )
    else:
        tables = set()
        columns = set()
        snapshot_count = 0
        status_counts = {}
        ok_count = 0
        stale_hash_rows = 0
        orphan_rows = 0
        hash_mismatch_rows = 0

    checks.extend(
        [
            _check(
                "validation_is_read_only",
                True,
                "Validator consumes the producer-owned SQLite projection without refreshing it.",
            ),
            _check(
                "runtime_project_scope_is_resolved",
                bool(selected_projects) and not scope.get("unavailable_requested_projects"),
                scope,
            ),
            _check("validation_contract_present", not contract_errors, {"contract_errors": contract_errors}),
            _check("required_tables_present", required_tables <= tables, {"required_tables": sorted(required_tables), "tables": sorted(tables)}),
            _check("source_snapshots_table_exists", "source_snapshots" in tables, sorted(tables)),
            _check("source_snapshots_columns_present", required_columns <= columns, {"required_columns": sorted(required_columns), "columns": sorted(columns)}),
            _check(
                "snapshot_count_matches_atlas_files",
                atlas_count == snapshot_count,
                {"atlas_files": atlas_count, "source_snapshots": snapshot_count},
            ),
            _check("ok_snapshots_have_hash_and_content", stale_hash_rows == 0, {"stale_hash_rows": stale_hash_rows}),
            _check(
                "live_source_snapshots_materialized",
                atlas_count == 0 or ok_count > 0,
                {"ok": ok_count, "atlas_files": atlas_count, "status_counts": status_counts},
            ),
            _check("source_snapshots_have_file_fk", orphan_rows == 0, {"orphan_rows": orphan_rows}),
            _check(
                "source_snapshot_hashes_match_atlas_files",
                hash_mismatch_rows == 0,
                {"hash_mismatch_rows": hash_mismatch_rows},
            ),
        ]
    )
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {"kind": REPORT_NAME, "version": "v1"},
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "atlas_files": atlas_count,
            "source_snapshots": snapshot_count,
            "status_counts": status_counts,
            "contract_errors": contract_errors,
            "project_scope": scope,
        },
        "validation_contract": validation_contract,
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / f"{REPORT_NAME}.json", payload)
    save_text_atomic(REPORTS_DIR / f"{REPORT_NAME}.md", render_report(payload))
    _log(
        "PASS validation "
        f"status={payload['summary']['status']} snapshots={snapshot_count} ok={ok_count}"
    )
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Source Snapshot Store Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- atlas_files: `{summary.get('atlas_files')}`",
        f"- source_snapshots: `{summary.get('source_snapshots')}`",
        f"- analyzed_projects: `{', '.join((summary.get('project_scope') or {}).get('analyzed_projects') or [])}`",
        f"- preserved_only_projects: `{', '.join((summary.get('project_scope') or {}).get('preserved_only_projects') or [])}`",
        "",
        "## Status Counts",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for status, count in (summary.get("status_counts") or {}).items():
        lines.append(f"| `{status}` | {count} |")
    lines.extend(["", "## Checks", "", "| Check | Passed | Details |", "|---|---|---|"])
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` | `{details}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the SQLite source snapshot projection.")
    parser.add_argument(
        "--projects",
        default="",
        help="Comma-separated runtime project keys eligible for this validation.",
    )
    args = parser.parse_args()
    projects = [item.strip().upper() for item in args.projects.split(",") if item.strip()]
    payload = validate_source_snapshot_store(projects or None)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

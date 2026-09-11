from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parents[1]
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, DYNAMIC_CONFIG, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.artifact_store import STORE, flush_shadow_writes
from tools.core.operational_limits import artifact_shadow_flush_timeout_seconds


REPORT_PATH = RAW_DIR / "sqlite_artifact_parity_validation.json"
REPORT_MD_PATH = REPORTS_DIR / "sqlite_artifact_parity_validation.md"
DB_PATH = RAW_DIR / "codemaps.db"
ATLAS_PATH = RAW_DIR / "atlas.json"


def _project_items(atlas: dict[str, Any]):
    for project_key, project_payload in atlas.items():
        if project_key == "symbols" or not isinstance(project_payload, dict):
            continue
        yield str(project_key), project_payload


def _atlas_counts(atlas: dict[str, Any]) -> dict[str, int]:
    projects = 0
    files = 0
    symbols = 0
    dependencies = 0
    file_keys: set[str] = set()

    for project_key, project_payload in _project_items(atlas):
        projects += 1
        files_payload = project_payload.get("files", {})
        if isinstance(files_payload, dict):
            files += len(files_payload)
            for rel_path in files_payload:
                file_keys.add(f"{project_key}::{rel_path}")

        files_payload = project_payload.get("files", {})
        if isinstance(files_payload, dict):
            symbols += sum(
                len(file_data.get("symbols", []) or [])
                for file_data in files_payload.values()
                if isinstance(file_data, dict)
            )

    for project_key, project_payload in _project_items(atlas):
        deps_payload = project_payload.get("dependencies", {})
        if not isinstance(deps_payload, dict):
            continue
        for source_rel, targets in deps_payload.items():
            if not isinstance(targets, list):
                continue
            source_key = f"{project_key}::{source_rel}"
            if source_key not in file_keys:
                continue
            for target_rel in targets:
                if not target_rel:
                    continue
                target_text = str(target_rel)
                target_key = target_text if "::" in target_text else f"{project_key}::{target_text}"
                if target_key in file_keys:
                    dependencies += 1

    return {
        "projects": projects,
        "files": files,
        "symbols": symbols,
        "dependencies": dependencies,
    }


def _db_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        return {
            "state_payloads": int(conn.execute("SELECT COUNT(*) FROM state_payloads;").fetchone()[0]),
            "projects": int(conn.execute("SELECT COUNT(*) FROM projects;").fetchone()[0]),
            "files": int(conn.execute("SELECT COUNT(*) FROM files;").fetchone()[0]),
            "symbols": int(conn.execute("SELECT COUNT(*) FROM symbols;").fetchone()[0]),
            "dependencies": int(conn.execute("SELECT COUNT(*) FROM dependencies;").fetchone()[0]),
        }


def _db_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table});").fetchall()}


def _state_payload(db_path: Path, name: str) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT payload, payload_sha, source_mtime FROM state_payloads WHERE name = ?;",
            (name,),
        ).fetchone()
    if not row:
        return None
    return {
        "payload": json.loads(row["payload"]),
        "payload_sha": row["payload_sha"],
        "source_mtime": row["source_mtime"],
    }


def _project_items_for_sample(atlas: dict[str, Any], preferred_project: str | None = None):
    if preferred_project and isinstance(atlas.get(preferred_project), dict):
        yield preferred_project, atlas[preferred_project]
        return
    yield from _project_items(atlas)


def _sample_lookup(atlas: dict[str, Any], db_path: Path, *, preferred_project: str | None = None) -> dict[str, Any]:
    sample: dict[str, Any] = {"status": "skipped", "reason": "no symbol-bearing file found"}
    candidate: tuple[str, str] | None = None
    expected_symbols: set[str] = set()

    for project_key, project_payload in _project_items_for_sample(atlas, preferred_project):
        by_file: dict[str, set[str]] = {}
        files_payload = project_payload.get("files", {})
        if not isinstance(files_payload, dict):
            continue
        for rel_path, file_info in files_payload.items():
            if not isinstance(file_info, dict):
                continue
            for symbol_info in file_info.get("symbols", []) or []:
                if not isinstance(symbol_info, dict):
                    continue
                symbol_name = str(symbol_info.get("name") or "").strip()
                if not symbol_name:
                    continue
                by_file.setdefault(str(rel_path), set()).add(symbol_name)
        if by_file:
            rel_path, names = max(by_file.items(), key=lambda item: len(item[1]))
            candidate = (project_key, rel_path)
            expected_symbols = names
            break

    if candidate is None:
        for project_key, project_payload in _project_items_for_sample(atlas, preferred_project):
            symbols_payload = project_payload.get("symbols", [])
            if not isinstance(symbols_payload, list):
                continue
            by_file: dict[str, set[str]] = {}
            for symbol_info in symbols_payload:
                if not isinstance(symbol_info, dict):
                    continue
                symbol_name = str(symbol_info.get("name") or "").strip()
                if not symbol_name:
                    continue
                rel_path = symbol_info.get("file")
                if not rel_path:
                    continue
                by_file.setdefault(str(rel_path), set()).add(str(symbol_name))
            if by_file:
                rel_path, names = max(by_file.items(), key=lambda item: len(item[1]))
                candidate = (project_key, rel_path)
                expected_symbols = names
                break

    if candidate is None:
        return sample

    project_key, rel_path = candidate
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT symbols.name
            FROM symbols
            JOIN files ON symbols.file_id = files.file_id
            WHERE files.project_key = ? AND files.rel_path = ?;
            """,
            (project_key, rel_path),
        ).fetchall()
    actual_symbols = {str(row[0]) for row in rows}
    missing = sorted(expected_symbols - actual_symbols)[:25]
    extra = sorted(actual_symbols - expected_symbols)[:25]
    return {
        "status": "pass" if not missing else "fail",
        "project": project_key,
        "rel_path": rel_path,
        "expected_symbol_count": len(expected_symbols),
        "actual_symbol_count": len(actual_symbols),
        "missing_symbols": missing,
        "extra_symbols": extra,
    }


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    sqlite_enabled = bool(DYNAMIC_CONFIG.get("use_sqlite", False))
    if sqlite_enabled:
        STORE.initialize_schema()
    flush_shadow_writes(timeout=float(artifact_shadow_flush_timeout_seconds()))
    atlas = STORE.load_raw("atlas", {})
    shadow_atlas = load_json_file(ATLAS_PATH, {}, bypass_proxy=True)

    checks.append({
        "name": "sqlite_feature_flag_enabled",
        "passed": sqlite_enabled,
        "details": f"use_sqlite={sqlite_enabled}",
    })
    checks.append({
        "name": "sqlite_database_exists",
        "passed": DB_PATH.exists(),
        "details": str(DB_PATH),
    })
    checks.append({
        "name": "atlas_json_is_full_payload",
        "passed": isinstance(shadow_atlas, dict) and not shadow_atlas.get("placeholder") and len(shadow_atlas) > 0,
        "details": f"shadow_atlas_keys={len(shadow_atlas) if isinstance(shadow_atlas, dict) else 0}",
    })
    checks.append({
        "name": "atlas_state_payload_is_full_payload",
        "passed": isinstance(atlas, dict) and not atlas.get("placeholder") and len(atlas) > 0,
        "details": f"state_payload_atlas_keys={len(atlas) if isinstance(atlas, dict) else 0}",
    })
    checks.append({
        "name": "atlas_shadow_json_matches_state_payload_keys",
        "passed": isinstance(atlas, dict)
        and isinstance(shadow_atlas, dict)
        and set(atlas.keys()) == set(shadow_atlas.keys()),
        "details": (
            f"state_keys={len(atlas) if isinstance(atlas, dict) else 0} "
            f"shadow_keys={len(shadow_atlas) if isinstance(shadow_atlas, dict) else 0}"
        ),
    })

    atlas_counts = _atlas_counts(atlas) if isinstance(atlas, dict) else {}
    db_counts = _db_counts(DB_PATH) if DB_PATH.exists() else {}
    checks.append({
        "name": "state_payloads_present",
        "passed": db_counts.get("state_payloads", 0) > 0,
        "details": f"state_payloads={db_counts.get('state_payloads')}",
    })
    state_columns = _db_columns(DB_PATH, "state_payloads") if DB_PATH.exists() else set()
    checks.append({
        "name": "state_payloads_freshness_columns_present",
        "passed": {"payload_sha", "source_mtime", "updated_at"}.issubset(state_columns),
        "details": sorted(state_columns),
    })
    STORE.load_raw("quality_gate", {})
    quality_gate_shadow = load_json_file(RAW_DIR / "quality_gate.json", {}, bypass_proxy=True)
    quality_gate_state = _state_payload(DB_PATH, "quality_gate") if DB_PATH.exists() else None
    checks.append({
        "name": "quality_gate_shadow_json_matches_state_payload",
        "passed": isinstance(quality_gate_state, dict)
        and isinstance(quality_gate_shadow, dict)
        and quality_gate_state.get("payload") == quality_gate_shadow,
        "details": (
            f"shadow_status={quality_gate_shadow.get('release_gate_status') if isinstance(quality_gate_shadow, dict) else None} "
            f"state_status={quality_gate_state.get('payload', {}).get('release_gate_status') if isinstance(quality_gate_state, dict) and isinstance(quality_gate_state.get('payload'), dict) else None}"
        ),
    })

    for key in ("projects", "files", "symbols", "dependencies"):
        expected = atlas_counts.get(key)
        actual = db_counts.get(key)
        checks.append({
            "name": f"{key}_count_parity",
            "passed": expected == actual,
            "details": f"json={expected} sqlite={actual}",
        })

    sample = _sample_lookup(atlas, DB_PATH) if isinstance(atlas, dict) and DB_PATH.exists() else {"status": "skipped"}
    main_sample = (
        _sample_lookup(atlas, DB_PATH, preferred_project="MAIN")
        if isinstance(atlas, dict) and DB_PATH.exists()
        else {"status": "skipped"}
    )
    checks.append({
        "name": "sample_symbol_lookup_parity",
        "passed": sample.get("status") == "pass",
        "details": json.dumps(sample, ensure_ascii=False),
    })
    checks.append({
        "name": "main_project_sample_symbol_lookup_parity",
        "passed": main_sample.get("status") == "pass",
        "details": json.dumps(main_sample, ensure_ascii=False),
    })

    failed = [check for check in checks if not check["passed"]]
    report = {
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "backend": STORE.backend,
        "artifact_paths": {
            "atlas": str(ATLAS_PATH),
            "database": str(DB_PATH),
        },
        "json_counts": atlas_counts,
        "sqlite_counts": db_counts,
        "sample_lookup": sample,
        "main_sample_lookup": main_sample,
        "checks": checks,
    }
    save_json_atomic(REPORT_PATH, report, indent=2)
    save_text_atomic(REPORT_MD_PATH, render_report(report))
    return report


def render_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    lines = [
        "# SQLite Artifact Parity Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- total_checks: `{summary.get('total_checks')}`",
        f"- passed_checks: `{summary.get('passed_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        f"- backend: `{report.get('backend')}`",
        "",
        "## Counts",
        "",
        "| Metric | Atlas JSON | SQLite |",
        "|---|---:|---:|",
    ]
    json_counts = report.get("json_counts", {}) if isinstance(report.get("json_counts"), dict) else {}
    sqlite_counts = report.get("sqlite_counts", {}) if isinstance(report.get("sqlite_counts"), dict) else {}
    for metric in ("projects", "files", "symbols", "dependencies"):
        lines.append(f"| `{metric}` | `{json_counts.get(metric)}` | `{sqlite_counts.get(metric)}` |")
    lines.extend(["", "## Checks", "", "| Check | Passed | Details |", "|---|---:|---|"])
    for check in report.get("checks", []) if isinstance(report.get("checks"), list) else []:
        if not isinstance(check, dict):
            continue
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` | {check.get('details')} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    report = run_validation()
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

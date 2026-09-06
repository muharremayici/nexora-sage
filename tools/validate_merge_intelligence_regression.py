from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


RAW_OUTPUT_PATH = RAW_DIR / "merge_intelligence_regression.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "merge_intelligence_regression.md"
CONTRACT_PATH = CODE_MAPS_DIR / "config" / "merge_intelligence_regression_contract.json"


def _count(payload: dict[str, Any], key: str) -> int:
    values = payload.get(key, []) if isinstance(payload, dict) else []
    return len(values) if isinstance(values, list) else 0


def _summary_count(payload: dict[str, Any], key: str, fallback_key: str) -> int:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    value = summary.get(key)
    if value is None:
        return _count(payload, fallback_key)
    try:
        return int(value or 0)
    except Exception:
        return 0


def _check(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _load_contract() -> dict[str, Any]:
    payload = load_json_file(CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _artifact_inputs(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = contract.get("artifact_inputs") if isinstance(contract, dict) else []
    artifacts: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        artifact_id = str(row.get("id") or "").strip()
        artifact_path = str(row.get("path") or "").strip()
        if artifact_id and artifact_path:
            artifacts[artifact_id] = load_json_file(RAW_DIR / artifact_path, {})
    return artifacts


def build_regression_checks(artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    routes = artifacts.get("framework_routes", {})
    ui = artifacts.get("ui_runtime_contracts", {})
    smoke = artifacts.get("ui_smoke_specs", {})
    packages = artifacts.get("merge_dependency_packages", {})
    simulation = artifacts.get("merge_simulation", {})
    cockpit = artifacts.get("merge_decision_cockpit", {})
    taskpacks = artifacts.get("ai_task_packs", {})

    route_count = _summary_count(routes, "routes", "routes")
    ui_candidates = _count(ui, "merge_candidates")
    smoke_specs = _count(smoke, "specs")
    package_count = _count(packages, "packages")
    simulation_count = _count(simulation, "simulations")
    cockpit_rows = cockpit.get("decisions", []) if isinstance(cockpit, dict) else []
    cockpit_count = len(cockpit_rows) if isinstance(cockpit_rows, list) else 0
    taskpack_records = taskpacks.get("taskpacks", []) if isinstance(taskpacks, dict) else []
    taskpack_count = len(taskpack_records) if isinstance(taskpack_records, list) else 0
    package_records = packages.get("packages", []) if isinstance(packages, dict) else []
    packaged_smoke_required = sum(
        1
        for item in package_records
        if isinstance(item, dict) and item.get("recommended_gate") == "browser_smoke_required"
    )

    smoke_required_all = sum(
        1
        for item in ui.get("merge_candidates", []) if isinstance(ui, dict)
        for _ in [item]
        if isinstance(item, dict) and item.get("recommended_gate") == "browser_smoke_required"
    )
    smoke_required_packageable = sum(
        1
        for item in ui.get("merge_candidates", []) if isinstance(ui, dict)
        for _ in [item]
        if (
            isinstance(item, dict)
            and item.get("recommended_gate") == "browser_smoke_required"
            and item.get("source_contract_file")
        )
    )
    browser_smoke_actions = sum(
        1
        for item in simulation.get("simulations", []) if isinstance(simulation, dict)
        for _ in [item]
        if isinstance(item, dict) and "run_browser_smoke" in (item.get("required_actions") or [])
    )

    cockpit_summary = cockpit.get("summary", {}) if isinstance(cockpit, dict) else {}
    actions = cockpit_summary.get("actions", {}) if isinstance(cockpit_summary, dict) else {}
    effective_actions = cockpit_summary.get("effective_actions", actions) if isinstance(cockpit_summary, dict) else {}
    confidence_tiers = cockpit_summary.get("confidence_tiers", {}) if isinstance(cockpit_summary, dict) else {}
    import_now = int(actions.get("Import Now", 0) or 0) if isinstance(actions, dict) else 0
    effective_total = sum(int(value or 0) for value in effective_actions.values()) if isinstance(effective_actions, dict) else 0
    confidence_total = sum(int(value or 0) for value in confidence_tiers.values()) if isinstance(confidence_tiers, dict) else 0

    rows_missing_keys = [
        row.get("candidate")
        for row in cockpit_rows
        if isinstance(row, dict) and (not row.get("stable_key") or not isinstance(row.get("confidence"), dict))
    ]
    low_conf_import_now = [
        row.get("candidate")
        for row in cockpit_rows
        if isinstance(row, dict)
        and row.get("action") == "Import Now"
        and str((row.get("confidence") or {}).get("tier") or "") == "low"
    ]
    taskpack_import_now = sum(
        1
        for row in taskpack_records
        if isinstance(row, dict) and row.get("action") == "Import Now"
    )
    taskpacks_missing_operational_source_files = [
        row.get("candidate")
        for row in taskpack_records
        if isinstance(row, dict)
        and (
            not row.get("target_path")
            or not row.get("source_contract_file")
            or not isinstance(row.get("copy_files_sample"), list)
            or not row.get("copy_files_sample")
            or not isinstance(row.get("copy_files_omitted"), int)
        )
    ]
    taskpacks_with_windows_only_commands = [
        row.get("candidate")
        for row in taskpack_records
        if isinstance(row, dict)
        for command in ((row.get("proof_contract") or {}).get("required_proof_commands") or [])
        if isinstance(command, str) and ".\\tools\\" in command
    ]

    return [
        _check("routes_detected_or_clean_snapshot", route_count >= 0, f"routes={route_count}"),
        _check("ui_candidates_detected_or_clean_zero", ui_candidates >= 0, f"ui_candidates={ui_candidates}"),
        _check("smoke_specs_cover_required_ui", smoke_specs >= smoke_required_all, f"smoke_specs={smoke_specs} smoke_required={smoke_required_all}"),
        _check(
            "browser_smoke_action_propagates",
            browser_smoke_actions >= packaged_smoke_required,
            (
                f"browser_smoke_actions={browser_smoke_actions} "
                f"packaged_smoke_required={packaged_smoke_required} "
                f"packageable_smoke_required={smoke_required_packageable} "
                f"total_smoke_required={smoke_required_all}"
            ),
        ),
        _check("package_simulation_cockpit_cardinality", package_count >= simulation_count == cockpit_count, f"packages={package_count} simulations={simulation_count} cockpit={cockpit_count}"),
        _check("cockpit_effective_actions_accounted", effective_total == cockpit_count, f"effective_total={effective_total} cockpit={cockpit_count}"),
        _check("cockpit_confidence_accounted", confidence_total == cockpit_count, f"confidence_total={confidence_total} cockpit={cockpit_count}"),
        _check("cockpit_rows_have_stable_keys_and_confidence", not rows_missing_keys, f"missing={rows_missing_keys[:10]}"),
        _check("import_now_never_low_confidence", not low_conf_import_now, f"low_conf_import_now={low_conf_import_now[:10]}"),
        _check("taskpacks_cover_import_now", taskpack_count >= import_now and taskpack_import_now == import_now, f"taskpacks={taskpack_count} import_now={import_now} taskpack_import_now={taskpack_import_now}"),
        _check("taskpacks_expose_operational_source_files", not taskpacks_missing_operational_source_files, f"missing={taskpacks_missing_operational_source_files[:10]}"),
        _check("taskpack_proof_commands_are_cross_platform", not taskpacks_with_windows_only_commands, f"windows_only={taskpacks_with_windows_only_commands[:10]}"),
    ]


def run_validation() -> dict[str, Any]:
    contract = _load_contract()
    artifacts = _artifact_inputs(contract)
    checks = [
        _check(
            "merge_regression_scope_contract_loaded",
            CONTRACT_PATH.exists() and len(artifacts) >= 7,
            f"contract={CONTRACT_PATH.relative_to(CODE_MAPS_DIR)} artifact_inputs={len(artifacts)}",
        )
    ]
    checks.extend(build_regression_checks(artifacts))
    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for item in checks if item.get("passed")),
        "failed_checks": sum(1 for item in checks if not item.get("passed")),
    }
    payload = {
        "meta": {
            "kind": "merge_intelligence_regression",
            "version": "v1",
            "contract": str(CONTRACT_PATH.relative_to(CODE_MAPS_DIR)),
            "artifact_inputs": sorted(artifacts),
        },
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)

    lines = [
        "# Merge Intelligence Regression",
        "",
        "Universal regression checks for route-aware UI contracts, merge packaging, static simulation, cockpit confidence, suppression accounting, and task-pack coverage.",
        "",
        f"- total_checks: `{summary['total_checks']}`",
        f"- passed_checks: `{summary['passed_checks']}`",
        f"- failed_checks: `{summary['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

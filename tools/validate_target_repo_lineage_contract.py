from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.artifact_registry import ARTIFACT_SCHEMAS
from tools.core.release_proof_steps import load_release_proof_command_names, load_release_proof_step_ids


CONTRACT_PATH = CODE_MAPS_DIR / "config" / "target_repo_lineage_contract.json"
PIPELINE_POLICY_PATH = CODE_MAPS_DIR / "config" / "pipeline_execution_policy.json"
RAW_OUTPUT_PATH = RAW_DIR / "target_repo_lineage_contract_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "target_repo_lineage_contract_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _load_contract() -> dict[str, Any]:
    try:
        payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_pipeline_policy() -> dict[str, Any]:
    try:
        payload = json.loads(PIPELINE_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _check(name: str, passed: bool, details: Any, *, severity: str = "error") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "severity": severity,
        "details": details,
    }


def _marker_present(marker: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    rel = str(marker.get("file") or "")
    needle = str(marker.get("contains") or "")
    path = (CODE_MAPS_DIR / rel).resolve()
    try:
        path.relative_to(CODE_MAPS_DIR.resolve())
    except ValueError:
        return False, {"file": rel, "contains": needle, "error": "path escapes CODE_MAPS_DIR"}
    text = _read_text(path)
    return bool(path.exists() and needle and needle in text), {
        "file": rel,
        "contains": needle,
        "exists": path.exists(),
    }


def _lineage_checks(contract: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    lineage = contract.get("target_fact_lineage", [])
    checks.append(
        _check(
            "target_fact_lineage_declared",
            isinstance(lineage, list) and len(lineage) >= 5,
            {"declared_count": len(lineage) if isinstance(lineage, list) else 0},
        )
    )
    for row in lineage if isinstance(lineage, list) else []:
        if not isinstance(row, dict):
            continue
        field_id = str(row.get("id") or "unknown")
        required_fields = row.get("required_agent_fields", [])
        checks.append(
            _check(
                f"{field_id}:required_agent_fields_declared",
                isinstance(required_fields, list) and bool(required_fields),
                required_fields,
            )
        )
        for group_name in ("producer_markers", "consumer_markers"):
            markers = [marker for marker in row.get(group_name, []) or [] if isinstance(marker, dict)]
            marker_results = []
            for marker in markers:
                present, evidence = _marker_present(marker)
                marker_results.append({"passed": present, **evidence})
            checks.append(
                _check(
                    f"{field_id}:{group_name}_present",
                    bool(markers) and all(item.get("passed") for item in marker_results),
                    marker_results,
                )
            )
    return checks


def build_validation() -> dict[str, Any]:
    contract = _load_contract()
    pipeline_policy = _load_pipeline_policy()
    default_policy = contract.get("default_policy", {}) if isinstance(contract.get("default_policy"), dict) else {}
    project_scope_policy = (
        pipeline_policy.get("project_scope_policy", {})
        if isinstance(pipeline_policy.get("project_scope_policy"), dict)
        else {}
    )
    claim_boundary = contract.get("claim_boundary", {}) if isinstance(contract.get("claim_boundary"), dict) else {}
    agent_surface_tools = contract.get("agent_surface_tools", [])
    server_text = _read_text(TOOLS_DIR / "mcp" / "server.py")
    release_proof_step_ids = load_release_proof_step_ids()
    release_proof_command_names = load_release_proof_command_names()

    checks: list[dict[str, Any]] = [
        _check(
            "contract_file_exists",
            CONTRACT_PATH.exists() and bool(contract),
            {"path": CONTRACT_PATH.relative_to(CODE_MAPS_DIR).as_posix()},
        ),
        _check(
            "target_policy_is_fail_closed_and_sqlite_first",
            default_policy.get("target_truth_preference") == "sqlite_source_snapshots_first"
            and default_policy.get("missing_target_behavior") == "fail_closed_agent_surface"
            and default_policy.get("drift_behavior") == "block_edit_directive_when_mismatch",
            default_policy,
        ),
        _check(
            "variation_visibility_is_main_first",
            default_policy.get("default_agent_edit_scope") == "MAIN"
            and default_policy.get("default_agent_read_scope") == "MAIN"
            and default_policy.get("target_repo_agent_default") == "host_projects_only"
            and default_policy.get("variation_visibility") == "explicit_only"
            and default_policy.get("companion_visibility") == "explicit_only",
            default_policy,
        ),
        _check(
            "project_scope_policy_matches_target_lineage_contract",
            all(
                default_policy.get(key) == project_scope_policy.get(key)
                for key in (
                    "default_agent_edit_scope",
                    "default_agent_read_scope",
                    "target_repo_agent_default",
                    "variation_visibility",
                    "companion_visibility",
                    "external_target_scope",
                )
            ),
            {
                "target_lineage_policy": {
                    key: default_policy.get(key)
                    for key in (
                        "default_agent_edit_scope",
                        "default_agent_read_scope",
                        "target_repo_agent_default",
                        "variation_visibility",
                        "companion_visibility",
                        "external_target_scope",
                    )
                },
                "pipeline_project_scope_policy": {
                    key: project_scope_policy.get(key)
                    for key in (
                        "default_agent_edit_scope",
                        "default_agent_read_scope",
                        "target_repo_agent_default",
                        "variation_visibility",
                        "companion_visibility",
                        "external_target_scope",
                    )
                },
            },
        ),
        _check(
            "claim_boundary_is_explicit",
            claim_boundary.get("v1_scope") == "target_repo_static_architecture_governance_and_agent_context_grounding"
            and "full_security_sast" in (claim_boundary.get("not_claimed") or []),
            claim_boundary,
        ),
        _check(
            "agent_surface_tools_exist",
            isinstance(agent_surface_tools, list)
            and bool(agent_surface_tools)
            and all(f"def {tool_name}(" in server_text for tool_name in agent_surface_tools),
            agent_surface_tools,
        ),
        _check(
            "artifact_validator_maps_target_lineage_validation",
            "target_repo_lineage_contract_validation" in ARTIFACT_SCHEMAS,
            {
                "artifact_id": "target_repo_lineage_contract_validation",
                "schema": str(ARTIFACT_SCHEMAS.get("target_repo_lineage_contract_validation", "")),
            },
        ),
        _check(
            "release_proof_gates_target_lineage_validation",
            "target_repo_lineage_contract" in release_proof_step_ids
            and "validate_target_repo_lineage_contract.py" in release_proof_command_names,
            "target_repo_lineage_contract must be a release proof step",
        ),
    ]
    checks.extend(_lineage_checks(contract))

    failures = [row for row in checks if not row.get("passed") and row.get("severity") == "error"]
    warnings = [row for row in checks if not row.get("passed") and row.get("severity") == "warning"]
    return {
        "meta": {
            "kind": "target_repo_lineage_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_target_repo_lineage_contract",
        },
        "summary": {
            "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
            "checks": len(checks),
            "passed": sum(1 for row in checks if row.get("passed")),
            "failed": len(failures),
            "warnings": len(warnings),
        },
        "checks": checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Target Repo Lineage Contract Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('checks')}`",
        f"- failed: `{summary.get('failed')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Severity | Details |",
        "|---|---|---|---|",
    ]
    for check in payload.get("checks", []) or []:
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        details = details.replace("\n", " ")[:600]
        escaped_details = details.replace("|", "\\|")
        lines.append(
            f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | "
            f"`{check.get('severity')}` | {escaped_details} |"
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
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

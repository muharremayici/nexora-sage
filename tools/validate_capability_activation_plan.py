from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.capability_registry import capability_ids, load_capability_registry, summarize_capabilities
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.engines.capability_activation_planner import build_capability_activation_plan


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _activation_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project in payload.get("projects", []):
        if not isinstance(project, dict):
            continue
        for key in ("enabled_capabilities", "disabled_capabilities"):
            for row in project.get(key, []):
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def validate_capability_activation_plan() -> dict[str, Any]:
    payload = build_capability_activation_plan()
    dna = load_json_file(RAW_DIR / "project_dna_profile.json", {})
    registry = load_capability_registry()
    ids = capability_ids(registry)
    capability_summary = summarize_capabilities(registry)
    generic_production_ids = {
        str(item.get("id"))
        for item in capability_summary.get("capabilities", [])
        if item.get("maturity") == "production_candidate"
        and "language_agnostic" in (item.get("language_scope") or [])
    }
    rows = _activation_rows(payload)
    row_ids = {str(row.get("id")) for row in rows if row.get("id")}
    roadmap_active_claims = [
        str(row.get("id"))
        for row in rows
        if row.get("maturity") == "roadmap"
        and row.get("status") == "planned"
        and "not active" not in str(row.get("claim_boundary") or "").lower()
    ]
    generic_production_disabled = [
        str(row.get("id"))
        for project in payload.get("projects", [])
        if isinstance(project, dict)
        for row in project.get("disabled_capabilities", [])
        if isinstance(row, dict) and str(row.get("id")) in generic_production_ids
    ]
    source = (ROOT / "tools" / "engines" / "capability_activation_planner.py").read_text(encoding="utf-8")
    orchestrator_source = (ROOT / "tools" / "orchestrators" / "orchestrator.py").read_text(encoding="utf-8")
    project_rows = [project for project in payload.get("projects", []) if isinstance(project, dict)]
    project_ids = {str(project.get("project")) for project in project_rows if project.get("project")}

    checks = [
        _check(
            "activation_plan_has_stable_contract",
            payload.get("meta", {}).get("kind") == "capability_activation_plan"
            and payload.get("summary", {}).get("status") == "PASS"
            and bool(project_rows)
            and "MAIN" in project_ids
            and int(payload.get("summary", {}).get("projects") or 0) == len(project_rows)
            and int(payload.get("summary", {}).get("capabilities") or 0) > 0,
            {"summary": payload.get("summary", {}), "projects": sorted(project_ids)},
        ),
        _check(
            "activation_plan_is_scheduler_input",
            payload.get("meta", {}).get("scope") == "scheduler_input"
            and payload.get("summary", {}).get("planning_only") is False
            and payload.get("summary", {}).get("pipeline_scheduler_enforced") is True
            and payload.get("summary", {}).get("scheduler_contract") == "capability_filter_v1",
            payload.get("summary", {}),
        ),
        _check(
            "orchestrator_consumes_capability_activation_plan",
            "apply_capability_activation" in orchestrator_source
            and "build_capability_activation_plan" in orchestrator_source
            and "activation_plan_requires_refresh" in orchestrator_source
            and "Activation plan is empty or invalid" in orchestrator_source
            and "release-deep" in orchestrator_source,
            {
                "filter": "apply_capability_activation",
                "release_bypass": "release-deep",
                "empty_plan_policy": "preserve_selected_pipeline",
                "manifest_change_policy": "refresh_before_filter",
            },
        ),
        _check(
            "activation_plan_consumes_project_dna_profile",
            dna.get("meta", {}).get("kind") == "project_dna_profile"
            and "project_dna_profile.json" in source,
            {"dna_kind": dna.get("meta", {}).get("kind")},
        ),
        _check(
            "activation_capabilities_resolve_to_registry",
            row_ids <= ids,
            {"unknown_capabilities": sorted(row_ids - ids)},
        ),
        _check(
            "language_agnostic_production_baseline_is_not_disabled",
            not generic_production_disabled,
            {"generic_production_disabled": sorted(set(generic_production_disabled))},
        ),
        _check(
            "roadmap_planned_rows_remain_non_claimable",
            not roadmap_active_claims,
            {"roadmap_active_claims": sorted(set(roadmap_active_claims))},
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    result = {
        "meta": {
            "kind": "capability_activation_plan_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "validator": "tools.validate_capability_activation_plan",
        },
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "capability_activation_plan.json", payload)
    save_json_atomic(RAW_DIR / "capability_activation_plan_validation.json", result)
    save_text_atomic(REPORTS_DIR / "capability_activation_plan.md", _render_plan(payload))
    save_text_atomic(REPORTS_DIR / "capability_activation_plan_validation.md", _render_validation(result))
    return result


def _render_plan(payload: dict[str, Any]) -> str:
    lines = [
        "# Capability Activation Plan",
        "",
        "| Project | Enabled/Planned | Disabled |",
        "|---|---:|---:|",
    ]
    for project in payload.get("projects", []):
        if not isinstance(project, dict):
            continue
        lines.append(
            f"| `{project.get('project')}` | {len(project.get('enabled_capabilities', []) or [])} | {len(project.get('disabled_capabilities', []) or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_validation(result: dict[str, Any]) -> str:
    lines = [
        "# Capability Activation Plan Validation",
        "",
        f"- status: `{result.get('summary', {}).get('status')}`",
        f"- passed: `{result.get('summary', {}).get('passed')}/{result.get('summary', {}).get('checks')}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in result.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    result = validate_capability_activation_plan()
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0 if result["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

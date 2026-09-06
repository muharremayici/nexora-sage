from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.lesson_projection import project_impacted_lessons


LOOP_PATH = CODE_MAPS_DIR / "config" / "sage_development_loop_contract.json"
LESSON_PATH = CODE_MAPS_DIR / "config" / "audit_lesson_registry.json"


def _contract(loop: dict) -> dict:
    review = loop.get("semantic_diff_review", {}) if isinstance(loop, dict) else {}
    levels = review.get("lesson_application_levels", {}) if isinstance(review, dict) else {}
    impacted = levels.get("impacted", {}) if isinstance(levels, dict) else {}
    value = impacted.get("projection_contract", {}) if isinstance(impacted, dict) else {}
    return value if isinstance(value, dict) else {}


def validate_semantic_diff_lesson_projection() -> dict:
    loop = json.loads(LOOP_PATH.read_text(encoding="utf-8"))
    registry = json.loads(LESSON_PATH.read_text(encoding="utf-8"))
    lessons = [row for row in registry.get("lessons", []) if isinstance(row, dict)]
    projection = project_impacted_lessons(
        lessons,
        changed_files=["tools/mcp/server.py", "config/audit_lesson_registry.json"],
        failure_families=["audience_boundary_leak"],
        contract=_contract(loop),
    )
    ids = {str(row.get("id")) for row in projection.get("lessons", [])}
    checks = [
        {"name": "contract_is_active", "passed": loop.get("semantic_diff_review", {}).get("lesson_application_levels", {}).get("impacted", {}).get("automation_status") == "active"},
        {"name": "synthetic_projection_passes", "passed": projection.get("summary", {}).get("status") == "PASS"},
        {"name": "mcp_file_has_source_layer", "passed": any(row.get("source_layer") == "contextos_watchdog_mcp" for row in projection.get("changed_files", []))},
        {"name": "audience_boundary_lesson_selected", "passed": "agent_surface_roles_must_be_enforced_by_tool_visibility" in ids or any("audience_boundary_leak" in row.get("matched_strong_signals", []) for row in projection.get("lessons", []))},
        {"name": "explicit_failure_family_restricts_selection", "passed": bool(projection.get("lessons")) and all(row.get("risk_class") == "audience_boundary_leak" for row in projection.get("lessons", []))},
        {"name": "source_layer_ownership_has_no_unknowns", "passed": projection.get("summary", {}).get("unknown_source_layer_files") == 0},
        {"name": "projection_limits_are_explicit", "passed": projection.get("limits", {}).get("semantic_inference") is False and projection.get("limits", {}).get("full_registry_replay") is False},
    ]
    payload = {
        "meta": {"kind": "semantic_diff_lesson_projection_validation", "version": "v1"},
        "summary": {"status": "PASS" if all(row["passed"] for row in checks) else "FAIL", "checks": len(checks), "passed": sum(1 for row in checks if row["passed"])},
        "checks": checks,
        "synthetic_projection": projection,
    }
    save_json_atomic(RAW_DIR / "semantic_diff_lesson_projection_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "semantic_diff_lesson_projection_validation.md", render_report(payload))
    return payload


def render_report(payload: dict) -> str:
    summary = payload["summary"]
    lines = ["# Semantic Diff Lesson Projection Validation", "", f"- status: `{summary['status']}`", f"- checks: `{summary['passed']}/{summary['checks']}`", ""]
    for row in payload.get("checks", []):
        lines.append(f"- [{'x' if row['passed'] else ' '}] `{row['name']}`")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    result = validate_semantic_diff_lesson_projection()
    print(json.dumps(result["summary"], indent=2))
    raise SystemExit(0 if result["summary"]["status"] == "PASS" else 1)

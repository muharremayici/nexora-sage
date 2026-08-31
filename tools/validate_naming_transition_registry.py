from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


REGISTRY_PATH = CONFIG_DIR / "naming_transition_registry.json"
RAW_PATH = RAW_DIR / "naming_transition_registry_validation.json"
REPORT_PATH = REPORTS_DIR / "naming_transition_registry_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def validate_naming_transition_registry() -> dict[str, Any]:
    registry = load_json_file(REGISTRY_PATH, {})
    if not isinstance(registry, dict):
        registry = {}
    validation_contract = registry.get("validation_contract", {})
    if not isinstance(validation_contract, dict):
        validation_contract = {}
    candidates = [row for row in registry.get("candidates", []) if isinstance(row, dict)]
    ids = [str(row.get("id") or "") for row in candidates]
    duplicate_ids = sorted({item for item in ids if item and ids.count(item) > 1})
    allowed_statuses = set(registry.get("allowed_statuses") or [])
    allowed_priorities = set(registry.get("allowed_priorities") or [])
    required_fields = [
        str(item)
        for item in validation_contract.get("required_candidate_fields", [])
        if str(item or "").strip()
    ]
    pre_v1_statuses = set(validation_contract.get("pre_v1_statuses") or [])
    blocking_pre_v1_priorities = set(validation_contract.get("blocking_pre_v1_priorities") or [])
    compatibility_wrapper_status = str(validation_contract.get("compatibility_wrapper_status") or "")
    compatibility_required_terms = [
        str(item).lower()
        for item in validation_contract.get("compatibility_wrapper_strategy_required_terms", [])
        if str(item or "").strip()
    ]
    required_candidate_decisions = [
        row for row in validation_contract.get("required_candidate_decisions", []) if isinstance(row, dict)
    ]
    incomplete = [
        row.get("id")
        for row in candidates
        if any(not row.get(field) for field in required_fields)
    ]
    unknown_statuses = sorted(
        {
            str(row.get("status") or "")
            for row in candidates
            if str(row.get("status") or "") not in allowed_statuses
        }
    )
    unknown_priorities = sorted(
        {
            str(row.get("priority") or "")
            for row in candidates
            if str(row.get("priority") or "") not in allowed_priorities
        }
    )
    wrappers_missing_required_terms = [
        row.get("id")
        for row in candidates
        if compatibility_wrapper_status
        and row.get("status") == compatibility_wrapper_status
        and not all(term in str(row.get("strategy") or "").lower() for term in compatibility_required_terms)
    ]
    pre_v1_decisions = [
        row
        for row in candidates
        if row.get("status") in pre_v1_statuses
    ]
    p0_pre_v1 = [
        row.get("id")
        for row in pre_v1_decisions
        if row.get("priority") in blocking_pre_v1_priorities
    ]
    candidates_by_id = {str(row.get("id") or ""): row for row in candidates if str(row.get("id") or "")}

    checks = [
        _check(
            "registry_file_exists",
            REGISTRY_PATH.exists() and registry.get("meta", {}).get("kind") == "naming_transition_registry",
            str(REGISTRY_PATH),
        ),
        _check("candidate_ids_are_unique", not duplicate_ids, duplicate_ids),
        _check("candidates_have_required_fields", not incomplete, incomplete),
        _check("candidate_statuses_are_known", not unknown_statuses, unknown_statuses),
        _check("candidate_priorities_are_known", not unknown_priorities, unknown_priorities),
        _check("compatibility_wrappers_declare_required_terms", not wrappers_missing_required_terms, wrappers_missing_required_terms),
        _check("pre_v1_decisions_are_visible", bool(p0_pre_v1), p0_pre_v1),
    ]
    for decision in required_candidate_decisions:
        candidate_id = str(decision.get("id") or "")
        allowed_decision_statuses = set(decision.get("allowed_statuses") or [])
        candidate = candidates_by_id.get(candidate_id, {})
        checks.append(
            _check(
                str(decision.get("check_name") or f"required_candidate_decision:{candidate_id}"),
                bool(candidate) and candidate.get("status") in allowed_decision_statuses,
                candidate or str(decision.get("details") or candidate_id),
            )
        )
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {
            "kind": "naming_transition_registry_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_naming_transition_registry",
        },
        "summary": {
            "status": status,
            "candidates": len(candidates),
            "pre_v1_decisions": len(pre_v1_decisions),
            "p0_pre_v1_decisions": len(p0_pre_v1),
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
        },
        "policy": registry.get("policy", {}),
        "pre_v1_decisions": pre_v1_decisions,
        "candidates": candidates,
        "checks": checks,
    }
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Naming Transition Registry Validation",
        "",
        "Tracks naming surfaces that need direct rename, compatibility wrapping, or explicit preservation before public release.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- candidates: `{summary.get('candidates')}`",
        f"- pre_v1_decisions: `{summary.get('pre_v1_decisions')}`",
        f"- p0_pre_v1_decisions: `{summary.get('p0_pre_v1_decisions')}`",
        "",
        "## Pre-V1 Decisions",
        "",
        "| Candidate | Current | Proposed | Priority | Status | Blast Radius |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload.get("pre_v1_decisions", []):
        lines.append(
            f"| `{row.get('id')}` | `{row.get('current')}` | `{row.get('proposed')}` | "
            f"`{row.get('priority')}` | `{row.get('status')}` | `{row.get('blast_radius')}` |"
        )
    lines.extend(["", "## Checks", "", "| Check | Passed |", "|---|---|"])
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_naming_transition_registry()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

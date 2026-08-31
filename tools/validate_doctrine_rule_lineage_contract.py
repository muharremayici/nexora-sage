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
from tools.core.artifact_registry import ARTIFACT_PATHS, ARTIFACT_SCHEMAS
from tools.core.release_proof_steps import load_release_proof_command_names, load_release_proof_step_ids


CONTRACT_PATH = CODE_MAPS_DIR / "config" / "doctrine_rule_lineage_contract.json"
RAW_OUTPUT_PATH = RAW_DIR / "doctrine_rule_lineage_contract_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "doctrine_rule_lineage_contract_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
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
    lineage = contract.get("critical_rule_lineage", [])
    checks.append(
        _check(
            "critical_rule_lineage_declared",
            isinstance(lineage, list) and bool(lineage),
            {"declared_count": len(lineage) if isinstance(lineage, list) else 0},
        )
    )
    for row in lineage if isinstance(lineage, list) else []:
        if not isinstance(row, dict):
            continue
        lineage_id = str(row.get("id") or "unknown")
        required_rules = [str(rule) for rule in row.get("required_rules", []) or []]
        doctrine_text = _read_text(CODE_MAPS_DIR / "config" / "doctrines" / "governance" / "audit_rules.json")
        runtime_text = _read_text(CODE_MAPS_DIR / "config" / "architecture_doctrine.json")
        checks.append(
            _check(
                f"{lineage_id}:required_rules_present_in_pack_and_runtime",
                bool(required_rules)
                and all(rule in doctrine_text for rule in required_rules)
                and all(rule in runtime_text for rule in required_rules),
                {"required_rules": required_rules},
            )
        )
        for group_name in (
            "producer_markers",
            "runtime_markers",
            "engine_consumer_markers",
            "agent_surface_markers",
        ):
            markers = [marker for marker in row.get(group_name, []) or [] if isinstance(marker, dict)]
            marker_results = []
            for marker in markers:
                present, evidence = _marker_present(marker)
                marker_results.append({"passed": present, **evidence})
            checks.append(
                _check(
                    f"{lineage_id}:{group_name}_present",
                    bool(markers) and all(item.get("passed") for item in marker_results),
                    marker_results,
                )
            )
    return checks


def build_validation() -> dict[str, Any]:
    contract = _load_json(CONTRACT_PATH)
    policy = contract.get("default_policy", {}) if isinstance(contract.get("default_policy"), dict) else {}
    claim_boundary = contract.get("claim_boundary", {}) if isinstance(contract.get("claim_boundary"), dict) else {}
    required_directive_fields = contract.get("agent_directive_required_fields", [])
    release_proof_step_ids = load_release_proof_step_ids()
    release_proof_command_names = load_release_proof_command_names()

    checks: list[dict[str, Any]] = [
        _check(
            "contract_file_exists",
            CONTRACT_PATH.exists() and bool(contract),
            {"path": CONTRACT_PATH.relative_to(CODE_MAPS_DIR).as_posix()},
        ),
        _check(
            "policy_names_doctrine_sources",
            policy.get("doctrine_source") == "config/doctrines/manifest.json"
            and policy.get("compiled_runtime_doctrine") == "config/architecture_doctrine.json"
            and policy.get("rule_surface_behavior") == "agent_packets_must_show_rule_label_rationale_and_validation",
            policy,
        ),
        _check(
            "agent_directive_required_fields_declared",
            isinstance(required_directive_fields, list)
            and all(
                field in required_directive_fields
                for field in [
                    "rule",
                    "rule_explanation.rule",
                    "rule_explanation.label",
                    "rule_explanation.rationale",
                    "validation_commands",
                    "source_artifacts",
                ]
            ),
            required_directive_fields,
        ),
        _check(
            "claim_boundary_is_explicit",
            claim_boundary.get("v1_scope") == "active_architecture_doctrine_rules_are_carried_to_agent_repair_directives"
            and "automatic_human_seal_without_hitl" in (claim_boundary.get("not_claimed") or []),
            claim_boundary,
        ),
        _check(
            "artifact_validator_maps_doctrine_rule_lineage_validation",
            "doctrine_rule_lineage_contract_validation" in ARTIFACT_PATHS
            and "doctrine_rule_lineage_contract_validation" in ARTIFACT_SCHEMAS,
            "doctrine_rule_lineage_contract_validation must be mapped by the central artifact registry consumed by artifact_validator.py",
        ),
        _check(
            "release_proof_gates_doctrine_rule_lineage_validation",
            "doctrine_rule_lineage_contract" in release_proof_step_ids
            and "validate_doctrine_rule_lineage_contract.py" in release_proof_command_names,
            "doctrine_rule_lineage_contract must be a release proof step",
        ),
    ]
    checks.extend(_lineage_checks(contract))

    failures = [row for row in checks if not row.get("passed") and row.get("severity") == "error"]
    warnings = [row for row in checks if not row.get("passed") and row.get("severity") == "warning"]
    return {
        "meta": {
            "kind": "doctrine_rule_lineage_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_doctrine_rule_lineage_contract",
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
        "# Doctrine Rule Lineage Contract Validation",
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

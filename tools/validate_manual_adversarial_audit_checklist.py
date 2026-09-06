from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ARTIFACT_PATHS
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


CHECKLIST_PATH = CONFIG_DIR / "manual_adversarial_audit_checklist.json"
SOURCE_LAYER_TAXONOMY_PATH = CONFIG_DIR / "source_layer_taxonomy.json"
RAW_OUTPUT_PATH = RAW_DIR / "manual_adversarial_audit_checklist_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "manual_adversarial_audit_checklist_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _missing_required_fields(row: dict[str, Any], required_fields: set[str]) -> list[str]:
    missing: list[str] = []
    for field in sorted(required_fields):
        value = row.get(field)
        if field not in row or value is None or value == "" or value == []:
            missing.append(field)
    return missing


def build_validation() -> dict[str, Any]:
    checklist = load_json_file(CHECKLIST_PATH, {})
    source_taxonomy = load_json_file(SOURCE_LAYER_TAXONOMY_PATH, {})
    meta = checklist.get("_meta", {}) if isinstance(checklist, dict) else {}
    validation = checklist.get("validation", {}) if isinstance(checklist, dict) else {}
    global_invariants = checklist.get("global_invariants", []) if isinstance(checklist, dict) else []
    layers = checklist.get("audit_layers", []) if isinstance(checklist, dict) else []
    source_layer_policy = checklist.get("source_layer_coverage_policy", {}) if isinstance(checklist, dict) else {}
    micro_walkthrough_policy = checklist.get("micro_walkthrough_policy", {}) if isinstance(checklist, dict) else {}
    invariant_rows = [row for row in global_invariants if isinstance(row, dict)] if isinstance(global_invariants, list) else []
    layer_rows = [row for row in layers if isinstance(row, dict)] if isinstance(layers, list) else []
    source_policy_rows = _rows(source_layer_policy.get("layers")) if isinstance(source_layer_policy, dict) else []
    micro_scope_rows = _rows(micro_walkthrough_policy.get("scopes")) if isinstance(micro_walkthrough_policy, dict) else []
    taxonomy_rows = _rows(source_taxonomy.get("layers")) if isinstance(source_taxonomy, dict) else []

    required_layer_fields = set(_string_list(validation.get("required_layer_fields")))
    required_invariant_ids = set(_string_list(validation.get("required_invariant_ids")))
    required_source_fields = set(_string_list(validation.get("required_source_layer_coverage_fields")))
    required_micro_scopes = set(_string_list(validation.get("required_micro_walkthrough_scopes")))
    required_micro_scope_fields = set(_string_list(validation.get("required_micro_scope_fields")))
    required_pipeline_step_check_ids = set(_string_list(validation.get("required_pipeline_step_check_ids")))
    allowed_source_risk_tiers = set(_string_list(validation.get("allowed_source_layer_risk_tiers")))
    minimum_layers = int(validation.get("minimum_layers") or 10)
    minimum_adversarial_cases = int(validation.get("minimum_adversarial_cases_per_layer") or 2)
    minimum_lineage_checks = int(validation.get("minimum_lineage_checks_per_layer") or 2)
    invariant_ids = {str(row.get("id") or "") for row in invariant_rows if row.get("id")}
    layer_ids = [str(row.get("id") or "") for row in layer_rows]
    duplicate_layer_ids = sorted(item for item in set(layer_ids) if item and layer_ids.count(item) > 1)
    orders = [int(row.get("order") or 0) for row in layer_rows if str(row.get("order") or "").isdigit()]
    duplicate_orders = sorted(item for item in set(orders) if orders.count(item) > 1)
    taxonomy_layer_ids = {str(row.get("id") or "") for row in taxonomy_rows if row.get("id")}
    source_policy_ids = [str(row.get("source_layer_id") or "") for row in source_policy_rows]
    source_policy_id_set = {item for item in source_policy_ids if item}
    duplicate_source_policy_ids = sorted(item for item in set(source_policy_ids) if item and source_policy_ids.count(item) > 1)
    missing_source_policy_ids = sorted(taxonomy_layer_ids - source_policy_id_set)
    extra_source_policy_ids = sorted(source_policy_id_set - taxonomy_layer_ids)

    missing_fields: list[dict[str, Any]] = []
    unknown_invariants: list[dict[str, Any]] = []
    unknown_artifacts: list[dict[str, Any]] = []
    weak_layers: list[dict[str, Any]] = []
    missing_failure_sweeps: list[dict[str, Any]] = []
    source_policy_issues: list[dict[str, Any]] = []
    micro_policy_issues: list[dict[str, Any]] = []

    for row in layer_rows:
        layer_id = str(row.get("id") or "<missing-id>")
        missing = _missing_required_fields(row, required_layer_fields)
        if missing:
            missing_fields.append({"layer": layer_id, "missing": missing})

        for invariant in _string_list(row.get("invariants")):
            if invariant not in invariant_ids:
                unknown_invariants.append({"layer": layer_id, "invariant": invariant})

        for artifact in _string_list(row.get("evidence_artifacts")):
            if artifact not in ARTIFACT_PATHS:
                unknown_artifacts.append({"layer": layer_id, "artifact": artifact})

        lineage_checks = _string_list(row.get("lineage_checks"))
        adversarial_cases = _string_list(row.get("adversarial_cases"))
        if len(lineage_checks) < minimum_lineage_checks or len(adversarial_cases) < minimum_adversarial_cases:
            weak_layers.append(
                {
                    "layer": layer_id,
                    "lineage_checks": len(lineage_checks),
                    "adversarial_cases": len(adversarial_cases),
                }
            )

        if not _string_list(row.get("failure_family_sweeps")):
            missing_failure_sweeps.append({"layer": layer_id})

    for row in source_policy_rows:
        source_layer_id = str(row.get("source_layer_id") or "<missing-id>")
        missing = _missing_required_fields(row, required_source_fields)
        if missing:
            source_policy_issues.append({"source_layer_id": source_layer_id, "missing": missing})
        risk_tier = str(row.get("risk_tier") or "")
        if allowed_source_risk_tiers and risk_tier not in allowed_source_risk_tiers:
            source_policy_issues.append({"source_layer_id": source_layer_id, "invalid_risk_tier": risk_tier})
        unknown_macro_layers = [
            layer_id for layer_id in _string_list(row.get("macro_audit_layer_ids"))
            if layer_id not in set(layer_ids)
        ]
        if unknown_macro_layers:
            source_policy_issues.append({"source_layer_id": source_layer_id, "unknown_macro_layers": unknown_macro_layers})

    micro_scopes = [str(row.get("scope") or "") for row in micro_scope_rows]
    micro_scope_set = {scope for scope in micro_scopes if scope}
    missing_micro_scopes = sorted(required_micro_scopes - micro_scope_set)
    extra_micro_scopes = sorted(micro_scope_set - required_micro_scopes)
    duplicate_micro_scopes = sorted(scope for scope in set(micro_scopes) if scope and micro_scopes.count(scope) > 1)
    if not isinstance(micro_walkthrough_policy, dict) or not str(micro_walkthrough_policy.get("rule") or "").strip():
        micro_policy_issues.append({"missing": "micro_walkthrough_policy.rule"})
    if missing_micro_scopes or extra_micro_scopes or duplicate_micro_scopes:
        micro_policy_issues.append(
            {
                "missing_micro_scopes": missing_micro_scopes,
                "extra_micro_scopes": extra_micro_scopes,
                "duplicate_micro_scopes": duplicate_micro_scopes,
            }
        )
    for row in micro_scope_rows:
        scope = str(row.get("scope") or "<missing-scope>")
        missing = _missing_required_fields(row, required_micro_scope_fields)
        if missing:
            micro_policy_issues.append({"scope": scope, "missing": missing})
        checks = _rows(row.get("checks"))
        check_ids = {str(check.get("id") or "") for check in checks if check.get("id")}
        for field in ("order_source", "sample_policy"):
            if not str(row.get(field) or "").strip():
                micro_policy_issues.append({"scope": scope, "missing": field})
        if not checks:
            micro_policy_issues.append({"scope": scope, "missing": "checks"})
        for check in checks:
            check_id = str(check.get("id") or "<missing-id>")
            if not str(check.get("question") or "").strip():
                micro_policy_issues.append({"scope": scope, "check_id": check_id, "missing": "question"})
            if not str(check.get("expected_signal") or "").strip():
                micro_policy_issues.append({"scope": scope, "check_id": check_id, "missing": "expected_signal"})
        if scope == "pipeline_steps" and not required_pipeline_step_check_ids <= check_ids:
            micro_policy_issues.append(
                {
                    "scope": scope,
                    "missing_pipeline_step_check_ids": sorted(required_pipeline_step_check_ids - check_ids),
                    "present_check_ids": sorted(check_ids),
                }
            )
        if not _string_list(row.get("failure_family_sweeps")):
            micro_policy_issues.append({"scope": scope, "missing": "failure_family_sweeps"})
        if not _string_list(row.get("positive_pattern_sweeps")):
            micro_policy_issues.append({"scope": scope, "missing": "positive_pattern_sweeps"})
        triage_rule = str(row.get("triage_rule") or "")
        if "P0" not in triage_rule or "P1" not in triage_rule or "sage_work_item_registry" not in triage_rule:
            micro_policy_issues.append(
                {
                    "scope": scope,
                    "weak_triage_rule": triage_rule,
                    "required": "Must mention P0/P0.5 immediate handling and P1/P2 work item registry capture.",
                }
            )
        exit_criteria = _string_list(row.get("exit_criteria"))
        if len(exit_criteria) < 3:
            micro_policy_issues.append(
                {
                    "scope": scope,
                    "exit_criteria_count": len(exit_criteria),
                    "minimum": 3,
                }
            )

    checks = [
        _check(
            "manual_adversarial_checklist_exists_and_has_known_kind",
            CHECKLIST_PATH.exists() and meta.get("kind") == "nexora.manual_adversarial_audit_checklist",
            {"path": "config/manual_adversarial_audit_checklist.json", "kind": meta.get("kind")},
        ),
        _check(
            "global_invariants_cover_required_failure_classes",
            required_invariant_ids <= invariant_ids,
            {"required": sorted(required_invariant_ids), "present": sorted(invariant_ids)},
        ),
        _check(
            "audit_layers_are_present_ordered_and_unique",
            len(layer_rows) >= minimum_layers and not duplicate_layer_ids and not duplicate_orders and orders == sorted(orders),
            {
                "layers": len(layer_rows),
                "minimum_layers": minimum_layers,
                "duplicate_layer_ids": duplicate_layer_ids,
                "duplicate_orders": duplicate_orders,
                "orders": orders,
            },
        ),
        _check("audit_layers_have_required_fields", not missing_fields, missing_fields),
        _check("audit_layers_reference_known_invariants", not unknown_invariants, unknown_invariants),
        _check("audit_layers_reference_known_evidence_artifacts", not unknown_artifacts, unknown_artifacts),
        _check("audit_layers_have_lineage_and_adversarial_depth", not weak_layers, weak_layers),
        _check("audit_layers_include_failure_family_sweeps", not missing_failure_sweeps, missing_failure_sweeps),
        _check(
            "source_layer_coverage_policy_covers_taxonomy",
            bool(taxonomy_layer_ids)
            and not missing_source_policy_ids
            and not extra_source_policy_ids
            and not duplicate_source_policy_ids,
            {
                "taxonomy_layers": len(taxonomy_layer_ids),
                "coverage_rows": len(source_policy_rows),
                "missing_source_layer_ids": missing_source_policy_ids,
                "extra_source_layer_ids": extra_source_policy_ids,
                "duplicate_source_layer_ids": duplicate_source_policy_ids,
            },
        ),
        _check(
            "source_layer_coverage_policy_rows_are_reviewable",
            not source_policy_issues,
            source_policy_issues,
        ),
        _check(
            "micro_walkthrough_policy_covers_required_scopes_and_step_checks",
            not micro_policy_issues,
            micro_policy_issues,
        ),
    ]
    failures = [row for row in checks if not row["passed"]]
    payload = {
        "meta": {
            "kind": "manual_adversarial_audit_checklist_validation",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "generator": "tools.validate_manual_adversarial_audit_checklist",
            "source": "config/manual_adversarial_audit_checklist.json",
        },
        "summary": {
            "status": "PASS" if not failures else "FAIL",
            "layers": len(layer_rows),
            "source_layer_coverage_rows": len(source_policy_rows),
            "global_invariants": len(invariant_rows),
            "micro_walkthrough_scopes": len(micro_scope_rows),
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failures),
            "failed_checks": len(failures),
        },
        "checks": checks,
    }
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Manual Adversarial Audit Checklist Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- layers: `{summary.get('layers')}`",
        f"- global_invariants: `{summary.get('global_invariants')}`",
        f"- micro_walkthrough_scopes: `{summary.get('micro_walkthrough_scopes')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []) or []:
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:900]
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{escaped_details}` |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.artifact_registry import artifact_metadata


RAW_OUTPUT_PATH = RAW_DIR / "doctrine_audit_quality_integrity_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "doctrine_audit_quality_integrity_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_validation() -> dict[str, Any]:
    pipeline_policy_path = ROOT / "tools" / "core" / "pipeline_policy.py"
    audit_policy_path = ROOT / "config" / "audit_policy.json"
    audit_path = ROOT / "tools" / "engines" / "audit.py"
    mcp_governance_path = ROOT / "tools" / "engines" / "mcp_governance_engine.py"
    audit_rules_path = ROOT / "tools" / "core" / "audit_rules.py"
    audit_report_path = ROOT / "tools" / "core" / "audit_report.py"
    quality_gate_path = ROOT / "tools" / "engines" / "quality_gate.py"
    doctrine_path = ROOT / "config" / "architecture_doctrine.json"
    doctrine_pack_path = ROOT / "config" / "doctrines" / "governance" / "audit_rules.json"
    doctrine_manifest_path = ROOT / "config" / "doctrines" / "manifest.json"

    pipeline_policy = _read(pipeline_policy_path)
    audit_policy = _read(audit_policy_path)
    audit_source = _read(audit_path)
    mcp_governance = _read(mcp_governance_path)
    audit_rules = _read(audit_rules_path)
    audit_report = _read(audit_report_path)
    quality_gate = _read(quality_gate_path)
    doctrine = _read(doctrine_path)
    doctrine_payload = json.loads(doctrine) if doctrine else {}
    doctrine_pack = json.loads(_read(doctrine_pack_path))
    doctrine_manifest = json.loads(_read(doctrine_manifest_path))
    audit_profiles = doctrine_pack.get("audit_rule_profiles", {})
    profile_contract = doctrine_pack.get("audit_rule_profile_contract", {})
    violation_labels = doctrine_pack.get("violation_labels", {})
    required_profile_fields = set(profile_contract.get("required_fields", []))
    allowed_layers = set(profile_contract.get("allowed_layers", []))
    allowed_default_modes = set(profile_contract.get("allowed_default_modes", []))
    allowed_requirement_kinds = set(profile_contract.get("allowed_requirement_kinds", []))
    alias_scope = audit_profiles.get("relative_imports_no_alias", {}).get("scope_contract", {})
    artifacts = artifact_metadata()

    checks = [
        _check(
            "audit_policy_preserves_full_loc_limit_contract",
            '"loc_limits"' in audit_policy
            and '"function": 150' in audit_policy
            and '"class": 500' in audit_policy
            and '"default": 400' in audit_policy
            and 'get_audit_policy().get("loc_limits", {})' in pipeline_policy,
            [
                str(audit_policy_path.relative_to(ROOT)),
                str(pipeline_policy_path.relative_to(ROOT)),
            ],
        ),
        _check(
            "api_entry_policy_is_centralized",
            "def get_api_entry_filenames" in pipeline_policy
            and '"api_entry_filenames"' in pipeline_policy
            and "get_api_entry_filenames" in audit_source
            and "get_api_entry_filenames" in mcp_governance
            and '["index.ts", "index.tsx", "__init__.py", "mod.go"]' not in audit_source
            and '["index.ts", "index.tsx", "__init__.py", "mod.go"]' not in mcp_governance,
            [
                str(pipeline_policy_path.relative_to(ROOT)),
                str(audit_path.relative_to(ROOT)),
                str(mcp_governance_path.relative_to(ROOT)),
            ],
        ),
        _check(
            "doctrine_declares_api_entry_filenames",
            '"api_entry_filenames"' in doctrine
            and '"index.ts"' in doctrine
            and '"__init__.py"' in doctrine
            and '"mod.go"' in doctrine,
            str(doctrine_path.relative_to(ROOT)),
        ),
        _check(
            "audit_uses_atlas_and_shared_policy_helpers",
            "def analyze_project(changed_files=None, atlas=None):" in audit_source
            and "atlas, atlas_input_source = resolve_atlas_data(atlas)" in audit_source
            and "resolve_loc_finding_semantics" in audit_source
            and "get_audit_report_sections()" in audit_source
            and "get_module_root_name()" in audit_source
            and "resolve_layer(" in audit_source
            and "is_violation(" in audit_source,
            str(audit_path.relative_to(ROOT)),
        ),
        _check(
            "audit_persists_rule_taxonomy_and_invalidates_cache",
            "build_rule_taxonomy" in audit_source
            and 'summary["rule_taxonomy"]' in audit_source
            and 'summary["audit_scope"]' in audit_source
            and "audited_project_count" in audit_source
            and "atlas_project_count" in audit_source
            and 'output_json_path = watchdog_artifact_path("audit") if is_scoped else AUDIT_REPORT_JSON_PATH' in audit_source
            and "save_json_atomic(output_json_path, payload)" in audit_source
            and "invalidate_audit_report_cache()" in audit_source,
            str(audit_path.relative_to(ROOT)),
        ),
        _check(
            "audit_rule_modes_are_contextual_and_doctrine_aware",
            'require_doctrine_mapping("audit_rule_profiles")' in audit_rules
            and 'require_doctrine_mapping("audit_rule_profile_contract")' in audit_rules
            and "governance_policy" in audit_rules
            and "blocking_rules" in audit_rules
            and "healing_rules" in audit_rules
            and "advisory_rules" in audit_rules
            and "build_rule_taxonomy" in audit_rules,
            str(audit_rules_path.relative_to(ROOT)),
        ),
        _check(
            "audit_rule_profiles_have_one_modular_doctrine_owner",
            isinstance(audit_profiles, dict)
            and bool(audit_profiles)
            and set(audit_profiles) == set(violation_labels)
            and all(
                isinstance(profile, dict)
                and required_profile_fields.issubset(profile)
                and profile.get("label") == violation_labels.get(rule_key)
                and profile.get("layer") in allowed_layers
                and profile.get("default_mode") in allowed_default_modes
                and isinstance(profile.get("requires"), list)
                and all(
                    isinstance(requirement, dict)
                    and set(requirement) == {"kind", "value"}
                    and requirement.get("kind") in allowed_requirement_kinds
                    for requirement in profile.get("requires", [])
                )
                for rule_key, profile in audit_profiles.items()
            )
            and doctrine_manifest.get("validation_contract", {}).get("exclusive_contract_owners", {}).get("audit_rule_profiles")
            == "governance.audit_rules"
            and doctrine_manifest.get("validation_contract", {}).get("exclusive_contract_owners", {}).get("audit_rule_profile_contract")
            == "governance.audit_rules"
            and doctrine_payload.get("audit_rule_profiles") == audit_profiles
            and doctrine_payload.get("audit_rule_profile_contract") == profile_contract,
            {
                "profile_count": len(audit_profiles),
                "label_count": len(violation_labels),
                "owner": doctrine_manifest.get("validation_contract", {}).get("exclusive_contract_owners", {}).get("audit_rule_profiles"),
                "pack": str(doctrine_pack_path.relative_to(ROOT)),
            },
        ),
        _check(
            "canonical_alias_boundary_scope_is_explicit_and_centrally_owned",
            isinstance(alias_scope, dict)
            and alias_scope.get("identity") == "canonical_alias_boundary_bypass"
            and set(alias_scope.get("languages", [])) == {"javascript", "typescript"}
            and alias_scope.get("requires_workspace_path_alias") is True
            and alias_scope.get("allow_within_module_relative_imports") is True
            and set(alias_scope.get("exempt_import_extensions", [])) == {".css", ".scss", ".sass", ".less"}
            and "canonical_alias_boundary_decision" in audit_rules
            and "violates_canonical_alias_boundary" in audit_source
            and "canonical_alias_boundary_decision" in mcp_governance
            and "Canonical Alias Boundary Bypass" in doctrine,
            str(doctrine_pack_path.relative_to(ROOT)),
        ),
        _check(
            "audit_report_helper_is_single_cached_consumption_surface",
            "@functools.lru_cache" in audit_report
            and "def invalidate_audit_report_cache" in audit_report
            and "def get_rule_taxonomy" in audit_report
            and "def get_project_counts" in audit_report,
            str(audit_report_path.relative_to(ROOT)),
        ),
        _check(
            "quality_gate_consumes_audit_helper_and_taxonomy_modes",
            "from tools.core.audit_report import load_audit_report" in quality_gate
            and "audit_report = load_audit_report()" in quality_gate
            and "def _audit_mode_breakdown" in quality_gate
            and 'summary.get("rule_taxonomy", {})' in quality_gate
            and "RAW_DIR / 'audit_report.json'" not in quality_gate
            and "release_enforced_total" in quality_gate
            and "advisory_audit_violations" in quality_gate
            and "max_enforced_audit_violations" in quality_gate,
            str(quality_gate_path.relative_to(ROOT)),
        ),
        _check(
            "artifact_contracts_cover_audit_and_quality_outputs",
            artifacts.get("audit_report", {}).get("schema") == "config/schemas/audit_report.schema.json"
            and artifacts.get("audit_report", {}).get("path") == "output/.raw/audit_report.json"
            and artifacts.get("quality_gate", {}).get("schema") == "config/schemas/quality_gate.schema.json"
            and artifacts.get("quality_gate", {}).get("path") == "output/.raw/quality_gate.json",
            {
                "audit_report": artifacts.get("audit_report"),
                "quality_gate": artifacts.get("quality_gate"),
            },
        ),
    ]
    failed = [check for check in checks if not check.get("passed")]
    return {
        "meta": {
            "kind": "doctrine_audit_quality_integrity_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_doctrine_audit_quality_integrity",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }


def render_report(validation: dict[str, Any]) -> str:
    summary = validation.get("summary", {})
    lines = [
        "# Doctrine Audit Quality Integrity Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in validation.get("checks", []):
        result = "PASS" if check.get("passed") else "FAIL"
        details = json.dumps(check.get("details"), ensure_ascii=False)
        lines.append(f"| `{check.get('name')}` | {result} | `{details}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    validation = run_validation()
    save_json_atomic(RAW_OUTPUT_PATH, validation)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(validation))
    print(json.dumps(validation.get("summary", {}), ensure_ascii=False))
    return 0 if validation.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

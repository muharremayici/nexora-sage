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


ENGINE_PATH = CODE_MAPS_DIR / "tools" / "engines" / "quality_gate.py"
DOCTRINE_PATH = CODE_MAPS_DIR / "config" / "architecture_doctrine.json"
QUALITY_GATE_POLICY_PATH = CODE_MAPS_DIR / "config" / "quality_gate_policy.json"
WORKLOAD_POLICY_PATH = CODE_MAPS_DIR / "config" / "workload_profile.json"
RUNTIME_CONFIG_SCHEMA_PATH = CODE_MAPS_DIR / "config" / "schemas" / "codemaps.config.schema.json"


def _check(name: str, passed: bool, details: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "details": details,
        "evidence": evidence,
    }


def _function_body(source: str, function_name: str) -> str:
    marker = f"def {function_name}("
    start = source.find(marker)
    if start < 0:
        return ""
    next_def = source.find("\ndef ", start + len(marker))
    return source[start:] if next_def < 0 else source[start:next_def]


def _ratio_tokens(rules: list[Any]) -> list[str]:
    tokens: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict) or "min_ratio" not in rule:
            continue
        tokens.append(str(rule["min_ratio"]))
    return sorted(set(tokens))


def run_validation() -> dict[str, Any]:
    engine_text = ENGINE_PATH.read_text(encoding="utf-8", errors="replace")
    compiler_text = (CODE_MAPS_DIR / "tools" / "config_compiler.py").read_text(encoding="utf-8", errors="replace")
    discovery_text = (CODE_MAPS_DIR / "tools" / "orchestrators" / "discovery.py").read_text(encoding="utf-8", errors="replace")
    doctrine_text = DOCTRINE_PATH.read_text(encoding="utf-8", errors="replace")
    doctrine = load_json_file(DOCTRINE_PATH, {})
    quality_policy = load_json_file(QUALITY_GATE_POLICY_PATH, {})
    workload_policy = load_json_file(WORKLOAD_POLICY_PATH, {})
    runtime_schema = load_json_file(RUNTIME_CONFIG_SCHEMA_PATH, {})
    if not isinstance(doctrine, dict):
        doctrine = {}
    if not isinstance(quality_policy, dict):
        quality_policy = {}
    if not isinstance(workload_policy, dict):
        workload_policy = {}
    if not isinstance(runtime_schema, dict):
        runtime_schema = {}

    manual_budget = workload_policy.get("manual_review_budget", {})
    manual_budget = manual_budget if isinstance(manual_budget, dict) else {}
    manual_required_fields = {
        "applicable_project_roles",
        "base_items",
        "per_indexed_project_items",
        "file_bucket",
        "per_file_bucket_items",
        "symbol_bucket",
        "per_symbol_bucket_items",
        "dependency_edge_bucket",
        "per_dependency_edge_bucket_items",
        "per_applicable_analyzer_family_items",
        "analyzer_families",
        "baseline_trend_headroom_ratio",
        "baseline_minimum_headroom_items",
        "hard_upper_bound_items",
    }
    quality_schema = runtime_schema.get("properties", {}).get("quality_gates", {})
    quality_schema = quality_schema if isinstance(quality_schema, dict) else {}
    quality_schema_properties = quality_schema.get("properties", {})
    quality_schema_properties = quality_schema_properties if isinstance(quality_schema_properties, dict) else {}
    quality_schema_required = quality_schema.get("required", [])
    quality_schema_required = quality_schema_required if isinstance(quality_schema_required, list) else []

    body = _function_body(engine_text, "_effective_dead_code_ratio_threshold")
    scaling = (
        doctrine.get("quality_gate_heuristics", {}).get("dead_code_ratio_scaling", {})
        if isinstance(doctrine.get("quality_gate_heuristics"), dict)
        else {}
    )
    scaling_contract = (
        doctrine.get("quality_gate_heuristics", {}).get("validation_contract", {})
        if isinstance(doctrine.get("quality_gate_heuristics"), dict)
        else {}
    )
    minimum_rule_counts = (
        scaling_contract.get("dead_code_ratio_scaling_min_rule_counts", {})
        if isinstance(scaling_contract, dict)
        else {}
    )
    high_rules = scaling.get("high", []) if isinstance(scaling, dict) else []
    medium_rules = scaling.get("medium", []) if isinstance(scaling, dict) else []
    ratio_tokens = _ratio_tokens(high_rules + medium_rules)
    body_hits = [token for token in ratio_tokens if token in body]
    doctrine_missing = [token for token in ratio_tokens if token not in doctrine_text]

    checks = [
        _check(
            "quality_gate_seed_defaults_are_policy_backed",
            quality_policy.get("meta", {}).get("kind") == "quality_gate_policy"
            and isinstance(quality_policy.get("contract_defaults"), dict)
            and "quality_gate_contract_defaults()" in compiler_text
            and "quality_gate_discovery_seed()" in discovery_text
            and "QUALITY_GATE_CONTRACT_DEFAULTS" not in compiler_text,
            "Generated quality gate seed/default values should live in config/quality_gate_policy.json and be consumed through tools.core.quality_gate_policy.",
            {
                "policy": QUALITY_GATE_POLICY_PATH.relative_to(CODE_MAPS_DIR).as_posix(),
                "compiler_uses_helper": "quality_gate_contract_defaults()" in compiler_text,
                "discovery_uses_helper": "quality_gate_discovery_seed()" in discovery_text,
            },
        ),
        _check(
            "quality_gate_dead_code_ratio_scaling_is_doctrine_backed",
            isinstance(high_rules, list)
            and isinstance(medium_rules, list)
            and isinstance(minimum_rule_counts.get("high"), int)
            and isinstance(minimum_rule_counts.get("medium"), int)
            and len(high_rules) >= minimum_rule_counts["high"]
            and len(medium_rules) >= minimum_rule_counts["medium"],
            "Dead-code ratio scaling for small and large repositories should live in architecture_doctrine.json.",
            {
                "scaling": scaling,
                "minimum_rule_counts": minimum_rule_counts,
            },
        ),
        _check(
            "quality_gate_engine_has_no_migrated_ratio_literals_in_scaling_function",
            not body_hits,
            "The scaling function should consume doctrine rules rather than embedding dead-code ratio tolerances.",
            body_hits,
        ),
        _check(
            "quality_gate_doctrine_contains_migrated_ratio_literals",
            not doctrine_missing,
            "Migrated ratio tolerances should remain explicit and reviewable in doctrine.",
            doctrine_missing,
        ),
        _check(
            "quality_gate_scaling_rules_have_bounds_and_ratios",
            all(
                isinstance(rule, dict)
                and "min_ratio" in rule
                and ("max_export_universe" in rule or "min_export_universe" in rule)
                for rule in high_rules + medium_rules
            ),
            "Each ratio scaling rule must declare a bound and a minimum effective ratio.",
            high_rules + medium_rules,
        ),
        _check(
            "manual_review_budget_is_central_and_bounded",
            manual_required_fields.issubset(manual_budget)
            and int(manual_budget.get("hard_upper_bound_items", 0) or 0) > 0
            and isinstance(manual_budget.get("analyzer_families"), dict)
            and bool(manual_budget.get("analyzer_families")),
            "Adaptive manual-review coefficients and the invariant ceiling must be owned by the central workload policy.",
            {
                "policy": WORKLOAD_POLICY_PATH.relative_to(CODE_MAPS_DIR).as_posix(),
                "missing_fields": sorted(manual_required_fields - set(manual_budget)),
                "hard_upper_bound_items": manual_budget.get("hard_upper_bound_items"),
            },
        ),
        _check(
            "manual_review_budget_is_consumed_without_compiled_literal",
            "manual_review_budget_selection(" in engine_text
            and 'pop("max_manual_review", None)' in compiler_text
            and "20 * project_count" not in engine_text
            and "20 * project_count_for_gate_defaults" not in compiler_text,
            "Quality Gate must consume the adaptive selector while the compiler removes the obsolete derived literal.",
            {
                "engine_uses_selector": "manual_review_budget_selection(" in engine_text,
                "compiler_removes_legacy_field": 'pop("max_manual_review", None)' in compiler_text,
            },
        ),
        _check(
            "manual_review_baseline_schema_is_explicit_and_not_a_fixed_required_cap",
            "manual_review_budget_baseline" in quality_schema_properties
            and "max_manual_review" not in quality_schema_required
            and "max_manual_review" not in quality_schema_properties,
            "Repository-specific baseline evidence must be schema-governed while max_manual_review is absent from canonical runtime fields.",
            {
                "baseline_schema_present": "manual_review_budget_baseline" in quality_schema_properties,
                "legacy_field_required": "max_manual_review" in quality_schema_required,
                "legacy_field_declared": "max_manual_review" in quality_schema_properties,
            },
        ),
        _check(
            "compiled_runtime_workspace_fields_have_declared_serialization_order",
            'workspace_scoped_keys = ("workspace_root", "source_extensions", "project_roles", "skip_dirs")'
            in compiler_text
            and 'workspace_scoped_keys = {"workspace_root"' not in compiler_text,
            "Serialized runtime truth must iterate workspace-owned fields in an explicit order rather than Python set order.",
            {"compiler": "tools/config_compiler.py"},
        ),
    ]

    payload = {
        "meta": {"kind": "quality_gate_policy_core_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "quality_gate_policy_core_validation.json", payload)

    lines = [
        "# Quality Gate Policy Core Validation",
        "",
        f"- total_checks: `{payload['summary']['total_checks']}`",
        f"- passed_checks: `{payload['summary']['passed_checks']}`",
        f"- failed_checks: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |")
    save_text_atomic(REPORTS_DIR / "quality_gate_policy_core_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

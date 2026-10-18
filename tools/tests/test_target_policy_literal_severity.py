"""Tool-specific literal admission; no target config or native tool is executed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.core.pipeline_policy import resolve_loc_finding_semantics
from tools.core.target_policy_profile import (
    _literal_rule_rows,
    aggregate_effective_target_policy,
    inventory_project_target_policy,
    load_target_policy_contract,
    _resolve_target_symbol_loc_declaration as resolve_target_symbol_loc_policy,  # Declaration-unit tests; application is tested separately.
)


def _effective(tmp_path: Path, tool: str, value, *, overrides=None, unrelated=False):
    rule = "unrelated" if unrelated else "max-lines-per-function"
    payload = {"rules": {rule: value}}
    name = ".eslintrc.json"
    if tool == "biome":
        name = "biome.json"
        payload = {"$schema": "https://biomejs.dev/schemas/2.5.15/schema.json",
            "linter": {"rules": {"complexity": {"noExcessiveLinesPerFunction": value}}}}
    if overrides is not None:
        payload["overrides"] = overrides
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    # Match the serialized Discovery -> runtime transport, not only an in-memory helper.
    return json.loads(json.dumps(aggregate_effective_target_policy({"MAIN": profile})))


def _resolve(effective, file="src/probe.ts"):
    return resolve_target_symbol_loc_policy(effective, project="MAIN", file_path=file,
        symbol_kind="function", sage_default_limit=200)


@pytest.mark.parametrize("severity,state", [
    (0, "disabled"), (1, "advisory"), (2, "enforced"),
    (1.0, "advisory"), ("off", "disabled"), ("warn", "advisory"), ("error", "enforced"),
])
def test_eslint_explicit_levels_retain_declared_limit_or_disabled_signal(tmp_path, severity, state):
    effective = _effective(tmp_path, "eslint", [severity, {"max": 7}])
    result = _resolve(effective)
    assert result["matched_rules"][0]["state"] == state
    if state == "disabled":
        assert result["status"] == "target_rule_disabled_sage_signal_retained"
        assert result["limit"] == 200
    else:
        assert result["status"] == "resolved_declared_literal_policy"
        assert result["limit"] == 7
        assert result["limit_authority"] == "declared_literal_policy_not_native_execution_proof"
    assert result["native_execution"] == "not_run"
    assert effective["summary"]["combined_governance_verdict"] == "not_available"


@pytest.mark.parametrize("severity", [True, False, "info", "warning", "on", "2", 3, 0.5, None,
    {"level": "error"}, ["error"], []])
def test_invalid_eslint_severity_is_retained_unresolved_not_applied(tmp_path, severity):
    effective = _effective(tmp_path, "eslint", [severity, {"max": 7}])
    result = _resolve(effective)
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    assert projection["rules"][0]["state"] == "unknown"
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 200
    assert result["unresolved_reasons"] == ["eslint:max-lines-per-function:unsupported_literal_rule_level"]


@pytest.mark.parametrize("value", [{"level": "error", "options": {"max": 7}}, [], {}, True, False])
def test_eslint_does_not_accept_biome_object_or_boolean_grammar(tmp_path, value):
    result = _resolve(_effective(tmp_path, "eslint", value))
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 200


@pytest.mark.parametrize("level,state", [("off", "disabled"), ("info", "advisory"),
    ("warn", "advisory"), ("error", "enforced")])
def test_biome_explicit_object_levels_are_not_eslint_grammar(tmp_path, level, state):
    result = _resolve(_effective(tmp_path, "biome", {"level": level, "options": {"maxLines": 7}}))
    assert result["matched_rules"][0]["state"] == state
    assert result["limit"] == (200 if level == "off" else 7)


@pytest.mark.parametrize("value", ["on", {"level": "on", "options": {"maxLines": 7}}])
def test_biome_on_preserves_unresolved_rule_default_not_enforced(tmp_path, value):
    effective = _effective(tmp_path, "biome", value)
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    assert projection["rules"][0]["state"] == "default"
    result = _resolve(effective)
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 200
    assert result["unresolved_reasons"] == ["biome:complexity/noExcessiveLinesPerFunction:rule_default_severity_unresolved"]


@pytest.mark.parametrize("value", [True, False, 0, 1, 2, "warning", ["error", {"maxLines": 7}],
    {"options": {"maxLines": 7}}, {"level": True, "options": {"maxLines": 7}}])
def test_invalid_biome_forms_do_not_borrow_eslint_severities(tmp_path, value):
    result = _resolve(_effective(tmp_path, "biome", value))
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 200
    assert result["unresolved_reasons"] == ["biome:complexity/noExcessiveLinesPerFunction:unsupported_literal_rule_level"]


def test_invalid_relevant_row_cannot_be_hidden_by_a_later_valid_override(tmp_path):
    effective = _effective(tmp_path, "eslint", [True, {"max": 7}], overrides=[
        {"files": ["src/**/*.ts"], "rules": {"max-lines-per-function": ["error", {"max": 8}]}}])
    result = _resolve(effective)
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 200


def test_out_of_scope_invalid_rule_does_not_claim_relevant_resolution(tmp_path):
    effective = _effective(tmp_path, "eslint", ["error", {"max": 7}], overrides=[
        {"files": ["tests/**/*.ts"], "rules": {"max-lines-per-function": [True, {"max": 8}]}}])
    assert _resolve(effective)["limit"] == 7
    assert _resolve(effective, "tests/probe.ts")["status"] == "unresolved_fallback_to_sage_default"


def test_unrelated_unknown_rule_does_not_become_loc_authority(tmp_path):
    result = _resolve(_effective(tmp_path, "eslint", [True, {"max": 7}], unrelated=True))
    assert result["status"] == "no_matching_static_rule"
    assert result["limit"] == 200


@pytest.mark.parametrize("tool,levels", [
    ("eslint", {"off": "disabled", "warn": "advisory", "error": "enforced"}),
    ("biome", {"off": "disabled", "on": "default", "info": "advisory", "warn": "advisory", "error": "enforced"}),
])
def test_scalar_levels_retain_tool_specific_inventory_state(tool, levels):
    for level, state in levels.items():
        payload = {"rules": {"probe": level}} if tool == "eslint" else {
            "linter": {"rules": {"complexity": {"probe": level}}}}
        rows, truncated = _literal_rule_rows(tool, payload, max_rules=500)
        assert not truncated and rows[0]["state"] == state


@pytest.mark.parametrize("guard,reason", [("extends_unresolved", "extends_unresolved"),
    ("rules_truncated", "rules_truncated"), ("multiple_configs", "multiple_config_precedence_unknown")])
def test_severity_repair_preserves_existing_config_refusals(tmp_path, guard, reason):
    effective = _effective(tmp_path, "eslint", ["error", {"max": 7}])
    configs = effective["projects"]["MAIN"]["tools"][0]["config_files"]
    if guard == "multiple_configs":
        configs.append(dict(configs[0], path="nested/.eslintrc.json"))
    else:
        configs[0]["static_projection"][guard] = True
    result = _resolve(effective)
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 200
    assert result["unresolved_reasons"] == [f"eslint:{reason}"]


@pytest.mark.parametrize("marker", [None, "foreign_severity_grammar"])
def test_old_or_foreign_projection_requires_refresh_even_with_advisory_state(tmp_path, marker):
    effective = _effective(tmp_path, "eslint", ["warn", {"max": 7}])
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    if marker is None:
        projection.pop("rule_level_contract", None)
    else:
        projection["rule_level_contract"] = marker
    result = _resolve(effective)
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 200
    assert result["unresolved_reasons"] == ["eslint:severity_admission_refresh_required"]


def test_invalid_severity_fallback_reaches_actual_audit_loc_decision(tmp_path):
    effective = _effective(tmp_path, "eslint", [True, {"max": 7}])
    finding = resolve_loc_finding_semantics("function", 180, symbol_name="probe", project="MAIN",
        file_path="src/probe.ts", effective_target_policy=effective)
    assert finding["limit"] == 150
    assert finding["limit_authority"] == "sage_default"
    assert finding["target_policy_resolution"]["status"] == "unresolved_fallback_to_sage_default"


@pytest.mark.parametrize("state", [{}, [], True, "invented", None])
def test_malformed_current_projection_state_remains_unknown_not_an_exception(tmp_path, state):
    effective = _effective(tmp_path, "eslint", ["warn", {"max": 7}])
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    projection["rules"][0]["state"] = state
    result = _resolve(effective)
    assert result["limit"] == 200
    assert result["unresolved_reasons"] == ["eslint:max-lines-per-function:unsupported_literal_rule_level"]


def test_unknown_rows_count_toward_existing_cap_and_keep_scope():
    rows, truncated = _literal_rule_rows("eslint", {"rules": {"a": True, "b": "info"}}, max_rules=1)
    assert truncated and len(rows) == 1
    assert rows[0]["id"] == "a" and rows[0]["state"] == "unknown"
    assert rows[0]["scope"] == {"includes": ["**/*"], "excludes": []}


def test_rule_level_contract_is_emitted_from_existing_central_owner(tmp_path):
    effective = _effective(tmp_path, "eslint", ["error", {"max": 7}])
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    assert projection["rule_level_contract"] == load_target_policy_contract()["static_projection"]["rule_level_contract"]


def test_stylelint_inventory_sibling_is_unchanged_and_not_a_loc_adapter():
    rows, truncated = _literal_rule_rows("stylelint", {"rules": {"color-no-invalid-hex": True}}, max_rules=500)
    assert rows == [{"id": "color-no-invalid-hex", "state": "advisory",
        "scope": {"includes": ["**/*"], "excludes": []}}]
    assert not truncated
    assert not any(row["tool"] == "stylelint" for row in
        load_target_policy_contract()["consumer_adapters"]["symbol_loc_limit"]["supported_rule_mappings"])

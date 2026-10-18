"""Numeric admission is distinct from native execution and LOC metric equality."""
import json

import pytest

from tools.core.pipeline_policy import resolve_loc_finding_semantics
from tools.core.target_policy_profile import (
    aggregate_effective_target_policy, inventory_project_target_policy,
    _resolve_target_symbol_loc_declaration as resolve_target_symbol_loc_policy,  # Declaration-unit tests, not comparison authority.
)
from tools.engines.quality_gate import _audit_enforcement_authority


def _effective(tmp_path, tool, options, *, version="2.5.15", value=None):
    if value is None:
        value = ["error", options] if tool == "eslint" else {"level": "error", "options": options}
    payload = {"rules": {"max-lines-per-function": value}} if tool == "eslint" else {
        "$schema": f"https://biomejs.dev/schemas/{version}/schema.json",
        "linter": {"rules": {"complexity": {"noExcessiveLinesPerFunction": value}}}}
    (tmp_path / (".eslintrc.json" if tool == "eslint" else "biome.json")).write_text(json.dumps(payload), encoding="utf-8")
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    return json.loads(json.dumps(aggregate_effective_target_policy({"MAIN": profile})))


def _resolve(effective):
    return resolve_target_symbol_loc_policy(effective, project="MAIN", file_path="src/main.ts",
        symbol_kind="function", sage_default_limit=150)


@pytest.mark.parametrize("tool,key", [("eslint", "max"), ("biome", "maxLines")])
@pytest.mark.parametrize("value", [2.7, float("inf"), float("-inf"), float("nan"), True, "20", 0, -1])
def test_unsafe_numeric_values_never_truncate_raise_or_authorize(tmp_path, tool, key, value):
    effective = _effective(tmp_path, tool, {key: value})
    json.dumps(effective, allow_nan=False)
    result = _resolve(effective)
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150
    assert result["unresolved_reasons"]


@pytest.mark.parametrize("tool,key", [("eslint", "max"), ("biome", "maxLines")])
def test_unknown_option_is_not_silently_lost_before_numeric_admission(tmp_path, tool, key):
    result = _resolve(_effective(tmp_path, tool, {key: 20, "unrecognizedFlag": True}))
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150


@pytest.mark.parametrize("tool,key,flag", [("eslint", "max", "skipComments"), ("biome", "maxLines", "skipBlankLines")])
def test_wrong_flag_type_cannot_disappear_from_applicability(tmp_path, tool, key, flag):
    result = _resolve(_effective(tmp_path, tool, {key: 20, flag: "not-a-boolean"}))
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150


@pytest.mark.parametrize("version,limit,accepted", [("2.2.0", 255, True), ("2.2.6", 256, False),
    ("2.2.7", 256, True), ("2.5.15", 65535, True), ("2.5.15", 65536, False)])
def test_biome_declared_version_width_is_respected(tmp_path, version, limit, accepted):
    result = _resolve(_effective(tmp_path, "biome", {"maxLines": limit}, version=version))
    assert (result["limit"] == limit) is accepted
    if not accepted:
        assert result["status"] == "unresolved_fallback_to_sage_default"


@pytest.mark.parametrize("tool,key", [("eslint", "max"), ("biome", "maxLines")])
@pytest.mark.parametrize("value", [1, 20, 20.0])
def test_supported_explicit_integers_remain_declarations_without_measurement_authority(tmp_path, tool, key, value):
    effective = _effective(tmp_path, tool, {key: value})
    result = _resolve(effective)
    assert result["status"] == "resolved_declared_literal_policy" and result["limit"] == value
    finding = resolve_loc_finding_semantics("function", 30, project="MAIN", file_path="src/main.ts", effective_target_policy=effective)
    assert finding["limit"] == 150 and finding["observed_loc"] == 30
    assert finding["target_policy_resolution"]["matched_rules"][0]["limit"] == value
    assert _audit_enforcement_authority(effective)["combined_verdict"] == "not_available"


@pytest.mark.parametrize("tool,key,flags", [("eslint", "max", {"skipComments": True, "skipBlankLines": False, "IIFEs": True}),
    ("biome", "maxLines", {"skipBlankLines": True, "skipIifes": False})])
def test_counting_flag_shape_survives_without_a_metric_or_runtime_claim(tmp_path, tool, key, flags):
    effective = _effective(tmp_path, tool, {key: 20, **flags})
    admission = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]["literal_applicability"]
    evidence = admission["loc_option_evidence"][0]
    assert evidence["counting_options"] == flags
    assert evidence["measurement_compatibility"] == "not_evaluated"
    assert evidence["native_execution"] == "not_run"
    assert _resolve(effective)["limit"] == 20


@pytest.mark.parametrize("limit", [float("inf"), float("nan"), 2.7, True, 2**53])
def test_current_cached_numeric_rows_are_checked_before_conversion(tmp_path, limit):
    effective = _effective(tmp_path, "eslint", {"max": 20})
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    projection["rules"][0]["numeric_options"]["max"] = limit
    result = _resolve(effective)
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150


def test_changed_numeric_policy_requires_profile_refresh(tmp_path):
    effective = _effective(tmp_path, "eslint", {"max": 20})
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    projection["literal_applicability"]["policy_sha256"] = "0" * 64
    assert _resolve(effective)["unresolved_reasons"] == ["eslint:literal_applicability_refresh_required"]


def test_eslint_integer_safety_bound_is_an_explicit_supported_subset(tmp_path):
    assert _resolve(_effective(tmp_path, "eslint", {"max": 2**53 - 1}))["limit"] == 2**53 - 1
    assert _resolve(_effective(tmp_path, "eslint", {"max": 2**53}))["status"] == "unresolved_fallback_to_sage_default"


@pytest.mark.parametrize("value", [["error", 20], ["error", {"max": 20}, {}]])
def test_unimplemented_or_excess_option_shapes_are_explicit_fallbacks(tmp_path, value):
    result = _resolve(_effective(tmp_path, "eslint", {}, value=value))
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150


def test_irrelevant_rule_options_are_not_loc_authority(tmp_path):
    (tmp_path / ".eslintrc.json").write_text(json.dumps({"rules": {"unrelated": ["error", {"max": 2.7}]}}), encoding="utf-8")
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    assert _resolve(aggregate_effective_target_policy({"MAIN": profile}))["status"] == "no_matching_static_rule"


@pytest.mark.parametrize("claims", [42, "2.5.15", [{}], [None]])
def test_malformed_current_identity_cannot_raise_during_numeric_bounds(tmp_path, claims):
    effective = _effective(tmp_path, "biome", {"maxLines": 20})
    admission = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]["literal_applicability"]
    admission["declared_package_versions"] = claims
    result = _resolve(effective)
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150


def test_option_evidence_obeys_existing_rule_budget_and_cannot_authorize_partial_inventory(tmp_path):
    from tools.core.target_policy_profile import load_target_policy_contract
    contract = load_target_policy_contract()
    contract["static_projection"]["max_rules_per_config"] = 1
    payload = {"overrides": [{"files": ["src/**/*.ts"], "rules": {
        "max-lines-per-function": ["error", {"max": value}]}} for value in [20, 30]]}
    (tmp_path / ".eslintrc.json").write_text(json.dumps(payload), encoding="utf-8")
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path, contract=contract)
    admission = profile["tools"][0]["config_files"][0]["static_projection"]["literal_applicability"]
    assert len(admission["loc_option_evidence"]) == 1 and admission["loc_option_evidence_omitted"] == 1
    assert admission["status"] == "unresolved" and "loc_option_evidence_truncated" in admission["reasons"]


@pytest.mark.parametrize("tool", ["eslint", "biome"])
@pytest.mark.parametrize("options", [None, [], True])
def test_non_object_option_containers_keep_an_explicit_unknown(tmp_path, tool, options):
    result = _resolve(_effective(tmp_path, tool, options))
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150


def test_overflowing_json_number_does_not_break_profile_serialization_or_sage_signal(tmp_path):
    (tmp_path / ".eslintrc.json").write_text(
        '{"rules":{"max-lines-per-function":["error",{"max":1e309}]}}', encoding="utf-8")
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    effective = aggregate_effective_target_policy({"MAIN": profile})
    json.dumps(effective, allow_nan=False)
    finding = resolve_loc_finding_semantics("function", 160, project="MAIN", file_path="src/main.ts", effective_target_policy=effective)
    assert finding["limit"] == 150 and finding["observed_loc"] > finding["limit"]
    assert finding["target_policy_resolution"]["status"] == "unresolved_fallback_to_sage_default"
    assert effective["feature_flags"]["sage_native_analysis"] == "enabled"


def test_numeric_contract_change_invalidates_earlier_interpretation(tmp_path):
    from tools.core.target_policy_profile import load_target_policy_contract
    effective = _effective(tmp_path, "eslint", {"max": 20})
    contract = load_target_policy_contract()
    contract["static_projection"]["literal_applicability"]["tools"]["eslint"]["loc_options"]["supported_maximum"] = 10
    result = resolve_target_symbol_loc_policy(effective, project="MAIN", file_path="src/main.ts",
        symbol_kind="function", sage_default_limit=150, contract=contract)
    assert result["unresolved_reasons"] == ["eslint:literal_applicability_refresh_required"]


def test_native_option_contract_and_existing_loc_adapter_references_agree():
    from tools.core.target_policy_profile import load_target_policy_contract
    contract = load_target_policy_contract()
    tools = contract["static_projection"]["literal_applicability"]["tools"]
    for mapping in contract["consumer_adapters"]["symbol_loc_limit"]["supported_rule_mappings"]:
        spec = tools[mapping["tool"]]["loc_options"]
        assert spec["rule_id"] == mapping["rule"] and spec["numeric_option"] == mapping["numeric_option"]
        assert spec["supported_minimum"] <= spec["supported_maximum"]
        assert all(bound["maximum"] <= spec["supported_maximum"] for bound in spec["version_maximums"])


@pytest.mark.parametrize("tool,key", [("eslint", "max"), ("biome", "maxLines")])
def test_unknown_source_owned_option_format_never_falls_through_to_another_dialect(tmp_path, tool, key):
    from tools.core.target_policy_applicability import literal_loc_limit
    from tools.core.target_policy_profile import load_target_policy_contract
    effective = _effective(tmp_path, tool, {key: 20})
    contract = load_target_policy_contract()
    contract["static_projection"]["literal_applicability"]["tools"][tool]["loc_options"]["format"] = "foreign"
    assert literal_loc_limit(20, tool, contract) is None
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path, contract=contract)
    admission = profile["tools"][0]["config_files"][0]["static_projection"]["literal_applicability"]
    assert admission["status"] == "unresolved"
    assert admission["loc_option_evidence"][0]["reasons"] == ["unsupported_loc_option_format"]
    # Earlier valid evidence cannot be reinterpreted under the foreign policy.
    result = resolve_target_symbol_loc_policy(effective, project="MAIN", file_path="src/main.ts",
        symbol_kind="function", sage_default_limit=150, contract=contract)
    assert result["unresolved_reasons"] == ["eslint:literal_applicability_refresh_required" if tool == "eslint" else "biome:literal_applicability_refresh_required"]

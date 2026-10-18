"""Literal policy applicability is not target-native execution authority."""
from copy import deepcopy
import json

import pytest

from tools.core.pipeline_policy import resolve_loc_finding_semantics
from tools.core.target_policy_profile import (
    aggregate_effective_target_policy, inventory_project_target_policy, load_target_policy_contract,
    _resolve_target_symbol_loc_declaration as resolve_target_symbol_loc_policy,  # Declaration-unit tests; application is tested separately.
)
from tools.engines.quality_gate import _audit_enforcement_authority


SCHEMA = "https://biomejs.dev/schemas/2.5.15/schema.json"
BIOME_RULE = {"rules": {"complexity": {"noExcessiveLinesPerFunction": {
    "level": "error", "options": {"maxLines": 20}}}}}


def _biome():
    return {"$schema": SCHEMA, "linter": deepcopy(BIOME_RULE)}


def _effective(tmp_path, payload, *, tool="biome", version=None, package=None):
    name = "biome.json" if tool == "biome" else ".eslintrc.json"
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    package = package if package is not None else ({"devDependencies": {"@biomejs/biome" if tool == "biome" else "eslint": version}} if version else {})
    path = tmp_path / "package.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path,
        package_json=package, package_path=path)
    return json.loads(json.dumps(aggregate_effective_target_policy({"MAIN": profile})))


def _resolve(effective, file="src/main.ts"):
    return resolve_target_symbol_loc_policy(effective, project="MAIN", file_path=file,
        symbol_kind="function", sage_default_limit=150)


@pytest.mark.parametrize("case", ["global_override", "global_disabled", "empty_linter_filter",
    "outside_linter_filter", "override_disabled", "v1_rule_pair", "version_conflict", "foreign_schema"])
def test_counterexamples_cannot_supply_literal_limit(tmp_path, case):
    payload, version, file = _biome(), None, "src/main.ts"
    if case == "global_override":
        payload = {"$schema": SCHEMA, "files": {"includes": ["src/**/*.ts"]},
            "overrides": [{"includes": ["tests/**/*.ts"], "linter": deepcopy(BIOME_RULE)}]}
        file = "tests/main.ts"
    elif case == "global_disabled":
        payload["linter"]["enabled"] = False
    elif case == "empty_linter_filter":
        payload["linter"]["includes"] = []
    elif case == "outside_linter_filter":
        payload["linter"]["includes"] = ["src/**/*.ts"]
        file = "tests/main.ts"
    elif case == "override_disabled":
        payload = {"$schema": SCHEMA, "overrides": [{"includes": ["src/**/*.ts"],
            "linter": dict(deepcopy(BIOME_RULE), enabled=False)}]}
    elif case == "v1_rule_pair":
        payload["$schema"], version = "https://biomejs.dev/schemas/1.9.4/schema.json", "1.9.4"
        payload["files"] = {"include": ["src/**/*.ts"]}
        file = "tests/main.ts"
    elif case == "version_conflict":
        version = "1.9.4"
    else:
        payload["$schema"] = "https://example.invalid/foreign.json"
    result = _resolve(_effective(tmp_path, payload, version=version), file)
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 150 and result["unresolved_reasons"]


def test_eslint_global_ignore_is_not_misrepresented_as_effective_policy(tmp_path):
    payload = {"ignorePatterns": ["src/generated/**"],
        "rules": {"max-lines-per-function": ["error", {"max": 20}]}}
    result = _resolve(_effective(tmp_path, payload, tool="eslint"), "src/generated/main.ts")
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 150


@pytest.mark.parametrize("tool", ["biome", "eslint"])
def test_simple_supported_literals_remain_inventory_without_measurement_authority(tmp_path, tool):
    payload = _biome() if tool == "biome" else {"rules": {"max-lines-per-function": ["error", {"max": 20}]}}
    effective = _effective(tmp_path, payload, tool=tool)
    result = _resolve(effective)
    assert result["status"] == "resolved_declared_literal_policy" and result["limit"] == 20
    finding = resolve_loc_finding_semantics("function", 30, project="MAIN", file_path="src/main.ts",
        effective_target_policy=effective)
    assert finding["limit"] == 150
    assert finding["limit_authority"] == "sage_default"
    assert finding["target_policy_resolution"]["matched_rules"][0]["limit"] == 20
    assert _audit_enforcement_authority(effective)["combined_verdict"] == "not_available"


def test_unresolved_admission_keeps_sage_analysis_and_default_signal(tmp_path):
    payload = _biome()
    payload["linter"]["enabled"] = False
    effective = _effective(tmp_path, payload)
    finding = resolve_loc_finding_semantics("function", 160, project="MAIN", file_path="src/main.ts",
        effective_target_policy=effective)
    assert finding["limit"] == 150 and finding["observed_loc"] == 160
    assert finding["rule"] == "loc_limits_symbol"
    assert effective["feature_flags"]["sage_native_analysis"] == "enabled"
    assert finding["target_policy_resolution"]["native_execution"] == "not_run"


def test_root_filter_without_overrides_remains_bounded_supported_scope(tmp_path):
    payload = dict(_biome(), files={"includes": ["src/**/*.ts"]})
    effective = _effective(tmp_path, payload)
    assert _resolve(effective)["limit"] == 20
    assert _resolve(effective, "tests/main.ts")["status"] == "no_matching_static_rule"


@pytest.mark.parametrize("version", ["^2.5.0", "~2.5.15", "latest", "workspace:*", "2.5.15-beta.1", "1.9.4", "3.0.0"])
def test_unresolved_or_unsupported_version_declarations_do_not_authorize_limit(tmp_path, version):
    result = _resolve(_effective(tmp_path, _biome(), version=version))
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 150


def test_biome_requires_a_bounded_declared_dialect_not_an_assumed_binary_version(tmp_path):
    payload = _biome()
    payload.pop("$schema")
    assert _resolve(_effective(tmp_path, payload))["status"] == "unresolved_fallback_to_sage_default"
    assert _resolve(_effective(tmp_path, payload, version="2.5.15"))["limit"] == 20


def test_exact_matching_declarations_are_not_promoted_to_execution(tmp_path):
    effective = _effective(tmp_path, _biome(), version="2.5.15")
    assert _resolve(effective)["limit"] == 20
    assert effective["summary"]["native_execution"] == "not_run"
    assert _audit_enforcement_authority(effective)["target_native_enforcement"] == "declared_not_evaluated"


@pytest.mark.parametrize("location", ["root", "linter", "override", "language", "vcs"])
def test_unsupported_filter_and_context_siblings_remain_explicit(tmp_path, location):
    payload = _biome()
    if location == "root":
        payload["files"] = {"ignore": ["src/**"]}
    elif location == "linter":
        payload["linter"]["ignore"] = ["src/**"]
    elif location == "override":
        payload["overrides"] = [{"includes": ["**/*"], "ignores": ["src/**"], "linter": deepcopy(BIOME_RULE)}]
    elif location == "language":
        payload["javascript"] = {"linter": {"enabled": False}}
    else:
        payload["vcs"] = {"enabled": True, "useIgnoreFile": True}
    result = _resolve(_effective(tmp_path, payload))
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["unresolved_reasons"]


@pytest.mark.parametrize("value", [None, {}, {"contract": "foreign", "status": "bounded_declared_subset", "reasons": []},
    {"status": "invented", "reasons": []}])
def test_missing_or_foreign_admission_records_require_refresh(tmp_path, value):
    effective = _effective(tmp_path, _biome())
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    projection["literal_applicability"] = value
    result = _resolve(effective)
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 150
    assert result["unresolved_reasons"] == ["biome:literal_applicability_refresh_required"]


def test_current_admission_id_does_not_override_unresolved_status_or_reasons(tmp_path):
    effective = _effective(tmp_path, _biome())
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    admission = projection["literal_applicability"]
    assert admission["contract"] == load_target_policy_contract()["static_projection"]["literal_applicability"]["id"]
    admission.update(status="unresolved", reasons=["uncomposed_filter"])
    result = _resolve(effective)
    assert result["limit"] == 150
    assert result["unresolved_reasons"] == ["biome:applicability:uncomposed_filter"]


@pytest.mark.parametrize("version,accepted", [("2.0.0", False), ("2.1.4", False), ("2.2.0", True)])
def test_declared_version_must_support_the_mapped_complexity_group(tmp_path, version, accepted):
    payload = _biome()
    payload["$schema"] = f"https://biomejs.dev/schemas/{version}/schema.json"
    result = _resolve(_effective(tmp_path, payload, version=version))
    assert (result["limit"] == 20) is accepted
    if not accepted:
        assert "biome:applicability:declared_version_before_mapped_rule_group" in result["unresolved_reasons"]


@pytest.mark.parametrize("location", ["root", "override"])
@pytest.mark.parametrize("value", [None, "src/**/*.ts", {}, [True], [None]])
def test_biome_malformed_scope_cannot_be_coerced_into_authority(tmp_path, location, value):
    payload = _biome()
    if location == "root":
        payload["files"] = {"includes": value}
    else:
        payload = {"$schema": SCHEMA, "overrides": [{"includes": value, "linter": deepcopy(BIOME_RULE)}]}
    result = _resolve(_effective(tmp_path, payload))
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150
    assert any(reason.endswith(":scope_shape_unresolved") for reason in result["unresolved_reasons"])


@pytest.mark.parametrize("location", ["root", "override"])
def test_biome_empty_scope_selects_nothing_instead_of_default_all(tmp_path, location):
    payload = _biome()
    if location == "root":
        payload["files"] = {"includes": []}
    else:
        payload = {"$schema": SCHEMA, "overrides": [{"includes": [], "linter": deepcopy(BIOME_RULE)}]}
    assert _resolve(_effective(tmp_path, payload))["status"] == "no_matching_static_rule"


@pytest.mark.parametrize("includes", ["src/**/*.ts", ["src/**/*.ts"]])
def test_eslint_legacy_scope_keeps_valid_string_and_array_forms(tmp_path, includes):
    payload = {"overrides": [{"files": includes, "excludedFiles": "tests/**",
        "rules": {"max-lines-per-function": ["error", {"max": 20}]}}]}
    assert _resolve(_effective(tmp_path, payload, tool="eslint"))["limit"] == 20


@pytest.mark.parametrize("key", ["files", "excludedFiles"])
@pytest.mark.parametrize("value", [None, True, [1]])
def test_eslint_malformed_override_scope_requires_fallback(tmp_path, key, value):
    override = {"files": ["src/**/*.ts"], "rules": {"max-lines-per-function": ["error", {"max": 20}]}, key: value}
    assert _resolve(_effective(tmp_path, {"overrides": [override]}, tool="eslint"))["status"] == "unresolved_fallback_to_sage_default"


@pytest.mark.parametrize("other,accepted", [("2.5.15", True), ("2.5.14", False), ("^2.5.0", False)])
def test_version_conflicts_are_not_hidden_by_package_section_overwrite(tmp_path, other, accepted):
    package = {"dependencies": {"@biomejs/biome": other}, "devDependencies": {"@biomejs/biome": "2.5.15"}}
    result = _resolve(_effective(tmp_path, _biome(), package=package))
    assert (result["limit"] == 20) is accepted


def test_changed_central_admission_policy_invalidates_old_profile(tmp_path):
    effective = _effective(tmp_path, _biome())
    contract = load_target_policy_contract()
    contract["static_projection"]["literal_applicability"]["tools"]["biome"]["minimum_declared_version"] = [2, 6, 0]
    result = resolve_target_symbol_loc_policy(effective, project="MAIN", file_path="src/main.ts",
        symbol_kind="function", sage_default_limit=150, contract=contract)
    assert result["limit"] == 150
    assert result["unresolved_reasons"] == ["biome:literal_applicability_refresh_required"]


@pytest.mark.parametrize("tool", ["biome", "eslint"])
def test_missing_required_override_scope_does_not_default_to_all_files(tmp_path, tool):
    override = {"linter": deepcopy(BIOME_RULE)} if tool == "biome" else {
        "rules": {"max-lines-per-function": ["error", {"max": 20}]}}
    payload = {"overrides": [override]}
    if tool == "biome":
        payload["$schema"] = SCHEMA
    result = _resolve(_effective(tmp_path, payload, tool=tool))
    assert result["status"] == "unresolved_fallback_to_sage_default" and result["limit"] == 150

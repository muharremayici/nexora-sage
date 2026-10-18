"""Bounded static glob scopes, not native lint/config execution proof."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.core.pipeline_policy import resolve_loc_finding_semantics
from tools.core.target_policy_profile import (
    _static_scope_pattern_matches,
    _static_scope_matches,
    aggregate_effective_target_policy,
    inventory_project_target_policy,
    load_target_policy_contract,
    _resolve_target_symbol_loc_declaration as resolve_target_symbol_loc_policy,  # Scope-unit tests, not comparison authority.
)


# Literal, slash-qualified fixtures can also be compared with a pure minimatch
# reference outside the public suite; public tests need no npm/target install.
SEGMENT_CASES = [
    ("src/probe.ts", "src/*.ts", True),
    ("src/nested/probe.ts", "src/*.ts", False),
    ("src/probe.ts", "src/**/*.ts", True),
    ("src/one/two/probe.ts", "src/**/*.ts", True),
    ("other/probe.ts", "src/**/*.ts", False),
    ("src/nested/probe.tsx", "src/**/*.ts", False),
    ("packages/one/main.ts", "packages/*/*.ts", True),
    ("packages/one/src/main.ts", "packages/*/*.ts", False),
    ("a/end.ts", "a/**/end.ts", True),
    ("a/b/c/end.ts", "a/**/end.ts", True),
    ("a/b/end.ts/extra", "a/**/end.ts", False),
    ("a/x/b/y/z/end.ts", "a/**/b/**/end.ts", True),
    ("a/b/end.ts", "a/**/b/**/end.ts", True),
    (".config/main.ts", ".config/*.ts", True),
    ("src/.private/main.ts", "src/**/*.ts", True),
    ("src/.hidden.ts", "src/*.ts", True),
    ("src/İstanbul.ts", "src/*.ts", True),
    ("SRC/main.ts", "src/*.ts", False),
    (" src/main.ts", " src/*.ts", True),
    ("src/main.ts", " src/*.ts", False),
]


def _effective(tmp_path: Path, tool: str, includes, excludes=None):
    if tool == "eslint":
        name = ".eslintrc.json"
        override = {"files": includes, "excludedFiles": excludes or [],
            "rules": {"max-lines-per-function": ["error", {"max": 20}]}}
    else:
        name = "biome.json"
        override = {"includes": includes + [f"!{pattern}" for pattern in (excludes or [])],
            "linter": {"rules": {"complexity": {"noExcessiveLinesPerFunction": {
                "level": "error", "options": {"maxLines": 20}}}}}}
    payload = {"overrides": [override]}
    if tool == "biome":
        payload["$schema"] = "https://biomejs.dev/schemas/2.5.15/schema.json"
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    return json.loads(json.dumps(aggregate_effective_target_policy({"MAIN": profile})))


def _resolve(effective, file):
    return resolve_target_symbol_loc_policy(effective, project="MAIN", file_path=file,
        symbol_kind="function", sage_default_limit=150)


@pytest.mark.parametrize("tool", ["eslint", "biome"])
def test_single_star_scope_does_not_leak_into_nested_directories(tmp_path, tool):
    effective = _effective(tmp_path, tool, ["src/*.ts"])
    assert _resolve(effective, "src/probe.ts")["limit"] == 20
    nested = _resolve(effective, "src/nested/probe.ts")
    assert nested["status"] == "no_matching_static_rule"
    assert nested["limit"] == 150


def test_shallow_exclusion_does_not_hide_nested_applicable_rule(tmp_path):
    effective = _effective(tmp_path, "eslint", ["src/**/*.ts"], ["src/*.ts"])
    assert _resolve(effective, "src/probe.ts")["status"] == "no_matching_static_rule"
    nested = _resolve(effective, "src/nested/probe.ts")
    assert nested["status"] == "resolved_declared_literal_policy"
    assert nested["limit"] == 20


def test_projected_biome_scope_exclusion_keeps_segment_boundary_without_native_config_claim():
    # Compatibility scope carrier only. Biome native negation is a separate,
    # unsupported dialect feature, not a fictional overrides.ignores property.
    scope = {"includes": ["src/**/*.ts"], "excludes": ["src/*.ts"]}
    policy = load_target_policy_contract()["static_projection"]["scope_matcher"]
    assert _static_scope_matches("src/main.ts", scope, tool_id="biome", scope_policy=policy) is False
    assert _static_scope_matches("src/nested/main.ts", scope, tool_id="biome", scope_policy=policy) is True


@pytest.mark.parametrize("tool", ["eslint", "biome"])
@pytest.mark.parametrize("file,pattern,expected", SEGMENT_CASES)
def test_supported_segment_globs_preserve_exact_path_boundary(tool, file, pattern, expected):
    assert _static_scope_pattern_matches(file, pattern, tool_id=tool) is expected


@pytest.mark.parametrize("tool,pattern,expected", [
    ("eslint", "*.ts", True), ("eslint", "*", True), ("eslint", "probe.ts", True),
    ("eslint", "./*.ts", False), ("eslint", "./probe.ts", False),
    ("biome", "*.ts", None), ("biome", "*", None), ("biome", "probe.ts", None),
    ("biome", "**/*.ts", True), ("biome", "**", True),
])
def test_basename_semantics_are_not_shared_across_tool_dialects(tmp_path, tool, pattern, expected):
    effective = _effective(tmp_path, tool, [pattern])
    result = _resolve(effective, "src/probe.ts")
    if expected is None:
        assert result["status"] == "unresolved_fallback_to_sage_default"
        assert any(reason.endswith(":scope_unresolved") for reason in result["unresolved_reasons"])
    else:
        assert (result["status"] == "resolved_declared_literal_policy") is expected


@pytest.mark.parametrize("file", ["test/src/main.ts", "src/nested/src/main.ts"])
def test_biome_v1_v2_prefix_disagreement_never_silently_chooses_a_version(tmp_path, file):
    effective = _effective(tmp_path, "biome", ["src/*.ts"])
    result = _resolve(effective, file)
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 150


@pytest.mark.parametrize("pattern", ["src/{a,b}/*.ts", "src/?.ts", "src/[ab].ts", "src/@(x).ts",
    "!src/*.ts", "!!src/*.ts", "src/a**/*.ts", "src/***/main.ts", "src/**/**/main.ts",
    "src\\*.ts", "../src/*.ts", "/src/*.ts", "C:/src/*.ts", "src//*.ts", "src/./*.ts",
    "src/../*.ts", "src/", "", "#comment"])
@pytest.mark.parametrize("tool", ["eslint", "biome"])
def test_unsupported_or_ambiguous_grammar_is_unknown_not_a_negative(tool, pattern):
    assert _static_scope_pattern_matches("src/main.ts", pattern, tool_id=tool) is None


@pytest.mark.parametrize("file", ["../src/main.ts", "/src/main.ts", "C:/src/main.ts", "src//main.ts",
    "src/../main.ts", "src/./main.ts", "src/main.ts/", ""])
def test_non_project_relative_file_identity_cannot_receive_policy(file):
    assert _static_scope_pattern_matches(file, "**/*.ts", tool_id="eslint") is None


def test_windows_source_separators_do_not_turn_pattern_escapes_into_separators():
    assert _static_scope_pattern_matches("src\\nested\\main.ts", "src/*.ts", tool_id="eslint") is False
    assert _static_scope_pattern_matches("src\\nested\\main.ts", "src/**/*.ts", tool_id="eslint") is True
    assert _static_scope_pattern_matches("src/nested/main.ts", "src\\**\\*.ts", tool_id="eslint") is None


@pytest.mark.parametrize("tool", ["eslint", "biome"])
def test_unresolved_include_or_exclude_retains_sage_default_with_reason(tmp_path, tool):
    for includes, excludes in [(["src/{a,b}/*.ts"], []), (["src/**/*.ts"], ["src/?.ts"])]:
        effective = _effective(tmp_path, tool, includes, excludes)
        result = _resolve(effective, "src/main.ts")
        assert result["status"] == "unresolved_fallback_to_sage_default"
        assert result["limit"] == 150
        assert any(reason.endswith(":scope_unresolved") for reason in result["unresolved_reasons"])


@pytest.mark.parametrize("marker", [None, "foreign_glob_interpretation"])
def test_old_scope_interpretation_requires_refresh(tmp_path, marker):
    effective = _effective(tmp_path, "eslint", ["src/*.ts"])
    projection = effective["projects"]["MAIN"]["tools"][0]["config_files"][0]["static_projection"]
    if marker is None:
        projection.pop("scope_match_contract", None)
    else:
        projection["scope_match_contract"] = marker
    result = _resolve(effective, "src/main.ts")
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["unresolved_reasons"] == ["eslint:scope_match_refresh_required"]


def test_scope_limits_are_owned_by_the_existing_central_contract():
    limits = load_target_policy_contract()["static_projection"]["scope_matcher"]
    for file, pattern in [("a" * (limits["max_path_chars"] + 1), "**"),
        ("src/main.ts", "a" * (limits["max_pattern_chars"] + 1)),
        ("/".join(["a"] * (limits["max_segments"] + 1)), "**"),
        ("src/main.ts", "/x/".join(["**"] * (limits["max_globstars"] + 1)))]:
        assert _static_scope_pattern_matches(file, pattern, tool_id="eslint") is None


def test_scope_admission_reaches_actual_audit_threshold_without_native_authority(tmp_path):
    effective = _effective(tmp_path, "eslint", ["src/*.ts"])
    finding = resolve_loc_finding_semantics("function", 30, symbol_name="probe", project="MAIN",
        file_path="src/nested/main.ts", effective_target_policy=effective)
    assert finding["limit"] == 150
    assert finding["limit_authority"] == "sage_default"
    assert finding["target_policy_resolution"]["status"] == "no_matching_static_rule"
    assert effective["summary"]["combined_governance_verdict"] == "not_available"
    assert effective["summary"]["native_execution"] == "not_run"

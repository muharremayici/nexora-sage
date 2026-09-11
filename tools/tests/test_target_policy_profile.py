from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools.core import config as core_config
from tools.core.artifact_validator import validate_against_schema
from tools.core.target_policy_profile import (
    aggregate_effective_target_policy,
    compile_effective_target_policy,
    inventory_project_target_policy,
    resolve_target_symbol_loc_policy,
)
from tools.engines.capability_activation_planner import activation_plan_requires_refresh
from tools.engines.quality_gate import _audit_enforcement_authority
from tools.external_target_preflight import build_preflight
from tools import config_compiler


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_project_policy_inventory_preserves_declared_not_executed_boundary(tmp_path: Path) -> None:
    package = {
        "scripts": {"lint": "eslint . --max-warnings 0", "build": "tsc -p tsconfig.json"},
        "devDependencies": {"eslint": "9.1.0", "typescript": "5.7.0"},
    }
    package_path = tmp_path / "package.json"
    _write_json(package_path, package)
    (tmp_path / "eslint.config.mjs").write_text("throw new Error('must never execute');\n", encoding="utf-8")
    _write_json(tmp_path / "tsconfig.json", {"compilerOptions": {"strict": True}})

    profile = inventory_project_target_policy(
        tmp_path,
        project="MAIN",
        package_json=package,
        package_path=package_path,
        workspace_root=tmp_path,
    )

    assert profile["status"] == "declared_not_evaluated"
    assert profile["declared_tools"] == ["eslint", "typescript"]
    assert profile["feature_flags"]["target_native_execution"] == "not_evaluated"
    assert profile["feature_flags"]["combined_governance_verdict"] == "not_available"
    eslint = next(row for row in profile["tools"] if row["id"] == "eslint")
    assert eslint["config_files"][0]["path"] == "eslint.config.mjs"
    assert len(eslint["config_files"][0]["sha256"]) == 64
    assert len(profile["contract_source"]["content_sha256"]) == 64
    assert eslint["native_execution"] == "not_run"


def test_absent_policy_signal_is_unknown_not_disabled(tmp_path: Path) -> None:
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)

    assert profile["status"] == "not_observed_in_configured_taxonomy"
    assert profile["declared_tools"] == []
    assert profile["feature_flags"]["target_policy_inventory"] == "no_signal"
    assert profile["absence_semantics"] == "not_observed_does_not_prove_absence_or_disable_a_rule"


def test_biome_literal_policy_projects_tool_and_scoped_rule_states(tmp_path: Path) -> None:
    biome = {
        "linter": {
            "enabled": True,
            "rules": {
                "complexity": {
                    "noExcessiveLinesPerFunction": {
                        "level": "warn",
                        "options": {"maxLines": 100, "skipBlankLines": True},
                    }
                }
            },
        },
        "overrides": [
            {
                "includes": ["tests/**/*.ts"],
                "linter": {"rules": {"complexity": {"noExcessiveLinesPerFunction": "off"}}},
            }
        ],
    }
    _write_json(tmp_path / "biome.json", biome)

    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    tool = next(row for row in profile["tools"] if row["id"] == "biome")
    projection = tool["config_files"][0]["static_projection"]

    assert tool["policy_adapter"] == "enabled"
    assert profile["feature_flags"]["policy_adapters"]["biome"] == "enabled"
    assert profile["feature_flags"]["target_declared_tool_states"]["biome"] == "enabled"
    assert projection["tool_state"] == "enabled"
    assert projection["rules"][0]["id"] == "complexity/noExcessiveLinesPerFunction"
    assert projection["rules"][0]["numeric_options"] == {"maxLines": 100}
    assert projection["rules"][1]["state"] == "disabled"
    assert projection["rules"][1]["scope"]["includes"] == ["tests/**/*.ts"]

    effective = aggregate_effective_target_policy({"MAIN": profile})
    source_limit = resolve_target_symbol_loc_policy(
        effective,
        project="MAIN",
        file_path="src/service.ts",
        symbol_kind="function",
        sage_default_limit=150,
    )
    test_limit = resolve_target_symbol_loc_policy(
        effective,
        project="MAIN",
        file_path="tests/service.ts",
        symbol_kind="function",
        sage_default_limit=150,
    )
    assert source_limit["status"] == "resolved_declared_literal_policy"
    assert source_limit["limit"] == 100
    assert source_limit["native_execution"] == "not_run"
    assert test_limit["status"] == "target_rule_disabled_sage_signal_retained"
    assert test_limit["limit"] == 150


def test_eslint_literal_loc_rule_resolves_numeric_limit_and_executable_config_fails_closed(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".eslintrc.json",
        {"rules": {"max-lines-per-function": ["error", {"max": 80}]}},
    )
    literal = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    literal_result = resolve_target_symbol_loc_policy(
        aggregate_effective_target_policy({"MAIN": literal}),
        project="MAIN",
        file_path="src/component.tsx",
        symbol_kind="component",
        sage_default_limit=400,
    )
    assert literal_result["status"] == "resolved_declared_literal_policy"
    assert literal_result["limit"] == 80

    (tmp_path / ".eslintrc.json").unlink()
    (tmp_path / "eslint.config.mjs").write_text("export default [];\n", encoding="utf-8")
    executable = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    executable_result = resolve_target_symbol_loc_policy(
        aggregate_effective_target_policy({"MAIN": executable}),
        project="MAIN",
        file_path="src/component.tsx",
        symbol_kind="component",
        sage_default_limit=400,
    )
    assert executable_result["status"] == "unresolved_fallback_to_sage_default"
    assert executable_result["limit"] == 400


def test_ambiguous_static_scope_fails_closed_to_sage_default(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".eslintrc.json",
        {
            "overrides": [
                {
                    "files": ["src/{admin,public}/**/*.tsx"],
                    "rules": {"max-lines-per-function": ["error", {"max": 90}]},
                }
            ]
        },
    )
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)

    result = resolve_target_symbol_loc_policy(
        aggregate_effective_target_policy({"MAIN": profile}),
        project="MAIN",
        file_path="src/admin/Page.tsx",
        symbol_kind="component",
        sage_default_limit=400,
    )

    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 400
    assert result["unresolved_reasons"] == ["eslint:max-lines-per-function:scope_unresolved"]


def test_biome_nested_files_scope_is_applied_and_conflicting_scope_shapes_fail_closed(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "biome.json",
        {
            "files": {"includes": ["src/**/*.ts"]},
            "linter": {
                "rules": {
                    "complexity": {
                        "noExcessiveLinesPerFunction": {
                            "level": "warn",
                            "options": {"maxLines": 75},
                        }
                    }
                }
            },
        },
    )
    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    effective = aggregate_effective_target_policy({"MAIN": profile})

    included = resolve_target_symbol_loc_policy(
        effective,
        project="MAIN",
        file_path="src/build.ts",
        symbol_kind="function",
        sage_default_limit=150,
    )
    excluded = resolve_target_symbol_loc_policy(
        effective,
        project="MAIN",
        file_path="tests/build.ts",
        symbol_kind="function",
        sage_default_limit=150,
    )

    assert included["status"] == "resolved_declared_literal_policy"
    assert included["limit"] == 75
    assert excluded["status"] == "no_matching_static_rule"
    assert excluded["limit"] == 150

    _write_json(
        tmp_path / "biome.json",
        {
            "includes": ["src/**/*.ts"],
            "files": {"includes": ["packages/**/*.ts"]},
            "linter": {
                "rules": {
                    "complexity": {
                        "noExcessiveLinesPerFunction": {
                            "level": "warn",
                            "options": {"maxLines": 75},
                        }
                    }
                }
            },
        },
    )
    conflicting = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    result = resolve_target_symbol_loc_policy(
        aggregate_effective_target_policy({"MAIN": conflicting}),
        project="MAIN",
        file_path="src/build.ts",
        symbol_kind="function",
        sage_default_limit=150,
    )
    assert result["status"] == "unresolved_fallback_to_sage_default"
    assert result["limit"] == 150


def test_executable_eslint_config_is_inventory_only_and_never_interpreted(tmp_path: Path) -> None:
    (tmp_path / "eslint.config.mjs").write_text(
        "throw new Error('executing this config would be a defect');\n",
        encoding="utf-8",
    )

    profile = inventory_project_target_policy(tmp_path, project="MAIN", workspace_root=tmp_path)
    tool = next(row for row in profile["tools"] if row["id"] == "eslint")
    projection = tool["config_files"][0]["static_projection"]

    assert tool["policy_adapter"] == "inventory_only"
    assert tool["effective_rule_resolution"] == "not_evaluated"
    assert projection == {
        "status": "executable_config_not_statically_resolved",
        "tool_state": "unknown",
    }


def test_compiled_policy_drops_unselected_project_authority() -> None:
    declared = {
        "contract": "target_policy_profile_v1",
        "status": "declared_not_evaluated",
        "declared_tools": ["biome"],
        "feature_flags": {},
    }
    discovery = {
        "_discovery_metadata": {
            "projects": {
                "KEEP": {"workspace_signals": {"target_policy": declared}},
                "DROP": {"workspace_signals": {"target_policy": declared}},
            }
        }
    }

    compiled = compile_effective_target_policy(discovery, {"KEEP": "packages/keep"})

    assert list(compiled["projects"]) == ["KEEP"]
    assert compiled["summary"]["declared_policy_projects"] == ["KEEP"]
    assert compiled["summary"]["declared_tools"] == ["biome"]


def test_quality_authority_reports_inventory_without_promoting_enforcement() -> None:
    profile = aggregate_effective_target_policy(
        {
            "MAIN": {
                "status": "declared_not_evaluated",
                "declared_tools": ["eslint"],
                "feature_flags": {"target_native_execution": "not_evaluated"},
            }
        }
    )

    authority = _audit_enforcement_authority(profile)

    assert authority["target_native_enforcement"] == "declared_not_evaluated"
    assert authority["declared_target_policy_tools"] == ["eslint"]
    assert authority["combined_verdict"] == "not_available"


def test_multi_project_policy_states_remain_explicitly_mixed() -> None:
    profile = aggregate_effective_target_policy(
        {
            "APP": {
                "status": "declared_not_evaluated",
                "declared_tools": ["biome"],
                "feature_flags": {
                    "policy_adapters": {"biome": "enabled"},
                    "target_declared_tool_states": {"biome": "enabled"},
                },
            },
            "DOCS": {
                "status": "declared_not_evaluated",
                "declared_tools": ["biome"],
                "feature_flags": {
                    "policy_adapters": {"biome": "inventory_only"},
                    "target_declared_tool_states": {"biome": "disabled"},
                },
            },
        }
    )

    assert profile["feature_flags"]["policy_adapters"]["biome"] == "mixed"
    assert profile["feature_flags"]["target_declared_tool_states"]["biome"] == "mixed"


def test_legacy_discovery_without_policy_profile_requires_refresh() -> None:
    compiled = compile_effective_target_policy(
        {"_discovery_metadata": {"projects": {"MAIN": {"workspace_signals": {}}}}},
        {"MAIN": "."},
    )

    assert compiled["status"] == "profile_incomplete_refresh_required"
    assert compiled["summary"]["missing_policy_projects"] == ["MAIN"]
    assert compiled["feature_flags"]["target_policy_inventory"] == "refresh_required"
    authority = _audit_enforcement_authority(compiled)
    assert authority["target_native_enforcement"] == "profile_incomplete_refresh_required"
    assert authority["combined_verdict"] == "not_available"


def test_policy_config_change_forces_capability_refresh() -> None:
    assert activation_plan_requires_refresh(["MAIN::eslint.config.mjs"]) is True
    assert activation_plan_requires_refresh(["PKG::biome.json"]) is True
    assert activation_plan_requires_refresh(["MAIN::src/button.tsx"]) is False


def test_external_preflight_exposes_project_scoped_policy_inventory(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "package.json",
        {
            "scripts": {"lint": "biome check ."},
            "devDependencies": {"@biomejs/biome": "2.4.0", "react": "19.0.0"},
        },
    )
    _write_json(tmp_path / "biome.json", {"linter": {"enabled": True}})
    (tmp_path / "App.tsx").write_text("export const App = () => <main />;\n", encoding="utf-8")

    preflight = build_preflight(tmp_path)
    target_policy = preflight["summary"]["target_policy"]

    assert target_policy["status"] == "declared_not_evaluated"
    assert target_policy["summary"]["declared_tools"] == ["biome"]
    assert target_policy["summary"]["native_execution"] == "not_run"
    assert target_policy["summary"]["combined_governance_verdict"] == "not_available"


def test_external_runtime_consumes_exact_preflight_policy_without_reinventing_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_json(
        tmp_path / "package.json",
        {
            "scripts": {"lint": "biome check ."},
            "devDependencies": {"@biomejs/biome": "1.9.0", "typescript": "5.7.0"},
        },
    )
    _write_json(tmp_path / "biome.json", {"linter": {"enabled": True}})
    _write_json(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"paths": {"@/*": ["./src/*"]}}},
    )
    source = tmp_path / "src" / "App.tsx"
    source.parent.mkdir()
    source.write_text("export const App = () => <main />;\n", encoding="utf-8")
    preflight = build_preflight(tmp_path)
    preflight["meta"]["run_id"] = "preflight-fixture"
    receipt_path = tmp_path / "runs" / "preflight-fixture.json"
    _write_json(receipt_path, preflight)
    receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()

    monkeypatch.setenv("CODEMAPS_TARGET_ROOT", str(tmp_path))
    monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", str(receipt_path))
    monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", receipt_sha)
    monkeypatch.delenv("CODEMAPS_TARGET_PROJECTS", raising=False)
    for helper in (
        "_target_dependency_names",
        "_infer_target_bundler",
        "_infer_target_path_aliases",
        "_observe_target_path_aliases",
        "_infer_target_plugins",
        "_infer_target_architecture",
        "external_target_repository_topology",
    ):
        monkeypatch.setattr(
            core_config,
            helper,
            lambda *_args, _helper=helper, **_kwargs: pytest.fail(
                f"runtime recomputed {_helper} after receiving a Preflight receipt"
            ),
        )

    runtime = core_config._apply_target_root_override(
        {
            "effective_target_policy": {
                "status": "declared_not_evaluated",
                "summary": {"declared_tools": ["eslint"]},
                "projects": {},
            },
            "audit": {"rules": {"legacy": {"enabled": True}}},
            "environment": {},
        }
    )

    assert runtime["effective_target_policy"]["summary"]["declared_tools"] == ["biome", "typescript"]
    assert "eslint" not in runtime["effective_target_policy"]["summary"]["declared_tools"]
    assert runtime["_target_root_override"]["observation_source"] == "external_target_preflight_receipt"
    assert runtime["_target_root_override"]["preflight_receipt_sha256"] == receipt_sha
    assert runtime["environment"]["scoped_path_aliases"][0]["path_aliases"] == {
        "@/*": ["./src/*"]
    }


def test_external_runtime_rejects_mismatched_preflight_content_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "App.ts").write_text("export const value = 1;\n", encoding="utf-8")
    preflight = build_preflight(tmp_path)
    preflight["meta"]["run_id"] = "preflight-fixture"
    receipt_path = tmp_path / "runs" / "preflight-fixture.json"
    _write_json(receipt_path, preflight)
    monkeypatch.setenv("CODEMAPS_TARGET_ROOT", str(tmp_path))
    monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", str(receipt_path))
    monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", "0" * 64)
    monkeypatch.delenv("CODEMAPS_TARGET_PROJECTS", raising=False)

    with pytest.raises(RuntimeError, match="content identity mismatch"):
        core_config._apply_target_root_override({})


def test_external_preflight_receipt_freshness_accepts_unchanged_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_json(
        target / "package.json",
        {"devDependencies": {"typescript": "5.7.0"}},
    )
    (target / "app.ts").write_text("export const app = 1;\n", encoding="utf-8")
    preflight = build_preflight(target)
    preflight["meta"]["run_id"] = "preflight-fresh"
    receipt_path = tmp_path / "receipts" / "runs" / "preflight-fresh.json"
    _write_json(receipt_path, preflight)
    source_env = {
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT": str(receipt_path),
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": hashlib.sha256(
            receipt_path.read_bytes()
        ).hexdigest(),
    }

    loaded = core_config.load_external_target_preflight_receipt(
        target,
        source_env=source_env,
        require_freshness=True,
    )

    assert loaded is not None
    assert loaded["meta"]["run_id"] == "preflight-fresh"


def test_external_preflight_receipt_freshness_rejects_changed_decision_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    package_path = target / "package.json"
    _write_json(package_path, {"devDependencies": {"typescript": "5.7.0"}})
    preflight = build_preflight(target)
    preflight["meta"]["run_id"] = "preflight-stale"
    receipt_path = tmp_path / "receipts" / "runs" / "preflight-stale.json"
    _write_json(receipt_path, preflight)
    source_env = {
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT": str(receipt_path),
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": hashlib.sha256(
            receipt_path.read_bytes()
        ).hexdigest(),
    }
    original_stat = package_path.stat()
    original_bytes = package_path.read_bytes()
    changed_bytes = original_bytes.replace(b"5.7.0", b"5.8.0")
    assert len(changed_bytes) == len(original_bytes)
    package_path.write_bytes(changed_bytes)
    os.utime(
        package_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    with pytest.raises(RuntimeError, match="target observation identity is stale"):
        core_config.load_external_target_preflight_receipt(
            target,
            source_env=source_env,
            require_freshness=True,
        )


def test_target_policy_contract_matches_schema() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "config" / "target_policy_profile_contract.json").read_text(encoding="utf-8"))
    errors = validate_against_schema(
        root / "config" / "schemas" / "target_policy_profile_contract.schema.json",
        "target_policy_profile_contract",
        payload,
    )

    assert errors == []


def test_quality_gate_authority_schema_matches_target_policy_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    contract = json.loads(
        (root / "config" / "target_policy_profile_contract.json").read_text(encoding="utf-8")
    )
    quality_schema = json.loads(
        (root / "config" / "schemas" / "quality_gate.schema.json").read_text(encoding="utf-8")
    )
    authority_schema = quality_schema["properties"]["audit_enforcement_authority"]["properties"]
    projection = contract["authority"]["quality_gate_projection"]

    assert authority_schema["target_native_enforcement"]["enum"] == projection[
        "target_native_enforcement_statuses"
    ]
    assert authority_schema["combined_verdict"]["enum"] == projection["combined_verdict_statuses"]


@pytest.mark.parametrize(
    "target_status",
    [
        "declared_not_evaluated",
        "not_observed_in_configured_taxonomy",
        "profile_incomplete_refresh_required",
    ],
)
def test_quality_gate_authority_schema_accepts_canonical_target_policy_states(
    target_status: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    payload = {
        "passed": True,
        "release_gate_status": "PASS",
        "ecosystem_signal_status": "CLEAR",
        "ecosystem_warning_signals": {},
        "audit_enforcement_authority": {
            "native_enforcement": "sage_native_audit_taxonomy",
            "target_native_enforcement": target_status,
            "combined_verdict": "not_available",
            "claim_boundary": "bounded",
        },
        "checks": [],
    }

    errors = validate_against_schema(
        root / "config" / "schemas" / "quality_gate.schema.json",
        "quality_gate",
        payload,
    )

    assert errors == []


def test_quality_gate_authority_schema_rejects_unknown_target_policy_state() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = {
        "passed": True,
        "release_gate_status": "PASS",
        "ecosystem_signal_status": "CLEAR",
        "ecosystem_warning_signals": {},
        "audit_enforcement_authority": {
            "native_enforcement": "sage_native_audit_taxonomy",
            "target_native_enforcement": "invented_status",
            "combined_verdict": "not_available",
            "claim_boundary": "bounded",
        },
        "checks": [],
    }

    errors = validate_against_schema(
        root / "config" / "schemas" / "quality_gate.schema.json",
        "quality_gate",
        payload,
    )

    assert any("target_native_enforcement" in error for error in errors)


def test_config_compiler_reprojects_policy_after_main_overlap_shield(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovery_path = tmp_path / "codemaps.discovery.json"
    overrides_path = tmp_path / "codemaps.overrides.json"
    config_path = tmp_path / "codemaps.config.json"
    profile = {
        "contract": "target_policy_profile_v1",
        "status": "declared_not_evaluated",
        "declared_tools": ["eslint"],
        "feature_flags": {},
    }
    _write_json(
        discovery_path,
        {
            "_meta": {"kind": "codemaps.discovery", "version": "v1", "generated_by": "test", "purpose": "test"},
            "workspace_root": ".",
            "source_extensions": [".ts"],
            "skip_dirs": [],
            "variations": {"MAIN": ".", "PACKAGE": "packages/example"},
            "project_roles": {"MAIN": "host", "PACKAGE": "companion"},
            "environment": {},
            "plugins": ["typescript"],
            "architecture": {"type": "monorepo", "module_root": "."},
            "audit_seed": {},
            "quality_gate_seed": {},
            "_discovery_metadata": {
                "projects": {
                    "MAIN": {"workspace_signals": {"target_policy": profile}},
                    "PACKAGE": {"workspace_signals": {"target_policy": profile}},
                }
            },
        },
    )
    _write_json(overrides_path, {})
    _write_json(config_path, {})
    monkeypatch.setattr(config_compiler, "DISCOVERY_PATH", discovery_path)
    monkeypatch.setattr(config_compiler, "OVERRIDES_PATH", overrides_path)
    monkeypatch.setattr(config_compiler, "CONFIG_PATH", config_path)

    compiled = config_compiler.compile_runtime_config()

    assert "MAIN" not in compiled["variations"]
    assert list(compiled["effective_target_policy"]["projects"]) == ["PACKAGE"]
    assert compiled["effective_target_policy"]["summary"]["declared_policy_projects"] == ["PACKAGE"]

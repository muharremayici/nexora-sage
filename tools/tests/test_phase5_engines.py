from pathlib import Path

from tools.engines.confidence_engine import scan_reflection_indicators
from tools.engines.blast_radius_engine import _atlas_dependency_coverage
from tools.engines.local_telemetry_engine import get_hybrid_telemetry_analysis, record_trace
from tools.engines.mcp_governance_engine import _symbol_loc, validate_proposed_patch
from tools.engines import mcp_governance_engine as mcp_governance
from tools.engines.test_impact_matcher import extract_base_name, generate_test_command, is_test_file
from tools.engines.ui_runtime_contract_analyzer import _smoke_plan, _smoke_route_for_target
from tools.core.import_classifier import should_enforce_alias_for_local_import
from tools.core.audit_rules import violates_canonical_alias_boundary
from tools.core import audit_rules, pipeline_policy


def test_ui_smoke_route_requires_explicit_route_evidence() -> None:
    assert _smoke_route_for_target("src/ideation/Editor.tsx", "writing") == "/"

    fallback = _smoke_plan(
        {"target_path": "src/publishing/Panel.tsx", "studio": "publishing", "ui_surface": True},
        "medium",
        [],
    )
    explicit = _smoke_plan(
        {"target_path": "src/feature/Panel.tsx", "studio": "feature", "ui_surface": True},
        "medium",
        [],
        {"smoke_path": "/verified-feature", "source": "framework_route_contract"},
    )

    assert fallback["suggested_route"] == "/"
    assert fallback["route_source"] == "root_fallback_not_target_route"
    assert explicit["suggested_route"] == "/verified-feature"
    assert explicit["route_source"] == "framework_route_contract"


def test_blast_radius_exposes_unavailable_atlas_dependency_coverage():
    evidence = _atlas_dependency_coverage(
        {
            "MAIN": {
                "project": {
                    "sequencer_evidence": {
                        "coverage": {
                            "details": [
                                {
                                    "claim_status": "unavailable",
                                    "status_counts": {
                                        "observed": 0,
                                        "degraded": 0,
                                        "unavailable": 12,
                                    },
                                }
                            ],
                            "warnings": [{"warning": "parser unavailable"}],
                        }
                    }
                }
            }
        }
    )

    assert evidence["status"] == "unavailable"
    assert evidence["status_counts"]["unavailable"] == 12
    assert evidence["warning_count"] == 1
    assert "forbids" in evidence["claim_boundary"]


def test_mcp_governance_uses_runtime_module_root_not_legacy_fallback():
    source = Path(__file__).parents[1] / "engines" / "mcp_governance_engine.py"
    text = source.read_text(encoding="utf-8")
    assert "get_module_root_name()" in text
    assert 'DOCTRINE.get("layer_definitions"' not in text
    assert '"lifecycle-modules"' not in text


def test_mcp_governance_alias_rule_requires_exact_target_alias_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_governance,
        "resolve_runtime_projects",
        lambda root: {"WEB": root},
    )
    monkeypatch.setitem(
        audit_rules.DYNAMIC_CONFIG,
        "_target_root_override",
        {"enabled": True, "observed_path_aliases": []},
    )

    without_alias_evidence = validate_proposed_patch(
        "src/lifecycle-modules/01-ideation/features/demo.tsx",
        "import x from '../../../../platform/core/serviceContainer';\nexport const Demo = () => null;\n",
        workspace_root=tmp_path,
    )
    assert without_alias_evidence["status"] == "PASS"
    assert "relative_imports_no_alias" not in {
        item["rule"] for item in without_alias_evidence["violations"]
    }

    monkeypatch.setitem(
        audit_rules.DYNAMIC_CONFIG,
        "_target_root_override",
        {"enabled": True, "observed_path_aliases": ["@/*"]},
    )
    with_alias_evidence = validate_proposed_patch(
        "src/lifecycle-modules/01-ideation/features/demo.tsx",
        "import x from '../../../../platform/core/serviceContainer';\nexport const Demo = () => null;\n",
        workspace_root=tmp_path,
    )
    assert with_alias_evidence["status"] == "FAIL"
    assert "relative_imports_no_alias" in {
        item["rule"] for item in with_alias_evidence["violations"]
    }


def test_mcp_governance_preserves_safe_sibling_and_barrel_imports():
    sibling = validate_proposed_patch(
        "src/platform/ai/widgets/ProjectAiSettingsModal.tsx",
        "import { AgentCard } from './AgentCard';\nexport const ProjectAiSettingsModal = AgentCard;\n",
    )
    barrel = validate_proposed_patch(
        "src/platform/ai/widgets/index.ts",
        "export { AgentCard } from './AgentCard';\n",
    )

    for result in (sibling, barrel):
        assert "relative_imports_no_alias" not in [item["rule"] for item in result["violations"]]


def test_mcp_governance_uses_exact_target_loc_policy(monkeypatch, tmp_path):
    effective_policy = {
        "projects": {
            "MAIN": {
                "tools": [
                    {
                        "id": "biome",
                        "config_files": [
                            {
                                "path": "biome.json",
                                "sha256": "a" * 64,
                                "static_projection": {
                                    "status": "partial_literal_projection",
                                    "extends_unresolved": False,
                                    "rules_truncated": False,
                                    "rules": [
                                        {
                                            "id": "complexity/noExcessiveLinesPerFunction",
                                            "state": "enforced",
                                            "numeric_options": {"maxLines": 100},
                                            "scope": {"includes": ["src/**/*.tsx"], "excludes": []},
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                ]
            }
        }
    }
    monkeypatch.setitem(pipeline_policy.DYNAMIC_CONFIG, "effective_target_policy", effective_policy)
    monkeypatch.setattr(mcp_governance, "resolve_runtime_projects", lambda _root: {"MAIN": tmp_path})
    monkeypatch.setattr(
        mcp_governance,
        "run_ast_sequencer",
        lambda *_args, **_kwargs: [
            {"type": "Component", "name": "Panel", "line": 1, "endLine": 120, "features": []}
        ],
    )
    monkeypatch.setattr(
        mcp_governance,
        "load_effective_architecture_policy_context",
        lambda _raw_dir: ({}, None),
    )

    result = validate_proposed_patch(
        "src/Panel.tsx",
        "export const Panel = () => null;\n",
        workspace_root=tmp_path,
    )

    loc_findings = [row for row in result["violations"] if row["rule"] == "loc_limits_component"]
    assert len(loc_findings) == 1
    assert loc_findings[0]["limit"] == 100
    assert (
        loc_findings[0]["limit_authority"]
        == "declared_literal_policy_not_native_execution_proof"
    )
    assert loc_findings[0]["target_policy_resolution"]["project"] == "MAIN"
    assert loc_findings[0]["target_policy_resolution"]["file"] == "src/Panel.tsx"


def test_mcp_governance_requires_exact_project_policy_for_architecture_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_governance,
        "resolve_runtime_projects",
        lambda root: {"WEB": root},
    )

    def policy(profile):
        return {
            "meta": {"kind": "effective_architecture_policy", "version": "v1"},
            "invalidation": {"atlas_snapshot_id": "snapshot-a"},
            "projects": {
                "WEB": {
                    "status": "ACTIVE",
                    "policy_activation_allowed": True,
                    "recommended_profile": profile,
                    "feature_flags": {"architecture_sensitive_rules": "enabled"},
                }
            },
        }

    monkeypatch.setattr(
        mcp_governance,
        "load_effective_architecture_policy_context",
        lambda _raw_dir: (policy("NEXTJS_APP_ROUTER"), "snapshot-a"),
    )
    next_result = validate_proposed_patch(
        "src/domain/order.ts",
        "import { Button } from '@/ui/Button';\nexport const order = Button;\n",
        workspace_root=tmp_path,
    )

    monkeypatch.setattr(
        mcp_governance,
        "load_effective_architecture_policy_context",
        lambda _raw_dir: (policy("CLEAN_ARCHITECTURE"), "snapshot-a"),
    )
    clean_result = validate_proposed_patch(
        "src/domain/order.ts",
        "import { Button } from '@/ui/Button';\nexport const order = Button;\n",
        workspace_root=tmp_path,
    )

    self_claimed_next_rules = {row["rule"] for row in next_result["violations"]}
    clean_rules = {row["rule"] for row in clean_result["violations"]}
    assert "domain_ui_leaks" not in self_claimed_next_rules
    assert "domain_ui_leaks" in clean_rules
    assert next_result["architecture_policy_application"] == {
        "authority": "effective_architecture_policy_v1",
        "project": "WEB",
        "effective_policy_status": "ACTIVE",
        "recommended_profile": "NEXTJS_APP_ROUTER",
        "architecture_sensitive_rules_enabled": True,
        "expected_snapshot_id": "snapshot-a",
        "observed_snapshot_id": "snapshot-a",
    }


def test_alias_hygiene_does_not_flag_package_subpath_imports():
    assert should_enforce_alias_for_local_import("./local/Button", "@/")
    assert should_enforce_alias_for_local_import("../platform/service", "@/")
    assert not should_enforce_alias_for_local_import("react-dom/client", "@/")
    assert not should_enforce_alias_for_local_import("zustand/react/shallow", "@/")
    assert not should_enforce_alias_for_local_import("@scope/pkg/subpath", "@/")

    package_subpaths = validate_proposed_patch(
        "src/main.tsx",
        "import ReactDOM from 'react-dom/client';\nimport { shallow } from 'zustand/react/shallow';\nexport const ok = true;\n",
    )
    assert "relative_imports_no_alias" not in [item["rule"] for item in package_subpaths["violations"]]


def test_alias_boundary_scope_preserves_safe_variants_and_flags_boundary_bypass():
    base = {
        "language": "typescript",
        "primary_alias": "@/",
        "module_root_name": "modules",
        "alias_contract_applies": True,
    }
    module_file = "src/modules/billing/ui/Card.tsx"

    assert not violates_canonical_alias_boundary(module_file, "./Button", **base)
    assert not violates_canonical_alias_boundary(module_file, "../model/types", **base)
    assert not violates_canonical_alias_boundary("src/platform/ai/widgets/index.ts", "./AgentCard", **base)
    assert not violates_canonical_alias_boundary(
        "src/platform/ai/widgets/ProjectAiSettingsModal.tsx",
        "./AiSettingsSidebar",
        **base,
    )
    assert violates_canonical_alias_boundary(module_file, "../../other-module/api", **base)
    assert violates_canonical_alias_boundary(module_file, "../../../platform/api", **base)
    assert not violates_canonical_alias_boundary("src/pages/Home.tsx", "./Button", **base)
    assert not violates_canonical_alias_boundary("src/pages/nested/Home.tsx", "../Button", **base)
    assert not violates_canonical_alias_boundary(
        "src/layouts/components/PanelControls.tsx",
        "../types",
        **base,
    )
    assert violates_canonical_alias_boundary(
        "src/layouts/components/PanelControls.tsx",
        "../../platform/api",
        **base,
    )
    assert not violates_canonical_alias_boundary("src/pages/Home.tsx", "./nested/../Button", **base)
    assert violates_canonical_alias_boundary("src/pages/Home.tsx", "./nested/../../platform/api", **base)
    assert not violates_canonical_alias_boundary(module_file, "react-dom/client", **base)
    assert not violates_canonical_alias_boundary(module_file, "../../theme.css", **base)
    assert not violates_canonical_alias_boundary(
        module_file,
        "../../../platform/api",
        **{**base, "alias_contract_applies": False},
    )
    assert not violates_canonical_alias_boundary(
        module_file,
        "../../../platform/api",
        **{**base, "language": "python"},
    )


def test_mcp_governance_rejects_path_escape_targets():
    result = validate_proposed_patch("../outside.py", "print('nope')\n")
    assert result["status"] == "FAIL"
    assert "mcp_path_escape" in [item["rule"] for item in result["violations"]]


def test_mcp_governance_fails_closed_for_unsupported_and_observation_only_languages(tmp_path):
    ruby = validate_proposed_patch(
        "spec/widget_spec.rb",
        "class Widget\n  def call\n    true\n  end\nend\n",
        workspace_root=tmp_path,
    )
    vue = validate_proposed_patch(
        "src/Widget.vue",
        "<template><div>Widget</div></template>\n",
        workspace_root=tmp_path,
    )

    for result, language in ((ruby, "unknown"), (vue, "vue")):
        assert result["status"] == "INCOMPLETE_EVIDENCE"
        assert result["safe_to_apply"] is False
        assert result["unsupported_language"] is True
        assert result["language"] == language
        assert "unsupported_language_extension" in [item["rule"] for item in result["violations"]]


def test_mcp_governance_rejects_no_effect_unified_diff(tmp_path):
    target = tmp_path / "sample.ts"
    target.write_text("export const value = 1;\n", encoding="utf-8")
    result = validate_proposed_patch(
        "sample.ts",
        "--- a/sample.ts\n+++ b/sample.ts\n@@ -1,1 +1,1 @@\n-export const value = 1;\n+export const value = 1;\n",
        workspace_root=tmp_path,
    )
    assert result["status"] == "NO_OP"
    assert result["safe_to_apply"] is False
    assert result["no_op_patch"] is True
    assert "mcp_no_effect_patch" in [item["rule"] for item in result["violations"]]


def test_mcp_governance_uses_audit_rule_mode_and_feature_tag_delta(monkeypatch, tmp_path):
    target = tmp_path / "src" / "service.ts"
    target.parent.mkdir(parents=True)
    target.write_text(
        "import { useProjectStore } from './stores/projectStore';\n"
        "export const current = () => useProjectStore();\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        mcp_governance,
        "build_rule_taxonomy",
        lambda: {"profiles": {"zustand_no_selector": {"mode": "enforced"}}},
    )

    unchanged = validate_proposed_patch(
        "src/service.ts",
        "import { useProjectStore } from './stores/projectStore';\n"
        "export const current = () => useProjectStore();\n"
        "export const note = true;\n",
        workspace_root=tmp_path,
    )

    assert unchanged["status"] == "PASS"
    assert unchanged["introduced_violation_count"] == 0
    assert any(row["rule"] == "zustand_no_selector" for row in unchanged["unchanged_violations"])


def test_mcp_governance_keeps_new_advisory_feature_tag_non_blocking(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_governance,
        "build_rule_taxonomy",
        lambda: {"profiles": {"zustand_no_selector": {"mode": "advisory"}}},
    )

    result = validate_proposed_patch(
        "src/service.ts",
        "import { useProjectStore } from './stores/projectStore';\n"
        "export const current = () => useProjectStore();\n",
        workspace_root=tmp_path,
    )

    assert result["status"] == "PASS"
    assert result["introduced_violation_count"] == 0
    assert result["advisory_violation_count"] == 1
    assert result["advisory_violations"][0]["rule"] == "zustand_no_selector"
    assert result["advisory_violations"][0]["baseline_status"] == "introduced_advisory"


def test_mcp_governance_does_not_treat_imperative_zustand_api_as_hook_subscription(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_governance,
        "build_rule_taxonomy",
        lambda: {"profiles": {"zustand_no_selector": {"mode": "enforced"}}},
    )

    result = validate_proposed_patch(
        "src/service.ts",
        "import { useProjectStore } from './stores/projectStore';\n"
        "export const save = () => useProjectStore.getState().saveFullProject();\n",
        workspace_root=tmp_path,
    )

    assert result["status"] == "PASS"
    assert not any(row["rule"] == "zustand_no_selector" for row in result["violations"])


def test_mcp_governance_rejects_unified_diff_with_stale_source_context(tmp_path):
    target = tmp_path / "sample.ts"
    target.write_text("export const value = 2;\n", encoding="utf-8")

    result = validate_proposed_patch(
        "sample.ts",
        "--- a/sample.ts\n+++ b/sample.ts\n@@ -1,1 +1,1 @@\n-export const value = 1;\n+export const value = 3;\n",
        workspace_root=tmp_path,
    )

    assert result["status"] == "FAIL"
    assert result["invalid_patch"] is True
    assert "mcp_patch_source_mismatch" in [item["rule"] for item in result["violations"]]


def test_mcp_governance_symbol_loc_prefers_line_coordinates():
    symbol = {"start": 194, "end": 601, "line": 9, "endLine": 18}
    assert _symbol_loc(symbol) == 10


def test_test_impact_matcher_polyglot_naming_and_commands():
    assert is_test_file("src/Foo.test.tsx")
    assert is_test_file("tests/test_book_chapter.py")
    assert is_test_file("pkg/book_chapter_test.go")
    assert is_test_file("src/BookChapterTest.java")
    assert extract_base_name("BookChapter.test.tsx") == "BookChapter"
    assert extract_base_name("test_book_chapter.py") == "book_chapter"
    assert generate_test_command("pkg/book_chapter_test.go").startswith("go test")


def test_confidence_engine_detects_dynamic_magic_tokens():
    indicators = scan_reflection_indicators("value = getattr(obj, name)\nmodule = importlib.import_module(name)\n")
    assert any("getattr" in item for item in indicators)
    assert len(indicators) >= 2


def test_local_telemetry_records_and_maps_trace_shape():
    trace = record_trace("component", "MAIN::src/main.tsx", 12)
    assert trace["trace_origin"] == "local"
    analysis = get_hybrid_telemetry_analysis()
    assert "mapped_hotpaths" in analysis
    assert analysis["coverage_gap_label"] == "local_session_coverage_gap"
    assert "trace_origin_summary" in analysis
    assert analysis["total_captured_traces"] >= 1

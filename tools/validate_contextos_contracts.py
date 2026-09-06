from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.doctrine_contract import remediation_action
from tools.core.pipeline_registry import load_pipeline_execution_policy, normalize_step_slug
from tools.core.contextos_mcp import (
    MASKED_BODY,
    build_agent_action_directives,
    build_surgical_operation_packet,
    build_upstream_trace,
    render_surgical_operation_brief,
    render_active_signals,
    safe_signal_body,
)
from tools.engines.quant_engine import _filter_signal_files
from tools.orchestrators.orchestrator import (
    build_step_catalog,
    normalize_changed_file_scope,
    select_steps_smart,
)
from tools.orchestrators.watchdog import CodeMapsHandler


def _check(name: str, passed: bool, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details or {}}


def _step_slugs(step_names: set[str]) -> set[str]:
    return {normalize_step_slug(name) for name in step_names}


def _policy_daily_keep_slugs(policy: dict[str, Any]) -> set[str]:
    profiles = policy.get("execution_profiles", {}) if isinstance(policy.get("execution_profiles"), dict) else {}
    daily = profiles.get("daily", {}) if isinstance(profiles.get("daily"), dict) else {}
    keep = daily.get("keep_slugs", []) if isinstance(daily.get("keep_slugs"), list) else []
    return {normalize_step_slug(item) for item in keep if str(item).strip()}


def _policy_watchdog_skipped_slugs(policy: dict[str, Any]) -> set[str]:
    guidance = policy.get("step_profile_guidance", {}) if isinstance(policy.get("step_profile_guidance"), dict) else {}
    skipped: set[str] = set()
    for slug, config in guidance.items():
        if not isinstance(config, dict):
            continue
        behavior = str(config.get("watchdog_behavior", config.get("daily_behavior", "kept")) or "kept").strip().lower()
        if behavior in {"skipped", "release_deep_only"}:
            skipped.add(normalize_step_slug(slug))
    return skipped


def _resolver(path: str, project_key: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return ROOT / path


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    execution_policy = load_pipeline_execution_policy()
    daily_keep_slugs = _policy_daily_keep_slugs(execution_policy)
    watchdog_skipped_slugs = _policy_watchdog_skipped_slugs(execution_policy)

    handler = CodeMapsHandler(display_root=ROOT / "src")
    shortened = handler._shorten_path(str(ROOT / "src" / "App.tsx"))
    checks.append(
        _check(
            "watchdog_paths_are_repo_relative",
            shortened == "src/App.tsx",
            {"shortened": shortened},
        )
    )
    watchdog_report = handler.render_session_report(
        {
            "meta": {"generated_at": "probe"},
            "summary": {
                "changed_files": 1,
                "elapsed_seconds": 1.0,
                "integrity": "VIOLATIONS",
                "violation_count": 1,
                "mode": "Incremental / cache-aware",
                "interface": "probe",
            },
            "path_contract": {
                "analysis_root": str(ROOT),
                "open_files_with": "analysis_root + changed_files or violations[].repo_relative_path",
                "target_ref_usage": "Use target_ref for SAGE/MCP follow-up references, not as a filesystem path.",
            },
            "changed_files": ["src/App.tsx"],
            "violations": [
                {
                    "repo_relative_path": "src/main.tsx",
                    "target_ref": "MAIN::src/main.tsx",
                    "rule": "relative_imports_no_alias",
                    "detail": "main.tsx imports ../../platform/bootstrap",
                }
            ],
        }
    )
    checks.append(
        _check(
            "watchdog_report_is_agent_path_operational",
            "## Path Contract" in watchdog_report
            and "analysis_root" in watchdog_report
            and "src/main.tsx" in watchdog_report
            and "MAIN::src/main.tsx" in watchdog_report
            and "analysis_root + changed_files or violations[].repo_relative_path" in watchdog_report,
            {"report_excerpt": watchdog_report[:900]},
        )
    )
    watchdog_advisor = handler.generate_agentic_remediation_prompt(
        [
            {
                "file": "main.tsx",
                "repo_relative_path": "src/main.tsx",
                "target_ref": "MAIN::src/main.tsx",
                "rule": "relative_imports_no_alias",
                "detail": "main.tsx imports ../../platform/bootstrap",
                "recommended_action": remediation_action("relative_imports_no_alias"),
            }
        ],
        reverted=False,
    )
    checks.append(
        _check(
            "watchdog_terminal_advisor_is_agent_path_operational",
            "[FILE] src/main.tsx" in watchdog_advisor
            and "[TARGET_REF] MAIN::src/main.tsx" in watchdog_advisor,
            {"advisor_excerpt": watchdog_advisor[:900]},
        )
    )
    pipeline_node = normalize_changed_file_scope([str(ROOT / "src" / "App.tsx")])
    checks.append(
        _check(
            "watchdog_paths_normalize_to_pipeline_nodes",
            pipeline_node == ["MAIN::App.tsx"],
            {"pipeline_node": pipeline_node},
        )
    )
    cwd_relative_node = normalize_changed_file_scope(["../src/App.tsx"])
    checks.append(
        _check(
            "watchdog_cwd_relative_paths_normalize_to_pipeline_nodes",
            cwd_relative_node == ["MAIN::App.tsx"],
            {"pipeline_node": cwd_relative_node},
        )
    )

    class _Args:
        step = None
        from_step = None
        skip_audit = False
        full = True
        force = False
        projects = None
        scope = None
        target = None
        ai_context = False
        smart_trigger = True
        watchdog_profile = "live"

    live_args = _Args()
    catalog = build_step_catalog(live_args, stale_projects=["MAIN"], changed_files=["MAIN::App.tsx"])
    selected_names = {
        step["name"]
        for step in select_steps_smart(
            catalog,
            live_args,
            changed_files=["MAIN::App.tsx"],
            dna_changed_files=["MAIN::App.tsx"],
            should_run_heavy=True,
            atlas={"MAIN": {"files": {"App.tsx": {"features": ["ZustandStore"]}}}},
        )
    }
    selected_slugs = _step_slugs(selected_names)
    unrelated_slugs = _step_slugs({
        step["name"] for step in select_steps_smart(
            catalog, live_args,
            changed_files=["MAIN::App.tsx"],
            dna_changed_files=["MAIN::App.tsx"],
            should_run_heavy=True,
            atlas={"MAIN": {"files": {"App.tsx": {"features": []}}}},
        )
    })
    checks.append(_check(
        "surgical_tsx_without_state_signal_skips_state_flow",
        normalize_step_slug("State Flow Scanner") not in unrelated_slugs,
        {"selected": sorted(unrelated_slugs), "evidence_role": "isolated_atlas_fixture"},
    ))
    required_signal_sensitive_slugs = {normalize_step_slug("State Flow Scanner")}
    checks.append(
        _check(
            "surgical_tsx_pulse_keeps_signal_relevant_state_flow",
            required_signal_sensitive_slugs.issubset(daily_keep_slugs)
            and required_signal_sensitive_slugs.issubset(selected_slugs),
            {
                "missing_from_policy": sorted(required_signal_sensitive_slugs - daily_keep_slugs),
                "missing_from_selected": sorted(required_signal_sensitive_slugs - selected_slugs),
                "selected": sorted(selected_names),
            },
        )
    )
    broad_react_proof_slugs = {
        normalize_step_slug("React Support Matrix"),
        normalize_step_slug("React Capability Probe"),
    }
    checks.append(
        _check(
            "live_watchdog_defers_broad_react_capability_proof",
            broad_react_proof_slugs.issubset(watchdog_skipped_slugs)
            and not broad_react_proof_slugs.intersection(selected_slugs),
            {
                "missing_watchdog_skip_policy": sorted(broad_react_proof_slugs - watchdog_skipped_slugs),
                "unexpected_selected": sorted(broad_react_proof_slugs.intersection(selected_slugs)),
                "selected": sorted(selected_names),
            },
        )
    )
    checks.append(
        _check(
            "surgical_tsx_pulse_does_not_pull_oracle_merge_chain",
            not watchdog_skipped_slugs.intersection(selected_slugs),
            {"unexpected": sorted(watchdog_skipped_slugs.intersection(selected_slugs)), "selected": sorted(selected_names)},
        )
    )
    checks.append(
        _check(
            "live_watchdog_surgical_profile_skips_deep_react_chain",
            not watchdog_skipped_slugs.intersection(selected_slugs),
            {"unexpected": sorted(watchdog_skipped_slugs.intersection(selected_slugs)), "selected": sorted(selected_names)},
        )
    )
    checks.append(
        _check(
            "live_watchdog_surgical_profile_skips_heavy_report_gates",
            not watchdog_skipped_slugs.intersection(selected_slugs),
            {"unexpected": sorted(watchdog_skipped_slugs.intersection(selected_slugs)), "selected": sorted(selected_names)},
        )
    )

    class _DeepArgs(_Args):
        watchdog_profile = "deep"

    deep_args = _DeepArgs()
    deep_catalog = build_step_catalog(deep_args, stale_projects=["MAIN"], changed_files=["MAIN::App.tsx"])
    deep_selected_names = {
        step["name"]
        for step in select_steps_smart(
            deep_catalog,
            deep_args,
            changed_files=["MAIN::App.tsx"],
            dna_changed_files=["MAIN::App.tsx"],
            should_run_heavy=True,
        )
    }
    deep_selected_slugs = _step_slugs(deep_selected_names)
    deep_react_chain_slugs = {
        normalize_step_slug(item)
        for item in (
            "React Ecosystem Analyzer",
            "React Runtime Intelligence",
            "React Compiler Readiness",
            "React Frontier Intelligence",
        )
    }
    checks.append(
        _check(
            "deep_surgical_profile_can_run_full_react_chain",
            deep_react_chain_slugs.issubset(deep_selected_slugs),
            {"missing": sorted(deep_react_chain_slugs - deep_selected_slugs), "selected": sorted(deep_selected_names)},
        )
    )

    generate_atlas_text = (CODE_MAPS_DIR / "tools" / "engines" / "generate_atlas.py").read_text(encoding="utf-8")
    orchestrator_text = (CODE_MAPS_DIR / "tools" / "orchestrators" / "orchestrator.py").read_text(encoding="utf-8")
    checks.append(
        _check(
            "atlas_engine_accepts_surgical_file_scope",
            "def generate_atlas(stale_projects=None, dry_run=False, surgical_files=None)" in generate_atlas_text
            and "Atlas surgical file scope" in generate_atlas_text
            and "Surgically updating atlas" in generate_atlas_text,
            {},
        )
    )
    checks.append(
        _check(
            "watchdog_pipeline_passes_normalized_files_to_atlas",
            "surgical_files=changed_files" in orchestrator_text
            and "changed_files = normalize_changed_file_scope(changed_files_override)" in orchestrator_text,
            {},
        )
    )

    with tempfile.TemporaryDirectory(prefix="nexora_contextos_contract_") as tmp:
        tmp_root = Path(tmp)
        source_file = tmp_root / "Component.tsx"
        archive_file = tmp_root / "payload.zip"
        secret_file = tmp_root / "secret.ts"
        long_file = tmp_root / "Long.ts"
        source_file.write_text("export const Component = () => null;\n", encoding="utf-8")
        archive_file.write_text("not a source file\n", encoding="utf-8")
        secret_file.write_text("export const client_secret = 'hidden';\n", encoding="utf-8")
        long_file.write_text(
            "\n".join(
                [
                    "export const first = 'safe';",
                    "export const second = 'also-safe';",
                    "export const third = '" + ("x" * 64) + "';",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        accepted, rejected = _filter_signal_files([str(source_file), str(archive_file)])
        checks.append(
            _check(
                "quant_engine_filters_to_source_or_config_files",
                len(accepted) == 1
                and accepted[0]["kind"] == "source"
                and accepted[0]["path"].endswith("Component.tsx")
                and rejected["unsupported_kind"] == 1,
                {"accepted": accepted, "rejected": rejected},
            )
        )

        masked = safe_signal_body(str(secret_file), "MAIN", 2000, _resolver)
        checks.append(
            _check(
                "contextos_body_reader_masks_secret_like_content",
                masked["masked"] is True and masked["body"] == MASKED_BODY,
                {"masked": masked.get("masked"), "body": masked.get("body")},
            )
        )

        truncated = safe_signal_body(str(long_file), "MAIN", 32, _resolver)
        checks.append(
            _check(
                "contextos_body_reader_enforces_line_boundary_character_budget",
                truncated["truncated"] is True
                and "[TRUNCATED]" in truncated["body"]
                and "line boundary" in truncated["body"]
                and "export const second" not in truncated["body"],
                {"truncated": truncated.get("truncated"), "body": truncated.get("body")},
            )
        )

    sample = {
        "meta": {"kind": "contextos_signals"},
        "summary": {"accepted_files": 2},
        "active_signals": [
            {
                "node_key": "MAIN::src/App.tsx",
                "relative_path": "src/App.tsx",
                "target_ref": "MAIN::src/App.tsx",
                "signal_kind": "source",
                "impact_score": None,
                "impact_score_status": "deferred_broad_proof",
                "direct_dependents_omitted": 0,
                "transitive_dependents_omitted": 0,
                "direct_dependents": ["MAIN::src/main.tsx"],
                "focus_halo": [
                    {
                        "node_key": "MAIN::src/main.tsx",
                        "relative_path": "src/main.tsx",
                        "halo_rank": 1,
                        "impact_score": 2.0,
                        "reason": "direct_dependent_of_active_focus",
                    }
                ],
                "reasoning_breadcrumbs": ["`src/App.tsx` is hot because it was observed by ContextOS."],
                "signal_actionability": {
                    "lane": "localized_review",
                    "priority": "low",
                    "reason": "Fresh Atlas reachability is available; broad proof is deferred.",
                },
                "active_violations": [],
                "active_violations_omitted": 0,
                "circular_cycles": [],
                "circular_cycles_omitted": 0,
                "circular_cycles_status": "deferred_broad_proof",
                "risk_claim_boundary": "scoped_dependency_and_active_violation_context",
            },
            {
                "node_key": "MAIN::config/language_registry.json",
                "relative_path": "config/language_registry.json",
                "target_ref": "MAIN::config/language_registry.json",
                "signal_kind": "config",
                "impact_score": 0.5,
                "impact_score_status": "available",
                "direct_dependents_omitted": 0,
                "transitive_dependents_omitted": 0,
                "signal_actionability": {},
                "active_violations_omitted": 0,
                "circular_cycles_omitted": 0,
                "circular_cycles_status": "available",
                "risk_claim_boundary": "broad_artifact_risk_projection",
            },
        ],
    }
    rendered_json = json.loads(
        render_active_signals(
            sample,
            output_format="json",
            include_bodies=True,
            max_files=1,
            max_chars_per_file=128,
            scope="full",
            resolve_absolute_path=_resolver,
        )
    )
    checks.append(
        _check(
            "contextos_json_mode_is_summary_first_and_bodyless",
            rendered_json["changed_files_count"] == 2
            and rendered_json["returned_l1_count"] == 1
            and rendered_json["target_refs"] == ["MAIN::src/App.tsx"]
            and rendered_json["active_signals"][0]["impact_score_status"] == "deferred_broad_proof"
            and rendered_json["active_signals"][0]["circular_cycles_status"] == "deferred_broad_proof"
            and rendered_json["active_signals"][0]["risk_claim_boundary"]
            == "scoped_dependency_and_active_violation_context"
            and "body" not in json.dumps(rendered_json),
            {
                "changed_files_count": rendered_json.get("changed_files_count"),
                "returned_l1_count": rendered_json.get("returned_l1_count"),
            },
        )
    )
    rendered_summary_markdown = render_active_signals(
        sample,
        output_format="markdown",
        include_bodies=False,
        max_files=2,
        max_chars_per_file=128,
        scope="summary",
        resolve_absolute_path=_resolver,
    )
    checks.append(
        _check(
            "contextos_summary_scope_lists_agent_target_files",
            "## Agent Focus Packet" in rendered_summary_markdown
            and "target_files:" in rendered_summary_markdown
            and "src/App.tsx" in rendered_summary_markdown
            and "target_refs:" in rendered_summary_markdown
            and "MAIN::src/App.tsx" in rendered_summary_markdown
            and "analysis_root + target_files" in rendered_summary_markdown,
            {"summary_excerpt": rendered_summary_markdown[:900]},
        )
    )

    rendered_markdown = render_active_signals(
        sample,
        output_format="markdown",
        include_bodies=False,
        max_files=2,
        max_chars_per_file=128,
        scope="full",
        resolve_absolute_path=_resolver,
    )
    checks.append(
        _check(
            "contextos_markdown_includes_halo_and_breadcrumbs",
            "Focus Halo" in rendered_markdown
            and "Reasoning Breadcrumbs" in rendered_markdown
            and "Circular Dependency Proof:** deferred" in rendered_markdown
            and "Circular Dependency Cycles:** None" not in rendered_markdown,
            {},
        )
    )
    signal_directives = build_agent_action_directives(sample, audit_report={}, quality_gate={}, max_items=1)
    signal_directive = signal_directives[0] if signal_directives else {}
    checks.append(
        _check(
            "watchdog_signal_directive_preserves_deferred_proof_boundary",
            signal_directive.get("intent") == "inspect_hot_file_and_dependency_halo"
            and signal_directive.get("evidence_boundary", {}).get("impact_score_status") == "deferred_broad_proof"
            and signal_directive.get("evidence_boundary", {}).get("circular_cycles_status") == "deferred_broad_proof"
            and "output/.raw/watchdog_audit_report.json" in signal_directive.get("source_artifacts", [])
            and "output/.raw/blast_radius.json" not in signal_directive.get("source_artifacts", []),
            {"directive": signal_directive},
        )
    )

    graph = {
        "edges": [
            {"source": "MAIN::src/main.tsx", "target": "MAIN::src/App.tsx"},
            {"source": "MAIN::src/App.tsx", "target": "MAIN::src/lib/auth.ts"},
        ]
    }
    sample_audit = {
        "rule_taxonomy": {
            "profiles": {
                "hexagonal_layer_violation": {
                    "rule": "hexagonal_layer_violation",
                    "label": "Hexagonal Layer Violation",
                    "mode": "enforced",
                    "layer": "architectural",
                    "rationale": "Infrastructure must not bypass the application boundary.",
                }
            }
        },
        "violations": [
            {
                "file": "src/infra/db.ts",
                "rule": "hexagonal_layer_violation",
                "detail": "infra/db.ts imports domain internals directly.",
            }
        ],
    }
    trace = build_upstream_trace("MAIN::src/main.tsx", graph, sample)
    checks.append(
        _check(
            "contextos_reverse_trace_links_active_source_to_failing_target",
            bool(trace["candidate_changed_sources"])
            and trace["candidate_changed_sources"][0]["source_node"] == "MAIN::src/App.tsx",
            {"trace": trace},
        )
    )

    packet = build_surgical_operation_packet(
        sample,
        circular_deps_data=graph,
        audit_report=sample_audit,
    )
    checks.append(
        _check(
            "contextos_surgical_operation_packet_has_l1_l2_and_steps",
            packet["summary"]["returned_focus_files"] == 2
            and packet["summary"]["returned_halo_files"] == 1
            and packet["recommended_next_steps"],
            {"summary": packet.get("summary")},
        )
    )
    checks.append(
        _check(
            "contextos_target_packet_excludes_sage_fixture_validation",
            "react_edge_case_gate" not in packet.get("summary", {}),
            {"summary_keys": sorted(packet.get("summary", {}))},
        )
    )
    directives = packet.get("agent_action_directives", [])
    first_directive = directives[0] if directives and isinstance(directives[0], dict) else {}
    checks.append(
        _check(
            "contextos_surgical_packet_has_patch_oriented_agent_directives",
            bool(directives)
            and first_directive.get("target_files") == ["src/infra/db.ts"]
            and first_directive.get("rule") == "hexagonal_layer_violation"
            and first_directive.get("rule_explanation", {}).get("label") == "Hexagonal Layer Violation"
            and first_directive.get("action")
            and first_directive.get("validation_tools")
            and first_directive.get("source_artifacts"),
            {"directive": first_directive},
        )
    )
    default_brief = render_surgical_operation_brief(packet, debug=False)
    debug_brief = render_surgical_operation_brief(packet, debug=True)
    checks.append(
        _check(
            "contextos_default_brief_hides_internal_debug_refs",
            "debug_internal_refs" not in default_brief
            and "atlas_nodes:" not in default_brief
            and "file_context:" not in default_brief
            and "source_artifacts:" not in default_brief
            and "debug_internal_refs:" in debug_brief
            and "atlas_nodes:" in debug_brief,
            {
                "default_contains_debug_internal_refs": "debug_internal_refs" in default_brief,
                "debug_contains_debug_internal_refs": "debug_internal_refs:" in debug_brief,
            },
        )
    )
    capability_ids = {item.get("id") for item in packet.get("relevant_capabilities", []) if isinstance(item, dict)}
    checks.append(
        _check(
            "contextos_surgical_operation_packet_has_relevant_capability_contracts",
            {"contextos", "dependency_health", "test_impact"}.issubset(capability_ids)
            and all(item.get("claim_boundary") for item in packet.get("relevant_capabilities", []) if isinstance(item, dict)),
            {"capability_ids": sorted(capability_ids)},
        )
    )
    architecture_context = packet.get("architecture_governance_context", {})
    checks.append(
        _check(
            "contextos_surgical_packet_exposes_compact_blueprint_and_seal_truth",
            isinstance(architecture_context, dict)
            and architecture_context.get("seal_state") in {"PROPOSAL_ONLY", "HUMAN_SEALED"}
            and architecture_context.get("mcp_tool") == "get_architecture_oracle"
            and len(architecture_context.get("projects", [])) <= 3
            and "architecture_doctrine.json" in " ".join(architecture_context.get("source_artifacts", [])),
            architecture_context,
        )
    )

    server_text = (CODE_MAPS_DIR / "tools" / "mcp" / "server.py").read_text(encoding="utf-8")
    checks.append(
        _check(
            "mcp_get_active_signals_delegates_to_contextos_module",
            "from tools.core.contextos_mcp import render_active_signals" in server_text
            and "_sanitize_signal" not in server_text
            and "_safe_signal_body" not in server_text,
            {},
        )
    )
    checks.append(
        _check(
            "mcp_exposes_contextos_operation_tools",
            "def trace_upstream_cause" in server_text and "def get_surgical_operation_packet" in server_text,
            {},
        )
    )

    payload = {
        "meta": {"kind": "contextos_contract_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "contextos_contract_validation.json", payload)
    lines = [
        "# ContextOS Contract Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} |")
    save_text_atomic(REPORTS_DIR / "contextos_contract_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tools.core.capability_registry import load_capability_registry, summarize_capabilities

CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
PIPELINE_EXECUTION_POLICY_PATH = CODE_MAPS_DIR / "config" / "pipeline_execution_policy.json"


def catalog_args_from_execution_policy(policy: dict[str, Any]) -> SimpleNamespace:
    validators = (
        policy.get("validator_preconditions", {}).get("validators", {})
        if isinstance(policy.get("validator_preconditions"), dict)
        else {}
    )
    contract = validators.get("validate_pipeline_execution_contract", {}) if isinstance(validators, dict) else {}
    args = contract.get("registry_fallback_catalog_args", {}) if isinstance(contract, dict) else {}
    args = args if isinstance(args, dict) else {}
    return SimpleNamespace(
        step=args.get("step"),
        from_step=args.get("from_step"),
        skip_audit=bool(args.get("skip_audit")),
        full=bool(args.get("full")),
        force=bool(args.get("force")),
        projects=args.get("projects"),
        scope=args.get("scope"),
        ai_context=bool(args.get("ai_context")),
        smart_trigger=bool(args.get("smart_trigger")),
        profile=str(args.get("profile") or ""),
    )


def validator_execution_contract(validator_id: str) -> dict[str, Any]:
    policy = load_pipeline_execution_policy()
    preconditions = policy.get("validator_preconditions", {}) if isinstance(policy, dict) else {}
    validators = preconditions.get("validators", {}) if isinstance(preconditions, dict) else {}
    row = validators.get(str(validator_id or ""), {}) if isinstance(validators, dict) else {}
    return row if isinstance(row, dict) else {}


ARTIFACT_OWNERSHIP: dict[str, dict[str, list[str]]] = {
    "Atlas": {
        "reads": ["atlas", "external_target_preflight", "discovery"],
        "writes": ["atlas", "atlas_commit", "workload_profile", "analysis_scope_authority", "analysis_scope_authority_analysis_snapshot_lineage"],
    },
    "Project DNA Profile": {"reads": ["atlas", "discovery", "package_json"], "writes": ["project_dna_profile"]},
    "Nuclear Sequencing": {
        "reads": ["atlas", "atlas_commit", "genome"],
        "writes": ["genome", "genome_analysis_snapshot_lineage", "surgical_discovery", "surgical_discovery_analysis_snapshot_lineage"],
    },
    "Architecture Oracle": {
        "reads": [
            "atlas",
            "atlas_commit",
            "discovery",
            "analysis_scope_authority",
            "analysis_scope_authority_analysis_snapshot_lineage",
        ],
        "writes": ["architecture_oracle", "effective_architecture_policy"],
    },
    "Fractal Mapping": {
        "reads": ["atlas", "atlas_commit", "genome", "genome_analysis_snapshot_lineage", "surgical_discovery", "surgical_discovery_analysis_snapshot_lineage", "fractal_map"],
        "writes": ["fractal_map", "fractal_map_analysis_snapshot_lineage"],
    },
    "Decision Evidence": {"reads": ["fractal_map", "genome"], "writes": ["decision_evidence"]},
    "Keyword Stats": {
        "reads": ["atlas", "atlas_commit", "genome", "keyword_scanner_all", "keyword_scanner_cache_meta"],
        "writes": ["keyword_scanner_stats"],
    },
    "Keyword Scanner": {
        "reads": ["atlas", "atlas_commit", "genome", "keyword_scanner_all", "keyword_scanner_cache_meta"],
        "writes": ["keyword_scanner_keywords"],
    },
    "Gem Scorer": {
        "reads": [
            "atlas",
            "atlas_commit",
            "genome",
            "keyword_scanner_stats",
            "keyword_scanner_keywords",
            "keyword_scanner_all",
            "keyword_scanner_cache_meta",
        ],
        "writes": ["keyword_scanner_all", "keyword_scanner_cache_meta"],
    },
    "UI Mapper": {"reads": ["atlas", "ui_architecture_map"], "writes": ["ui_architecture_map"]},
    "Adapter Registry": {"reads": ["codemaps_adapters"], "writes": ["adapter_registry"]},
    "Framework Route Analyzer": {
        "reads": ["atlas", "atlas_commit", "package_json", "atlas_bound_source"],
        "writes": ["framework_routes", "framework_routes_analysis_snapshot_lineage"],
    },
    "Landscape Mapper": {"reads": ["atlas", "landscape_map"], "writes": ["landscape_map"]},
    "Dead Code Detector": {"reads": ["atlas"], "writes": ["dead_code", "dead_code_tuning"]},
    "Circular Dependency Finder": {"reads": ["atlas"], "writes": ["circular_deps"]},
    "Audit": {
        "reads": ["atlas", "analysis_scope_authority", "effective_architecture_policy"],
        "writes": ["audit_report", "watchdog_audit_report"],
        "display_name": "Architectural Audit",
    },
    "AST Structural Diff": {"reads": ["genome"], "writes": ["nanometric_diff"]},
    "Variation Engine": {"reads": ["atlas"], "writes": ["variation_analysis"]},
    "Test-Impact Matcher": {"reads": ["atlas", "circular_deps"], "writes": ["test_impact_report"]},
    "Confidence Engine": {"reads": ["atlas", "circular_deps"], "writes": ["confidence_risk_report"]},
    "Local Dev Telemetry": {"reads": ["atlas", "telemetry_traces"], "writes": ["local_telemetry_analysis"]},
    "Hexagonal Port-Adapter Binder": {"reads": ["atlas", "hexagonal_bindings"], "writes": ["hexagonal_bindings"]},
    "State Flow Scanner": {"reads": ["atlas", "genome", "state_flow"], "writes": ["state_flow"]},
    "React Support Matrix": {
        "reads": ["atlas", "atlas_commit", "discovery", "package_json", "project_dna_profile", "react_support_matrix", "state_flow"],
        "writes": ["react_support_matrix", "react_support_validation"],
    },
    "Semantic Clone Detector": {"reads": ["genome"], "writes": ["clone_detector"]},
    "Auto-Merge Script Generator": {
        "reads": ["fractal_map", "audit_report", "genome", "blast_radius"],
        "writes": ["merge_plan_summary", "generated_merge_scripts"],
    },
    "Oracle Validation Gate": {
        "reads": ["workload_profile", "atlas", "analysis_scope_authority"],
        "writes": ["oracle_validation_reports", "validation_oracle_scope"],
    },
    "Blast Radius Engine": {"reads": ["atlas", "circular_deps"], "writes": ["blast_radius"]},
    "Self-Healing Generator": {"reads": ["audit_report"], "writes": ["generated_self_healing_scripts"]},
    "Health Score": {
        "reads": ["audit_report", "dead_code", "circular_deps", "genome", "surgical_discovery", "keyword_scanner_all"],
        "writes": ["health_score"],
    },
    "Module Risk Matrix": {
        "reads": ["audit_report", "dead_code", "surgical_discovery", "circular_deps"],
        "writes": ["module_risk_matrix"],
    },
    "Temporal Diff": {"reads": ["health_score", "dead_code", "circular_deps", "audit_report", "module_risk_matrix", "genome"], "writes": ["temporal_diff"]},
    "Host Merge Intelligence": {
        "reads": ["atlas", "atlas_commit", "fractal_map", "fractal_map_analysis_snapshot_lineage"],
        "writes": ["host_merge_intelligence", "host_merge_intelligence_analysis_snapshot_lineage"],
    },
    "UI Runtime Contract Analyzer": {
        "reads": ["atlas", "atlas_commit", "atlas_bound_source", "framework_routes", "framework_routes_analysis_snapshot_lineage", "host_merge_intelligence", "host_merge_intelligence_analysis_snapshot_lineage"],
        "writes": ["ui_runtime_contracts", "ui_runtime_contracts_analysis_snapshot_lineage"],
    },
    "UI Smoke Spec Generator": {
        "reads": ["atlas", "atlas_commit", "ui_runtime_contracts", "ui_runtime_contracts_analysis_snapshot_lineage"],
        "writes": ["ui_smoke_specs", "ui_smoke_specs_analysis_snapshot_lineage"],
    },
    "UI Smoke Execution Readiness": {
        "reads": ["atlas", "atlas_commit", "ui_smoke_specs", "ui_smoke_specs_analysis_snapshot_lineage"],
        "writes": ["ui_smoke_execution", "ui_smoke_execution_analysis_snapshot_lineage"],
    },
    "Next Boundary Analyzer": {"reads": ["atlas", "atlas_bound_source"], "writes": ["next_boundary_analysis"]},
    "State/Data Graph Analyzer": {"reads": ["state_flow", "atlas"], "writes": ["state_data_graph"]},
    "A11y/i18n Contract Analyzer": {"reads": ["atlas", "atlas_bound_source"], "writes": ["a11y_i18n_contracts"]},
    "React Ecosystem Analyzer": {"reads": ["atlas", "atlas_bound_source"], "writes": ["react_ecosystem_analysis"]},
    "React Runtime Intelligence": {
        "reads": ["atlas", "atlas_bound_source", "react_ecosystem_analysis", "ui_smoke_execution"],
        "writes": ["react_runtime_intelligence"],
    },
    "React Compiler Readiness": {"reads": ["react_runtime_intelligence", "react_ecosystem_analysis"], "writes": ["react_compiler_readiness"]},
    "React Frontier Intelligence": {
        "reads": ["atlas", "source_snapshot_store", "ts_diagnostics"],
        "writes": ["react_frontier_intelligence", "react_frontier_evidence_readiness"],
    },
    "Merge Dependency Packager": {
        "reads": ["atlas", "atlas_commit", "ui_runtime_contracts", "ui_runtime_contracts_analysis_snapshot_lineage"],
        "writes": ["merge_dependency_packages", "merge_dependency_packages_analysis_snapshot_lineage"],
    },
    "Merge Simulation Engine": {
        "reads": ["atlas", "atlas_commit", "merge_dependency_packages", "merge_dependency_packages_analysis_snapshot_lineage", "ui_smoke_execution", "ui_smoke_execution_analysis_snapshot_lineage", "ts_diagnostics"],
        "writes": ["merge_simulation", "merge_simulation_analysis_snapshot_lineage"],
    },
    "Merge Decision Cockpit": {
        "reads": ["atlas", "atlas_commit", "merge_simulation", "merge_simulation_analysis_snapshot_lineage", "ui_runtime_contracts", "ui_runtime_contracts_analysis_snapshot_lineage", "merge_dependency_packages", "merge_dependency_packages_analysis_snapshot_lineage", "ui_smoke_specs", "ui_smoke_specs_analysis_snapshot_lineage"],
        "writes": ["merge_decision_cockpit", "merge_decision_cockpit_analysis_snapshot_lineage"],
    },
    "AI Task Pack Generator": {"reads": ["merge_decision_cockpit"], "writes": ["ai_task_packs"]},
    "Merge Intelligence Regression": {"reads": ["framework_routes", "ui_runtime_contracts", "ui_smoke_specs", "merge_dependency_packages", "merge_simulation", "merge_decision_cockpit", "ai_task_packs"], "writes": ["merge_intelligence_regression"]},
    "Distribution Hardening": {"reads": [], "writes": ["distribution_hardening_validation"]},
    "Entrypoint Failure Drills": {"reads": ["atlas", "health_score", "quality_gate"], "writes": ["entrypoint_failure_validation"]},
    "Scoped Host Analyzer": {"reads": ["atlas", "fractal_map", "audit_report", "genome"], "writes": ["scoped_host_analysis"]},
    "React Capability Probe": {"reads": ["package_json", "discovery"], "writes": ["react_capability_probe"]},
    "Proof Obligations": {
        "reads": [
            "atlas",
            "genome",
            "health_score",
            "dead_code",
            "circular_deps",
            "audit_report",
            "react_support_matrix",
            "state_flow",
            "project_dna_profile",
            "oracle_validation_reports",
        ],
        "writes": ["proof_obligations"],
    },
    "Quality Review Oracle": {
        "reads": [
            "atlas",
            "genome",
            "proof_obligations",
            "health_score",
            "dead_code",
            "circular_deps",
            "react_support_matrix",
            "state_flow",
            "oracle_validation_reports",
        ],
        "writes": ["quality_review"],
    },
    "Quality Gates": {
        "reads": [
            "*",
            "analysis_scope_authority",
            "atlas",
            "genome",
            "audit_report",
            "fractal_map",
            "state_flow",
            "blast_radius",
            "react_support_matrix",
            "project_dna_profile",
            "dead_code",
            "ui_runtime_contracts",
            "merge_dependency_packages",
            "merge_simulation",
            "merge_decision_cockpit",
            "quality_review",
            "proof_obligations",
            "health_score",
        ],
        "writes": ["quality_gate"],
    },
    "Live Surface Analyzer": {
        "reads": ["atlas", "source_snapshot_store", "dead_code", "clone_detector", "oracle_validation_reports"],
        "writes": ["live_surface_findings", "live_surface_priority_pack"],
    },
    "Release Readiness": {
        "reads": [
            "*",
            "atlas",
            "genome",
            "quality_gate",
            "analysis_scope_authority",
            "merge_intelligence_regression",
            "adapter_registry",
            "distribution_hardening_validation",
            "entrypoint_failure_validation",
            "operational_parity_validation",
            "performance_budget_validation",
            "performance_ledger",
            "react_fixture_matrix_validation",
            "react_universal_readiness",
            "universal_proof_validation",
            "mcp_agent_surface_validation",
            "merge_decision_cockpit",
            "ai_task_packs",
            "codemaps_suppressions",
            "release_identity",
        ],
        "writes": ["release_readiness"],
    },
    "Nexora Operator Packet": {
        "reads": [
            "analysis_scope_authority",
            "nexora_brief",
            "nexora_agent_contract",
            "watchdog_session",
            "mcp_agent_surface_validation",
            "report_freshness_index",
            "hitl_approval_ledger",
            "hitl_decision_requests",
            "hitl_governance_validation",
            "hitl_lifecycle_smoke",
            "nexora_agent_response_validation",
            "nexora_agent_response_ledger",
            "nexora_agent_handoff",
            "pipeline_execution_contract_validation",
            "engine_signal_contract_validation",
            "architecture_oracle",
            "signals",
            "audit_report",
            "quality_gate",
            "capability_registry",
            "capability_activation_plan",
        ],
        "writes": ["nexora_operator_packet"],
    },
    "Artifact Contract Validation": {"reads": ["*"], "writes": ["artifact_contract_validation"]},
    "Master Report Generation": {
        "reads": [
            "analysis_scope_authority",
            "quality_gate",
            "release_readiness",
            "health_score",
            "keyword_scanner_all",
            "surgical_discovery",
            "audit_report",
            "dead_code",
        ],
        "writes": ["master_report"],
    },
    "AI Context Generator": {
        "reads": ["*", "analysis_scope_authority", "quality_gate", "nexora_operator_packet", "signals"],
        "writes": ["ai_context"],
    },
    "Surgical Safety Gate": {"reads": ["module_risk_matrix", "genome", "host_merge_intelligence"], "writes": ["surgical_readiness"]},
    "CMO Dashboard": {
        "reads": ["decision_evidence", "host_merge_intelligence", "health_score", "blast_radius", "architecture_doctrine"],
        "writes": ["cmo_dashboard"],
    },
    "Closure Walker": {"reads": ["genome"], "writes": ["closure_walker_readiness"]},
    "ContextOS Quant Engine": {
        "reads": ["atlas", "watchdog_audit_report", "watchdog_session", "blast_radius", "circular_deps", "audit_report"],
        "writes": ["signals", "watchdog_graph_advisories"],
    },
}


STEP_MODULE_INVOCATIONS: dict[str, str] = {
    "Project DNA Profile": "tools.engines.project_dna_profiler",
    "Nuclear Sequencing": "tools.engines.nuclear_processor",
    "Architecture Oracle": "tools.engines.architecture_oracle",
    "Fractal Mapping": "tools.engines.fractal_mapper",
    "Decision Evidence": "tools.engines.decision_evidence",
    "Keyword Stats": "tools.engines.keyword_scanner",
    "Keyword Scanner": "tools.engines.keyword_scanner",
    "Gem Scorer": "tools.engines.keyword_scanner",
    "UI Mapper": "tools.engines.ui_mapper",
    "Adapter Registry": "tools.engines.adapter_registry_report",
    "Framework Route Analyzer": "tools.engines.framework_route_analyzer",
    "Landscape Mapper": "tools.engines.landscape_mapper",
    "Dead Code Detector": "tools.engines.dead_code_detector",
    "Circular Dependency Finder": "tools.engines.circular_dependency_finder",
    "Audit": "tools.engines.audit",
    "AST Structural Diff": "tools.engines.nanometric_diff_engine",
    "Variation Engine": "tools.engines.variation_manager",
    "Test-Impact Matcher": "tools.engines.test_impact_matcher",
    "Confidence Engine": "tools.engines.confidence_engine",
    "Local Dev Telemetry": "tools.engines.local_telemetry_engine",
    "Hexagonal Port-Adapter Binder": "tools.engines.hexagonal_binder",
    "State Flow Scanner": "tools.engines.state_flow_scanner",
    "React Support Matrix": "tools.validate_react_support",
    "Semantic Clone Detector": "tools.engines.clone_detector",
    "Auto-Merge Script Generator": "tools.engines.merge_script_generator",
    "Oracle Validation Gate": "tools.engines.validation_oracle",
    "Blast Radius Engine": "tools.engines.blast_radius_engine",
    "Self-Healing Generator": "tools.engines.self_healing_generator",
    "Health Score": "tools.engines.health_score",
    "Module Risk Matrix": "tools.engines.module_risk_matrix",
    "Temporal Diff": "tools.engines.temporal_diff",
    "Host Merge Intelligence": "tools.engines.host_merge_intelligence",
    "UI Runtime Contract Analyzer": "tools.engines.ui_runtime_contract_analyzer",
    "UI Smoke Spec Generator": "tools.engines.ui_smoke_spec_generator",
    "UI Smoke Execution Readiness": "tools.engines.ui_smoke_execution_report",
    "Next Boundary Analyzer": "tools.engines.next_boundary_analyzer",
    "State/Data Graph Analyzer": "tools.engines.state_data_graph_analyzer",
    "A11y/i18n Contract Analyzer": "tools.engines.a11y_i18n_contract_analyzer",
    "React Ecosystem Analyzer": "tools.engines.react_ecosystem_analyzer",
    "React Runtime Intelligence": "tools.engines.react_runtime_intelligence",
    "React Compiler Readiness": "tools.engines.react_compiler_readiness",
    "React Frontier Intelligence": "tools.engines.react_frontier_intelligence",
    "Merge Dependency Packager": "tools.engines.merge_dependency_packager",
    "Merge Simulation Engine": "tools.engines.merge_simulation_engine",
    "Merge Decision Cockpit": "tools.engines.merge_decision_cockpit",
    "AI Task Pack Generator": "tools.engines.ai_task_pack_generator",
    "Merge Intelligence Regression": "tools.validate_merge_intelligence_regression",
    "Distribution Hardening": "tools.validate_distribution_hardening",
    "Entrypoint Failure Drills": "tools.validate_entrypoints_and_failures",
    "Scoped Host Analyzer": "tools.engines.scoped_host_analyzer",
    "React Capability Probe": "tools.validate_react_support",
    "Proof Obligations": "tools.engines.proof_obligations_engine",
    "Quality Review Oracle": "tools.engines.quality_review_engine",
    "Quality Gates": "tools.engines.quality_gate",
    "Live Surface Analyzer": "tools.engines.live_surface_analyzer",
    "Release Readiness": "tools.engines.release_readiness_report",
    "Nexora Operator Packet": "tools.generate_nexora_operator_packet",
    "Artifact Contract Validation": "tools.engines.artifact_contract_validator",
    "Master Report Generation": "tools.engines.master_report_generator",
    "AI Context Generator": "tools.engines.ai_context_generator",
    "Surgical Safety Gate": "tools.engines.safety_gate_calculator",
    "CMO Dashboard": "tools.engines.cmo_dashboard",
    "Closure Walker": "tools.engines.closure_walker",
    "ContextOS Quant Engine": "tools.engines.quant_engine",
}

STEP_DIAGNOSTIC_COMMANDS: dict[str, str] = {
    "Keyword Stats": "python -m tools.engines.keyword_scanner --mode stats",
    "Keyword Scanner": "python -m tools.engines.keyword_scanner --mode keywords",
    "Gem Scorer": "python -m tools.engines.keyword_scanner --mode gems",
}


def normalize_step_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def load_pipeline_execution_policy() -> dict[str, Any]:
    if not PIPELINE_EXECUTION_POLICY_PATH.exists():
        return {}
    payload = json.loads(PIPELINE_EXECUTION_POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("pipeline execution policy must be a JSON object")
    return payload


def pipeline_lock_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    source = policy if isinstance(policy, dict) else load_pipeline_execution_policy()
    contract = source.get("pipeline_lock", {}) if isinstance(source, dict) else {}
    return contract if isinstance(contract, dict) else {}


def system_scope_contract_for_step(
    step: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one step's declared subject scopes from the central policy."""
    source = policy if isinstance(policy, dict) else load_pipeline_execution_policy()
    scope_policy = source.get("step_system_scope_policy", {}) if isinstance(source, dict) else {}
    groups = scope_policy.get("groups", {}) if isinstance(scope_policy, dict) else {}
    slug = normalize_step_slug(step.get("name"))
    matches = [
        (str(group_id), group)
        for group_id, group in groups.items()
        if isinstance(group, dict) and slug in {str(item) for item in group.get("step_slugs", []) or []}
    ] if isinstance(groups, dict) else []
    if len(matches) != 1:
        return {
            "classification": "unclassified" if not matches else "ambiguous",
            "group": "",
            "system_scopes": [],
            "allowed": False,
            "reason": str(scope_policy.get("unclassified_behavior") or "fail_closed"),
        }
    group_id, group = matches[0]
    return {
        "classification": "declared",
        "group": group_id,
        "system_scopes": [str(item) for item in group.get("system_scopes", []) or []],
        "allowed": True,
        "reason": str(group.get("rationale") or ""),
    }


def filter_catalog_for_system_scope(
    catalog: list[dict[str, Any]],
    system_scope: str,
    policy: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fail closed for unclassified or wrong-subject pipeline steps."""
    source = policy if isinstance(policy, dict) else load_pipeline_execution_policy()
    allowed: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for step in catalog:
        contract = system_scope_contract_for_step(step, source)
        if contract.get("classification") == "declared" and system_scope in contract.get("system_scopes", []):
            scoped_step = dict(step)
            scope_dependencies = step.get("scope_dependencies", {})
            scoped_dependencies = (
                scope_dependencies.get(system_scope, [])
                if isinstance(scope_dependencies, dict)
                else []
            )
            scoped_step["depends_on"] = list(
                dict.fromkeys(
                    [
                        *list(step.get("depends_on", []) or []),
                        *[str(item) for item in scoped_dependencies or []],
                    ]
                )
            )
            allowed.append(scoped_step)
        else:
            excluded.append(
                {
                    "name": str(step.get("name") or ""),
                    "slug": normalize_step_slug(step.get("name")),
                    "classification": str(contract.get("classification") or "unknown"),
                    "group": str(contract.get("group") or ""),
                }
            )
    invalid_dependencies: list[dict[str, Any]] = []
    while True:
        allowed_names = {str(step.get("name") or "") for step in allowed}
        current_invalid = [
            {
                "name": str(step.get("name") or ""),
                "excluded_dependencies": sorted(
                    str(dep)
                    for dep in step.get("depends_on", []) or []
                    if str(dep) not in allowed_names
                ),
            }
            for step in allowed
            if any(str(dep) not in allowed_names for dep in step.get("depends_on", []) or [])
        ]
        if not current_invalid:
            break
        invalid_dependencies.extend(current_invalid)
        invalid_names = {row["name"] for row in current_invalid}
        allowed = [step for step in allowed if str(step.get("name") or "") not in invalid_names]
    if invalid_dependencies:
        excluded.extend(
            {
                "name": row["name"],
                "slug": normalize_step_slug(row["name"]),
                "classification": "dependency_scope_mismatch",
                "group": "",
            }
            for row in invalid_dependencies
        )
    return allowed, {
        "system_scope": system_scope,
        "allowed_steps": len(allowed),
        "excluded_steps": excluded,
        "invalid_dependencies": invalid_dependencies,
    }


def execution_contract_for_step(step: dict[str, Any], reads: list[str], writes: list[str], policy: dict[str, Any]) -> dict[str, Any]:
    slug = normalize_step_slug(step.get("name"))
    sequential_required = policy.get("sequential_required_slugs", {}) if isinstance(policy.get("sequential_required_slugs"), dict) else {}
    full_run_sequential = policy.get("full_run_sequential_slugs", {}) if isinstance(policy.get("full_run_sequential_slugs"), dict) else {}
    profile_guidance = policy.get("step_profile_guidance", {}) if isinstance(policy.get("step_profile_guidance"), dict) else {}
    wildcard_reads = {str(item) for item in policy.get("wildcard_read_artifacts", []) or []}
    reasons: list[str] = []
    scheduler_class = "dag_parallel_safe"

    if bool(step.get("full_only", False)):
        scheduler_class = "full_run_only"
        reasons.append("step marked full_only")

    if slug in sequential_required:
        scheduler_class = "sequential_required"
        reasons.append(str(sequential_required.get(slug) or "policy marks step sequential_required"))

    if slug in full_run_sequential:
        scheduler_class = "sequential_required"
        reasons.append(str(full_run_sequential.get(slug) or "policy marks full-run step sequential"))

    if wildcard_reads & {str(item) for item in reads}:
        scheduler_class = "sequential_required"
        reasons.append("reads wildcard/global artifact state")

    shared_read_write_artifacts = sorted({str(item) for item in reads} & {str(item) for item in writes})
    if shared_read_write_artifacts:
        reasons.append(
            "reads prior output as cache/projection input: "
            + ", ".join(shared_read_write_artifacts)
        )

    if not reasons:
        reasons.append("safe for DAG scheduling after dependencies are satisfied")

    sqlite_writer = bool(writes)
    return {
        "scheduler_class": scheduler_class,
        "parallel_safe_after_dependencies": scheduler_class == "dag_parallel_safe",
        "sqlite_writer": sqlite_writer,
        "requires_shadow_flush_before_release_validation": sqlite_writer,
        "release_style_sequential_recommended": scheduler_class != "dag_parallel_safe" or sqlite_writer,
        "profile_guidance": profile_guidance.get(slug, {}) if isinstance(profile_guidance.get(slug), dict) else {},
        "reasons": reasons,
    }


def dependency_closure_for_step(catalog: list[dict[str, Any]], step_name: str) -> list[str]:
    by_name = {str(step.get("name") or ""): step for step in catalog}
    seen: set[str] = set()
    ordered: list[str] = []

    def visit(name: str) -> None:
        if name in seen or name not in by_name:
            return
        seen.add(name)
        for dep in by_name[name].get("depends_on", []) or []:
            visit(str(dep))
        ordered.append(name)

    visit(str(step_name or ""))
    return ordered


def claim_owned_execution_plan(
    catalog: list[dict[str, Any]],
    profile_name: str,
    *,
    projects: list[str] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive one claim profile from the canonical DAG or fail on policy drift."""
    execution_policy = policy if isinstance(policy, dict) else load_pipeline_execution_policy()
    profiles = execution_policy.get("execution_profiles", {})
    profile = profiles.get(str(profile_name or "").strip().lower(), {}) if isinstance(profiles, dict) else {}
    if not isinstance(profile, dict) or profile.get("mode") != "claim_closure":
        raise ValueError(f"execution profile is not claim-owned: {profile_name}")

    by_slug: dict[str, dict[str, Any]] = {}
    for step in catalog:
        slug = normalize_step_slug(step.get("name"))
        if not slug or slug in by_slug:
            raise ValueError(f"claim-owned execution requires unique step slugs: {slug or '<empty>'}")
        by_slug[slug] = step

    target_slug = normalize_step_slug(profile.get("target_step_slug"))
    target = by_slug.get(target_slug)
    if target is None:
        raise ValueError(f"claim-owned target step is unavailable: {target_slug}")
    if target.get("accepts_execution_claim") is not True:
        raise ValueError(f"claim-owned target step cannot consume its plan: {target.get('name')}")

    required_direct = {
        normalize_step_slug(item)
        for item in profile.get("required_direct_dependency_slugs", [])
        if str(item).strip()
    }
    families = profile.get("excluded_evidence_families", [])
    if not required_direct or not isinstance(families, list) or not families:
        raise ValueError(f"claim-owned profile has no dependency partition: {profile_name}")

    family_ids: set[str] = set()
    excluded_direct: set[str] = set()
    excluded_artifacts: set[str] = set()
    excluded_checks: set[str] = set()
    excluded_signals: set[str] = set()
    normalized_families: list[dict[str, Any]] = []
    for family in families:
        if not isinstance(family, dict):
            raise ValueError(f"claim-owned excluded evidence family is not an object: {profile_name}")
        family_id = str(family.get("id") or "").strip()
        producer_slug = normalize_step_slug(family.get("producer_step_slug"))
        artifact_ids = sorted({str(item) for item in family.get("artifact_ids", []) if str(item).strip()})
        check_ids = sorted({str(item) for item in family.get("quality_check_ids", []) if str(item).strip()})
        signal_ids = sorted({str(item) for item in family.get("quality_signal_ids", []) if str(item).strip()})
        if (
            not family_id
            or family_id in family_ids
            or not producer_slug
            or not artifact_ids
            or not check_ids
        ):
            raise ValueError(f"claim-owned excluded evidence family is incomplete or duplicated: {family_id or '<empty>'}")
        duplicated_evidence = {
            "producer_step_slug": [producer_slug] if producer_slug in excluded_direct else [],
            "artifact_ids": sorted(set(artifact_ids) & excluded_artifacts),
            "quality_check_ids": sorted(set(check_ids) & excluded_checks),
            "quality_signal_ids": sorted(set(signal_ids) & excluded_signals),
        }
        if any(duplicated_evidence.values()):
            raise ValueError(
                "claim-owned excluded evidence families overlap: "
                f"profile={profile_name} family={family_id} duplicates={duplicated_evidence}"
            )
        family_ids.add(family_id)
        excluded_direct.add(producer_slug)
        excluded_artifacts.update(artifact_ids)
        excluded_checks.update(check_ids)
        excluded_signals.update(signal_ids)
        normalized_families.append(
            {
                "id": family_id,
                "producer_step_slug": producer_slug,
                "artifact_ids": artifact_ids,
                "quality_check_ids": check_ids,
                "quality_signal_ids": signal_ids,
                "reason": str(family.get("reason") or "").strip(),
            }
        )

    direct_by_slug = {
        normalize_step_slug(dependency): str(dependency)
        for dependency in target.get("depends_on", []) or []
    }
    actual_direct = set(direct_by_slug)
    if required_direct & excluded_direct:
        raise ValueError(f"claim-owned dependency partition overlaps: {profile_name}")
    if required_direct | excluded_direct != actual_direct:
        raise ValueError(
            "claim-owned dependency partition drift: "
            f"profile={profile_name} missing={sorted(actual_direct - required_direct - excluded_direct)} "
            f"unknown={sorted((required_direct | excluded_direct) - actual_direct)}"
        )

    ordered: list[str] = []
    seen: set[str] = set()

    def visit(step_slug: str) -> None:
        if step_slug in seen:
            return
        step = by_slug.get(step_slug)
        if step is None:
            raise ValueError(f"claim-owned dependency step is unavailable: {step_slug}")
        seen.add(step_slug)
        for dependency in step.get("depends_on", []) or []:
            dependency_slug = normalize_step_slug(dependency)
            if step_slug == target_slug and dependency_slug in excluded_direct:
                continue
            visit(dependency_slug)
        ordered.append(str(step.get("name") or ""))

    visit(target_slug)
    selected_slugs = {normalize_step_slug(item) for item in ordered}
    boundary_edges: list[dict[str, str]] = []
    for selected_slug in sorted(selected_slugs):
        step = by_slug[selected_slug]
        for dependency in step.get("depends_on", []) or []:
            dependency_slug = normalize_step_slug(dependency)
            if dependency_slug not in selected_slugs:
                boundary_edges.append(
                    {
                        "consumer": str(step.get("name") or ""),
                        "producer": str(dependency),
                        "producer_slug": dependency_slug,
                    }
                )
    expected_boundary = {
        (str(target.get("name") or ""), direct_by_slug[slug], slug)
        for slug in excluded_direct
    }
    actual_boundary = {
        (row["consumer"], row["producer"], row["producer_slug"])
        for row in boundary_edges
    }
    if actual_boundary != expected_boundary:
        raise ValueError(f"claim-owned closure has undeclared boundary edges: {profile_name}")

    requested_projects = sorted({str(item).strip().upper() for item in (projects or []) if str(item).strip()})
    cost_band = profile.get("cost_band", {}) if isinstance(profile.get("cost_band"), dict) else {}
    return {
        "profile": str(profile_name).strip().lower(),
        "mode": "claim_closure",
        "execution_mode": str(profile.get("execution_mode_id") or ""),
        "target_step": str(target.get("name") or ""),
        "target_step_slug": target_slug,
        "claim_boundary": str(profile.get("claim_boundary") or ""),
        "release_authority": profile.get("release_authority") is True,
        "freshness_claim": str(profile.get("freshness_claim") or ""),
        "cache_posture": str(profile.get("cache_posture") or ""),
        "project_scope": {
            "policy": str(profile.get("project_scope") or ""),
            "requested_projects": requested_projects,
        },
        "cost_band": {
            "status": str(cost_band.get("status") or "UNKNOWN"),
            "basis": str(cost_band.get("basis") or "insufficient_exact_profile_samples"),
        },
        "step_count": len(ordered),
        "selected_steps": ordered,
        "required_direct_dependency_slugs": sorted(required_direct),
        "excluded_direct_dependency_slugs": sorted(excluded_direct),
        "excluded_evidence_families": normalized_families,
        "excluded_artifact_ids": sorted(excluded_artifacts),
        "excluded_quality_check_ids": sorted(excluded_checks),
        "excluded_quality_signal_ids": sorted(excluded_signals),
        "boundary_edges": boundary_edges,
    }


def _direct_artifact_dependencies(
    by_name: dict[str, dict[str, Any]],
    step_name: str,
) -> list[str]:
    """Return scheduler dependencies whose outputs are direct inputs of the step."""
    reads = {
        str(item) for item in ARTIFACT_OWNERSHIP.get(step_name, {}).get("reads", []) or []
    }
    dependencies: list[str] = []
    for dependency in by_name.get(step_name, {}).get("depends_on", []) or []:
        dependency_name = str(dependency)
        writes = {
            str(item)
            for item in ARTIFACT_OWNERSHIP.get(dependency_name, {}).get("writes", []) or []
        }
        if "*" in reads or reads & writes:
            dependencies.append(dependency_name)
    return dependencies


def explicit_step_freshness_reuse_plan(
    catalog: list[dict[str, Any]],
    target_name: str,
    *,
    stale_projects: list[str] | None,
    force: bool,
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    """Return dependency steps that may be reused from current SQLite artifacts."""
    policy = load_pipeline_execution_policy()
    freshness = policy.get("explicit_step_freshness", {}) if isinstance(policy, dict) else {}
    closure = dependency_closure_for_step(catalog, target_name)
    retained = list(closure)
    result: dict[str, Any] = {
        "enabled": bool(freshness.get("enabled")),
        "target": target_name,
        "closure": closure,
        "reused_steps": [],
        "retained_steps": retained,
        "reason": "policy_disabled",
    }
    if not result["enabled"] or force:
        result["reason"] = "force_or_policy_disabled"
        return result
    if stale_projects:
        result["reason"] = "stale_projects_require_full_dependency_closure"
        return result

    from tools.core.artifact_freshness_contract import artifact_state_meta
    from tools.core.config import RAW_DIR

    artifact_root = Path(raw_dir or RAW_DIR)
    anchor_name = str(freshness.get("anchor_artifact") or "atlas")
    accepted_sources = {str(item) for item in freshness.get("accepted_artifact_sources", []) or []}
    anchor = artifact_state_meta(artifact_root, anchor_name)
    if not anchor.get("exists") or str(anchor.get("source")) not in accepted_sources:
        result["reason"] = "missing_or_untrusted_anchor"
        return result

    def freshness_epoch(row: dict[str, Any]) -> float:
        return float(row.get("validation_mtime") or row.get("mtime") or 0.0)

    anchor_mtime = freshness_epoch(anchor)
    by_name = {str(step.get("name") or ""): step for step in catalog}
    output_rows: dict[str, dict[str, dict[str, Any]]] = {}
    for step_name in closure:
        writes = [str(item) for item in ARTIFACT_OWNERSHIP.get(step_name, {}).get("writes", []) or []]
        output_rows[step_name] = {
            artifact: artifact_state_meta(artifact_root, artifact) for artifact in writes
        }

    reused: list[str] = []
    stale_against_dependencies: dict[str, list[str]] = {}
    for step_name in closure:
        if step_name == target_name:
            continue
        rows = list(output_rows.get(step_name, {}).values())
        if not rows:
            continue
        row_epochs = [freshness_epoch(row) for row in rows]
        dependency_epochs: list[tuple[str, float]] = []
        step_reads = {
            str(item) for item in ARTIFACT_OWNERSHIP.get(step_name, {}).get("reads", []) or []
        }
        for dependency_name in _direct_artifact_dependencies(by_name, step_name):
            dependency_rows = [
                row
                for artifact, row in output_rows.get(dependency_name, {}).items()
                if "*" in step_reads or artifact in step_reads
            ]
            if dependency_rows:
                dependency_epochs.append(
                    (dependency_name, max(freshness_epoch(row) for row in dependency_rows))
                )
        stale_dependencies = sorted(
            dependency_name
            for dependency_name, dependency_epoch in dependency_epochs
            if min(row_epochs) + 0.001 < dependency_epoch
        )
        if stale_dependencies:
            stale_against_dependencies[step_name] = stale_dependencies
        is_fresh = all(
            row.get("exists")
            and str(row.get("source")) in accepted_sources
            and freshness_epoch(row) + 0.001 >= anchor_mtime
            for row in rows
        ) and not stale_dependencies
        if is_fresh:
            reused.append(step_name)
    reused_set = set(reused)
    reuse_invalidations: dict[str, list[str]] = {}
    changed = True
    while changed:
        changed = False
        for step_name in closure:
            if step_name not in reused_set:
                continue
            dependencies = {
                item
                for item in _direct_artifact_dependencies(by_name, step_name)
                if item in closure
            }
            rerun_dependencies = sorted(dependencies - reused_set)
            if rerun_dependencies:
                reused_set.remove(step_name)
                reuse_invalidations[step_name] = rerun_dependencies
                changed = True

    result["reused_steps"] = [step_name for step_name in closure if step_name in reused_set]
    result["retained_steps"] = [step_name for step_name in closure if step_name not in reused_set]
    result["stale_against_dependencies"] = stale_against_dependencies
    result["reuse_invalidations"] = reuse_invalidations
    result["reason"] = "sqlite_outputs_current_and_upstreams_reused"
    return result


def explicit_step_closure_contract_for_step(
    catalog: list[dict[str, Any]],
    step: dict[str, Any],
    reads: list[str],
    policy: dict[str, Any],
) -> dict[str, Any]:
    closure_policy = policy.get("explicit_step_closure_policy", {}) if isinstance(policy, dict) else {}
    tiers = closure_policy.get("tiers", {}) if isinstance(closure_policy.get("tiers"), dict) else {}
    sample_limit = int(closure_policy.get("sample_limit") or 12)
    global_threshold = int(closure_policy.get("global_closure_warning_threshold") or 35)
    broad_threshold = int(closure_policy.get("broad_closure_warning_threshold") or 20)
    closure = dependency_closure_for_step(catalog, str(step.get("name") or ""))
    count = len(closure)
    if count <= 1:
        tier = "standalone"
    elif count <= 5:
        tier = "narrow"
    elif count >= global_threshold or "*" in {str(item) for item in reads}:
        tier = "global"
    elif count >= broad_threshold:
        tier = "broad"
    else:
        tier = "narrow"

    tier_policy = tiers.get(tier, {}) if isinstance(tiers.get(tier), dict) else {}
    overrides = closure_policy.get("step_overrides", {}) if isinstance(closure_policy.get("step_overrides"), dict) else {}
    slug = normalize_step_slug(step.get("name"))
    override = overrides.get(slug, {}) if isinstance(overrides.get(slug), dict) else {}
    warning = str(override.get("operator_warning") or tier_policy.get("operator_warning") or "")
    recommended = str(override.get("recommended_alternative") or "")
    return {
        "closure_policy": "transitive_dependencies_by_default",
        "closure_tier": tier,
        "dependency_closure_count": count,
        "dependency_closure_sample": closure[: max(1, sample_limit)],
        "freshness_claim": str(closure_policy.get("freshness_claim") or "single_step_evidence_not_global_freshness"),
        "operator_warning": warning,
        "recommended_alternative": recommended,
    }


def invocation_contract_for_step(
    catalog: list[dict[str, Any]],
    step: dict[str, Any],
    reads: list[str],
    writes: list[str],
    policy: dict[str, Any],
) -> dict[str, Any]:
    name = str(step.get("name") or "")
    module = STEP_MODULE_INVOCATIONS.get(name)
    contract: dict[str, Any] = {
        "canonical_step_command": f'python sage.py run --step "{name}"',
        "direct_file_invocation": "unsupported",
        "direct_file_policy": "Use the product CLI for step runs; use module mode only for diagnostic engine runs.",
        "input_artifacts": reads,
        "output_artifacts": writes,
        "explicit_step_closure": explicit_step_closure_contract_for_step(catalog, step, reads, policy),
    }
    if module:
        contract["diagnostic_module"] = module
        contract["diagnostic_module_command"] = STEP_DIAGNOSTIC_COMMANDS.get(name, f"python -m {module}")
    return contract


def step_registry_from_catalog(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    capability_summary = summarize_capabilities(load_capability_registry())
    execution_policy = load_pipeline_execution_policy()
    execution_profiles = (
        execution_policy.get("execution_profiles", {})
        if isinstance(execution_policy.get("execution_profiles"), dict)
        else {}
    )
    execution_modes = (
        execution_policy.get("execution_modes", {})
        if isinstance(execution_policy.get("execution_modes"), dict)
        else {}
    )
    capability_artifacts: dict[str, set[str]] = {
        str(row.get("id") or ""): {str(item) for item in row.get("artifacts", []) or []}
        for row in capability_summary.get("capabilities", [])
        if row.get("id")
    }
    steps = []
    for index, step in enumerate(catalog):
        name = str(step.get("name") or "")
        ownership = ARTIFACT_OWNERSHIP.get(name, {})
        reads = list(ownership.get("reads", []) or [])
        writes = list(ownership.get("writes", []) or [])
        step_artifacts = {item for item in writes if item != "*"}
        capability_ids = sorted(
            cap_id
            for cap_id, artifacts in capability_artifacts.items()
            if step_artifacts & artifacts
        )
        steps.append(
            {
                "index": index,
                "name": name,
                "slug": normalize_step_slug(name),
                "category": step.get("category") or "uncategorized",
                "depends_on": list(step.get("depends_on", []) or []),
                "heavy": bool(step.get("heavy", False)),
                "full_only": bool(step.get("full_only", False)),
                "has_skip_condition": bool(step.get("skip_when", False)),
                "has_include_condition": "include_when" in step,
                "reads_artifacts": reads,
                "writes_artifacts": writes,
                "capabilities": capability_ids,
                "system_scope_contract": system_scope_contract_for_step(step, execution_policy),
                "execution_contract": execution_contract_for_step(step, reads, writes, execution_policy),
                "invocation_contract": invocation_contract_for_step(catalog, step, reads, writes, execution_policy),
            }
        )
    writers: dict[str, list[str]] = {}
    for step in steps:
        for artifact in step.get("writes_artifacts", []):
            writers.setdefault(artifact, []).append(step["name"])
    write_conflicts = {
        artifact: names
        for artifact, names in sorted(writers.items())
        if len(names) > 1
    }
    claim_targets = {
        normalize_step_slug(step.get("name"))
        for step in catalog
        if step.get("accepts_execution_claim") is True
    }
    claim_owned_profiles = {
        str(profile): claim_owned_execution_plan(
            catalog,
            str(profile),
            policy=execution_policy,
        )
        for profile, config in sorted(execution_profiles.items())
        if (
            isinstance(config, dict)
            and config.get("mode") == "claim_closure"
            and normalize_step_slug(config.get("target_step_slug")) in claim_targets
        )
    }
    return {
        "meta": {"kind": "pipeline_step_registry", "version": "v1"},
        "execution_profiles": {
            str(profile): {
                "mode": str(config.get("mode") or ""),
                "include_full_only": config.get("include_full_only") is True,
                "description": str(config.get("description") or ""),
                "keep_slugs": list(config.get("keep_slugs", []) or []) if isinstance(config, dict) else [],
                "execution_mode_id": str(config.get("execution_mode_id") or ""),
                "claim_plan": claim_owned_profiles.get(str(profile)),
            }
            for profile, config in sorted(execution_profiles.items())
            if isinstance(config, dict)
        },
        "execution_modes": {
            str(mode): {
                "profile": str(config.get("profile") or ""),
                "purpose": str(config.get("purpose") or ""),
                "trigger": str(config.get("trigger") or ""),
                "scope": str(config.get("scope") or ""),
                "ssot_expectation": str(config.get("ssot_expectation") or ""),
                "heavy_step_policy": str(config.get("heavy_step_policy") or ""),
                "required_artifacts": list(config.get("required_artifacts", []) or []) if isinstance(config, dict) else [],
                "required_validators": list(config.get("required_validators", []) or []) if isinstance(config, dict) else [],
                "agent_surface": str(config.get("agent_surface") or ""),
                "claim_boundary": str(config.get("claim_boundary") or ""),
            }
            for mode, config in sorted(execution_modes.items())
            if isinstance(config, dict)
        },
        "summary": {
            "steps": len(steps),
            "heavy": sum(1 for step in steps if step["heavy"]),
            "full_only": sum(1 for step in steps if step["full_only"]),
            "dependency_bound": sum(1 for step in steps if step["depends_on"]),
            "artifact_writers": len(writers),
            "artifact_write_conflicts": len(write_conflicts),
            "capability_bound": sum(1 for step in steps if step["capabilities"]),
            "dag_parallel_safe": sum(
                1 for step in steps
                if step["execution_contract"]["scheduler_class"] == "dag_parallel_safe"
            ),
            "sequential_required": sum(
                1 for step in steps
                if step["execution_contract"]["scheduler_class"] == "sequential_required"
            ),
            "execution_full_run_only": sum(
                1 for step in steps
                if step["execution_contract"]["scheduler_class"] == "full_run_only"
            ),
            "sqlite_writers": sum(1 for step in steps if step["execution_contract"]["sqlite_writer"]),
            "invocation_contracts": sum(1 for step in steps if step.get("invocation_contract")),
            "claim_owned_profiles": len(claim_owned_profiles),
        },
        "artifact_ownership": {
            "writers": writers,
            "write_conflicts": write_conflicts,
        },
        "claim_owned_execution_plans": claim_owned_profiles,
        "steps": steps,
    }

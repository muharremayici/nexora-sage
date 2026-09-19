from __future__ import annotations

from pathlib import Path

from tools.core.config import CODE_MAPS_DIR
from tools.core.source_layer_classification_policy import exact_path_rule


IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}

GENERATED_OR_RUNTIME_ROOTS = {
    ".gemini",
    ".tmp",
    "output",
    "scratch",
}

GENERATED_OR_RUNTIME_NAMES = {
    ".pipeline_run.lock",
    ".pipeline_run.lock.metadata.json",
    "codemaps.config.json",
    "codemaps.db",
    "codemaps.discovery.json",
    "codemaps.overrides.json",
    "nexora_kernel.db",
}

ROOT_PUBLIC_FILES = {
    ".gitignore",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "requirements.txt",
    "sage.py",
    "codemaps.py",
    "SKILL.md",
    "AGENTS.md",
    "CLEAN_INSTALL_NOTES.md",
    "CONTRIBUTING.md",
    "THREAT_MODEL.md",
}

VALIDATOR_OR_REPORT_FILES = {
    "analyze_architecture_blueprint_corpus.py",
    "ci_release_check.py",
    "run_release_proof_bundle.py",
    "run_sage_self_audit.py",
    "update_performance_ledger.py",
    "generate_source_layer_inventory.py",
    "generate_layer_release_matrix.py",
    "generate_sage_self_map.py",
    "generate_v1_quality_scorecard.py",
    "generate_system_health_check.py",
    "generate_v1_system_health_check.py",
    "generate_mcp_surface_command_matrix.py",
    "generate_system_connectivity_map.py",
    "generate_decision_domain_classification.py",
    "generate_release_seal_remaining_actions.py",
    "generate_final_suspicion_audit.py",
}


def relative_posix(path: Path, root: Path = CODE_MAPS_DIR) -> str:
    return Path(path).relative_to(root).as_posix()


def is_ignored(path: Path, root: Path = CODE_MAPS_DIR) -> bool:
    parts = set(Path(path).relative_to(root).parts)
    return bool(parts & IGNORED_PARTS) or any(part.endswith(".egg-info") for part in parts)


def classify_source_layer(path: Path, root: Path = CODE_MAPS_DIR) -> tuple[str, str]:
    rel = relative_posix(path, root=root)
    parts = rel.split("/")
    name = Path(path).name
    lower = rel.lower()

    if parts[0] in GENERATED_OR_RUNTIME_ROOTS or name in GENERATED_OR_RUNTIME_NAMES:
        return "generated_runtime_artifact", "runtime root/name"
    if name.startswith("batch") and name.endswith(".txt"):
        return "generated_runtime_artifact", "batch audit dump"
    if name.endswith((".pyc", ".pyo")) or ".nexora_hitl_secret" in name:
        return "generated_runtime_artifact", "cache/secret/runtime"

    policy_rule = exact_path_rule(rel)
    if policy_rule is not None:
        return policy_rule

    if rel in ROOT_PUBLIC_FILES:
        if name in {"requirements.txt", "CLEAN_INSTALL_NOTES.md"}:
            return "installation_onboarding", "root install/operator contract"
        if name in {"SKILL.md", "AGENTS.md"}:
            return "hitl_agent_governance", "root AI-agent routing or skill contract"
        if name in {"CONTRIBUTING.md", "THREAT_MODEL.md"}:
            return "documentation_claims_evidence", "root contribution/trust contract"
        return "root_product_surface", "root public product surface"

    if parts[0] == ".github":
        return "installation_onboarding", "CI and repository automation contract"

    if parts[0] == "config":
        if len(parts) > 1 and parts[1] == "schemas":
            return "schema_artifact_contracts", "config schema"
        if len(parts) > 1 and parts[1] == "golden":
            return "golden_truth_fixtures", "golden truth set"
        return "configuration_doctrine_policy", "config policy/runtime truth"

    if parts[0] == "docs":
        if "layer_integrity_audit" in lower or lower.startswith("docs/mri_") or "audit_2026" in lower or "system_audit" in lower:
            return "documentation_layer_audits", "audit/history document"
        if any(token in lower for token in ("release", "claim", "evidence", "roadmap", "positioning", "matrix", "quickstart", "runbook", "checklist")):
            return "documentation_claims_evidence", "release/product evidence document"
        return "documentation_claims_evidence", "documentation"

    if name == "verify_benchmark.py":
        return "reporting_product_readiness", "benchmark/reporting helper"

    if parts[0] != "tools":
        return "unknown_or_review", "non-tools/config/docs root file"

    if len(parts) > 1 and parts[1] == "tests":
        return "tests_fixtures", "test suite or fixture"
    if name == "__init__.py":
        return "package_runtime", "Python package marker"
    if len(parts) > 1 and parts[1] == "validate_react":
        return "react_surgical_intelligence", "React validator helper"
    if len(parts) > 1 and parts[1] == "mcp":
        return "contextos_watchdog_mcp", "MCP server surface"
    if len(parts) > 1 and parts[1] == "orchestrators":
        if name in {"discovery.py"}:
            return "discovery_config_truth", "discovery orchestrator"
        if name == "watchdog.py":
            return "contextos_watchdog_mcp", "watchdog live surface"
        return "orchestration_pipeline", "orchestrator/setup"
    if len(parts) > 1 and parts[1] == "utils":
        return "core_runtime_services", "utility runtime"

    if len(parts) > 1 and parts[1] == "core":
        if name in {"db.py", "artifact_store.py", "json_io.py", "atlas_io.py"}:
            return "sqlite_artifact_store", "artifact IO/store"
        if name in {"bootstrap_env.py", "vendor_bootstrap.py"}:
            return "installation_onboarding", "bootstrap/vendor path"
        if any(token in name for token in ("pipeline", "artifact_contract", "artifact_validator")):
            return "validation_release_gates", "contract/registry"
        return "core_runtime_services", "core service"

    if len(parts) > 1 and parts[1] == "engines":
        if name in {"package.json", "package-lock.json"}:
            return "package_runtime", "engine Node runtime manifest"
        if name.startswith("ast_sequencer") or name in {"generate_atlas.py", "ts_diagnostics_collector.cjs", "debug_sequencer.cjs"}:
            return "atlas_polyglot_sequencing", "sequencer/atlas"
        if any(token in name for token in ("react", "next_boundary", "state_data", "state_flow", "a11y", "ui_runtime", "ui_smoke", "framework_route", "ui_mapper")):
            return "react_surgical_intelligence", "React/UI intelligence"
        if any(token in name for token in ("merge", "variation", "task_pack", "validation_oracle", "decision_cockpit", "self_healing")):
            return "variation_merge_intelligence", "variation/merge"
        if any(token in name for token in ("audit", "dead_code", "quality", "health", "proof", "circular", "blast", "risk", "clone", "release_readiness", "safety_gate", "decision_evidence", "nanometric_diff", "live_surface", "performance_n_plus_one", "resilience_error")):
            return "governance_audit_quality", "governance/quality"
        if any(token in name for token in ("quant", "telemetry", "confidence", "mcp_governance")):
            return "contextos_watchdog_mcp", "ContextOS/telemetry"
        if "project_dna" in name or "capability_activation" in name:
            return "discovery_config_truth", "project DNA/capability activation planning"
        if any(token in name for token in ("dashboard", "report", "context", "cmo", "master", "maintenance", "capability_registry", "capability_pipeline")):
            return "reporting_product_readiness", "reporting"
        if any(token in name for token in ("artifact_contract_validator",)):
            return "validation_release_gates", "artifact contract validation"
        if any(token in name for token in ("test_impact", "temporal_diff")):
            return "validation_release_gates", "change/test-impact validation"
        if any(token in name for token in ("architecture_oracle", "adapter_registry", "hexagonal", "fractal", "landscape", "nuclear", "closure", "host", "scoped_host", "convergence", "studio", "keyword")):
            return "governance_audit_quality", "architecture/governance analysis"
        return "unknown_or_review", "unclassified engine"

    if name.startswith("validate_") or name in VALIDATOR_OR_REPORT_FILES:
        if name == "generate_v1_quality_scorecard.py":
            return "reporting_product_readiness", "whole-product quality scorecard"
        if name == "generate_system_health_check.py":
            return "reporting_product_readiness", "whole-system release health check"
        if name == "generate_v1_system_health_check.py":
            return "reporting_product_readiness", "legacy wrapper for whole-system release health check"
        if name == "generate_mcp_surface_command_matrix.py":
            return "contextos_watchdog_mcp", "MCP agent-surface command matrix"
        if name in {"generate_system_connectivity_map.py", "generate_decision_domain_classification.py", "generate_release_seal_remaining_actions.py"}:
            return "reporting_product_readiness", "whole-system release/readiness report"
        if name == "generate_final_suspicion_audit.py":
            return "reporting_product_readiness", "final release suspicion audit"
        if "project_dna" in name or "capability_activation" in name:
            return "discovery_config_truth", "project DNA/capability activation planning validator"
        if "react" in name:
            return "react_surgical_intelligence", "React validator"
        if "external" in name:
            return "external_target_corpus", "external target validator"
        return "validation_release_gates", "validator/release gate"
    if any(token in name for token in ("external_target", "inspect_target")):
        return "external_target_corpus", "external target support"
    if any(token in name for token in ("hitl", "agent_response", "agent_handoff", "agent_contract", "operator_packet")):
        return "hitl_agent_governance", "agent/HITL"
    if any(token in name for token in ("demo", "report", "dashboard", "readiness", "brief", "surface_inventory", "surface_quality", "surface_review_tracker", "surface_seal", "manual_seal_pack", "runtime_honesty_review", "provenance", "phase_status", "transcript", "test_gap", "harness", "manual_review_pack", "medium_precision_pack", "truth_seed", "optimization_plan", "lesson_projection")):
        return "reporting_product_readiness", "report/readiness"
    if name in {"config_compiler.py", "doctrine_compiler.py", "governance_sync.py", "sync_project_truth_layers.py", "auto_doctrine.py"}:
        return "discovery_config_truth", "config truth"
    if name in {
        "clean_distribution_artifacts.py",
        "generate_installation_proof.py",
        "run_distribution_tests.py",
        "sync_clean_distribution.py",
    }:
        return "installation_onboarding", "clean distribution"
    if name in {"import_react_fixture.py", "generate_react_fixture_seed.py"}:
        return "react_surgical_intelligence", "React fixture support"
    if name in {"execute_auto_merge_rollback.py"}:
        return "variation_merge_intelligence", "rollback/merge support"

    return "unknown_or_review", "unclassified tools file"

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.pipeline_registry import (
    STEP_DIAGNOSTIC_COMMANDS,
    catalog_args_from_execution_policy,
    claim_owned_execution_plan,
    dependency_closure_for_step,
    normalize_step_slug,
    step_registry_from_catalog,
)
from tools.core.release_proof_steps import (
    load_release_proof_scope_contract,
    load_release_proof_steps,
    load_release_proof_steps_contract,
    release_proof_step_scope_map,
)
from tools.core.watchdog_proof_debt import validate_watchdog_proof_debt_policy
from tools.core.watchdog_runtime_contract import load_watchdog_runtime_contract


def _vocabulary_set(vocabularies: dict[str, Any], key: str) -> set[str]:
    values = vocabularies.get(key, [])
    return {str(item) for item in values} if isinstance(values, list) else set()


def _vocabulary_map(vocabularies: dict[str, Any], key: str) -> dict[str, str]:
    values = vocabularies.get(key, {})
    return {str(k): str(v) for k, v in values.items()} if isinstance(values, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _static_runtime_boundary_violations(
    profile_guidance: dict[str, Any],
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    required_steps = contract.get("required_steps", []) if isinstance(contract, dict) else []
    expected = {
        "evidence_mode": contract.get("evidence_mode"),
        "executes_target_runtime": contract.get("executes_target_runtime"),
        "ingests_target_runtime_stderr": contract.get("ingests_target_runtime_stderr"),
        "runtime_question_authority": contract.get("runtime_question_authority"),
    }
    violations: list[dict[str, Any]] = []
    if (
        not isinstance(required_steps, list)
        or not required_steps
        or expected["evidence_mode"] != "static_repository_analysis"
        or expected["executes_target_runtime"] is not False
        or expected["ingests_target_runtime_stderr"] is not False
        or expected["runtime_question_authority"] != "target_native_execution_required"
        or not str(contract.get("claim_boundary") or "").strip()
    ):
        violations.append({"reason": "invalid_static_runtime_boundary_contract"})
        return violations

    for raw_slug in required_steps:
        slug = normalize_step_slug(raw_slug)
        guidance = profile_guidance.get(slug)
        if not isinstance(guidance, dict):
            violations.append({"step": slug, "reason": "missing_profile_guidance"})
            continue
        mismatches = {
            field: {"expected": value, "actual": guidance.get(field)}
            for field, value in expected.items()
            if guidance.get(field) != value
        }
        if mismatches:
            violations.append({"step": slug, "mismatches": mismatches})
    return violations


def _pipeline_registry_fallback_args(policy: dict[str, Any]) -> dict[str, Any]:
    validators = (
        policy.get("validator_preconditions", {}).get("validators", {})
        if isinstance(policy.get("validator_preconditions"), dict)
        else {}
    )
    contract = validators.get("validate_pipeline_execution_contract", {}) if isinstance(validators, dict) else {}
    args = contract.get("registry_fallback_catalog_args", {}) if isinstance(contract, dict) else {}
    return args if isinstance(args, dict) else {}


def _catalog_registry_fallback(policy: dict[str, Any]) -> dict[str, Any]:
    try:
        from tools.orchestrators.orchestrator import build_step_catalog

        return step_registry_from_catalog(
            build_step_catalog(catalog_args_from_execution_policy(policy), stale_projects=None, changed_files=None)
        )
    except Exception:
        return {}


def _registry(policy: dict[str, Any]) -> dict[str, Any]:
    registry = load_json_file(RAW_DIR / "pipeline_step_registry.json", {})
    if not isinstance(registry, dict) or not registry.get("steps"):
        registry = _catalog_registry_fallback(policy)
    return registry if isinstance(registry, dict) else {}


def _steps(registry: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(registry, dict):
        return []
    return [step for step in registry.get("steps", []) if isinstance(step, dict)]


def _claim_owned_profile_validation(policy: dict[str, Any]) -> dict[str, Any]:
    try:
        from tools.orchestrators.orchestrator import build_step_catalog

        catalog = build_step_catalog(
            catalog_args_from_execution_policy(policy),
            stale_projects=None,
            changed_files=None,
        )
        profiles = policy.get("execution_profiles", {}) if isinstance(policy, dict) else {}
        execution_modes = policy.get("execution_modes", {}) if isinstance(policy, dict) else {}
        claim_profiles = [
            str(name)
            for name, config in sorted(profiles.items())
            if isinstance(config, dict) and config.get("mode") == "claim_closure"
        ] if isinstance(profiles, dict) else []
        plans = {
            name: claim_owned_execution_plan(catalog, name, policy=policy)
            for name in claim_profiles
        }
        target_contract = load_json_object_strict(
            ROOT / "config" / "target_repository_proof_contract.json",
            label="Target repository proof contract",
        )
        refresh_profile = str(target_contract.get("public_cli", {}).get("refresh_execution_profile") or "")
        selected_plan = plans.get(refresh_profile, {})
        broad_quality_count = len(dependency_closure_for_step(catalog, "Quality Gates"))
        profile_links = {
            str(name): str(config.get("quality_gate_claim_profile") or "")
            for name, config in sorted(profiles.items())
            if isinstance(config, dict) and config.get("quality_gate_claim_profile")
        }
        invalid_links = {
            name: linked
            for name, linked in profile_links.items()
            if linked not in plans
        }
        invalid_profile_modes: dict[str, str] = {}
        for name in plans:
            mode_id = str(profiles.get(name, {}).get("execution_mode_id") or "")
            mode_config = execution_modes.get(mode_id, {}) if mode_id else {}
            if not isinstance(mode_config, dict) or str(mode_config.get("profile") or "") != name:
                invalid_profile_modes[name] = mode_id
        valid = (
            bool(plans)
            and refresh_profile in plans
            and bool(selected_plan.get("claim_boundary"))
            and selected_plan.get("release_authority") is False
            and int(selected_plan.get("step_count") or 0) < broad_quality_count
            and bool(selected_plan.get("excluded_evidence_families"))
            and bool(selected_plan.get("freshness_claim"))
            and bool(selected_plan.get("cache_posture"))
            and bool(selected_plan.get("project_scope", {}).get("policy"))
            and bool(selected_plan.get("cost_band", {}).get("status"))
            and not invalid_links
            and not invalid_profile_modes
        )
        return {
            "status": "PASS" if valid else "FAIL",
            "refresh_profile": refresh_profile,
            "broad_quality_step_count": broad_quality_count,
            "plans": plans,
            "profile_links": profile_links,
            "invalid_profile_links": invalid_links,
            "invalid_profile_modes": invalid_profile_modes,
        }
    except Exception as exc:
        return {
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _release_proof_dag_validation() -> dict[str, Any]:
    contract = load_release_proof_steps_contract()
    steps = load_release_proof_steps()
    by_id = {str(step.get("id") or ""): step for step in steps if step.get("id")}
    duplicate_ids = sorted(
        step_id
        for step_id in set(by_id)
        if sum(1 for step in steps if str(step.get("id") or "") == step_id) > 1
    )
    unknown_dependencies: list[dict[str, Any]] = []
    for step in steps:
        step_id = str(step.get("id") or "")
        deps = [str(item) for item in step.get("depends_on", []) or []]
        missing = sorted(dep for dep in deps if dep not in by_id)
        if missing:
            unknown_dependencies.append({"step": step_id, "unknown_dependencies": missing})

    emitted: set[str] = set()
    pending = {step_id: step for step_id, step in by_id.items()}
    ordered: list[str] = []
    while pending:
        progressed = False
        for step_id, step in list(pending.items()):
            deps = [str(item) for item in step.get("depends_on", []) or []]
            if any(dep not in by_id for dep in deps):
                continue
            if all(dep in emitted for dep in deps):
                ordered.append(step_id)
                emitted.add(step_id)
                pending.pop(step_id)
                progressed = True
        if not progressed:
            break

    unresolved = {
        step_id: [str(item) for item in step.get("depends_on", []) or []]
        for step_id, step in pending.items()
    }

    def ancestors(step_id: str) -> set[str]:
        visited: set[str] = set()
        pending_ids = list(by_id.get(step_id, {}).get("depends_on", []) or [])
        while pending_ids:
            dependency = str(pending_ids.pop())
            if dependency in visited:
                continue
            visited.add(dependency)
            pending_ids.extend(by_id.get(dependency, {}).get("depends_on", []) or [])
        return visited

    producer_consumer_violations: list[dict[str, Any]] = []
    invariants = contract.get("producer_consumer_invariants", [])
    for invariant in invariants if isinstance(invariants, list) else []:
        if not isinstance(invariant, dict):
            producer_consumer_violations.append({"reason": "invalid_invariant_shape"})
            continue
        producer = str(invariant.get("producer") or "")
        consumers = [str(item) for item in invariant.get("consumers", []) or []]
        if producer not in by_id:
            producer_consumer_violations.append(
                {"producer": producer, "reason": "unknown_producer"}
            )
            continue
        for consumer in consumers:
            if consumer not in by_id:
                producer_consumer_violations.append(
                    {"producer": producer, "consumer": consumer, "reason": "unknown_consumer"}
                )
            elif producer not in ancestors(consumer):
                producer_consumer_violations.append(
                    {
                        "producer": producer,
                        "consumer": consumer,
                        "reason": "missing_dependency_path",
                    }
                )
    return {
        "steps": len(steps),
        "ordered_steps": len(ordered),
        "duplicate_ids": duplicate_ids,
        "unknown_dependencies": unknown_dependencies,
        "unresolved_or_cyclic": unresolved,
        "producer_consumer_invariants": len(invariants) if isinstance(invariants, list) else 0,
        "producer_consumer_violations": producer_consumer_violations,
    }


def _release_proof_timeout_profile_validation(policy: dict[str, Any]) -> dict[str, Any]:
    """Validate that declared outer profiles cover centrally owned inner limits."""

    profiles = policy.get("release_proof_timeout_profiles") if isinstance(policy, dict) else {}
    profiles = profiles if isinstance(profiles, dict) else {}
    invalid_profiles: list[dict[str, Any]] = []
    referenced_profiles: set[str] = set()
    for step in load_release_proof_steps():
        profile_id = str(step.get("timeout_profile") or "").strip()
        if not profile_id:
            continue
        referenced_profiles.add(profile_id)
        profile = profiles.get(profile_id)
        paths = profile.get("nested_operational_limit_paths") if isinstance(profile, dict) else None
        invalid_paths: list[str] = []
        if isinstance(paths, list):
            for path in paths:
                value: Any = policy
                for segment in str(path or "").split("."):
                    if not segment or not isinstance(value, dict) or segment not in value:
                        invalid_paths.append(str(path))
                        break
                    value = value[segment]
                else:
                    try:
                        if int(value) <= 0:
                            invalid_paths.append(str(path))
                    except (TypeError, ValueError):
                        invalid_paths.append(str(path))
        try:
            has_valid_shape = (
                isinstance(profile, dict)
                and isinstance(paths, list)
                and bool(paths)
                and not invalid_paths
                and float(profile.get("headroom_ratio")) >= 0
                and int(profile.get("minimum_timeout_seconds")) > 0
            )
        except (TypeError, ValueError):
            has_valid_shape = False
        if not has_valid_shape:
            invalid_profiles.append(
                {
                    "step": str(step.get("id") or ""),
                    "timeout_profile": profile_id,
                    "invalid_operational_limit_paths": invalid_paths,
                }
            )
    return {
        "referenced_profiles": sorted(referenced_profiles),
        "invalid_profiles": invalid_profiles,
    }


def _release_proof_scope_validation() -> dict[str, Any]:
    contract = load_release_proof_scope_contract()
    steps = load_release_proof_steps()
    pipeline_policy = load_json_object_strict(
        ROOT / "config" / "pipeline_execution_policy.json",
        label="Pipeline execution policy",
    )
    by_id = {str(step.get("id") or ""): step for step in steps if step.get("id")}
    violations: list[dict[str, Any]] = []
    try:
        scope_by_step = release_proof_step_scope_map()
    except (FileNotFoundError, ValueError) as exc:
        scope_by_step = {}
        violations.append({"reason": "scope_contract_load_failed", "detail": str(exc)})

    domains = contract.get("domains", []) if isinstance(contract, dict) else []
    domain_ids: list[str] = []
    for domain in domains if isinstance(domains, list) else []:
        if not isinstance(domain, dict):
            violations.append({"reason": "domain_not_object"})
            continue
        domain_id = str(domain.get("id") or "").strip()
        domain_ids.append(domain_id)
        if (
            not domain_id
            or not str(domain.get("evidence_role") or "").strip()
            or not str(domain.get("execution_cadence") or "").strip()
            or not isinstance(domain.get("step_ids"), list)
            or not domain.get("step_ids")
        ):
            violations.append({"domain": domain_id, "reason": "incomplete_domain_contract"})

    duplicate_domain_ids = sorted(
        domain_id for domain_id in set(domain_ids) if domain_ids.count(domain_id) > 1
    )
    if duplicate_domain_ids:
        violations.append({"reason": "duplicate_domain_ids", "ids": duplicate_domain_ids})

    missing_steps = sorted(set(by_id) - set(scope_by_step))
    unknown_steps = sorted(set(scope_by_step) - set(by_id))
    if missing_steps:
        violations.append({"reason": "unclassified_release_proof_steps", "steps": missing_steps})
    if unknown_steps:
        violations.append({"reason": "unknown_classified_steps", "steps": unknown_steps})

    required_invariants = {
        "every_step_has_exactly_one_primary_domain",
        "unknown_steps_fail_closed",
        "unbounded_live_workspace_analysis_is_forbidden",
        "external_holdouts_are_not_rerun_by_full_release_proof",
        "human_or_legal_authority_is_not_created_by_machine_pass",
        "machine_readiness_is_not_collapsed_into_human_or_public_authority",
        "representative_repository_evidence_cannot_widen_external_universality_claims",
    }
    invariants = contract.get("invariants", {}) if isinstance(contract, dict) else {}
    invalid_invariants = sorted(
        invariant
        for invariant in required_invariants
        if not isinstance(invariants, dict) or invariants.get(invariant) is not True
    )
    if invalid_invariants:
        violations.append({"reason": "required_scope_invariants_not_fail_closed", "ids": invalid_invariants})

    operating_model = contract.get("proof_operating_model", {}) if isinstance(contract, dict) else {}
    operating_model_valid = (
        isinstance(operating_model, dict)
        and (operating_model.get("development") or {}).get("execution")
        == "selected_dependency_closure"
        and (operating_model.get("development") or {}).get("full_bundle_by_default") is False
        and (operating_model.get("frozen_candidate") or {}).get("execution")
        == "evaluate_all_receipts_execute_invalidated_and_always_fresh"
        and (operating_model.get("frozen_candidate") or {}).get("live_repository_limit") == 1
        and (operating_model.get("live_multi_project_analysis") or {}).get("execution")
        == "explicit_operator_action_outside_canonical_release_proof"
        and (operating_model.get("external_holdout_refresh") or {}).get("execution")
        == "declared_evaluation_wave_outside_canonical_release_proof"
    )
    if not operating_model_valid:
        violations.append({"reason": "invalid_release_proof_operating_model"})

    evidence_reuse = contract.get("evidence_reuse", {}) if isinstance(contract, dict) else {}
    checkpoint = evidence_reuse.get("checkpoint", {}) if isinstance(evidence_reuse, dict) else {}
    diagnostic_planner = (
        evidence_reuse.get("diagnostic_planner", {})
        if isinstance(evidence_reuse, dict)
        else {}
    )
    completed_run_reuse = (
        evidence_reuse.get("completed_run_reuse", {})
        if isinstance(evidence_reuse, dict)
        else {}
    )
    environment_identity = (
        evidence_reuse.get("environment_identity", {})
        if isinstance(evidence_reuse, dict)
        else {}
    )
    required_identity_fields = {
        "source_dependency_sha256",
        "validator_sha256",
        "policy_sha256",
        "environment_sha256",
        "input_artifacts_sha256",
        "scope_sha256",
        "step_contract_sha256",
        "output_artifact_sha256",
    }
    resume_source = (ROOT / "tools" / "core" / "release_proof_resume.py").read_text(
        encoding="utf-8",
        errors="replace",
    )
    runner_source = (ROOT / "tools" / "run_release_proof_bundle.py").read_text(
        encoding="utf-8",
        errors="replace",
    )
    resume_contract_valid = (
        isinstance(evidence_reuse, dict)
        and evidence_reuse.get("mode") == "identity_bound_execution_reuse"
        and evidence_reuse.get("authority")
        == "execution_skip_invalidation_only_no_release_or_publication_authority"
        and isinstance(diagnostic_planner, dict)
        and diagnostic_planner.get("mode") == "plan_only"
        and diagnostic_planner.get("authority")
        == "diagnostic_only_no_execution_or_release_authority"
        and diagnostic_planner.get("execution_skipping_allowed") is False
        and evidence_reuse.get("execution_mode") == "interruption_resume_exact_identity"
        and evidence_reuse.get("execution_authority")
        == "skip_completed_non_delivery_steps_from_same_interrupted_scope_only_no_release_authority"
        and set(evidence_reuse.get("identity_fields", [])) == required_identity_fields
        and set(evidence_reuse.get("always_fresh_domains", []))
        == {"distribution_installation", "human_legal_authority", "release_envelope"}
        and isinstance(completed_run_reuse, dict)
        and completed_run_reuse.get("enabled") is True
        and completed_run_reuse.get("aggregate_strategy")
        == "validate_content_bound_receipts_and_execute_invalidated_or_always_fresh"
        and completed_run_reuse.get("proof_scope") == "full_release_proof"
        and completed_run_reuse.get("release_phase") == "frozen_candidate"
        and completed_run_reuse.get("authority")
        == "execution_skip_only_no_release_or_publication_authority"
        and isinstance(environment_identity, dict)
        and environment_identity.get("mode")
        == "single_bounded_runtime_dependency_receipt_per_invocation"
        and set(environment_identity.get("required_components", []))
        == {
            "python_executable_and_interpreter",
            "operating_system_release_and_architecture",
            "active_environment_distribution_versions",
            "declared_node_runtime_version",
            "typescript_manifest_lock_and_installed_version",
        }
        and environment_identity.get("incomplete_behavior")
        == "disable_resume_and_completed_run_reuse"
        and environment_identity.get("authority")
        == "reuse_invalidation_only_no_release_or_portability_authority"
        and isinstance(checkpoint, dict)
        and checkpoint.get("path")
        == "${code_maps}/output/.operational/release_proof/resume_checkpoint.json"
        and checkpoint.get("lock_path")
        == "${code_maps}/output/.operational/release_proof/release_proof.lock"
        and checkpoint.get("automatic") is True
        and checkpoint.get("fresh_restart_flag") == "--restart"
        and set(checkpoint.get("resumable_states", []))
        == {"in_progress", "proof_steps_complete"}
        and checkpoint.get("terminal_state") == "completed"
        and checkpoint.get("required_output_artifact") is True
        and "checkpoint_reuse_eligible" in resume_source
        and "environment_receipt" in resume_source
        and "node_ast_cache_state" in resume_source
        and "same_invocation_shared_evidence_required" in resume_source
        and "proof_resume.record_checkpoint_result" in runner_source
        and "AdvisoryFileLock(proof_resume.lock_path())" in runner_source
        and 'elif arg == "--restart"' in runner_source
    )
    if not resume_contract_valid:
        violations.append({"reason": "invalid_release_proof_interruption_resume_contract"})
    live = contract.get("live_repository_execution", {}) if isinstance(contract, dict) else {}
    live_step_id = str(live.get("step_id") or "") if isinstance(live, dict) else ""
    live_step = by_id.get(live_step_id)
    required_projects = (
        [str(item) for item in live.get("required_project_filter", [])]
        if isinstance(live, dict) and isinstance(live.get("required_project_filter"), list)
        else []
    )
    command = [str(item) for item in (live_step.get("command", []) if isinstance(live_step, dict) else [])]
    command_projects: list[str] = []
    if "--projects" in command:
        index = command.index("--projects") + 1
        if index < len(command):
            command_projects = [item.strip() for item in command[index].split(",") if item.strip()]
    command_profile = ""
    if "--profile" in command:
        index = command.index("--profile") + 1
        if index < len(command):
            command_profile = command[index]
    release_deep_run_steps = sorted(
        step_id
        for step_id, step in by_id.items()
        if "sage.py" in " ".join(str(item) for item in step.get("command", []))
        and "release-deep" in [str(item) for item in step.get("command", [])]
    )
    relationship_steps = (
        [str(item) for item in live.get("multi_project_relationship_evidence_steps", [])]
        if isinstance(live, dict) and isinstance(live.get("multi_project_relationship_evidence_steps"), list)
        else []
    )
    required_profile = str(live.get("required_execution_profile") or "") if isinstance(live, dict) else ""
    forbidden_live_slugs = {
        str(item)
        for item in live.get("forbidden_live_step_slugs", [])
        if str(item).strip()
    } if isinstance(live, dict) and isinstance(live.get("forbidden_live_step_slugs"), list) else set()
    profile_contract = (
        pipeline_policy.get("execution_profiles", {}).get(required_profile, {})
        if isinstance(pipeline_policy, dict)
        and isinstance(pipeline_policy.get("execution_profiles"), dict)
        else {}
    )
    profile_keep_slugs = {
        str(item)
        for item in profile_contract.get("keep_slugs", [])
        if str(item).strip()
    } if isinstance(profile_contract, dict) and isinstance(profile_contract.get("keep_slugs"), list) else set()
    shared_producer_artifacts = (
        live_step.get("shared_producer_artifacts", [])
        if isinstance(live_step, dict)
        else []
    )
    shared_producer_map = {
        str(item.get("consumer_step_id") or ""): {
            "artifact_id": str(item.get("artifact_id") or ""),
            "raw_artifact": getattr(item.get("raw_artifact"), "name", ""),
        }
        for item in shared_producer_artifacts
        if isinstance(item, dict)
    }
    required_shared_producer_map = {
        "release_clone_context_refresh": {
            "artifact_id": "clone_detector",
            "raw_artifact": "clone_detector.json",
        },
        "release_quality_gate_refresh": {
            "artifact_id": "quality_gate",
            "raw_artifact": "quality_gate.json",
        },
    }
    logical_shared_consumers_valid = all(
        by_id.get(step_id, {}).get("command") == []
        and by_id.get(step_id, {}).get("evidence_from_dependency") == {
            "producer_step_id": live_step_id,
            "artifact_id": expected["artifact_id"],
        }
        and by_id.get(step_id, {}).get("fresh_artifact_required") is True
        for step_id, expected in required_shared_producer_map.items()
    )
    sage_pipeline_run_steps = sorted(
        step_id
        for step_id in (
            live_step_id,
            "release_clone_context_refresh",
            "release_quality_gate_refresh",
        )
        if "sage.py" in " ".join(str(item) for item in by_id.get(step_id, {}).get("command", []))
    )
    live_contract_valid = (
        live_step is not None
        and live_step_id == "release_analysis_quality_gate_refresh"
        and scope_by_step.get(live_step_id, {}).get("proof_domain") == "sage_on_repository"
        and live.get("acquisition_mode") == "default_workspace"
        and live.get("authority") == "representative_runtime_integration_only"
        and live.get("maximum_live_projects") == 1
        and required_projects == ["MAIN"]
        and command_projects == required_projects
        and "--full" in command
        and "--force" in command
        and required_profile == "release-bounded"
        and command_profile == required_profile
        and release_deep_run_steps == []
        and sage_pipeline_run_steps == [live_step_id]
        and profile_contract.get("mode") == "keep_slugs"
        and profile_contract.get("include_full_only") is True
        and {"atlas", "semanticclonedetector", "qualitygates"}.issubset(profile_keep_slugs)
        and not (forbidden_live_slugs & profile_keep_slugs)
        and shared_producer_map == required_shared_producer_map
        and logical_shared_consumers_valid
        and relationship_steps
        and not sorted(set(relationship_steps) - set(by_id))
        and bool(str(live.get("claim_boundary") or "").strip())
        and bool(str(live.get("rule") or "").strip())
    )
    if not live_contract_valid:
        violations.append(
            {
                "reason": "invalid_or_unbounded_live_repository_release_execution",
                "step_id": live_step_id,
                "required_projects": required_projects,
                "command_projects": command_projects,
                "required_profile": required_profile,
                "command_profile": command_profile,
                "release_deep_run_steps": release_deep_run_steps,
                "sage_pipeline_run_steps": sage_pipeline_run_steps,
                "forbidden_live_slugs_enabled_by_profile": sorted(forbidden_live_slugs & profile_keep_slugs),
                "shared_producer_map": shared_producer_map,
                "logical_shared_consumers_valid": logical_shared_consumers_valid,
                "relationship_steps": relationship_steps,
            }
        )

    final_governance = (
        contract.get("final_governance_execution", {})
        if isinstance(contract, dict)
        else {}
    )
    final_step_id = (
        str(final_governance.get("step_id") or "")
        if isinstance(final_governance, dict)
        else ""
    )
    final_step = by_id.get(final_step_id, {})
    final_command = (
        [str(item) for item in final_step.get("command", [])]
        if isinstance(final_step, dict)
        else []
    )
    required_final_projects = (
        [str(item) for item in final_governance.get("required_project_filter", [])]
        if isinstance(final_governance, dict)
        and isinstance(final_governance.get("required_project_filter"), list)
        else []
    )
    required_final_profile = (
        str(final_governance.get("required_execution_profile") or "")
        if isinstance(final_governance, dict)
        else ""
    )
    required_shared_artifact_id = (
        str(final_governance.get("required_shared_producer_artifact_id") or "")
        if isinstance(final_governance, dict)
        else ""
    )
    final_evidence_contract = (
        final_step.get("evidence_from_dependency", {})
        if isinstance(final_step, dict)
        and isinstance(final_step.get("evidence_from_dependency"), dict)
        else {}
    )
    final_raw_artifact = final_step.get("raw_artifact") if isinstance(final_step, dict) else None
    final_governance_valid = (
        final_step_id == "release_quality_gate_refresh"
        and scope_by_step.get(final_step_id, {}).get("proof_domain") == "sage_on_repository"
        and final_governance.get("depends_on_step_id") == live_step_id
        and live_step_id in [str(item) for item in final_step.get("depends_on", [])]
        and required_final_projects == ["MAIN"]
        and required_final_profile == required_profile
        and required_shared_artifact_id == "quality_gate"
        and final_command == []
        and final_evidence_contract == {
            "producer_step_id": live_step_id,
            "artifact_id": required_shared_artifact_id,
        }
        and final_step.get("fresh_artifact_required") is True
        and final_governance.get("fresh_artifact_required") is True
        and getattr(final_raw_artifact, "name", "") == "quality_gate.json"
        and final_governance.get("maximum_live_projects") == 1
        and final_governance.get("authority") == "representative_main_final_governance_only"
        and bool(str(final_governance.get("claim_boundary") or "").strip())
        and bool(str(final_governance.get("rule") or "").strip())
    )
    if not final_governance_valid:
        violations.append(
            {
                "reason": "invalid_or_stale_final_governance_release_execution",
                "step_id": final_step_id,
                "depends_on_step_id": final_governance.get("depends_on_step_id"),
                "required_projects": required_final_projects,
                "required_execution_profile": required_final_profile,
                "required_shared_producer_artifact_id": required_shared_artifact_id,
                "evidence_from_dependency": final_evidence_contract,
                "fresh_artifact_required": final_step.get("fresh_artifact_required"),
                "raw_artifact": str(final_raw_artifact or ""),
            }
        )

    bounded_consumers = live.get("bounded_consumer_steps", {}) if isinstance(live, dict) else {}
    snapshot_contract = bounded_consumers.get("source_snapshot_store", {}) if isinstance(bounded_consumers, dict) else {}
    snapshot_step = by_id.get("source_snapshot_store", {})
    snapshot_command = [str(item) for item in snapshot_step.get("command", [])] if isinstance(snapshot_step, dict) else []
    snapshot_projects: list[str] = []
    if "--projects" in snapshot_command:
        index = snapshot_command.index("--projects") + 1
        if index < len(snapshot_command):
            snapshot_projects = [item.strip() for item in snapshot_command[index].split(",") if item.strip()]
    snapshot_validator_path = ROOT / "tools" / "validate_source_snapshot_store.py"
    snapshot_validator_text = snapshot_validator_path.read_text(encoding="utf-8", errors="replace")
    bounded_consumer_valid = (
        isinstance(snapshot_contract, dict)
        and snapshot_contract.get("required_project_filter") == ["MAIN"]
        and snapshot_contract.get("mutation_allowed") is False
        and snapshot_projects == ["MAIN"]
        and "project_runtime_atlas" in snapshot_validator_text
        and "refresh_atlas_projection" not in snapshot_validator_text
    )
    if not bounded_consumer_valid:
        violations.append(
            {
                "reason": "invalid_or_mutating_bounded_repository_consumer",
                "step_id": "source_snapshot_store",
                "command_projects": snapshot_projects,
            }
        )

    required_live_consumers = (
        [str(item) for item in live.get("required_live_artifact_consumers", [])]
        if isinstance(live, dict) and isinstance(live.get("required_live_artifact_consumers"), list)
        else []
    )
    unordered_live_consumers = [
        step_id
        for step_id in required_live_consumers
        if step_id not in by_id
        or live_step_id not in [str(item) for item in by_id[step_id].get("depends_on", [])]
    ]
    if not required_live_consumers or unordered_live_consumers:
        violations.append(
            {
                "reason": "live_artifact_consumer_missing_explicit_producer_dependency",
                "producer_step_id": live_step_id,
                "required_consumers": required_live_consumers,
                "unordered_consumers": unordered_live_consumers,
            }
        )

    holdout = contract.get("external_holdout_policy", {}) if isinstance(contract, dict) else {}
    holdout_step = str(holdout.get("registry_validation_step") or "") if isinstance(holdout, dict) else ""
    holdout_valid = (
        isinstance(holdout, dict)
        and holdout.get("live_holdout_execution_in_full_proof") is False
        and holdout_step in by_id
        and scope_by_step.get(holdout_step, {}).get("proof_domain") == "external_evidence_registry"
        and bool(str(holdout.get("rule") or "").strip())
    )
    if not holdout_valid:
        violations.append({"reason": "invalid_external_holdout_release_boundary", "step_id": holdout_step})

    domain_counts: dict[str, int] = {}
    for classification in scope_by_step.values():
        domain_id = str(classification.get("proof_domain") or "")
        domain_counts[domain_id] = domain_counts.get(domain_id, 0) + 1
    return {
        "status": "PASS" if not violations else "FAIL",
        "steps": len(by_id),
        "classified_steps": len(scope_by_step),
        "domain_counts": domain_counts,
        "live_repository_execution_step": live_step_id,
        "live_repository_project_filter": required_projects,
        "final_governance_execution_step": final_step_id,
        "final_governance_project_filter": required_final_projects,
        "violations": violations,
    }


def validate_pipeline_execution_contract() -> dict[str, Any]:
    policy = load_json_object_strict(ROOT / "config" / "pipeline_execution_policy.json", label="Pipeline execution policy")
    registry = _registry(policy)
    steps = _steps(registry)
    release_proof_dag = _release_proof_dag_validation()
    release_proof_timeout_profiles = _release_proof_timeout_profile_validation(policy)
    release_proof_scope = _release_proof_scope_validation()
    claim_owned_profiles = _claim_owned_profile_validation(policy)
    known_slugs = {normalize_step_slug(step.get("name")) for step in steps}
    profile_guidance = {}
    if isinstance(registry, dict):
        policy_profiles = registry.get("execution_profiles", {})
        profile_names = set(policy_profiles.keys()) if isinstance(policy_profiles, dict) else set()
    else:
        profile_names = set()
    registry_modes = registry.get("execution_modes", {}) if isinstance(registry, dict) else {}
    mode_names = set(registry_modes.keys()) if isinstance(registry_modes, dict) else set()
    vocabularies = policy.get("vocabularies", {}) if isinstance(policy, dict) else {}
    valid_scheduler_classes = set(policy.get("scheduler_classes", {}).keys()) if isinstance(policy, dict) and isinstance(policy.get("scheduler_classes"), dict) else set()
    valid_cost_tiers = _vocabulary_set(vocabularies, "cost_tiers")
    valid_step_behaviors = _vocabulary_set(vocabularies, "step_behaviors")
    valid_profiles = set(policy.get("execution_profiles", {}).keys()) if isinstance(policy, dict) and isinstance(policy.get("execution_profiles"), dict) else set()
    valid_report_profiles = _vocabulary_set(vocabularies, "report_profiles")
    valid_explicit_step_closure_tiers = _vocabulary_set(vocabularies, "explicit_step_closure_tiers")
    valid_validator_environment_classes = _vocabulary_set(vocabularies, "validator_environment_classes")
    valid_clean_mirror_behaviors = _vocabulary_set(vocabularies, "clean_mirror_behaviors")
    expected_clean_mirror_behavior_by_environment = _vocabulary_map(vocabularies, "clean_mirror_behavior_by_environment")
    valid_validator_console_progress = _vocabulary_set(vocabularies, "validator_console_progress")
    valid_validator_silence_policies = _vocabulary_set(vocabularies, "validator_silence_policies")
    required_validation_red_lines = _vocabulary_set(vocabularies, "required_validation_red_lines")
    required_project_scope_fields = _vocabulary_set(vocabularies, "required_project_scope_fields")
    required_operational_limits = _vocabulary_set(vocabularies, "required_operational_limits")
    required_report_surface_limits = _vocabulary_set(vocabularies, "required_report_surface_limits")
    required_contextos_signal_limits = _vocabulary_set(vocabularies, "required_contextos_signal_limits")
    required_scoped_local_literals = _vocabulary_set(vocabularies, "required_scoped_local_literals")
    required_pipeline_lock_policy_fields = _vocabulary_set(vocabularies, "required_pipeline_lock_policy_fields")
    required_heartbeat_cadence_fields = _vocabulary_set(vocabularies, "required_heartbeat_cadence_fields")
    required_local_duration_guidance_fields = _vocabulary_set(vocabularies, "required_local_duration_guidance_fields")
    allowed_local_duration_telemetry_artifacts = _vocabulary_set(vocabularies, "allowed_local_duration_telemetry_artifacts")
    required_heavy_validator_preconditions = _vocabulary_set(vocabularies, "required_heavy_validator_preconditions")
    required_subprocess_runtime_policy_fields = _vocabulary_set(vocabularies, "required_subprocess_runtime_policy_fields")
    required_subprocess_streaming_exceptions = _vocabulary_set(vocabularies, "required_subprocess_streaming_exceptions")
    required_subprocess_short_exceptions = _vocabulary_set(vocabularies, "required_subprocess_short_exceptions")
    required_non_main_contexts = _vocabulary_set(vocabularies, "required_non_main_contexts")
    required_execution_modes = _vocabulary_set(vocabularies, "required_execution_modes")
    required_execution_mode_fields = _vocabulary_set(vocabularies, "required_execution_mode_fields")
    release_deep_required_ssot_validators = _vocabulary_set(vocabularies, "release_deep_required_ssot_validators")
    finalizer_slugs = _vocabulary_set(vocabularies, "finalizer_slugs")
    daily_forbidden_heavy_surface_slugs = _vocabulary_set(vocabularies, "daily_forbidden_heavy_slugs")
    if isinstance(policy, dict) and isinstance(policy.get("step_profile_guidance"), dict):
        profile_guidance = policy["step_profile_guidance"]
    policy_modes = policy.get("execution_modes", {}) if isinstance(policy, dict) else {}
    policy_mode_names = set(policy_modes.keys()) if isinstance(policy_modes, dict) else set()
    validator_preconditions = policy.get("validator_preconditions", {}) if isinstance(policy, dict) else {}
    validator_environment_classes = (
        validator_preconditions.get("environment_classes", {}) if isinstance(validator_preconditions, dict) else {}
    )
    validator_contracts = (
        validator_preconditions.get("validators", {}) if isinstance(validator_preconditions, dict) else {}
    )
    report_profile_guidance = policy.get("report_profile_guidance", {}) if isinstance(policy, dict) else {}
    red_lines = policy.get("validation_governance_red_lines", {}) if isinstance(policy, dict) else {}
    project_scope_policy = policy.get("project_scope_policy", {}) if isinstance(policy, dict) else {}
    subprocess_runtime_policy = policy.get("subprocess_runtime_policy", {}) if isinstance(policy, dict) else {}
    manual_validator_execution_policy = policy.get("manual_validator_execution_policy", {}) if isinstance(policy, dict) else {}
    operational_limits = policy.get("operational_limits", {}) if isinstance(policy, dict) else {}
    report_surface_limits = policy.get("report_surface_limits", {}) if isinstance(policy, dict) else {}
    contextos_signal_limits = policy.get("contextos_signal_limits", {}) if isinstance(policy, dict) else {}
    scoped_local_literals = policy.get("scoped_local_literals", {}) if isinstance(policy, dict) else {}
    pipeline_lock = policy.get("pipeline_lock", {}) if isinstance(policy, dict) else {}
    step_system_scope_policy = policy.get("step_system_scope_policy", {}) if isinstance(policy, dict) else {}
    heartbeat_cadence = policy.get("heartbeat_cadence", {}) if isinstance(policy, dict) else {}
    local_duration_guidance = policy.get("local_duration_guidance", {}) if isinstance(policy, dict) else {}
    static_runtime_boundary_contract = policy.get("static_runtime_boundary_contract", {}) if isinstance(policy, dict) else {}
    missing_contract = [step.get("name") for step in steps if not isinstance(step.get("execution_contract"), dict)]
    invalid_scheduler = []
    sqlite_writer_mismatches = []
    unsafe_finalizers = []
    unreasoned_steps = []
    invalid_profile_guidance = []
    missing_watchdog_guidance = []
    guidance_for_unknown_steps = []
    invalid_execution_modes = []
    incomplete_execution_modes = []
    daily_profile_contradictions = []
    daily_forbidden_kept = []
    optimization_without_cache_or_rationale = []
    missing_invocation_contract = []
    invalid_invocation_contract = []
    invalid_diagnostic_commands = []
    invalid_explicit_step_closure_contract = []
    broad_explicit_steps_without_warning = []
    invalid_static_runtime_boundaries = _static_runtime_boundary_violations(
        profile_guidance,
        static_runtime_boundary_contract,
    )
    missing_release_deep_ssot_validators = []
    invalid_validator_preconditions = []
    invalid_heavy_validator_preconditions = []
    clean_source_validators_requiring_generated_output = []
    generated_artifact_validators_without_artifacts = []
    validators_with_invalid_clean_mirror_behavior = []
    validators_without_observability_contract = []
    invalid_report_profile_guidance = []
    invalid_validation_red_lines = []
    invalid_project_scope_policy = []
    invalid_subprocess_runtime_policy = []
    invalid_manual_validator_execution_policy = []
    invalid_operational_limits = []
    invalid_report_surface_limits = []
    invalid_contextos_signal_limits = []
    invalid_scoped_local_literals = []
    invalid_pipeline_lock_policy = []
    invalid_step_system_scope_policy = []
    invalid_heartbeat_cadence = []
    invalid_local_duration_guidance = []
    invalid_duration_guidance_consumers = []
    invalid_watchdog_auto_refresh_timeouts = []
    invalid_watchdog_proof_debt_policy = []
    invalid_watchdog_scoped_evidence_contract = []
    invalid_watchdog_semantic_trigger_rules = []
    invalid_smart_trigger_step_sets = []
    invalid_registry_fallback_catalog_args = []
    registry_fallback_catalog_args = _pipeline_registry_fallback_args(policy)

    scope_groups = (
        step_system_scope_policy.get("groups", {})
        if isinstance(step_system_scope_policy, dict)
        else {}
    )
    taxonomy_source = str(step_system_scope_policy.get("taxonomy_source") or "")
    taxonomy = load_json_file(ROOT / taxonomy_source, {}) if taxonomy_source else {}
    known_system_scopes = {
        str(row.get("id") or "")
        for row in taxonomy.get("system_scopes", []) or []
        if isinstance(row, dict) and str(row.get("id") or "")
    } if isinstance(taxonomy, dict) else set()
    classified_slugs: dict[str, list[str]] = {}
    for group_id, group in (scope_groups.items() if isinstance(scope_groups, dict) else []):
        if not isinstance(group, dict):
            invalid_step_system_scope_policy.append({"group": group_id, "reason": "group_not_object"})
            continue
        scopes = {str(item) for item in group.get("system_scopes", []) or []}
        if not scopes or not scopes.issubset(known_system_scopes):
            invalid_step_system_scope_policy.append(
                {"group": group_id, "reason": "unknown_or_empty_system_scopes", "scopes": sorted(scopes)}
            )
        for slug in group.get("step_slugs", []) or []:
            classified_slugs.setdefault(str(slug), []).append(str(group_id))
    duplicate_scope_classifications = {
        slug: groups for slug, groups in classified_slugs.items() if len(groups) != 1
    }
    missing_scope_classifications = sorted(known_slugs - set(classified_slugs))
    unknown_scope_classifications = sorted(set(classified_slugs) - known_slugs)
    if duplicate_scope_classifications or missing_scope_classifications or unknown_scope_classifications:
        invalid_step_system_scope_policy.append(
            {
                "reason": "scope_classification_coverage",
                "duplicates": duplicate_scope_classifications,
                "missing": missing_scope_classifications,
                "unknown": unknown_scope_classifications,
            }
        )
    if (
        str(step_system_scope_policy.get("unclassified_behavior") or "") != "fail_closed"
        or str(step_system_scope_policy.get("pipeline_run_scope") or "") != "SAGE_ON_REPOSITORY"
        or str(step_system_scope_policy.get("default_repository_scope") or "") != "SAGE_ON_REPOSITORY"
        or str(step_system_scope_policy.get("external_target_scope") or "") not in known_system_scopes
        or str(step_system_scope_policy.get("self_governance_scope") or "") not in known_system_scopes
        or not str(step_system_scope_policy.get("self_governance_entrypoint") or "").strip()
    ):
        invalid_step_system_scope_policy.append({"reason": "scope_policy_not_fail_closed_or_unknown_scope"})

    step_by_name = {str(step.get("name") or ""): step for step in steps}
    for step in steps:
        contract = step.get("system_scope_contract", {})
        scopes = {str(item) for item in contract.get("system_scopes", []) or []} if isinstance(contract, dict) else set()
        if (
            not isinstance(contract, dict)
            or contract.get("classification") != "declared"
            or not scopes
            or not scopes.issubset(known_system_scopes)
        ):
            invalid_step_system_scope_policy.append(
                {"step": step.get("name"), "reason": "invalid_registry_scope_contract", "contract": contract}
            )
            continue
        for dependency_name in step.get("depends_on", []) or []:
            dependency = step_by_name.get(str(dependency_name), {})
            dependency_contract = dependency.get("system_scope_contract", {}) if isinstance(dependency, dict) else {}
            dependency_scopes = {
                str(item) for item in dependency_contract.get("system_scopes", []) or []
            } if isinstance(dependency_contract, dict) else set()
            if not scopes.issubset(dependency_scopes):
                invalid_step_system_scope_policy.append(
                    {
                        "step": step.get("name"),
                        "dependency": dependency_name,
                        "reason": "dependency_scope_narrower_than_consumer",
                        "step_scopes": sorted(scopes),
                        "dependency_scopes": sorted(dependency_scopes),
                    }
                )

    for step in steps:
        contract = step.get("execution_contract") if isinstance(step.get("execution_contract"), dict) else {}
        scheduler_class = str(contract.get("scheduler_class") or "")
        if contract and scheduler_class not in valid_scheduler_classes:
            invalid_scheduler.append({"step": step.get("name"), "scheduler_class": scheduler_class})

        writes = step.get("writes_artifacts", [])
        writes = writes if isinstance(writes, list) else []
        if contract and bool(contract.get("sqlite_writer")) != bool(writes):
            sqlite_writer_mismatches.append(
                {
                    "step": step.get("name"),
                    "sqlite_writer": contract.get("sqlite_writer"),
                    "writes_artifacts": writes,
                }
            )

        slug = normalize_step_slug(step.get("name"))
        if slug in finalizer_slugs and scheduler_class == "dag_parallel_safe":
            unsafe_finalizers.append(step.get("name"))

        reasons = contract.get("reasons") if isinstance(contract, dict) else None
        if contract and not reasons:
            unreasoned_steps.append(step.get("name"))

        invocation = step.get("invocation_contract") if isinstance(step.get("invocation_contract"), dict) else {}
        if not invocation:
            missing_invocation_contract.append(step.get("name"))
        else:
            canonical = str(invocation.get("canonical_step_command") or "")
            direct_policy = str(invocation.get("direct_file_invocation") or "")
            input_artifacts = invocation.get("input_artifacts")
            output_artifacts = invocation.get("output_artifacts")
            explicit_closure = invocation.get("explicit_step_closure")
            if (
                not canonical.startswith("python sage.py run --step ")
                or "internal_step_command" in invocation
                or direct_policy != "unsupported"
                or not isinstance(input_artifacts, list)
                or not isinstance(output_artifacts, list)
            ):
                invalid_invocation_contract.append(
                    {
                        "step": step.get("name"),
                        "canonical_step_command": canonical,
                        "has_internal_step_command": "internal_step_command" in invocation,
                        "direct_file_invocation": direct_policy,
                        "input_artifacts_type": type(input_artifacts).__name__,
                        "output_artifacts_type": type(output_artifacts).__name__,
                    }
                )
            expected_diagnostic_command = STEP_DIAGNOSTIC_COMMANDS.get(str(step.get("name") or ""))
            if expected_diagnostic_command and invocation.get("diagnostic_module_command") != expected_diagnostic_command:
                invalid_diagnostic_commands.append(
                    {
                        "step": step.get("name"),
                        "expected": expected_diagnostic_command,
                        "actual": invocation.get("diagnostic_module_command"),
                    }
                )
            if not isinstance(explicit_closure, dict):
                invalid_explicit_step_closure_contract.append(
                    {"step": step.get("name"), "reason": "missing_explicit_step_closure"}
                )
            else:
                tier = str(explicit_closure.get("closure_tier") or "")
                count = explicit_closure.get("dependency_closure_count")
                sample = explicit_closure.get("dependency_closure_sample")
                freshness = str(explicit_closure.get("freshness_claim") or "")
                if (
                    explicit_closure.get("closure_policy") != "transitive_dependencies_by_default"
                    or tier not in valid_explicit_step_closure_tiers
                    or not isinstance(count, int)
                    or count < 1
                    or not isinstance(sample, list)
                    or not freshness
                ):
                    invalid_explicit_step_closure_contract.append(
                        {
                            "step": step.get("name"),
                            "closure_policy": explicit_closure.get("closure_policy"),
                            "closure_tier": tier,
                            "dependency_closure_count": count,
                            "sample_type": type(sample).__name__,
                            "freshness_claim": freshness,
                        }
                    )
                if tier in {"broad", "global"} and not str(explicit_closure.get("operator_warning") or "").strip():
                    broad_explicit_steps_without_warning.append(
                        {
                            "step": step.get("name"),
                            "closure_tier": tier,
                            "dependency_closure_count": count,
                        }
                    )

        guidance = profile_guidance.get(slug) if isinstance(profile_guidance.get(slug), dict) else {}
        if not guidance:
            guidance = contract.get("profile_guidance") if isinstance(contract, dict) else {}
        if isinstance(guidance, dict) and guidance:
            cost_tier = str(guidance.get("cost_tier") or "")
            daily_behavior = str(guidance.get("daily_behavior") or "")
            watchdog_behavior = str(guidance.get("watchdog_behavior") or "")
            preferred = guidance.get("preferred_profiles", [])
            preferred = preferred if isinstance(preferred, list) else []
            invalid_profiles = [str(item) for item in preferred if str(item) not in valid_profiles]
            invalid_behaviors = [
                {"field": "daily_behavior", "value": daily_behavior}
                for _ in [0]
                if daily_behavior and daily_behavior not in valid_step_behaviors
            ]
            if watchdog_behavior and watchdog_behavior not in valid_step_behaviors:
                invalid_behaviors.append({"field": "watchdog_behavior", "value": watchdog_behavior})
            if cost_tier not in valid_cost_tiers or invalid_profiles or invalid_behaviors:
                invalid_profile_guidance.append(
                    {
                        "step": step.get("name"),
                        "cost_tier": cost_tier,
                        "invalid_profiles": invalid_profiles,
                        "invalid_behaviors": invalid_behaviors,
                    }
                )
            if cost_tier in {"medium_high", "high"} and not watchdog_behavior:
                missing_watchdog_guidance.append(step.get("name"))
            if (
                cost_tier in {"medium_high", "high"}
                and bool(guidance.get("optimization_candidate"))
                and daily_behavior == "skipped"
                and watchdog_behavior == "skipped"
                and not guidance.get("cache_strategy")
                and "consume existing" not in str(guidance.get("rationale") or "").lower()
                and "explicit" not in str(guidance.get("rationale") or "").lower()
            ):
                optimization_without_cache_or_rationale.append(step.get("name"))

    for slug in profile_guidance:
        if normalize_step_slug(slug) not in known_slugs:
            guidance_for_unknown_steps.append(slug)

    smart_trigger_step_sets = policy.get("smart_trigger_step_sets", {}) if isinstance(policy, dict) else {}
    if not isinstance(smart_trigger_step_sets, dict):
        invalid_smart_trigger_step_sets.append({"reason": "smart_trigger_step_sets_not_object"})
    else:
        for set_name, set_config in sorted(smart_trigger_step_sets.items()):
            if not isinstance(set_config, dict):
                invalid_smart_trigger_step_sets.append({"set": set_name, "reason": "set_config_not_object"})
                continue
            if not str(set_config.get("description") or "").strip():
                invalid_smart_trigger_step_sets.append({"set": set_name, "reason": "missing_description"})
            for field in ("keep_slugs", "remove_slugs"):
                if field not in set_config:
                    continue
                values = set_config.get(field)
                if not isinstance(values, list) or not values:
                    invalid_smart_trigger_step_sets.append({"set": set_name, "field": field, "reason": "empty_or_not_list"})
                    continue
                unknown = sorted(
                    normalize_step_slug(item)
                    for item in values
                    if normalize_step_slug(item) not in known_slugs
                )
                if unknown:
                    invalid_smart_trigger_step_sets.append(
                        {"set": set_name, "field": field, "unknown_step_slugs": unknown}
                    )

    semantic_trigger_rules = policy.get("watchdog_semantic_trigger_rules", {}) if isinstance(policy, dict) else {}
    if not isinstance(semantic_trigger_rules, dict) or not semantic_trigger_rules:
        invalid_watchdog_semantic_trigger_rules.append({"reason": "watchdog_semantic_trigger_rules_missing_or_empty"})
    else:
        for rule_id, rule in sorted(semantic_trigger_rules.items()):
            if not isinstance(rule, dict):
                invalid_watchdog_semantic_trigger_rules.append({"rule": rule_id, "reason": "rule_not_object"})
                continue
            step_set = str(rule.get("step_set") or "")
            if step_set not in smart_trigger_step_sets:
                invalid_watchdog_semantic_trigger_rules.append(
                    {"rule": rule_id, "reason": "unknown_step_set", "step_set": step_set}
                )
            if str(rule.get("unknown_behavior") or "") not in {"keep_for_safety", "skip_with_warning"}:
                invalid_watchdog_semantic_trigger_rules.append(
                    {"rule": rule_id, "reason": "invalid_unknown_behavior"}
                )
            if not any(
                isinstance(rule.get(field), list) and rule.get(field)
                for field in ("truthy_object_fields", "feature_prefixes")
            ):
                invalid_watchdog_semantic_trigger_rules.append(
                    {"rule": rule_id, "reason": "no_semantic_evidence_selector"}
                )

    daily_policy = policy.get("execution_profiles", {}).get("daily", {}) if isinstance(policy, dict) else {}
    daily_keep_slugs = set()
    if isinstance(daily_policy, dict) and isinstance(daily_policy.get("keep_slugs"), list):
        daily_keep_slugs = {
            normalize_step_slug(item)
            for item in daily_policy.get("keep_slugs", [])
            if str(item).strip()
        }
    for slug, guidance in sorted(profile_guidance.items()):
        if not isinstance(guidance, dict):
            continue
        norm_slug = normalize_step_slug(slug)
        preferred = guidance.get("preferred_profiles", [])
        preferred_profiles = {str(item) for item in preferred} if isinstance(preferred, list) else set()
        daily_behavior = str(guidance.get("daily_behavior") or "")
        if "daily" not in preferred_profiles and (daily_behavior == "kept" or norm_slug in daily_keep_slugs):
            daily_profile_contradictions.append(
                {
                    "step": slug,
                    "daily_behavior": daily_behavior,
                    "in_daily_keep": norm_slug in daily_keep_slugs,
                    "preferred_profiles": sorted(preferred_profiles),
                }
            )
        if norm_slug in daily_forbidden_heavy_surface_slugs and (
            daily_behavior != "skipped" or norm_slug in daily_keep_slugs
        ):
            daily_forbidden_kept.append(
                {
                    "step": slug,
                    "daily_behavior": daily_behavior,
                    "in_daily_keep": norm_slug in daily_keep_slugs,
                }
            )

    mode_items = sorted(policy_modes.items()) if isinstance(policy_modes, dict) else []
    for mode_name, mode_config in mode_items:
        if not isinstance(mode_config, dict):
            invalid_execution_modes.append({"mode": mode_name, "reason": "mode config is not an object"})
            continue
        missing_fields = [
            field
            for field in sorted(required_execution_mode_fields)
            if field not in mode_config
            or mode_config.get(field) in ("", None, [])
        ]
        if missing_fields:
            incomplete_execution_modes.append({"mode": mode_name, "missing_fields": missing_fields})
        profile = str(mode_config.get("profile") or "")
        if profile not in valid_profiles:
            invalid_execution_modes.append(
                {"mode": mode_name, "profile": profile, "reason": "unknown execution profile"}
            )
        for list_field in ("required_artifacts", "required_validators"):
            if not isinstance(mode_config.get(list_field), list):
                invalid_execution_modes.append(
                    {"mode": mode_name, "field": list_field, "reason": "field must be a list"}
                )
        if mode_name == "release_deep":
            validators = {
                str(item)
                for item in mode_config.get("required_validators", [])
                if str(item).strip()
            } if isinstance(mode_config.get("required_validators"), list) else set()
            missing_release_deep_ssot_validators = sorted(release_deep_required_ssot_validators - validators)
        if mode_name == "watchdog_save_pulse":
            try:
                scoped_contract = load_watchdog_runtime_contract()
                if (
                    mode_config.get("runtime_cache_policy") != "skip_unrelated_broad_pre_warm"
                    or "watchdog_audit_report" not in mode_config.get("required_artifacts", [])
                    or scoped_contract.get("scope_transport") != "direct_pipeline_argument"
                ):
                    invalid_watchdog_scoped_evidence_contract.append(
                        {"mode": mode_name, "reason": "watchdog scoped runtime/cache contract is incomplete"}
                    )
            except Exception as exc:
                invalid_watchdog_scoped_evidence_contract.append({"mode": mode_name, "reason": str(exc)})
            debt_policy = mode_config.get("deep_proof_debt_policy") if isinstance(mode_config.get("deep_proof_debt_policy"), dict) else {}
            proof_debt_validation = validate_watchdog_proof_debt_policy(debt_policy)
            if not proof_debt_validation["valid"]:
                invalid_watchdog_proof_debt_policy.append({"mode": mode_name, **proof_debt_validation})
            auto_refresh = debt_policy.get("auto_refresh") if isinstance(debt_policy.get("auto_refresh"), dict) else {}
            commands = auto_refresh.get("command_by_mode") if isinstance(auto_refresh.get("command_by_mode"), dict) else {}
            timeouts = auto_refresh.get("timeout_seconds_by_mode") if isinstance(auto_refresh.get("timeout_seconds_by_mode"), dict) else {}
            missing_timeout_modes = []
            invalid_timeout_modes = []
            for refresh_mode, command_args in (sorted(commands.items()) if isinstance(commands, dict) else []):
                if not str(refresh_mode).startswith("auto_") or not isinstance(command_args, list) or not command_args:
                    continue
                if refresh_mode not in timeouts:
                    missing_timeout_modes.append(refresh_mode)
                    continue
                try:
                    timeout_seconds = int(timeouts.get(refresh_mode))
                except Exception:
                    timeout_seconds = 0
                if timeout_seconds <= 0 or timeout_seconds > 7200:
                    invalid_timeout_modes.append({"mode": refresh_mode, "timeout_seconds": timeouts.get(refresh_mode)})
            if missing_timeout_modes or invalid_timeout_modes:
                invalid_watchdog_auto_refresh_timeouts.append(
                    {
                        "mode": mode_name,
                        "reason": "watchdog auto-refresh commands must declare bounded positive timeouts",
                        "missing_timeout_modes": missing_timeout_modes,
                        "invalid_timeout_modes": invalid_timeout_modes,
                    }
                )

    if not isinstance(validator_environment_classes, dict) or not valid_validator_environment_classes.issubset(
        set(validator_environment_classes.keys())
    ):
        invalid_validator_preconditions.append(
            {
                "reason": "missing_validator_environment_classes",
                "expected": sorted(valid_validator_environment_classes),
                "actual": sorted(validator_environment_classes.keys()) if isinstance(validator_environment_classes, dict) else [],
            }
        )
    if not isinstance(validator_contracts, dict) or not validator_contracts:
        invalid_validator_preconditions.append({"reason": "missing_validator_contracts"})
    else:
        expected_fallback_args = {
            "ai_context",
            "force",
            "from_step",
            "full",
            "profile",
            "projects",
            "scope",
            "skip_audit",
            "smart_trigger",
            "step",
        }
        missing_fallback_args = sorted(expected_fallback_args - set(registry_fallback_catalog_args.keys()))
        if (
            missing_fallback_args
            or registry_fallback_catalog_args.get("profile") not in valid_profiles
            or registry_fallback_catalog_args.get("full") is not True
            or registry_fallback_catalog_args.get("ai_context") is not True
        ):
            invalid_registry_fallback_catalog_args.append(
                {
                    "missing_fields": missing_fallback_args,
                    "profile": registry_fallback_catalog_args.get("profile"),
                    "full": registry_fallback_catalog_args.get("full"),
                    "ai_context": registry_fallback_catalog_args.get("ai_context"),
                }
            )
        missing_heavy_validators = sorted(required_heavy_validator_preconditions - set(validator_contracts.keys()))
        if missing_heavy_validators:
            invalid_heavy_validator_preconditions.append(
                {"reason": "missing_heavy_validator_preconditions", "validators": missing_heavy_validators}
            )
        for validator_id, contract in sorted(validator_contracts.items()):
            if not isinstance(contract, dict):
                invalid_validator_preconditions.append({"validator": validator_id, "reason": "contract_not_object"})
                continue
            command = str(contract.get("command") or "")
            environment_class = str(contract.get("environment_class") or "")
            clean_source_allowed = contract.get("clean_source_allowed")
            clean_mirror_behavior = str(contract.get("clean_mirror_behavior") or "")
            required_artifacts = contract.get("required_artifacts")
            wrong_context_behavior = str(contract.get("wrong_context_behavior") or "")
            observability = contract.get("observability")
            duration_guidance_id = str(contract.get("local_duration_guidance_id") or "").strip()
            if (
                not command.startswith("python ")
                or environment_class not in valid_validator_environment_classes
                or not isinstance(required_artifacts, list)
                or not wrong_context_behavior
            ):
                invalid_validator_preconditions.append(
                    {
                        "validator": validator_id,
                        "command": command,
                        "environment_class": environment_class,
                        "required_artifacts_type": type(required_artifacts).__name__,
                        "wrong_context_behavior": wrong_context_behavior,
                    }
                )
            if environment_class == "source_clean" and (
                required_artifacts or contract.get("must_not_require_generated_output") is not True
            ):
                clean_source_validators_requiring_generated_output.append(
                    {
                        "validator": validator_id,
                        "required_artifacts": required_artifacts,
                        "must_not_require_generated_output": contract.get("must_not_require_generated_output"),
                    }
                )
            if environment_class in {"post_analysis", "release_deep", "external_target"} and not required_artifacts:
                generated_artifact_validators_without_artifacts.append(
                    {"validator": validator_id, "environment_class": environment_class}
                )
            if duration_guidance_id and duration_guidance_id not in local_duration_guidance:
                invalid_duration_guidance_consumers.append(
                    {"validator": validator_id, "local_duration_guidance_id": duration_guidance_id}
                )
            expected_clean_source_allowed = environment_class == "source_clean"
            expected_clean_mirror_behavior = expected_clean_mirror_behavior_by_environment.get(environment_class)
            if (
                not isinstance(clean_source_allowed, bool)
                or clean_source_allowed is not expected_clean_source_allowed
                or clean_mirror_behavior not in valid_clean_mirror_behaviors
                or clean_mirror_behavior != expected_clean_mirror_behavior
            ):
                validators_with_invalid_clean_mirror_behavior.append(
                    {
                        "validator": validator_id,
                        "environment_class": environment_class,
                        "clean_source_allowed": clean_source_allowed,
                        "expected_clean_source_allowed": expected_clean_source_allowed,
                        "clean_mirror_behavior": clean_mirror_behavior,
                        "expected_clean_mirror_behavior": expected_clean_mirror_behavior,
                    }
                )
            if not isinstance(observability, dict):
                validators_without_observability_contract.append(
                    {"validator": validator_id, "reason": "missing_observability_contract"}
                )
            else:
                console_progress = str(observability.get("console_progress") or "")
                silence_policy = str(observability.get("silence_policy") or "")
                if (
                    console_progress not in valid_validator_console_progress
                    or silence_policy not in valid_validator_silence_policies
                    or not isinstance(observability.get("writes_machine_artifact"), bool)
                    or not isinstance(observability.get("writes_human_report"), bool)
                    or not isinstance(observability.get("records_step_telemetry"), bool)
                ):
                    validators_without_observability_contract.append(
                        {
                            "validator": validator_id,
                            "console_progress": console_progress,
                            "silence_policy": silence_policy,
                            "writes_machine_artifact_type": type(observability.get("writes_machine_artifact")).__name__,
                            "writes_human_report_type": type(observability.get("writes_human_report")).__name__,
                            "records_step_telemetry_type": type(observability.get("records_step_telemetry")).__name__,
                        }
                    )
            if isinstance(observability, dict):
                heavy_validator_observability_invalid = (
                    observability.get("console_progress") != "per_step"
                    or observability.get("silence_policy") != "start_pass_fail_per_step"
                )
            else:
                heavy_validator_observability_invalid = True
            heavy_validator_boundary_invalid = (
                environment_class != "release_deep"
                or clean_source_allowed is not False
                or clean_mirror_behavior != "release_deep_only"
                or str(contract.get("clean_mirror_rule") or "") == ""
                or heavy_validator_observability_invalid
            )
            if validator_id in required_heavy_validator_preconditions and heavy_validator_boundary_invalid:
                invalid_heavy_validator_preconditions.append(
                    {
                        "validator": validator_id,
                        "environment_class": environment_class,
                        "clean_source_allowed": clean_source_allowed,
                        "clean_mirror_behavior": clean_mirror_behavior,
                        "clean_mirror_rule": contract.get("clean_mirror_rule"),
                        "observability": observability,
                    }
                )

    if not isinstance(report_profile_guidance, dict) or not report_profile_guidance:
        invalid_report_profile_guidance.append({"reason": "missing_report_profile_guidance"})
    else:
        for report_id, guidance in sorted(report_profile_guidance.items()):
            if not isinstance(guidance, dict):
                invalid_report_profile_guidance.append({"report": report_id, "reason": "guidance_not_object"})
                continue
            preferred = guidance.get("preferred_profiles", [])
            preferred_profiles = {str(item) for item in preferred} if isinstance(preferred, list) else set()
            invalid_profiles = sorted(preferred_profiles - valid_report_profiles)
            if (
                not str(guidance.get("command") or "").startswith("python ")
                or str(guidance.get("cost_tier") or "") not in valid_cost_tiers
                or str(guidance.get("daily_behavior") or "") not in valid_step_behaviors
                or str(guidance.get("watchdog_behavior") or "") not in valid_step_behaviors
                or invalid_profiles
                or not str(guidance.get("profile_boundary") or "")
                or guidance.get("telemetry_required") is not True
                or not str(guidance.get("rationale") or "")
            ):
                invalid_report_profile_guidance.append(
                    {
                        "report": report_id,
                        "command": guidance.get("command"),
                        "cost_tier": guidance.get("cost_tier"),
                        "daily_behavior": guidance.get("daily_behavior"),
                        "watchdog_behavior": guidance.get("watchdog_behavior"),
                        "invalid_profiles": invalid_profiles,
                        "profile_boundary": guidance.get("profile_boundary"),
                        "telemetry_required": guidance.get("telemetry_required"),
                    }
                )

    red_line_rules = red_lines.get("rules", []) if isinstance(red_lines, dict) else []
    red_line_ids = {
        str(rule.get("id") or "")
        for rule in red_line_rules
        if isinstance(rule, dict) and str(rule.get("id") or "").strip()
    } if isinstance(red_line_rules, list) else set()
    missing_red_lines = sorted(required_validation_red_lines - red_line_ids)
    malformed_red_lines = [
        rule
        for rule in red_line_rules
        if not isinstance(rule, dict) or not str(rule.get("id") or "").strip() or not str(rule.get("rule") or "").strip()
    ] if isinstance(red_line_rules, list) else ["rules_not_list"]
    if missing_red_lines or malformed_red_lines or not str(red_lines.get("purpose") if isinstance(red_lines, dict) else "").strip():
        invalid_validation_red_lines.append(
            {
                "missing": missing_red_lines,
                "malformed_count": len(malformed_red_lines),
                "has_purpose": bool(str(red_lines.get("purpose") if isinstance(red_lines, dict) else "").strip()),
            }
        )

    if not isinstance(project_scope_policy, dict):
        invalid_project_scope_policy.append({"reason": "project_scope_policy_not_object"})
    else:
        missing_scope_fields = sorted(
            field
            for field in required_project_scope_fields
            if field not in project_scope_policy
        )
        blank_scope_fields = sorted(
            field
            for field in required_project_scope_fields - {"allowed_non_main_contexts"}
            if field in project_scope_policy and not str(project_scope_policy.get(field) or "").strip()
        )
        allowed_non_main_contexts = project_scope_policy.get("allowed_non_main_contexts", [])
        if isinstance(allowed_non_main_contexts, list):
            allowed_contexts = {str(item) for item in allowed_non_main_contexts if str(item).strip()}
        else:
            allowed_contexts = set()
        missing_contexts = sorted(required_non_main_contexts - allowed_contexts)
        if (
            missing_scope_fields
            or blank_scope_fields
            or missing_contexts
            or str(project_scope_policy.get("default_agent_edit_scope") or "") != "MAIN"
            or str(project_scope_policy.get("variation_visibility") or "") != "explicit_only"
            or str(project_scope_policy.get("companion_visibility") or "") != "explicit_only"
        ):
            invalid_project_scope_policy.append(
                {
                    "missing_fields": missing_scope_fields,
                    "blank_fields": blank_scope_fields,
                    "missing_allowed_non_main_contexts": missing_contexts,
                    "default_agent_edit_scope": project_scope_policy.get("default_agent_edit_scope"),
                    "variation_visibility": project_scope_policy.get("variation_visibility"),
                    "companion_visibility": project_scope_policy.get("companion_visibility"),
                }
            )

    if not isinstance(subprocess_runtime_policy, dict):
        invalid_subprocess_runtime_policy.append({"reason": "subprocess_runtime_policy_not_object"})
    else:
        missing_fields = sorted(required_subprocess_runtime_policy_fields - set(subprocess_runtime_policy.keys()))
        streaming_exceptions = subprocess_runtime_policy.get("streaming_exceptions", {})
        short_exceptions = subprocess_runtime_policy.get("short_bounded_exceptions", {})
        test_only_exceptions = subprocess_runtime_policy.get("test_only_exceptions", {})
        protocol_surfaces = subprocess_runtime_policy.get("protocol_surfaces", {})
        missing_streaming = (
            sorted(required_subprocess_streaming_exceptions - set(streaming_exceptions.keys()))
            if isinstance(streaming_exceptions, dict)
            else sorted(required_subprocess_streaming_exceptions)
        )
        missing_short = (
            sorted(required_subprocess_short_exceptions - set(short_exceptions.keys()))
            if isinstance(short_exceptions, dict)
            else sorted(required_subprocess_short_exceptions)
        )
        malformed_exception_entries = []
        for section_name, section in {
            "streaming_exceptions": streaming_exceptions,
            "short_bounded_exceptions": short_exceptions,
            "test_only_exceptions": test_only_exceptions,
        }.items():
            if not isinstance(section, dict):
                malformed_exception_entries.append({"section": section_name, "reason": "not_object"})
                continue
            for key, value in section.items():
                if (
                    not isinstance(value, dict)
                    or not str(value.get("classification") or "").strip()
                    or not str(value.get("rationale") or "").strip()
                ):
                    malformed_exception_entries.append({"section": section_name, "key": key})
        if (
            missing_fields
            or missing_streaming
            or missing_short
            or malformed_exception_entries
            or "run_observed_subprocess" not in str(subprocess_runtime_policy.get("default_rule") or "")
            or not isinstance(protocol_surfaces, dict)
            or "log=null" not in str(protocol_surfaces.get("rule") or "")
        ):
            invalid_subprocess_runtime_policy.append(
                {
                    "missing_fields": missing_fields,
                    "missing_streaming_exceptions": missing_streaming,
                    "missing_short_bounded_exceptions": missing_short,
                    "malformed_exception_entries": malformed_exception_entries,
                    "protocol_surfaces": protocol_surfaces,
                }
            )

    if not isinstance(manual_validator_execution_policy, dict):
        invalid_manual_validator_execution_policy.append({"reason": "manual_validator_execution_policy_not_object"})
    else:
        default_probe_steps = manual_validator_execution_policy.get("default_probe_steps")
        missing_fields = sorted(
            {
                "default_probe_steps",
                "mode",
                "sqlite_writer_rule",
                "parallel_intent_behavior",
                "max_parallel_sqlite_writers",
            }
            - set(manual_validator_execution_policy.keys())
        )
        if (
            missing_fields
            or not isinstance(default_probe_steps, list)
            or {"source_snapshot_store", "symbol_span_integrity"} - {str(item) for item in default_probe_steps or []}
            or str(manual_validator_execution_policy.get("mode") or "") != "plan_only_by_default"
            or str(manual_validator_execution_policy.get("parallel_intent_behavior") or "") != "fail_closed_with_serial_execution_plan"
            or manual_validator_execution_policy.get("max_parallel_sqlite_writers") != 1
        ):
            invalid_manual_validator_execution_policy.append(
                {
                    "missing_fields": missing_fields,
                    "default_probe_steps": default_probe_steps,
                    "mode": manual_validator_execution_policy.get("mode"),
                    "parallel_intent_behavior": manual_validator_execution_policy.get("parallel_intent_behavior"),
                    "max_parallel_sqlite_writers": manual_validator_execution_policy.get("max_parallel_sqlite_writers"),
                }
            )

    if not isinstance(operational_limits, dict):
        invalid_operational_limits.append({"reason": "operational_limits_not_object"})
    else:
        missing_limits = sorted(required_operational_limits - set(operational_limits.keys()))
        invalid_values = [
            {"key": key, "value": operational_limits.get(key)}
            for key in sorted(required_operational_limits & set(operational_limits.keys()))
            if not isinstance(operational_limits.get(key), int) or int(operational_limits.get(key)) <= 0
        ]
        if missing_limits or invalid_values:
            invalid_operational_limits.append(
                {"missing_limits": missing_limits, "invalid_values": invalid_values}
            )

    if not isinstance(report_surface_limits, dict):
        invalid_report_surface_limits.append({"reason": "report_surface_limits_not_object"})
    else:
        missing_limits = sorted(required_report_surface_limits - set(report_surface_limits.keys()))
        invalid_values = [
            {"key": key, "value": report_surface_limits.get(key)}
            for key in sorted(required_report_surface_limits & set(report_surface_limits.keys()))
            if not isinstance(report_surface_limits.get(key), int) or int(report_surface_limits.get(key)) <= 0
        ]
        if missing_limits or invalid_values:
            invalid_report_surface_limits.append(
                {"missing_limits": missing_limits, "invalid_values": invalid_values}
            )

    if not isinstance(contextos_signal_limits, dict):
        invalid_contextos_signal_limits.append({"reason": "contextos_signal_limits_not_object"})
    else:
        missing_limits = sorted(required_contextos_signal_limits - set(contextos_signal_limits.keys()))
        invalid_values = [
            {"key": key, "value": contextos_signal_limits.get(key)}
            for key in sorted(required_contextos_signal_limits & set(contextos_signal_limits.keys()))
            if not isinstance(contextos_signal_limits.get(key), int) or int(contextos_signal_limits.get(key)) <= 0
        ]
        if missing_limits or invalid_values:
            invalid_contextos_signal_limits.append(
                {"missing_limits": missing_limits, "invalid_values": invalid_values}
            )

    if not isinstance(scoped_local_literals, dict):
        invalid_scoped_local_literals.append({"reason": "scoped_local_literals_not_object"})
    else:
        missing_literals = sorted(required_scoped_local_literals - set(scoped_local_literals.keys()))
        malformed_literals = [
            key
            for key in sorted(required_scoped_local_literals & set(scoped_local_literals.keys()))
            if not isinstance(scoped_local_literals.get(key), dict)
            or not str(scoped_local_literals[key].get("owner") or "").strip()
            or not str(scoped_local_literals[key].get("classification") or "").strip()
            or not str(scoped_local_literals[key].get("rationale") or "").strip()
        ]
        if missing_literals or malformed_literals:
            invalid_scoped_local_literals.append(
                {"missing_literals": missing_literals, "malformed_literals": malformed_literals}
            )

    if not isinstance(pipeline_lock, dict):
        invalid_pipeline_lock_policy.append({"reason": "pipeline_lock_not_object"})
    else:
        missing_fields = sorted(required_pipeline_lock_policy_fields - set(pipeline_lock.keys()))
        endpoint = str(pipeline_lock.get("endpoint") or "")
        diagnostic_endpoint = str(pipeline_lock.get("diagnostic_endpoint") or "")
        invalid_values = {
            "endpoint": endpoint,
            "diagnostic_endpoint": diagnostic_endpoint,
            "protocol": pipeline_lock.get("protocol"),
            "scope": pipeline_lock.get("scope"),
            "persistent_endpoint": pipeline_lock.get("persistent_endpoint"),
            "holder_decision_source": pipeline_lock.get("holder_decision_source"),
            "metadata_role": pipeline_lock.get("metadata_role"),
            "stale_recovery": pipeline_lock.get("stale_recovery"),
            "release_state": pipeline_lock.get("release_state"),
        }
        if (
            missing_fields
            or not endpoint
            or Path(endpoint).is_absolute()
            or not diagnostic_endpoint
            or Path(diagnostic_endpoint).is_absolute()
            or diagnostic_endpoint == endpoint
            or bool(pipeline_lock.get("persistent_endpoint")) is not True
            or str(pipeline_lock.get("holder_decision_source") or "") != "os_advisory_lock"
            or str(pipeline_lock.get("metadata_role") or "") != "diagnostic_only"
            or str(pipeline_lock.get("stale_recovery") or "") != "no_ttl_or_pid_eviction"
        ):
            invalid_pipeline_lock_policy.append(
                {"missing_fields": missing_fields, "invalid_values": invalid_values}
            )

    if not isinstance(heartbeat_cadence, dict):
        invalid_heartbeat_cadence.append({"reason": "heartbeat_cadence_not_object"})
    else:
        missing_fields = sorted(required_heartbeat_cadence_fields - set(heartbeat_cadence.keys()))
        scope_trace_types = heartbeat_cadence.get("scope_trace_types")
        numeric_keys = (
            "bootstrap_seconds",
            "minimum_seconds",
            "maximum_seconds",
            "minimum_samples",
            "max_samples",
            "target_updates_per_observed_run",
        )
        invalid_numbers = {
            key: heartbeat_cadence.get(key)
            for key in numeric_keys
            if not isinstance(heartbeat_cadence.get(key), int) or int(heartbeat_cadence.get(key)) <= 0
        }
        if (
            missing_fields
            or invalid_numbers
            or int(heartbeat_cadence.get("minimum_seconds") or 0) > int(heartbeat_cadence.get("maximum_seconds") or 0)
            or int(heartbeat_cadence.get("minimum_samples") or 0) > int(heartbeat_cadence.get("max_samples") or 0)
            or str(heartbeat_cadence.get("telemetry_artifact") or "") != "telemetry_traces"
            or str(heartbeat_cadence.get("selection") or "") != "median_duration_divided_by_target_updates_clamped"
            or not isinstance(scope_trace_types, dict)
            or not all(isinstance(value, list) and value for value in (scope_trace_types or {}).values())
        ):
            invalid_heartbeat_cadence.append(
                {"missing_fields": missing_fields, "invalid_numbers": invalid_numbers, "scope_trace_types": scope_trace_types}
            )

    if not isinstance(local_duration_guidance, dict) or not local_duration_guidance:
        invalid_local_duration_guidance.append({"reason": "local_duration_guidance_not_object_or_empty"})
    else:
        for guidance_id, contract in sorted(local_duration_guidance.items()):
            missing_fields = sorted(required_local_duration_guidance_fields - set(contract.keys())) if isinstance(contract, dict) else sorted(required_local_duration_guidance_fields)
            phases = contract.get("phases") if isinstance(contract, dict) else None
            invalid_numeric_fields = {
                field: contract.get(field) if isinstance(contract, dict) else None
                for field in ("sample_limit", "minimum_samples_per_phase")
                if not isinstance(contract.get(field), int) or int(contract.get(field) or 0) <= 0
            }
            if (
                missing_fields
                or invalid_numeric_fields
                or not isinstance(phases, dict)
                or not phases
                or not all(str(phase_id).strip() and str(identifier).strip() for phase_id, identifier in phases.items())
                or str(contract.get("telemetry_artifact") or "") not in allowed_local_duration_telemetry_artifacts
                or not str(contract.get("trace_type") or "").strip()
                or not str(contract.get("claim_boundary") or "").strip()
            ):
                invalid_local_duration_guidance.append(
                    {
                        "guidance_id": str(guidance_id),
                        "missing_fields": missing_fields,
                        "invalid_numeric_fields": invalid_numeric_fields,
                        "phases": phases,
                    }
                )

    checks = [
        _check("pipeline_step_registry_has_steps", bool(steps), {"steps": len(steps)}),
        _check("all_steps_have_execution_contract", not missing_contract, missing_contract),
        _check("all_steps_have_invocation_contract", not missing_invocation_contract, missing_invocation_contract),
        _check("invocation_contracts_are_canonical", not invalid_invocation_contract, invalid_invocation_contract),
        _check("diagnostic_commands_preserve_step_modes", not invalid_diagnostic_commands, invalid_diagnostic_commands),
        _check("explicit_step_closure_contracts_are_complete", not invalid_explicit_step_closure_contract, invalid_explicit_step_closure_contract),
        _check("broad_explicit_steps_warn_operator", not broad_explicit_steps_without_warning, broad_explicit_steps_without_warning),
        _check("scheduler_classes_are_known", not invalid_scheduler, invalid_scheduler),
        _check("sqlite_writer_matches_artifact_writes", not sqlite_writer_mismatches, sqlite_writer_mismatches),
        _check("finalizer_steps_are_not_parallel_safe", not unsafe_finalizers, unsafe_finalizers),
        _check("all_execution_contracts_explain_reasons", not unreasoned_steps, unreasoned_steps),
        _check("execution_profiles_are_exported", valid_profiles.issubset(profile_names), sorted(profile_names)),
        _check(
            "claim_owned_target_proof_profiles_are_dependency_closed",
            claim_owned_profiles.get("status") == "PASS",
            claim_owned_profiles,
        ),
        _check("profile_guidance_targets_known_steps", not guidance_for_unknown_steps, guidance_for_unknown_steps),
        _check("profile_guidance_uses_known_values", not invalid_profile_guidance, invalid_profile_guidance),
        _check("daily_profile_matches_step_guidance", not daily_profile_contradictions, daily_profile_contradictions),
        _check("daily_profile_excludes_merge_oracle_surfaces", not daily_forbidden_kept, daily_forbidden_kept),
        _check("costly_steps_declare_watchdog_behavior", not missing_watchdog_guidance, missing_watchdog_guidance),
        _check("optimized_heavy_steps_declare_cache_or_explicit_refresh_rationale", not optimization_without_cache_or_rationale, optimization_without_cache_or_rationale),
        _check("runtime_named_static_steps_declare_execution_boundary", not invalid_static_runtime_boundaries, invalid_static_runtime_boundaries),
        _check("execution_modes_are_declared", required_execution_modes.issubset(policy_mode_names), sorted(policy_mode_names)),
        _check("execution_modes_are_exported", required_execution_modes.issubset(mode_names), sorted(mode_names)),
        _check("execution_modes_are_complete", not incomplete_execution_modes, incomplete_execution_modes),
        _check("execution_modes_use_known_profiles", not invalid_execution_modes, invalid_execution_modes),
        _check("release_deep_requires_sqlite_first_ssot_validators", not missing_release_deep_ssot_validators, missing_release_deep_ssot_validators),
        _check("validator_preconditions_are_declared", not invalid_validator_preconditions, invalid_validator_preconditions),
        _check("heavy_validator_preconditions_are_release_deep_only", not invalid_heavy_validator_preconditions, invalid_heavy_validator_preconditions),
        _check("clean_source_validators_do_not_require_generated_output", not clean_source_validators_requiring_generated_output, clean_source_validators_requiring_generated_output),
        _check("generated_artifact_validators_declare_required_artifacts", not generated_artifact_validators_without_artifacts, generated_artifact_validators_without_artifacts),
        _check("validator_clean_mirror_behavior_is_machine_readable", not validators_with_invalid_clean_mirror_behavior, validators_with_invalid_clean_mirror_behavior),
        _check("validator_observability_contracts_are_declared", not validators_without_observability_contract, validators_without_observability_contract),
        _check("report_profile_guidance_is_declared", not invalid_report_profile_guidance, invalid_report_profile_guidance),
        _check("validation_governance_red_lines_are_declared", not invalid_validation_red_lines, invalid_validation_red_lines),
        _check("project_scope_policy_is_declared", not invalid_project_scope_policy, invalid_project_scope_policy),
        _check("subprocess_runtime_policy_is_declared", not invalid_subprocess_runtime_policy, invalid_subprocess_runtime_policy),
        _check("manual_validator_execution_policy_is_declared", not invalid_manual_validator_execution_policy, invalid_manual_validator_execution_policy),
        _check("operational_limits_are_declared", not invalid_operational_limits, invalid_operational_limits),
        _check("report_surface_limits_are_declared", not invalid_report_surface_limits, invalid_report_surface_limits),
        _check("contextos_signal_limits_are_declared", not invalid_contextos_signal_limits, invalid_contextos_signal_limits),
        _check("scoped_local_literals_are_justified", not invalid_scoped_local_literals, invalid_scoped_local_literals),
        _check("pipeline_lock_policy_is_fail_closed_and_policy_backed", not invalid_pipeline_lock_policy, invalid_pipeline_lock_policy),
        _check("pipeline_steps_have_complete_subject_scope_contracts", not invalid_step_system_scope_policy, invalid_step_system_scope_policy),
        _check("heartbeat_cadence_is_bounded_and_policy_backed", not invalid_heartbeat_cadence, invalid_heartbeat_cadence),
        _check("local_duration_guidance_is_exact_phase_and_policy_backed", not invalid_local_duration_guidance, invalid_local_duration_guidance),
        _check("duration_guidance_consumers_reference_known_policy", not invalid_duration_guidance_consumers, invalid_duration_guidance_consumers),
        _check("watchdog_auto_refresh_timeouts_are_bounded", not invalid_watchdog_auto_refresh_timeouts, invalid_watchdog_auto_refresh_timeouts),
        _check("watchdog_proof_debt_policy_is_complete", not invalid_watchdog_proof_debt_policy, invalid_watchdog_proof_debt_policy),
        _check(
            "watchdog_scoped_evidence_contract_is_complete",
            not invalid_watchdog_scoped_evidence_contract,
            invalid_watchdog_scoped_evidence_contract,
        ),
        _check("watchdog_semantic_trigger_rules_are_contract_driven", not invalid_watchdog_semantic_trigger_rules, invalid_watchdog_semantic_trigger_rules),
        _check("smart_trigger_step_sets_target_known_steps", not invalid_smart_trigger_step_sets, invalid_smart_trigger_step_sets),
        _check("pipeline_registry_fallback_catalog_args_are_policy_backed", not invalid_registry_fallback_catalog_args, invalid_registry_fallback_catalog_args),
        _check(
            "release_proof_step_dependencies_form_dag",
            not release_proof_dag["duplicate_ids"]
            and not release_proof_dag["unknown_dependencies"]
            and not release_proof_dag["unresolved_or_cyclic"],
            release_proof_dag,
        ),
        _check(
            "release_proof_runtime_consumers_follow_declared_producers",
            not release_proof_dag["producer_consumer_violations"],
            release_proof_dag,
        ),
        _check(
            "release_proof_timeout_profiles_resolve_central_inner_limits",
            not release_proof_timeout_profiles["invalid_profiles"],
            release_proof_timeout_profiles,
        ),
        _check(
            "release_proof_steps_have_bounded_authority_and_subject_scope",
            release_proof_scope["status"] == "PASS",
            release_proof_scope,
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    scheduler_counts: dict[str, int] = {}
    sqlite_writers = 0
    for step in steps:
        contract = step.get("execution_contract") if isinstance(step.get("execution_contract"), dict) else {}
        scheduler = str(contract.get("scheduler_class") or "missing")
        scheduler_counts[scheduler] = scheduler_counts.get(scheduler, 0) + 1
        if contract.get("sqlite_writer"):
            sqlite_writers += 1

    payload = {
        "meta": {
            "kind": "pipeline_execution_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_pipeline_execution_contract",
        },
        "summary": {
            "status": status,
            "steps": len(steps),
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "scheduler_counts": scheduler_counts,
            "sqlite_writers": sqlite_writers,
            "execution_modes": len(mode_names),
            "claim_owned_profiles": len(claim_owned_profiles.get("plans", {})),
            "validator_preconditions": len(validator_contracts) if isinstance(validator_contracts, dict) else 0,
            "validator_observability_contracts": (
                len(validator_contracts) - len({item.get("validator") for item in validators_without_observability_contract if isinstance(item, dict)})
                if isinstance(validator_contracts, dict)
                else 0
            ),
            "report_profile_guidance": len(report_profile_guidance) if isinstance(report_profile_guidance, dict) else 0,
            "validation_governance_red_lines": len(red_line_ids),
            "operational_limits": len(operational_limits) if isinstance(operational_limits, dict) else 0,
            "report_surface_limits": len(report_surface_limits) if isinstance(report_surface_limits, dict) else 0,
            "contextos_signal_limits": len(contextos_signal_limits) if isinstance(contextos_signal_limits, dict) else 0,
            "scoped_local_literals": len(scoped_local_literals) if isinstance(scoped_local_literals, dict) else 0,
            "smart_trigger_step_sets": len(smart_trigger_step_sets) if isinstance(smart_trigger_step_sets, dict) else 0,
            "watchdog_semantic_trigger_rules": len(semantic_trigger_rules) if isinstance(semantic_trigger_rules, dict) else 0,
            "project_scope_policy": 1 if isinstance(project_scope_policy, dict) and project_scope_policy else 0,
            "subprocess_runtime_policy": 1 if isinstance(subprocess_runtime_policy, dict) and subprocess_runtime_policy else 0,
            "release_proof_steps": release_proof_dag["steps"],
            "release_proof_timeout_profiles": len(release_proof_timeout_profiles["referenced_profiles"]),
            "release_proof_scope_domains": len(release_proof_scope["domain_counts"]),
        },
        "release_proof_dag": release_proof_dag,
        "release_proof_scope": release_proof_scope,
        "claim_owned_execution_profiles": claim_owned_profiles,
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
            for mode, config in sorted(policy_modes.items())
            if isinstance(config, dict)
        },
        "validator_preconditions": {
            str(validator): {
                "environment_class": str(config.get("environment_class") or ""),
                "command": str(config.get("command") or ""),
                "clean_source_allowed": bool(config.get("clean_source_allowed")) if isinstance(config.get("clean_source_allowed"), bool) else None,
                "clean_mirror_behavior": str(config.get("clean_mirror_behavior") or ""),
                "required_artifacts": list(config.get("required_artifacts", []) or []) if isinstance(config, dict) else [],
                "wrong_context_behavior": str(config.get("wrong_context_behavior") or ""),
            }
            for validator, config in sorted(validator_contracts.items())
            if isinstance(config, dict)
        },
        "report_profile_guidance": {
            str(report): {
                "command": str(config.get("command") or ""),
                "cost_tier": str(config.get("cost_tier") or ""),
                "daily_behavior": str(config.get("daily_behavior") or ""),
                "watchdog_behavior": str(config.get("watchdog_behavior") or ""),
                "preferred_profiles": list(config.get("preferred_profiles", []) or []) if isinstance(config, dict) else [],
                "profile_boundary": str(config.get("profile_boundary") or ""),
            }
            for report, config in sorted(report_profile_guidance.items())
            if isinstance(config, dict)
        },
        "validation_governance_red_lines": [
            {"id": str(rule.get("id") or ""), "rule": str(rule.get("rule") or "")}
            for rule in red_line_rules
            if isinstance(rule, dict)
        ] if isinstance(red_line_rules, list) else [],
        "project_scope_policy": {
            "default_agent_edit_scope": str(project_scope_policy.get("default_agent_edit_scope") or "") if isinstance(project_scope_policy, dict) else "",
            "default_agent_read_scope": str(project_scope_policy.get("default_agent_read_scope") or "") if isinstance(project_scope_policy, dict) else "",
            "target_repo_agent_default": str(project_scope_policy.get("target_repo_agent_default") or "") if isinstance(project_scope_policy, dict) else "",
            "variation_visibility": str(project_scope_policy.get("variation_visibility") or "") if isinstance(project_scope_policy, dict) else "",
            "companion_visibility": str(project_scope_policy.get("companion_visibility") or "") if isinstance(project_scope_policy, dict) else "",
            "external_target_scope": str(project_scope_policy.get("external_target_scope") or "") if isinstance(project_scope_policy, dict) else "",
            "allowed_non_main_contexts": list(project_scope_policy.get("allowed_non_main_contexts", []) or []) if isinstance(project_scope_policy, dict) and isinstance(project_scope_policy.get("allowed_non_main_contexts"), list) else [],
            "agent_surface_rule": str(project_scope_policy.get("agent_surface_rule") or "") if isinstance(project_scope_policy, dict) else "",
            "pipeline_rule": str(project_scope_policy.get("pipeline_rule") or "") if isinstance(project_scope_policy, dict) else "",
        },
        "subprocess_runtime_policy": {
            "default_rule": str(subprocess_runtime_policy.get("default_rule") or "") if isinstance(subprocess_runtime_policy, dict) else "",
            "streaming_exceptions": sorted((subprocess_runtime_policy.get("streaming_exceptions") or {}).keys()) if isinstance(subprocess_runtime_policy, dict) and isinstance(subprocess_runtime_policy.get("streaming_exceptions"), dict) else [],
            "short_bounded_exceptions": sorted((subprocess_runtime_policy.get("short_bounded_exceptions") or {}).keys()) if isinstance(subprocess_runtime_policy, dict) and isinstance(subprocess_runtime_policy.get("short_bounded_exceptions"), dict) else [],
            "test_only_exceptions": sorted((subprocess_runtime_policy.get("test_only_exceptions") or {}).keys()) if isinstance(subprocess_runtime_policy, dict) and isinstance(subprocess_runtime_policy.get("test_only_exceptions"), dict) else [],
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "pipeline_execution_contract_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "pipeline_execution_contract_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Pipeline Execution Contract Validation",
        "",
        "Validates scheduler semantics attached to `pipeline_step_registry.json`.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- steps: `{summary.get('steps')}`",
        f"- sqlite_writers: `{summary.get('sqlite_writers')}`",
        f"- execution_modes: `{summary.get('execution_modes')}`",
        f"- validator_preconditions: `{summary.get('validator_preconditions')}`",
        f"- report_profile_guidance: `{summary.get('report_profile_guidance')}`",
        f"- validation_governance_red_lines: `{summary.get('validation_governance_red_lines')}`",
        f"- operational_limits: `{summary.get('operational_limits')}`",
        f"- report_surface_limits: `{summary.get('report_surface_limits')}`",
        f"- contextos_signal_limits: `{summary.get('contextos_signal_limits')}`",
        f"- scoped_local_literals: `{summary.get('scoped_local_literals')}`",
        f"- project_scope_policy: `{summary.get('project_scope_policy')}`",
        f"- release_proof_scope_domains: `{summary.get('release_proof_scope_domains')}`",
        f"- scheduler_counts: `{summary.get('scheduler_counts')}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.extend(
        [
            "",
            "## Execution Modes",
            "",
            "| Mode | Profile | Scope | Heavy Step Policy | Claim Boundary |",
            "|---|---|---|---|---|",
        ]
    )
    for mode, config in sorted((payload.get("execution_modes") or {}).items()):
        lines.append(
            f"| `{mode}` | `{config.get('profile')}` | `{config.get('scope')}` | `{config.get('heavy_step_policy')}` | `{config.get('claim_boundary')}` |"
        )
    lines.extend(
        [
            "",
            "## Validator Preconditions",
            "",
            "| Validator | Environment | Clean Source | Clean Mirror Behavior | Required Artifacts | Wrong Context Behavior |",
            "|---|---|---|---|---|---|",
        ]
    )
    for validator, config in sorted((payload.get("validator_preconditions") or {}).items()):
        artifacts = ", ".join(str(item) for item in config.get("required_artifacts", []))
        lines.append(
            f"| `{validator}` | `{config.get('environment_class')}` | `{config.get('clean_source_allowed')}` | `{config.get('clean_mirror_behavior')}` | `{artifacts}` | `{config.get('wrong_context_behavior')}` |"
        )
    lines.extend(
        [
            "",
            "## Report Profile Guidance",
            "",
            "| Report | Cost | Daily | Watchdog | Preferred Profiles | Boundary |",
            "|---|---|---|---|---|---|",
        ]
    )
    for report, config in sorted((payload.get("report_profile_guidance") or {}).items()):
        profiles = ", ".join(str(item) for item in config.get("preferred_profiles", []))
        lines.append(
            f"| `{report}` | `{config.get('cost_tier')}` | `{config.get('daily_behavior')}` | `{config.get('watchdog_behavior')}` | `{profiles}` | `{config.get('profile_boundary')}` |"
        )
    lines.extend(
        [
            "",
            "## Validation Governance Red Lines",
            "",
            "| Rule | Contract |",
            "|---|---|",
        ]
    )
    for rule in payload.get("validation_governance_red_lines", []):
        lines.append(f"| `{rule.get('id')}` | {rule.get('rule')} |")
    project_scope = payload.get("project_scope_policy") if isinstance(payload.get("project_scope_policy"), dict) else {}
    contexts = ", ".join(str(item) for item in project_scope.get("allowed_non_main_contexts", []))
    lines.extend(
        [
            "",
            "## Project Scope Policy",
            "",
            "| Default Edit Scope | Variation Visibility | Companion Visibility | External Target Scope | Allowed Non-MAIN Contexts |",
            "|---|---|---|---|---|",
            f"| `{project_scope.get('default_agent_edit_scope')}` | `{project_scope.get('variation_visibility')}` | `{project_scope.get('companion_visibility')}` | `{project_scope.get('external_target_scope')}` | `{contexts}` |",
            "",
            f"- agent_surface_rule: {project_scope.get('agent_surface_rule')}",
            f"- pipeline_rule: {project_scope.get('pipeline_rule')}",
        ]
    )
    release_scope = payload.get("release_proof_scope") if isinstance(payload.get("release_proof_scope"), dict) else {}
    lines.extend(
        [
            "",
            "## Release Proof Scope",
            "",
            f"- status: `{release_scope.get('status')}`",
            f"- classified_steps: `{release_scope.get('classified_steps')}/{release_scope.get('steps')}`",
            f"- live_repository_execution_step: `{release_scope.get('live_repository_execution_step')}`",
            f"- live_repository_project_filter: `{release_scope.get('live_repository_project_filter')}`",
            f"- domain_counts: `{release_scope.get('domain_counts')}`",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_pipeline_execution_contract()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

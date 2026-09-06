from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR_FOR_IMPORT))

from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.factorial_evaluation import evaluate_factorial_plans


CONTRACT_PATH = CONFIG_DIR / "external_repository_evaluation_contract.json"
SCHEMA_PATH = CONFIG_DIR / "schemas" / "external_repository_evaluation_contract.schema.json"
POLYGLOT_PATH = CONFIG_DIR / "polyglot_capabilities.json"
HARNESS_PATH = CONFIG_DIR / "agent_harness_contract.json"
PREFLIGHT_PATH = CONFIG_DIR / "external_target_preflight_policy.json"
REGISTRY_PATH = CONFIG_DIR / "external_repository_evaluation_registry.json"
REGISTRY_SCHEMA_PATH = CONFIG_DIR / "schemas" / "external_repository_evaluation_registry.schema.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def evaluate_contract(
    contract: dict[str, Any],
    polyglot: dict[str, Any],
    harness: dict[str, Any],
    preflight: dict[str, Any],
    *,
    human_text: str,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    validation = contract.get("validation_contract", {})
    roles = contract.get("corpus_roles", {})
    outcomes = contract.get("outcome_semantics", {})
    claim_rules = contract.get("claim_level_evaluation", {})

    required_roles = set(validation.get("required_corpus_roles", []))
    checks.append(
        _check(
            "corpus_roles_are_complete",
            set(roles) == required_roles,
            {"required": sorted(required_roles), "actual": sorted(roles)},
        )
    )

    invalid_holdout_roles = {
        name: role
        for name, role in roles.items()
        if role.get("independent_generalization_evidence") is True
        and role.get("may_influence_rules_or_thresholds") is not False
    }
    checks.append(
        _check(
            "independent_roles_forbid_pre_result_tuning",
            not invalid_holdout_roles,
            invalid_holdout_roles or "independent roles cannot tune SAGE before result freeze",
        )
    )

    leakage_states = contract.get("leakage_policy", {}).get("states", {})
    invalid_leakage = {
        name: state
        for name, state in leakage_states.items()
        if name != "unseen_verified" and state.get("holdout_eligible") is True
    }
    checks.append(
        _check(
            "only_verified_unseen_subjects_are_holdout_eligible",
            leakage_states.get("unseen_verified", {}).get("holdout_eligible") is True and not invalid_leakage,
            invalid_leakage or leakage_states,
        )
    )

    polyglot_levels = set(polyglot.get("claim_level_order", []))
    checks.append(
        _check(
            "claim_level_rules_follow_polyglot_authority",
            set(claim_rules) == polyglot_levels,
            {"polyglot": sorted(polyglot_levels), "evaluation": sorted(claim_rules)},
        )
    )

    forbidden_by_level = validation.get("forbidden_domains_by_claim_level", {})
    domain_conflicts: dict[str, list[str]] = {}
    missing_outside_claims: dict[str, list[str]] = {}
    for level, forbidden in forbidden_by_level.items():
        rule = claim_rules.get(level, {})
        eligible = set(rule.get("eligible_test_domains", []))
        outside = set(rule.get("outside_claim_domains", []))
        conflicts = sorted(eligible.intersection(forbidden))
        missing = sorted(set(forbidden) - outside)
        if conflicts:
            domain_conflicts[level] = conflicts
        if missing:
            missing_outside_claims[level] = missing
    checks.append(
        _check(
            "claim_levels_cannot_pass_forbidden_domains",
            not domain_conflicts and not missing_outside_claims,
            {"eligible_conflicts": domain_conflicts, "missing_outside_claims": missing_outside_claims},
        )
    )

    authority_resolution = contract.get("authority_resolution", {})
    checks.append(
        _check(
            "claim_level_is_ceiling_and_requires_active_evidence",
            authority_resolution.get("claim_level_is_ceiling_not_entitlement") is True
            and "active_capability_and_evidence_intersection" in authority_resolution.get("resolution_order", [])
            and "never PASS" in str(authority_resolution.get("missing_active_capability_rule") or ""),
            authority_resolution,
        )
    )

    required_outcomes = set(validation.get("required_outcomes", []))
    checks.append(
        _check(
            "outcome_semantics_are_complete",
            set(outcomes) == required_outcomes,
            {"required": sorted(required_outcomes), "actual": sorted(outcomes)},
        )
    )

    required_task_fields = set(validation.get("required_task_fields", []))
    actual_task_fields = set(contract.get("task_contract", {}).get("required_fields", []))
    checks.append(
        _check(
            "evaluation_tasks_bind_real_failure_mode_and_user_relevance",
            actual_task_fields == required_task_fields
            and {"real_failure_mode", "user_relevance_hypothesis", "expected_evidence"}.issubset(actual_task_fields),
            {"required": sorted(required_task_fields), "actual": sorted(actual_task_fields)},
        )
    )

    harness_baseline = harness.get("evaluation_baseline", {})
    required_harness_fields = {"status", "required_snapshot_fields", "required_variants", "required_metrics", "unknown_value"}
    missing_harness_fields = sorted(required_harness_fields - set(harness_baseline))
    checks.append(
        _check(
            "harness_baseline_is_reused_not_reinvented",
            contract.get("evaluation_run_contract", {}).get("harness_baseline_authority")
            == "config/agent_harness_contract.json#/evaluation_baseline"
            and not missing_harness_fields,
            {"missing_harness_fields": missing_harness_fields},
        )
    )

    factorial = contract.get("factorial_evaluation_contract", {})
    authority_profiles = factorial.get("corpus_authority_profiles", {})
    known_profile = authority_profiles.get("known_regression_mechanics_only", {})
    independent_profile = authority_profiles.get("independent_holdout_pre_registered", {})
    checks.append(
        _check(
            "factorial_run_lifecycle_reuses_canonical_harness_variants_and_metrics",
            factorial.get("baseline_authority") == "config/agent_harness_contract.json#/evaluation_baseline"
            and factorial.get("registry_path")
            == "config/external_repository_evaluation_registry.json#/factorial_evaluation_plans"
            and set(factorial.get("same_snapshot_fields", []))
            == {"repository_fingerprint", "task_fingerprint", "actor_identity", "model_identity", "model_configuration"}
            and set(factorial.get("required_target_source_evidence_fields", []))
            == {"source_location", "path", "repository_fingerprint", "content_sha256"}
            and set(factorial.get("required_cell_result_fields", []))
            == {"classification", "reported_scope", "evidence", "uncertainty", "recommendation", "mutation_performed"}
            and set(factorial.get("required_cell_result_envelope_fields", [])) == {"status"}
            and set(factorial.get("allowed_cell_result_statuses", [])) == {"measured", "not_available"}
            and {"actionable", "false_positive", "unknown"}.issubset(
                set(factorial.get("allowed_cell_classifications", []))
            )
            and set(factorial.get("required_cell_input_manifest_fields", []))
            == {"cell_id", "variant", "input_path", "content_sha256", "controller_only_truth_disclosed", "actor_input_scope"}
            and set(authority_profiles) == {"known_regression_mechanics_only", "independent_holdout_pre_registered"}
            and known_profile.get("requires_verified_unseen_history") is False
            and independent_profile.get("requires_verified_unseen_history") is True
            and independent_profile.get("required_history_prefix") == ["selected_unseen", "identity_verified"]
            and independent_profile.get("plan_must_precede_first_running_transition") is True
            and independent_profile.get("requires_hashed_cell_inputs") is True
            and factorial.get("only_planned_cell_difference") == "variant"
            and bool(harness_baseline.get("required_variants"))
            and bool(harness_baseline.get("required_metrics")),
            {
                "required_variants": harness_baseline.get("required_variants", []),
                "required_metrics": harness_baseline.get("required_metrics", []),
                "same_snapshot_fields": factorial.get("same_snapshot_fields", []),
                "corpus_authority_profiles": sorted(authority_profiles),
            },
        )
    )

    sampling = contract.get("finding_quality_sampling_contract", {})
    adjudications = sampling.get("adjudication_classes", {})
    required_adjudications = set(validation.get("required_finding_adjudications", []))
    selection_policy = sampling.get("selection_policy", {})
    required_sample_fields = set(validation.get("required_finding_sample_fields", []))
    sampling_invariants = validation.get("finding_sampling_invariants", {})
    positive_claim_classes = {
        name
        for name, payload in adjudications.items()
        if payload.get("supports_positive_quality_claim") is True
    }
    source_truth_classes = {
        name
        for name, payload in adjudications.items()
        if payload.get("requires_independent_source_truth") is True
    }
    checks.append(
        _check(
            "finding_quality_sampling_is_bounded_adaptive_and_source_grounded",
            set(adjudications) == required_adjudications
            and positive_claim_classes == set(sampling_invariants.get("positive_claim_adjudications", []))
            and source_truth_classes == set(sampling_invariants.get("independent_truth_adjudications", []))
            and sampling.get("sample_record_fields_authority")
            == "/validation_contract/required_finding_sample_fields"
            and bool(required_sample_fields)
            and selection_policy.get("strategy") in set(sampling_invariants.get("allowed_selection_strategies", []))
            and selection_policy.get("fixed_sample_count_allowed")
            is sampling_invariants.get("fixed_sample_count_allowed")
            and bool(selection_policy.get("adaptive_budget_inputs"))
            and bool(sampling.get("independence_rules"))
            and bool(sampling.get("aggregate_rules")),
            {
                "required_adjudications": sorted(required_adjudications),
                "actual_adjudications": sorted(adjudications),
                "positive_claim_classes": sorted(positive_claim_classes),
                "source_truth_classes": sorted(source_truth_classes),
                "required_sample_fields": sorted(required_sample_fields),
                "strategy": selection_policy.get("strategy"),
                "fixed_sample_count_allowed": selection_policy.get("fixed_sample_count_allowed"),
            },
        )
    )

    preflight_authority = preflight.get("analysis_authority", {})
    depth_labels = preflight_authority.get("depth_labels_by_effective_claim_level", {})
    specialist_languages = set(preflight_authority.get("deep_specialist_requires_react_signal_languages", []))
    declared_specialist_languages = {
        language
        for language, payload in polyglot.get("languages", {}).items()
        if payload.get("claim_level") == "deep_specialist"
    }
    expected_dependency_sections = {
        "runtime": "dependencies",
        "development": "devDependencies",
        "peer": "peerDependencies",
        "optional": "optionalDependencies",
    }
    expected_status_policy = {
        "invalid_target": "FAIL",
        "recognized_language_or_react_signal": "PASS",
        "recognized_with_unsupported_families": "ATTENTION",
        "readable_but_no_recognized_signal": "ATTENTION",
    }
    status_policy = preflight.get("status_policy", {})
    framework_source_policy = preflight.get("framework_source_evidence", {})
    framework_validation = preflight.get("validation_contract", {})
    required_frameworks = set(framework_source_policy) if isinstance(framework_source_policy, dict) else set()
    allowed_framework_claims = {
        str(value) for value in framework_validation.get("allowed_framework_claim_levels", [])
    }
    specialist_framework = str(framework_validation.get("specialist_framework_family") or "")
    specialist_claim = str(framework_validation.get("specialist_framework_claim_level") or "")
    unsupported_claim = str(framework_validation.get("unsupported_framework_claim_level") or "")
    react_source_policy = framework_source_policy.get(specialist_framework, {})
    framework_claims = {
        framework: payload.get("effective_claim_level")
        for framework, payload in framework_source_policy.items()
    }
    checks.append(
        _check(
            "external_preflight_preserves_language_scope",
            depth_labels.get("structural") == "structural_polyglot"
            and depth_labels.get("ast_strong") == "ast_strong_polyglot"
            and preflight_authority.get("fallback_claim_level_without_react_signal") == "ast_strong"
            and specialist_languages == declared_specialist_languages
            and preflight.get("node_dependency_sections") == expected_dependency_sections
            and status_policy == expected_status_policy
            and bool(required_frameworks)
            and specialist_framework in required_frameworks
            and specialist_claim in allowed_framework_claims
            and unsupported_claim in allowed_framework_claims
            and framework_claims.get(specialist_framework) == specialist_claim
            and all(
                framework_claims.get(framework) == unsupported_claim
                for framework in required_frameworks - {specialist_framework}
            )
            and bool(react_source_policy.get("package_names"))
            and bool(react_source_policy.get("source_extensions"))
            and all(
                str(extension).startswith(".")
                for extension in react_source_policy.get("source_extensions", [])
            )
            and preflight_authority.get("react_effective_scope_label") == "react_typescript_static_only",
            {
                "depth_labels": depth_labels,
                "fallback_claim_level_without_react_signal": preflight_authority.get("fallback_claim_level_without_react_signal"),
                "react_required_specialist_languages": sorted(specialist_languages),
                "declared_specialist_languages": sorted(declared_specialist_languages),
                "node_dependency_sections": preflight.get("node_dependency_sections"),
                "status_policy": status_policy,
                "react_source_policy": react_source_policy,
                "required_frameworks": sorted(required_frameworks),
                "framework_claims": framework_claims,
                "react_effective_scope_label": preflight_authority.get("react_effective_scope_label"),
            },
        )
    )

    projection = contract.get("human_projection", {})
    missing_phrases = [phrase for phrase in projection.get("required_phrases", []) if phrase not in human_text]
    checks.append(
        _check(
            "human_projection_preserves_evaluation_boundaries",
            not missing_phrases,
            missing_phrases or projection.get("path"),
        )
    )

    promotion = contract.get("promotion_policy", {})
    checks.append(
        _check(
            "calibration_and_single_holdout_cannot_expand_claims",
            promotion.get("calibration_to_claim") == "forbidden"
            and promotion.get("single_holdout_to_universal_claim") == "forbidden",
            {
                "calibration_to_claim": promotion.get("calibration_to_claim"),
                "single_holdout_to_universal_claim": promotion.get("single_holdout_to_universal_claim"),
            },
        )
    )
    generalization = contract.get("holdout_generalization_design", {})
    stages = generalization.get("ordered_stages", [])
    stage_orders = [stage.get("order") for stage in stages if isinstance(stage, dict)]
    stage_names = [stage.get("stage") for stage in stages if isinstance(stage, dict)]
    sequence_is_structurally_valid = (
        len(stages) >= 2
        and len(stage_orders) == len(stages)
        and stage_orders == list(range(1, len(stages) + 1))
        and len(stage_names) == len(set(stage_names))
        and stages[0].get("tests_same_failure_family") is True
        and stages[0].get("requires_material_topology_difference_from_predecessor") is False
        and stages[1].get("requires_material_topology_difference_from_predecessor") is True
        and generalization.get("single_stage_universal_claim_authorized") is False
        and generalization.get("required_sequence_completion_before_claim_review") is True
    )
    checks.append(
        _check(
            "post_regression_holdouts_progress_from_near_neighbor_to_topology_divergence",
            sequence_is_structurally_valid,
            {
                "stage_orders": stage_orders,
                "stage_names": stage_names,
                "single_stage_universal_claim_authorized": generalization.get("single_stage_universal_claim_authorized"),
                "required_sequence_completion_before_claim_review": generalization.get("required_sequence_completion_before_claim_review"),
            },
        )
    )
    return checks


def evaluate_registry(contract: dict[str, Any], registry: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    subjects = registry.get("subjects", [])
    sequences = registry.get("generalization_sequences", [])
    finding_plans = registry.get("finding_quality_evaluation_plans", [])
    state_machine = registry.get("state_machine", {})
    allowed_states = set(state_machine.get("allowed_states", []))
    holdout_states = set(state_machine.get("holdout_eligible_states", []))
    required_repository_fields = set(contract.get("identity_contract", {}).get("required_repository_fields", []))
    required_strata = set(contract.get("identity_contract", {}).get("required_selection_strata", []))
    required_task_fields = set(contract.get("task_contract", {}).get("required_fields", []))
    corpus_roles = set(contract.get("corpus_roles", {}))

    sampling_contract = contract.get("finding_quality_sampling_contract", {})
    plan_contract = sampling_contract.get("plan_preregistration_contract", {})
    required_plan_fields = set(plan_contract.get("required_plan_fields", []))
    allowed_plan_statuses = set(plan_contract.get("allowed_statuses", []))
    required_risk_tiers = set(plan_contract.get("required_risk_tiers", []))
    forbidden_run_statuses = set(plan_contract.get("run_forbidden_statuses", []))
    required_candidate_fields = set(plan_contract.get("required_candidate_fields", []))
    finding_family_authorities = sampling_contract.get("finding_family_authorities", {})
    claim_levels = set(contract.get("claim_level_evaluation", {}))
    evidence_gap_refs = {
        f"config/external_repository_evaluation_registry.json#/generalization_sequences/{sequence_index}/open_evidence_gaps/{gap_index}"
        for sequence_index, sequence in enumerate(sequences)
        for gap_index, _ in enumerate(sequence.get("open_evidence_gaps", []))
    }
    subject_by_id = {
        str(subject.get("repository_id") or ""): subject
        for subject in subjects
    }

    factorial_result = evaluate_factorial_plans(contract, registry, _load(HARNESS_PATH))
    checks.append(
        _check(
            "factorial_plans_are_paired_pre_registered_and_fail_closed",
            factorial_result["passed"],
            factorial_result["details"],
        )
    )

    plan_ids = [str(plan.get("plan_id") or "") for plan in finding_plans]
    checks.append(
        _check(
            "finding_quality_plan_identities_are_unique",
            bool(plan_ids) and len(plan_ids) == len(set(plan_ids)),
            {"plan_count": len(plan_ids), "unique_ids": len(set(plan_ids))},
        )
    )

    invalid_plans: dict[str, Any] = {}
    for plan in finding_plans:
        plan_id = str(plan.get("plan_id") or "<missing>")
        status = str(plan.get("status") or "")
        reasons: dict[str, Any] = {}
        missing_fields = sorted(required_plan_fields - set(plan))
        if missing_fields:
            reasons["missing_fields"] = missing_fields
        if status not in allowed_plan_statuses:
            reasons["invalid_status"] = status
        if plan.get("originating_evidence_gap") not in evidence_gap_refs:
            reasons["unknown_evidence_gap"] = plan.get("originating_evidence_gap")
        missing_task_fields = sorted(required_task_fields - set(plan.get("task", {})))
        if missing_task_fields:
            reasons["missing_task_fields"] = missing_task_fields

        risk_tiers = set(plan.get("risk_strata", {}))
        if risk_tiers != required_risk_tiers:
            reasons["risk_tier_mismatch"] = {
                "required": sorted(required_risk_tiers),
                "actual": sorted(risk_tiers),
            }

        families = plan.get("finding_families", [])
        actual_family_authorities = {
            str(family.get("finding_family") or ""): str(family.get("artifact_or_rule_id") or "")
            for family in families
        }
        unknown_or_rebound_families = {
            family: authority
            for family, authority in actual_family_authorities.items()
            if finding_family_authorities.get(family) != authority
        }
        if not families or unknown_or_rebound_families or len(families) != len(actual_family_authorities):
            reasons["finding_family_authority_mismatch"] = {
                "available": finding_family_authorities,
                "actual": actual_family_authorities,
                "unknown_or_rebound": unknown_or_rebound_families,
                "row_count": len(families),
            }
        invalid_family_tiers = sorted(
            {
                str(family.get("risk_tier") or "")
                for family in families
                if family.get("risk_tier") not in required_risk_tiers
            }
        )
        if invalid_family_tiers:
            reasons["invalid_family_risk_tiers"] = invalid_family_tiers

        authority = plan.get("authority_boundary", {})
        if authority.get("maximum_claim_level") not in claim_levels:
            reasons["invalid_maximum_claim_level"] = authority.get("maximum_claim_level")
        included_domains = set(authority.get("included_domains", []))
        excluded_domains = set(authority.get("excluded_domains", []))
        overlap = sorted(included_domains & excluded_domains)
        if overlap:
            reasons["authority_domain_overlap"] = overlap

        fn_protocol = plan.get("fn_truth_protocol", {})
        concrete_truths = fn_protocol.get("concrete_truths", [])
        candidate = plan.get("candidate_selection", {})
        missing_candidate_fields = sorted(required_candidate_fields - set(candidate))
        if missing_candidate_fields:
            reasons["missing_candidate_fields"] = missing_candidate_fields
        if fn_protocol.get("freeze_before") != plan_contract.get("fn_truth_freeze_boundary"):
            reasons["fn_truth_freeze_boundary_mismatch"] = fn_protocol.get("freeze_before")
        if status in forbidden_run_statuses and candidate.get("sage_execution_allowed") is not False:
            reasons["sage_execution_allowed_before_truth_freeze"] = candidate.get("sage_execution_allowed")
        if status == plan_contract.get("subject_search_status"):
            if candidate.get("status") != plan_contract.get("initial_candidate_status"):
                reasons["subject_search_already_started"] = candidate.get("status")
            if (
                candidate.get("repository_id") != plan_contract.get("unselected_repository_id")
                or candidate.get("source_url_or_local_identity") != plan_contract.get("unselected_source_identity")
                or candidate.get("commit_or_content_fingerprint") != plan_contract.get("unselected_fingerprint")
                or candidate.get("identity_evidence")
                or candidate.get("selection_trace")
            ):
                reasons["candidate_identity_visible_before_search"] = candidate
            if candidate.get("source_inspection_allowed") is not False:
                reasons["source_inspection_allowed_before_identity_freeze"] = candidate.get("source_inspection_allowed")
            if fn_protocol.get("concrete_truth_status") != plan_contract.get("pending_concrete_truth_status") or fn_protocol.get("concrete_truths"):
                reasons["concrete_truth_visible_before_source_freeze"] = fn_protocol
        if status == plan_contract.get("source_truth_pending_status"):
            candidate_id = str(candidate.get("repository_id") or "")
            candidate_subject = subject_by_id.get(candidate_id)
            fingerprint = str(candidate.get("commit_or_content_fingerprint") or "")
            if candidate.get("status") != plan_contract.get("selected_candidate_status"):
                reasons["selected_candidate_status_mismatch"] = candidate.get("status")
            if (
                candidate_id == plan_contract.get("unselected_repository_id")
                or not candidate.get("source_url_or_local_identity")
                or not candidate.get("identity_evidence")
                or not candidate.get("selection_trace")
                or candidate.get("source_inspection_allowed") is not True
                or len(fingerprint) != 40
                or any(character not in "0123456789abcdef" for character in fingerprint)
            ):
                reasons["candidate_identity_not_frozen"] = candidate
            if not candidate_subject:
                reasons["selected_candidate_subject_missing"] = candidate_id
            elif (
                candidate_subject.get("evaluation_plan_id") != plan_id
                or candidate_subject.get("source_url_or_local_identity") != candidate.get("source_url_or_local_identity")
                or candidate_subject.get("commit_or_content_fingerprint") != fingerprint
                or candidate_subject.get("state") != plan_contract.get("selected_subject_state")
                or candidate_subject.get("result", {}).get("outcome") != plan_contract.get("pending_subject_outcome")
            ):
                reasons["selected_candidate_subject_mismatch"] = {
                    "candidate": candidate,
                    "subject": candidate_subject,
                }
            if fn_protocol.get("concrete_truth_status") != plan_contract.get("pending_concrete_truth_status") or fn_protocol.get("concrete_truths"):
                reasons["concrete_truth_visible_before_source_freeze"] = fn_protocol
        if status in set(plan_contract.get("source_truth_invariant_statuses", [])):
            candidate_id = str(candidate.get("repository_id") or "")
            candidate_subject = subject_by_id.get(candidate_id)
            required_truth_fields = set(plan_contract.get("required_concrete_truth_fields", []))
            allowed_applicability = set(plan_contract.get("allowed_truth_applicability", []))
            truth_families = [str(truth.get("finding_family") or "") for truth in concrete_truths]
            malformed_truths = {
                str(truth.get("truth_id") or index): {
                    "missing_fields": sorted(required_truth_fields - set(truth)),
                    "applicability": truth.get("applicability"),
                }
                for index, truth in enumerate(concrete_truths)
                if required_truth_fields - set(truth)
                or truth.get("applicability") not in allowed_applicability
            }
            if not concrete_truths:
                reasons["source_truth_frozen_without_concrete_truths"] = True
            if malformed_truths:
                reasons["malformed_concrete_truths"] = malformed_truths
            if set(truth_families) != set(actual_family_authorities) or len(truth_families) != len(set(truth_families)):
                reasons["concrete_truth_family_coverage_mismatch"] = {
                    "required": sorted(actual_family_authorities),
                    "actual": truth_families,
                }
            if fn_protocol.get("concrete_truth_status") != plan_contract.get("source_truth_frozen_protocol_status"):
                reasons["source_truth_protocol_status_mismatch"] = fn_protocol.get("concrete_truth_status")
            if (
                candidate.get("status") != plan_contract.get("source_truth_frozen_candidate_status")
                or candidate.get("source_inspection_allowed") is not True
                or candidate.get("sage_execution_allowed") is not True
            ):
                reasons["source_truth_candidate_gate_mismatch"] = candidate
            if not candidate_subject:
                reasons["source_verified_subject_missing"] = candidate_id
            else:
                subject_mismatch = (
                    candidate_subject.get("evaluation_plan_id") != plan_id
                    or candidate_subject.get("source_url_or_local_identity") != candidate.get("source_url_or_local_identity")
                    or candidate_subject.get("commit_or_content_fingerprint") != candidate.get("commit_or_content_fingerprint")
                )
                if status == plan_contract.get("source_truth_frozen_status"):
                    subject_mismatch = subject_mismatch or (
                        candidate_subject.get("state") != plan_contract.get("source_verified_subject_state")
                        or candidate_subject.get("result", {}).get("outcome") != plan_contract.get("pending_subject_outcome")
                    )
                elif status == plan_contract.get("running_status"):
                    subject_mismatch = subject_mismatch or candidate_subject.get("state") != plan_contract.get("running_status")
                elif status == plan_contract.get("result_frozen_status"):
                    subject_mismatch = subject_mismatch or candidate_subject.get("state") not in set(
                        plan_contract.get("result_frozen_subject_states", [])
                    )
                if subject_mismatch:
                    reasons["source_verified_subject_mismatch"] = candidate_subject
        if plan.get("sampling_policy_authority") != plan_contract.get("sampling_policy_authority"):
            reasons["sampling_policy_authority_mismatch"] = plan.get("sampling_policy_authority")
        if plan.get("claim_disposition") != plan_contract.get("required_claim_disposition"):
            reasons["claim_expansion_not_allowed"] = plan.get("claim_disposition")
        if reasons:
            invalid_plans[plan_id] = reasons

    checks.append(
        _check(
            "finding_quality_plans_are_predeclared_source_independent_and_bounded",
            not invalid_plans,
            invalid_plans or {"plans": len(finding_plans), "status": "all finding-quality plans conform"},
        )
    )

    ids = [str(subject.get("repository_id") or "") for subject in subjects]
    fingerprints = [str(subject.get("commit_or_content_fingerprint") or "") for subject in subjects]
    checks.append(
        _check(
            "evaluation_subject_identities_are_unique",
            len(ids) == len(set(ids)) and len(fingerprints) == len(set(fingerprints)),
            {"subject_count": len(subjects), "unique_ids": len(set(ids)), "unique_fingerprints": len(set(fingerprints))},
        )
    )

    invalid_subjects: dict[str, Any] = {}
    for subject in subjects:
        subject_id = str(subject.get("repository_id") or "<missing>")
        state = subject.get("state")
        role = subject.get("corpus_role")
        history = subject.get("transition_history", [])
        reasons: dict[str, Any] = {}
        missing_fields = sorted(required_repository_fields - set(subject))
        missing_strata = sorted(required_strata - set(subject.get("selection_strata", {})))
        missing_task_fields = sorted(required_task_fields - set(subject.get("task", {})))
        if missing_fields:
            reasons["missing_repository_fields"] = missing_fields
        if missing_strata:
            reasons["missing_selection_strata"] = missing_strata
        if missing_task_fields:
            reasons["missing_task_fields"] = missing_task_fields
        if state not in allowed_states:
            reasons["invalid_state"] = state
        if role not in corpus_roles:
            reasons["invalid_corpus_role"] = role
        if not history or history[-1].get("state") != state:
            reasons["history_state_mismatch"] = {"state": state, "last_history_state": history[-1].get("state") if history else None}
        if role in {"holdout", "blind_holdout"} and state in holdout_states:
            if not str(subject.get("prior_sage_exposure") or "").startswith("unseen_verified"):
                reasons["unverified_holdout_exposure"] = subject.get("prior_sage_exposure")
            if subject.get("result", {}).get("outcome") != "not_available":
                reasons["result_visible_before_holdout_transition"] = subject.get("result", {}).get("outcome")
        if reasons:
            invalid_subjects[subject_id] = reasons

    checks.append(
        _check(
            "evaluation_subjects_follow_identity_task_and_leakage_contracts",
            not invalid_subjects,
            invalid_subjects or {"subjects": len(subjects), "status": "all registered subjects conform"},
        )
    )

    subject_by_id = {str(subject.get("repository_id") or ""): subject for subject in subjects}
    review_contract = contract.get("holdout_generalization_design", {}).get("sequence_review_contract", {})
    required_sequence_fields = set(review_contract.get("required_fields", []))
    allowed_review_statuses = set(review_contract.get("allowed_review_statuses", []))
    allowed_claim_dispositions = set(review_contract.get("allowed_claim_dispositions", []))
    expected_stages = [
        (stage.get("order"), stage.get("stage"))
        for stage in contract.get("holdout_generalization_design", {}).get("ordered_stages", [])
    ]
    sequence_ids = [str(sequence.get("sequence_id") or "") for sequence in sequences]
    checks.append(
        _check(
            "generalization_sequence_identities_are_unique",
            len(sequence_ids) == len(set(sequence_ids)),
            {"sequence_count": len(sequence_ids), "unique_ids": len(set(sequence_ids))},
        )
    )

    invalid_sequences: dict[str, Any] = {}
    for sequence in sequences:
        sequence_id = str(sequence.get("sequence_id") or "<missing>")
        reasons: dict[str, Any] = {}
        missing_fields = sorted(required_sequence_fields - set(sequence))
        if missing_fields:
            reasons["missing_fields"] = missing_fields
        if sequence.get("review_status") not in allowed_review_statuses:
            reasons["invalid_review_status"] = sequence.get("review_status")
        if sequence.get("claim_disposition") not in allowed_claim_dispositions:
            reasons["invalid_claim_disposition"] = sequence.get("claim_disposition")

        originating_ids = sequence.get("originating_regression_subject_ids", [])
        invalid_origins = {
            subject_id: subject_by_id.get(subject_id, {}).get("state", "missing")
            for subject_id in originating_ids
            if subject_id not in subject_by_id or subject_by_id[subject_id].get("state") != "known_regression"
        }
        if invalid_origins:
            reasons["invalid_regression_origins"] = invalid_origins

        stages = sequence.get("stages", [])
        actual_stages = [(stage.get("order"), stage.get("stage")) for stage in stages]
        if actual_stages != expected_stages:
            reasons["stage_order_mismatch"] = {"expected": expected_stages, "actual": actual_stages}
        stage_subject_ids = [str(stage.get("repository_id") or "") for stage in stages]
        if len(stage_subject_ids) != len(set(stage_subject_ids)):
            reasons["stage_subjects_not_distinct"] = stage_subject_ids
        invalid_stage_references: dict[str, Any] = {}
        for stage in stages:
            subject_id = str(stage.get("repository_id") or "")
            subject = subject_by_id.get(subject_id)
            if subject is None:
                invalid_stage_references[subject_id or "<missing>"] = "subject_missing"
                continue
            if subject.get("state") != "result_frozen":
                invalid_stage_references[subject_id] = {"state": subject.get("state")}
            if stage.get("outcome") != subject.get("result", {}).get("outcome"):
                invalid_stage_references.setdefault(subject_id, {})["outcome"] = {
                    "declared": stage.get("outcome"),
                    "subject": subject.get("result", {}).get("outcome"),
                }
        if invalid_stage_references:
            reasons["invalid_stage_references"] = invalid_stage_references

        next_decision = sequence.get("next_holdout_decision", {})
        if next_decision.get("automatic_count_extension") is not False or next_decision.get("evidence_gap_required") is not True:
            reasons["next_holdout_not_gap_driven"] = next_decision
        if not sequence.get("shared_supported_invariants") or not sequence.get("non_promoted_claims") or not sequence.get("open_evidence_gaps"):
            reasons["bounded_review_sections_incomplete"] = True
        if reasons:
            invalid_sequences[sequence_id] = reasons

    checks.append(
        _check(
            "generalization_sequences_are_bounded_source_linked_reviews",
            not invalid_sequences,
            invalid_sequences or {"sequences": len(sequences), "status": "all sequence reviews conform"},
        )
    )
    return checks


def run_validation() -> dict[str, Any]:
    contract = _load(CONTRACT_PATH)
    try:
        ensure_against_schema(SCHEMA_PATH, "external_repository_evaluation_contract", contract)
        schema_errors: list[str] = []
    except Exception as exc:
        schema_errors = [str(exc)]

    registry = _load(REGISTRY_PATH)
    try:
        ensure_against_schema(REGISTRY_SCHEMA_PATH, "external_repository_evaluation_registry", registry)
        registry_schema_errors: list[str] = []
    except Exception as exc:
        registry_schema_errors = [str(exc)]

    projection_path = CODE_MAPS_DIR / str(contract.get("human_projection", {}).get("path") or "")
    human_text = projection_path.read_text(encoding="utf-8", errors="replace") if projection_path.is_file() else ""
    checks = [_check("schema_valid", not schema_errors, schema_errors or "schema ok")]
    checks.append(_check("evaluation_registry_schema_valid", not registry_schema_errors, registry_schema_errors or "schema ok"))
    checks.extend(evaluate_contract(contract, _load(POLYGLOT_PATH), _load(HARNESS_PATH), _load(PREFLIGHT_PATH), human_text=human_text))
    checks.extend(evaluate_registry(contract, registry))

    summary = {
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check["passed"]),
        "failed_checks": sum(1 for check in checks if not check["passed"]),
        "contract_status": contract.get("meta", {}).get("status", "unknown"),
    }
    payload = {"meta": {"kind": "external_repository_evaluation_contract_validation", "version": "v1"}, "summary": summary, "checks": checks}
    save_json_atomic(RAW_DIR / "external_repository_evaluation_contract_validation.json", payload)

    lines = [
        "# External Repository Evaluation Contract Validation",
        "",
        f"- Status: `{summary['status']}`",
        f"- Contract status: `{summary['contract_status']}`",
        f"- Checks: `{summary['passed_checks']}/{summary['total_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = check["details"] if isinstance(check["details"], str) else json.dumps(check["details"], ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {escaped_details} |")
    save_text_atomic(REPORTS_DIR / "external_repository_evaluation_contract_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_validator import validate_against_schema
from tools.validate_external_repository_evaluation_contract import evaluate_contract, evaluate_registry


ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _inputs():
    contract = _load("config/external_repository_evaluation_contract.json")
    polyglot = _load("config/polyglot_capabilities.json")
    harness = _load("config/agent_harness_contract.json")
    preflight = _load("config/external_target_preflight_policy.json")
    human_text = (ROOT / contract["human_projection"]["path"]).read_text(encoding="utf-8")
    return contract, polyglot, harness, preflight, human_text


def _failed_names(checks):
    return {check["name"] for check in checks if not check["passed"]}


@pytest.mark.parametrize("autocrlf", ["true", "false"])
def test_hashed_evaluation_inputs_survive_git_checkout(tmp_path, autocrlf):
    import hashlib

    repo = tmp_path / "source"
    checkout = tmp_path / "checkout"
    repo.mkdir()
    shutil.copyfile(ROOT / ".gitattributes", repo / ".gitattributes")
    registry = _load("config/external_repository_evaluation_registry.json")
    manifests = [
        row
        for plan in registry["factorial_evaluation_plans"]
        for row in plan.get("cell_input_manifests", [])
    ]
    assert manifests
    for row in manifests:
        target = repo / row["input_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / row["input_path"], target)

    def git(*args):
        return subprocess.run(
            ["git", "-c", f"core.autocrlf={autocrlf}", "-C", str(repo), *args],
            check=True, capture_output=True, text=True,
        )

    git("init", "--quiet")
    git("add", "--", ".gitattributes", "config/evaluation_inputs")
    git("checkout-index", "--all", f"--prefix={checkout.as_posix()}/")
    for row in manifests:
        content = (checkout / row["input_path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == row["content_sha256"]
        assert hashlib.sha256(content + b"changed").hexdigest() != row["content_sha256"]


def test_real_contract_passes_cross_authority_checks():
    contract, polyglot, harness, preflight, human_text = _inputs()
    assert not _failed_names(evaluate_contract(contract, polyglot, harness, preflight, human_text=human_text))


def test_registry_subject_accepts_commit_or_sha256_content_fingerprint():
    schema_path = ROOT / "config/schemas/external_repository_evaluation_registry.schema.json"
    registry = _load("config/external_repository_evaluation_registry.json")
    assert not validate_against_schema(schema_path, "external_repository_evaluation_registry", registry)

    mutated = copy.deepcopy(registry)
    mutated["subjects"][0]["commit_or_content_fingerprint"] = "a" * 41
    errors = validate_against_schema(schema_path, "external_repository_evaluation_registry", mutated)
    assert any("commit_or_content_fingerprint" in error for error in errors)


def test_known_repository_cannot_be_relabelled_as_holdout():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["leakage_policy"]["states"]["known_to_development"]["holdout_eligible"] = True
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "only_verified_unseen_subjects_are_holdout_eligible" in _failed_names(checks)


def test_holdout_cannot_tune_rules_before_result_freeze():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["corpus_roles"]["holdout"]["may_influence_rules_or_thresholds"] = True
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "independent_roles_forbid_pre_result_tuning" in _failed_names(checks)


def test_ast_strong_cannot_claim_framework_semantic_pass_authority():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["claim_level_evaluation"]["ast_strong"]["eligible_test_domains"].append("framework_semantic")
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "claim_levels_cannot_pass_forbidden_domains" in _failed_names(checks)


def test_missing_claim_level_rule_fails_when_polyglot_authority_expands():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["claim_level_evaluation"].pop("structural")
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "claim_level_rules_follow_polyglot_authority" in _failed_names(checks)


def test_claim_level_cannot_become_framework_entitlement_without_active_evidence():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["authority_resolution"]["claim_level_is_ceiling_not_entitlement"] = False
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "claim_level_is_ceiling_and_requires_active_evidence" in _failed_names(checks)


def test_task_cannot_drop_real_failure_mode():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["task_contract"]["required_fields"].remove("real_failure_mode")
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "evaluation_tasks_bind_real_failure_mode_and_user_relevance" in _failed_names(checks)


def test_factorial_plan_cannot_drop_canonical_harness_variant():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["factorial_evaluation_plans"][0]["cells"].pop()
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_cells_cannot_drift_to_different_model_identity():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["factorial_evaluation_plans"][0]["cells"][1]["model_identity"] = "different-model"
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_metric_cannot_use_unreasoned_not_available_scalar():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["factorial_evaluation_plans"][0]["cells"][0]["metrics"]["input_tokens"] = "not_available"
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_plan_cannot_execute_before_runner_and_sage_identity_freeze():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["factorial_evaluation_plans"][0]["status"] = "ready_to_execute"
    mutated["factorial_evaluation_plans"][0]["runner_snapshot"]["actor_identity"] = "not_selected"
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_task_target_requires_content_bound_source_evidence():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["factorial_evaluation_plans"][0]["task_snapshot"]["target_source_evidence"][0]["source_location"] = "missing/target.ts#symbol"
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_independent_factorial_cell_input_must_be_hash_bound_and_answer_key_sealed():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = next(
        item
        for item in mutated["factorial_evaluation_plans"]
        if item["corpus_authority"] == "independent_holdout_pre_registered"
    )
    plan["cell_input_manifests"][0]["content_sha256"] = "0" * 64
    plan["cell_input_manifests"][1]["controller_only_truth_disclosed"] = True
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_completed_factorial_cell_requires_inspectable_result_evidence():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    cell = mutated["factorial_evaluation_plans"][0]["cells"][0]
    cell["status"] = "completed"
    cell["result"] = {"status": "measured", "classification": "false_positive", "evidence": [], "uncertainty": [], "recommendation": "No mutation.", "mutation_performed": False}
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_mechanics_subject_cannot_be_relabelled_independent():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["factorial_evaluation_plans"][0]["corpus_authority"] = "independent_holdout_pre_registered"
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_pre_registered_independent_factorial_subject_is_admitted_before_execution():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = next(
        item
        for item in mutated["factorial_evaluation_plans"]
        if item["corpus_authority"] == "independent_holdout_pre_registered"
    )
    subject = next(item for item in mutated["subjects"] if item["repository_id"] == plan["repository_subject_id"])
    plan["status"] = "ready_to_execute"
    plan["corpus_authority"] = "independent_holdout_pre_registered"
    plan["created_at"] = "2026-07-21T17:00:00+02:00"
    plan["result"] = {"outcome": "not_available", "reason": "Pre-registered independent cells have not started."}
    for cell in plan["cells"]:
        cell["status"] = "not_started"
        cell["evidence_references"] = []
        cell["result"] = {"status": "not_available", "reason": "cell_not_started"}
        cell["metrics"] = {
            name: {"status": "not_available", "value": "not_available", "reason": "cell_not_started"}
            for name in cell["metrics"]
        }
    subject["state"] = "identity_verified"
    subject["result"] = {"outcome": "not_available", "reason": "Source identity is frozen; no SAGE result exists."}
    subject["transition_history"] = subject["transition_history"][:2]
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" not in _failed_names(checks)


def test_pending_factorial_cell_cannot_fabricate_a_measured_result():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    cell = mutated["factorial_evaluation_plans"][0]["cells"][0]
    cell["status"] = "not_started"
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_field_contract_cannot_leak_into_finding_quality_plans():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    checks = evaluate_registry(contract, registry)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" not in _failed_names(checks)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" not in _failed_names(checks)


def test_factorial_decision_accuracy_must_match_controller_truth():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = next(item for item in mutated["factorial_evaluation_plans"] if item["plan_id"].startswith("reshaped_"))
    plan["cells"][0]["result"]["classification"] = "file_local_private_refactor"
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_scope_metrics_must_match_reported_and_truth_sets():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = next(item for item in mutated["factorial_evaluation_plans"] if item["plan_id"].startswith("reshaped_"))
    plan["cells"][1]["result"]["reported_scope"].append("packages/unrelated.ts#noise")
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_task_success_cannot_hide_component_metric_failure():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = next(item for item in mutated["factorial_evaluation_plans"] if item["plan_id"].startswith("reshaped_"))
    plan["cells"][2]["metrics"]["task_success"]["value"] = True
    checks = evaluate_registry(contract, mutated)
    assert "factorial_plans_are_paired_pre_registered_and_fail_closed" in _failed_names(checks)


def test_factorial_schema_and_runtime_share_classification_vocabulary():
    contract = _load("config/external_repository_evaluation_contract.json")
    schema = _load("config/schemas/external_repository_evaluation_registry.schema.json")
    measured_result = schema["$defs"]["factorialCellResult"]["oneOf"][1]
    assert set(measured_result["properties"]["classification"]["enum"]) == set(
        contract["factorial_evaluation_contract"]["allowed_cell_classifications"]
    )


def test_post_regression_holdout_sequence_cannot_skip_topology_divergence():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["holdout_generalization_design"]["ordered_stages"][1]["requires_material_topology_difference_from_predecessor"] = False
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "post_regression_holdouts_progress_from_near_neighbor_to_topology_divergence" in _failed_names(checks)


def test_single_generalization_stage_cannot_authorize_universal_claim():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["holdout_generalization_design"]["single_stage_universal_claim_authorized"] = True
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "post_regression_holdouts_progress_from_near_neighbor_to_topology_divergence" in _failed_names(checks)


def test_finding_sampling_cannot_use_fixed_repository_agnostic_count():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["finding_quality_sampling_contract"]["selection_policy"]["fixed_sample_count_allowed"] = True
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "finding_quality_sampling_is_bounded_adaptive_and_source_grounded" in _failed_names(checks)


def test_unknown_finding_evidence_cannot_support_positive_quality_claim():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(contract)
    mutated["finding_quality_sampling_contract"]["adjudication_classes"]["UNKNOWN"]["supports_positive_quality_claim"] = True
    checks = evaluate_contract(mutated, polyglot, harness, preflight, human_text=human_text)
    assert "finding_quality_sampling_is_bounded_adaptive_and_source_grounded" in _failed_names(checks)


def test_finding_quality_plan_cannot_expose_candidate_before_subject_search_transition():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = mutated["finding_quality_evaluation_plans"][0]
    plan_contract = contract["finding_quality_sampling_contract"]["plan_preregistration_contract"]
    plan["status"] = plan_contract["subject_search_status"]
    plan["candidate_selection"] = {
        "status": plan_contract["initial_candidate_status"],
        "repository_id": plan_contract["unselected_repository_id"],
        "source_url_or_local_identity": plan_contract["unselected_source_identity"],
        "commit_or_content_fingerprint": plan_contract["unselected_fingerprint"],
        "identity_evidence": [],
        "selection_trace": [],
        "source_inspection_allowed": False,
        "sage_execution_allowed": False,
    }
    plan["candidate_selection"]["repository_id"] = "premature_candidate"
    plan["candidate_selection"]["identity_evidence"] = ["source inspected too early"]
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_finding_quality_plan_cannot_rebind_canonical_finding_family_authority():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["finding_quality_evaluation_plans"][0]["finding_families"][0]["artifact_or_rule_id"] = "local_dead_code_alias"
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_finding_quality_plan_may_select_a_bounded_subset_of_canonical_families():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = mutated["finding_quality_evaluation_plans"][0]
    plan["finding_families"] = plan["finding_families"][:2]
    plan["fn_truth_protocol"]["concrete_truths"] = plan["fn_truth_protocol"]["concrete_truths"][:2]
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" not in _failed_names(checks)


def test_finding_quality_plan_cannot_drop_required_risk_stratum():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["finding_quality_evaluation_plans"][0]["risk_strata"].pop("uncertain_boundaries")
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_selected_finding_quality_candidate_must_match_registered_subject_identity():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["finding_quality_evaluation_plans"][0]["candidate_selection"]["commit_or_content_fingerprint"] = "f" * 40
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_selected_finding_quality_subject_must_link_back_to_plan():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    selected_id = mutated["finding_quality_evaluation_plans"][0]["candidate_selection"]["repository_id"]
    selected_subject = next(subject for subject in mutated["subjects"] if subject["repository_id"] == selected_id)
    selected_subject.pop("evaluation_plan_id")
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_selected_finding_quality_candidate_cannot_run_before_source_truth_freeze():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = mutated["finding_quality_evaluation_plans"][0]
    plan_contract = contract["finding_quality_sampling_contract"]["plan_preregistration_contract"]
    plan["status"] = plan_contract["source_truth_pending_status"]
    plan["candidate_selection"]["status"] = plan_contract["selected_candidate_status"]
    plan["candidate_selection"]["sage_execution_allowed"] = True
    plan["fn_truth_protocol"]["concrete_truth_status"] = plan_contract["pending_concrete_truth_status"]
    plan["fn_truth_protocol"]["concrete_truths"] = []
    subject = next(item for item in mutated["subjects"] if item.get("evaluation_plan_id") == plan["plan_id"])
    subject["state"] = plan_contract["selected_subject_state"]
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_selected_finding_quality_candidate_cannot_claim_truth_before_source_freeze():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = mutated["finding_quality_evaluation_plans"][0]
    plan_contract = contract["finding_quality_sampling_contract"]["plan_preregistration_contract"]
    plan["status"] = plan_contract["source_truth_pending_status"]
    plan["candidate_selection"]["status"] = plan_contract["selected_candidate_status"]
    plan["candidate_selection"]["sage_execution_allowed"] = False
    subject = next(item for item in mutated["subjects"] if item.get("evaluation_plan_id") == plan["plan_id"])
    subject["state"] = plan_contract["selected_subject_state"]
    protocol = plan["fn_truth_protocol"]
    protocol["concrete_truth_status"] = "frozen"
    protocol["concrete_truths"] = [{"family": "dead_code", "claim": "premature"}]
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_source_truth_freeze_requires_every_declared_finding_family():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan = mutated["finding_quality_evaluation_plans"][0]
    plan["fn_truth_protocol"]["concrete_truths"] = plan["fn_truth_protocol"]["concrete_truths"][:-1]
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_source_truth_freeze_cannot_open_sage_gate_with_unverified_subject():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    plan_id = mutated["finding_quality_evaluation_plans"][0]["plan_id"]
    subject = next(item for item in mutated["subjects"] if item.get("evaluation_plan_id") == plan_id)
    subject["state"] = "selected_unseen"
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_source_truth_freeze_rejects_unknown_applicability_state():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    truth = mutated["finding_quality_evaluation_plans"][0]["fn_truth_protocol"]["concrete_truths"][0]
    truth["applicability"] = "PASS"
    checks = evaluate_registry(contract, mutated)
    assert "finding_quality_plans_are_predeclared_source_independent_and_bounded" in _failed_names(checks)


def test_preflight_cannot_promote_non_react_specialist_without_ast_fallback():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(preflight)
    mutated["analysis_authority"]["fallback_claim_level_without_react_signal"] = "deep_specialist"
    checks = evaluate_contract(contract, polyglot, harness, mutated, human_text=human_text)
    assert "external_preflight_preserves_language_scope" in _failed_names(checks)


def test_preflight_cannot_relabel_invalid_target_as_pass():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(preflight)
    mutated["status_policy"]["invalid_target"] = "PASS"
    checks = evaluate_contract(contract, polyglot, harness, mutated, human_text=human_text)
    assert "external_preflight_preserves_language_scope" in _failed_names(checks)


def test_preflight_cannot_promote_unproven_sibling_framework():
    contract, polyglot, harness, preflight, human_text = _inputs()
    mutated = copy.deepcopy(preflight)
    mutated["framework_source_evidence"]["solid"]["effective_claim_level"] = "deep_specialist"
    checks = evaluate_contract(contract, polyglot, harness, mutated, human_text=human_text)
    assert "external_preflight_preserves_language_scope" in _failed_names(checks)


def test_pre_registered_actual_budget_subject_conforms_to_evaluation_contract():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")

    assert not _failed_names(evaluate_registry(contract, registry))


def test_holdout_with_visible_result_is_rejected_before_regression_transition():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    holdout_states = set(registry["state_machine"]["holdout_eligible_states"])
    holdout_template = next(subject for subject in registry["subjects"] if subject["corpus_role"] == "holdout")
    synthetic_subject = copy.deepcopy(holdout_template)
    synthetic_subject["repository_id"] = "synthetic_unseen_holdout_for_negative_contract_test"
    synthetic_subject["commit_or_content_fingerprint"] = "f" * 40
    synthetic_subject["state"] = sorted(holdout_states)[0]
    synthetic_subject["result"] = {"outcome": "PASS", "reason": "premature inspection"}
    synthetic_subject.pop("regression_verification", None)
    synthetic_subject["transition_history"] = [{
        "state": synthetic_subject["state"],
        "timestamp": "2026-01-01T00:00:00+00:00",
        "evidence": ["synthetic lifecycle fixture"],
    }]
    registry["subjects"].append(synthetic_subject)

    assert "evaluation_subjects_follow_identity_task_and_leakage_contracts" in _failed_names(
        evaluate_registry(contract, registry)
    )


def test_generalization_sequence_cannot_reuse_one_subject_for_both_stages():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    sequence = mutated["generalization_sequences"][0]
    sequence["stages"][1]["repository_id"] = sequence["stages"][0]["repository_id"]
    sequence["stages"][1]["outcome"] = sequence["stages"][0]["outcome"]

    assert "generalization_sequences_are_bounded_source_linked_reviews" in _failed_names(
        evaluate_registry(contract, mutated)
    )


def test_generalization_sequence_cannot_auto_extend_repository_count():
    contract = _load("config/external_repository_evaluation_contract.json")
    registry = _load("config/external_repository_evaluation_registry.json")
    mutated = copy.deepcopy(registry)
    mutated["generalization_sequences"][0]["next_holdout_decision"]["automatic_count_extension"] = True

    assert "generalization_sequences_are_bounded_source_linked_reviews" in _failed_names(
        evaluate_registry(contract, mutated)
    )

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def evaluate_factorial_plans(
    contract: dict[str, Any],
    registry: dict[str, Any],
    harness: dict[str, Any],
) -> dict[str, Any]:
    factorial_contract = contract.get("factorial_evaluation_contract", {})
    baseline = harness.get("evaluation_baseline", {})
    subjects = registry.get("subjects", [])
    plans = registry.get("factorial_evaluation_plans", [])
    subject_by_id = {str(subject.get("repository_id") or ""): subject for subject in subjects}

    required_variants = set(baseline.get("required_variants", []))
    required_metrics = set(baseline.get("required_metrics", []))
    required_plan_fields = set(factorial_contract.get("required_plan_fields", []))
    required_task_fields = set(factorial_contract.get("required_task_snapshot_fields", []))
    required_target_evidence_fields = set(factorial_contract.get("required_target_source_evidence_fields", []))
    required_input_manifest_fields = set(factorial_contract.get("required_cell_input_manifest_fields", []))
    required_runner_fields = set(factorial_contract.get("required_runner_snapshot_fields", []))
    required_sage_fields = set(factorial_contract.get("required_sage_snapshot_fields", []))
    required_cell_fields = set(factorial_contract.get("required_cell_fields", []))
    required_cell_result_envelope_fields = set(factorial_contract.get("required_cell_result_envelope_fields", []))
    allowed_cell_result_statuses = set(factorial_contract.get("allowed_cell_result_statuses", []))
    required_cell_result_fields = set(factorial_contract.get("required_cell_result_fields", []))
    allowed_cell_classifications = set(factorial_contract.get("allowed_cell_classifications", []))
    required_measurement_fields = set(factorial_contract.get("required_metric_measurement_fields", []))
    allowed_measurement_statuses = set(factorial_contract.get("allowed_metric_measurement_statuses", []))
    allowed_statuses = set(factorial_contract.get("allowed_statuses", []))
    allowed_cell_statuses = set(factorial_contract.get("allowed_cell_statuses", []))
    pending_identity = factorial_contract.get("pending_identity_value")
    pending_source_identity = factorial_contract.get("pending_source_identity_value")
    pending_result = factorial_contract.get("pending_result_value")
    same_snapshot_fields = set(factorial_contract.get("same_snapshot_fields", []))
    corpus_authority_profiles = factorial_contract.get("corpus_authority_profiles", {})

    plan_ids = [str(plan.get("plan_id") or "") for plan in plans]
    invalid_plans: dict[str, Any] = {}
    for plan in plans:
        plan_id = str(plan.get("plan_id") or "<missing>")
        reasons: dict[str, Any] = {}
        status = str(plan.get("status") or "")
        task = plan.get("task_snapshot", {})
        target_scope = set(task.get("target_scope", [])) if isinstance(task.get("target_scope"), list) else set()
        adjudication_truth = task.get("adjudication_truth", {})
        truth_scope = (
            set(adjudication_truth.get("exact_scope", []))
            if isinstance(adjudication_truth, dict) and isinstance(adjudication_truth.get("exact_scope"), list)
            else set()
        )
        target_evidence = task.get("target_source_evidence", [])
        runner = plan.get("runner_snapshot", {})
        sage = plan.get("sage_snapshot", {})
        input_manifests = plan.get("cell_input_manifests", [])
        cells = plan.get("cells", [])
        subject = subject_by_id.get(str(plan.get("repository_subject_id") or ""))

        missing_groups = {
            "missing_plan_fields": sorted(required_plan_fields - set(plan)),
            "missing_task_snapshot_fields": sorted(required_task_fields - set(task)),
            "missing_runner_snapshot_fields": sorted(required_runner_fields - set(runner)),
            "missing_sage_snapshot_fields": sorted(required_sage_fields - set(sage)),
        }
        reasons.update({name: fields for name, fields in missing_groups.items() if fields})
        if not isinstance(adjudication_truth, dict):
            reasons["malformed_adjudication_truth"] = "not_an_object"
        else:
            truth_reasons: dict[str, Any] = {}
            if adjudication_truth.get("expected_classification") not in allowed_cell_classifications:
                truth_reasons["invalid_expected_classification"] = adjudication_truth.get("expected_classification")
            exact_scope = adjudication_truth.get("exact_scope")
            if not isinstance(exact_scope, list) or not exact_scope:
                truth_reasons["exact_scope_missing"] = True
            elif len(exact_scope) != len(set(exact_scope)):
                truth_reasons["exact_scope_not_unique"] = exact_scope
            elif not truth_scope.issubset(target_scope):
                truth_reasons["exact_scope_outside_target_scope"] = sorted(truth_scope - target_scope)
            if truth_reasons:
                reasons["malformed_adjudication_truth"] = truth_reasons
        malformed_target_evidence: list[dict[str, Any]] = []
        if not isinstance(target_evidence, list) or not target_evidence:
            reasons["missing_target_source_evidence"] = True
        else:
            for index, evidence in enumerate(target_evidence):
                evidence_reasons: dict[str, Any] = {}
                if not isinstance(evidence, dict):
                    malformed_target_evidence.append({"index": index, "reason": "not_an_object"})
                    continue
                missing_evidence_fields = sorted(required_target_evidence_fields - set(evidence))
                if missing_evidence_fields:
                    evidence_reasons["missing_fields"] = missing_evidence_fields
                source_location = str(evidence.get("source_location") or "")
                path = str(evidence.get("path") or "")
                if source_location not in target_scope:
                    evidence_reasons["source_location_outside_target_scope"] = source_location
                if source_location.split("#", 1)[0] != path:
                    evidence_reasons["path_source_location_mismatch"] = {"path": path, "source_location": source_location}
                if subject and evidence.get("repository_fingerprint") != subject.get("commit_or_content_fingerprint"):
                    evidence_reasons["repository_fingerprint_mismatch"] = evidence.get("repository_fingerprint")
                if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("content_sha256") or "")):
                    evidence_reasons["invalid_content_sha256"] = evidence.get("content_sha256")
                if evidence_reasons:
                    malformed_target_evidence.append({"index": index, **evidence_reasons})
        if malformed_target_evidence:
            reasons["malformed_target_source_evidence"] = malformed_target_evidence
        if status not in allowed_statuses:
            reasons["invalid_status"] = status
        corpus_authority = str(plan.get("corpus_authority") or "")
        authority_profile = corpus_authority_profiles.get(corpus_authority)
        if not isinstance(authority_profile, dict):
            reasons["unknown_corpus_authority"] = corpus_authority
        elif not subject:
            reasons["factorial_subject_missing"] = plan.get("repository_subject_id")
        else:
            states_by_status = authority_profile.get("allowed_subject_states_by_plan_status", {})
            allowed_subject_states = set(states_by_status.get(status, []))
            if subject.get("state") not in allowed_subject_states:
                reasons["subject_state_not_admitted_by_corpus_authority"] = {
                    "corpus_authority": corpus_authority,
                    "plan_status": status,
                    "subject_state": subject.get("state"),
                    "allowed_subject_states": sorted(allowed_subject_states),
                }
            if authority_profile.get("requires_verified_unseen_history") is True:
                history = subject.get("transition_history", [])
                history_states = [str(event.get("state") or "") for event in history if isinstance(event, dict)]
                required_prefix = list(authority_profile.get("required_history_prefix", []))
                if history_states[: len(required_prefix)] != required_prefix:
                    reasons["independent_subject_history_prefix_missing"] = {
                        "required": required_prefix,
                        "actual": history_states,
                    }
                if not str(subject.get("prior_sage_exposure") or "").startswith("unseen_verified"):
                    reasons["independent_subject_exposure_not_verified"] = subject.get("prior_sage_exposure")
                if authority_profile.get("plan_must_precede_first_running_transition") is True:
                    running_timestamps = [
                        str(event.get("timestamp") or "")
                        for event in history
                        if isinstance(event, dict) and event.get("state") == "running"
                    ]
                    if running_timestamps:
                        try:
                            plan_created_at = datetime.fromisoformat(str(plan.get("created_at") or ""))
                            first_running_at = min(datetime.fromisoformat(value) for value in running_timestamps)
                            if plan_created_at >= first_running_at:
                                reasons["independent_plan_not_pre_registered_before_execution"] = {
                                    "plan_created_at": plan.get("created_at"),
                                    "first_running_at": min(running_timestamps),
                                }
                        except ValueError:
                            reasons["independent_plan_or_history_timestamp_invalid"] = {
                                "plan_created_at": plan.get("created_at"),
                                "running_timestamps": running_timestamps,
                            }

        variants = [str(cell.get("variant") or "") for cell in cells]
        cell_ids = [str(cell.get("cell_id") or "") for cell in cells]
        if set(variants) != required_variants or len(variants) != len(set(variants)):
            reasons["variant_coverage_mismatch"] = {"required": sorted(required_variants), "actual": variants}
        if len(cell_ids) != len(set(cell_ids)):
            reasons["duplicate_cell_ids"] = cell_ids

        if isinstance(authority_profile, dict) and authority_profile.get("requires_hashed_cell_inputs") is True:
            malformed_manifests: list[dict[str, Any]] = []
            manifest_cell_ids: list[str] = []
            manifest_variants: list[str] = []
            if not isinstance(input_manifests, list) or not input_manifests:
                reasons["missing_cell_input_manifests"] = True
            else:
                for index, manifest in enumerate(input_manifests):
                    manifest_reasons: dict[str, Any] = {}
                    if not isinstance(manifest, dict):
                        malformed_manifests.append({"index": index, "reason": "not_an_object"})
                        continue
                    missing_fields = sorted(required_input_manifest_fields - set(manifest))
                    if missing_fields:
                        manifest_reasons["missing_fields"] = missing_fields
                    cell_id = str(manifest.get("cell_id") or "")
                    variant = str(manifest.get("variant") or "")
                    manifest_cell_ids.append(cell_id)
                    manifest_variants.append(variant)
                    if manifest.get("controller_only_truth_disclosed") is not False:
                        manifest_reasons["controller_only_truth_disclosed"] = manifest.get("controller_only_truth_disclosed")
                    input_path = str(manifest.get("input_path") or "")
                    input_file = Path(input_path)
                    if input_file.is_absolute() or not input_path.startswith("config/evaluation_inputs/"):
                        manifest_reasons["input_path_outside_authority"] = input_path
                    else:
                        input_file = Path(__file__).resolve().parents[2] / input_file
                    if "input_path_outside_authority" not in manifest_reasons and not input_file.is_file():
                        manifest_reasons["input_file_missing"] = input_path
                    elif "input_path_outside_authority" not in manifest_reasons:
                        actual_sha256 = hashlib.sha256(input_file.read_bytes()).hexdigest()
                        if actual_sha256 != manifest.get("content_sha256"):
                            manifest_reasons["input_hash_mismatch"] = {
                                "expected": manifest.get("content_sha256"),
                                "actual": actual_sha256,
                            }
                    if not isinstance(manifest.get("actor_input_scope"), list) or not manifest.get("actor_input_scope"):
                        manifest_reasons["actor_input_scope_missing"] = True
                    if manifest_reasons:
                        malformed_manifests.append({"index": index, **manifest_reasons})
                if set(manifest_cell_ids) != set(cell_ids) or len(manifest_cell_ids) != len(set(manifest_cell_ids)):
                    reasons["input_manifest_cell_coverage_mismatch"] = {
                        "required": sorted(cell_ids),
                        "actual": manifest_cell_ids,
                    }
                if set(manifest_variants) != required_variants or len(manifest_variants) != len(set(manifest_variants)):
                    reasons["input_manifest_variant_coverage_mismatch"] = {
                        "required": sorted(required_variants),
                        "actual": manifest_variants,
                    }
            if malformed_manifests:
                reasons["malformed_cell_input_manifests"] = malformed_manifests

        malformed_cells: dict[str, Any] = {}
        for cell in cells:
            cell_id = str(cell.get("cell_id") or "<missing>")
            cell_reasons: dict[str, Any] = {}
            missing_cell_fields = sorted(required_cell_fields - set(cell))
            if missing_cell_fields:
                cell_reasons["missing_fields"] = missing_cell_fields
            if cell.get("status") not in allowed_cell_statuses:
                cell_reasons["invalid_status"] = cell.get("status")
            metrics = cell.get("metrics", {})
            cell_result = cell.get("result", {})
            if set(metrics) != required_metrics:
                cell_reasons["metric_coverage_mismatch"] = {
                    "required": sorted(required_metrics),
                    "actual": sorted(metrics),
                }
            malformed_metrics = {
                metric_name: measurement
                for metric_name, measurement in metrics.items()
                if not isinstance(measurement, dict)
                or set(measurement) != required_measurement_fields
                or measurement.get("status") not in allowed_measurement_statuses
                or (measurement.get("status") == "not_available" and measurement.get("value") != pending_result)
                or (measurement.get("status") == "measured" and measurement.get("value") == pending_result)
                or not str(measurement.get("reason") or "").strip()
            }
            if malformed_metrics:
                cell_reasons["malformed_metric_measurements"] = malformed_metrics
            if not isinstance(cell_result, dict):
                cell_reasons["malformed_cell_result"] = "not_an_object"
            else:
                missing_envelope_fields = sorted(required_cell_result_envelope_fields - set(cell_result))
                if missing_envelope_fields:
                    cell_reasons["missing_cell_result_envelope_fields"] = missing_envelope_fields
                if cell_result.get("status") not in allowed_cell_result_statuses:
                    cell_reasons["invalid_cell_result_status"] = cell_result.get("status")
            if cell.get("status") == "completed":
                if isinstance(cell_result, dict):
                    if cell_result.get("status") != "measured":
                        cell_reasons["completed_cell_result_not_measured"] = cell_result.get("status")
                    missing_result_fields = sorted(required_cell_result_fields - set(cell_result))
                    if missing_result_fields:
                        cell_reasons["missing_cell_result_fields"] = missing_result_fields
                    if cell_result.get("classification") not in allowed_cell_classifications:
                        cell_reasons["invalid_cell_classification"] = cell_result.get("classification")
                    reported_scope = cell_result.get("reported_scope")
                    if not isinstance(reported_scope, list) or not reported_scope:
                        cell_reasons["invalid_reported_scope"] = reported_scope
                    elif len(reported_scope) != len(set(reported_scope)):
                        cell_reasons["reported_scope_not_unique"] = reported_scope
                    elif truth_scope:
                        reported_scope_set = set(reported_scope)
                        overlap_count = len(reported_scope_set & truth_scope)
                        expected_metrics: dict[str, bool | float] = {
                            "decision_accuracy": cell_result.get("classification")
                            == adjudication_truth.get("expected_classification"),
                            "scope_precision": overlap_count / len(reported_scope_set),
                            "scope_recall": overlap_count / len(truth_scope),
                        }
                        expected_metrics["task_success"] = bool(
                            expected_metrics["decision_accuracy"]
                            and math.isclose(float(expected_metrics["scope_precision"]), 1.0)
                            and math.isclose(float(expected_metrics["scope_recall"]), 1.0)
                        )
                        metric_mismatches: dict[str, Any] = {}
                        for metric_name, expected_value in expected_metrics.items():
                            measurement = metrics.get(metric_name, {})
                            actual_value = measurement.get("value") if isinstance(measurement, dict) else None
                            if isinstance(expected_value, float):
                                matches = (
                                    isinstance(actual_value, (int, float))
                                    and not isinstance(actual_value, bool)
                                    and math.isclose(float(actual_value), expected_value, rel_tol=1e-9, abs_tol=1e-12)
                                )
                            else:
                                matches = type(actual_value) is type(expected_value) and actual_value == expected_value
                            if not matches or measurement.get("status") != "measured":
                                metric_mismatches[metric_name] = {
                                    "expected": expected_value,
                                    "actual": actual_value,
                                    "status": measurement.get("status") if isinstance(measurement, dict) else None,
                                }
                        if metric_mismatches:
                            cell_reasons["adjudication_metric_mismatch"] = metric_mismatches
                    if not isinstance(cell_result.get("evidence"), list) or not cell_result.get("evidence"):
                        cell_reasons["missing_cell_result_evidence"] = True
                    if not isinstance(cell_result.get("uncertainty"), list):
                        cell_reasons["invalid_cell_result_uncertainty"] = cell_result.get("uncertainty")
                    if not str(cell_result.get("recommendation") or "").strip():
                        cell_reasons["missing_cell_result_recommendation"] = True
                    if cell_result.get("mutation_performed") is not False:
                        cell_reasons["mutation_contract_violated"] = cell_result.get("mutation_performed")
                    if not cell.get("evidence_references"):
                        cell_reasons["completed_cell_missing_evidence_references"] = True
            elif isinstance(cell_result, dict):
                if cell_result.get("status") != "not_available" or not str(cell_result.get("reason") or "").strip():
                    cell_reasons["pending_cell_result_not_reasoned_unavailable"] = cell_result
            expected_cell_values = {
                "repository_fingerprint": subject.get("commit_or_content_fingerprint") if subject else None,
                "task_fingerprint": task.get("task_fingerprint"),
                "actor_identity": runner.get("actor_identity"),
                "model_identity": runner.get("model_identity"),
                "model_configuration": runner.get("model_configuration"),
            }
            mismatches = {
                field: cell.get(field)
                for field, expected in expected_cell_values.items()
                if expected is not None and cell.get(field) != expected
            }
            if mismatches:
                cell_reasons["controlled_snapshot_mismatch"] = mismatches
            if cell_reasons:
                malformed_cells[cell_id] = cell_reasons
        if malformed_cells:
            reasons["malformed_cells"] = malformed_cells

        unequal_snapshots = {
            field: sorted({str(cell.get(field)) for cell in cells})
            for field in same_snapshot_fields
            if len({str(cell.get(field)) for cell in cells}) != 1
        }
        if unequal_snapshots:
            reasons["controlled_snapshot_drift"] = unequal_snapshots
        if set(plan.get("controlled_equal_fields", [])) != same_snapshot_fields:
            reasons["controlled_equal_fields_mismatch"] = plan.get("controlled_equal_fields", [])

        if status == factorial_contract.get("pre_execution_status"):
            gate_open = (
                runner.get("actor_identity") != pending_identity
                or runner.get("model_identity") != pending_identity
                or runner.get("model_configuration") != pending_identity
                or sage.get("source_identity") != pending_source_identity
                or any(cell.get("status") != "not_started" for cell in cells)
                or plan.get("result", {}).get("outcome") != pending_result
            )
            if gate_open:
                reasons["pre_execution_gate_is_not_closed"] = True
        elif status in {
            factorial_contract.get("executable_status"),
            factorial_contract.get("running_status"),
            factorial_contract.get("result_frozen_status"),
        }:
            if (
                pending_identity in {runner.get("actor_identity"), runner.get("model_identity"), runner.get("model_configuration")}
                or sage.get("source_identity") == pending_source_identity
            ):
                reasons["execution_identity_not_frozen"] = True
        if status == factorial_contract.get("result_frozen_status"):
            open_cells = [cell.get("cell_id") for cell in cells if cell.get("status") not in {"completed", "invalid"}]
            if open_cells:
                reasons["result_frozen_with_open_cells"] = open_cells
            if plan.get("result", {}).get("outcome") == pending_result:
                reasons["result_frozen_without_outcome"] = True
        if reasons:
            invalid_plans[plan_id] = reasons

    passed = bool(plan_ids) and len(plan_ids) == len(set(plan_ids)) and not invalid_plans
    details = invalid_plans or {
        "plan_count": len(plan_ids),
        "unique_ids": len(set(plan_ids)),
        "status": "all factorial plans conform",
    }
    return {"passed": passed, "details": details}

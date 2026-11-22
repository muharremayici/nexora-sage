"""Bounded interpretation of recorded review outcomes; never execution admission."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import time

from tools.core.json_syntax import loads_json_strict


AUTHORITY = "recorded_outcome_observation_only_no_skip_execution_or_authority"
QUEUE_AUTHORITY = "diagnostic_queue_only_no_proof_reuse_seal_or_publication"
logger = logging.getLogger(__name__)


def validate_outcome_policy(policy: dict, steps: dict) -> list[str]:
    if not isinstance(policy, dict) or policy.get("schema") != "nexora_recorded_review_outcomes_v1" or policy.get("authority") != AUTHORITY:
        return ["outcome_observation_contract_invalid"]
    errors = []
    assessment = policy.get("native_continuation_assessment")
    if (not isinstance(assessment, dict)
            or assessment.get("schema") != "nexora_native_continuation_assessment_v1"
            or assessment.get("authority") != "read_only_native_interruption_assessment_no_dispatch"
            or type(assessment.get("maximum_checkpoint_bytes")) is not int
            or not 1 <= assessment["maximum_checkpoint_bytes"] <= 16777216):
        errors.append("native_continuation_assessment_contract_invalid")
    for field, maximum in (("maximum_receipts", 32), ("maximum_receipt_bytes", 16777216),
                           ("maximum_snapshot_bytes", 16777216), ("maximum_log_bytes", 16777216)):
        if type(policy.get(field)) is not int or not 1 <= policy[field] <= maximum:
            errors.append("invalid_" + field)
    owners = policy.get("owners")
    if not isinstance(owners, dict) or not owners:
        return errors + ["outcome_owners_missing"]
    for step_id, contract in owners.items():
        if step_id not in steps or not isinstance(contract, dict):
            errors.append("outcome_owner_not_registered"); continue
        if not all(isinstance(contract.get(k), str) and contract[k] for k in ("mode", "artifact_kind", "artifact_version", "ready_status")):
            errors.append("outcome_owner_shape_invalid"); continue
        if contract["mode"] == "agent_review_pack":
            fields = ("pending_status", "pending_review_status", "human_pending_review_status")
            if (not all(isinstance(contract.get(k), str) and contract[k] for k in fields)
                    or type(contract.get("pending_returncode")) is not int or type(contract.get("ready_returncode")) is not int
                    or contract.get("reviewed_status_owner") != "agent_surface_seal_contract.review_tracker_policy.reviewed_sample_status"):
                errors.append("agent_outcome_contract_invalid")
        elif contract["mode"] == "release_health":
            rules = contract.get("wait_checks")
            if (not isinstance(contract.get("blocked_status"), str) or type(contract.get("returncode")) is not int
                    or not isinstance(rules, list) or not rules):
                errors.append("health_outcome_contract_invalid"); continue
            keys = []
            for rule in rules:
                if (not isinstance(rule, dict) or not all(isinstance(rule.get(k), str) and rule[k] for k in ("category", "name", "reason"))
                        or rule.get("condition") not in {"human_seal_absent", "missing_installation_evidence"}
                        or not isinstance(rule.get("statuses"), list) or not rule["statuses"]
                        or not all(isinstance(v, str) and v for v in rule["statuses"])
                        or (rule["condition"] == "human_seal_absent" and (
                            not isinstance(rule.get("seal_impact_statuses"), list) or not rule["seal_impact_statuses"]
                            or not all(isinstance(v, str) and v for v in rule["seal_impact_statuses"])))):
                    errors.append("health_wait_rule_invalid"); continue
                keys.append((rule["category"], rule["name"]))
            if len(keys) != len(set(keys)): errors.append("health_wait_rule_ambiguous")
        else:
            errors.append("outcome_owner_mode_unsupported")
    return errors


def _agent(summary: dict, code: int, contract: dict, reviewed_status: str) -> dict:
    lists = [summary.get(k) for k in ("agent_manual_review_pending_families", "agent_manual_reviewed_families",
                                     "failed_automated_families", "missing_representative_samples")]
    if not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in lists):
        return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "agent_review_fields_missing_or_invalid"}
    pending, reviewed, failed, missing = lists
    if failed or missing:
        return {"outcome": "TECHNICAL_FAILURE_REPORTED", "reason": "automated_failure_or_missing_sample"}
    families = summary.get("families")
    if type(families) is not int or families < 1 or len(pending + reviewed) != len(set(pending + reviewed)) or len(pending + reviewed) != families:
        return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "agent_review_partition_inconsistent"}
    if summary.get("status") == contract["pending_status"]:
        if code != contract["pending_returncode"]:
            return {"outcome": "EXECUTION_ERROR", "reason": "unexpected_agent_pending_returncode"}
        if pending and summary.get("agent_manual_review_status") == contract["pending_review_status"]:
            return {"outcome": "WAITING_FOR_AGENT_REVIEW", "pending_families": len(pending)}
    elif summary.get("status") == contract["ready_status"]:
        if code != contract["ready_returncode"]:
            return {"outcome": "EXECUTION_ERROR", "reason": "unexpected_agent_ready_returncode"}
        if (not pending and summary.get("agent_manual_review_status") == reviewed_status
                and summary.get("human_review_status") == contract["human_pending_review_status"]):
            return {"outcome": "WAITING_FOR_HUMAN_REVIEW", "reason": "producer_reports_agent_review_complete"}
    return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "agent_status_or_review_fields_inconsistent"}


def _health(payload: dict, summary: dict, code: int, contract: dict) -> dict:
    if code != contract["returncode"]:
        return {"outcome": "EXECUTION_ERROR", "reason": "unexpected_health_returncode"}
    categories, blocking = payload.get("categories"), summary.get("blocking_categories")
    if not isinstance(categories, list) or not categories or not isinstance(blocking, list) or not all(isinstance(x, str) for x in blocking):
        return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "health_category_envelope_missing"}
    bad, ids, required_count = [], [], 0
    for category in categories:
        if (not isinstance(category, dict) or not isinstance(category.get("id"), str) or not category["id"]
                or not isinstance(category.get("checks"), list) or not category["checks"]):
            return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "health_category_shape_invalid"}
        ids.append(category["id"])
        names = []
        for check in category["checks"]:
            if (not isinstance(check, dict) or type(check.get("required")) is not bool
                    or not isinstance(check.get("name"), str) or not check["name"]
                    or not isinstance(check.get("status"), str) or not check["status"]):
                return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "health_check_shape_invalid"}
            names.append(check["name"])
            required_count += int(check["required"])
            if check["required"] and check["status"] != "PASS": bad.append((category["id"], check))
        if len(names) != len(set(names)):
            return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "health_check_names_ambiguous"}
    if not required_count or len(ids) != len(set(ids)) or len(blocking) != len(set(blocking)) or set(blocking) != {c for c, _ in bad}:
        return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "health_blocking_categories_inconsistent"}
    if (summary.get("status") == contract["ready_status"] and not bad
            and summary.get("machine_verdict") == "READY_FOR_HUMAN_SEAL"):
        return {"outcome": "REPORTED_MACHINE_READY", "reason": "recorded_report_only_not_current_acceptance"}
    if (summary.get("status") != contract["blocked_status"] or not bad
            or summary.get("machine_verdict") != "BLOCKED" or summary.get("release_ready") is not False):
        return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "health_status_inconsistent"}
    waits, technical, unknown = [], [], []
    for category, check in bad:
        rule = next((r for r in contract["wait_checks"] if (r["category"], r["name"]) == (category, check["name"])), None)
        matches = rule is not None and check["status"] in rule["statuses"]
        if matches and rule["condition"] == "human_seal_absent":
            detail = check.get("summary")
            matches = (summary.get("human_seal_required") is True and summary.get("human_seal_satisfied") is False
                       and isinstance(detail, dict) and detail.get("status") == "PASS"
                       and detail.get("seal_impact_status") in rule["seal_impact_statuses"])
        elif matches and rule["condition"] == "missing_installation_evidence":
            matches = check.get("summary") == {}
        if matches: waits.append(rule["reason"])
        elif check["status"] in {"FAIL", "ERROR"}: technical.append(check["name"])
        else: unknown.append(check["name"])
    if technical: return {"outcome": "TECHNICAL_FAILURE_REPORTED", "checks": technical, "reported_waits": waits}
    if unknown: return {"outcome": "INCOMPLETE_EVIDENCE", "checks": unknown, "reported_waits": waits}
    return {"outcome": "WAITING_FOR_PREREQUISITES", "reasons": list(dict.fromkeys(waits))}


def classify_recorded_outcome(step_id: str, payload: dict, returncode: int, policy: dict, *, reviewed_status: str | None = None) -> dict:
    contract = policy["owners"].get(step_id)
    if contract is None: return {"outcome": "UNMAPPED_OWNER_OUTCOME", "reason": "no_reviewed_owner_contract"}
    meta, summary = payload.get("meta"), payload.get("summary")
    if (not isinstance(meta, dict) or meta.get("kind") != contract["artifact_kind"]
            or meta.get("version") != contract["artifact_version"] or not isinstance(summary, dict)
            or (payload.get("status") is not None and payload["status"] != summary.get("status"))):
        return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "native_artifact_contract_mismatch"}
    if contract["mode"] == "agent_review_pack":
        if not isinstance(reviewed_status, str) or not reviewed_status:
            return {"outcome": "INCOMPLETE_EVIDENCE", "reason": "review_status_owner_unavailable"}
        return _agent(summary, returncode, contract, reviewed_status)
    return _health(payload, summary, returncode, contract)


def _bounded_file(root: Path, value: str, limit: int, expected_sha: str | None = None) -> tuple[bytes, str]:
    path = Path(value)
    if not path.is_absolute(): path = root / path
    if path.resolve() != path or not path.is_relative_to(root / "output/reports") or not path.is_file():
        raise ValueError("unsafe_or_missing_observation_path")
    if expected_sha is not None and not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
        raise ValueError("invalid_observation_hash")
    with path.open("rb") as stream: content = stream.read(limit + 1)
    if len(content) > limit: raise ValueError("observation_size_budget")
    actual = hashlib.sha256(content).hexdigest()
    if expected_sha is not None and actual != expected_sha: raise ValueError("observation_hash_mismatch")
    return content, actual


def observe_recorded_receipt(root: Path, receipt_path: str, steps: dict, selected: list[str], policy: dict) -> dict:
    """Inspect explicit per-step report files; never read current large primaries."""
    result = {"receipt": str(receipt_path), "observation_integrity": "INVALID", "outcome": "INCOMPLETE_EVIDENCE",
              "authority": AUTHORITY, "freshness": "NOT_ESTABLISHED", "execution_skipping_allowed": False,
              "execution_authorized": False, "current_release_acceptance": False, "human_authority_granted": False}
    try:
        content, receipt_sha = _bounded_file(root.resolve(), receipt_path, policy["maximum_receipt_bytes"])
        record = loads_json_strict(content.decode("utf-8-sig"))
        if not isinstance(record, dict): raise ValueError("per_step_execution_record_required")
        step_id = record.get("id")
        if not isinstance(step_id, str) or step_id not in selected or step_id not in steps:
            raise ValueError("receipt_step_not_selected_or_registered")
        if record.get("command") != steps[step_id]["command"]: raise ValueError("recorded_command_mismatch")
        if type(record.get("returncode")) is not int: raise ValueError("execution_returncode_missing")
        if "timed_out" in record and type(record["timed_out"]) is not bool: raise ValueError("timeout_shape_invalid")
        if not isinstance(record.get("native_primary_identity"), str) or not re.fullmatch(r"sqlite:[a-f0-9]{64}", record["native_primary_identity"]):
            raise ValueError("recorded_native_primary_identity_missing")
        for name in ("stdout", "stderr"):
            reference = record[name]
            _bounded_file(root.resolve(), reference["path"], policy["maximum_log_bytes"], reference["sha256"])
        reference = record["artifact_snapshot"]
        snapshot, snapshot_sha = _bounded_file(root.resolve(), reference["path"], policy["maximum_snapshot_bytes"], reference["sha256"])
        payload = loads_json_strict(snapshot.decode("utf-8-sig"))
        if not isinstance(payload, dict): raise ValueError("snapshot_object_required")
        if "summary" in record and record["summary"] != payload.get("summary", {}): raise ValueError("contradictory_receipt_summary")
        if "top_level_status" in record and record["top_level_status"] != payload.get("status"): raise ValueError("contradictory_receipt_status")
        reviewed_status = None
        if policy["owners"].get(step_id, {}).get("mode") == "agent_review_pack":
            # The existing owner supplies the value; this consumer does not relist it.
            owner_path = root.resolve() / "config/agent_surface_seal_contract.json"
            if owner_path.resolve() != owner_path: raise ValueError("unsafe_review_status_owner")
            with owner_path.open("rb") as stream: owner_bytes = stream.read(policy["maximum_receipt_bytes"] + 1)
            if len(owner_bytes) > policy["maximum_receipt_bytes"]: raise ValueError("review_status_owner_size_budget")
            owner = loads_json_strict(owner_bytes.decode("utf-8-sig"))
            reviewed_status = owner["review_tracker_policy"]["reviewed_sample_status"]
        verdict = ({"outcome": "EXECUTION_ERROR", "reason": "recorded_timeout"} if record.get("timed_out") is True else
                   classify_recorded_outcome(step_id, payload, record["returncode"], policy, reviewed_status=reviewed_status))
        return {**result, **verdict, "id": step_id, "observation_integrity": "VERIFIED_RECORDED_BYTES",
                "receipt_sha256": receipt_sha, "snapshot_sha256": snapshot_sha,
                "recorded_returncode": record["returncode"], "recorded_native_status": payload["summary"].get("status") if isinstance(payload.get("summary"), dict) else None,
                "recorded_primary_identity": record["native_primary_identity"], "policy_sha256": hashlib.sha256(
                    json.dumps(policy, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
                "native_primary_binding": "RECORDED_NOT_REVALIDATED",
                "command_binding": "REGISTERED_COMMAND_MATCH_NOT_SOURCE_FRESHNESS"}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {**result, "reason": "receipt_observation_unavailable_or_contradictory", "error_type": type(exc).__name__}


def _checkpoint_continuation(payload: dict, *, ordered: list[dict], selected: list[str], source: dict,
                             context: dict, scopes: dict, observations: list[dict]) -> dict:
    """Delegate eligibility to the actual native owner; never project execution."""
    from tools.core import release_proof_resume as native

    proof_scope = "selected_step_closure" if selected else "full_release_proof"
    identity = native.run_identity(source_fingerprint=source["fingerprint"], proof_scope=proof_scope,
        selected_step_ids=selected, ordered_steps=ordered, reuse_context_value=context)
    results, reasons = native.checkpoint_resume_results(payload, expected_run_identity=identity, restart=False)
    base = {"status": "ASSESSED", "continuation": "STOP_CHECKPOINT_CONTEXT",
            "native_checkpoint_reasons": reasons, "current_run_identity": identity,
            "proof_scope": proof_scope, "planned_steps": len(ordered), "steps": [],
            "native_eligible_at_assessment": 0, "actual_resumed_steps": 0,
            "execution_authorized": False, "execution_skipping_allowed": False,
            "requires_execution_time_revalidation": True, "checkpoint_mutated": False,
            "completed_proof_reuse_assessed": False, "human_authority_granted": False}
    # The owner returns this reason even for a valid empty interrupted checkpoint.
    if reasons != ["resumable_interrupted_checkpoint_loaded"]:
        return base
    retained, rows = {}, []
    for step in ordered:
        sid = step["id"]
        prior = results.get(sid)
        historical = [r["outcome"] for r in observations if r.get("id") == sid
                      and r.get("observation_integrity") == "VERIFIED_RECORDED_BYTES"]
        row = {"id": sid, "native_eligible_at_assessment": False,
               "recorded_outcomes_not_current": historical, "execution_skipping_allowed": False}
        if prior is None:
            row.update(disposition="UNSTARTED", reasons=["no_prior_interrupted_step_receipt"])
        elif type(prior.get("returncode")) is not int:
            row.update(disposition="STOP_RETAINED_NONPASSING_RESULT", reasons=["prior_returncode_not_integer"])
        else:
            unretained = [dep for dep in step.get("depends_on", []) if dep not in retained]
            if unretained:
                row.update(disposition="DEPENDENCY_NOT_YET_ELIGIBLE", reasons=["dependency_not_retained_by_assessment"], dependencies=unretained)
            else:
                scope = scopes[sid]
                current = native.step_reuse_identity(step, scope_contract=scope, proof_scope=proof_scope,
                    selected_step_ids=selected, source_fingerprint=source["fingerprint"],
                    dependency_results=retained, reuse_context_value=context)
                verdict = native.resume_decision(step_id=sid, proof_domain=scope["proof_domain"],
                    evidence_from_dependency=isinstance(step.get("evidence_from_dependency"), dict),
                    current_identity=current, previous_result=prior, reuse_context_value=context)
                if verdict["reuse"]:
                    retained[sid] = prior
                    row.update(disposition="NATIVE_ELIGIBLE_AT_ASSESSMENT", native_eligible_at_assessment=True, reasons=verdict["reasons"])
                else:
                    row.update(disposition="STOP_RETAINED_NONPASSING_RESULT" if prior.get("passed") is not True
                               else "STOP_NATIVE_REVALIDATION_REQUIRED", reasons=verdict["reasons"])
        rows.append(row)
    stopped = any(r["disposition"].startswith("STOP_") or r["disposition"] == "DEPENDENCY_NOT_YET_ELIGIBLE" for r in rows)
    return {**base, "continuation": "STOP_RETAINED_EVIDENCE_OR_REVALIDATION" if stopped
            else "REQUIRES_AUTHORIZED_NATIVE_EXECUTION", "steps": rows,
            "native_eligible_at_assessment": len(retained)}


def assess_native_continuation(root: Path, steps: dict, selected: list[str], observations: list[dict],
                               policy: dict, *, log=None) -> dict:
    """Fresh, explicit read-only assessment; this is not a new queue/executor."""
    from tools.core.config import CODE_MAPS_DIR
    from tools.core import release_proof_resume as native
    from tools.core.release_proof_steps import release_proof_step_scope_map
    from tools.core.source_state_identity import source_state_identity
    from tools import run_release_proof_bundle as owner

    base = {"status": "UNAVAILABLE", "continuation": "STOP_UNAVAILABLE_CONTEXT",
            "authority": "read_only_native_interruption_assessment_no_dispatch",
            "execution_authorized": False, "execution_skipping_allowed": False,
            "checkpoint_mutated": False, "actual_resumed_steps": 0,
            "requires_execution_time_revalidation": True, "completed_proof_reuse_assessed": False,
            "human_authority_granted": False}
    emit = log if log is not None else lambda value: None
    try:
        if root.resolve() != CODE_MAPS_DIR.resolve():
            raise ValueError("native_checkpoint_installation_owner_mismatch")
        if validate_outcome_policy(policy, steps):
            raise ValueError("invalid_native_assessment_policy")
        ordered = (owner._dependency_closure_for_steps(list(steps.values()), selected) if selected
                   else owner._ordered_proof_steps(list(steps.values())))
        scopes = release_proof_step_scope_map()
        if any(s["id"] not in scopes for s in ordered):
            raise ValueError("native_step_scope_missing")
        path = native.checkpoint_path()
        if (path.resolve() != path or not path.is_relative_to(root.resolve() / "output/.operational/release_proof")):
            raise ValueError("unsafe_native_checkpoint_path")
        with path.open("rb") as stream:
            content = stream.read(policy["native_continuation_assessment"]["maximum_checkpoint_bytes"] + 1)
        if len(content) > policy["native_continuation_assessment"]["maximum_checkpoint_bytes"]:
            raise ValueError("native_checkpoint_size_budget")
        payload = loads_json_strict(content.decode("utf-8-sig"))
        if not isinstance(payload, dict) or (isinstance(payload.get("steps"), dict)
                and any(not isinstance(v, dict) for v in payload["steps"].values())):
            raise ValueError("invalid_checkpoint_payload")
        # A negative owner decision needs no expensive current-source/environment
        # capture. An empty expected identity can never admit a checkpoint.
        _, structural_reasons = native.checkpoint_resume_results(payload, expected_run_identity={}, restart=False)
        if structural_reasons != ["current_run_identity_incomplete"]:
            return {**base, "status": "ASSESSED", "continuation": "STOP_CHECKPOINT_CONTEXT",
                    "native_checkpoint_reasons": structural_reasons,
                    "proof_scope": "selected_step_closure" if selected else "full_release_proof",
                    "planned_steps": len(ordered), "steps": [], "native_eligible_at_assessment": 0,
                    "checkpoint_sha256": hashlib.sha256(content).hexdigest(),
                    "checkpoint_invocation_id": payload.get("invocation_id"),
                    "current_context_capture": "NOT_REQUIRED_AFTER_NATIVE_STRUCTURAL_REFUSAL"}
        emit("[manual-planner] phase=capturing_current_native_context no_step_execution=true")
        source = source_state_identity(root)
        context = native.build_reuse_context()
        assessed = _checkpoint_continuation(payload, ordered=ordered, selected=selected, source=source,
            context=context, scopes=scopes, observations=observations)
        # Recheck the held checkpoint and governed source after reading native outputs.
        with path.open("rb") as stream:
            after = stream.read(len(content) + 1)
        if after != content or source_state_identity(root) != source:
            raise ValueError("context_changed_during_native_assessment")
        return {**base, **assessed, "checkpoint_sha256": hashlib.sha256(content).hexdigest(),
                "checkpoint_invocation_id": payload.get("invocation_id"),
                "observed_source": source, "context_stability": "OBSERVED_ENDPOINTS_ONLY_NOT_ATOMIC_OR_RESERVED"}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return {**base, "reason": "native_continuation_assessment_unavailable", "error_type": type(exc).__name__}


def validate_review_queue_policy(policy: dict, steps: dict) -> list[str]:
    if (not isinstance(policy, dict) or policy.get("schema") != "nexora_review_readiness_queue_v1"
            or policy.get("authority") != QUEUE_AUTHORITY
            or policy.get("source_steps_owner") != "distribution_test_profiles.test_execution.candidate_preflight.source_validator_steps"
            or policy.get("dependency_rule") != "complete_registered_closure_no_external_receipt_admission"
            or policy.get("wait_rule") != "preserve_and_block_no_automatic_reexecution"
            or type(policy.get("timeout_seconds")) is not int or not 1 <= policy["timeout_seconds"] <= 600
            or type(policy.get("output_limit_bytes")) is not int or not 1024 <= policy["output_limit_bytes"] <= 16777216
            or not isinstance(policy.get("additional_step_ids"), list)
            or not all(isinstance(s, str) and s in steps for s in policy["additional_step_ids"])
            or len(policy["additional_step_ids"]) != len(set(policy["additional_step_ids"]))):
        return ["invalid_review_queue_execution_policy"]
    return []


def review_queue_plan(root: Path, steps: dict, selected: list[str], manual_policy: dict) -> dict:
    from tools.core.distribution_test_execution import digest
    from tools.core.release_candidate_preflight import candidate_preflight_plan
    from tools import run_release_proof_bundle as owner

    policy = manual_policy.get("review_queue_execution")
    if validate_review_queue_policy(policy, steps) or not selected or len(selected) != len(set(selected)):
        raise ValueError("invalid_review_queue_policy_or_explicit_selection")
    outcomes = manual_policy.get("outcome_observation")
    if validate_outcome_policy(outcomes, steps):
        raise ValueError("invalid_review_outcome_policy")
    profile = loads_json_strict((root / "config/distribution_test_profiles.json").read_text(encoding="utf-8"))
    source_plan = candidate_preflight_plan(profile, root=root)
    allowed = {s["id"] for s in source_plan["source_validators"]} | set(policy["additional_step_ids"])
    ordered = owner._dependency_closure_for_steps(list(steps.values()), selected)
    unsupported = [s["id"] for s in ordered if s["id"] not in allowed]
    if unsupported:
        return {"status": "REVIEW_QUEUE_BLOCKED", "reason": "UNREVIEWED_DEPENDENCY_SCOPE",
                "unsupported_steps": unsupported, "actual_executed_steps": 0, "authority": QUEUE_AUTHORITY}
    planned = []
    for step in ordered:
        command = step.get("command")
        if (not isinstance(command, list) or len(command) != 2 or command[0] != sys.executable
                or not isinstance(command[1], str)):
            raise ValueError("review_queue_requires_registered_owned_python_command")
        script = Path(command[1])
        expected = root / "tools" / script.name
        if (script.resolve() != expected or not expected.is_file()
                or not re.fullmatch(r"(?:validate|generate)_[a-z0-9_]+\.py", script.name)
                or (not script.name.startswith("validate_") and step["id"] not in outcomes["owners"])):
            raise ValueError("review_queue_command_outside_reviewed_owner")
        raw = Path(step["raw_artifact"])
        if raw.resolve() != raw or not raw.is_relative_to(root / "output/.raw"):
            raise ValueError("review_queue_artifact_outside_owned_raw_directory")
        planned.append({"id": step["id"], "command": command, "raw_artifact": str(raw),
                        "step_contract_sha256": digest(json.loads(json.dumps(step, default=str)))})
    # This is the same durable queue's private plan shape, not another journal.
    return {"status": "REVIEW_QUEUE_PLAN", "mode": "review_readiness", "schema": policy["schema"],
            "authority": QUEUE_AUTHORITY, "selected_ids": selected, "source_validators": planned,
            "policy": {**policy, "source_queue": {"schema": policy["schema"]}},
            "outcome_policy": outcomes, "source_plan_sha256": digest(source_plan)}


def review_step_observation(step: dict, *, root: Path, limits: dict, policy: dict) -> dict:
    from tools.core.release_candidate_preflight import supervise_preflight
    from tools.core.json_io import load_raw_artifact_primary_with_identity

    process = supervise_preflight(step["command"], cwd=root, env=os.environ.copy(), limits=limits)
    primary = load_raw_artifact_primary_with_identity(Path(step["raw_artifact"]))
    payload, identity = primary if primary is not None else ({}, None)
    summary = payload.get("summary", {}) if isinstance(payload, dict) else None
    status = payload.get("status", summary.get("status") if isinstance(summary, dict) else None) if isinstance(payload, dict) else None
    verdict = {"outcome": "TECHNICAL_FAILURE_REPORTED"}
    if (isinstance(payload, dict) and isinstance(summary, dict) and isinstance(identity, str)
            and re.fullmatch(r"sqlite:[a-f0-9]{64}", identity)
            and not (payload.get("status") is not None and summary.get("status") is not None and payload["status"] != summary["status"])):
        if step["id"] in policy["owners"]:
            marker = None
            if policy["owners"][step["id"]]["mode"] == "agent_review_pack":
                owner = loads_json_strict((root / "config/agent_surface_seal_contract.json").read_text(encoding="utf-8"))
                marker = owner["review_tracker_policy"]["reviewed_sample_status"]
            verdict = classify_recorded_outcome(step["id"], payload, process["returncode"], policy, reviewed_status=marker)
        elif process["returncode"] == 0 and status == "PASS":
            verdict = {"outcome": "VALIDATOR_PASS"}
    clean_process = (process.get("timed_out") is False and process.get("output_limit_exceeded") is False
                     and process.get("process_capture_complete") is True and type(process.get("returncode")) is int)
    outcome = verdict["outcome"] if clean_process else "EXECUTION_ERROR"
    waiting = outcome.startswith("WAITING_FOR_") or outcome == "REPORTED_MACHINE_READY"
    return {"id": step["id"], "status": "REVIEW_STEP_PASS" if outcome == "VALIDATOR_PASS" else
            "REVIEW_STEP_WAITING" if waiting else "REVIEW_STEP_FAILURE", "outcome": outcome,
            "outcome_details": verdict, "artifact_status": status, "artifact_primary_identity": identity,
            "process": process}


def run_review_queue(root: Path, steps: dict, selected: list[str], manual_policy: dict, *,
                     queue_id: str | None = None, max_steps: int | None = None) -> dict:
    from tools.core.config import CODE_MAPS_DIR
    from tools.core.distribution_test_execution import build_execution_identity
    from tools.core.release_candidate_preflight import SourceReadinessQueue, SourceQueueRefusal
    from tools.core.source_state_identity import source_state_identity

    if root.resolve() != CODE_MAPS_DIR.resolve() or (max_steps is not None and (type(max_steps) is not int or max_steps < 1)):
        raise ValueError("review_queue_invalid_installation_or_step_limit")
    plan = review_queue_plan(root, steps, selected, manual_policy)
    if plan["status"] != "REVIEW_QUEUE_PLAN":
        return plan
    queue = None
    try:
        queue = SourceReadinessQueue(root, queue_id, review=True)
        identity = build_execution_identity(root)
        if identity.get("native_environment_complete") is not True:
            return {"status": "REVIEW_QUEUE_BLOCKED", "reason": "INCOMPLETE_ENVIRONMENT", "actual_executed_steps": 0}
        with queue:
            queue.bind(plan, identity)
            previous = len(queue.results)
            observations = list(queue.results)
            started = time.monotonic()
            deadline = started + plan["policy"]["timeout_seconds"] - queue.spent
            error, interrupted = None, None
            try:
                for step in plan["source_validators"][previous:]:
                    if max_steps is not None and len(observations) - previous >= max_steps:
                        break
                    remaining = deadline - time.monotonic()
                    output = plan["policy"]["output_limit_bytes"] - sum(r["process"]["output_bytes"] for r in observations)
                    if remaining <= 0 or output <= 0:
                        raise ValueError("review_queue_shared_execution_budget")
                    print(f"[review-readiness] step={step['id']} previously_completed={previous}", flush=True)
                    queue.start_step(step)
                    row = review_step_observation(step, root=root, policy=plan["outcome_policy"],
                        limits={**plan["policy"], "timeout_seconds": remaining, "output_limit_bytes": output})
                    queue.finish_step(step, row)
                    observations.append(row)
                    if row["status"] != "REVIEW_STEP_PASS":
                        break
            except BaseException as exc:
                error = type(exc).__name__
                logger.error("Review diagnostic stopped: error_type=%s step=%s", error, queue.pending_step)
                if not isinstance(exc, Exception): interrupted = exc
            source_end = None
            try:
                source_end = source_state_identity(root)
            except (OSError, ValueError, RuntimeError) as exc:
                error = error or type(exc).__name__
            valid = (error is None and source_end == identity["source"] and time.monotonic() < deadline
                     and sum(r["process"]["output_bytes"] for r in observations) <= plan["policy"]["output_limit_bytes"])
            passing = all(r["status"] == "REVIEW_STEP_PASS" for r in observations)
            waiting = valid and bool(observations) and observations[-1]["status"] == "REVIEW_STEP_WAITING"
            complete = valid and passing and len(observations) == len(plan["source_validators"])
            paused = valid and passing and not complete
            state = "COMPLETE" if complete else "WAITING" if waiting else "PAUSED" if paused else "UNRESOLVED_EXECUTION" if queue.pending_step is not None else "VALIDATION_FAILED"
            status = "REVIEW_QUEUE_COMPLETE" if complete else "REVIEW_QUEUE_WAITING" if waiting else "REVIEW_QUEUE_PENDING" if paused else "REVIEW_QUEUE_FAILED"
            receipt = {"status": status, "schema": plan["schema"], "authority": QUEUE_AUTHORITY,
                       "id": queue.id, "attempt": queue.attempt, "plan": plan, "identity": identity,
                       "source_end": source_end, "observations": observations, "error_type": error,
                       "previously_completed": previous, "executed_this_attempt": len(observations) - previous,
                       "actual_test_cases": 0, "canonical_test_step_reuse": False, "publication_authority": False}
            with queue.receipt_path.open("x", encoding="utf-8") as out:
                json.dump(receipt, out, ensure_ascii=False, indent=2);out.write("\n");out.flush();os.fsync(out.fileno())
            queue.finish(state, time.monotonic() - started)
            result = {"status": status, "queue_id": queue.id, "queue_state": state,
                      "receipt": str(queue.receipt_path), "receipt_sha256": hashlib.sha256(queue.receipt_path.read_bytes()).hexdigest(),
                      "previously_completed": previous, "executed_this_attempt": len(observations) - previous,
                      "actual_test_cases": 0, "authority": QUEUE_AUTHORITY,
                      "canonical_test_step_reuse": False, "publication_authority": False}
            if interrupted is not None: raise interrupted
            return result
    except SourceQueueRefusal as exc:
        state = str(exc)
        return {"status": "REVIEW_QUEUE_RECORDED" if state == "ALREADY_RECORDED" else
                "REVIEW_QUEUE_WAITING_RECORDED" if state == "WAITING_NO_AUTOMATIC_REEXECUTION" else "REVIEW_QUEUE_BLOCKED",
                "queue_state": state, "queue_id": queue.id if queue is not None else None,
                "actual_executed_steps": 0, "authority": QUEUE_AUTHORITY,
                "canonical_test_step_reuse": False, "publication_authority": False}

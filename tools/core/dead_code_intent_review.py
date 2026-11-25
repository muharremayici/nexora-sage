"""Bounded intent-review requests, not observed wiring or deletion authority."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached
from tools.core.wiring_source_observations import BOUNDARIES, ROLES


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason, "correlation_status": "not_evaluated",
            "integration_status": "unknown", "mutation_proposed": False, "questions": []}


def intent_review_request() -> dict:
    """Load current guidance through the shared content-aware policy reader."""
    contract = load_json_object_strict_cached(
        CONFIG_DIR / "dead_code_language_agnostic_core_contract.json",
        label="Dead-code core contract")
    policy = contract.get("intent_review_request")
    if not isinstance(policy, dict):
        return _unavailable("review_policy_missing")
    limits, questions = policy.get("limits"), policy.get("questions")
    if not isinstance(limits, dict):
        return _unavailable("review_policy_invalid")
    count, width = limits.get("maximum_questions"), limits.get("maximum_question_characters")
    if (not isinstance(policy.get("id"), str) or not policy["id"].strip()
            or type(count) is not int or type(width) is not int or count < 1 or width < 1
            or not isinstance(questions, list) or not 0 < len(questions) <= count
            or any(not isinstance(question, str) or not question.strip() or len(question) > width
                   for question in questions)):
        return _unavailable("review_policy_invalid")
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"status": "evidence_required", "policy": policy["id"], "policy_sha256": digest,
            "correlation_status": "not_evaluated", "integration_status": "unknown",
            "mutation_proposed": False, "questions": deepcopy(questions)}


def project_intent_review_request(value: object) -> dict:
    """Do not fabricate current guidance from legacy, stale or promoted metadata."""
    if not isinstance(value, dict):
        return _unavailable("stored_review_request_missing")
    current = intent_review_request()
    if current["status"] != "evidence_required":
        return current
    if value.get("mutation_proposed") is not False or value != current:
        return _unavailable("stored_review_request_not_current")
    return deepcopy(current)


def project_wiring_review_candidate(view: object) -> dict | None:
    """Label an already admitted view, not a source verifier or an intent solver.

    The sole caller owns primary/snapshot/live checks and record validation.
    Only actually shown facts can support this non-blocking manual-review label.
    Withholding it is not evidence of clean code or completed integration.
    """
    if (not isinstance(view, dict) or view.get("status") != "observed"
            or view.get("source_binding") != "snapshot_and_live_match"
            or view.get("edit_authority") != "not_granted" or view.get("mutation_proposed") is not False
            or any(view.get(key) != value for key,value in BOUNDARIES.items())):
        return None
    records = view.get("records")
    if (not isinstance(records, list) or len(records) < len(ROLES)
            or any(type(view.get(key)) is not int for key in ("record_count", "shown", "omitted", "producer_omitted"))
            or view["shown"] != len(records) or view["shown"] > view["record_count"]
            or view["omitted"] != view["record_count"] - view["shown"] or view["producer_omitted"] != 0):
        return None
    shown = {}
    for index, row in enumerate(records):
        if isinstance(row, dict) and isinstance(row.get("kind"), str) and row["kind"] in ROLES:
            shown.setdefault(row["kind"], index)
    if set(shown) != set(ROLES):
        return None
    request = intent_review_request()
    if request["status"] != "evidence_required" or view.get("review_policy") != request["policy"]:
        return None
    contract = load_json_object_strict_cached(CONFIG_DIR / "dead_code_language_agnostic_core_contract.json",
        label="Dead-code core contract")
    policy = contract.get("manual_review_candidate")
    if (not isinstance(policy, dict) or set(policy) != {"id", "required_fact_kinds", "status", "basis"}
            or not isinstance(policy.get("id"), str) or not policy["id"] or len(policy["id"]) > 100
            or policy.get("status") != "needs_manual_intent_review"
            or policy.get("basis") != "same_file_source_fact_cooccurrence_only"
            or not isinstance(policy.get("required_fact_kinds"), list)
            or any(not isinstance(kind, str) for kind in policy["required_fact_kinds"])
            or len(policy["required_fact_kinds"]) != len(ROLES) or set(policy["required_fact_kinds"]) != set(ROLES)):
        return None
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"status": policy["status"], "basis": policy["basis"], "policy": policy["id"], "policy_sha256": digest,
        "evidence_record_indices": [shown[kind] for kind in policy["required_fact_kinds"]],
        "relation_status": "not_established", "counterevidence_status": "not_assessed",
        "integration_status": "unknown", "mutation_proposed": False}

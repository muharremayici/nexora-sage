from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached


POLICY_PATH = CONFIG_DIR / "sage_self_development_validation_profiles.json"
ACCEPTANCE_FIXTURE_PREFIX = "fixtures/self-development-validation/"


def load_validation_profile_policy() -> dict[str, Any]:
    policy = load_json_object_strict_cached(
        POLICY_PATH,
        label="SAGE self-development validation profiles",
    )
    validate_policy(policy)
    return policy


def _non_empty_string_list(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{label} must be a non-empty string list")
    return [item.strip() for item in value]


def validate_policy(policy: Mapping[str, Any]) -> None:
    validation = policy.get("validation")
    input_contract = policy.get("input_contract")
    selection = policy.get("selection")
    profiles = policy.get("profiles")
    if not all(isinstance(value, Mapping) for value in (validation, input_contract, selection, profiles)):
        raise ValueError("Validation profile policy is missing required object sections")

    order = _non_empty_string_list(selection.get("profile_order"), "selection.profile_order")
    required_profiles = _non_empty_string_list(
        validation.get("required_profiles"),
        "validation.required_profiles",
    )
    if len(order) != len(set(order)) or set(order) != set(required_profiles):
        raise ValueError("Profile order must contain every required profile exactly once")
    if set(profiles) != set(order):
        raise ValueError("profiles must exactly match selection.profile_order")
    for expected_rank, profile_id in enumerate(order):
        profile = profiles.get(profile_id)
        if not isinstance(profile, Mapping) or profile.get("rank") != expected_rank:
            raise ValueError(f"Profile rank mismatch: {profile_id}")
        _non_empty_string_list(profile.get("minimum_checks"), f"profiles.{profile_id}.minimum_checks")
        budget = profile.get("cost_budget")
        if not isinstance(budget, Mapping):
            raise ValueError(f"Missing cost budget: {profile_id}")
        for key in ("target_runtime_seconds", "maximum_sibling_sweeps", "full_release_proof_runs"):
            value = budget.get(key)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"Invalid {profile_id} cost budget: {key}")

    universal = set(_non_empty_string_list(policy.get("universal_invariants"), "universal_invariants"))
    required_universal = set(
        _non_empty_string_list(
            validation.get("required_universal_invariants"),
            "validation.required_universal_invariants",
        )
    )
    if not required_universal.issubset(universal):
        raise ValueError("Universal validation invariants are incomplete")

    for key in (
        "required_fields",
        "allowed_signals",
        "allowed_reversibility",
        "allowed_release_phases",
        "allowed_scope_kinds",
    ):
        _non_empty_string_list(input_contract.get(key), f"input_contract.{key}")
    if input_contract.get("unknown_signal_minimum_profile") not in profiles:
        raise ValueError("Unknown-signal minimum profile is invalid")
    if selection.get("default_profile") not in profiles:
        raise ValueError("Default validation profile is invalid")

    allowed_operators = set(
        _non_empty_string_list(
            validation.get("allowed_condition_operators"),
            "validation.allowed_condition_operators",
        )
    )
    rules = selection.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("Selection rules must be non-empty")
    rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise ValueError("Selection rule must be an object")
        rule_id = str(rule.get("id") or "")
        if not rule_id or rule_id in rule_ids:
            raise ValueError("Selection rule ids must be unique and non-empty")
        rule_ids.add(rule_id)
        if rule.get("strength") not in {"hard", "advisory"}:
            raise ValueError(f"Invalid rule strength: {rule_id}")
        if rule.get("minimum_profile") not in profiles:
            raise ValueError(f"Invalid rule profile: {rule_id}")
        condition = rule.get("when")
        if not isinstance(condition, Mapping) or not condition:
            raise ValueError(f"Missing rule condition: {rule_id}")
        unknown_operators = set(condition) - allowed_operators
        if unknown_operators:
            raise ValueError(f"Unknown condition operators for {rule_id}: {sorted(unknown_operators)}")

    evidence = policy.get("evidence_identity")
    downgrade = policy.get("downgrade")
    if not isinstance(evidence, Mapping) or not isinstance(downgrade, Mapping):
        raise ValueError("Evidence identity and downgrade contracts are required")
    _non_empty_string_list(evidence.get("required_fields"), "evidence_identity.required_fields")
    if evidence.get("duplicate_behavior") != "reject_as_independent_evidence":
        raise ValueError("Duplicate evidence must not count as independent confidence")
    if downgrade.get("below_hard_floor") != "reject" or downgrade.get("missing_reason") != "reject":
        raise ValueError("Downgrade policy must fail closed")

    acceptance_cases = policy.get("acceptance_cases")
    if not isinstance(acceptance_cases, list) or not acceptance_cases:
        raise ValueError("acceptance_cases must be a non-empty list")
    for case in acceptance_cases:
        if not isinstance(case, Mapping):
            raise ValueError("Acceptance cases must be objects")
        case_id = str(case.get("id") or "unnamed")
        change = case.get("change")
        if not isinstance(change, Mapping):
            raise ValueError(f"Acceptance case change must be an object: {case_id}")
        changed_files = _non_empty_string_list(
            change.get("changed_files"),
            f"acceptance_cases.{case_id}.change.changed_files",
        )
        non_fixture_paths = [
            path for path in changed_files if not path.startswith(ACCEPTANCE_FIXTURE_PREFIX)
        ]
        if non_fixture_paths:
            raise ValueError(
                f"Acceptance case paths must use {ACCEPTANCE_FIXTURE_PREFIX}: "
                f"{case_id}={non_fixture_paths}"
            )


def _normalized_change(change: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    contract = policy["input_contract"]
    required = set(contract["required_fields"])
    missing = sorted(required - set(change))
    if missing:
        raise ValueError(f"Change context missing required fields: {missing}")

    changed_files = change.get("changed_files")
    signals = change.get("signals")
    fan_out = change.get("consumer_fan_out")
    behavior_change = change.get("behavior_change")
    if not isinstance(changed_files, list) or any(not isinstance(item, str) or not item for item in changed_files):
        raise ValueError("changed_files must be a string list")
    if not isinstance(signals, list) or any(not isinstance(item, str) or not item for item in signals):
        raise ValueError("signals must be a string list")
    if not isinstance(fan_out, int) or isinstance(fan_out, bool) or fan_out < 0:
        raise ValueError("consumer_fan_out must be a non-negative integer")
    if not isinstance(behavior_change, bool):
        raise ValueError("behavior_change must be boolean")

    reversibility = change.get("reversibility")
    release_phase = change.get("release_phase")
    scope_kind = change.get("scope_kind")
    if reversibility not in contract["allowed_reversibility"]:
        raise ValueError(f"Unknown reversibility: {reversibility}")
    if release_phase not in contract["allowed_release_phases"]:
        raise ValueError(f"Unknown release phase: {release_phase}")
    if scope_kind not in contract["allowed_scope_kinds"]:
        raise ValueError(f"Unknown scope kind: {scope_kind}")

    allowed_signals = set(contract["allowed_signals"])
    normalized_signals = sorted(set(signals))
    return {
        "changed_files": list(dict.fromkeys(changed_files)),
        "signals": normalized_signals,
        "unknown_signals": sorted(set(normalized_signals) - allowed_signals),
        "consumer_fan_out": fan_out,
        "reversibility": reversibility,
        "release_phase": release_phase,
        "behavior_change": behavior_change,
        "scope_kind": scope_kind,
    }


def _micro_eligible(change: Mapping[str, Any], contract: Mapping[str, Any]) -> bool:
    return (
        change["release_phase"] in contract["release_phase_in"]
        and change["scope_kind"] in contract["scope_kind_in"]
        and change["behavior_change"] is contract["behavior_change_is"]
        and len(change["changed_files"]) <= contract["changed_file_count_lte"]
        and change["consumer_fan_out"] <= contract["consumer_fan_out_lte"]
        and change["reversibility"] in contract["reversibility_in"]
        and not change["unknown_signals"]
    )


def _condition_matches(condition: Mapping[str, Any], change: Mapping[str, Any]) -> bool:
    results: list[bool] = []
    for operator, expected in condition.items():
        if operator == "release_phase_in":
            results.append(change["release_phase"] in expected)
        elif operator == "any_signal":
            results.append(bool(set(change["signals"]) & set(expected)))
        elif operator == "consumer_fan_out_gte":
            results.append(change["consumer_fan_out"] >= expected)
        elif operator == "changed_file_count_gte":
            results.append(len(change["changed_files"]) >= expected)
        elif operator == "reversibility_in":
            results.append(change["reversibility"] in expected)
        elif operator == "unknown_signal_present":
            results.append(bool(change["unknown_signals"]) is bool(expected))
        else:
            raise ValueError(f"Unsupported validation-profile condition: {operator}")
    return bool(results) and all(results)


def select_validation_profile(
    change: Mapping[str, Any],
    *,
    requested_profile: str | None = None,
    downgrade_reason: str | None = None,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected_policy = dict(policy) if policy is not None else load_validation_profile_policy()
    validate_policy(selected_policy)
    normalized = _normalized_change(change, selected_policy)
    selection = selected_policy["selection"]
    profiles = selected_policy["profiles"]
    order = selection["profile_order"]
    ranks = {profile_id: profiles[profile_id]["rank"] for profile_id in order}

    base_profile = (
        "micro"
        if _micro_eligible(normalized, selection["micro_eligibility"])
        else selection["default_profile"]
    )
    hard_rank = ranks[base_profile]
    recommended_rank = hard_rank
    hard_reasons: list[str] = [f"base:{base_profile}"]
    advisory_reasons: list[str] = []
    for rule in selection["rules"]:
        if not _condition_matches(rule["when"], normalized):
            continue
        rank = ranks[rule["minimum_profile"]]
        if rule["strength"] == "hard":
            hard_rank = max(hard_rank, rank)
            recommended_rank = max(recommended_rank, rank)
            hard_reasons.append(rule["id"])
        else:
            recommended_rank = max(recommended_rank, rank)
            advisory_reasons.append(rule["id"])

    downgrade = {
        "requested_profile": requested_profile,
        "accepted": None,
        "reason": downgrade_reason,
        "decision": "not_requested",
    }
    final_rank = recommended_rank
    if requested_profile is not None:
        if requested_profile not in profiles:
            raise ValueError(f"Unknown requested validation profile: {requested_profile}")
        requested_rank = ranks[requested_profile]
        if requested_rank >= recommended_rank:
            final_rank = requested_rank
            downgrade.update(accepted=True, decision="broadened_or_equal")
        elif requested_rank < hard_rank:
            downgrade.update(accepted=False, decision="rejected_below_hard_floor")
        else:
            minimum_reason = selected_policy["downgrade"]["minimum_reason_characters"]
            if not isinstance(downgrade_reason, str) or len(downgrade_reason.strip()) < minimum_reason:
                downgrade.update(accepted=False, decision="rejected_missing_reason")
            else:
                final_rank = requested_rank
                downgrade.update(accepted=True, decision="accepted_advisory_downgrade")

    final_profile = order[final_rank]
    hard_floor = order[hard_rank]
    recommended_profile = order[recommended_rank]
    return {
        "status": "SELECTED",
        "profile": final_profile,
        "hard_floor": hard_floor,
        "recommended_profile": recommended_profile,
        "universal_invariants": list(selected_policy["universal_invariants"]),
        "minimum_checks": list(profiles[final_profile]["minimum_checks"]),
        "cost_budget": dict(profiles[final_profile]["cost_budget"]),
        "hard_reasons": hard_reasons,
        "advisory_reasons": advisory_reasons,
        "unknown_signals": normalized["unknown_signals"],
        "downgrade": downgrade,
        "invalidation": dict(selected_policy["invalidation"]),
    }


def validation_evidence_identity(
    evidence: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> str:
    selected_policy = dict(policy) if policy is not None else load_validation_profile_policy()
    validate_policy(selected_policy)
    fields = selected_policy["evidence_identity"]["required_fields"]
    payload: dict[str, str] = {}
    for field in fields:
        value = evidence.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Validation evidence identity missing field: {field}")
        payload[field] = value.strip()
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_independent_evidence(
    records: list[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected_policy = dict(policy) if policy is not None else load_validation_profile_policy()
    identities: dict[str, int] = {}
    duplicates: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        try:
            identity = validation_evidence_identity(record, policy=selected_policy)
        except (TypeError, ValueError) as exc:
            invalid.append({"index": index, "reason": str(exc)})
            continue
        if identity in identities:
            duplicates.append(
                {
                    "index": index,
                    "duplicates_index": identities[identity],
                    "identity": identity,
                }
            )
        else:
            identities[identity] = index
    return {
        "status": "PASS" if not duplicates and not invalid else "FAIL",
        "records": len(records),
        "independent_records": len(identities),
        "duplicates": duplicates,
        "invalid": invalid,
        "duplicate_behavior": selected_policy["evidence_identity"]["duplicate_behavior"],
    }

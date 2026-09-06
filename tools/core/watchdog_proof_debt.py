from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.artifact_freshness_contract import artifact_state_meta


REQUIRED_PROOF_DEBT_FIELDS = (
    "pulse_warning_threshold",
    "changed_file_warning_threshold",
    "violation_warning_threshold",
    "reset_artifact_scope",
    "reset_artifact_ids",
    "missing_reset_artifact_behavior",
    "repo_wide_artifacts_not_proven_by_watchdog",
)


def validate_watchdog_proof_debt_policy(policy: dict[str, Any]) -> dict[str, Any]:
    missing_fields = [field for field in REQUIRED_PROOF_DEBT_FIELDS if field not in policy]
    invalid_thresholds = []
    for field in REQUIRED_PROOF_DEBT_FIELDS[:3]:
        try:
            if int(policy.get(field)) <= 0:
                invalid_thresholds.append(field)
        except (TypeError, ValueError):
            invalid_thresholds.append(field)
    invalid_lists = [
        field
        for field in (
            "reset_artifact_ids",
            "repo_wide_artifacts_not_proven_by_watchdog",
        )
        if not isinstance(policy.get(field), list) or not policy.get(field)
    ]
    recommended_modes = policy.get("recommended_refresh_modes")
    refresh_sequence = policy.get("recommended_refresh_sequence")
    has_modes = isinstance(recommended_modes, list) and bool(recommended_modes)
    has_sequence = isinstance(refresh_sequence, list) and bool(refresh_sequence)
    if not has_modes and not has_sequence:
        invalid_lists.append("recommended_refresh_modes_or_sequence")
    if recommended_modes is not None and not has_modes:
        invalid_lists.append("recommended_refresh_modes")
    if refresh_sequence is not None and (
        not has_sequence
        or any(not isinstance(command, str) or not command.strip() for command in refresh_sequence)
    ):
        invalid_lists.append("recommended_refresh_sequence")
    invalid_behavior = (
        []
        if str(policy.get("missing_reset_artifact_behavior") or "") in {"debt_due", "report_only"}
        else ["missing_reset_artifact_behavior"]
    )
    invalid_scope = (
        []
        if str(policy.get("reset_artifact_scope") or "")
        in {"current_target_output", "primary_sage_output"}
        else ["reset_artifact_scope"]
    )
    modes_for_validation = recommended_modes if isinstance(recommended_modes, list) else []
    auto_refresh = policy.get("auto_refresh") if isinstance(policy.get("auto_refresh"), dict) else {}
    commands = auto_refresh.get("command_by_mode") if isinstance(auto_refresh.get("command_by_mode"), dict) else {}
    invalid_recommendations = [
        str(mode)
        for mode in modes_for_validation
        if not isinstance(mode, str)
        or not mode.strip()
        or not isinstance(commands.get(mode), list)
        or not commands.get(mode)
    ]
    return {
        "valid": not (
            missing_fields
            or invalid_thresholds
            or invalid_lists
            or invalid_behavior
            or invalid_scope
            or invalid_recommendations
        ),
        "missing_fields": missing_fields,
        "invalid_thresholds": invalid_thresholds,
        "invalid_lists": invalid_lists,
        "invalid_behavior": invalid_behavior,
        "invalid_scope": invalid_scope,
        "invalid_recommendations": invalid_recommendations,
    }


def _epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0.0


def _required_positive_int(policy: dict[str, Any], field: str) -> int:
    try:
        value = int(policy[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"watchdog proof-debt policy requires positive integer field: {field}") from exc
    if value <= 0:
        raise ValueError(f"watchdog proof-debt policy field must be positive: {field}")
    return value


def _broad_proof_observation(raw_dir: Path, artifact_ids: list[str]) -> dict[str, Any]:
    artifacts = []
    for artifact_id in artifact_ids:
        meta = artifact_state_meta(raw_dir, artifact_id)
        freshness_epoch = float(meta.get("validation_mtime") or meta.get("mtime") or 0.0)
        artifacts.append(
            {
                "artifact": artifact_id,
                "exists": bool(meta.get("exists")),
                "source": str(meta.get("source") or "missing"),
                "freshness_epoch": freshness_epoch,
            }
        )
    missing = [row["artifact"] for row in artifacts if not row["exists"]]
    return {"artifacts": artifacts, "missing_artifacts": missing, "complete": not missing}


def advance_watchdog_proof_debt(
    previous_state: dict[str, Any] | None,
    policy: dict[str, Any],
    *,
    raw_dir: Path,
    generated_at: str,
    changed_file_count: int,
    violation_count: int,
) -> dict[str, Any]:
    previous = previous_state if isinstance(previous_state, dict) else {}
    contract = validate_watchdog_proof_debt_policy(policy)
    if not contract["valid"]:
        return {
            "status": "due",
            "due_reasons": ["proof_debt_policy_incomplete"],
            "policy_contract_error": contract,
            "pulses_since_broad_refresh": int(previous.get("pulses_since_broad_refresh") or 0) + 1,
            "changed_files_since_broad_refresh": int(previous.get("changed_files_since_broad_refresh") or 0)
            + max(0, int(changed_file_count or 0)),
            "broad_refresh_detected": False,
            "broad_proof_observation": {"artifacts": [], "missing_artifacts": [], "complete": False},
        }
    pulse_threshold = _required_positive_int(policy, "pulse_warning_threshold")
    changed_threshold = _required_positive_int(policy, "changed_file_warning_threshold")
    violation_threshold = _required_positive_int(policy, "violation_warning_threshold")
    artifact_ids = [str(item) for item in policy.get("reset_artifact_ids", []) if str(item).strip()]
    observation = _broad_proof_observation(raw_dir, artifact_ids)
    now_epoch = _epoch(generated_at)
    window_started_epoch = float(previous.get("window_started_epoch") or now_epoch)
    refreshed = bool(
        previous
        and observation["complete"]
        and observation["artifacts"]
        and all(float(row["freshness_epoch"]) > window_started_epoch for row in observation["artifacts"])
    )
    if refreshed:
        pulses = 0
        changed_files = 0
        window_started_epoch = now_epoch
        window_started_at = generated_at
    else:
        pulses = int(previous.get("pulses_since_broad_refresh") or 0)
        changed_files = int(previous.get("changed_files_since_broad_refresh") or 0)
        window_started_at = str(previous.get("window_started_at") or generated_at)

    pulses += 1
    changed_files += max(0, int(changed_file_count or 0))
    due_reasons: list[str] = []
    if violation_count >= violation_threshold:
        due_reasons.append("live_violation_threshold")
    if pulses >= pulse_threshold:
        due_reasons.append("pulse_threshold")
    if changed_files >= changed_threshold:
        due_reasons.append("changed_file_threshold")
    if observation["missing_artifacts"] and policy.get("missing_reset_artifact_behavior") == "debt_due":
        due_reasons.append("broad_proof_artifact_missing")

    return {
        "status": "due" if due_reasons else "not_due",
        "due_reasons": due_reasons,
        "pulses_since_broad_refresh": pulses,
        "changed_files_since_broad_refresh": changed_files,
        "window_started_at": window_started_at,
        "window_started_epoch": window_started_epoch,
        "broad_refresh_detected": refreshed,
        "thresholds": {
            "pulse_warning_threshold": pulse_threshold,
            "changed_file_warning_threshold": changed_threshold,
            "violation_warning_threshold": violation_threshold,
        },
        "broad_proof_observation": observation,
    }

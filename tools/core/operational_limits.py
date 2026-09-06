from __future__ import annotations

import math
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict
from tools.core.persistence_limits import DEFAULT_PERSISTENCE_LIMITS


DEFAULT_OPERATIONAL_LIMITS: dict[str, Any] = {
    **DEFAULT_PERSISTENCE_LIMITS,
    "atlas_batch_sequencer_timeout_seconds": 180,
    "bootstrap_command_timeout_seconds": 180,
    "cli_command_timeout_seconds": 180,
    "cli_pipeline_refresh_timeout_seconds": 1000,
    "ci_release_check_default_timeout_seconds": 180,
    "ci_release_check_release_timeout_seconds": 1800,
    "distribution_cli_help_timeout_seconds": 60,
    "distribution_git_visibility_retry_attempts": 2,
    "distribution_git_visibility_retry_delay_ms": 200,
    "clean_mirror_quiescence_timeout_seconds": 15,
    "clean_mirror_quiescence_probe_interval_ms": 250,
    "clean_mirror_quiescence_stable_samples": 3,
    "artifact_shadow_flush_timeout_seconds": 15,
    "entrypoint_failure_default_timeout_seconds": 180,
    "entrypoint_failure_dead_code_drill_timeout_seconds": 300,
    "external_polyglot_smoke_timeout_seconds": 90,
    "external_react_smoke_timeout_seconds": 90,
    "external_target_smoke_timeout_seconds": 180,
    "install_proof_daily_timeout_seconds": 2400,
    "install_proof_release_timeout_seconds": 4200,
    "install_proof_smoke_timeout_seconds": 240,
    "install_proof_step_timeout_seconds": {
        "installation_contract": 180,
        "doctor": 90,
        "mcp_config": 60,
        "release_language": 60,
        "final_consistency": 120,
        "init": 1200,
        "daily_run": 1200,
        "installed_distribution_surface": 180,
    },
    "quant_git_status_timeout_seconds": 5,
    "react_v11_fixture_ast_timeout_seconds": 60,
    "setup_interactive_timeout_seconds": 180,
    "setup_wizard_step_timeout_seconds": 1200,
    "sqlite_busy_timeout_ms": 30000,
    "sqlite_schema_initialize_retries": 3,
    "sqlite_schema_initialize_retry_delay_ms": 100,
    "sqlite_read_timeout_seconds": 5,
    "sqlite_write_timeout_seconds": 30,
    "pipeline_step_heartbeat_seconds": 15,
    "patch_applicability_timeout_seconds": 30,
    "watchdog_git_restore_timeout_seconds": 30,
}


def operational_limit_seconds(key: str) -> int:
    """Return a centrally governed operational timeout/limit in seconds."""

    policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
    limits: Any = policy.get("operational_limits") if isinstance(policy, dict) else {}
    value = limits.get(key) if isinstance(limits, dict) else None
    if value is None:
        value = DEFAULT_OPERATIONAL_LIMITS.get(key)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(DEFAULT_OPERATIONAL_LIMITS[key]))


def watchdog_git_restore_timeout_seconds() -> int:
    return operational_limit_seconds("watchdog_git_restore_timeout_seconds")


def patch_applicability_timeout_seconds() -> int:
    return operational_limit_seconds("patch_applicability_timeout_seconds")


def atlas_batch_sequencer_timeout_seconds() -> int:
    return operational_limit_seconds("atlas_batch_sequencer_timeout_seconds")


def atlas_staging_batch_size() -> int:
    return operational_limit_seconds("atlas_staging_batch_size")


def atlas_staging_file_payload_limit_bytes() -> int:
    return operational_limit_seconds("atlas_staging_file_payload_limit_bytes")


def state_payload_inline_limit_bytes() -> int:
    return operational_limit_seconds("state_payload_inline_limit_bytes")


def state_payload_part_size_bytes() -> int:
    return operational_limit_seconds("state_payload_part_size_bytes")


def bootstrap_command_timeout_seconds() -> int:
    return operational_limit_seconds("bootstrap_command_timeout_seconds")


def cli_command_timeout_seconds() -> int:
    return operational_limit_seconds("cli_command_timeout_seconds")


def cli_pipeline_refresh_timeout_seconds() -> int:
    return operational_limit_seconds("cli_pipeline_refresh_timeout_seconds")


def ci_release_check_default_timeout_seconds() -> int:
    return operational_limit_seconds("ci_release_check_default_timeout_seconds")


def ci_release_check_release_timeout_seconds() -> int:
    return operational_limit_seconds("ci_release_check_release_timeout_seconds")


def distribution_cli_help_timeout_seconds() -> int:
    return operational_limit_seconds("distribution_cli_help_timeout_seconds")


def distribution_git_visibility_retry_attempts() -> int:
    return operational_limit_seconds("distribution_git_visibility_retry_attempts")


def distribution_git_visibility_retry_delay_ms() -> int:
    return operational_limit_seconds("distribution_git_visibility_retry_delay_ms")


def clean_mirror_quiescence_timeout_seconds() -> int:
    return operational_limit_seconds("clean_mirror_quiescence_timeout_seconds")


def clean_mirror_quiescence_probe_interval_ms() -> int:
    return operational_limit_seconds("clean_mirror_quiescence_probe_interval_ms")


def clean_mirror_quiescence_stable_samples() -> int:
    return operational_limit_seconds("clean_mirror_quiescence_stable_samples")


def artifact_shadow_flush_timeout_seconds() -> int:
    return operational_limit_seconds("artifact_shadow_flush_timeout_seconds")


def entrypoint_failure_default_timeout_seconds() -> int:
    return operational_limit_seconds("entrypoint_failure_default_timeout_seconds")


def entrypoint_failure_dead_code_drill_timeout_seconds() -> int:
    return operational_limit_seconds("entrypoint_failure_dead_code_drill_timeout_seconds")


def external_polyglot_smoke_timeout_seconds() -> int:
    return operational_limit_seconds("external_polyglot_smoke_timeout_seconds")


def external_react_smoke_timeout_seconds() -> int:
    return operational_limit_seconds("external_react_smoke_timeout_seconds")


def external_target_smoke_timeout_seconds() -> int:
    return operational_limit_seconds("external_target_smoke_timeout_seconds")


def react_v11_fixture_ast_timeout_seconds() -> int:
    return operational_limit_seconds("react_v11_fixture_ast_timeout_seconds")


def setup_interactive_timeout_seconds() -> int:
    return operational_limit_seconds("setup_interactive_timeout_seconds")


def setup_wizard_step_timeout_seconds() -> int:
    return operational_limit_seconds("setup_wizard_step_timeout_seconds")


def quant_git_status_timeout_seconds() -> int:
    return operational_limit_seconds("quant_git_status_timeout_seconds")


def install_proof_timeout_seconds(level: str) -> int:
    normalized = str(level or "smoke").strip().lower()
    if normalized == "daily":
        return operational_limit_seconds("install_proof_daily_timeout_seconds")
    if normalized == "release":
        return operational_limit_seconds("install_proof_release_timeout_seconds")
    return operational_limit_seconds("install_proof_smoke_timeout_seconds")


def install_proof_step_timeout_seconds(step_id: str) -> int:
    normalized = str(step_id or "").strip()
    defaults = DEFAULT_OPERATIONAL_LIMITS.get("install_proof_step_timeout_seconds", {})
    default_value = defaults.get(normalized, 180) if isinstance(defaults, dict) else 180
    policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
    limits: Any = policy.get("operational_limits") if isinstance(policy, dict) else {}
    step_limits = limits.get("install_proof_step_timeout_seconds") if isinstance(limits, dict) else {}
    value = step_limits.get(normalized) if isinstance(step_limits, dict) else None
    if value is None:
        value = default_value
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default_value))


def _policy_integer_at_path(policy: dict[str, Any], path: str) -> int:
    """Resolve a dot-separated policy path to one positive integer limit."""

    value: Any = policy
    for segment in str(path or "").split("."):
        if not segment or not isinstance(value, dict) or segment not in value:
            raise KeyError(path)
        value = value[segment]
    return max(1, int(value))


def release_proof_timeout_profile_minimum_seconds(profile_id: str) -> int | None:
    """Resolve a release-proof nested-plan floor from the central execution policy."""

    normalized = str(profile_id or "").strip()
    if not normalized:
        return None
    policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
    profiles = policy.get("release_proof_timeout_profiles") if isinstance(policy, dict) else {}
    profile = profiles.get(normalized) if isinstance(profiles, dict) else {}
    if not isinstance(profile, dict):
        raise ValueError(f"Unknown release-proof timeout profile: {normalized}")
    paths = profile.get("nested_operational_limit_paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError(f"Release-proof timeout profile '{normalized}' has no nested operational limit paths.")
    try:
        nested_budget = sum(_policy_integer_at_path(policy, str(path)) for path in paths if str(path))
        headroom_ratio = max(0.0, float(profile.get("headroom_ratio", 0.0)))
        configured_minimum = max(1, int(profile.get("minimum_timeout_seconds", 1)))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Release-proof timeout profile '{normalized}' is invalid.") from exc
    return max(configured_minimum, int(math.ceil(nested_budget * (1.0 + headroom_ratio))))


def sqlite_read_timeout_seconds() -> int:
    return operational_limit_seconds("sqlite_read_timeout_seconds")


def sqlite_write_timeout_seconds() -> int:
    return operational_limit_seconds("sqlite_write_timeout_seconds")


def sqlite_busy_timeout_ms() -> int:
    return operational_limit_seconds("sqlite_busy_timeout_ms")


def sqlite_schema_initialize_retries() -> int:
    return operational_limit_seconds("sqlite_schema_initialize_retries")


def sqlite_schema_initialize_retry_delay_ms() -> int:
    return operational_limit_seconds("sqlite_schema_initialize_retry_delay_ms")


def pipeline_step_heartbeat_selection(scope: str = "default") -> dict[str, Any]:
    """Return the bounded local cadence and its explicit evidence basis."""

    from tools.core.heartbeat_cadence import heartbeat_cadence_selection

    return heartbeat_cadence_selection(scope)


def pipeline_step_heartbeat_seconds(scope: str = "default") -> int:
    """Return a bounded local cadence, with the operational limit as safe policy fallback."""

    selection = pipeline_step_heartbeat_selection(scope)
    try:
        return max(1, int(selection.get("interval_seconds")))
    except (TypeError, ValueError):
        return operational_limit_seconds("pipeline_step_heartbeat_seconds")

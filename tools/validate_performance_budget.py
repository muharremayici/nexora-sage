from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.core.config import CONFIG_DIR, LOGS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.workload_profile import build_workload_profile, dead_code_budget_seconds

PERF_CONFIG_PATH = CONFIG_DIR / "performance_budget.json"
PIPELINE_LOG_PATH = LOGS_DIR / "pipeline.log"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pct = max(0.0, min(100.0, percentile)) / 100.0
    index = round((len(ordered) - 1) * pct)
    return ordered[int(index)]


def _rolling_decision(value: float | None, samples: list[float], threshold: float, config: dict[str, Any]) -> dict[str, Any]:
    policy = config.get("rolling_policy", {}) if isinstance(config.get("rolling_policy"), dict) else {}
    decision_policy_values = policy.get("decision_policy_values", {}) if isinstance(policy.get("decision_policy_values"), dict) else {}
    window = int(policy.get("window", 5) or 5)
    min_samples = int(policy.get("min_samples", 3) or 3)
    hard_fail_multiplier = float(policy.get("hard_fail_multiplier", 1.20) or 1.20)
    percentile = float(policy.get("percentile", 95) or 95)
    recent = samples[-window:] if window > 0 else list(samples)
    median = _median(recent)
    pct_value = _percentile(recent, percentile)
    if value is None:
        return {
            "passed": False,
            "details": f"missing metric; threshold<={threshold:.2f}s",
            "policy": str(decision_policy_values.get("missing_metric") or "missing_metric"),
            "rolling": {"samples": recent, "median": median, "percentile": pct_value},
        }
    if value <= threshold:
        return {
            "passed": True,
            "details": f"value={value:.2f}s threshold<={threshold:.2f}s",
            "policy": str(decision_policy_values.get("latest_within_budget") or "latest_within_budget"),
            "rolling": {"samples": recent, "median": median, "percentile": pct_value},
        }
    if value > threshold * hard_fail_multiplier:
        return {
            "passed": False,
            "details": f"value={value:.2f}s threshold<={threshold:.2f}s hard_fail>{threshold * hard_fail_multiplier:.2f}s",
            "policy": str(decision_policy_values.get("hard_fail_multiplier") or "hard_fail_multiplier"),
            "rolling": {"samples": recent, "median": median, "percentile": pct_value},
        }
    if len(recent) >= min_samples and median is not None and median <= threshold:
        return {
            "passed": True,
            "details": f"value={value:.2f}s threshold<={threshold:.2f}s rolling_median={median:.2f}s samples={len(recent)}",
            "policy": str(decision_policy_values.get("rolling_median_grace") or "rolling_median_grace"),
            "rolling": {"samples": recent, "median": median, "percentile": pct_value},
        }
    return {
        "passed": False,
        "details": f"value={value:.2f}s threshold<={threshold:.2f}s rolling_samples={len(recent)}",
        "policy": str(decision_policy_values.get("latest_over_budget") or "latest_over_budget"),
        "rolling": {"samples": recent, "median": median, "percentile": pct_value},
    }


def _performance_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(config, dict) and config:
        return config
    loaded = load_json_file(PERF_CONFIG_PATH, {})
    return loaded if isinstance(loaded, dict) else {}


def _log_parsing(config: dict[str, Any] | None = None) -> dict[str, Any]:
    loaded = _performance_config(config)
    parsing = loaded.get("log_parsing", {}) if isinstance(loaded.get("log_parsing"), dict) else {}
    return parsing


def _session_policy(config: dict[str, Any] | None = None) -> dict[str, Any]:
    loaded = _performance_config(config)
    policy = loaded.get("session_policy", {}) if isinstance(loaded.get("session_policy"), dict) else {}
    return policy


def _pattern(config: dict[str, Any] | None, key: str) -> str:
    value = _log_parsing(config).get(key, "")
    return str(value or "")


def _latest_atlas_phase_profile(text: str, config: dict[str, Any] | None = None) -> dict[str, float]:
    pattern = _pattern(config, "atlas_phase_pattern")
    if not pattern:
        return {}
    matches = re.findall(pattern, text)
    if not matches:
        return {}
    values = matches[-1]
    if not isinstance(values, tuple) or len(values) != 8:
        return {}
    keys = ("pre_build", "build", "bridge", "validate", "persist", "commit", "workload", "ram_cache")
    return {key: float(value) for key, value in zip(keys, values)}


def _latest_atlas_persistence_profile(text: str, config: dict[str, Any] | None = None) -> dict[str, float | int]:
    pattern = _pattern(config, "atlas_persistence_pattern")
    if not pattern:
        return {}
    matches = re.findall(pattern, text)
    if not matches:
        return {}
    values = matches[-1]
    if not isinstance(values, tuple) or len(values) != 8:
        return {}
    return {
        "state_payload_chars": int(values[0]),
        "state_payload_bytes": int(values[1]),
        "state_payload_serialize_seconds": float(values[2]),
        "state_payload_encode_seconds": float(values[3]),
        "state_payload_hash_seconds": float(values[4]),
        "state_payload_sqlite_seconds": float(values[5]),
        "atlas_relational_index_seconds": float(values[6]),
        "total_save_raw_seconds": float(values[7]),
    }


def _session_policy_value(config: dict[str, Any] | None, key: str) -> str:
    value = _session_policy(config).get(key, "")
    return str(value or "")


def _list_as_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _config_contract_issues(config: dict[str, Any]) -> list[str]:
    contract = config.get("validation_contract", {}) if isinstance(config.get("validation_contract"), dict) else {}
    log_parsing = _log_parsing(config)
    session_policy = _session_policy(config)
    required_log = contract.get("required_log_parsing_fields", [])
    required_session = contract.get("required_session_policy_fields", [])
    required_decision_policy = contract.get("required_decision_policy_fields", [])
    required_bottleneck_report = _list_as_strings(contract.get("required_bottleneck_report_fields", []))
    required_bottleneck_recommendation = _list_as_strings(contract.get("required_bottleneck_recommendation_fields", []))
    issues: list[str] = []
    if not isinstance(required_log, list) or not required_log:
        issues.append("validation_contract.required_log_parsing_fields_missing")
    else:
        issues.extend(
            f"log_parsing.{field}"
            for field in required_log
            if not log_parsing.get(str(field))
        )
    if not isinstance(required_session, list) or not required_session:
        issues.append("validation_contract.required_session_policy_fields_missing")
    else:
        issues.extend(
            f"session_policy.{field}"
            for field in required_session
            if not session_policy.get(str(field))
        )
    evidence_statuses = session_policy.get("evidence_statuses", [])
    if not isinstance(evidence_statuses, list) or len(evidence_statuses) < 3:
        issues.append("session_policy.evidence_statuses_requires_three_values")
    decision_policy_values = config.get("rolling_policy", {}).get("decision_policy_values", {}) if isinstance(config.get("rolling_policy"), dict) else {}
    required_decision_policy_values = _list_as_strings(required_decision_policy)
    if not required_decision_policy_values:
        issues.append("validation_contract.required_decision_policy_fields_missing")
    if not isinstance(decision_policy_values, dict):
        issues.append("rolling_policy.decision_policy_values_missing")
    else:
        issues.extend(
            f"rolling_policy.decision_policy_values.{field}"
            for field in required_decision_policy_values
            if not decision_policy_values.get(field)
        )
    bottleneck_report = config.get("bottleneck_report", {}) if isinstance(config.get("bottleneck_report"), dict) else {}
    if not required_bottleneck_report:
        issues.append("validation_contract.required_bottleneck_report_fields_missing")
    if not isinstance(bottleneck_report, dict) or not bottleneck_report:
        issues.append("bottleneck_report_missing")
    else:
        issues.extend(
            f"bottleneck_report.{field}"
            for field in required_bottleneck_report
            if field not in bottleneck_report
        )
        recommendation_groups = (
            "step_recommendations",
            "aggregate_recommendations",
            "benchmark_advisories",
        )
        for group in recommendation_groups:
            rows = bottleneck_report.get(group, [])
            if not isinstance(rows, list):
                issues.append(f"bottleneck_report.{group}_must_be_list")
                continue
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    issues.append(f"bottleneck_report.{group}.{index}_must_be_object")
                    continue
                issues.extend(
                    f"bottleneck_report.{group}.{index}.{field}"
                    for field in required_bottleneck_recommendation
                    if not row.get(field)
                )
        profile_rule = bottleneck_report.get("profile_integrity_recommendation", {})
        if not isinstance(profile_rule, dict):
            issues.append("bottleneck_report.profile_integrity_recommendation_must_be_object")
        else:
            issues.extend(
                f"bottleneck_report.profile_integrity_recommendation.{field}"
                for field in required_bottleneck_recommendation
                if not profile_rule.get(field)
            )
    return issues


def _last_float(pattern: str, text: str) -> float | None:
    if not pattern:
        return None
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except (TypeError, ValueError):
        return None


def _all_floats(pattern: str, text: str) -> list[float]:
    values: list[float] = []
    if not pattern:
        return values
    for match in re.findall(pattern, text, flags=re.MULTILINE):
        try:
            values.append(float(match))
        except (TypeError, ValueError):
            continue
    return values


def _latest_session(log_text: str, config: dict[str, Any] | None = None) -> str:
    marker = str(_log_parsing(config).get("session_start_marker") or "")
    if not marker:
        return log_text
    idx = log_text.rfind(marker)
    if idx < 0:
        return log_text
    return log_text[idx:]


def _session_chunks(log_text: str, config: dict[str, Any] | None = None) -> list[str]:
    marker = str(_log_parsing(config).get("session_start_marker") or "")
    if not marker:
        return []
    chunks = log_text.split(marker)
    return [marker + chunk for chunk in chunks[1:]]


def _session_mode(chunk: str, config: dict[str, Any] | None = None) -> str:
    match = re.search(_pattern(config, "mode_pattern"), chunk) if _pattern(config, "mode_pattern") else None
    if match:
        return match.group(1)
    watchdog_markers = _log_parsing(config).get("watchdog_markers", [])
    if isinstance(watchdog_markers, list) and any(str(marker) in chunk for marker in watchdog_markers):
        return _session_policy_value(config, "fallback_watchdog_mode")
    return _session_policy_value(config, "fallback_normal_mode")


def _latest_budget_session(log_text: str, config: dict[str, Any] | None = None) -> tuple[str, str, str | None]:
    """Return the latest session suitable for release/full performance budgets.

    Explicit step and watchdog sessions are useful operational telemetry, but
    they can pull unusual dependency closures and should not overwrite the
    release/full budget signal.
    """
    chunks = _session_chunks(log_text, config)
    if not chunks:
        return "", _session_policy_value(config, "no_completed_budget_session"), "missing_session_markers"
    ignored_modes = {
        str(item)
        for item in _session_policy(config).get("ignored_budget_modes", [])
        if str(item).strip()
    }
    latest = chunks[-1]
    latest_mode = _session_mode(latest, config)
    for chunk in reversed(chunks):
        mode = _session_mode(chunk, config)
        if mode not in ignored_modes and _last_float(_pattern(config, "completion_seconds_pattern"), chunk) is not None:
            ignored = latest_mode if chunk is not latest and latest_mode in ignored_modes else None
            return chunk, mode, ignored
    return "", _session_policy_value(config, "no_completed_budget_session"), latest_mode


def _session_timestamps(chunk: str, config: dict[str, Any] | None = None) -> dict[str, str | None]:
    matches = re.findall(
        _pattern(config, "timestamp_pattern"),
        chunk,
        flags=re.MULTILINE,
    ) if _pattern(config, "timestamp_pattern") else []
    if not matches:
        return {"started_at": None, "last_event_at": None}
    return {"started_at": matches[0], "last_event_at": matches[-1]}


def _pipeline_samples_by_force_profile(log_text: str, forced: bool, config: dict[str, Any] | None = None) -> list[float]:
    samples: list[float] = []
    ignored_modes = {
        str(item)
        for item in _session_policy(config).get("ignored_budget_modes", [])
        if str(item).strip()
    }
    for chunk in _session_chunks(log_text, config):
        if _session_mode(chunk, config) in ignored_modes:
            continue
        is_forced = str(_log_parsing(config).get("forced_marker") or "") in chunk
        if is_forced != forced:
            continue
        value = _last_float(_pattern(config, "completion_seconds_pattern"), chunk)
        if value is not None:
            samples.append(value)
    return samples


def _completed_session_total(chunk: str, config: dict[str, Any] | None = None) -> float | None:
    return _last_float(_pattern(config, "completion_seconds_pattern"), chunk)


def _pipeline_samples_by_mode(log_text: str, mode_name: str, config: dict[str, Any] | None = None) -> list[float]:
    samples: list[float] = []
    for chunk in _session_chunks(log_text, config):
        if _session_mode(chunk, config) != mode_name:
            continue
        value = _completed_session_total(chunk, config)
        if value is not None:
            samples.append(value)
    return samples


def _latest_completed_session_by_mode(log_text: str, mode_name: str, config: dict[str, Any] | None = None) -> tuple[str, str, float] | None:
    for chunk in reversed(_session_chunks(log_text, config)):
        mode = _session_mode(chunk, config)
        if mode != mode_name:
            continue
        total = _completed_session_total(chunk, config)
        if total is not None:
            return chunk, mode, total
    return None


def _latest_completed_forced_session(log_text: str, config: dict[str, Any] | None = None) -> tuple[str, str, float] | None:
    ignored_modes = {
        str(item)
        for item in _session_policy(config).get("ignored_budget_modes", [])
        if str(item).strip()
    }
    for chunk in reversed(_session_chunks(log_text, config)):
        if _session_mode(chunk, config) in ignored_modes:
            continue
        if str(_log_parsing(config).get("forced_marker") or "") not in chunk:
            continue
        total = _completed_session_total(chunk, config)
        if total is not None:
            return chunk, _session_mode(chunk, config), total
    return None


def _latest_release_deep_session(log_text: str, config: dict[str, Any] | None = None) -> tuple[str, str, float] | None:
    return _latest_completed_session_by_mode(log_text, _session_policy_value(config, "release_deep_mode"), config)


def _check(name: str, value: float | None, threshold: float) -> dict[str, Any]:
    if value is None:
        return {"name": name, "passed": False, "details": f"missing metric; threshold<={threshold}"}
    passed = value <= threshold
    return {
        "name": name,
        "passed": passed,
        "details": f"value={value:.2f}s threshold<={threshold:.2f}s",
    }


def _rolling_check(name: str, value: float | None, samples: list[float], threshold: float, config: dict[str, Any]) -> dict[str, Any]:
    decision = _rolling_decision(value, samples, threshold, config)
    return {
        "name": name,
        "passed": bool(decision["passed"]),
        "details": str(decision["details"]),
        "policy": decision.get("policy"),
        "rolling": decision.get("rolling", {}),
    }


def _metric_rolling_config(config: dict[str, Any], metric_name: str) -> dict[str, Any]:
    policy = config.get("rolling_policy", {}) if isinstance(config.get("rolling_policy"), dict) else {}
    metric_overrides = policy.get("metric_overrides", {}) if isinstance(policy.get("metric_overrides"), dict) else {}
    override = metric_overrides.get(metric_name, {}) if isinstance(metric_overrides.get(metric_name), dict) else {}
    merged_policy = dict(policy)
    merged_policy.update(override)
    merged_policy.pop("metric_overrides", None)
    merged = dict(config)
    merged["rolling_policy"] = merged_policy
    return merged


def _optional_check(name: str, value: float | None, threshold: float, missing_details: str) -> dict[str, Any]:
    if value is None:
        return {"name": name, "passed": True, "details": missing_details}
    return _check(name, value, threshold)


def _optional_rolling_check(
    name: str,
    value: float | None,
    samples: list[float],
    threshold: float,
    config: dict[str, Any],
    missing_details: str,
) -> dict[str, Any]:
    if value is None:
        return {
            "name": name,
            "passed": True,
            "details": missing_details,
            "policy": _session_policy_value(config, "optional_metric_missing_policy") or "step_not_in_latest_session",
            "rolling": {"samples": samples[-5:]},
        }
    return _rolling_check(name, value, samples, threshold, config)


def _atlas_context(text: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    pattern = _pattern(config, "atlas_context_pattern")
    matches = re.findall(pattern, text, flags=re.MULTILINE) if pattern else []
    if not matches:
        return {"projects": None, "files": None, "total": None}
    projects_s, files_s, total_s = matches[-1]
    try:
        return {"projects": int(projects_s), "files": int(files_s), "total": float(total_s)}
    except (TypeError, ValueError):
        return {"projects": None, "files": None, "total": None}


def _effective_scaled_budget(
    config: dict[str, Any],
    scaling_key: str,
    base_budget: float,
    atlas_projects: int | None,
    atlas_files: int | None,
) -> float:
    scaling = config.get("scaling", {}) if isinstance(config.get("scaling"), dict) else {}
    metric_scaling = scaling.get(scaling_key, {}) if isinstance(scaling.get(scaling_key), dict) else {}
    per_project_seconds = float(metric_scaling.get("per_project_seconds", 0.0) or 0.0)
    file_soft_cap = int(metric_scaling.get("file_soft_cap", 0) or 0)
    file_bucket = int(metric_scaling.get("file_bucket", 0) or 0)
    per_file_bucket_seconds = float(metric_scaling.get("per_file_bucket_seconds", 0.0) or 0.0)

    effective = base_budget
    if atlas_projects is not None and atlas_projects > 1:
        effective += float(atlas_projects - 1) * per_project_seconds
    if atlas_files is not None and file_soft_cap > 0 and atlas_files > file_soft_cap and file_bucket > 0:
        extra_buckets = (atlas_files - file_soft_cap) / float(file_bucket)
        effective += extra_buckets * per_file_bucket_seconds
    return round(effective, 2)


def run_validation() -> dict[str, Any]:
    config = load_json_file(PERF_CONFIG_PATH, {})
    if not isinstance(config, dict):
        config = {}

    budgets = config.get("budgets", {}) if isinstance(config.get("budgets"), dict) else {}
    max_full_pipeline_total = float(budgets.get("full_pipeline_total_seconds", budgets.get("pipeline_total_seconds", 180.0)) or 180.0)
    max_forced_full_pipeline_total = float(budgets.get("forced_full_pipeline_total_seconds", max_full_pipeline_total) or max_full_pipeline_total)
    max_cached_pipeline_total = float(budgets.get("cached_pipeline_total_seconds", 30.0) or 30.0)
    max_atlas_total = float(budgets.get("atlas_total_seconds", 60.0) or 60.0)
    max_fractal_total = float(budgets.get("fractal_total_seconds", 120.0) or 120.0)
    max_dead_code_step = float(budgets.get("dead_code_step_seconds", 45.0) or 45.0)

    if not PIPELINE_LOG_PATH.exists():
        payload = {
            "summary": {"total_checks": 1, "passed_checks": 0, "failed_checks": 1},
            "checks": [{"name": "pipeline_log_exists", "passed": False, "details": f"missing {PIPELINE_LOG_PATH}"}],
        }
        save_json_atomic(RAW_DIR / "performance_budget_validation.json", payload)
        save_text_atomic(
            REPORTS_DIR / "performance_budget_validation.md",
            "# Performance Budget Validation\n\n- Failed: missing `output/logs/pipeline.log`\n",
        )
        return payload

    log_text = PIPELINE_LOG_PATH.read_text(encoding="utf-8", errors="replace")
    physical_latest_session = _latest_session(log_text, config)
    latest_session, selected_session_mode, ignored_latest_session_mode = _latest_budget_session(log_text, config)
    selected_session_timestamps = _session_timestamps(latest_session, config)
    physical_session_timestamps = _session_timestamps(physical_latest_session, config)
    pipeline_totals = _all_floats(_pattern(config, "completion_seconds_pattern"), log_text)
    latest_session_pipeline_totals = _all_floats(_pattern(config, "completion_seconds_pattern"), latest_session)
    pipeline_total = latest_session_pipeline_totals[-1] if latest_session_pipeline_totals else (pipeline_totals[-1] if pipeline_totals else None)
    atlas_ctx = _atlas_context(latest_session, config)
    physical_atlas_phases = _latest_atlas_phase_profile(physical_latest_session, config)
    physical_atlas_state_payload = _latest_atlas_persistence_profile(physical_latest_session, config)
    atlas_totals = _all_floats(_pattern(config, "atlas_total_seconds_pattern"), log_text)
    latest_session_atlas_totals = _all_floats(_pattern(config, "atlas_total_seconds_pattern"), latest_session)
    atlas_total = latest_session_atlas_totals[-1] if latest_session_atlas_totals else atlas_ctx.get("total")
    atlas_projects = atlas_ctx.get("projects")
    atlas_files = atlas_ctx.get("files")
    pipeline_scaling = config.get("scaling", {}).get("pipeline", {}) if isinstance(config.get("scaling"), dict) else {}
    heavy_run_min_seconds = float(pipeline_scaling.get("heavy_run_min_seconds", 30.0) or 30.0) if isinstance(pipeline_scaling, dict) else 30.0
    latest_forced = str(_log_parsing(config).get("forced_marker") or "") in latest_session
    ignored_budget_modes = {
        str(item)
        for item in _session_policy(config).get("ignored_budget_modes", [])
        if str(item).strip()
    }
    budget_session_available = selected_session_mode not in {
        *ignored_budget_modes,
        _session_policy_value(config, "no_completed_budget_session"),
    }
    profile_pipeline_totals = _pipeline_samples_by_force_profile(log_text, latest_forced, config)
    heavy_pipeline_totals = [value for value in profile_pipeline_totals if value >= heavy_run_min_seconds]
    cached_pipeline_totals = [value for value in pipeline_totals if value < heavy_run_min_seconds]
    latest_heavy_pipeline_total = heavy_pipeline_totals[-1] if heavy_pipeline_totals else pipeline_total
    latest_cached_pipeline_total = cached_pipeline_totals[-1] if cached_pipeline_totals else None
    latest_release_deep = _latest_release_deep_session(log_text, config)
    latest_release_deep_session = latest_release_deep[0] if latest_release_deep else ""
    latest_release_deep_total = latest_release_deep[2] if latest_release_deep else None
    latest_release_deep_timestamps = _session_timestamps(latest_release_deep_session, config) if latest_release_deep else {"started_at": None, "last_event_at": None}
    release_deep_pipeline_totals = _pipeline_samples_by_mode(log_text, _session_policy_value(config, "release_deep_mode"), config)
    latest_forced_full = _latest_completed_forced_session(log_text, config)
    latest_forced_full_session = latest_forced_full[0] if latest_forced_full else ""
    latest_forced_full_total = latest_forced_full[2] if latest_forced_full else None
    latest_forced_full_timestamps = _session_timestamps(latest_forced_full_session, config) if latest_forced_full else {"started_at": None, "last_event_at": None}
    forced_full_pipeline_totals = _pipeline_samples_by_force_profile(log_text, True, config)
    selected_full_budget = max_forced_full_pipeline_total if latest_forced else max_full_pipeline_total
    effective_full_pipeline_budget = _effective_scaled_budget(config, "pipeline", selected_full_budget, atlas_projects, atlas_files)
    effective_release_deep_budget = _effective_scaled_budget(config, "pipeline", max_forced_full_pipeline_total, atlas_projects, atlas_files)
    effective_atlas_budget = _effective_scaled_budget(config, "atlas", max_atlas_total, atlas_projects, atlas_files)
    effective_fractal_budget = _effective_scaled_budget(config, "fractal", max_fractal_total, atlas_projects, atlas_files)
    fractal_total = _last_float(_pattern(config, "fractal_total_seconds_pattern"), latest_session)
    dead_code_step = _last_float(_pattern(config, "dead_code_step_seconds_pattern"), latest_session)
    dead_code_metric_source = "latest_pipeline_session"
    if dead_code_step is None:
        dead_code_artifact = load_json_file(RAW_DIR / "dead_code.json", {})
        dead_code_meta = dead_code_artifact.get("meta", {}) if isinstance(dead_code_artifact, dict) else {}
        try:
            dead_code_step = float(dead_code_meta.get("runtime_seconds"))
            dead_code_metric_source = "dead_code_artifact"
        except (TypeError, ValueError):
            dead_code_step = None
    workload_profile = load_json_file(RAW_DIR / "workload_profile.json", {})
    if not isinstance(workload_profile, dict) or not workload_profile:
        atlas = load_atlas_data()
        workload_profile = build_workload_profile(atlas if isinstance(atlas, dict) else {})
    effective_dead_code_budget = dead_code_budget_seconds(workload_profile, max_dead_code_step)
    config_contract_issues = _config_contract_issues(config)
    config_contract_check = {
        "name": "performance_log_contract_declared",
        "passed": not config_contract_issues,
        "details": ", ".join(config_contract_issues) or "log parsing and session policy contract declared",
    }

    if not budget_session_available:
        checks = [
            config_contract_check,
            {
                "name": "full_pipeline_total_budget",
                "passed": True,
                "details": f"latest completed pipeline session is `{selected_session_mode}`; release/full budget not evaluated",
                "policy": _session_policy_value(config, "non_release_skip_policy"),
            },
            {
                "name": "cached_pipeline_total_budget",
                "passed": True,
                "details": f"latest completed pipeline session is `{selected_session_mode}`; cached budget not evaluated",
                "policy": _session_policy_value(config, "non_release_skip_policy"),
            },
            {
                "name": "atlas_total_budget",
                "passed": True,
                "details": f"latest completed pipeline session is `{selected_session_mode}`; atlas release budget not evaluated",
                "policy": _session_policy_value(config, "non_release_skip_policy"),
            },
            {
                "name": "fractal_total_budget",
                "passed": True,
                "details": f"latest completed pipeline session is `{selected_session_mode}`; fractal release budget not evaluated",
                "policy": _session_policy_value(config, "non_release_skip_policy"),
            },
            {
                "name": "dead_code_step_budget",
                "passed": True,
                "details": f"latest completed pipeline session is `{selected_session_mode}`; dead-code release budget not evaluated",
                "policy": _session_policy_value(config, "non_release_skip_policy"),
            },
        ]
    else:
        checks = [
            config_contract_check,
            _rolling_check(
                "full_pipeline_total_budget",
                latest_heavy_pipeline_total,
                heavy_pipeline_totals,
                effective_full_pipeline_budget,
                config,
            ),
            _optional_check(
                "cached_pipeline_total_budget",
                latest_cached_pipeline_total,
                max_cached_pipeline_total,
                f"no cache-like sample below {heavy_run_min_seconds:.2f}s; full budget enforced",
            ),
            _optional_rolling_check(
                "atlas_total_budget",
                atlas_total,
                atlas_totals,
                effective_atlas_budget,
                _metric_rolling_config(config, "atlas_total_budget"),
                "atlas did not run in the latest pipeline session",
            ),
            _optional_check(
                "fractal_total_budget",
                fractal_total,
                effective_fractal_budget,
                "fractal mapping did not run in the latest pipeline session",
            ),
            _optional_check(
                "dead_code_step_budget",
                dead_code_step,
                effective_dead_code_budget,
                "dead code detector did not run in the latest pipeline session",
            ),
        ]
    checks.append(
        _optional_rolling_check(
            "release_deep_pipeline_total_budget",
            latest_release_deep_total,
            release_deep_pipeline_totals,
            effective_release_deep_budget,
            _metric_rolling_config(config, "release_deep_pipeline_total_budget"),
            "no completed release_deep session found; release-deep budget not evaluated",
        )
    )
    checks.append(
        _optional_rolling_check(
            "forced_full_pipeline_total_budget",
            latest_forced_full_total,
            forced_full_pipeline_totals,
            effective_release_deep_budget,
            _metric_rolling_config(config, "forced_full_pipeline_total_budget"),
            "no completed force/cache-bypass session found; forced full budget not evaluated",
        )
    )

    failed_checks = sum(1 for c in checks if not c.get("passed"))
    release_proof_refresh_required = bool(ignored_latest_session_mode and failed_checks)
    if not budget_session_available:
        performance_evidence_status = str(_session_policy(config).get("evidence_statuses", ["non_release_session_only"])[0])
    elif ignored_latest_session_mode:
        performance_evidence_status = str(_session_policy(config).get("evidence_statuses", ["", "stale_after_non_release_activity"])[1])
    else:
        performance_evidence_status = str(_session_policy(config).get("evidence_statuses", ["", "", "current_for_selected_budget_session"])[2])

    payload = {
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for c in checks if c.get("passed")),
            "failed_checks": failed_checks,
        },
        "metrics": {
            "pipeline_total_seconds": latest_heavy_pipeline_total,
            "pipeline_profile": _session_policy_value(config, "forced_profile_label") if latest_forced else selected_session_mode,
            "ignored_latest_session_mode": ignored_latest_session_mode,
            "physical_latest_session_mode": _session_mode(physical_latest_session, config),
            "selected_session_started_at": selected_session_timestamps.get("started_at"),
            "selected_session_last_event_at": selected_session_timestamps.get("last_event_at"),
            "physical_latest_session_started_at": physical_session_timestamps.get("started_at"),
            "physical_latest_session_last_event_at": physical_session_timestamps.get("last_event_at"),
            "performance_evidence_status": performance_evidence_status,
            "release_proof_refresh_required": release_proof_refresh_required,
            "latest_pipeline_total_seconds": pipeline_total,
            "latest_release_deep_total_seconds": latest_release_deep_total,
            "latest_release_deep_started_at": latest_release_deep_timestamps.get("started_at"),
            "latest_release_deep_last_event_at": latest_release_deep_timestamps.get("last_event_at"),
            "release_deep_budget_evaluated": latest_release_deep_total is not None,
            "latest_forced_full_total_seconds": latest_forced_full_total,
            "latest_forced_full_started_at": latest_forced_full_timestamps.get("started_at"),
            "latest_forced_full_last_event_at": latest_forced_full_timestamps.get("last_event_at"),
            "forced_full_budget_evaluated": latest_forced_full_total is not None,
            "cached_pipeline_total_seconds": latest_cached_pipeline_total,
            "pipeline_total_samples": pipeline_totals[-10:],
            "release_deep_pipeline_total_samples": release_deep_pipeline_totals[-10:],
            "forced_full_pipeline_total_samples": forced_full_pipeline_totals[-10:],
            "atlas_total_seconds": atlas_total,
            "physical_atlas_phase_timings": physical_atlas_phases,
            "physical_atlas_state_payload_profile": physical_atlas_state_payload,
            "atlas_projects": atlas_projects,
            "atlas_files": atlas_files,
            "fractal_total_seconds": fractal_total,
            "dead_code_step_seconds": dead_code_step,
            "dead_code_metric_source": dead_code_metric_source,
            "budget_session_available": budget_session_available,
            "workload_band": workload_profile.get("band"),
            "workload_counts": workload_profile.get("counts", {}),
        },
        "budgets": {
            "full_pipeline_total_seconds_base": max_full_pipeline_total,
            "forced_full_pipeline_total_seconds_base": max_forced_full_pipeline_total,
            "selected_pipeline_profile": _session_policy_value(config, "forced_profile_label") if latest_forced else selected_session_mode,
            "selected_session_mode": selected_session_mode,
            "ignored_latest_session_mode": ignored_latest_session_mode,
            "full_pipeline_total_seconds_effective": effective_full_pipeline_budget,
            "release_deep_pipeline_total_seconds_effective": effective_release_deep_budget,
            "forced_full_pipeline_total_seconds_effective": effective_release_deep_budget,
            "cached_pipeline_total_seconds": max_cached_pipeline_total,
            "atlas_total_seconds_base": max_atlas_total,
            "atlas_total_seconds_effective": effective_atlas_budget,
            "fractal_total_seconds_base": max_fractal_total,
            "fractal_total_seconds_effective": effective_fractal_budget,
            "dead_code_step_seconds_base": max_dead_code_step,
            "dead_code_step_seconds_effective": effective_dead_code_budget,
            "rolling_policy": config.get("rolling_policy", {}),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "performance_budget_validation.json", payload)

    ledger = load_json_file(RAW_DIR / "performance_ledger.json", {})
    ledger_rows = ledger.get("runs", []) if isinstance(ledger, dict) else []
    ledger_rows = [row for row in ledger_rows if isinstance(row, dict)]
    recent_failures = [
        row
        for row in ledger_rows[:25]
        if str(row.get("budget_status") or "PASS").upper() == "FAIL"
    ][:5]

    def _fmt_metric(value: Any) -> str:
        return "-" if value is None else str(value)

    lines = [
        "# Performance Budget Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        f"- Selected session: `{selected_session_mode}`",
        f"- Physical latest session: `{payload['metrics']['physical_latest_session_mode']}`",
        f"- Ignored latest session: `{ignored_latest_session_mode or ''}`",
        f"- Selected session started: `{payload['metrics']['selected_session_started_at'] or ''}`",
        f"- Selected session last event: `{payload['metrics']['selected_session_last_event_at'] or ''}`",
        f"- Physical latest session started: `{payload['metrics']['physical_latest_session_started_at'] or ''}`",
        f"- Physical latest session last event: `{payload['metrics']['physical_latest_session_last_event_at'] or ''}`",
        f"- Latest release-deep started: `{payload['metrics']['latest_release_deep_started_at'] or ''}`",
        f"- Latest release-deep last event: `{payload['metrics']['latest_release_deep_last_event_at'] or ''}`",
        f"- Release-deep budget evaluated: `{str(payload['metrics']['release_deep_budget_evaluated']).lower()}`",
        f"- Latest forced/full started: `{payload['metrics']['latest_forced_full_started_at'] or ''}`",
        f"- Latest forced/full last event: `{payload['metrics']['latest_forced_full_last_event_at'] or ''}`",
        f"- Forced/full budget evaluated: `{str(payload['metrics']['forced_full_budget_evaluated']).lower()}`",
        f"- Evidence status: `{payload['metrics']['performance_evidence_status']}`",
        f"- Release proof refresh required: `{str(payload['metrics']['release_proof_refresh_required']).lower()}`",
        f"- Budget session available: `{str(budget_session_available).lower()}`",
        f"- Workload band: `{payload['metrics']['workload_band']}`",
    ]
    if not budget_session_available:
        lines.append("- Diagnostic metrics below are from the selected non-release session; release/full budgets were not enforced for this run.")
    lines.extend(["", "| Check | Result | Details |", "|---|---|---|"])
    for check in checks:
        lines.append(
            f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |"
        )
    lines.extend(
        [
            "",
            "## Effective Budgets",
            "",
            "| Metric | Observed | Effective Budget |",
            "|---|---:|---:|",
            f"| Full pipeline | {_fmt_metric(payload['metrics']['pipeline_total_seconds'])} | {payload['budgets']['full_pipeline_total_seconds_effective']} |",
            f"| Release-deep pipeline | {_fmt_metric(payload['metrics']['latest_release_deep_total_seconds'])} | {payload['budgets']['release_deep_pipeline_total_seconds_effective']} |",
            f"| Forced/full pipeline | {_fmt_metric(payload['metrics']['latest_forced_full_total_seconds'])} | {payload['budgets']['forced_full_pipeline_total_seconds_effective']} |",
            f"| Cached pipeline | {_fmt_metric(payload['metrics']['cached_pipeline_total_seconds'])} | {payload['budgets']['cached_pipeline_total_seconds']} |",
            f"| Atlas | {_fmt_metric(payload['metrics']['atlas_total_seconds'])} | {payload['budgets']['atlas_total_seconds_effective']} |",
            f"| Fractal | {_fmt_metric(payload['metrics']['fractal_total_seconds'])} | {payload['budgets']['fractal_total_seconds_effective']} |",
            f"| Dead code | {_fmt_metric(payload['metrics']['dead_code_step_seconds'])} | {payload['budgets']['dead_code_step_seconds_effective']} |",
            "",
            "## Workload Counts",
            "",
            "| Dimension | Count |",
            "|---|---:|",
        ]
    )
    for key, value in (payload["metrics"].get("workload_counts") or {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## Recent Ledger Failures",
            "",
        ]
    )
    if recent_failures:
        lines.extend(
            [
                "| Run ID | Failed Checks | Notes |",
                "|---|---|---|",
            ]
        )
        for row in recent_failures:
            failed_names = row.get("failed_check_names", [])
            if isinstance(failed_names, list):
                failed_text = ", ".join(str(item) for item in failed_names)
            else:
                failed_text = str(failed_names or "")
            lines.append(
                "| {run_id} | {failed} | {notes} |".format(
                    run_id=str(row.get("run_id") or ""),
                    failed=failed_text or str(row.get("failed_checks") or ""),
                    notes=str(row.get("notes") or "").replace("\n", " ").strip(),
                )
            )
    else:
        lines.append("- No failed performance ledger rows in the latest 25 runs.")
    save_text_atomic(REPORTS_DIR / "performance_budget_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())



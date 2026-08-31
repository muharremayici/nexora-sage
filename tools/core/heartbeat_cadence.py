from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR
from tools.core.json_io import load_json_file, load_json_object_strict


_SAFE_BOOTSTRAP_SECONDS = 15
_POLICY_PATH = CONFIG_DIR / "pipeline_execution_policy.json"
_TELEMETRY_PATH = RAW_DIR / "telemetry_traces.json"


def heartbeat_cadence_contract() -> dict[str, Any]:
    """Load the machine-calibrated heartbeat policy from its single registry."""

    policy = load_json_object_strict(_POLICY_PATH, label="Pipeline execution policy")
    contract = policy.get("heartbeat_cadence")
    if not isinstance(contract, dict):
        raise ValueError("Pipeline execution policy is missing heartbeat_cadence.")
    return contract


def heartbeat_cadence_selection(scope: str = "default") -> dict[str, Any]:
    """Select a bounded local cadence without treating telemetry as repository truth."""

    try:
        contract = heartbeat_cadence_contract()
        bootstrap = max(1, int(contract["bootstrap_seconds"]))
        minimum = max(1, int(contract["minimum_seconds"]))
        maximum = max(minimum, int(contract["maximum_seconds"]))
        sample_floor = max(1, int(contract["minimum_samples"]))
        sample_limit = max(sample_floor, int(contract["max_samples"]))
        target_updates = max(1, int(contract["target_updates_per_observed_run"]))
        scope_types = contract.get("scope_trace_types")
        if not isinstance(scope_types, dict):
            raise ValueError("heartbeat_cadence.scope_trace_types must be an object.")
        accepted_types = scope_types.get(str(scope)) or scope_types.get("default")
        if not isinstance(accepted_types, list) or not accepted_types:
            raise ValueError(f"heartbeat_cadence has no trace types for scope '{scope}'.")
    except Exception as exc:
        return {
            "interval_seconds": _SAFE_BOOTSTRAP_SECONDS,
            "basis": "safe_bootstrap_policy_unavailable",
            "scope": str(scope),
            "sample_count": 0,
            "error": str(exc),
        }

    accepted_type_set = {str(item) for item in accepted_types}
    traces_payload = load_json_file(_TELEMETRY_PATH, {})
    traces = traces_payload.get("traces", []) if isinstance(traces_payload, dict) else []
    durations_ms = [
        int(trace.get("execution_ms") or 0)
        for trace in traces
        if isinstance(trace, dict)
        and str(trace.get("type") or "") in accepted_type_set
        and int(trace.get("execution_ms") or 0) > 0
    ][-sample_limit:]
    if len(durations_ms) < sample_floor or not bool(contract.get("enabled", True)):
        return {
            "interval_seconds": bootstrap,
            "basis": "bootstrap_insufficient_local_samples",
            "scope": str(scope),
            "sample_count": len(durations_ms),
            "minimum_samples": sample_floor,
            "accepted_trace_types": [str(item) for item in accepted_types],
        }

    median_seconds = float(median(durations_ms)) / 1000.0
    interval = min(maximum, max(minimum, round(median_seconds / target_updates)))
    return {
        "interval_seconds": int(interval),
        "basis": "local_median_duration_clamped",
        "scope": str(scope),
        "sample_count": len(durations_ms),
        "median_duration_seconds": round(median_seconds, 3),
        "minimum_seconds": minimum,
        "maximum_seconds": maximum,
        "target_updates_per_observed_run": target_updates,
        "accepted_trace_types": [str(item) for item in accepted_types],
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * min(1.0, max(0.0, float(percentile)))
    lower = int(rank)
    upper = min(len(ordered) - 1, lower + 1)
    if lower == upper:
        return round(ordered[lower], 3)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 3)


def local_duration_guidance(guidance_id: str) -> dict[str, Any]:
    """Summarize exact-identifier local timing without changing execution policy."""

    normalized = str(guidance_id or "").strip()
    policy = load_json_object_strict(_POLICY_PATH, label="Pipeline execution policy")
    contracts = policy.get("local_duration_guidance") if isinstance(policy, dict) else {}
    contract = contracts.get(normalized) if isinstance(contracts, dict) else None
    if not isinstance(contract, dict):
        raise ValueError(f"Unknown local duration guidance: {normalized}")
    phases = contract.get("phases")
    if not isinstance(phases, dict) or not phases:
        raise ValueError(f"Local duration guidance '{normalized}' has no phases.")
    trace_type = str(contract.get("trace_type") or "")
    telemetry_artifact = str(contract.get("telemetry_artifact") or "").strip()
    if not telemetry_artifact:
        raise ValueError(f"Local duration guidance '{normalized}' has no telemetry artifact.")
    telemetry_path = (CODE_MAPS_DIR / telemetry_artifact).resolve()
    try:
        telemetry_path.relative_to(CODE_MAPS_DIR.resolve())
    except ValueError as exc:
        raise ValueError(f"Local duration guidance '{normalized}' telemetry artifact is outside SAGE root.") from exc
    sample_limit = max(1, int(contract.get("sample_limit") or 1))
    minimum_samples = max(1, int(contract.get("minimum_samples_per_phase") or 1))
    traces_payload = load_json_file(telemetry_path, {})
    traces = traces_payload.get("traces", []) if isinstance(traces_payload, dict) else []
    phase_rows: dict[str, dict[str, Any]] = {}
    missing_phases: list[str] = []
    for phase_id, identifier in phases.items():
        durations = [
            float(trace.get("execution_ms") or 0) / 1000.0
            for trace in traces
            if isinstance(trace, dict)
            and str(trace.get("type") or "") == trace_type
            and str(trace.get("identifier") or "") == str(identifier)
            and float(trace.get("execution_ms") or 0) > 0
        ][-sample_limit:]
        available = len(durations) >= minimum_samples
        if not available:
            missing_phases.append(str(phase_id))
        phase_rows[str(phase_id)] = {
            "identifier": str(identifier),
            "sample_count": len(durations),
            "status": "available" if available else "unavailable",
            "p50_seconds": _percentile(durations, 0.50) if available else None,
            "p95_seconds": _percentile(durations, 0.95) if available else None,
        }
    status = "available" if not missing_phases else "unavailable"
    return {
        "status": status,
        "basis": "local_exact_phase_percentiles" if status == "available" else "insufficient_exact_phase_samples",
        "guidance_id": normalized,
        "telemetry_artifact": telemetry_artifact,
        "trace_type": trace_type,
        "sample_limit": sample_limit,
        "minimum_samples_per_phase": minimum_samples,
        "phases": phase_rows,
        "missing_phases": missing_phases,
        "claim_boundary": str(contract.get("claim_boundary") or ""),
    }


def record_execution_duration(trace_type: str, identifier: str, duration_seconds: float) -> None:
    """Record bounded runtime timing as local telemetry; analysis never depends on it."""

    try:
        from tools.engines.local_telemetry_engine import record_trace

        record_trace(
            str(trace_type),
            str(identifier),
            max(1, int(round(float(duration_seconds) * 1000))),
            trace_origin="sage_runtime",
        )
    except Exception:
        # Timing calibration is advisory only. It must never hide or replace the
        # operation it observes.
        return

from tools.core import heartbeat_cadence
from tools.core.operational_limits import pipeline_step_heartbeat_seconds


def test_heartbeat_cadence_bootstraps_before_local_samples(monkeypatch):
    monkeypatch.setattr(
        heartbeat_cadence,
        "heartbeat_cadence_contract",
        lambda: {
            "enabled": True,
            "bootstrap_seconds": 15,
            "minimum_seconds": 5,
            "maximum_seconds": 30,
            "minimum_samples": 3,
            "max_samples": 50,
            "target_updates_per_observed_run": 4,
            "scope_trace_types": {"watchdog": ["watchdog_pipeline_execution"], "default": ["watchdog_pipeline_execution"]},
        },
    )
    monkeypatch.setattr(heartbeat_cadence, "load_json_file", lambda *_args: {"traces": []})

    selection = heartbeat_cadence.heartbeat_cadence_selection("watchdog")

    assert selection["interval_seconds"] == 15
    assert selection["basis"] == "bootstrap_insufficient_local_samples"


def test_heartbeat_cadence_uses_same_scope_local_median(monkeypatch):
    monkeypatch.setattr(
        heartbeat_cadence,
        "heartbeat_cadence_contract",
        lambda: {
            "enabled": True,
            "bootstrap_seconds": 15,
            "minimum_seconds": 5,
            "maximum_seconds": 30,
            "minimum_samples": 3,
            "max_samples": 50,
            "target_updates_per_observed_run": 4,
            "scope_trace_types": {"watchdog": ["watchdog_pipeline_execution"], "default": ["watchdog_pipeline_execution"]},
        },
    )
    monkeypatch.setattr(
        heartbeat_cadence,
        "load_json_file",
        lambda *_args: {
            "traces": [
                {"type": "watchdog_pipeline_execution", "execution_ms": 80_000},
                {"type": "watchdog_pipeline_execution", "execution_ms": 100_000},
                {"type": "watchdog_pipeline_execution", "execution_ms": 120_000},
                {"type": "component", "execution_ms": 1},
            ]
        },
    )

    selection = heartbeat_cadence.heartbeat_cadence_selection("watchdog")

    assert selection["interval_seconds"] == 25
    assert selection["basis"] == "local_median_duration_clamped"
    assert selection["sample_count"] == 3


def test_operational_limit_delegates_to_machine_calibrated_selection(monkeypatch):
    monkeypatch.setattr(
        "tools.core.heartbeat_cadence.heartbeat_cadence_selection",
        lambda scope: {"interval_seconds": 9, "basis": "fixture", "scope": scope},
    )

    assert pipeline_step_heartbeat_seconds("watchdog") == 9


def test_local_duration_guidance_requires_every_exact_phase(monkeypatch):
    policy = {
        "local_duration_guidance": {
            "parity": {
                "telemetry_artifact": "output/.raw/telemetry_traces.json",
                "trace_type": "subprocess_execution",
                "sample_limit": 3,
                "minimum_samples_per_phase": 2,
                "phases": {"before": "before", "replay": "replay"},
                "claim_boundary": "advisory only",
            }
        }
    }
    monkeypatch.setattr(heartbeat_cadence, "load_json_object_strict", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(
        heartbeat_cadence,
        "load_json_file",
        lambda *_args: {
            "traces": [
                {"type": "subprocess_execution", "identifier": "before", "execution_ms": 10_000},
                {"type": "subprocess_execution", "identifier": "before", "execution_ms": 12_000},
                {"type": "subprocess_execution", "identifier": "other", "execution_ms": 1},
            ]
        },
    )

    guidance = heartbeat_cadence.local_duration_guidance("parity")

    assert guidance["status"] == "unavailable"
    assert guidance["telemetry_artifact"] == "output/.raw/telemetry_traces.json"
    assert guidance["missing_phases"] == ["replay"]
    assert guidance["phases"]["before"]["p50_seconds"] == 11.0
    assert guidance["phases"]["replay"]["p95_seconds"] is None


def test_local_duration_guidance_reports_exact_phase_percentiles(monkeypatch):
    policy = {
        "local_duration_guidance": {
            "parity": {
                "telemetry_artifact": "output/.raw/telemetry_traces.json",
                "trace_type": "subprocess_execution",
                "sample_limit": 3,
                "minimum_samples_per_phase": 2,
                "phases": {"before": "before", "replay": "replay"},
                "claim_boundary": "advisory only",
            }
        }
    }
    monkeypatch.setattr(heartbeat_cadence, "load_json_object_strict", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(
        heartbeat_cadence,
        "load_json_file",
        lambda *_args: {
            "traces": [
                {"type": "subprocess_execution", "identifier": "before", "execution_ms": 10_000},
                {"type": "subprocess_execution", "identifier": "before", "execution_ms": 12_000},
                {"type": "subprocess_execution", "identifier": "replay", "execution_ms": 100_000},
                {"type": "subprocess_execution", "identifier": "replay", "execution_ms": 140_000},
            ]
        },
    )

    guidance = heartbeat_cadence.local_duration_guidance("parity")

    assert guidance["status"] == "available"
    assert guidance["phases"]["replay"]["p50_seconds"] == 120.0
    assert guidance["phases"]["replay"]["p95_seconds"] == 138.0

from __future__ import annotations

from copy import deepcopy

from tools.validate_system_connectivity_map import validate_payload


def _payload() -> dict:
    return {
        "meta": {"kind": "system_connectivity_map"},
        "summary": {
            "status": "PASS",
            "critical_failures": [],
            "source_layers": 1,
            "pipeline_steps": 1,
            "spine_nodes": 1,
            "artifact_writers": 1,
            "artifact_consumers": 1,
            "external_input_artifacts": 1,
            "consumed_external_inputs": 1,
            "broader_consumed_external_inputs": 0,
            "declared_but_unconsumed_anywhere": 0,
            "writer_conflicts": 0,
            "layer_attention_items": 0,
            "spine_attention_items": 0,
            "unowned_consumed_artifacts": 0,
        },
        "connectivity": {
            "source_layers": [{"id": "layer"}],
            "spine_nodes": [{"id": "spine"}],
            "pipeline_steps": [{"id": "step"}],
            "pipeline_artifacts": {
                "writers": {"artifact": ["producer"]},
                "consumers": {"artifact": ["consumer"]},
                "writer_conflicts": {},
                "external_inputs": {"external": {}},
                "declared_but_unconsumed_anywhere": [],
                "invalid_broader_system_consumers": [],
                "unowned_consumed_artifacts": [],
            },
        },
        "attention": {
            "layers": [],
            "spine": [],
            "unowned_consumed_artifacts_sample": [],
            "consumed_external_inputs": ["external"],
            "broader_consumed_external_inputs": [],
            "declared_but_unconsumed_anywhere": [],
            "invalid_broader_system_consumers": [],
        },
    }


def _validate(payload: dict) -> list[dict]:
    return validate_payload(
        payload,
        expected_layer_ids={"layer"},
        expected_spine_ids={"spine"},
        expected_pipeline_step_ids={"step"},
        expected_external_input_ids={"external"},
    )


def test_valid_connectivity_payload_passes_all_checks() -> None:
    assert all(check["passed"] for check in _validate(_payload()))


def test_layer_attention_cannot_keep_pass_status() -> None:
    payload = deepcopy(_payload())
    payload["attention"]["layers"] = [{"layer": "layer", "warnings": {"missing_validators": ["x"]}}]
    payload["summary"]["layer_attention_items"] = 1
    checks = {check["name"]: check for check in _validate(payload)}
    assert not checks["critical_failures_cover_observed_unsafe_states"]["passed"]
    assert not checks["pass_requires_zero_critical_or_unsafe_state"]["passed"]


def test_writer_conflict_cannot_keep_pass_status() -> None:
    payload = deepcopy(_payload())
    payload["connectivity"]["pipeline_artifacts"]["writer_conflicts"] = {"artifact": ["a", "b"]}
    payload["summary"]["writer_conflicts"] = 1
    checks = {check["name"]: check for check in _validate(payload)}
    assert not checks["critical_failures_cover_observed_unsafe_states"]["passed"]


def test_reported_counts_must_match_observed_structures() -> None:
    payload = deepcopy(_payload())
    payload["summary"]["artifact_consumers"] = 2
    checks = {check["name"]: check for check in _validate(payload)}
    assert not checks["summary_counts_match_observed_structures"]["passed"]


def test_equal_pipeline_count_with_wrong_identity_cannot_pass() -> None:
    payload = deepcopy(_payload())
    payload["connectivity"]["pipeline_steps"] = [{"id": "wrong-step"}]
    checks = {check["name"]: check for check in _validate(payload)}
    assert not checks["pipeline_step_ids_match_registry"]["passed"]


def test_equal_external_input_count_with_wrong_identity_cannot_pass() -> None:
    payload = deepcopy(_payload())
    payload["attention"]["consumed_external_inputs"] = ["wrong-external"]
    checks = {check["name"]: check for check in _validate(payload)}
    assert not checks["external_input_ids_match_policy"]["passed"]

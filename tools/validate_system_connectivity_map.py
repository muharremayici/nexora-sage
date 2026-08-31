from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict


MAP_PATH = RAW_DIR / "system_connectivity_map.json"
RAW_PATH = RAW_DIR / "system_connectivity_map_validation.json"
REPORT_PATH = REPORTS_DIR / "system_connectivity_map_validation.md"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _ids(rows: list[dict[str, Any]], key: str) -> set[str]:
    return {str(row.get(key) or "") for row in rows if str(row.get(key) or "")}


def _derived_failure_ids(payload: dict[str, Any]) -> set[str]:
    attention = payload.get("attention") if isinstance(payload.get("attention"), dict) else {}
    connectivity = payload.get("connectivity") if isinstance(payload.get("connectivity"), dict) else {}
    artifacts = (
        connectivity.get("pipeline_artifacts")
        if isinstance(connectivity.get("pipeline_artifacts"), dict)
        else {}
    )
    failures: set[str] = set()
    if attention.get("layers"):
        failures.add("layer_missing_links")
    if attention.get("spine"):
        failures.add("spine_missing_links")
    if artifacts.get("writer_conflicts"):
        failures.add("artifact_writer_conflicts")
    if artifacts.get("unowned_consumed_artifacts"):
        failures.add("unowned_consumed_artifacts")
    if artifacts.get("declared_but_unconsumed_anywhere"):
        failures.add("declared_external_inputs_unconsumed_anywhere")
    if artifacts.get("invalid_broader_system_consumers"):
        failures.add("invalid_broader_system_consumers")
    return failures


def validate_payload(
    payload: dict[str, Any],
    *,
    expected_layer_ids: set[str],
    expected_spine_ids: set[str],
    expected_pipeline_step_ids: set[str],
    expected_external_input_ids: set[str],
) -> list[dict[str, Any]]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    connectivity = payload.get("connectivity") if isinstance(payload.get("connectivity"), dict) else {}
    attention = payload.get("attention") if isinstance(payload.get("attention"), dict) else {}
    layer_rows = _rows(connectivity.get("source_layers"))
    spine_rows = _rows(connectivity.get("spine_nodes"))
    pipeline_step_rows = _rows(connectivity.get("pipeline_steps"))
    artifacts = (
        connectivity.get("pipeline_artifacts")
        if isinstance(connectivity.get("pipeline_artifacts"), dict)
        else {}
    )
    actual_layer_ids = _ids(layer_rows, "id")
    actual_spine_ids = _ids(spine_rows, "id")
    actual_pipeline_step_ids = _ids(pipeline_step_rows, "id")
    declared_external_input_ids = {
        str(item)
        for key in (
            "consumed_external_inputs",
            "broader_consumed_external_inputs",
            "declared_but_unconsumed_anywhere",
        )
        for item in attention.get(key, []) or []
        if str(item)
    }
    critical_failures = {str(item) for item in summary.get("critical_failures", []) or []}
    derived_failures = _derived_failure_ids(payload)
    count_expectations = {
        "source_layers": len(layer_rows),
        "pipeline_steps": len(pipeline_step_rows),
        "spine_nodes": len(spine_rows),
        "artifact_writers": len(artifacts.get("writers", {}) or {}),
        "artifact_consumers": len(artifacts.get("consumers", {}) or {}),
        "external_input_artifacts": len(declared_external_input_ids),
        "consumed_external_inputs": len(attention.get("consumed_external_inputs", []) or []),
        "broader_consumed_external_inputs": len(attention.get("broader_consumed_external_inputs", []) or []),
        "declared_but_unconsumed_anywhere": len(artifacts.get("declared_but_unconsumed_anywhere", []) or []),
        "writer_conflicts": len(artifacts.get("writer_conflicts", {}) or {}),
        "layer_attention_items": len(attention.get("layers", []) or []),
        "spine_attention_items": len(attention.get("spine", []) or []),
        "unowned_consumed_artifacts": len(artifacts.get("unowned_consumed_artifacts", []) or []),
    }
    count_mismatches = {
        key: {"reported": summary.get(key), "observed": expected}
        for key, expected in count_expectations.items()
        if summary.get(key) != expected
    }
    return [
        _check(
            "map_has_expected_identity",
            payload.get("meta", {}).get("kind") == "system_connectivity_map",
            payload.get("meta", {}),
        ),
        _check(
            "source_layer_ids_match_release_matrix",
            actual_layer_ids == expected_layer_ids,
            {
                "missing": sorted(expected_layer_ids - actual_layer_ids),
                "extra": sorted(actual_layer_ids - expected_layer_ids),
            },
        ),
        _check(
            "spine_node_ids_match_registry",
            actual_spine_ids == expected_spine_ids,
            {
                "missing": sorted(expected_spine_ids - actual_spine_ids),
                "extra": sorted(actual_spine_ids - expected_spine_ids),
            },
        ),
        _check(
            "pipeline_step_ids_match_registry",
            actual_pipeline_step_ids == expected_pipeline_step_ids,
            {
                "missing": sorted(expected_pipeline_step_ids - actual_pipeline_step_ids),
                "extra": sorted(actual_pipeline_step_ids - expected_pipeline_step_ids),
            },
        ),
        _check(
            "external_input_ids_match_policy",
            declared_external_input_ids == expected_external_input_ids,
            {
                "missing": sorted(expected_external_input_ids - declared_external_input_ids),
                "extra": sorted(declared_external_input_ids - expected_external_input_ids),
            },
        ),
        _check("summary_counts_match_observed_structures", not count_mismatches, count_mismatches),
        _check(
            "critical_failures_cover_observed_unsafe_states",
            derived_failures <= critical_failures,
            {
                "missing_failure_ids": sorted(derived_failures - critical_failures),
                "derived_failure_ids": sorted(derived_failures),
                "reported_failure_ids": sorted(critical_failures),
            },
        ),
        _check(
            "pass_requires_zero_critical_or_unsafe_state",
            (summary.get("status") == "PASS") == (not critical_failures and not derived_failures),
            {
                "status": summary.get("status"),
                "critical_failure_ids": sorted(critical_failures),
                "derived_failure_ids": sorted(derived_failures),
            },
        ),
    ]


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# System Connectivity Map Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:900]
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{details}` |")
    return "\n".join(lines) + "\n"


def run_validation() -> dict[str, Any]:
    payload = load_json_file(MAP_PATH, {})
    layer_matrix = load_json_file(RAW_DIR / "layer_release_matrix.json", {})
    pipeline_registry = load_json_file(RAW_DIR / "pipeline_step_registry.json", {})
    spine_registry = load_json_object_strict(CONFIG_DIR / "system_spine_registry.json", label="System spine registry")
    pipeline_policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
    expected_layer_ids = _ids(_rows(layer_matrix.get("layers")), "layer")
    expected_spine_ids = _ids(_rows(spine_registry.get("spine_nodes")), "id")
    expected_pipeline_step_ids = _ids(_rows(pipeline_registry.get("steps")), "name")
    external_inputs = pipeline_policy.get("external_input_artifacts", {}).get("items", {})
    expected_external_input_ids = (
        {str(item) for item in external_inputs if str(item)} if isinstance(external_inputs, dict) else set()
    )
    checks = validate_payload(
        payload,
        expected_layer_ids=expected_layer_ids,
        expected_spine_ids=expected_spine_ids,
        expected_pipeline_step_ids=expected_pipeline_step_ids,
        expected_external_input_ids=expected_external_input_ids,
    )
    failed = [check for check in checks if not check["passed"]]
    result = {
        "meta": {"kind": "system_connectivity_map_validation", "version": "v1"},
        "source_evidence": {
            "artifact": "output/.raw/system_connectivity_map.json",
            "sha256": _sha256_file(MAP_PATH) if MAP_PATH.exists() else None,
        },
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_PATH, result)
    save_text_atomic(REPORT_PATH, _render_report(result))
    return result


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import fnmatch
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.pipeline_registry import (
    ARTIFACT_OWNERSHIP,
    catalog_args_from_execution_policy,
    load_pipeline_execution_policy,
)
from tools.orchestrators.orchestrator import build_step_catalog


CONTRACT_PATH = CODE_MAPS_DIR / "config" / "data_lineage_contract.json"
PIPELINE_POLICY_PATH = CODE_MAPS_DIR / "config" / "pipeline_execution_policy.json"
RAW_OUTPUT_PATH = RAW_DIR / "data_lineage_contract_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "data_lineage_contract_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _load_contract() -> dict[str, Any]:
    try:
        payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_pipeline_policy() -> dict[str, Any]:
    try:
        payload = json.loads(PIPELINE_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _check(name: str, passed: bool, details: Any, *, severity: str = "error") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "severity": severity,
        "details": details,
    }


def _catalog_step_names() -> set[str]:
    args = catalog_args_from_execution_policy(load_pipeline_execution_policy())
    return {str(step.get("name") or "") for step in build_step_catalog(args)}


def _artifact_sets(catalog_step_names: set[str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    producers: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}
    for step_name, ownership in ARTIFACT_OWNERSHIP.items():
        if step_name not in catalog_step_names:
            continue
        for artifact in ownership.get("writes", []) or []:
            producers.setdefault(str(artifact), []).append(str(step_name))
        for artifact in ownership.get("reads", []) or []:
            artifact_name = str(artifact)
            if artifact_name == "*":
                continue
            consumers.setdefault(artifact_name, []).append(str(step_name))
    return producers, consumers


def _matches_any_pattern(artifact: str, patterns: set[str]) -> bool:
    return any(fnmatch.fnmatch(artifact, pattern) for pattern in patterns)


def _marker_present(marker: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    rel = str(marker.get("file") or "")
    needle = str(marker.get("contains") or "")
    path = (CODE_MAPS_DIR / rel).resolve()
    try:
        path.relative_to(CODE_MAPS_DIR.resolve())
    except ValueError:
        return False, {"file": rel, "contains": needle, "error": "path escapes CODE_MAPS_DIR"}
    text = _read_text(path)
    return bool(path.exists() and needle and needle in text), {
        "file": rel,
        "contains": needle,
        "exists": path.exists(),
    }


def build_validation() -> dict[str, Any]:
    contract = _load_contract()
    pipeline_policy = _load_pipeline_policy()
    catalog_step_names = _catalog_step_names()
    ownership_step_names = {str(step_name) for step_name in ARTIFACT_OWNERSHIP}
    ownership_not_in_catalog = sorted(ownership_step_names - catalog_step_names)
    catalog_without_ownership = sorted(catalog_step_names - ownership_step_names)
    producers, consumers = _artifact_sets(catalog_step_names)
    produced = set(producers)
    consumed = set(consumers)
    external_inputs = set(contract.get("external_inputs", {}).get("artifacts", []) or [])
    pipeline_external_inputs = set(
        (
            pipeline_policy.get("external_input_artifacts", {}).get("items", {})
            if isinstance(pipeline_policy.get("external_input_artifacts"), dict)
            else {}
        ).keys()
    )
    terminal_payload = contract.get("terminal_outputs", {}) if isinstance(contract.get("terminal_outputs"), dict) else {}
    terminal_outputs = set(terminal_payload.get("artifacts", []) or [])
    terminal_classifications = (
        terminal_payload.get("classifications", {})
        if isinstance(terminal_payload.get("classifications"), dict)
        else {}
    )
    pattern_outputs = set(contract.get("pattern_outputs", {}).get("artifacts", []) or [])

    consumed_without_producer = sorted(
        artifact for artifact in consumed - produced
        if artifact not in external_inputs
    )
    produced_without_consumer = sorted(
        artifact for artifact in produced - consumed
        if artifact not in terminal_outputs and not _matches_any_pattern(artifact, pattern_outputs)
    )

    checks: list[dict[str, Any]] = [
        _check(
            "contract_file_exists",
            CONTRACT_PATH.exists() and bool(contract),
            {"path": CONTRACT_PATH.relative_to(CODE_MAPS_DIR).as_posix()},
        ),
        _check(
            "pipeline_artifact_ownership_matches_live_catalog",
            not ownership_not_in_catalog and not catalog_without_ownership,
            {
                "ownership_not_in_catalog": ownership_not_in_catalog,
                "catalog_without_ownership": catalog_without_ownership,
            },
        ),
        _check(
            "pipeline_consumers_have_known_producers_or_external_input",
            not consumed_without_producer,
            {
                "unclassified_consumed_without_producer": consumed_without_producer,
                "external_inputs": sorted(external_inputs),
            },
        ),
        _check(
            "pipeline_policy_external_inputs_match_data_lineage_contract",
            external_inputs == pipeline_external_inputs,
            {
                "data_lineage_only": sorted(external_inputs - pipeline_external_inputs),
                "pipeline_policy_only": sorted(pipeline_external_inputs - external_inputs),
                "external_inputs": sorted(external_inputs),
            },
        ),
        _check(
            "pipeline_producers_have_consumers_or_terminal_classification",
            not produced_without_consumer,
            {
                "unclassified_produced_without_consumer": produced_without_consumer,
                "terminal_outputs": sorted(terminal_outputs),
                "pattern_outputs": sorted(pattern_outputs),
            },
        ),
        _check(
            "terminal_outputs_have_per_artifact_classification",
            all(
                isinstance(terminal_classifications.get(artifact), dict)
                and bool(terminal_classifications.get(artifact, {}).get("category"))
                and bool(terminal_classifications.get(artifact, {}).get("reason"))
                for artifact in terminal_outputs
            ),
            {
                "missing_or_incomplete": sorted(
                    artifact
                    for artifact in terminal_outputs
                    if not (
                        isinstance(terminal_classifications.get(artifact), dict)
                        and bool(terminal_classifications.get(artifact, {}).get("category"))
                        and bool(terminal_classifications.get(artifact, {}).get("reason"))
                    )
                ),
                "classified": sorted(terminal_classifications),
            },
        ),
        _check(
            "terminal_output_classifications_do_not_name_unknown_artifacts",
            not (set(terminal_classifications) - terminal_outputs),
            {
                "unknown_classification_keys": sorted(set(terminal_classifications) - terminal_outputs),
            },
        ),
    ]

    field_results: list[dict[str, Any]] = []
    for field in contract.get("critical_field_lineage", []) or []:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("id") or "unknown")
        for group_name in ("producer_markers", "consumer_markers", "required_surface_markers"):
            markers = [row for row in field.get(group_name, []) or [] if isinstance(row, dict)]
            marker_results = []
            for marker in markers:
                present, evidence = _marker_present(marker)
                marker_results.append({"passed": present, **evidence})
            passed = bool(markers) and all(row.get("passed") for row in marker_results)
            field_results.append(
                {
                    "field": field_id,
                    "group": group_name,
                    "passed": passed,
                    "markers": marker_results,
                }
            )
            checks.append(
                _check(
                    f"{field_id}:{group_name}_present",
                    passed,
                    marker_results,
                )
            )

    failures = [row for row in checks if not row.get("passed") and row.get("severity") == "error"]
    warnings = [row for row in checks if not row.get("passed") and row.get("severity") == "warning"]
    return {
        "meta": {
            "kind": "data_lineage_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_data_lineage_contract",
        },
        "summary": {
            "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
            "checks": len(checks),
            "passed": sum(1 for row in checks if row.get("passed")),
            "failed": len(failures),
            "warnings": len(warnings),
            "produced_artifacts": len(produced),
            "consumed_artifacts": len(consumed),
        },
        "artifact_graph": {
            "consumed_without_producer": consumed_without_producer,
            "produced_without_consumer": produced_without_consumer,
            "terminal_classifications": terminal_classifications,
            "producer_count": {artifact: len(steps) for artifact, steps in sorted(producers.items())},
            "consumer_count": {artifact: len(steps) for artifact, steps in sorted(consumers.items())},
        },
        "field_lineage": field_results,
        "checks": checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    graph = payload.get("artifact_graph", {})
    lines = [
        "# Data Lineage Contract Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('checks')}`",
        f"- failed: `{summary.get('failed')}`",
        f"- produced_artifacts: `{summary.get('produced_artifacts')}`",
        f"- consumed_artifacts: `{summary.get('consumed_artifacts')}`",
        "",
        "## Artifact Graph",
        "",
        f"- consumed_without_producer: `{len(graph.get('consumed_without_producer', []) or [])}`",
        f"- produced_without_consumer: `{len(graph.get('produced_without_consumer', []) or [])}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Severity | Details |",
        "|---|---|---|---|",
    ]
    for check in payload.get("checks", []) or []:
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        details = details.replace("\n", " ")[:600]
        escaped_details = details.replace("|", "\\|")
        lines.append(
            f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | "
            f"`{check.get('severity')}` | {escaped_details} |"
        )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

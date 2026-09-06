from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ARTIFACT_PATHS, ARTIFACT_SCHEMAS
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.release_proof_steps import load_release_proof_steps


REGISTRY_PATH = CONFIG_DIR / "system_spine_registry.json"
RAW_PATH = RAW_DIR / "system_spine_registry_validation.json"
REPORT_PATH = REPORTS_DIR / "system_spine_registry_validation.md"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _validation_contract(registry: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    contract = registry.get("validation_contract") if isinstance(registry, dict) else None
    if not isinstance(contract, dict):
        return {}, ["validation_contract_missing"]
    errors: list[str] = []
    if not _string_list(contract.get("required_node_ids")):
        errors.append("required_node_ids_missing")
    return contract, sorted(errors)


def _artifact_id(raw_artifact: str) -> str:
    name = Path(raw_artifact).name
    return name[:-5] if name.endswith(".json") else Path(name).stem


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# System Spine Registry Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- spine_nodes: `{summary.get('spine_nodes')}`",
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
    registry = load_json_file(REGISTRY_PATH, {})
    meta = registry.get("_meta", {}) if isinstance(registry.get("_meta"), dict) else {}
    nodes = registry.get("spine_nodes", []) if isinstance(registry.get("spine_nodes"), list) else []
    validation_contract, contract_errors = _validation_contract(registry)
    required_node_ids = set(_string_list(validation_contract.get("required_node_ids")))
    required_roles = set(registry.get("required_roles", []) if isinstance(registry.get("required_roles"), list) else [])
    roles = {str(node.get("role") or "") for node in nodes if isinstance(node, dict)}
    node_ids = {str(node.get("id") or "") for node in nodes if isinstance(node, dict)}

    release_steps = {str(step.get("id") or ""): step for step in load_release_proof_steps()}
    duplicate_ids = sorted(
        node_id
        for node_id in {str(node.get("id") or "") for node in nodes if isinstance(node, dict)}
        if sum(1 for node in nodes if isinstance(node, dict) and str(node.get("id") or "") == node_id) > 1
    )
    missing_files: list[dict[str, str]] = []
    missing_release_steps: list[dict[str, str]] = []
    raw_mismatches: list[dict[str, str]] = []
    missing_artifact_registry: list[dict[str, str]] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        for key in ("source", "validator"):
            value = str(node.get(key) or "")
            if value and not (ROOT / value).exists():
                missing_files.append({"id": node_id, "field": key, "path": value})
        step_id = str(node.get("release_proof_step") or "")
        step = release_steps.get(step_id)
        if not step:
            missing_release_steps.append({"id": node_id, "release_proof_step": step_id})
            continue
        raw_artifact = str(node.get("raw_artifact") or "")
        expected_raw = step.get("raw_artifact")
        if raw_artifact:
            expected_name = Path(str(expected_raw)).name if expected_raw else ""
            actual_name = Path(raw_artifact).name
            if expected_name and actual_name != expected_name:
                raw_mismatches.append(
                    {"id": node_id, "release_proof_step": step_id, "registry_raw": raw_artifact, "step_raw": str(expected_raw)}
                )
            artifact_id = _artifact_id(raw_artifact)
            if artifact_id not in ARTIFACT_SCHEMAS or artifact_id not in ARTIFACT_PATHS:
                missing_artifact_registry.append({"id": node_id, "artifact_id": artifact_id, "raw_artifact": raw_artifact})

    checks = [
        _check(
            "validation_contract_present",
            not contract_errors,
            {"contract_errors": contract_errors, "validation_contract": validation_contract},
        ),
        _check(
            "registry_has_expected_kind_and_defaults",
            meta.get("kind") == "nexora.system_spine_registry"
            and registry.get("default_rules", {}).get("architecture") == "centralized_and_modular"
            and registry.get("default_rules", {}).get("no_god_file") is True,
            {"meta": meta, "default_rules": registry.get("default_rules", {})},
        ),
        _check(
            "required_roles_are_represented",
            bool(required_roles) and required_roles <= roles,
            {"required_roles": sorted(required_roles), "roles": sorted(roles)},
        ),
        _check(
            "required_spine_nodes_are_represented",
            bool(required_node_ids) and required_node_ids <= node_ids,
            {
                "missing_required_node_ids": sorted(required_node_ids - node_ids),
                "required_node_ids": sorted(required_node_ids),
            },
        ),
        _check(
            "spine_node_ids_are_unique",
            not duplicate_ids,
            {"duplicate_ids": duplicate_ids},
        ),
        _check(
            "spine_sources_and_validators_exist",
            not missing_files,
            {"missing_files": missing_files},
        ),
        _check(
            "spine_release_steps_exist",
            not missing_release_steps,
            {"missing_release_steps": missing_release_steps},
        ),
        _check(
            "spine_raw_artifacts_match_release_steps",
            not raw_mismatches,
            {"raw_mismatches": raw_mismatches},
        ),
        _check(
            "spine_artifacts_are_in_artifact_registry",
            not missing_artifact_registry,
            {"missing_artifact_registry": missing_artifact_registry},
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {"kind": "system_spine_registry_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "spine_nodes": len(nodes),
            "validation_contract_status": "ok" if not contract_errors else "invalid",
        },
        "checks": checks,
    }
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, _render_report(payload))
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

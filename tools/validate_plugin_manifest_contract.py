from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.capability_registry import capability_ids, load_capability_registry
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


MANIFEST_DIR = CONFIG_DIR / "plugin_manifests"
CONTRACT_PATH = CONFIG_DIR / "plugin_manifest_contract.json"
RAW_OUTPUT = RAW_DIR / "plugin_manifest_contract_validation.json"
REPORT_OUTPUT = REPORTS_DIR / "plugin_manifest_contract_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in value if str(item).strip()} if isinstance(value, list) else set()


def _load_contract() -> dict[str, Any]:
    payload = load_json_file(CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _load_manifests() -> list[tuple[Path, dict[str, Any]]]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(MANIFEST_DIR.glob("*.plugin.json")) if MANIFEST_DIR.exists() else []:
        payload = load_json_file(path, {})
        if isinstance(payload, dict):
            rows.append((path, payload))
    return rows


def build_validation() -> dict[str, Any]:
    contract = _load_contract()
    validation_contract = contract.get("validation_contract", {})
    validation_contract = validation_contract if isinstance(validation_contract, dict) else {}
    required_fields = _string_set(validation_contract.get("required_fields"))
    active_statuses = _string_set(validation_contract.get("active_statuses"))
    non_active_no_default_enable = _string_set(
        validation_contract.get("non_active_statuses_must_not_default_enable")
    )
    manifests = _load_manifests()
    known_capabilities = capability_ids(load_capability_registry())
    missing_fields = []
    unresolved_capabilities = []
    active_without_entrypoints = []
    unsafe_activation = []

    for path, manifest in manifests:
        missing = sorted(field for field in required_fields if field not in manifest)
        if missing:
            missing_fields.append({"path": path.name, "missing": missing})
        capability_id = str(manifest.get("capability_id") or "")
        if capability_id not in known_capabilities:
            unresolved_capabilities.append({"path": path.name, "capability_id": capability_id})
        status = str(manifest.get("status") or "")
        entrypoints = manifest.get("entrypoints") if isinstance(manifest.get("entrypoints"), dict) else {}
        engines = entrypoints.get("engines", []) if isinstance(entrypoints, dict) else []
        validators = entrypoints.get("validators", []) if isinstance(entrypoints, dict) else []
        if status in active_statuses and (not engines or not validators):
            active_without_entrypoints.append(path.name)
        activation = manifest.get("activation") if isinstance(manifest.get("activation"), dict) else {}
        if status in non_active_no_default_enable and activation.get("default_enabled") is True:
            unsafe_activation.append(path.name)

    checks = [
        _check(
            "plugin_manifest_validation_contract_exists",
            CONTRACT_PATH.exists() and bool(required_fields) and bool(active_statuses),
            {
                "path": str(CONTRACT_PATH),
                "required_fields": sorted(required_fields),
                "active_statuses": sorted(active_statuses),
                "non_active_statuses_must_not_default_enable": sorted(non_active_no_default_enable),
            },
        ),
        _check(
            "manifest_directory_exists",
            MANIFEST_DIR.exists(),
            {"path": str(MANIFEST_DIR)},
        ),
        _check(
            "at_least_one_manifest_template_exists",
            bool(manifests),
            {"manifests": [path.name for path, _ in manifests]},
        ),
        _check(
            "manifests_have_required_contract_fields",
            not missing_fields,
            {"missing_fields": missing_fields},
        ),
        _check(
            "manifest_capabilities_resolve_to_registry",
            not unresolved_capabilities,
            {"unresolved_capabilities": unresolved_capabilities},
        ),
        _check(
            "active_manifests_have_engines_and_validators",
            not active_without_entrypoints,
            {"active_without_entrypoints": active_without_entrypoints},
        ),
        _check(
            "non_active_manifests_do_not_default_enable",
            not unsafe_activation,
            {"unsafe_activation": unsafe_activation},
        ),
    ]
    payload = {
        "meta": {
            "kind": "plugin_manifest_contract_validation",
            "version": "v1",
            "generator": "tools.validate_plugin_manifest_contract",
            "generated_at": _utc_now(),
        },
        "summary": {
            "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
            "manifests": len(manifests),
            "contract_source": str(CONTRACT_PATH),
        },
        "checks": checks,
    }
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Plugin Manifest Contract Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- manifests: `{summary.get('manifests')}`",
        f"- passed: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | {escaped_details} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT, payload)
    save_text_atomic(REPORT_OUTPUT, render_report(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

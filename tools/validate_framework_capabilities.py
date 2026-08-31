from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR_FOR_IMPORT))

from tools.core.adapter_registry import load_adapter_registry
from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


CAPABILITIES_PATH = CONFIG_DIR / "framework_capabilities.json"
SCHEMA_PATH = CONFIG_DIR / "schemas" / "framework_capabilities.schema.json"
BOUNDARY_DOC_PATH = CODE_MAPS_DIR / "docs" / "FRAMEWORK_ADAPTER_BOUNDARY.md"

def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _contract_set(contract: dict[str, Any], field: str) -> set[str]:
    values = contract.get(field, [])
    return {str(item) for item in values if str(item).strip()} if isinstance(values, list) else set()


def _contract_list(contract: dict[str, Any], field: str) -> list[str]:
    values = contract.get(field, [])
    return [str(item) for item in values if str(item).strip()] if isinstance(values, list) else []


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    capabilities = _read_json(CAPABILITIES_PATH)
    ecosystems = capabilities.get("ecosystems", {}) if isinstance(capabilities.get("ecosystems"), dict) else {}
    validation_contract = (
        capabilities.get("validation_contract", {})
        if isinstance(capabilities.get("validation_contract"), dict)
        else {}
    )
    required_ecosystems = _contract_set(validation_contract, "required_ecosystems")
    required_manifest_fields = _contract_set(validation_contract, "required_manifest_owned_fields")
    required_code_fields = _contract_set(validation_contract, "required_code_owned_fields")

    try:
        ensure_against_schema(SCHEMA_PATH, "framework_capabilities", capabilities)
        schema_errors: list[str] = []
    except Exception as exc:
        schema_errors = [str(exc)]
    checks.append(_check("framework_capability_schema_valid", not schema_errors, schema_errors or "schema ok"))

    checks.append(
        _check(
            "framework_capability_validation_contract_declared",
            bool(required_ecosystems)
            and bool(required_manifest_fields)
            and bool(required_code_fields)
            and bool(_contract_list(validation_contract, "required_boundary_doc_phrases")),
            validation_contract,
        )
    )

    missing_ecosystems = sorted(required_ecosystems - set(ecosystems))
    checks.append(
        _check(
            "required_framework_ecosystems_declared",
            not missing_ecosystems,
            missing_ecosystems or "required framework ecosystems declared",
        )
    )

    adapter_payload = load_adapter_registry()
    adapter_frameworks = {
        str(framework)
        for adapter in adapter_payload.get("adapters", [])
        if isinstance(adapter, dict)
        for framework in adapter.get("frameworks", [])
    }
    missing_adapter_frameworks = sorted(adapter_frameworks - set(ecosystems))
    checks.append(
        _check(
            "adapter_frameworks_have_capability_manifests",
            not missing_adapter_frameworks,
            missing_adapter_frameworks or "all adapter framework ids have capability manifests",
        )
    )

    weak_entries: dict[str, list[str]] = {}
    for name, entry in ecosystems.items():
        if not isinstance(entry, dict):
            weak_entries[str(name)] = ["not_object"]
            continue
        missing = []
        if not entry.get("packages"):
            missing.append("packages")
        if not entry.get("evidence_categories"):
            missing.append("evidence_categories")
        if not entry.get("engines"):
            missing.append("engines")
        if missing:
            weak_entries[str(name)] = missing
    checks.append(
        _check(
            "framework_entries_have_claim_evidence_and_engine_links",
            not weak_entries,
            weak_entries or "all framework entries link packages, evidence categories and engines",
        )
    )

    boundary = capabilities.get("engine_boundary", {})
    manifest_owned = set(boundary.get("manifest_owned", [])) if isinstance(boundary, dict) else set()
    code_owned = set(boundary.get("code_owned", [])) if isinstance(boundary, dict) else set()
    checks.append(
        _check(
            "manifest_vs_code_boundary_is_explicit",
            required_manifest_fields.issubset(manifest_owned)
            and required_code_fields.issubset(code_owned),
            {"manifest_owned": sorted(manifest_owned), "code_owned": sorted(code_owned)},
        )
    )

    doc_text = _read(BOUNDARY_DOC_PATH)
    doc_required = _contract_list(validation_contract, "required_boundary_doc_phrases")
    missing_doc = [phrase for phrase in doc_required if phrase not in doc_text]
    checks.append(
        _check(
            "framework_boundary_doc_matches_policy",
            not missing_doc,
            missing_doc or "FRAMEWORK_ADAPTER_BOUNDARY.md documents the intended split",
        )
    )

    route_analyzer_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "framework_route_analyzer.py")
    required_manifest_function = str(validation_contract.get("route_detection_manifest_function") or "")
    forbidden_route_literals = _contract_list(validation_contract, "forbidden_route_detection_literals")
    checks.append(
        _check(
            "framework_route_detection_uses_capability_manifest",
            bool(required_manifest_function)
            and required_manifest_function in route_analyzer_text
            and not any(literal in route_analyzer_text for literal in forbidden_route_literals),
            "Framework config-file detection should be manifest-driven; route parsing remains code-owned behavior.",
        )
    )

    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check["passed"]),
        "failed_checks": sum(1 for check in checks if not check["passed"]),
    }
    payload = {
        "meta": {"kind": "framework_capability_validation", "version": "v1"},
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "framework_capability_validation.json", payload)

    lines = [
        "# Framework Capability Validation",
        "",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {escaped_details} |")
    save_text_atomic(REPORTS_DIR / "framework_capability_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

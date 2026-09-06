from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.doctrine_compiler import (
    COMPILED_DOCTRINE_PATH,
    MANIFEST_PATH,
    compile_doctrine_registry,
    doctrine_source_fingerprint,
    doctrine_without_compile_metadata,
    load_manifest,
)


RAW_OUTPUT_PATH = RAW_DIR / "modular_doctrine_registry_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "modular_doctrine_registry_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _pack_paths(manifest: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for entry in manifest.get("active_doctrines", []):
        if isinstance(entry, dict) and entry.get("path"):
            paths.append(MANIFEST_PATH.parent / str(entry["path"]))
    return paths


def build_validation() -> dict[str, Any]:
    manifest = load_manifest(MANIFEST_PATH) if MANIFEST_PATH.exists() else {}
    pack_paths = _pack_paths(manifest) if isinstance(manifest, dict) else []
    validation_contract = manifest.get("validation_contract", {}) if isinstance(manifest, dict) else {}
    minimum_active_packs = validation_contract.get("minimum_active_packs") if isinstance(validation_contract, dict) else None
    exclusive_contract_owners = (
        validation_contract.get("exclusive_contract_owners", {}) if isinstance(validation_contract, dict) else {}
    )
    runtime_doctrine = load_json_file(COMPILED_DOCTRINE_PATH, {})
    compiled = compile_doctrine_registry(MANIFEST_PATH) if MANIFEST_PATH.exists() else {}
    current_source_fingerprint = doctrine_source_fingerprint(MANIFEST_PATH) if MANIFEST_PATH.exists() else ""
    pack_payloads = [load_json_file(path, {}) for path in pack_paths if path.exists()]
    pack_namespaces = [
        ((payload.get("_meta") or {}).get("namespace") if isinstance(payload, dict) else None)
        for payload in pack_payloads
    ]
    duplicate_namespaces = sorted(
        namespace for namespace in set(pack_namespaces) if namespace and pack_namespaces.count(namespace) > 1
    )
    runtime_without_meta = doctrine_without_compile_metadata(runtime_doctrine if isinstance(runtime_doctrine, dict) else {})
    compiled_without_meta = doctrine_without_compile_metadata(compiled if isinstance(compiled, dict) else {})

    checks = [
        _check(
            "doctrine_manifest_exists_and_declares_active_packs",
            MANIFEST_PATH.exists()
            and isinstance(manifest.get("active_doctrines"), list)
            and isinstance(minimum_active_packs, int)
            and len(manifest.get("active_doctrines", [])) >= minimum_active_packs,
            {
                "manifest": str(MANIFEST_PATH.relative_to(ROOT)),
                "active_packs": len(manifest.get("active_doctrines", [])),
                "minimum_active_packs": minimum_active_packs,
            },
        ),
        _check(
            "all_required_doctrine_packs_exist",
            all(path.exists() for path in pack_paths),
            [str(path.relative_to(ROOT)) for path in pack_paths if not path.exists()],
        ),
        _check(
            "doctrine_packs_have_namespaced_metadata",
            bool(pack_payloads)
            and all(
                isinstance(payload, dict)
                and isinstance(payload.get("_meta"), dict)
                and payload["_meta"].get("kind") == "nexora.doctrine_pack"
                and payload["_meta"].get("namespace")
                for payload in pack_payloads
            )
            and not duplicate_namespaces,
            {"packs": len(pack_payloads), "duplicate_namespaces": duplicate_namespaces},
        ),
        _check(
            "exclusive_contract_owners_are_declared_and_compiler_enforced",
            isinstance(exclusive_contract_owners, dict)
            and bool(exclusive_contract_owners)
            and all(
                isinstance(contract_key, str)
                and bool(contract_key)
                and isinstance(owner_id, str)
                and owner_id
                in {
                    str(entry.get("id"))
                    for entry in manifest.get("active_doctrines", [])
                    if isinstance(entry, dict)
                }
                for contract_key, owner_id in exclusive_contract_owners.items()
            ),
            exclusive_contract_owners,
        ),
        _check(
            "compiled_doctrine_preserves_runtime_keys",
            set(compiled_without_meta.keys()) == set(runtime_without_meta.keys()),
            {
                "missing_in_compiled": sorted(set(runtime_without_meta.keys()) - set(compiled_without_meta.keys())),
                "extra_in_compiled": sorted(set(compiled_without_meta.keys()) - set(runtime_without_meta.keys())),
            },
        ),
        _check(
            "compiled_doctrine_payload_matches_runtime_doctrine",
            compiled_without_meta == runtime_without_meta,
            "Compiler output should match config/architecture_doctrine.json except compile metadata.",
        ),
        _check(
            "compiled_doctrine_declares_registry_provenance",
            isinstance(runtime_doctrine.get("_meta"), dict)
            and runtime_doctrine["_meta"].get("compiled") is True
            and runtime_doctrine["_meta"].get("compiled_from_manifest") == "config/doctrines/manifest.json"
            and isinstance(minimum_active_packs, int)
            and runtime_doctrine["_meta"].get("compiled_pack_count", 0) >= minimum_active_packs,
            runtime_doctrine.get("_meta", {}),
        ),
        _check(
            "compiled_doctrine_source_fingerprint_matches_active_packs",
            isinstance(runtime_doctrine.get("_meta"), dict)
            and runtime_doctrine["_meta"].get("source_fingerprint")
            == current_source_fingerprint,
            {
                "compiled": (runtime_doctrine.get("_meta") or {}).get("source_fingerprint"),
                "current": current_source_fingerprint,
            },
        ),
    ]

    summary = {
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check["passed"]),
        "failed_checks": sum(1 for check in checks if not check["passed"]),
        "active_packs": len(pack_paths),
        "generated_at": _utc_now(),
    }
    return {
        "meta": {
            "kind": "modular_doctrine_registry_validation",
            "version": "v1",
            "generator": "tools.validate_modular_doctrine_registry",
        },
        "summary": summary,
        "checks": checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Modular Doctrine Registry Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- active_packs: `{summary.get('active_packs')}`",
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
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

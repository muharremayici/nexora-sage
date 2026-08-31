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
from tools.core.principle_packs import (
    PRINCIPLE_MANIFEST_FILE,
    build_principle_pack_summary,
    load_principle_manifest,
    load_principle_packs,
    principle_contract,
    principle_pack_paths,
    render_principle_pack_report,
    validate_principle_pack,
)


RAW_OUTPUT_PATH = RAW_DIR / "principle_pack_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "principle_pack_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def build_validation() -> dict[str, Any]:
    manifest = load_principle_manifest()
    paths = principle_pack_paths(manifest)
    packs = load_principle_packs(manifest)
    contract = principle_contract(manifest)
    manifest_meta = manifest.get("_meta", {}) if isinstance(manifest, dict) else {}
    pack_errors: dict[str, list[str]] = {}
    namespaces: list[str] = []
    principle_ids: list[str] = []

    for path, pack in zip(paths, packs):
        errors = validate_principle_pack(pack, path=path, manifest=manifest)
        if errors:
            pack_errors[str(path.relative_to(ROOT).as_posix())] = errors
        meta = pack.get("_meta", {}) if isinstance(pack, dict) else {}
        namespace = str(meta.get("namespace") or "")
        if namespace:
            namespaces.append(namespace)
        for principle in pack.get("principles", []) if isinstance(pack, dict) else []:
            if isinstance(principle, dict) and principle.get("id"):
                principle_ids.append(str(principle["id"]))

    duplicate_namespaces = sorted(item for item in set(namespaces) if namespaces.count(item) > 1)
    duplicate_principles = sorted(item for item in set(principle_ids) if principle_ids.count(item) > 1)
    active_entries = manifest.get("active_principles", []) if isinstance(manifest, dict) else []
    checks = [
        _check(
            "principle_manifest_exists_and_is_advisory",
            PRINCIPLE_MANIFEST_FILE.exists()
            and manifest_meta.get("kind") == "nexora.principle_manifest"
            and contract.get("status") == "advisory_only"
            and contract.get("quality_gate_effect") == "none"
            and contract.get("doctrine_compilation") == "excluded",
            {
                "manifest": str(PRINCIPLE_MANIFEST_FILE.relative_to(ROOT).as_posix()),
                "meta": manifest_meta,
                "contract": contract,
            },
        ),
        _check(
            "all_active_principle_packs_exist",
            bool(paths) and all(path.exists() for path in paths),
            [str(path.relative_to(ROOT).as_posix()) for path in paths if not path.exists()],
        ),
        _check(
            "principle_packs_are_valid_and_unique",
            bool(packs) and not pack_errors and not duplicate_namespaces and not duplicate_principles,
            {
                "pack_errors": pack_errors,
                "duplicate_namespaces": duplicate_namespaces,
                "duplicate_principle_ids": duplicate_principles,
            },
        ),
        _check(
            "active_principles_are_not_doctrine_manifest_entries",
            isinstance(contract.get("active_path_prefixes"), list)
            and bool(contract.get("active_path_prefixes"))
            and all(
                any(str(entry.get("path", "")).startswith(str(prefix)) for prefix in contract.get("active_path_prefixes", []))
                for entry in active_entries
                if isinstance(entry, dict)
            ),
            "Principle packs are loaded from config/principles and must not be listed in config/doctrines/manifest.json.",
        ),
    ]
    summary = build_principle_pack_summary(manifest)
    summary.update(
        {
            "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
            "generated_at": _utc_now(),
        }
    )
    return {
        "meta": {
            "kind": "principle_pack_validation",
            "version": "v1",
            "generator": "tools.validate_principle_packs",
        },
        "summary": summary,
        "checks": checks,
    }


def main() -> int:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_principle_pack_report(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

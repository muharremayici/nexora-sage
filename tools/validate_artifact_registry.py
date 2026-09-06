"""Validate the canonical declarative artifact registry and its shared views."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_registry import (
    ARTIFACT_METADATA,
    ARTIFACT_PATHS,
    ARTIFACT_SCHEMAS,
    REGISTRY_PATH,
    load_artifact_registry,
    mandatory_artifact_ids,
)
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


RAW_PATH = RAW_DIR / "artifact_registry_validation.json"
REPORT_PATH = REPORTS_DIR / "artifact_registry_validation.md"


def _check(check_id: str, ok: bool, details: Any = None) -> dict[str, Any]:
    row = {"id": check_id, "ok": bool(ok)}
    if details is not None:
        row["details"] = details
    return row


def run() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        registry = load_artifact_registry()
        rows = registry["artifacts"]
        ids = [row["id"] for row in rows]
        storage_roots = registry["path_contract"]["storage_roots"]
        storage_rows = {
            storage_class: [row for row in rows if row["storage_class"] == storage_class]
            for storage_class in storage_roots
        }
        mandatory = mandatory_artifact_ids()
        checks.extend(
            [
                _check("registry_exists_and_is_canonical_json", REGISTRY_PATH.is_file(), REGISTRY_PATH.as_posix()),
                _check("artifact_ids_are_unique", len(ids) == len(set(ids)), {"count": len(ids)}),
                _check("shared_schema_and_path_views_cover_every_artifact", set(ids) == set(ARTIFACT_SCHEMAS) == set(ARTIFACT_PATHS) == set(ARTIFACT_METADATA)),
                _check("all_declared_schema_files_exist", all(path.is_file() for path in ARTIFACT_SCHEMAS.values())),
                _check(
                    "storage_classes_match_paths",
                    all(
                        row["path"].startswith(str(root))
                        for storage_class, root in storage_roots.items()
                        for row in storage_rows[storage_class]
                    ),
                    {storage_class: len(group) for storage_class, group in storage_rows.items()},
                ),
                _check("mandatory_artifacts_are_explicit", {"atlas", "genome"} <= mandatory, {"mandatory": sorted(mandatory)}),
                _check("registry_validation_artifact_is_self_described", "artifact_registry_validation" in ARTIFACT_PATHS and "artifact_registry_validation" in ARTIFACT_SCHEMAS),
                _check("self_validation_output_is_not_a_global_prerequisite", "artifact_registry_validation" not in mandatory, {"mandatory": sorted(mandatory)}),
            ]
        )
    except Exception as exc:
        checks.append(_check("registry_readable_and_fail_closed", False, str(exc)))
    failed = [row["id"] for row in checks if not row["ok"]]
    return {
        "meta": {"kind": "artifact_registry_validation", "version": "v1"},
        "summary": {"status": "PASS" if not failed else "FAIL", "total_checks": len(checks), "passed_checks": len(checks) - len(failed), "failed_checks": failed},
        "checks": checks,
    }


def main() -> int:
    payload = run()
    save_json_atomic(RAW_PATH, payload)
    lines = ["# Artifact Registry Validation", ""]
    lines.extend(f"- {'PASS' if row['ok'] else 'FAIL'}: `{row['id']}`" for row in payload["checks"])
    save_text_atomic(REPORT_PATH, "\n".join(lines) + "\n")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ARTIFACT_PATHS, ARTIFACT_SCHEMAS
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.release_proof_steps import load_release_proof_steps


RAW_PATH = RAW_DIR / "release_artifact_registry_alignment_validation.json"
REPORT_PATH = REPORTS_DIR / "release_artifact_registry_alignment_validation.md"


def _artifact_id(path: Path) -> str:
    name = path.name
    return name[:-5] if name.endswith(".json") else path.stem


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Release Artifact Registry Alignment Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- release_steps_with_raw_artifacts: `{summary.get('release_steps_with_raw_artifacts')}`",
        f"- missing_schema_entries: `{summary.get('missing_schema_entries')}`",
        f"- missing_path_entries: `{summary.get('missing_path_entries')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:900]
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{details}` |")
    return "\n".join(lines) + "\n"


def run_validation() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for step in load_release_proof_steps():
        raw_artifact = step.get("raw_artifact")
        if not isinstance(raw_artifact, Path):
            continue
        artifact_id = _artifact_id(raw_artifact)
        rows.append(
            {
                "step_id": step.get("id"),
                "artifact_id": artifact_id,
                "raw_artifact": raw_artifact.as_posix(),
                "has_schema": artifact_id in ARTIFACT_SCHEMAS,
                "has_path": artifact_id in ARTIFACT_PATHS,
            }
        )

    missing_schema = [row for row in rows if not row["has_schema"]]
    missing_path = [row for row in rows if not row["has_path"]]
    checks = [
        _check(
            "release_raw_artifact_inventory_is_non_empty",
            bool(rows),
            {"release_steps_with_raw_artifacts": len(rows)},
        ),
        _check(
            "release_raw_artifacts_have_schema_entries",
            not missing_schema,
            {"missing": missing_schema},
        ),
        _check(
            "release_raw_artifacts_have_path_entries",
            not missing_path,
            {"missing": missing_path},
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {"kind": "release_artifact_registry_alignment_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "release_steps_with_raw_artifacts": len(rows),
            "missing_schema_entries": len(missing_schema),
            "missing_path_entries": len(missing_path),
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

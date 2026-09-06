from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, SCHEMAS_DIR, save_json_atomic, save_text_atomic
from tools.core.artifact_validator import ensure_against_schema, load_json, ArtifactValidationError
from tools.core.proof_envelope_policy import get_proof_envelope_policy, iter_policy_numeric_paths

RAW_OUTPUT_PATH = RAW_DIR / "l4_policy_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "l4_policy_validation.md"
CONFIG_PATH = CONFIG_DIR / "l4_proof_envelope.json"
SCHEMA_PATH = SCHEMAS_DIR / "l4_proof_envelope.schema.json"


def _check(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _path_get(data: dict, path: str):
    cursor = data
    for key in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    checks.append(_check("l4_policy:config_exists", CONFIG_PATH.exists(), f"path={CONFIG_PATH.name}"))
    checks.append(_check("l4_policy:schema_exists", SCHEMA_PATH.exists(), f"path={SCHEMA_PATH.name}"))

    config_payload = load_json(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    schema_ok = False
    schema_error = ""
    if CONFIG_PATH.exists() and SCHEMA_PATH.exists():
        try:
            ensure_against_schema(SCHEMA_PATH, "l4_proof_envelope", config_payload)
            schema_ok = True
        except ArtifactValidationError as exc:
            schema_error = str(exc)
    checks.append(_check("l4_policy:schema_valid", schema_ok, schema_error or "ok"))

    effective_policy = get_proof_envelope_policy()
    checks.append(
        _check(
            "l4_policy:effective_has_required_roots",
            isinstance(effective_policy.get("proof_obligations"), dict)
            and isinstance(effective_policy.get("quality_review"), dict),
            "proof_obligations and quality_review roots present",
        )
    )

    numeric_paths = iter_policy_numeric_paths(effective_policy)
    missing_numeric = [path for path in numeric_paths if not isinstance(_path_get(effective_policy, path), (int, float))]
    checks.append(
        _check(
            "l4_policy:effective_numeric_completeness",
            len(missing_numeric) == 0,
            "missing=" + ", ".join(missing_numeric[:10]) if missing_numeric else "all numeric thresholds resolved",
        )
    )

    adjusted_hits = []
    if isinstance(config_payload, dict):
        for path in numeric_paths:
            configured = _path_get(config_payload, path)
            effective = _path_get(effective_policy, path)
            if configured is not None and effective != configured:
                adjusted_hits.append(path)
    checks.append(
        _check(
            "l4_policy:no_unexpected_adjustment",
            len(adjusted_hits) == 0,
            "adjusted=" + ", ".join(adjusted_hits[:10]) if adjusted_hits else "no policy sanitation adjustments",
        )
    )

    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for row in checks if row["passed"]),
        "failed_checks": sum(1 for row in checks if not row["passed"]),
    }
    payload = {
        "meta": {"kind": "l4_policy_validation", "version": "v1"},
        "summary": summary,
        "checks": checks,
    }

    lines = [
        "# L4 Policy Validation",
        "",
        f"- Total checks: **{summary['total_checks']}**",
        f"- Passed: **{summary['passed_checks']}**",
        f"- Failed: **{summary['failed_checks']}**",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
    ]
    for row in checks:
        lines.append(f"| `{row['name']}` | {'PASS' if row['passed'] else 'FAIL'} | {row['details']} |")

    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic

CODEMAPS_PATH = CODE_MAPS_DIR / "codemaps.py"


def _check(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_validation() -> dict[str, Any]:
    source = CODEMAPS_PATH.read_text(encoding="utf-8", errors="replace") if CODEMAPS_PATH.exists() else ""

    checks = [
        _check(
            "validate_has_auto_remediate_flag",
            "--auto-remediate-stale" in source,
            "codemaps validate parser",
        ),
        _check(
            "validate_refresh_chain_has_qualitygates",
            '"--step", "qualitygates"' in source,
            "refresh dependency chain includes qualitygates",
        ),
        _check(
            "validate_refresh_chain_has_aicontext",
            '"--step", "aicontext"' in source,
            "refresh dependency chain includes aicontext",
        ),
        _check(
            "validate_retry_block_exists",
            "Validator failure detected; running stale remediation refresh and retry once..." in source
            and "auto_remediate_stale" in source,
            "retry-on-failure block present",
        ),
        _check(
            "doctor_uses_auto_remediate",
            "auto_remediate_stale=True" in source,
            "doctor include-validate path sets auto remediation",
        ),
    ]

    payload = {
        "meta": {"kind": "stale_remediation_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for c in checks if c.get("passed")),
            "failed_checks": sum(1 for c in checks if not c.get("passed")),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "stale_remediation_validation.json", payload)

    lines = [
        "# Stale Remediation Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(
            f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |"
        )
    save_text_atomic(REPORTS_DIR / "stale_remediation_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

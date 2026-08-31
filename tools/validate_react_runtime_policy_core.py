from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


ENGINE_PATH = CODE_MAPS_DIR / "tools" / "engines" / "react_runtime_intelligence.py"
POLICY_PATH = CONFIG_DIR / "react_runtime_policy.json"


def _check(name: str, passed: bool, details: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "details": details,
        "evidence": evidence,
    }


def run_validation() -> dict[str, Any]:
    engine_text = ENGINE_PATH.read_text(encoding="utf-8", errors="replace")
    policy_text = POLICY_PATH.read_text(encoding="utf-8", errors="replace") if POLICY_PATH.exists() else ""
    policy = load_json_file(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}

    runtime_surfaces = policy.get("runtime_import_surfaces", {})
    reference_surfaces = policy.get("reference_surfaces", {})
    validation_contract = policy.get("validation_contract", {})
    policy_owned_tokens = validation_contract.get("required_policy_tokens", [])
    engine_hits = [token for token in policy_owned_tokens if token in engine_text]
    policy_missing = [token for token in policy_owned_tokens if token not in policy_text]

    checks = [
        _check(
            "react_runtime_engine_has_no_policy_owned_tokens",
            not engine_hits,
            "Runtime import/reference-surface tokens should live in config/react_runtime_policy.json.",
            engine_hits,
        ),
        _check(
            "react_runtime_policy_contains_migrated_tokens",
            isinstance(policy_owned_tokens, list) and len(policy_owned_tokens) > 0 and not policy_missing,
            "Migrated runtime policy tokens should remain explicit and reviewable in the policy file.",
            policy_missing,
        ),
        _check(
            "react_runtime_import_surfaces_are_configured",
            isinstance(runtime_surfaces, dict)
            and isinstance(runtime_surfaces.get("heavy_client_imports"), list)
            and isinstance(runtime_surfaces.get("server_only_imports"), list)
            and len(runtime_surfaces.get("heavy_client_imports") or []) > 0
            and len(runtime_surfaces.get("server_only_imports") or []) > 0,
            "Bundle and server-only import recognizers must be policy-backed.",
            runtime_surfaces,
        ),
        _check(
            "react_runtime_reference_surfaces_are_configured",
            isinstance(reference_surfaces, dict)
            and isinstance(reference_surfaces.get("path_markers"), list)
            and isinstance(reference_surfaces.get("fixture_path_markers"), list)
            and isinstance(reference_surfaces.get("fixture_file_suffixes"), list)
            and len(reference_surfaces.get("path_markers") or []) > 0
            and len(reference_surfaces.get("fixture_path_markers") or []) > 0
            and len(reference_surfaces.get("fixture_file_suffixes") or []) > 0,
            "Reference/demo/test-fixture calibration must be policy-backed.",
            reference_surfaces,
        ),
    ]

    payload = {
        "meta": {"kind": "react_runtime_policy_core_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "react_runtime_policy_core_validation.json", payload)

    lines = [
        "# React Runtime Policy Core Validation",
        "",
        f"- total_checks: `{payload['summary']['total_checks']}`",
        f"- passed_checks: `{payload['summary']['passed_checks']}`",
        f"- failed_checks: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |")
    save_text_atomic(REPORTS_DIR / "react_runtime_policy_core_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

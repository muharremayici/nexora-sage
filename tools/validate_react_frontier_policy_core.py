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


ENGINE_PATH = CODE_MAPS_DIR / "tools" / "engines" / "react_frontier_intelligence.py"
POLICY_PATH = CONFIG_DIR / "react_frontier_policy.json"


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
    artifacts = policy.get("evidence_artifacts", {})
    gates = policy.get("evidence_gates", {})
    validation_contract = policy.get("validation_contract", {})
    policy_owned_tokens = validation_contract.get("required_policy_tokens", [])

    engine_hits = [token for token in policy_owned_tokens if token in engine_text]
    policy_missing = [token for token in policy_owned_tokens if token not in policy_text]

    checks = [
        _check(
            "react_frontier_engine_has_no_policy_owned_artifact_tokens",
            not engine_hits,
            "Bundle/profiler artifact names and framework build locations should live in config/react_frontier_policy.json.",
            engine_hits,
        ),
        _check(
            "react_frontier_policy_contains_migrated_tokens",
            isinstance(policy_owned_tokens, list) and len(policy_owned_tokens) > 0 and not policy_missing,
            "Migrated frontier artifact tokens should remain explicit and reviewable in policy.",
            policy_missing,
        ),
        _check(
            "react_frontier_evidence_artifacts_are_configured",
            isinstance(artifacts, dict)
            and isinstance(artifacts.get("bundle_stats_names"), list)
            and isinstance(artifacts.get("react_profiler_names"), list)
            and isinstance(artifacts.get("recommended_locations"), list)
            and isinstance(artifacts.get("json_scan_keys"), list)
            and len(artifacts.get("bundle_stats_names") or []) > 0
            and len(artifacts.get("react_profiler_names") or []) > 0
            and len(artifacts.get("recommended_locations") or []) > 0
            and len(artifacts.get("json_scan_keys") or []) > 0,
            "Frontier evidence artifact discovery must be policy-backed.",
            artifacts,
        ),
        _check(
            "react_frontier_evidence_gates_are_configured",
            isinstance(gates, dict)
            and isinstance(gates.get("runtime_or_browser_dimensions"), list)
            and isinstance(gates.get("hydration_dimensions"), list)
            and isinstance(gates.get("runtime_gate"), str)
            and isinstance(gates.get("hydration_gate"), str),
            "Frontier refactor verification gates must be policy-backed.",
            gates,
        ),
    ]

    payload = {
        "meta": {"kind": "react_frontier_policy_core_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "react_frontier_policy_core_validation.json", payload)

    lines = [
        "# React Frontier Policy Core Validation",
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
    save_text_atomic(REPORTS_DIR / "react_frontier_policy_core_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

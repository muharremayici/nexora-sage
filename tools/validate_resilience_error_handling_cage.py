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
from tools.engines.resilience_error_handling_cage import analyze_resilience_file


ENGINE_PATH = CODE_MAPS_DIR / "tools" / "engines" / "resilience_error_handling_cage.py"
POLICY_PATH = CONFIG_DIR / "resilience_error_handling_policy.json"


def _check(name: str, passed: bool, details: str, evidence: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details, "evidence": evidence}


def _load_policy() -> dict[str, Any]:
    policy = load_json_file(POLICY_PATH, {})
    return policy if isinstance(policy, dict) else {}


def _findings_for(sample: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    result = analyze_resilience_file("FIXTURE", "src/api.ts", sample, policy) or {}
    findings = result.get("findings", []) if isinstance(result, dict) else []
    return [item for item in findings if isinstance(item, dict)]


def run_validation() -> dict[str, Any]:
    policy = _load_policy()
    engine_text = ENGINE_PATH.read_text(encoding="utf-8", errors="replace")
    policy_owned_tokens = sorted(
        {
            str(item)
            for key in ["external_call_markers", "required_contract_markers", "safe_wrapper_markers"]
            for item in (policy.get(key, []) if isinstance(policy.get(key), list) else [])
            if str(item).strip()
        }
    )
    engine_token_hits = [
        token for token in policy_owned_tokens
        if f'"{token}"' in engine_text or f"'{token}'" in engine_text
    ]

    naked_sample = """
export async function loadUser(id) {
  const response = await fetch(`/api/users/${id}`);
  return response.json();
}
"""
    try_catch_sample = """
export async function loadUser(id) {
  try {
    const response = await fetch(`/api/users/${id}`);
    return response.json();
  } catch (error) {
    return null;
  }
}
"""
    catch_chain_sample = """
export function loadUser(id) {
  return fetch(`/api/users/${id}`).catch(() => null);
}
"""
    safe_wrapper_sample = """
export function loadUser(id) {
  return fetchWithRetry(`/api/users/${id}`);
}
"""
    timeout_sample = """
export async function loadUser(id) {
  const controller = new AbortController();
  const response = await fetch(`/api/users/${id}`, { signal: controller.signal });
  return response.json();
}
"""

    naked_findings = _findings_for(naked_sample, policy)
    try_findings = _findings_for(try_catch_sample, policy)
    catch_findings = _findings_for(catch_chain_sample, policy)
    safe_findings = _findings_for(safe_wrapper_sample, policy)
    timeout_findings = _findings_for(timeout_sample, policy)

    checks = [
        _check(
            "resilience_policy_file_exists",
            POLICY_PATH.exists() and policy.get("meta", {}).get("kind") == "resilience_error_handling_policy",
            "The resilience cage must be policy-backed.",
            {"path": str(POLICY_PATH), "meta": policy.get("meta")},
        ),
        _check(
            "resilience_policy_declares_detection_surfaces",
            all(policy.get(key) for key in ["source_file_extensions", "external_call_markers", "required_contract_markers", "safe_wrapper_markers"])
            and isinstance(policy.get("risk_catalog"), dict)
            and bool(policy.get("risk_catalog")),
            "External call markers, contract markers, safe wrappers and risk catalog must be configured.",
            {key: policy.get(key) for key in ["external_call_markers", "required_contract_markers", "safe_wrapper_markers"]},
        ),
        _check(
            "resilience_engine_has_no_policy_owned_literal_tokens",
            not engine_token_hits,
            "External call and resilience contract markers must live in config/resilience_error_handling_policy.json.",
            engine_token_hits,
        ),
        _check(
            "resilience_fixture_detects_naked_external_call",
            bool(naked_findings),
            "A naked external call should be flagged as a resilience candidate.",
            naked_findings,
        ),
        _check(
            "resilience_fixture_does_not_flag_try_catch",
            not try_findings,
            "Visible try/catch should satisfy the local resilience contract.",
            try_findings,
        ),
        _check(
            "resilience_fixture_does_not_flag_catch_chain",
            not catch_findings,
            "Visible catch-chain should satisfy the local resilience contract.",
            catch_findings,
        ),
        _check(
            "resilience_fixture_does_not_flag_safe_wrapper",
            not safe_findings,
            "Configured safe wrappers should satisfy the resilience contract.",
            safe_findings,
        ),
        _check(
            "resilience_fixture_does_not_flag_abort_signal",
            not timeout_findings,
            "Abort/signal timeout-style contracts should satisfy the resilience contract.",
            timeout_findings,
        ),
    ]

    payload = {
        "meta": {"kind": "resilience_error_handling_cage_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "resilience_error_handling_cage_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "resilience_error_handling_cage_validation.md", _render_report(payload))
    return payload


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Resilience And Error Handling Cage Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- passed: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = str(check.get("details") or "").replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | {details} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

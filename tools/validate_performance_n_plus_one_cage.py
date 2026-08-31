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
from tools.engines.performance_n_plus_one_cage import analyze_performance_file


ENGINE_PATH = CODE_MAPS_DIR / "tools" / "engines" / "performance_n_plus_one_cage.py"
POLICY_PATH = CONFIG_DIR / "performance_n_plus_one_policy.json"


def _check(name: str, passed: bool, details: str, evidence: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details, "evidence": evidence}


def _load_policy() -> dict[str, Any]:
    policy = load_json_file(POLICY_PATH, {})
    return policy if isinstance(policy, dict) else {}


def run_validation() -> dict[str, Any]:
    policy = _load_policy()
    engine_text = ENGINE_PATH.read_text(encoding="utf-8", errors="replace")
    policy_owned_tokens = sorted(
        {
            str(item)
            for key in ["loop_start_patterns", "async_markers", "call_markers", "safe_wrapper_patterns"]
            for item in (policy.get(key, []) if isinstance(policy.get(key), list) else [])
            if str(item).strip()
        }
    )
    engine_token_hits = [
        token for token in policy_owned_tokens
        if f'"{token}"' in engine_text or f"'{token}'" in engine_text
    ]

    n_plus_one_sample = """
export async function loadUsers(ids) {
  const users = [];
  for (const id of ids) {
    const user = await prisma.user.findUnique({ where: { id } });
    users.push(user);
  }
  return users;
}
"""
    n_plus_one_result = analyze_performance_file("FIXTURE", "src/loadUsers.ts", n_plus_one_sample, policy) or {}
    n_plus_one_findings = n_plus_one_result.get("findings", []) if isinstance(n_plus_one_result, dict) else []
    n_plus_one_risks = sorted({str(item.get("risk_id")) for item in n_plus_one_findings if isinstance(item, dict)})

    safe_batch_sample = """
export async function loadUsers(ids) {
  return Promise.all(ids.map(async (id) => {
    return fetch(`/api/users/${id}`);
  }));
}
"""
    safe_batch_result = analyze_performance_file("FIXTURE", "src/loadUsers.ts", safe_batch_sample, policy) or {}
    safe_batch_findings = safe_batch_result.get("findings", []) if isinstance(safe_batch_result, dict) else []

    pure_loop_sample = """
export function names(users) {
  return users.map((user) => user.name.toUpperCase());
}
"""
    pure_loop_result = analyze_performance_file("FIXTURE", "src/names.ts", pure_loop_sample, policy) or {}
    pure_loop_findings = pure_loop_result.get("findings", []) if isinstance(pure_loop_result, dict) else []

    checks = [
        _check(
            "performance_policy_file_exists",
            POLICY_PATH.exists() and policy.get("meta", {}).get("kind") == "performance_n_plus_one_policy",
            "The performance cage must be policy-backed.",
            {"path": str(POLICY_PATH), "meta": policy.get("meta")},
        ),
        _check(
            "performance_policy_declares_detection_surfaces",
            all(policy.get(key) for key in ["source_file_extensions", "loop_start_patterns", "async_markers", "call_markers", "safe_wrapper_patterns"])
            and isinstance(policy.get("risk_catalog"), dict)
            and bool(policy.get("risk_catalog")),
            "Loop patterns, async markers, call markers, safe wrappers and risk catalog must be configured.",
            {key: policy.get(key) for key in ["loop_start_patterns", "async_markers", "call_markers", "safe_wrapper_patterns"]},
        ),
        _check(
            "performance_engine_has_no_policy_owned_literal_tokens",
            not engine_token_hits,
            "Loop, async, call and safe-wrapper markers must live in config/performance_n_plus_one_policy.json.",
            engine_token_hits,
        ),
        _check(
            "performance_fixture_detects_loop_contained_async_call",
            "loop_contained_async_call" in set(n_plus_one_risks),
            "The minimal fixture should catch await database/network calls inside loops.",
            n_plus_one_risks,
        ),
        _check(
            "performance_fixture_does_not_flag_safe_batch_wrapper",
            not safe_batch_findings,
            "Promise/batch wrappers declared in policy should not be treated as N+1 candidates.",
            safe_batch_findings,
        ),
        _check(
            "performance_fixture_does_not_flag_pure_iteration",
            not pure_loop_findings,
            "Pure iterator callbacks without async I/O must not be flagged.",
            pure_loop_findings,
        ),
    ]

    payload = {
        "meta": {"kind": "performance_n_plus_one_cage_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "performance_n_plus_one_cage_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "performance_n_plus_one_cage_validation.md", _render_report(payload))
    return payload


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Performance And N+1 Cage Validation",
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

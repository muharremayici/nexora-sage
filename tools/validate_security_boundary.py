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


RAW_OUTPUT_PATH = RAW_DIR / "security_boundary_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "security_boundary_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_validation() -> dict[str, Any]:
    matrix_path = ROOT / "docs" / "SECURITY_CAPABILITY_MATRIX.md"
    contextos_path = ROOT / "tools" / "core" / "contextos_mcp.py"
    mcp_governance_path = ROOT / "tools" / "engines" / "mcp_governance_engine.py"
    hitl_ledger_path = ROOT / "tools" / "hitl_approval_ledger.py"
    hitl_validator_path = ROOT / "tools" / "validate_hitl_governance.py"
    self_healing_path = ROOT / "tools" / "engines" / "self_healing_generator.py"
    skill_path = ROOT / "SKILL.md"
    claim_guard_path = ROOT / "tools" / "validate_claim_guard.py"

    matrix = _read(matrix_path)
    contextos = _read(contextos_path)
    mcp_governance = _read(mcp_governance_path)
    hitl_ledger = _read(hitl_ledger_path)
    hitl_validator = _read(hitl_validator_path)
    self_healing = _read(self_healing_path)
    skill = _read(skill_path)
    claim_guard = _read(claim_guard_path)

    checks = [
        _check(
            "security_matrix_scopes_claims_away_from_full_sast",
            "not a full SAST platform" in matrix
            and "Dependency vulnerability scanning" in matrix
            and "Roadmap" in matrix
            and "Runtime taint and exploit flow" in matrix,
            str(matrix_path.relative_to(ROOT)),
        ),
        _check(
            "contextos_masks_secret_like_content_before_body_exposure",
            "MASKED_BODY" in contextos
            and ".env" in contextos
            and "client_secret" in contextos
            and "access_token" in contextos
            and "safe_signal_body" in contextos,
            str(contextos_path.relative_to(ROOT)),
        ),
        _check(
            "mcp_patch_validation_blocks_workspace_path_escape",
            "_resolve_target_inside_root" in mcp_governance
            and "relative_to(root)" in mcp_governance
            and "mcp_path_escape" in mcp_governance
            and "safe_env" in mcp_governance,
            str(mcp_governance_path.relative_to(ROOT)),
        ),
        _check(
            "hitl_ledger_uses_hmac_chain_and_validator_checks_it",
            "HMAC-SHA256" in hitl_ledger
            and "hmac.new" in hitl_ledger
            and "prev_chain_hash" in hitl_ledger
            and "hmac.compare_digest" in hitl_ledger
            and "ledger_integrity_hmac_chain_valid" in hitl_validator,
            [
                str(hitl_ledger_path.relative_to(ROOT)),
                str(hitl_validator_path.relative_to(ROOT)),
            ],
        ),
        _check(
            "generated_mutation_scripts_are_blocked_by_default",
            "CODEMAPS_ALLOW_MUTATION_SCRIPTS" in self_healing
            and "exit 64" in self_healing
            and "_manifest_guard_ps_lines" in self_healing
            and "_manifest_guard_bash_lines" in self_healing
            and "sha256" in self_healing,
            str(self_healing_path.relative_to(ROOT)),
        ),
        _check(
            "agent_skill_requires_human_approval_for_mutation_actions",
            "Do not execute generated mutation, merge, delete, move, or refactor actions without explicit human approval" in skill
            and "create a HITL decision request" in skill
            and "record it in the HITL approval ledger" in skill,
            str(skill_path.relative_to(ROOT)),
        ),
        _check(
            "claim_guard_prevents_security_claim_drift",
            "docs_do_not_claim_full_sast_or_appsec_replacement" in claim_guard
            and "Dependency vulnerability scanning" in matrix
            and "not a full SAST" in matrix,
            str(claim_guard_path.relative_to(ROOT)),
        ),
    ]
    failed = [check for check in checks if not check.get("passed")]
    return {
        "meta": {
            "kind": "security_boundary_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_security_boundary",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }


def render_report(validation: dict[str, Any]) -> str:
    summary = validation.get("summary", {})
    lines = [
        "# Security Boundary Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in validation.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False)
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | `{details}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    validation = run_validation()
    save_json_atomic(RAW_OUTPUT_PATH, validation)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(validation))
    print(json.dumps(validation.get("summary", {}), ensure_ascii=False))
    return 0 if validation.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

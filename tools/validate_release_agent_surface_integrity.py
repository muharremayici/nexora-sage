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
from tools.core.release_proof_steps import load_release_proof_command_names


RAW_OUTPUT_PATH = RAW_DIR / "release_agent_surface_integrity_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "release_agent_surface_integrity_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _proof_surface_keeps_canonical_claim_guard(proof_source: str, proof_contract_source: str) -> dict[str, bool]:
    """Verify the proof projects the claim through the canonical identity matcher."""
    proof_surface = proof_source + "\n" + proof_contract_source
    return {
        "claim_guard_status_projected": "claim_guard_ready" in proof_surface,
        "scoped_react_status_projected": "scoped_react_ready" in proof_surface,
        "canonical_claim_matcher_used": "matches_current_release_claim(allowed_claim)" in proof_source,
        "observed_claim_projected": '"allowed_release_claim": allowed_claim' in proof_source,
    }


def run_validation() -> dict[str, Any]:
    release_path = ROOT / "tools" / "engines" / "release_readiness_report.py"
    proof_path = ROOT / "tools" / "run_release_proof_bundle.py"
    proof_contract_path = ROOT / "config" / "release_proof_steps_contract.json"
    brief_path = ROOT / "tools" / "generate_nexora_brief.py"
    operator_packet_path = ROOT / "tools" / "generate_nexora_operator_packet.py"
    claim_guard_path = ROOT / "tools" / "validate_claim_guard.py"
    release_doc_path = ROOT / "docs" / "RELEASE_EVIDENCE_BUNDLE.md"
    skill_path = ROOT / "SKILL.md"

    release_source = _read(release_path)
    proof_source = _read(proof_path)
    proof_contract_source = _read(proof_contract_path)
    proof_command_names = load_release_proof_command_names()
    brief_source = _read(brief_path)
    operator_source = _read(operator_packet_path)
    claim_guard_source = _read(claim_guard_path)
    release_doc = _read(release_doc_path)
    release_doc_plain = release_doc.replace("`", "")
    skill_source = _read(skill_path)
    claim_guard_projection = _proof_surface_keeps_canonical_claim_guard(
        proof_source,
        proof_contract_source,
    )

    checks = [
        _check(
            "release_readiness_separates_platform_ready_from_ecosystem_attention",
            '"quality_gate_pass"' in release_source
            and "enforced=False" in release_source
            and "ecosystem_signal_status" in release_source
            and "Platform readiness means Nexora SAGE release contracts are satisfied" in release_source,
            str(release_path.relative_to(ROOT)),
        ),
        _check(
            "release_readiness_requires_claim_and_agent_surface_evidence",
            '"react_universal_readiness_pass"' in release_source
            and '"mcp_agent_surface_pass"' in release_source
            and "react_claim_ready" in release_source
            and "external_fixture_pool_required" in release_source,
            str(release_path.relative_to(ROOT)),
        ),
        _check(
            "release_proof_bundle_runs_layer_integrity_validators",
            {
                "validate_discovery_universality.py",
                "validate_atlas_genome_audit_integrity.py",
                "validate_oracle_doctrine_hitl_integrity.py",
                "validate_doctrine_audit_quality_integrity.py",
            }
            <= proof_command_names,
            str(proof_contract_path.relative_to(ROOT)),
        ),
        _check(
            "release_proof_bundle_keeps_claim_guard_in_status",
            all(claim_guard_projection.values()),
            {
                "source": str(proof_path.relative_to(ROOT)),
                "contract": str(proof_contract_path.relative_to(ROOT)),
                **claim_guard_projection,
            },
        ),
        _check(
            "brief_projects_release_and_attention_to_agents",
            "release_readiness" in brief_source
            and "quality_gate" in brief_source
            and "remaining_active_repo_attention" in brief_source
            and "attention_needed_not_release_blocking" in brief_source,
            str(brief_path.relative_to(ROOT)),
        ),
        _check(
            "operator_packet_uses_brief_contract_and_release_claim_rule",
            "nexora_brief.json" in operator_source
            and "Use release proof and React universal readiness before product-readiness claims" in operator_source,
            str(operator_packet_path.relative_to(ROOT)),
        ),
        _check(
            "skill_guides_agents_to_contextos_before_manual_file_expansion",
            "get_surgical_operation_packet(max_signals?, format?, target_root?)" in skill_source
            and "get_active_signals(scope?, include_bodies?, target_root?)" in skill_source
            and "trace_upstream_cause(target_node, target_root?, format?)" in skill_source
            and skill_source.find("get_surgical_operation_packet") < skill_source.find("inspect_file"),
            str(skill_path.relative_to(ROOT)),
        ),
        _check(
            "claim_guard_binds_release_readiness_to_allowed_claim",
            "release_readiness_snapshot_is_current_claim_source" in claim_guard_source
            and "react_claim_requires_universal_readiness" in claim_guard_source
            and "docs_do_not_claim_full_sast_or_appsec_replacement" in claim_guard_source,
            str(claim_guard_path.relative_to(ROOT)),
        ),
        _check(
            "release_evidence_bundle_mentions_interpretation_rules",
            "Interpretation Rules" in release_doc_plain
            and "react_universal_ready=true requires complete executable evidence" in release_doc_plain
            and "External corpus evidence strengthens the React claim" in release_doc_plain,
            str(release_doc_path.relative_to(ROOT)),
        ),
    ]
    failed = [check for check in checks if not check.get("passed")]
    return {
        "meta": {
            "kind": "release_agent_surface_integrity_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_release_agent_surface_integrity",
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
        "# Release Agent Surface Integrity Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in validation.get("checks", []):
        result = "PASS" if check.get("passed") else "FAIL"
        details = json.dumps(check.get("details"), ensure_ascii=False)
        lines.append(f"| `{check.get('name')}` | {result} | `{details}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    validation = run_validation()
    save_json_atomic(RAW_OUTPUT_PATH, validation)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(validation))
    print(json.dumps(validation.get("summary", {}), ensure_ascii=False))
    return 0 if validation.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

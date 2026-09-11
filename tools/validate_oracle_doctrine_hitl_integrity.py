from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


RAW_OUTPUT_PATH = RAW_DIR / "oracle_doctrine_hitl_integrity_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "oracle_doctrine_hitl_integrity_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _pipeline_oracle_io_contract(path: Path) -> dict[str, Any]:
    """Read the literal registry row without depending on source formatting."""

    try:
        tree = ast.parse(_read(path), filename=str(path))
        for node in tree.body:
            value = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "ARTIFACT_OWNERSHIP":
                    value = node.value
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "ARTIFACT_OWNERSHIP"
                for target in node.targets
            ):
                value = node.value
            if value is not None:
                ownership = ast.literal_eval(value)
                row = ownership.get("Architecture Oracle", {})
                return row if isinstance(row, dict) else {}
    except (OSError, SyntaxError, ValueError):
        return {}
    return {}


def run_validation() -> dict[str, Any]:
    oracle_source_path = ROOT / "tools" / "engines" / "architecture_oracle.py"
    orchestrator_path = ROOT / "tools" / "orchestrators" / "orchestrator.py"
    registry_path = ROOT / "tools" / "core" / "pipeline_registry.py"
    mcp_path = ROOT / "tools" / "mcp" / "server.py"
    claim_guard_path = ROOT / "tools" / "validate_claim_guard.py"
    agent_contract_path = ROOT / "tools" / "generate_nexora_agent_contract.py"
    hitl_validator_path = ROOT / "tools" / "validate_hitl_governance.py"
    hitl_governance_contract_path = ROOT / "config" / "hitl_governance_contract.json"
    oracle_doc_path = ROOT / "docs" / "POST_ATLAS_ARCHITECTURE_ORACLE.md"
    coverage_doc_path = ROOT / "docs" / "ARCHITECTURE_ORACLE_BLUEPRINT_COVERAGE.md"

    oracle_source = _read(oracle_source_path)
    orchestrator_source = _read(orchestrator_path)
    registry_source = _read(registry_path)
    oracle_io_contract = _pipeline_oracle_io_contract(registry_path)
    mcp_source = _read(mcp_path)
    claim_guard_source = _read(claim_guard_path)
    agent_contract_source = _read(agent_contract_path)
    hitl_validator_source = _read(hitl_validator_path)
    hitl_governance_contract = _read(hitl_governance_contract_path)
    oracle_doc = _read(oracle_doc_path)
    coverage_doc = _read(coverage_doc_path)

    oracle_payload = load_json_file(RAW_DIR / "architecture_oracle.json", {})
    summary = oracle_payload.get("summary", {}) if isinstance(oracle_payload.get("summary"), dict) else {}
    policy = oracle_payload.get("policy", {}) if isinstance(oracle_payload.get("policy"), dict) else {}
    projects_payload = oracle_payload.get("projects", [])
    if isinstance(projects_payload, dict):
        project_values = [item for item in projects_payload.values() if isinstance(item, dict)]
    elif isinstance(projects_payload, list):
        project_values = [item for item in projects_payload if isinstance(item, dict)]
    else:
        project_values = []
    project_ids = {str(item.get("project")) for item in project_values if item.get("project")}

    checks = [
        _check(
            "oracle_is_post_atlas_advisory_mode",
            '"input": "post_atlas"' in oracle_source
            and '"mode": "advisory_human_seal"' in oracle_source
            and '"hard_gate_enforced": False' in oracle_source
            and '"audit_blocking_before_seal": False' in oracle_source,
            str(oracle_source_path.relative_to(ROOT)),
        ),
        _check(
            "oracle_writes_only_oracle_artifacts",
            'RAW_DIR / "architecture_oracle.json"' in oracle_source
            and 'REPORTS_DIR / "architecture_oracle.md"' in oracle_source
            and "architecture_doctrine.json" not in oracle_source
            and "DOCTRINE_FILE" not in oracle_source,
            str(oracle_source_path.relative_to(ROOT)),
        ),
        _check(
            "orchestrator_runs_oracle_after_atlas",
            '"Architecture Oracle"' in orchestrator_source
            and '"Atlas"' in orchestrator_source
            and '"depends_on": ["Atlas"]' in orchestrator_source,
            str(orchestrator_path.relative_to(ROOT)),
        ),
        _check(
            "pipeline_registry_declares_oracle_io_contract",
            {"atlas", "analysis_scope_authority"}.issubset(
                {str(item) for item in oracle_io_contract.get("reads", [])}
            )
            and {str(item) for item in oracle_io_contract.get("writes", [])}
            == {"architecture_oracle", "effective_architecture_policy"},
            {
                "path": str(registry_path.relative_to(ROOT)),
                "contract": oracle_io_contract,
            },
        ),
        _check(
            "mcp_exposes_oracle_as_proposal_surface",
            "architecture://oracle" in mcp_source
            and "get_architecture_oracle" in mcp_source
            and 'payload.get("policy"' in mcp_source,
            str(mcp_path.relative_to(ROOT)),
        ),
        _check(
            "claim_guard_refuses_oracle_hard_gate_drift",
            "architecture_oracle_external_blueprint_evidence_present" in claim_guard_source
            and 'oracle_summary.get("hard_gate_enforced") is False' in claim_guard_source,
            str(claim_guard_path.relative_to(ROOT)),
        ),
        _check(
            "hitl_contract_requires_architecture_doctrine_seal_gate",
            "architecture_doctrine_seal" in agent_contract_source
            and "architecture_doctrine.json" in agent_contract_source
            and "without explicit human approval" in agent_contract_source
            and "architecture_doctrine_seal" in hitl_governance_contract
            and "required_agent_contract_approval_gates" in hitl_governance_contract
            and "GOVERNANCE_CONTRACT_PATH" in hitl_validator_source,
            [
                str(agent_contract_path.relative_to(ROOT)),
                str(hitl_validator_path.relative_to(ROOT)),
                str(hitl_governance_contract_path.relative_to(ROOT)),
            ],
        ),
        _check(
            "oracle_artifact_policy_matches_human_seal_contract",
            summary.get("hard_gate_enforced") is False
            and policy.get("seal_requires_human_approval") is True
            and policy.get("audit_blocking_before_seal") is False
            and bool(project_values)
            and "MAIN" in project_ids
            and int(summary.get("project_count") or 0) == len(project_values)
            and all(item.get("requires_human_approval") is True for item in project_values),
            {"summary": summary, "policy": policy, "project_count": len(project_values), "projects": sorted(project_ids)},
        ),
        _check(
            "oracle_docs_state_no_auto_rewrite_and_human_seal",
            "does not rewrite `architecture_doctrine.json`" in oracle_doc
            and "Human seals" in oracle_doc
            and "without HITL approval" in oracle_doc
            and "does not hard-block unsealed repositories" in coverage_doc,
            [
                str(oracle_doc_path.relative_to(ROOT)),
                str(coverage_doc_path.relative_to(ROOT)),
            ],
        ),
    ]
    failed = [check for check in checks if not check.get("passed")]
    return {
        "meta": {
            "kind": "oracle_doctrine_hitl_integrity_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_oracle_doctrine_hitl_integrity",
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
        "# Oracle Doctrine HITL Integrity Validation",
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

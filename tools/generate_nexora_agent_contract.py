from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict[str, Any]:
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def _summary(path: Path) -> dict[str, Any]:
    payload = _load(path)
    summary = payload.get("summary")
    return summary if isinstance(summary, dict) else {}


def _quality_warnings(raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    quality = _load(raw_dir / "quality_gate.json")
    warnings = quality.get("ecosystem_warning_signals")
    return warnings if isinstance(warnings, dict) else {}


def _generation_identity(raw_dir: Path) -> dict[str, Any]:
    atlas = load_atlas_data(raw_dir)
    atlas_meta = atlas.get("meta", {}) if isinstance(atlas.get("meta"), dict) else {}
    watchdog = _load(raw_dir / "watchdog_session.json")
    watchdog_meta = watchdog.get("meta", {}) if isinstance(watchdog.get("meta"), dict) else {}
    values = {
        "atlas_snapshot_id": atlas_meta.get("snapshot_id") or atlas.get("snapshot_id"),
        "run_id": watchdog_meta.get("run_id") or watchdog.get("run_id"),
        "watchdog_session_id": watchdog_meta.get("session_id") or watchdog.get("session_id"),
    }
    observed = {key: value for key, value in values.items() if value not in {None, ""}}
    return {"status": "observed" if observed else "unavailable", **observed}


def build_approval_gates(
    *,
    raw_dir: Path = RAW_DIR,
    system_scope: str = "SAGE_ON_SAGE",
    subject_root: str = "",
) -> list[dict[str, Any]]:
    raw_dir = Path(raw_dir)
    scope = str(system_scope or "").strip()
    if scope not in {"SAGE_ON_SAGE", "SAGE_ON_REPOSITORY"}:
        raise ValueError(f"Unsupported approval-gate system scope: {scope or '<empty>'}")
    warnings = _quality_warnings(raw_dir)
    release = _load(raw_dir / "release_readiness.json")
    brief = _load(raw_dir / "nexora_brief.json")
    watchdog = _summary(raw_dir / "watchdog_session.json")
    oracle = _load(raw_dir / "architecture_oracle.json")
    oracle_summary = oracle.get("summary", {}) if isinstance(oracle.get("summary"), dict) else {}
    authority = {
        "system_scope": scope,
        "subject_root": str(subject_root or ""),
        "artifact_root": str(raw_dir.resolve()),
        "generation_identity": _generation_identity(raw_dir),
    }

    gates = [
        {
            "id": "watchdog_auto_restore",
            "human_approval_required": True,
            "risk": "critical",
            "trigger": "Watchdog ENFORCE mode would restore changed files after a policy violation.",
            "agent_rule": "A signed, unexpired approval scoped to the watched root is mandatory before any automatic git restore.",
            "evidence": ["config/hitl_policy.json", "output/.raw/hitl_approval_ledger.json", "output/.raw/watchdog_session.json"],
        },
        {
            "id": "mutation_scripts",
            "human_approval_required": True,
            "risk": "critical",
            "trigger": "Generated scripts that mutate, merge, delete, move, or rewrite project files.",
            "agent_rule": "Never execute mutation scripts through MCP. Present evidence and ask for explicit human approval.",
            "evidence": ["output/scripts/*_manifest.json", "docs/GENERATED_SCRIPT_SAFETY_RUNBOOK.md"],
        },
        {
            "id": "ui_runtime_manual_validation",
            "human_approval_required": bool(warnings.get("ui_browser_smoke_required_candidates", 0)),
            "risk": "high" if warnings.get("ui_browser_smoke_required_candidates", 0) else "low",
            "trigger": "UI/runtime candidates require browser smoke or manual behavior validation.",
            "agent_rule": "Mark the recommendation as provisional until browser/manual validation is complete.",
            "evidence": ["output/.raw/ui_runtime_contracts.json", "output/reports/ui_smoke_specs.md"],
            "current_signal": {
                "ui_high_risk_merge_candidates": warnings.get("ui_high_risk_merge_candidates"),
                "ui_browser_smoke_required_candidates": warnings.get("ui_browser_smoke_required_candidates"),
                "main_ui_high_risk_files": warnings.get("main_ui_high_risk_files"),
            },
        },
        {
            "id": "merge_import_review",
            "human_approval_required": bool(warnings.get("cockpit_import_with_review", 0) or warnings.get("cockpit_do_not_import", 0)),
            "risk": "high",
            "trigger": "Merge/import recommendations outside the explicit Import Now lane.",
            "agent_rule": "Do not apply merge/import decisions automatically unless the action is Import Now and the human confirms scope.",
            "evidence": ["output/.raw/merge_decision_cockpit.json", "output/reports/merge_decision_cockpit.md"],
            "current_signal": {
                "import_with_review": warnings.get("cockpit_import_with_review"),
                "do_not_import": warnings.get("cockpit_do_not_import"),
            },
        },
        {
            "id": "refactor_or_large_file_split",
            "human_approval_required": True,
            "risk": "medium",
            "trigger": "Large structural refactors, file splitting, or ownership boundary changes.",
            "agent_rule": "Keep refactors out of automatic remediation unless the human explicitly requests them.",
            "evidence": ["output/reports/nanometric_action_plan_2026-05-13.md"],
        },
        {
            "id": "external_target_analysis",
            "human_approval_required": True,
            "risk": "medium",
            "trigger": "Analyzing a folder outside the default workspace root via target-root.",
            "agent_rule": "State the target root, isolation output path, and preflight result before treating findings as authoritative.",
            "evidence": ["output/reports/external_target_runs.md", "docs/EXTERNAL_TARGET_RUNBOOK.md"],
        },
        {
            "id": "architecture_doctrine_seal",
            "human_approval_required": True,
            "risk": "high",
            "trigger": "Applying an Architecture Oracle proposal, changing architecture_doctrine.json, or treating an advisory proposal as sealed governance.",
            "agent_rule": "Never rewrite architecture_doctrine.json or treat Architecture Oracle output as sealed doctrine without explicit human approval and a HITL ledger entry.",
            "evidence": ["output/.raw/architecture_oracle.json", "output/reports/architecture_oracle.md", "config/architecture_doctrine.json"],
            "current_signal": {
                "oracle_status": oracle_summary.get("status"),
                "seal_ready_projects": oracle_summary.get("seal_ready_projects"),
                "hard_gate_enforced": oracle_summary.get("hard_gate_enforced"),
            },
        },
        {
            "id": "release_claims",
            "human_approval_required": (release.get("readiness") != "PRODUCTION_READY"),
            "risk": "high" if release.get("readiness") != "PRODUCTION_READY" else "low",
            "trigger": "Claiming production readiness or broad React ecosystem support.",
            "agent_rule": "Only claim readiness when release proof, React universal readiness, and report freshness pass.",
            "evidence": ["output/.raw/release_proof_bundle.json", "output/.raw/react_universal_readiness.json", "output/.raw/report_freshness_index.json"],
            "current_signal": {
                "release_readiness": release.get("readiness"),
                "brief_quality_gate": (_summary(RAW_DIR / "nexora_brief.json") or brief.get("summary", {})).get("quality_gate"),
                "release_proof_bundle_currentness": "not_available_during_agent_contract_generation",
                "required_followup": "call get_release_proof or inspect the final release_proof_bundle after the proof run completes before making public readiness claims",
            },
        },
        {
            "id": "watchdog_followup",
            "human_approval_required": watchdog.get("integrity") == "VIOLATIONS",
            "risk": "high" if watchdog.get("integrity") == "VIOLATIONS" else "low",
            "trigger": "Latest watchdog pulse reports violations.",
            "agent_rule": "If violations exist, summarize them and ask for direction before applying fixes.",
            "evidence": ["output/.raw/watchdog_session.json", "output/reports/watchdog_session.md"],
            "current_signal": {
                "integrity": watchdog.get("integrity"),
                "violation_count": watchdog.get("violation_count"),
                "changed_files": watchdog.get("changed_files"),
            },
        },
    ]
    if scope == "SAGE_ON_REPOSITORY":
        sage_only_gate_ids = {"architecture_doctrine_seal", "release_claims"}
        gates = [gate for gate in gates if gate.get("id") not in sage_only_gate_ids]
    return [{**gate, **authority} for gate in gates]


def build_contract() -> dict[str, Any]:
    brief = _load(RAW_DIR / "nexora_brief.json")
    brief_summary = brief.get("summary", {}) if isinstance(brief.get("summary"), dict) else {}
    release = _load(RAW_DIR / "release_readiness.json")
    universal = _summary(RAW_DIR / "react_universal_readiness.json")
    gates = build_approval_gates()

    return {
        "meta": {
            "kind": "nexora_agent_contract",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_nexora_agent_contract",
            "workspace_root": str(CODE_MAPS_DIR),
        },
        "identity": {
            "product_role": "AI-native codebase intelligence substrate",
            "relationship": {
                "nexora": "assistant_to_ai_agent",
                "ai_agent": "assistant_to_human",
                "human": "authority_and_approval_owner",
            },
        },
        "response_protocol": {
            "required_fields": [
                "verdict",
                "evidence",
                "confidence",
                "risk",
                "human_approval_required",
                "suggested_next_action",
                "source_artifacts",
            ],
            "rule": "Every agent-facing answer must distinguish evidence from inference and must state when human approval is required.",
        },
        "operating_rules": [
            "Read nexora_brief before opening large artifacts.",
            "Use inspect_file, inspect_folder, or inspect_symbol before giving targeted code advice.",
            "Use release proof and React universal readiness before making product-readiness claims.",
            "Treat watchdog_session as the latest live-change signal.",
            "Never execute mutation, merge, delete, move, or refactor actions through MCP without explicit human approval.",
        ],
        "current_posture": {
            "release_readiness": release.get("readiness"),
            "release_failed": brief_summary.get("release_failed"),
            "quality_gate": brief_summary.get("quality_gate"),
            "ecosystem_signal_status": brief_summary.get("ecosystem_signal_status"),
            "react_universal_ready": universal.get("universal_ready"),
            "universality_basis": universal.get("universality_basis"),
            "external_fixture_pool_required": universal.get("external_fixture_pool_required"),
        },
        "approval_gates": gates,
        "mcp_parity_targets": [
            "get_nexora_brief",
            "get_nexora_agent_contract",
            "get_watchdog_session",
            "inspect_file",
            "inspect_folder",
            "inspect_symbol",
            "get_release_proof",
            "get_react_universal_readiness",
            "get_human_approval_gates",
        ],
    }


def render_report(contract: dict[str, Any]) -> str:
    posture = contract.get("current_posture", {})
    protocol = contract.get("response_protocol", {})
    lines = [
        "# Nexora Agent Contract",
        "",
        f"- generated_at: `{contract.get('meta', {}).get('generated_at')}`",
        f"- product_role: `{contract.get('identity', {}).get('product_role')}`",
        f"- release_readiness: `{posture.get('release_readiness')}`",
        f"- quality_gate: `{posture.get('quality_gate')}`",
        f"- ecosystem_signal_status: `{posture.get('ecosystem_signal_status')}`",
        f"- react_universal_ready: `{posture.get('react_universal_ready')}`",
        "",
        "## Relationship",
        "",
        "- Nexora assists the AI agent.",
        "- The AI agent assists the human.",
        "- The human owns authority, approval, and final judgment.",
        "",
        "## Required Agent Answer Shape",
        "",
    ]
    for field in protocol.get("required_fields", []):
        lines.append(f"- `{field}`")
    lines.extend(["", f"Rule: {protocol.get('rule')}", "", "## Operating Rules", ""])
    for rule in contract.get("operating_rules", []):
        lines.append(f"- {rule}")

    lines.extend(["", "## Human Approval Gates", "", "| Gate | Approval Required | Risk | Agent Rule |", "|---|---|---|---|"])
    for gate in contract.get("approval_gates", []):
        lines.append(
            f"| `{gate.get('id')}` | `{gate.get('human_approval_required')}` | `{gate.get('risk')}` | {gate.get('agent_rule')} |"
        )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    contract = build_contract()
    save_json_atomic(RAW_DIR / "nexora_agent_contract.json", contract)
    save_text_atomic(REPORTS_DIR / "nexora_agent_contract.md", render_report(contract))
    return contract


def main() -> int:
    contract = run()
    print(json.dumps(contract.get("current_posture", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

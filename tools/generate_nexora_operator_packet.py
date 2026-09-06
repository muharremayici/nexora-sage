from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, ROOT as ANALYZED_REPOSITORY_ROOT, save_json_atomic, save_text_atomic
from tools.core.capability_activation import (
    compact_activation_summary,
    load_capability_activation_plan,
    relevant_activation_context,
)
from tools.core.capability_registry import load_capability_registry, relevant_capability_contracts
from tools.core.reality_scope import TARGET_REPOSITORY_PROJECTION_ID, projection_capability_scopes
from tools.core.agent_command_contracts import command_contracts_for_agent
from tools.core.architecture_blueprints import architecture_governance_context
from tools.core.contextos_mcp import build_agent_action_directives
from tools.core.json_io import load_json_file
from tools.core.contextos_signal_limits import contextos_signal_limit
from tools.core.analysis_scope_authority import (
    extract_scope_authority,
    fail_closed_scope_authority,
    reconcile_quality_scope_authority,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict[str, Any]:
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def _combined_release_readiness(
    platform_readiness: Any,
    quality_release_status: Any,
    quality_passed: Any,
) -> str:
    platform_status = str(platform_readiness or "UNKNOWN").strip().upper()
    quality_status = str(quality_release_status or "").strip().upper()
    quality_is_green = (
        quality_status == "PASS" and quality_passed is not False
        if quality_status
        else quality_passed is True
    )
    if platform_status == "PRODUCTION_READY" and quality_is_green:
        return "PRODUCTION_READY"
    if platform_status == "UNKNOWN" and not quality_status and quality_passed is None:
        return "INCOMPLETE_EVIDENCE"
    return "NOT_READY"


def _active_gates(contract: dict[str, Any]) -> list[dict[str, Any]]:
    gates = contract.get("approval_gates", []) if isinstance(contract, dict) else []
    return [
        {
            "id": gate.get("id"),
            "risk": gate.get("risk"),
            "agent_rule": gate.get("agent_rule"),
            "evidence": gate.get("evidence", []),
            "current_signal": gate.get("current_signal", {}),
        }
        for gate in gates
        if isinstance(gate, dict) and gate.get("human_approval_required")
    ]


def _execution_contract_context(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    execution_modes = payload.get("execution_modes") if isinstance(payload.get("execution_modes"), dict) else {}
    mode_order = [
        "first_repo_onboarding",
        "normal_full",
        "daily",
        "watchdog_save_pulse",
        "release_deep",
        "explicit_step",
        "external_corpus_validation",
        "clean_install_onboarding_proof",
    ]
    return {
        "summary": {
            "status": summary.get("status"),
            "steps": summary.get("steps"),
            "scheduler_counts": summary.get("scheduler_counts", {}),
            "sqlite_writers": summary.get("sqlite_writers"),
            "execution_modes": summary.get("execution_modes"),
        },
        "execution_modes": [
            {
                "id": mode,
                "profile": execution_modes.get(mode, {}).get("profile"),
                "scope": execution_modes.get(mode, {}).get("scope"),
                "heavy_step_policy": execution_modes.get(mode, {}).get("heavy_step_policy"),
                "claim_boundary": execution_modes.get(mode, {}).get("claim_boundary"),
                "agent_surface": execution_modes.get(mode, {}).get("agent_surface"),
            }
            for mode in mode_order
            if isinstance(execution_modes.get(mode), dict)
        ],
        "agent_rule": (
            "Use DAG-safe steps only after dependencies are satisfied; keep sequential-required "
            "steps as final/broad artifact consumers; avoid full-run-only steps in watchdog pulses "
            "unless explicitly requested."
        ),
        "mcp_tool": "get_pipeline_execution_contract",
        "artifact": "output/.raw/pipeline_execution_contract_validation.json",
    }


def _engine_signal_contract_context(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    contract_rows = [
        row
        for row in payload.get("contract_summary", [])
        if isinstance(row, dict)
        and row.get("capability_id") in {
            "react_surgical_intelligence",
            "architecture_governance",
            "contextos",
            "agent_surface",
            "release_proof",
            "engine_signal_evidence_contract",
        }
    ]
    return {
        "summary": {
            "status": summary.get("status"),
            "contracts": summary.get("contracts"),
            "release_relevant_capabilities": summary.get("release_relevant_capabilities"),
            "evidence_kinds": summary.get("evidence_kinds"),
        },
        "relevant_contracts": contract_rows[:8],
        "agent_rule": (
            "Treat engine outputs as signals unless the contract says they are calibrated evidence, "
            "a governance verdict, a human-approved action plan, or a ContextOS packet. Do not use "
            "roadmap or needs_runtime_proof signals as v1 release claims."
        ),
        "mcp_tool": "get_engine_signal_contracts",
        "artifact": "output/.raw/engine_signal_contract_validation.json",
    }


def _public_agent_directives(directives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hidden_fields = {
        "atlas_nodes",
        "file_context",
        "source_artifacts",
        "debug_internal_refs",
    }
    public_rows: list[dict[str, Any]] = []
    for directive in directives:
        if not isinstance(directive, dict):
            continue
        row = {key: value for key, value in directive.items() if key not in hidden_fields}
        commands = row.get("validation_commands") if isinstance(row.get("validation_commands"), list) else []
        if commands:
            row["validation_command_contracts"] = command_contracts_for_agent(commands, limit=6)
        public_rows.append(row)
    return public_rows


def build_operator_packet() -> dict[str, Any]:
    brief = _load(RAW_DIR / "nexora_brief.json")
    contract = _load(RAW_DIR / "nexora_agent_contract.json")
    watchdog = _load(RAW_DIR / "watchdog_session.json")
    mcp_surface = _load(RAW_DIR / "mcp_agent_surface_validation.json")
    freshness = _load(RAW_DIR / "report_freshness_index.json")
    approval_ledger = _load(RAW_DIR / "hitl_approval_ledger.json")
    decision_requests = _load(RAW_DIR / "hitl_decision_requests.json")
    hitl_governance = _load(RAW_DIR / "hitl_governance_validation.json")
    hitl_lifecycle = _load(RAW_DIR / "hitl_lifecycle_smoke.json")
    response_validation = _load(RAW_DIR / "nexora_agent_response_validation.json")
    response_ledger = _load(RAW_DIR / "nexora_agent_response_ledger.json")
    handoff = _load(RAW_DIR / "nexora_agent_handoff.json")
    execution_contract = _load(RAW_DIR / "pipeline_execution_contract_validation.json")
    engine_signal_contract = _load(RAW_DIR / "engine_signal_contract_validation.json")
    architecture_oracle = _load(RAW_DIR / "architecture_oracle.json")
    signals = _load(RAW_DIR / "signals.json")
    audit_report = _load(RAW_DIR / "audit_report.json")
    quality_gate = _load(RAW_DIR / "quality_gate.json")
    analysis_scope_authority = extract_scope_authority(
        _load(RAW_DIR / "analysis_scope_authority.json")
    ) or fail_closed_scope_authority("analysis_scope_authority_missing_at_operator_packet")
    capability_registry = load_capability_registry()
    capability_activation_plan = load_capability_activation_plan()

    brief_summary = brief.get("summary", {}) if isinstance(brief.get("summary"), dict) else {}
    contract_posture = contract.get("current_posture", {}) if isinstance(contract.get("current_posture"), dict) else {}
    proof_summary = {
        "status": "not_available_during_operator_packet_generation",
        "currentness": "final_release_proof_bundle_written_after_proof_run",
        "required_followup": "call get_release_proof or inspect the final release_proof_bundle after the proof run completes before making public readiness claims",
    }
    watchdog_summary = watchdog.get("summary", {}) if isinstance(watchdog.get("summary"), dict) else {}
    mcp_summary = mcp_surface.get("summary", {}) if isinstance(mcp_surface.get("summary"), dict) else {}
    freshness_summary = freshness.get("summary", {}) if isinstance(freshness.get("summary"), dict) else {}
    approval_summary = approval_ledger.get("summary", {}) if isinstance(approval_ledger.get("summary"), dict) else {}
    decision_summary = decision_requests.get("summary", {}) if isinstance(decision_requests.get("summary"), dict) else {}
    hitl_governance_summary = hitl_governance.get("summary", {}) if isinstance(hitl_governance.get("summary"), dict) else {}
    hitl_lifecycle_summary = hitl_lifecycle.get("summary", {}) if isinstance(hitl_lifecycle.get("summary"), dict) else {}
    response_validation_summary = response_validation.get("summary", {}) if isinstance(response_validation.get("summary"), dict) else {}
    response_ledger_summary = response_ledger.get("summary", {}) if isinstance(response_ledger.get("summary"), dict) else {}
    surface_inventory_summary = {
        "status": "not_available_during_operator_packet_generation",
        "currentness": "surface_inventory_validates_this_packet_after_generation",
        "required_followup": "Call get_surface_inventory after generation; do not infer inventory PASS from this packet.",
    }
    quality_release_status = quality_gate.get("release_gate_status") if isinstance(quality_gate, dict) else None
    quality_passed = quality_gate.get("passed") if isinstance(quality_gate, dict) else None
    quality_ecosystem_status = quality_gate.get("ecosystem_signal_status") if isinstance(quality_gate, dict) else None
    analysis_scope_authority, scope_actionable = reconcile_quality_scope_authority(
        analysis_scope_authority,
        quality_gate,
    )
    scope_evidence_status = str(analysis_scope_authority.get("evidence_status") or "INCOMPLETE_EVIDENCE")
    platform_release_readiness = contract_posture.get("release_readiness") or brief_summary.get("release_readiness")
    combined_release_readiness = _combined_release_readiness(
        platform_release_readiness,
        quality_release_status,
        quality_passed,
    )
    quality_interpretation = "unknown"
    if quality_release_status == "PASS" and quality_ecosystem_status == "ATTENTION":
        quality_interpretation = "release_gate_pass_with_ecosystem_attention"
    elif quality_release_status == "PASS":
        quality_interpretation = "release_gate_pass"
    elif quality_release_status:
        quality_interpretation = "release_gate_not_passing"
    protocol = contract.get("response_protocol", {}) if isinstance(contract.get("response_protocol"), dict) else {}
    agent_action_directives = build_agent_action_directives(
        signals,
        audit_report=audit_report,
        quality_gate=quality_gate,
        max_items=contextos_signal_limit("agent_directives"),
        project_scope="MAIN",
    )
    if not scope_actionable:
        agent_action_directives = []
    public_agent_action_directives = _public_agent_directives(agent_action_directives)
    operator_agent_action_directives = [
        {
            **public_row,
            "source_artifacts": source_row.get("source_artifacts", []),
        }
        for public_row, source_row in zip(public_agent_action_directives, agent_action_directives)
    ]
    approval_entries = approval_ledger.get("entries", []) if isinstance(approval_ledger, dict) else []
    approval_entry_limit = contextos_signal_limit("agent_ledger_entries")
    target_repository_agent_surface = {
        "surface": "target_repository_coding_agent",
        "status": "READY" if scope_actionable else "INCOMPLETE_EVIDENCE",
        "scope_authority": {
            "topology_authority_id": analysis_scope_authority.get("topology_authority_id"),
            "scope_authority_id": analysis_scope_authority.get("scope_authority_id"),
            "evidence_status": scope_evidence_status,
            "claim_scope": analysis_scope_authority.get("claim_scope"),
            "full_repository_claim_eligible": analysis_scope_authority.get("full_repository_claim_eligible") is True,
            "incomplete_reasons": list(analysis_scope_authority.get("incomplete_reasons") or []),
        },
        "required_action": (
            "none"
            if scope_actionable
            else "Refresh repository discovery, Atlas, Audit and Quality Gates before acting on directives."
        ),
        "default_projection": "agent_action_directives",
        "analysis_root": str(ANALYZED_REPOSITORY_ROOT),
        "contains_platform_status": False,
        "contains_debug_artifacts": False,
        "agent_rule": (
            "Use this surface for coding inside the analyzed repository. It intentionally excludes "
            "SAGE platform release/debug posture; use the separate operator surface only when operating SAGE itself."
        ),
        "path_contract": {
            "open_files_with": "analysis_root + directives[].target_files or related_files",
            "target_refs_usage": "SAGE/MCP follow-up references only; not filesystem paths",
        },
        "directives": public_agent_action_directives,
        "validation_policy": {
            "run_listed_commands_first": True,
            "fallback": "If no command is listed, run the closest target-repository validator for the edited file.",
        },
    }

    return {
        "meta": {
            "kind": "nexora_operator_packet",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_nexora_operator_packet",
            "workspace_root": str(CODE_MAPS_DIR),
            "analyzed_repository_root": str(ANALYZED_REPOSITORY_ROOT),
        },
        "mission_control": {
            "relationship": {
                "nexora": "assistant_to_ai_agent",
                "ai_agent": "assistant_to_human",
                "human": "authority_and_approval_owner",
            },
            "release_readiness": combined_release_readiness,
            "platform_release_readiness": platform_release_readiness,
            "target_quality_gate_status": quality_release_status,
            "target_scope_gate_status": quality_gate.get("scope_gate_status"),
            "target_scope_evidence_status": scope_evidence_status,
            "target_scope_authority_id": analysis_scope_authority.get("scope_authority_id"),
            "quality_gate": quality_release_status or contract_posture.get("quality_gate") or brief_summary.get("quality_gate"),
            "quality_gate_passed": quality_passed,
            "quality_gate_interpretation": quality_interpretation,
            "ecosystem_signal_status": quality_ecosystem_status
            or contract_posture.get("ecosystem_signal_status")
            or brief_summary.get("ecosystem_signal_status"),
            "react_universal_ready": contract_posture.get("react_universal_ready") or brief_summary.get("react_universal_ready"),
            "mcp_agent_surface": mcp_summary.get("status"),
            "release_proof": proof_summary.get("status"),
            "release_proof_currentness": proof_summary.get("currentness"),
            "release_proof_required_followup": proof_summary.get("required_followup"),
            "report_freshness": freshness_summary.get("freshness_gate"),
            "watchdog_integrity": watchdog_summary.get("integrity"),
            "watchdog_violation_count": watchdog_summary.get("violation_count"),
            "hitl_active_approvals": approval_summary.get("active_approvals", 0),
            "hitl_recorded_decisions": approval_summary.get("entries", 0),
            "hitl_open_decision_requests": decision_summary.get("open", 0),
            "hitl_governance": hitl_governance_summary.get("status"),
            "hitl_lifecycle_smoke": hitl_lifecycle_summary.get("status"),
            "agent_response_contract": response_validation_summary.get("status"),
            "agent_response_ledger_entries": response_ledger_summary.get("entries", 0),
            "agent_response_ledger_invalid": response_ledger_summary.get("invalid", 0),
            "agent_handoff_ready": bool(handoff),
            "surface_inventory": surface_inventory_summary.get("status"),
            "capability_activation_plan": (capability_activation_plan.get("summary") or {}).get("status"),
            "capability_scheduler_enforced": (capability_activation_plan.get("summary") or {}).get("pipeline_scheduler_enforced"),
            "pipeline_execution_contract": (execution_contract.get("summary") or {}).get("status"),
            "engine_signal_contract": (engine_signal_contract.get("summary") or {}).get("status"),
        },
        "role_surfaces": {
            "operator_platform_status": {
                "surface": "sage_operator_or_release_owner",
                "contains_platform_status": True,
                "default_projection": "mission_control",
                "agent_rule": (
                    "Use mission_control for SAGE platform readiness, release proof, HITL posture, and MCP surface health. "
                    "Do not send this whole platform surface as the default coding prompt for a target-repository agent."
                ),
            },
            "target_repository_agent": {
                "surface": "target_repository_coding_agent",
                "contains_platform_status": False,
                "default_projection": "target_repository_agent_surface",
                "agent_rule": (
                    "Use only the target_repository_agent_surface or surgical Markdown/YAML brief for default coding-agent context."
                ),
            },
        },
        "target_repository_agent_surface": target_repository_agent_surface,
        "required_answer_shape": protocol.get(
            "required_fields",
            [
                "verdict",
                "evidence",
                "confidence",
                "risk",
                "human_approval_required",
                "suggested_next_action",
                "source_artifacts",
            ],
        ),
        "answer_template": {
            "verdict": "",
            "evidence": [],
            "confidence": "low|medium|high",
            "risk": "low|medium|high|critical",
            "human_approval_required": False,
            "suggested_next_action": "",
            "source_artifacts": [],
        },
        "active_human_approval_gates": _active_gates(contract),
        "hitl_approval_ledger": {
            "summary": approval_summary,
            "latest_entries": approval_entries[-approval_entry_limit:],
            "entries_omitted": max(0, len(approval_entries) - approval_entry_limit),
            "full_ledger_artifact": "output/.raw/hitl_approval_ledger.json",
        },
        "hitl_decision_requests": {
            "summary": decision_summary,
            "open_requests": [
                item
                for item in (decision_requests.get("requests", []) if isinstance(decision_requests, dict) else [])
                if isinstance(item, dict) and item.get("status") == "open"
            ][-10:],
        },
        "hitl_governance_validation": hitl_governance_summary,
        "hitl_lifecycle_smoke": hitl_lifecycle_summary,
        "agent_response_contract_validation": response_validation_summary,
        "agent_response_ledger": {
            "summary": response_ledger_summary,
            "latest_entries": (response_ledger.get("entries", []) if isinstance(response_ledger, dict) else [])[-10:],
        },
        "agent_handoff": {
            "ready": bool(handoff),
            "artifact": "output/reports/nexora_agent_handoff.md",
        },
        "surface_inventory": {
            "summary": surface_inventory_summary,
            "artifact": "output/reports/nexora_surface_inventory.md",
        },
        "architecture_governance_context": architecture_governance_context(
            architecture_oracle,
            approval_ledger,
        ),
        "relevant_capabilities": relevant_capability_contracts(
            capability_registry,
            capability_ids=[
                "agent_surface",
                "contextos",
                "release_proof",
                "react_surgical_intelligence",
                "architecture_governance",
                "engine_signal_evidence_contract",
            ],
            artifacts=[
                "nexora_operator_packet",
                "mcp_agent_surface_validation",
                "signals",
                "release_proof_bundle",
                "react_ecosystem_analysis",
                "quality_gate",
            ],
            limit=8,
            allowed_system_scopes=projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID),
        ),
        "capability_activation": relevant_activation_context(
            capability_activation_plan,
            capability_ids=[
                "agent_surface",
                "contextos",
                "release_proof",
                "react_surgical_intelligence",
                "architecture_governance",
                "capability_activation_planner",
            ],
            limit=8,
            max_projects=3,
            include_disabled=False,
            allowed_system_scopes=projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID),
            project_ids=["MAIN"],
        ),
        "pipeline_execution_contract": _execution_contract_context(execution_contract),
        "engine_signal_contract": _engine_signal_contract_context(engine_signal_contract),
        "agent_action_directives": operator_agent_action_directives,
        "recommended_first_actions": [
            "Use target_repository_agent_surface for default coding-agent context; use mission_control only for SAGE operator/release posture.",
            "Read relevant_capabilities before deciding which artifacts or validators to trust.",
            "Read architecture_governance_context before interpreting architecture rules or claiming a human seal.",
            "Read capability_activation before deciding which capability family is meaningful for this workspace.",
            "Read pipeline_execution_contract before parallelizing release-style validation or changing execution behavior.",
            "Read engine_signal_contract before interpreting a finding as a verdict or action plan.",
            "Use inspect_file, inspect_folder, or inspect_symbol before targeted code advice.",
            "Use release proof and React universal readiness before product-readiness claims.",
            "Ask for explicit human approval before mutation, merge/import, deletion, movement, or large refactor actions.",
            "Treat UI/runtime findings as provisional when browser/manual validation is required.",
        ],
        "source_artifacts": {
            "operator_packet": "output/.raw/nexora_operator_packet.json",
            "brief": "output/.raw/nexora_brief.json",
            "agent_contract": "output/.raw/nexora_agent_contract.json",
            "release_proof": "output/.raw/release_proof_bundle.json",
            "mcp_agent_surface": "output/.raw/mcp_agent_surface_validation.json",
            "watchdog_session": "output/.raw/watchdog_session.json",
            "report_freshness": "output/.raw/report_freshness_index.json",
            "hitl_approval_ledger": "output/.raw/hitl_approval_ledger.json",
            "hitl_decision_requests": "output/.raw/hitl_decision_requests.json",
            "hitl_governance_validation": "output/.raw/hitl_governance_validation.json",
            "hitl_lifecycle_smoke": "output/.raw/hitl_lifecycle_smoke.json",
            "agent_response_validation": "output/.raw/nexora_agent_response_validation.json",
            "agent_response_template": "output/.raw/nexora_agent_response_template.json",
            "agent_response_ledger": "output/.raw/nexora_agent_response_ledger.json",
            "agent_handoff": "output/.raw/nexora_agent_handoff.json",
            "surface_inventory": "output/.raw/nexora_surface_inventory.json",
            "capability_registry": "config/capability_registry.json",
            "capability_activation_plan": "output/.raw/capability_activation_plan.json",
            "pipeline_execution_contract": "output/.raw/pipeline_execution_contract_validation.json",
            "engine_signal_contract": "output/.raw/engine_signal_contract_validation.json",
        },
    }


def render_report(packet: dict[str, Any]) -> str:
    mission = packet.get("mission_control", {})
    lines = [
        "# Nexora Operator Packet",
        "",
        f"- generated_at: `{packet.get('meta', {}).get('generated_at')}`",
        f"- release_readiness: `{mission.get('release_readiness')}`",
        f"- platform_release_readiness: `{mission.get('platform_release_readiness')}`",
        f"- target_quality_gate_status: `{mission.get('target_quality_gate_status')}`",
        f"- quality_gate: `{mission.get('quality_gate')}`",
        f"- quality_gate_passed: `{mission.get('quality_gate_passed')}`",
        f"- quality_gate_interpretation: `{mission.get('quality_gate_interpretation')}`",
        f"- ecosystem_signal_status: `{mission.get('ecosystem_signal_status')}`",
        f"- react_universal_ready: `{mission.get('react_universal_ready')}`",
        f"- mcp_agent_surface: `{mission.get('mcp_agent_surface')}`",
        f"- release_proof: `{mission.get('release_proof')}`",
        f"- report_freshness: `{mission.get('report_freshness')}`",
        f"- watchdog_integrity: `{mission.get('watchdog_integrity')}`",
        f"- hitl_active_approvals: `{mission.get('hitl_active_approvals')}`",
        f"- hitl_recorded_decisions: `{mission.get('hitl_recorded_decisions')}`",
        f"- hitl_open_decision_requests: `{mission.get('hitl_open_decision_requests')}`",
        f"- hitl_governance: `{mission.get('hitl_governance')}`",
        f"- hitl_lifecycle_smoke: `{mission.get('hitl_lifecycle_smoke')}`",
        f"- agent_response_contract: `{mission.get('agent_response_contract')}`",
        f"- agent_response_ledger_entries: `{mission.get('agent_response_ledger_entries')}`",
        f"- agent_response_ledger_invalid: `{mission.get('agent_response_ledger_invalid')}`",
        f"- agent_handoff_ready: `{mission.get('agent_handoff_ready')}`",
        f"- surface_inventory: `{mission.get('surface_inventory')}`",
        f"- capability_activation_plan: `{mission.get('capability_activation_plan')}`",
        f"- capability_scheduler_enforced: `{mission.get('capability_scheduler_enforced')}`",
        f"- pipeline_execution_contract: `{mission.get('pipeline_execution_contract')}`",
        "",
        "## Required Answer Shape",
        "",
    ]
    for field in packet.get("required_answer_shape", []):
        lines.append(f"- `{field}`")

    lines.extend(["", "## Active Human Approval Gates", "", "| Gate | Risk | Agent Rule |", "|---|---|---|"])
    gates = packet.get("active_human_approval_gates", [])
    if gates:
        for gate in gates:
            lines.append(f"| `{gate.get('id')}` | `{gate.get('risk')}` | {gate.get('agent_rule')} |")
    else:
        lines.append("| `none` | `low` | No active human approval gates. |")

    lines.extend(["", "## Recommended First Actions", ""])
    for action in packet.get("recommended_first_actions", []):
        lines.append(f"- {action}")

    lines.extend(
        [
            "",
            "## Agent Action Directives",
            "",
            "| Intent | Actionability | Target Files | Action | Validation |",
            "|---|---|---|---|---|",
        ]
    )
    for directive in packet.get("agent_action_directives", []):
        targets = ", ".join(f"`{item}`" for item in directive.get("target_files", [])[:6]) or "`none`"
        validation_tools = directive.get("validation_tools") if isinstance(directive.get("validation_tools"), list) else []
        validations = ", ".join(f"`{item.get('call')}`" for item in validation_tools[:3] if isinstance(item, dict)) or "`none`"
        actionability = str(directive.get("actionability") or "orientation_only")
        mutation_proposed = actionability == "actionable_proposal" and bool(directive.get("mutation_proposed"))
        action = (
            str(directive.get("action") or "")
            if mutation_proposed
            else "Inspect bounded evidence only; no mutation is proposed by this directive."
        ).replace("|", "\\|")
        lines.append(f"| `{directive.get('intent')}` | `{actionability}` | {targets} | {action} | {validations} |")

    lines.extend(["", "## Relevant Capabilities", "", "| Capability | Claim Boundary | Trusted Artifacts |", "|---|---|---|"])
    capabilities = packet.get("relevant_capabilities", [])
    if capabilities:
        for capability in capabilities:
            artifacts = ", ".join(f"`{item}`" for item in capability.get("artifacts_to_trust", [])[:8])
            lines.append(
                f"| `{capability.get('id')}` | {capability.get('claim_boundary')} | {artifacts} |"
            )
    else:
        lines.append("| `none` | No capability registry entries available. |  |")

    execution_contract = packet.get("pipeline_execution_contract", {})
    lines.extend(["", "## Execution Modes", "", "| Mode | Profile | Scope | Claim Boundary |", "|---|---|---|---|"])
    for mode in execution_contract.get("execution_modes", []) if isinstance(execution_contract, dict) else []:
        lines.append(
            f"| `{mode.get('id')}` | `{mode.get('profile')}` | `{mode.get('scope')}` | `{mode.get('claim_boundary')}` |"
        )

    activation = packet.get("capability_activation", {})
    activation_summary = activation.get("summary", {}) if isinstance(activation, dict) else {}
    lines.extend(["", "## Capability Activation", ""])
    lines.append(f"- status: `{activation_summary.get('status')}`")
    lines.append(f"- planning_only: `{activation_summary.get('planning_only')}`")
    lines.append(f"- pipeline_scheduler_enforced: `{activation_summary.get('pipeline_scheduler_enforced')}`")
    lines.extend(["", "| Project | Capability | Status | Reason |", "|---|---|---|---|"])
    for project in activation.get("projects", []) if isinstance(activation, dict) else []:
        for capability in project.get("capabilities", []) if isinstance(project, dict) else []:
            lines.append(
                f"| `{project.get('project')}` | `{capability.get('id')}` | `{capability.get('status')}` | {capability.get('reason')} |"
            )

    ledger = packet.get("hitl_approval_ledger", {})
    lines.extend(["", "## HITL Approval Ledger", ""])
    lines.append(f"- entries: `{(ledger.get('summary') or {}).get('entries', 0)}`")
    lines.append(f"- active_approvals: `{(ledger.get('summary') or {}).get('active_approvals', 0)}`")

    requests = packet.get("hitl_decision_requests", {})
    lines.extend(["", "## HITL Decision Requests", ""])
    lines.append(f"- requests: `{(requests.get('summary') or {}).get('requests', 0)}`")
    lines.append(f"- open: `{(requests.get('summary') or {}).get('open', 0)}`")

    response_ledger = packet.get("agent_response_ledger", {})
    lines.extend(["", "## Agent Response Ledger", ""])
    lines.append(f"- entries: `{(response_ledger.get('summary') or {}).get('entries', 0)}`")
    lines.append(f"- invalid: `{(response_ledger.get('summary') or {}).get('invalid', 0)}`")

    lines.extend(["", "## Source Artifacts", ""])
    for name, path in (packet.get("source_artifacts") or {}).items():
        lines.append(f"- `{name}`: `{path}`")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    packet = build_operator_packet()
    save_json_atomic(RAW_DIR / "nexora_operator_packet.json", packet)
    save_text_atomic(REPORTS_DIR / "nexora_operator_packet.md", render_report(packet))
    return packet


def main() -> int:
    packet = run()
    print(json.dumps(packet.get("mission_control", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

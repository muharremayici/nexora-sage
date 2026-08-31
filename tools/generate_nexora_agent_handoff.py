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
from tools.core.json_io import load_json_file


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict[str, Any]:
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def build_handoff() -> dict[str, Any]:
    operator = _load(RAW_DIR / "nexora_operator_packet.json")
    contract = _load(RAW_DIR / "nexora_agent_contract.json")
    mcp_surface = _load(RAW_DIR / "mcp_agent_surface_validation.json")
    mission = operator.get("mission_control", {}) if isinstance(operator.get("mission_control"), dict) else {}
    response_fields = operator.get("required_answer_shape") or (contract.get("response_protocol", {}) or {}).get("required_fields", [])
    approval_gates = operator.get("active_human_approval_gates", [])
    relevant_capabilities = operator.get("relevant_capabilities", []) if isinstance(operator.get("relevant_capabilities"), list) else []

    return {
        "meta": {
            "kind": "nexora_agent_handoff",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_nexora_agent_handoff",
            "workspace_root": str(CODE_MAPS_DIR),
        },
        "mission_control": mission,
        "response_fields": response_fields,
        "active_human_approval_gates": approval_gates,
        "relevant_capabilities": relevant_capabilities,
        "mcp_surface": mcp_surface.get("summary", {}),
        "recommended_tool_order": [
            "get_operator_packet",
            "read relevant_capabilities before trusting artifacts or making product claims",
            "inspect_file / inspect_folder / inspect_symbol",
            "get_release_proof / get_react_universal_readiness / get_report_freshness",
            "create_hitl_decision_request when human approval is needed",
            "record_hitl_decision after the human decides",
            "record_agent_response for high-stakes answers",
        ],
    }


def render_handoff(payload: dict[str, Any]) -> str:
    mission = payload.get("mission_control", {})
    lines = [
        "# SAGE Agent Handoff",
        "",
        "You are an AI agent assisted by SAGE (Nexora SAGE). SAGE is your codebase intelligence substrate. You assist the human; the human owns authority and final approval.",
        "",
        "## Current Posture",
        "",
        f"- release_readiness: `{mission.get('release_readiness')}`",
        f"- quality_gate: `{mission.get('quality_gate')}`",
        f"- ecosystem_signal_status: `{mission.get('ecosystem_signal_status')}`",
        f"- react_universal_ready: `{mission.get('react_universal_ready')}`",
        f"- mcp_agent_surface: `{mission.get('mcp_agent_surface')}`",
        f"- hitl_governance: `{mission.get('hitl_governance')}`",
        f"- hitl_lifecycle_smoke: `{mission.get('hitl_lifecycle_smoke')}`",
        f"- agent_response_contract: `{mission.get('agent_response_contract')}`",
        f"- watchdog_integrity: `{mission.get('watchdog_integrity')}`",
        "",
        "## Required Answer Shape",
        "",
    ]
    for field in payload.get("response_fields", []):
        lines.append(f"- `{field}`")

    lines.extend(
        [
            "",
            "Every high-stakes answer must separate evidence from inference and must state whether human approval is required.",
            "",
            "## Human Approval Gates",
            "",
            "| Gate | Risk | Rule |",
            "|---|---|---|",
        ]
    )
    gates = payload.get("active_human_approval_gates", [])
    if gates:
        for gate in gates:
            lines.append(f"| `{gate.get('id')}` | `{gate.get('risk')}` | {gate.get('agent_rule')} |")
    else:
        lines.append("| `none` | `low` | No active approval gates. |")

    lines.extend(["", "## Tool Order", ""])
    for item in payload.get("recommended_tool_order", []):
        lines.append(f"- {item}")

    lines.extend(["", "## Relevant Capabilities", "", "| Capability | Claim Boundary | Trusted Artifacts |", "|---|---|---|"])
    capabilities = payload.get("relevant_capabilities", [])
    if capabilities:
        for capability in capabilities:
            artifacts = ", ".join(f"`{item}`" for item in capability.get("artifacts_to_trust", [])[:8])
            lines.append(
                f"| `{capability.get('id')}` | {capability.get('claim_boundary')} | {artifacts} |"
            )
    else:
        lines.append("| `none` | No operator capability context available. |  |")

    lines.extend(
        [
            "",
            "## Operating Rule",
            "",
            "Do not run mutation, merge/import, delete, move, or large refactor actions unless the human explicitly approves and the decision is recorded in the HITL approval ledger.",
        ]
    )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_handoff()
    save_json_atomic(RAW_DIR / "nexora_agent_handoff.json", payload)
    save_text_atomic(REPORTS_DIR / "nexora_agent_handoff.md", render_handoff(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload.get("mission_control", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.validate_mcp_agent_surface import REQUIRED_AGENT_TOOLS


AGENT_SURFACE_CONTRACT_PATH = CODE_MAPS_DIR / "config" / "agent_surface_contract.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_readiness_contract() -> dict[str, Any]:
    contract = load_json_file(AGENT_SURFACE_CONTRACT_PATH, {})
    readiness = contract.get("ai_agent_readiness", {}) if isinstance(contract, dict) else {}
    return readiness if isinstance(readiness, dict) else {}


def _contract_list(section: dict[str, Any], key: str) -> list[str]:
    values = section.get(key, []) if isinstance(section, dict) else []
    return [str(value) for value in values if str(value).strip()] if isinstance(values, list) else []


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        base = _decorator_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _mcp_tools() -> set[str]:
    server_path = CODE_MAPS_DIR / "tools" / "mcp" / "server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8"))
    tools: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        decorators = {_decorator_name(decorator) for decorator in node.decorator_list}
        if "mcp.tool" in decorators:
            tools.add(node.name)
    return tools


def _check(name: str, passed: bool, details: Any, enforced: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "enforced": bool(enforced),
        "details": details,
    }


def _summary_failed(payload: dict[str, Any]) -> int | None:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    if not isinstance(summary, dict):
        return None
    for key in ("failed_checks", "failed", "blocking_failures"):
        if key in summary:
            try:
                return int(summary.get(key) or 0)
            except (TypeError, ValueError):
                return None
    return None


def build_ai_agent_readiness_report() -> dict[str, Any]:
    readiness_contract = _agent_readiness_contract()
    recommended_first_calls = _contract_list(readiness_contract, "recommended_first_calls")
    required_operation_tools = set(_contract_list(readiness_contract, "required_operation_tools"))
    agent_operating_contract = readiness_contract.get("agent_operating_contract", {})
    if not isinstance(agent_operating_contract, dict):
        agent_operating_contract = {}

    tools = _mcp_tools()
    mcp_surface = load_json_file(RAW_DIR / "mcp_agent_surface_validation.json", {})
    contextos_signals = load_json_file(RAW_DIR / "signals.json", {})
    contextos_contract = load_json_file(RAW_DIR / "contextos_contract_validation.json", {})
    contextos_signal_validation = load_json_file(RAW_DIR / "contextos_signal_validation.json", {})
    hitl_governance = load_json_file(RAW_DIR / "hitl_governance_validation.json", {})
    hitl_lifecycle = load_json_file(RAW_DIR / "hitl_lifecycle_smoke.json", {})
    agent_contract = load_json_file(RAW_DIR / "nexora_agent_contract.json", {})
    operator_packet = load_json_file(RAW_DIR / "nexora_operator_packet.json", {})
    response_validation = load_json_file(RAW_DIR / "nexora_agent_response_validation.json", {})
    release_readiness = load_json_file(RAW_DIR / "release_readiness.json", {})
    test_gap = load_json_file(RAW_DIR / "test_gap_report.json", {})

    mcp_missing = sorted(REQUIRED_AGENT_TOOLS - tools)
    operation_missing = sorted(required_operation_tools - tools)
    signal_summary = contextos_signals.get("summary", {}) if isinstance(contextos_signals, dict) else {}
    active_signals = contextos_signals.get("active_signals", []) if isinstance(contextos_signals, dict) else []
    active_focus_count = len(active_signals) if isinstance(active_signals, list) else 0

    contextos_contract_failed = _summary_failed(contextos_contract)
    contextos_signal_failed = _summary_failed(contextos_signal_validation)
    hitl_governance_failed = _summary_failed(hitl_governance)
    hitl_lifecycle_failed = _summary_failed(hitl_lifecycle)
    response_failed = _summary_failed(response_validation)

    checks = [
        _check(
            "mcp_required_agent_tools_present",
            not mcp_missing and mcp_surface.get("summary", {}).get("status") in ("PASS", None),
            {"missing": mcp_missing, "total_mcp_tools": len(tools)},
        ),
        _check(
            "mcp_operation_tools_present",
            not operation_missing,
            {"missing": operation_missing},
        ),
        _check(
            "contextos_active_focus_available",
            active_focus_count > 0 and signal_summary.get("source_mode") != "unavailable",
            {
                "active_focus_count": active_focus_count,
                "source_mode": contextos_signals.get("meta", {}).get("source_mode"),
                "is_scene_pivot": signal_summary.get("is_scene_pivot"),
            },
            enforced=False,
        ),
        _check(
            "contextos_contracts_pass",
            contextos_contract_failed == 0 and contextos_signal_failed == 0,
            {
                "contextos_contract_failed": contextos_contract_failed,
                "contextos_signal_failed": contextos_signal_failed,
            },
        ),
        _check(
            "hitl_governance_ready",
            hitl_governance_failed in (0, None) and hitl_lifecycle_failed in (0, None),
            {
                "hitl_governance_failed": hitl_governance_failed,
                "hitl_lifecycle_failed": hitl_lifecycle_failed,
            },
            enforced=bool(hitl_governance or hitl_lifecycle),
        ),
        _check(
            "agent_contract_and_operator_packet_available",
            bool(agent_contract) and bool(operator_packet),
            {
                "agent_contract": bool(agent_contract),
                "operator_packet": bool(operator_packet),
            },
        ),
        _check(
            "agent_response_contract_available",
            response_failed in (0, None),
            {"response_validation_failed": response_failed},
            enforced=bool(response_validation),
        ),
        _check(
            "release_readiness_snapshot_available",
            release_readiness.get("readiness") in ("PRODUCTION_READY", "NOT_READY"),
            {"readiness": release_readiness.get("readiness")},
            enforced=False,
        ),
        _check(
            "test_gap_report_available_for_agent_test_selection",
            bool(test_gap.get("summary")),
            {"summary": test_gap.get("summary", {})},
            enforced=False,
        ),
    ]
    blocking_failures = [check for check in checks if check.get("enforced") and not check.get("passed")]
    warning_failures = [check for check in checks if not check.get("enforced") and not check.get("passed")]
    status = "READY" if not blocking_failures else "NOT_READY"
    if not blocking_failures and warning_failures:
        status = "READY_WITH_WARNINGS"

    return {
        "meta": {
            "kind": "ai_agent_readiness_report",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_ai_agent_readiness_report",
        },
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check.get("passed")),
            "blocking_failures": len(blocking_failures),
            "warnings": len(warning_failures),
            "mcp_tools": len(tools),
            "active_focus_files": active_focus_count,
            "required_operation_tools": len(required_operation_tools),
            "missing_operation_tools": operation_missing,
        },
        "recommended_first_calls": recommended_first_calls,
        "checks": checks,
        "agent_operating_contract": agent_operating_contract,
        "evidence_artifacts": [
            "config/agent_surface_contract.json",
            "output/.raw/mcp_agent_surface_validation.json",
            "output/.raw/signals.json",
            "output/.raw/contextos_contract_validation.json",
            "output/.raw/contextos_signal_validation.json",
            "output/.raw/nexora_agent_contract.json",
            "output/.raw/nexora_operator_packet.json",
            "output/.raw/test_gap_report.json",
        ],
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# AI-Agent Readiness Report v1",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed')}/{summary.get('checks')}`",
        f"- blocking_failures: `{summary.get('blocking_failures')}`",
        f"- warnings: `{summary.get('warnings')}`",
        f"- mcp_tools: `{summary.get('mcp_tools')}`",
        f"- active_focus_files: `{summary.get('active_focus_files')}`",
        "",
        "## Recommended First Calls",
        "",
    ]
    for call in payload.get("recommended_first_calls", []):
        lines.append(f"- `{call}`")
    lines.extend(["", "## Checks", "", "| Check | Result | Enforced | Details |", "|---|---|---|---|"])
    for check in payload.get("checks", []):
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        details = details.replace("|", "\\|")
        lines.append(
            f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | {check.get('enforced')} | {details} |"
        )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_ai_agent_readiness_report()
    save_json_atomic(RAW_DIR / "ai_agent_readiness_report.json", payload)
    save_text_atomic(REPORTS_DIR / "ai_agent_readiness_report.md", render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("blocking_failures", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_trust import build_artifact_trust_summary
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.mcp import server as mcp_server


CONTRACT_PATH = CONFIG_DIR / "agent_packet_chain_integrity_contract.json"
RAW_OUTPUT_PATH = RAW_DIR / "agent_packet_chain_integrity_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "agent_packet_chain_integrity_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any, *, severity: str = "error") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "severity": severity,
        "details": details,
    }


def _load_contract() -> dict[str, Any]:
    payload = load_json_file(CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _list(value: Any) -> list[str]:
    return [str(item) for item in value if str(item)] if isinstance(value, list) else []


def _section(contract: dict[str, Any], key: str) -> dict[str, Any]:
    value = contract.get(key, {})
    return value if isinstance(value, dict) else {}


def _contains_all(text: str, values: Any) -> bool:
    return all(item in text for item in _list(values))


def _contains_none(text: str, values: Any) -> bool:
    return all(item not in text for item in _list(values))


def _contains_any(text: str, values: Any) -> bool:
    items = _list(values)
    return not items or any(item in text for item in items)


def _sample(name: str, producer) -> dict[str, Any]:
    try:
        body = producer()
        return {"name": name, "ok": True, "body": str(body)}
    except Exception as exc:
        return {"name": name, "ok": False, "body": str(exc)}


def build_validation() -> dict[str, Any]:
    contract = _load_contract()
    server_text = _read(ROOT / "tools" / "mcp" / "server.py")
    contextos_text = _read(ROOT / "tools" / "core" / "contextos_mcp.py")
    orchestrator_text = _read(ROOT / "tools" / "orchestrators" / "orchestrator.py")
    skill_text = _read(ROOT / "SKILL.md")
    runbook_text = _read(ROOT / "docs" / "AI_AGENT_HITL_RUNBOOK.md")
    trust_summary = build_artifact_trust_summary(RAW_DIR)

    surgical = _sample("surgical_operation_packet", lambda: mcp_server.get_surgical_operation_packet(max_signals=3))
    queue = _sample("violation_work_queue", lambda: mcp_server.get_violation_work_queue(page_size=5))
    active = _sample("active_signals", lambda: mcp_server.get_active_signals(max_files=2, max_chars_per_file=240))
    confidence = _sample("confidence_score", lambda: mcp_server.get_confidence_score("src/App.tsx"))

    surgical_body = surgical["body"]
    queue_body = queue["body"]
    active_body = active["body"]
    confidence_body = confidence["body"]
    work_queue_contract = _section(contract, "work_queue_body_contract")
    surgical_contract = _section(contract, "surgical_brief_contract")
    idle_contract = _section(contract, "active_signals_idle_contract")
    confidence_contract = _section(contract, "confidence_brief_contract")
    contextos_contract = _section(contract, "contextos_default_brief_contract")
    pipeline_contract = _section(contract, "pipeline_order_contract")
    docs_contract = _section(contract, "docs_idle_contract")
    target_root_contract = _section(contract, "target_root_contract")

    checks = [
        _check(
            "agent_packet_chain_integrity_contract_exists",
            CONTRACT_PATH.exists() and contract.get("status") == "active",
            {"path": str(CONTRACT_PATH), "status": contract.get("status")},
        ),
        _check(
            "artifact_trust_chain_ready_for_agent_surface",
            trust_summary.get("status") in set(_list(contract.get("artifact_trust_allowed_statuses"))),
            {
                "status": trust_summary.get("status"),
                "failures": trust_summary.get("failures", [])[:5],
                "warnings": trust_summary.get("warnings", [])[:5],
                "scope": trust_summary.get("scope", {}),
            },
        ),
        _check(
            "work_queue_refreshes_or_fails_closed_on_stale_chain",
            _contains_all(server_text, contract.get("required_server_contracts")),
            "work queue must check artifact trust before exposing target-repository debt items",
        ),
        _check(
            "work_queue_groups_raw_rows_into_agent_work_items",
            _contains_all(queue_body, work_queue_contract.get("must_include"))
            and _contains_none(queue_body, work_queue_contract.get("must_not_include"))
            and _contains_any(queue_body, work_queue_contract.get("must_include_any")),
            queue_body[:1200],
            severity="warning",
        ),
        _check(
            "surgical_brief_exposes_validation_without_debug_refs",
            surgical["ok"]
            and _contains_all(surgical_body, surgical_contract.get("must_include"))
            and _contains_none(surgical_body, surgical_contract.get("must_not_include")),
            surgical_body[:1200],
        ),
        _check(
            "active_signals_idle_state_is_fail_closed",
            active["ok"]
            and (
                str(idle_contract.get("idle_marker", "")) not in active_body
                or _contains_all(active_body, idle_contract.get("fail_closed_if_idle_must_include"))
            ),
            active_body[:1200],
        ),
        _check(
            "confidence_brief_uses_merge_safety_not_ambiguous_risk_language",
            confidence["ok"]
            and _contains_all(confidence_body, confidence_contract.get("must_include"))
            and _contains_none(confidence_body, confidence_contract.get("must_not_include")),
            confidence_body[:900],
        ),
        _check(
            "contextos_renderer_keeps_default_brief_target_repo_friendly",
            _contains_all(contextos_text, contextos_contract.get("contextos_must_include"))
            and _contains_all(
                _read(
                ROOT / "tools" / "generate_agent_surface_quality_review.py"
                ),
                contextos_contract.get("quality_review_must_include"),
            ),
            "default brief must hide internal graph/provenance details while preserving debug projection",
        ),
        _check(
            "pipeline_refreshes_operator_packet_before_ai_context",
            _contains_all(orchestrator_text, pipeline_contract.get("orchestrator_must_include")),
            "AI Context Generator must read a freshly produced operator packet and ContextOS signal surface, not stale compatibility artifacts.",
        ),
        _check(
            "skill_and_runbook_document_idle_contextos_semantics",
            _contains_all(skill_text, docs_contract.get("skill_must_include"))
            and _contains_all(runbook_text, docs_contract.get("runbook_must_include")),
            "agent-facing docs must not let agents interpret missing ContextOS signals as no-risk proof",
        ),
        _check(
            "target_root_isolation_and_fail_closed_contract_present",
            _contains_all(server_text, target_root_contract.get("server_must_include"))
            and _contains_all(skill_text, target_root_contract.get("skill_must_include"))
            and _contains_any(skill_text, target_root_contract.get("skill_must_include_any")),
            "MCP target_root tools must read isolated target artifacts and fail closed when artifacts are absent",
        ),
    ]

    failures = [row for row in checks if not row["passed"] and row.get("severity") == "error"]
    warnings = [row for row in checks if not row["passed"] and row.get("severity") == "warning"]
    payload = {
        "meta": {
            "kind": "agent_packet_chain_integrity_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_agent_packet_chain_integrity",
        },
        "summary": {
            "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
            "total_checks": len(checks),
            "passed_checks": sum(1 for row in checks if row["passed"]),
            "failed_checks": len(failures),
            "warning_checks": len(warnings),
        },
        "artifact_trust": {
            "status": trust_summary.get("status"),
            "scope": trust_summary.get("scope"),
            "freshness": trust_summary.get("freshness"),
        },
        "contract": {
            "path": str(CONTRACT_PATH),
            "status": contract.get("status"),
        },
        "checks": checks,
        "sample_sizes": {
            "surgical_operation_packet": len(surgical_body),
            "violation_work_queue": len(queue_body),
            "active_signals": len(active_body),
            "confidence_score": len(confidence_body),
        },
    }
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Agent Packet Chain Integrity Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- total_checks: `{summary.get('total_checks')}`",
        f"- passed_checks: `{summary.get('passed_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        f"- warning_checks: `{summary.get('warning_checks')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Severity | Details |",
        "|---|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        details = details.replace("\n", " ")[:500]
        escaped_details = details.replace("|", "\\|")
        lines.append(
            f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | `{check.get('severity')}` | {escaped_details} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_file


RETENTION_TARGET_ID = "mcp_call_telemetry"
DEFAULT_LEDGER_NAME = "mcp_call_telemetry_ledger.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retention_policy() -> dict[str, Any]:
    payload = load_json_file(CONFIG_DIR / "runtime_output_retention_policy.json", {})
    targets = payload.get("retention_targets") if isinstance(payload, dict) else []
    for row in targets if isinstance(targets, list) else []:
        if isinstance(row, dict) and row.get("id") == RETENTION_TARGET_ID:
            return row
    return {
        "id": RETENTION_TARGET_ID,
        "path": f"output/.raw/{DEFAULT_LEDGER_NAME}",
        "max_entries": 200,
        "max_summary_tools": 50,
        "enabled": False,
    }


def _ledger_path(policy: dict[str, Any]) -> Path:
    configured = str(policy.get("path") or f"output/.raw/{DEFAULT_LEDGER_NAME}").replace("\\", "/")
    return RAW_DIR / Path(configured).name


def _load_ledger(path: Path) -> dict[str, Any]:
    payload = load_json_file(path, {})
    if not isinstance(payload, dict):
        payload = {}
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    summary = payload.get("summary_by_tool") if isinstance(payload.get("summary_by_tool"), dict) else {}
    return {
        "meta": payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
        "policy": payload.get("policy") if isinstance(payload.get("policy"), dict) else {},
        "summary_by_tool": summary,
        "entries": entries,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * max(0.0, min(1.0, percentile))
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[int(rank)], 2)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def _wait_guidance(p50_ms: float, p95_ms: float) -> dict[str, Any]:
    p50_seconds = max(1, math.ceil(float(p50_ms or 0) / 1000))
    p95_seconds = max(1, math.ceil(float(p95_ms or 0) / 1000))
    return {
        "check_after_seconds": max(2, p50_seconds * 2),
        "attention_after_seconds": max(10, p95_seconds * 3),
        "agent_rule": "Use this local-machine hint only for wait cadence; do not treat it as repository evidence.",
    }


def build_summary(entries: list[dict[str, Any]], *, max_summary_tools: int = 50) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in entries:
        if not isinstance(row, dict):
            continue
        tool_name = str(row.get("tool_name") or "").strip()
        if not tool_name:
            continue
        grouped.setdefault(tool_name, []).append(row)

    summary: dict[str, Any] = {}
    for tool_name in sorted(grouped)[: max(1, int(max_summary_tools or 50))]:
        rows = grouped[tool_name]
        durations = [float(row.get("duration_ms") or 0) for row in rows]
        last = rows[-1]
        p50 = _percentile(durations, 0.50)
        p95 = _percentile(durations, 0.95)
        summary[tool_name] = {
            "count": len(rows),
            "last_status": last.get("status") or "unknown",
            "last_duration_ms": round(float(last.get("duration_ms") or 0), 2),
            "p50_duration_ms": p50,
            "p95_duration_ms": p95,
            "max_duration_ms": round(max(durations) if durations else 0, 2),
            "last_payload_chars": int(last.get("payload_chars") or 0),
            "last_fail_closed_reason": last.get("fail_closed_reason") or "",
            "expected_wait": _wait_guidance(p50, p95),
        }
    return summary


def record_mcp_call_result(
    tool_name: str,
    started: float,
    result: Any,
    *,
    status: str = "ok",
    fail_closed_reason: str = "",
) -> None:
    policy = _retention_policy()
    if not bool(policy.get("enabled", True)):
        return
    max_entries = max(1, int(policy.get("max_entries") or 200))
    max_summary_tools = max(1, int(policy.get("max_summary_tools") or 50))
    path = _ledger_path(policy)
    ledger = _load_ledger(path)
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    text = result if isinstance(result, str) else str(result)
    entries.append(
        {
            "tool_name": str(tool_name or "").strip() or "unknown",
            "recorded_at": _utc_now(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "status": str(status or "ok"),
            "payload_chars": len(text or ""),
            "fail_closed_reason": str(fail_closed_reason or ""),
        }
    )
    entries = entries[-max_entries:]
    payload = {
        "meta": {
            "kind": "mcp_call_telemetry_ledger",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "claim_boundary": "local MCP call timing and payload-size guidance, not repository correctness evidence",
        },
        "policy": {
            "retention_target_id": RETENTION_TARGET_ID,
            "max_entries": max_entries,
            "max_summary_tools": max_summary_tools,
        },
        "summary_by_tool": build_summary(entries, max_summary_tools=max_summary_tools),
        "entries": entries,
    }
    save_json_atomic(path, payload)


def load_mcp_call_telemetry(tool_name: str = "") -> dict[str, Any]:
    policy = _retention_policy()
    path = _ledger_path(policy)
    ledger = _load_ledger(path)
    ledger.setdefault(
        "meta",
        {
            "kind": "mcp_call_telemetry_ledger",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "claim_boundary": "local MCP call timing and payload-size guidance, not repository correctness evidence",
        },
    )
    ledger.setdefault(
        "policy",
        {
            "retention_target_id": RETENTION_TARGET_ID,
            "max_entries": int(policy.get("max_entries") or 200),
            "max_summary_tools": int(policy.get("max_summary_tools") or 50),
        },
    )
    if tool_name:
        summary = ledger.get("summary_by_tool") if isinstance(ledger.get("summary_by_tool"), dict) else {}
        entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
        ledger["summary_by_tool"] = {tool_name: summary.get(tool_name, {})}
        ledger["entries"] = [row for row in entries if isinstance(row, dict) and row.get("tool_name") == tool_name]
    return ledger


def render_mcp_call_telemetry_brief(tool_name: str = "") -> str:
    ledger = load_mcp_call_telemetry(tool_name)
    summary = ledger.get("summary_by_tool") if isinstance(ledger.get("summary_by_tool"), dict) else {}
    lines = [
        "# MCP Call Telemetry",
        "",
        "```yaml",
        "status: " + ("available" if summary else "no_mcp_call_samples"),
        "claim_boundary: \"Local-machine wait guidance only; not repository proof.\"",
        "tool_filter: " + (f"\"{tool_name}\"" if tool_name else "\"\""),
        "tools:",
    ]
    if summary:
        for name, row in summary.items():
            if not isinstance(row, dict):
                continue
            wait = row.get("expected_wait") if isinstance(row.get("expected_wait"), dict) else {}
            lines.extend(
                [
                    "  - name: " + f"\"{name}\"",
                    f"    count: {int(row.get('count') or 0)}",
                    "    last_status: " + f"\"{row.get('last_status') or 'unknown'}\"",
                    f"    last_duration_ms: {float(row.get('last_duration_ms') or 0)}",
                    f"    p50_duration_ms: {float(row.get('p50_duration_ms') or 0)}",
                    f"    p95_duration_ms: {float(row.get('p95_duration_ms') or 0)}",
                    f"    last_payload_chars: {int(row.get('last_payload_chars') or 0)}",
                    "    last_fail_closed_reason: " + f"\"{row.get('last_fail_closed_reason') or ''}\"",
                    "    expected_wait:",
                    f"      check_after_seconds: {int(wait.get('check_after_seconds') or 0)}",
                    f"      attention_after_seconds: {int(wait.get('attention_after_seconds') or 0)}",
                ]
            )
    else:
        lines.append("  []")
    lines.extend(
        [
            "agent_rule:",
            "  - Use expected_wait to decide when to poll or escalate a long-running MCP call.",
            "  - Do not use this telemetry to infer repository health or code correctness.",
            "```",
            "",
        ]
    )
    return "\n".join(lines)

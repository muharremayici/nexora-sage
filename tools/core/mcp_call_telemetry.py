from __future__ import annotations

import math
import os
import re
import time
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import (
    CONFIG_DIR,
    MCP_CALL_TELEMETRY_DB,
    MCP_HONESTY_TELEMETRY_FILE,
)
from tools.core.execution_identity import SAGE_OPERATOR_ACTOR_PROFILE
from tools.core.json_io import load_json_file


RETENTION_TARGET_ID = "mcp_call_telemetry"
DEFAULT_LEDGER_NAME = "mcp_call_telemetry.db"
MCP_OPERATIONAL_DB_ENV = "SAGE_MCP_OPERATIONAL_DB_PATH"
_INACTIVE_CALL_CAPTURE = object()
_ACTIVE_CALL_RESULT: ContextVar[Any] = ContextVar(
    "sage_mcp_call_result",
    default=_INACTIVE_CALL_CAPTURE,
)


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
        "path": "output/.operational/mcp/mcp_call_telemetry.db",
        "max_entries": 200,
        "max_summary_tools": 50,
        "enabled": False,
    }


def mcp_operational_db_path() -> Path:
    """Return the product-global MCP telemetry authority, never a target RAW_DIR."""
    override = str(os.environ.get(MCP_OPERATIONAL_DB_ENV) or "").strip()
    if not override:
        return MCP_CALL_TELEMETRY_DB
    candidate = Path(override).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{MCP_OPERATIONAL_DB_ENV} must be an absolute path")
    return candidate.resolve()


def mcp_operational_honesty_path() -> Path:
    """Keep MCP-call honesty events beside the selected operational database."""
    db_path = mcp_operational_db_path()
    if db_path == MCP_CALL_TELEMETRY_DB:
        return MCP_HONESTY_TELEMETRY_FILE
    return db_path.parent / MCP_HONESTY_TELEMETRY_FILE.name


def activate_mcp_call_capture() -> Token[Any]:
    """Begin one call-local result capture without writing persistent state."""
    return _ACTIVE_CALL_RESULT.set(None)


def reset_mcp_call_capture(token: Token[Any]) -> None:
    _ACTIVE_CALL_RESULT.reset(token)


def current_mcp_call_capture() -> dict[str, Any]:
    captured = _ACTIVE_CALL_RESULT.get()
    return dict(captured) if isinstance(captured, dict) else {}


def _safe_label(value: Any, *, default: str, limit: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)[:limit] or default


def _result_payload_chars(result: Any) -> int:
    if result is None:
        return 0
    if isinstance(result, str):
        return len(result)
    if isinstance(result, (list, tuple)):
        texts = [str(getattr(item, "text", "")) for item in result if getattr(item, "text", None) is not None]
        if texts:
            return sum(len(text) for text in texts)
    return len(str(result))


def _target_mode(profile: str, arguments: dict[str, Any]) -> str:
    if str(arguments.get("target_root") or "").strip():
        return "explicit_target"
    if str(profile or "").strip() == SAGE_OPERATOR_ACTOR_PROFILE:
        return "sage_self"
    return "default_repository"


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
            "last_target_mode": last.get("target_mode") or "unknown",
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
    """Capture bounded result metadata; the outer MCP wrapper performs the single durable write."""
    if _ACTIVE_CALL_RESULT.get() is _INACTIVE_CALL_CAPTURE:
        return
    _ACTIVE_CALL_RESULT.set(
        {
            "tool_name": _safe_label(tool_name, default="unknown", limit=160),
            "duration_ms": round(max(0.0, (time.perf_counter() - started) * 1000), 2),
            "status": _safe_label(status, default="unknown", limit=80),
            "payload_chars": _result_payload_chars(result),
            "fail_closed_reason": _safe_label(fail_closed_reason, default="none", limit=120),
        }
    )


def persist_completed_mcp_call(
    *,
    tool_name: str,
    profile: str,
    arguments: dict[str, Any],
    started: float,
    outcome: str,
    failure_layer: str,
    trace_id: str | None,
    result: Any = None,
) -> dict[str, Any] | None:
    """Persist one authoritative MCP call event in the product-global operational database."""
    policy = _retention_policy()
    if not bool(policy.get("enabled", True)):
        return None
    captured = current_mcp_call_capture()
    captured_matches = captured.get("tool_name") == _safe_label(tool_name, default="unknown", limit=160)
    payload_chars = int(captured.get("payload_chars") or 0) if captured_matches else _result_payload_chars(result)
    result_status = (
        str(captured.get("status") or "unknown")
        if captured_matches
        else ("ok" if outcome == "success" else "error")
    )
    fail_closed_reason = str(captured.get("fail_closed_reason") or "none") if captured_matches else "none"

    from tools.core.governance_trace import record_mcp_tool_trace

    return record_mcp_tool_trace(
        tool_name=tool_name,
        profile=profile,
        arguments=arguments if isinstance(arguments, dict) else {},
        started=started,
        outcome=outcome,
        failure_layer=failure_layer,
        trace_id=trace_id,
        payload_chars=payload_chars,
        result_status=result_status,
        fail_closed_reason=fail_closed_reason,
        target_mode=_target_mode(profile, arguments if isinstance(arguments, dict) else {}),
        db_path=mcp_operational_db_path(),
        max_events=max(1, int(policy.get("max_entries") or 200)),
    )


def _entry_from_trace(row: dict[str, Any]) -> dict[str, Any]:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    fail_closed_reason = str(details.get("fail_closed_reason") or "")
    return {
        "trace_id": str(row.get("trace_id") or ""),
        "tool_name": str(row.get("tool_name") or "unknown"),
        "recorded_at": str(row.get("recorded_at") or ""),
        "duration_ms": round(float(row.get("latency_ms") or 0), 2),
        "status": str(details.get("result_status") or row.get("outcome") or "unknown"),
        "outcome": str(row.get("outcome") or "unknown"),
        "failure_layer": str(row.get("failure_layer") or "unknown"),
        "payload_chars": max(0, int(details.get("payload_chars") or 0)),
        "fail_closed_reason": "" if fail_closed_reason == "none" else fail_closed_reason,
        "target_mode": str(details.get("target_mode") or "unknown"),
    }


def load_mcp_call_telemetry(tool_name: str = "") -> dict[str, Any]:
    policy = _retention_policy()
    max_entries = max(1, int(policy.get("max_entries") or 200))
    max_summary_tools = max(1, int(policy.get("max_summary_tools") or 50))
    path = mcp_operational_db_path()
    rows: list[dict[str, Any]] = []
    if path.exists():
        from tools.core.governance_trace import load_trace_events

        rows = load_trace_events(
            event_type="mcp_tool_call",
            limit=max_entries,
            db_path=path,
            initialize_schema=False,
        )
    entries = [_entry_from_trace(row) for row in reversed(rows)]
    if tool_name:
        entries = [row for row in entries if row.get("tool_name") == tool_name]
    summary = build_summary(entries, max_summary_tools=max_summary_tools)
    return {
        "meta": {
            "kind": "mcp_call_telemetry_ledger",
            "version": "2.0.0",
            "generated_at": _utc_now(),
            "storage_authority": "product_global_operational",
            "physical_ssot": "SQLite governance_trace_events",
            "claim_boundary": "local MCP call timing and payload-size guidance, not repository correctness evidence",
        },
        "policy": {
            "retention_target_id": RETENTION_TARGET_ID,
            "max_entries": max_entries,
            "max_summary_tools": max_summary_tools,
            "target_response_bodies": "excluded",
            "target_paths": "excluded",
        },
        "summary_by_tool": summary,
        "entries": entries,
    }


def render_mcp_call_telemetry_brief(tool_name: str = "") -> str:
    ledger = load_mcp_call_telemetry(tool_name)
    summary = ledger.get("summary_by_tool") if isinstance(ledger.get("summary_by_tool"), dict) else {}
    lines = [
        "# MCP Call Telemetry",
        "",
        "```yaml",
        "status: " + ("available" if summary else "no_mcp_call_samples"),
        'storage_authority: "product_global_operational"',
        'claim_boundary: "Local-machine wait guidance only; not repository proof."',
        "tool_filter: " + (f'"{tool_name}"' if tool_name else '""'),
        "tools:",
    ]
    if summary:
        for name, row in summary.items():
            if not isinstance(row, dict):
                continue
            wait = row.get("expected_wait") if isinstance(row.get("expected_wait"), dict) else {}
            lines.extend(
                [
                    "  - name: " + f'"{name}"',
                    f"    count: {int(row.get('count') or 0)}",
                    "    last_status: " + f'"{row.get("last_status") or "unknown"}"',
                    f"    last_duration_ms: {float(row.get('last_duration_ms') or 0)}",
                    f"    p50_duration_ms: {float(row.get('p50_duration_ms') or 0)}",
                    f"    p95_duration_ms: {float(row.get('p95_duration_ms') or 0)}",
                    f"    last_payload_chars: {int(row.get('last_payload_chars') or 0)}",
                    "    last_fail_closed_reason: " + f'"{row.get("last_fail_closed_reason") or ""}"',
                    "    last_target_mode: " + f'"{row.get("last_target_mode") or "unknown"}"',
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

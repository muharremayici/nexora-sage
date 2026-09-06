from __future__ import annotations

import hashlib
import threading
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger


TELEMETRY_PATH = RAW_DIR / "honesty_telemetry.json"
_ACTIVE_TELEMETRY_PATH: ContextVar[Path | None] = ContextVar("sage_honesty_telemetry_path", default=None)
_LOCK = threading.RLock()
_WRITING = threading.local()
_MAX_EVENTS = 2000


def activate_honesty_telemetry_path(path: Path) -> Token[Path | None]:
    """Bind best-effort honesty events to one call-local operational namespace."""
    return _ACTIVE_TELEMETRY_PATH.set(Path(path).resolve())


def current_honesty_telemetry_path() -> Path:
    """Return the active call-local path or the target runtime default."""
    return _ACTIVE_TELEMETRY_PATH.get() or TELEMETRY_PATH


def reset_honesty_telemetry_path(token: Token[Path | None]) -> None:
    """Restore the previous honesty telemetry storage authority."""
    _ACTIVE_TELEMETRY_PATH.reset(token)


def _physical_ssot(path: Path) -> str:
    """Describe the writer actually selected by save_json_atomic for this path."""
    return (
        "sqlite_artifact_store"
        if Path(path).absolute().parent == Path(RAW_DIR).absolute()
        else "json_file"
    )


def event_time_window(events: list[dict[str, Any]]) -> dict[str, str | None]:
    first_seen: list[str] = []
    last_seen: list[str] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        first_seen_at = str(item.get("first_seen_at") or item.get("timestamp") or "")
        last_seen_at = str(item.get("last_seen_at") or item.get("timestamp") or "")
        if first_seen_at:
            first_seen.append(first_seen_at)
        if last_seen_at:
            last_seen.append(last_seen_at)
    return {
        "oldest_event_at": min(first_seen) if first_seen else None,
        "latest_event_at": max(last_seen) if last_seen else None,
    }


def _summary_status(events: list[dict[str, Any]], occurrences_by_category: dict[str, int]) -> tuple[str, str]:
    if not events:
        return "PASS", "No honesty telemetry events have been recorded."
    severities = {str(item.get("severity") or "warning").lower() for item in events if isinstance(item, dict)}
    if severities & {"error", "critical"}:
        return "ATTENTION", "Honesty telemetry recorded error or critical events; review before widening claims."
    categories = ", ".join(f"{key}={value}" for key, value in sorted(occurrences_by_category.items()))
    return "ATTENTION", f"Honesty telemetry recorded non-blocking signals: {categories}."


def _event_origin(item: dict[str, Any]) -> str:
    subject = str(item.get("subject") or "")
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    detail_reason = str(details.get("reason") or "").lower()
    if (
        subject.startswith(("TEST::", "PROBE::"))
        or "NO_SUCH_FILE" in subject
        or "disappeared.ts" in subject
        or "fixture" in detail_reason
        or "probe" in detail_reason
    ):
        return "validation_probe"
    if str(item.get("category") or "") == "storage_fallback":
        return "system_storage"
    return "target_repo_signal"


def _summaries(events: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    by_category: dict[str, int] = {}
    by_origin: dict[str, int] = {}
    for item in events:
        if not isinstance(item, dict):
            continue
        count = int(item.get("count", 1) or 1)
        category = str(item.get("category") or "unknown")
        origin = _event_origin(item)
        by_category[category] = by_category.get(category, 0) + count
        by_origin[origin] = by_origin.get(origin, 0) + count
    return by_category, by_origin


def _event_key(component: str, category: str, operation: str, subject: str) -> str:
    raw = "\x1f".join((component, category, operation, subject))
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


def refresh_honesty_telemetry_status() -> dict[str, Any]:
    """Rewrite the telemetry summary with an explicit PASS/ATTENTION status."""
    telemetry_path = current_honesty_telemetry_path()
    with _LOCK:
        payload = load_json_file(telemetry_path, {}, bypass_proxy=False)
        if not isinstance(payload, dict):
            payload = {}
        events = list(payload.get("events", []) or [])
        summary, origins = _summaries(events)
        time_window = event_time_window(events)
        status, status_reason = _summary_status(events, summary)
        refreshed = {
            "meta": {
                "kind": "honesty_telemetry",
                "version": "v1",
                "physical_ssot": _physical_ssot(telemetry_path),
                "purpose": "caught errors, fallbacks, uncertainty and SSOT boundary provenance",
            },
            "summary": {
                "status": status,
                "status_reason": status_reason,
                "unique_events": len(events),
                "occurrences_by_category": summary,
                "occurrences_by_origin": origins,
                **time_window,
            },
            "events": events[-_MAX_EVENTS:],
        }
        save_json_atomic(telemetry_path, refreshed)
        return refreshed


def record_honesty_event(
    *,
    component: str,
    category: str,
    operation: str,
    subject: str = "",
    severity: str = "warning",
    reason: str,
    fallback: str | None = None,
    claim_impact: str = "none",
    evidence_source: str = "internal",
    exception: BaseException | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record bounded, deduplicated evidence about fallbacks and uncertainty.

    Telemetry is best-effort so an observability failure never masks the
    original operation. Re-entrancy is blocked because the artifact store can
    itself be the component reporting a degraded path.
    """
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "event_id": _event_key(component, category, operation, subject),
        "timestamp": now,
        "first_seen_at": now,
        "last_seen_at": now,
        "component": str(component),
        "category": str(category),
        "operation": str(operation),
        "subject": str(subject),
        "severity": str(severity),
        "reason": str(reason)[:1000],
        "fallback": str(fallback)[:500] if fallback else None,
        "claim_impact": str(claim_impact),
        "evidence_source": str(evidence_source),
        "exception_type": type(exception).__name__ if exception else None,
        "exception_message": str(exception)[:1000] if exception else None,
        "details": details or {},
        "count": 1,
    }
    if getattr(_WRITING, "active", False):
        return event
    try:
        _WRITING.active = True
        telemetry_path = current_honesty_telemetry_path()
        with _LOCK:
            payload = load_json_file(telemetry_path, {}, bypass_proxy=False)
            if not isinstance(payload, dict):
                payload = {}
            events = list(payload.get("events", []) or [])
            existing = next((item for item in events if item.get("event_id") == event["event_id"]), None)
            if existing:
                event["first_seen_at"] = str(existing.get("first_seen_at") or existing.get("timestamp") or event["first_seen_at"])
                existing.update({key: value for key, value in event.items() if key != "count"})
                existing["count"] = int(existing.get("count", 1) or 1) + 1
                occurrence_count = existing["count"]
            else:
                events.append(event)
                occurrence_count = 1
            if occurrence_count == 1 or occurrence_count in {10, 100, 1000}:
                logger.log(
                    {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}.get(severity, 30),
                    "[HONESTY] %s %s %s: %s (count=%s)",
                    component,
                    category,
                    operation,
                    reason,
                    occurrence_count,
                )
            events = events[-_MAX_EVENTS:]
            summary, origins = _summaries(events)
            time_window = event_time_window(events)
            status, status_reason = _summary_status(events, summary)
            save_json_atomic(
                telemetry_path,
                {
                    "meta": {
                        "kind": "honesty_telemetry",
                        "version": "v1",
                        "physical_ssot": _physical_ssot(telemetry_path),
                        "purpose": "caught errors, fallbacks, uncertainty and SSOT boundary provenance",
                    },
                    "summary": {
                        "status": status,
                        "status_reason": status_reason,
                        "unique_events": len(events),
                        "occurrences_by_category": summary,
                        "occurrences_by_origin": origins,
                        **time_window,
                    },
                    "events": events,
                },
            )
    except Exception as telemetry_error:
        logger.error("[HONESTY] Failed to persist telemetry event: %s", telemetry_error)
    finally:
        _WRITING.active = False
    return event

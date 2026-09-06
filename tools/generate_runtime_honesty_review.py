from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.honesty_telemetry import event_time_window
from tools.core.json_io import load_json_file


POLICY_PATH = ROOT / "config" / "runtime_honesty_review_policy.json"


MOJIBAKE_MARKERS = ("Ã", "Ä", "Å", "â")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count_by(events: list[dict[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for event in events:
        counter[str(event.get(key) or "unknown")] += int(event.get("count", 1) or 1)
    return dict(counter.most_common())


def _top_events(events: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    rows = sorted(events, key=lambda item: int(item.get("count", 1) or 1), reverse=True)
    return [
        {
            "component": item.get("component"),
            "category": item.get("category"),
            "operation": item.get("operation"),
            "subject": item.get("subject"),
            "severity": item.get("severity"),
            "reason": item.get("reason"),
            "fallback": item.get("fallback"),
            "claim_impact": item.get("claim_impact"),
            "review_class": _event_review_class(item),
            "current_resolution": _current_resolution(item),
            "operator_action": _operator_action(item),
            "count": int(item.get("count", 1) or 1),
            "first_seen_at": item.get("first_seen_at") or item.get("timestamp"),
            "last_seen_at": item.get("last_seen_at") or item.get("timestamp"),
        }
        for item in rows[:limit]
    ]


def _encoding_findings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in events:
        haystack = " ".join(
            str(item.get(field) or "")
            for field in ("reason", "fallback", "exception_message", "subject")
        )
        markers = sorted({marker for marker in MOJIBAKE_MARKERS if marker in haystack})
        if markers:
            findings.append(
                {
                    "event_id": item.get("event_id"),
                    "component": item.get("component"),
                    "category": item.get("category"),
                    "operation": item.get("operation"),
                    "markers": markers,
                    "claim_impact": item.get("claim_impact"),
                    "count": int(item.get("count", 1) or 1),
                }
            )
    return findings


def _is_operator_attention_event(item: dict[str, Any]) -> bool:
    sqlite_missing_row_self_healed = (
        item.get("category") == "storage_fallback"
        and item.get("operation") == "load_raw_missing_sqlite_row"
        and item.get("claim_impact") == "artifact_freshness_self_healed"
        and item.get("fallback") == "shadow_json_read_and_sqlite_self_heal"
    )
    sqlite_failure_self_healed = (
        item.get("category") == "storage_fallback"
        and item.get("operation") == "load_raw"
        and item.get("claim_impact") == "artifact_freshness_requires_validation"
        and item.get("fallback") == "shadow_json_read_and_sqlite_self_heal"
    )
    return sqlite_missing_row_self_healed or sqlite_failure_self_healed


@lru_cache(maxsize=1)
def _review_policy() -> dict[str, Any]:
    return load_json_file(POLICY_PATH, {})


def _matches_rule(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    component = str(item.get("component") or "")
    subject = str(item.get("subject") or "")
    category = str(item.get("category") or "")
    claim_impact = str(item.get("claim_impact") or "")
    fallback = str(item.get("fallback") or "")
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    detail_reason = str(details.get("reason") or "")

    if "component_in" in rule and component not in set(rule.get("component_in") or []):
        return False
    if "subject_prefix_in" in rule and not any(subject.startswith(str(prefix)) for prefix in rule.get("subject_prefix_in") or []):
        return False
    if "subject_contains" in rule and not any(str(token) in subject for token in rule.get("subject_contains") or []):
        return False
    if "category" in rule and category != str(rule.get("category") or ""):
        return False
    if "claim_impact" in rule and claim_impact != str(rule.get("claim_impact") or ""):
        return False
    if "claim_impact_not_in" in rule and claim_impact in set(str(value) for value in rule.get("claim_impact_not_in") or []):
        return False
    if "fallback" in rule and fallback != str(rule.get("fallback") or ""):
        return False
    if "detail_reason_in" in rule and detail_reason not in set(rule.get("detail_reason_in") or []):
        return False
    return True


def _event_review_class(item: dict[str, Any]) -> str:
    policy = _review_policy()
    if _is_operator_attention_event(item):
        storage_policy = policy.get("storage_self_healed_review", {}) if isinstance(policy, dict) else {}
        return str(storage_policy.get("id") or "storage_self_healed_review")
    classes = policy.get("classes", []) if isinstance(policy, dict) else []
    for configured_class in classes:
        if not isinstance(configured_class, dict):
            continue
        for rule in configured_class.get("match_any", []) or []:
            if isinstance(rule, dict) and _matches_rule(item, rule):
                return str(configured_class.get("id") or policy.get("default_review_class") or "non_blocking_runtime_noise")
    return str(policy.get("default_review_class") or "non_blocking_runtime_noise")


def _operator_action(item: dict[str, Any]) -> str:
    policy = _review_policy()
    review_class = _event_review_class(item)
    storage_policy = policy.get("storage_self_healed_review", {}) if isinstance(policy, dict) else {}
    if review_class == storage_policy.get("id"):
        return str(storage_policy.get("operator_action") or policy.get("default_operator_action") or "")
    classes = policy.get("classes", []) if isinstance(policy, dict) else []
    for configured_class in classes:
        if isinstance(configured_class, dict) and review_class == configured_class.get("id"):
            return str(configured_class.get("operator_action") or policy.get("default_operator_action") or "")
    return str(policy.get("default_operator_action") or "No blocking action; retain for observability.")


def _current_resolution(item: dict[str, Any]) -> str:
    subject = str(item.get("subject") or "")
    if _is_operator_attention_event(item):
        db_path = RAW_DIR / "codemaps.db"
        json_path = RAW_DIR / f"{subject}.json"
        if db_path.exists():
            try:
                with sqlite3.connect(db_path) as conn:
                    row = conn.execute("SELECT 1 FROM state_payloads WHERE name = ?;", (subject,)).fetchone()
                if row:
                    return "current_sqlite_row_present"
            except sqlite3.Error:
                return "current_sqlite_check_failed"
        if json_path.exists():
            return "current_json_shadow_only"
        return "current_artifact_missing"
    if item.get("component") == "discovery" and item.get("operation") == "parse_tsconfig":
        path = Path(subject)
        if not path.exists():
            return "current_subject_missing"
        try:
            with path.open("r", encoding="utf-8") as handle:
                json.load(handle)
            return "current_json_valid"
        except json.JSONDecodeError:
            return "current_json_invalid"
        except OSError:
            return "current_subject_unreadable"
    return "not_rechecked"


def _render(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Runtime Honesty Review",
        "",
        f"- status: `{summary.get('status')}`",
        f"- unique_events: `{summary.get('unique_events')}`",
        f"- total_occurrences: `{summary.get('total_occurrences')}`",
        f"- error_or_critical_occurrences: `{summary.get('error_or_critical_occurrences')}`",
        f"- operator_attention_occurrences: `{summary.get('operator_attention_occurrences')}`",
        f"- claim_affecting_occurrences: `{summary.get('claim_affecting_occurrences')}`",
        f"- encoding_warning_events: `{summary.get('encoding_warning_events')}`",
        f"- oldest_event_at: `{summary.get('oldest_event_at')}`",
        f"- latest_event_at: `{summary.get('latest_event_at')}`",
        "",
        "## Category Counts",
        "",
    ]
    for key, value in payload.get("category_counts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Claim Impact Counts", ""])
    for key, value in payload.get("claim_impact_counts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Review Class Counts", ""])
    for key, value in payload.get("review_class_counts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Top Events",
            "",
            "| Component | Review Class | Current Resolution | Operation | Subject | Claim Impact | Count | Last Seen | Operator Action |",
            "|---|---|---|---|---|---|---:|---|---|",
        ]
    )
    for event in payload.get("top_events", []):
        action = str(event.get("operator_action") or "").replace("|", "\\|")
        subject = str(event.get("subject") or "").replace("|", "\\|")
        lines.append(
            f"| `{event.get('component')}` | `{event.get('review_class')}` | `{event.get('current_resolution')}` | `{event.get('operation')}` | `{subject}` | `{event.get('claim_impact')}` | {event.get('count')} | `{event.get('last_seen_at')}` | {action} |"
        )
    lines.extend(["", "## Interpretation", ""])
    lines.extend(f"- {item}" for item in payload.get("interpretation", []))
    return "\n".join(lines) + "\n"


def build_review() -> dict[str, Any]:
    telemetry = load_json_file(RAW_DIR / "honesty_telemetry.json", {})
    runtime = load_json_file(RAW_DIR / "runtime_honesty_validation.json", {})
    events = telemetry.get("events", []) if isinstance(telemetry, dict) else []
    events = [item for item in events if isinstance(item, dict)]
    category_counts = _count_by(events, "category")
    severity_counts = _count_by(events, "severity")
    claim_impact_counts = _count_by(events, "claim_impact")
    review_class_counts: Counter[str] = Counter()
    for event in events:
        review_class_counts[_event_review_class(event)] += int(event.get("count", 1) or 1)
    total_occurrences = sum(int(item.get("count", 1) or 1) for item in events)
    error_or_critical = sum(
        int(item.get("count", 1) or 1)
        for item in events
        if str(item.get("severity") or "").lower() in {"error", "critical"}
        and not _is_operator_attention_event(item)
    )
    operator_attention = sum(
        int(item.get("count", 1) or 1)
        for item in events
        if _is_operator_attention_event(item)
    )
    claim_affecting = sum(
        int(item.get("count", 1) or 1)
        for item in events
        if str(item.get("claim_impact") or "none") not in {"none", "performance_only"}
    )
    encoding_findings = _encoding_findings(events)
    time_window = event_time_window(events)
    runtime_summary = runtime.get("summary", {}) if isinstance(runtime, dict) else {}
    status = "PASS" if runtime_summary.get("status") == "PASS" and error_or_critical == 0 else "FAIL"
    payload = {
        "meta": {
            "kind": "runtime_honesty_review",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_runtime_honesty_review",
        },
        "summary": {
            "status": status,
            "unique_events": len(events),
            "total_occurrences": total_occurrences,
            "error_or_critical_occurrences": error_or_critical,
            "operator_attention_occurrences": operator_attention,
            "claim_affecting_occurrences": claim_affecting,
            "encoding_warning_events": len(encoding_findings),
            "runtime_honesty_status": runtime_summary.get("status"),
            **time_window,
        },
        "category_counts": category_counts,
        "severity_counts": severity_counts,
        "claim_impact_counts": claim_impact_counts,
        "review_class_counts": dict(review_class_counts.most_common()),
        "top_events": _top_events(events),
        "encoding_findings": encoding_findings[:50],
        "interpretation": [
            "This review summarizes honesty telemetry for humans and AI operators; it does not replace the raw telemetry artifact.",
            "Claim-affecting events are not automatically release blockers, but they must remain visible when they degrade analysis completeness.",
            "Expected validation probes prove fail-closed paths speak; target-repo input attention should be inspected before expanding claims for that scope.",
            "Encoding warning events usually come from operating-system exception text; they are surfaced so reports can be improved without hiding original errors.",
            "Runtime proof remains out of scope for v1 unless a dedicated runtime evidence artifact exists.",
        ],
    }
    save_json_atomic(RAW_DIR / "runtime_honesty_review.json", payload)
    save_text_atomic(REPORTS_DIR / "runtime_honesty_review.md", _render(payload))
    return payload


def main() -> int:
    payload = build_review()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

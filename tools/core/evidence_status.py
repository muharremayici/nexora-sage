from __future__ import annotations

from typing import Any


PASS_TOKENS = {"PASS", "OK", "READY", "PRODUCTION_READY"}
FAIL_TOKENS = {"FAIL", "ERROR", "NOT_READY", "BLOCKED"}
NON_BLOCKING_CHECK_SEVERITIES = {"advisory", "info", "warning"}


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _token(value: Any) -> str:
    return str(value or "").strip().upper()


def _is_enforced_check(check: dict[str, Any]) -> bool:
    if check.get("enforced", True) is False:
        return False
    return str(check.get("severity") or "error").strip().lower() not in NON_BLOCKING_CHECK_SEVERITIES


def has_substantive_evidence(payload: Any) -> bool:
    """Return whether a PASS-like payload contains an observed evidence body."""
    if not isinstance(payload, dict):
        return False

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    non_evidence_count_tokens = {
        "blocked",
        "debt",
        "error",
        "fail",
        "missing",
        "omitted",
        "skipped",
        "stale",
        "unknown",
        "violation",
        "warning",
    }
    for key, raw_value in summary.items():
        if isinstance(raw_value, bool):
            continue
        value = _to_int(raw_value)
        normalized_key = str(key).strip().lower()
        if (
            value is not None
            and value > 0
            and not any(token in normalized_key for token in non_evidence_count_tokens)
        ):
            return True

    for key in ("checks", "findings", "references", "results", "rows", "evidence"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and value:
            return True
    return False


def normalize_evidence_status(payload: Any) -> dict[str, Any]:
    """Return a single PASS/FAIL/UNKNOWN verdict for generated evidence payloads."""
    if not isinstance(payload, dict):
        return {"status": "UNKNOWN", "passed": None, "source": "invalid_payload"}

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    failed_checks = _to_int(summary.get("failed_checks"))
    if failed_checks is not None and failed_checks > 0:
        return {
            "status": "FAIL",
            "passed": False,
            "source": "summary.failed_checks",
            "failed_checks": failed_checks,
        }

    if isinstance(payload.get("checks"), list):
        checks = [check for check in payload.get("checks", []) if isinstance(check, dict)]
        if checks and all("passed" in check for check in checks):
            enforced_checks = [check for check in checks if _is_enforced_check(check)]
            failed = sum(1 for check in enforced_checks if check.get("passed") is not True)
            if failed:
                return {
                    "status": "FAIL",
                    "passed": False,
                    "source": "checks[].passed",
                    "failed_checks": failed,
                }

    candidates = [
        ("summary.status", summary.get("status")),
        ("top.status", payload.get("status")),
        ("top.readiness", payload.get("readiness")),
    ]
    for source, value in candidates:
        token = _token(value)
        if token in PASS_TOKENS:
            if has_substantive_evidence(payload):
                return {"status": "PASS", "passed": True, "source": source, "raw_status": value}
            return {
                "status": "UNKNOWN",
                "passed": None,
                "source": f"{source}.empty_pass_shell",
                "raw_status": value,
            }
        if token in FAIL_TOKENS:
            return {"status": "FAIL", "passed": False, "source": source, "raw_status": value}

    if failed_checks is not None:
        if failed_checks == 0 and not has_substantive_evidence(payload):
            return {
                "status": "UNKNOWN",
                "passed": None,
                "source": "summary.failed_checks.empty_pass_shell",
                "failed_checks": failed_checks,
            }
        return {
            "status": "PASS" if failed_checks == 0 else "FAIL",
            "passed": failed_checks == 0,
            "source": "summary.failed_checks",
            "failed_checks": failed_checks,
        }

    if isinstance(payload.get("checks"), list):
        checks = [check for check in payload.get("checks", []) if isinstance(check, dict)]
        if checks and all("passed" in check for check in checks):
            enforced_checks = [check for check in checks if _is_enforced_check(check)]
            failed = sum(1 for check in enforced_checks if check.get("passed") is not True)
            return {
                "status": "PASS" if failed == 0 else "FAIL",
                "passed": failed == 0,
                "source": "checks[].passed",
                "failed_checks": failed,
            }

    if isinstance(payload.get("passed"), bool):
        passed = bool(payload.get("passed"))
        if passed and not has_substantive_evidence(payload):
            return {
                "status": "UNKNOWN",
                "passed": None,
                "source": "top.passed.empty_pass_shell",
            }
        return {"status": "PASS" if passed else "FAIL", "passed": passed, "source": "top.passed"}

    return {"status": "UNKNOWN", "passed": None, "source": "missing_verdict"}


def evidence_passed(payload: Any, default: bool = False) -> bool:
    verdict = normalize_evidence_status(payload)
    passed = verdict.get("passed")
    return bool(default) if passed is None else bool(passed)


def evidence_failed_checks(payload: Any) -> int | None:
    verdict = normalize_evidence_status(payload)
    if verdict.get("passed") is None:
        return None
    failed = verdict.get("failed_checks")
    if failed is not None:
        return _to_int(failed)
    return 0 if verdict.get("passed") is True else 1

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from tools.core.config import RAW_DIR
from tools.core.json_io import load_json_file


import functools

@functools.lru_cache(maxsize=1)
def _cached_audit_report() -> Dict[str, Any]:
    path = RAW_DIR / "audit_report.json"
    try:
        payload = load_json_file(path, {})
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def load_audit_report() -> Dict[str, Any]:
    return dict(_cached_audit_report())


def invalidate_audit_report_cache() -> None:
    _cached_audit_report.cache_clear()


def get_audit_summary() -> Dict[str, Any]:
    summary = load_audit_report().get("summary", {})
    return summary if isinstance(summary, dict) else {}


def get_total_violations() -> int:
    summary = get_audit_summary()
    return int(summary.get("total", 0) or 0)


def get_project_counts() -> Dict[str, int]:
    by_project = get_audit_summary().get("by_project", {})
    if not isinstance(by_project, dict):
        return {}
    return {str(key): int(value or 0) for key, value in by_project.items()}


def get_project_violations(project_key: str) -> int:
    return int(get_project_counts().get(str(project_key or ""), 0) or 0)


def get_violations() -> List[Dict[str, Any]]:
    violations = load_audit_report().get("violations", [])
    return violations if isinstance(violations, list) else []


def get_rule_counts() -> Dict[str, int]:
    by_rule = get_audit_summary().get("by_rule", {})
    if not isinstance(by_rule, dict):
        return {}
    return {str(key): int(value or 0) for key, value in by_rule.items()}


def get_rule_taxonomy() -> Dict[str, Any]:
    taxonomy = get_audit_summary().get("rule_taxonomy", {})
    return taxonomy if isinstance(taxonomy, dict) else {}

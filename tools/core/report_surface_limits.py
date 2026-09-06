from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


DEFAULT_REPORT_SURFACE_LIMITS: dict[str, int] = {
    "a11y_i18n_contracts.primary_files": 120,
    "next_boundary_analysis.primary_files": 120,
    "react_compiler_readiness.primary_findings": 800,
    "react_ecosystem_analysis.primary_findings": 500,
    "react_frontier_intelligence.primary_findings": 700,
    "react_runtime_intelligence.primary_findings": 600,
}


def report_surface_limit(key: str) -> int:
    policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
    limits: Any = policy.get("report_surface_limits") if isinstance(policy, dict) else {}
    value = limits.get(key) if isinstance(limits, dict) else None
    if value is None:
        value = DEFAULT_REPORT_SURFACE_LIMITS.get(key)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(DEFAULT_REPORT_SURFACE_LIMITS[key]))

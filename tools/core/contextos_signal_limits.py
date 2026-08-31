from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


def contextos_signal_limit(key: str) -> int:
    policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
    limits: Any = policy.get("contextos_signal_limits") if isinstance(policy, dict) else {}
    value = limits.get(key) if isinstance(limits, dict) else None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Missing or invalid ContextOS signal limit: {key}") from exc
    if parsed <= 0:
        raise ValueError(f"ContextOS signal limit must be positive: {key}={parsed}")
    return parsed

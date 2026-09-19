"""Storage budget policy acquisition, independent of the storage it configures."""
from __future__ import annotations

from pathlib import Path

from tools.core.json_syntax import loads_json_strict


DEFAULT_PERSISTENCE_LIMITS: dict[str, int] = {
    "atlas_staging_batch_size": 128,
    "atlas_staging_file_payload_limit_bytes": 33554432,
    "atlas_staging_file_aggregate_limit_bytes": 268435456,
    "state_payload_inline_limit_bytes": 8388608,
    "state_payload_part_size_bytes": 4194304,
}


def load_persistence_limits(policy_path: Path) -> dict[str, int]:
    """Read one policy snapshot; invalid files fail before any storage mutation."""
    policy = loads_json_strict(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError(f"Pipeline execution policy root must be a JSON object: {policy_path}")
    limits = policy.get("operational_limits")
    limits = limits if isinstance(limits, dict) else {}
    resolved = {}
    for key, default in DEFAULT_PERSISTENCE_LIMITS.items():
        value = limits.get(key)
        try:
            resolved[key] = max(1, int(default if value is None else value))
        except (TypeError, ValueError):
            resolved[key] = default
    return resolved

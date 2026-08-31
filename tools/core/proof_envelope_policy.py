from __future__ import annotations

from copy import deepcopy
import math

from tools.core.config import CONFIG_DIR
from tools.core.artifact_validator import ensure_against_schema
from tools.core.json_io import load_json_object_strict_cached
from tools.core.logger import logger
from tools.core.config import SCHEMAS_DIR


SCHEMA_PATH = SCHEMAS_DIR / "l4_proof_envelope.schema.json"


def _path_get(data: dict, path: str):
    cursor = data
    for key in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor


def _path_set(data: dict, path: str, value):
    keys = path.split(".")
    cursor = data
    for key in keys[:-1]:
        if key not in cursor or not isinstance(cursor[key], dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[keys[-1]] = value


def iter_policy_numeric_constraints(policy: dict) -> list[dict]:
    meta = policy.get("_meta", {}) if isinstance(policy, dict) else {}
    constraints = meta.get("numeric_constraints", []) if isinstance(meta, dict) else []
    return [item for item in constraints if isinstance(item, dict)]


def iter_policy_numeric_paths(policy: dict) -> list[str]:
    paths: list[str] = []
    for item in iter_policy_numeric_constraints(policy):
        path = str(item.get("path", "")).strip()
        if path:
            paths.append(path)
    return paths


def _sanitize_policy(policy: dict) -> dict:
    sanitized = deepcopy(policy)
    constraints = iter_policy_numeric_constraints(sanitized)

    for constraint in constraints:
        path = str(constraint.get("path", "")).strip()
        if not path:
            continue
        min_value = constraint.get("min")
        max_value = constraint.get("max")
        integer = bool(constraint.get("integer"))
        current = _path_get(sanitized, path)
        default = constraint.get("default")
        if not isinstance(min_value, (int, float)) or not isinstance(default, (int, float)):
            continue
        valid = isinstance(current, (int, float)) and not isinstance(current, bool) and math.isfinite(float(current))
        if not valid:
            logger.warning("[L4_POLICY] Invalid numeric value for '%s': %r. Falling back to default %r.", path, current, default)
            _path_set(sanitized, path, default)
            continue

        numeric = int(current) if integer else float(current)
        if numeric < min_value or (max_value is not None and numeric > max_value):
            logger.warning(
                "[L4_POLICY] Out-of-range value for '%s': %r. Expected [%s, %s]. Falling back to default %r.",
                path,
                current,
                min_value,
                max_value if max_value is not None else "inf",
                default,
            )
            _path_set(sanitized, path, default)
            continue

        _path_set(sanitized, path, numeric)

    return sanitized


def _load_proof_envelope_policy() -> dict:
    configured = load_json_object_strict_cached(CONFIG_DIR / "l4_proof_envelope.json", label="L4 proof envelope policy")
    ensure_against_schema(SCHEMA_PATH, "l4_proof_envelope", configured)
    return _sanitize_policy(configured)


def get_proof_envelope_policy() -> dict:
    return deepcopy(_load_proof_envelope_policy())

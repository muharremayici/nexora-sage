from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


POLICY_PATH = CONFIG_DIR / "text_surface_integrity_policy.json"
POLICY_SCHEMA_PATH = CONFIG_DIR / "schemas" / "text_surface_integrity_policy.schema.json"


def load_text_surface_policy(
    policy_path: Path = POLICY_PATH,
    schema_path: Path = POLICY_SCHEMA_PATH,
) -> dict[str, Any]:
    policy = load_json_object_strict(policy_path, label="Text surface integrity policy")
    ensure_against_schema(schema_path, policy_path.stem, policy)
    return policy

from __future__ import annotations

from copy import deepcopy
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


QUALITY_GATE_POLICY_PATH = CONFIG_DIR / "quality_gate_policy.json"


def load_quality_gate_policy() -> dict[str, Any]:
    return load_json_object_strict(QUALITY_GATE_POLICY_PATH, label="Quality gate policy")


def quality_gate_contract_defaults() -> dict[str, Any]:
    policy = load_quality_gate_policy()
    defaults = policy.get("contract_defaults", {}) if isinstance(policy, dict) else {}
    return deepcopy(defaults) if isinstance(defaults, dict) else {}


def quality_gate_discovery_seed() -> dict[str, Any]:
    policy = load_quality_gate_policy()
    defaults = quality_gate_contract_defaults()
    fields = policy.get("discovery_seed_fields", []) if isinstance(policy, dict) else []
    if not isinstance(fields, list):
        return {}
    return {str(field): deepcopy(defaults[str(field)]) for field in fields if str(field) in defaults}

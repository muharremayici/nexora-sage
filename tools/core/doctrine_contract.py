from __future__ import annotations

from typing import Any

from tools.core.config import DOCTRINE


class DoctrineContractView(dict):
    """Fail-closed mapping for mandatory compiled doctrine decisions."""

    def __init__(self, value: dict, path: tuple[str, ...]) -> None:
        super().__init__(value)
        self._contract_path = path

    def _wrap(self, key: str, value: Any) -> Any:
        if isinstance(value, dict):
            return DoctrineContractView(value, (*self._contract_path, key))
        return value

    def __getitem__(self, key: str) -> Any:
        if key not in self:
            raise ValueError(f"[DOCTRINE] Missing mandatory path: {'.'.join((*self._contract_path, key))}")
        return self._wrap(key, super().__getitem__(key))

    def get(self, key: str, default: Any = None) -> Any:
        value = self[key]
        if value is None or value == "":
            raise ValueError(f"[DOCTRINE] Mandatory path is empty: {'.'.join((*self._contract_path, key))}")
        return value

    def get_or_contract_default(self, key: str, *, default_key: str = "default") -> Any:
        selected_key = key if key in self else default_key
        return self.get(selected_key)


def require_doctrine_path(*path: str, expected_type: type | tuple[type, ...] | None = None) -> Any:
    value: Any = DOCTRINE
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"[DOCTRINE] Missing mandatory path: {'.'.join(path)}")
        value = value[key]
    if value in (None, "", [], {}):
        raise ValueError(f"[DOCTRINE] Mandatory path is empty: {'.'.join(path)}")
    if expected_type is not None and not isinstance(value, expected_type):
        raise TypeError(f"[DOCTRINE] Invalid type at {'.'.join(path)}: {type(value).__name__}")
    return value


def remediation_action(rule_id: str) -> str:
    waves = require_doctrine_path("audit_remediation_policy", "waves", expected_type=dict)
    rule = waves.get(rule_id) if isinstance(waves.get(rule_id), dict) else waves.get("default")
    if not isinstance(rule, dict) or not isinstance(rule.get("action"), str) or not rule["action"].strip():
        raise ValueError(f"[DOCTRINE] Missing remediation action for rule: {rule_id}")
    return str(rule["action"])


def require_dead_code_policy(section: str) -> dict:
    return require_doctrine_mapping("dead_code_heuristics").get(section)


def require_doctrine_mapping(*path: str) -> DoctrineContractView:
    value = require_doctrine_path(*path, expected_type=dict)
    return DoctrineContractView(value, tuple(path))

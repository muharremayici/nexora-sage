"""Load exact-path source-layer classification extensions from one contract."""

from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached


POLICY_PATH = CONFIG_DIR / "source_layer_classification_policy.json"


def load_source_layer_classification_policy() -> dict[str, Any]:
    return load_json_object_strict_cached(POLICY_PATH, label="source layer classification policy")


def exact_path_rule(path: str) -> tuple[str, str] | None:
    payload = load_source_layer_classification_policy()
    rows = payload.get("exact_path_rules")
    if not isinstance(rows, list):
        raise ValueError("source layer classification policy exact_path_rules must be a list")
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("path") or "") == path]
    if len(matches) > 1:
        raise ValueError(f"source layer classification policy has duplicate exact path rule: {path}")
    if not matches:
        return None
    row = matches[0]
    layer = str(row.get("layer") or "").strip()
    reason = str(row.get("reason") or "").strip()
    if not layer or not reason:
        raise ValueError(f"source layer classification policy rule is incomplete: {path}")
    return layer, reason

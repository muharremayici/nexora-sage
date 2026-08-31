from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_file


CONTRACT_PATH = CONFIG_DIR / "source_layer_taxonomy.json"


def load_source_layer_taxonomy(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def source_layer_rows(path: Path = CONTRACT_PATH) -> list[dict[str, str]]:
    rows = load_source_layer_taxonomy(path).get("layers", [])
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        layer_id = str(row.get("id") or "").strip()
        if not layer_id or layer_id in seen:
            continue
        seen.add(layer_id)
        normalized.append(
            {
                "id": layer_id,
                "description": str(row.get("description") or ""),
            }
        )
    return normalized


def source_layer_order(path: Path = CONTRACT_PATH) -> list[str]:
    return [row["id"] for row in source_layer_rows(path)]


def source_layer_descriptions(path: Path = CONTRACT_PATH) -> dict[str, str]:
    return {row["id"]: row["description"] for row in source_layer_rows(path)}

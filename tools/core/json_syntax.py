"""Strict JSON syntax without configuration, telemetry or storage dependencies."""
from __future__ import annotations

import json
from typing import Any


class DuplicateJSONKeyError(ValueError):
    """Raised when JSON contains two members with the same object key."""


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def loads_json_strict(content: str) -> Any:
    """Parse JSON without allowing last-key-wins semantic ambiguity."""
    return json.loads(content, object_pairs_hook=_unique_object_pairs)

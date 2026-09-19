"""Dependency-light exact-content cache for strict JSON object contracts."""
from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.core.json_syntax import loads_json_strict
from tools.core.unmanaged_atomic_io import native_filesystem_path


_STRICT_JSON_CONTENT_CACHE: dict[
    tuple[str, Callable[[dict[str, Any]], dict[str, Any]] | None],
    tuple[str, dict[str, Any]],
] = {}
_STRICT_JSON_CONTENT_CACHE_METRICS = {
    "reads": 0,
    "hits": 0,
    "misses": 0,
    "bytes_hashed": 0,
}
_STRICT_JSON_CONTENT_CACHE_LOCK = threading.RLock()


def _content_snapshot(path: Path) -> tuple[bytes, str]:
    content = Path(native_filesystem_path(path)).read_bytes()
    return content, hashlib.sha256(content).hexdigest()


def load_json_object_strict_cached(
    path: str | Path,
    *,
    label: str = "JSON contract",
    normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a strict JSON object cached by exact content rather than timestamps."""
    contract_path = Path(path)
    content, fingerprint = _content_snapshot(contract_path)
    cache_key = (str(contract_path.resolve()), normalizer)
    with _STRICT_JSON_CONTENT_CACHE_LOCK:
        _STRICT_JSON_CONTENT_CACHE_METRICS["reads"] += 1
        _STRICT_JSON_CONTENT_CACHE_METRICS["bytes_hashed"] += len(content)
        cached = _STRICT_JSON_CONTENT_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            _STRICT_JSON_CONTENT_CACHE_METRICS["hits"] += 1
            return cached[1]
        _STRICT_JSON_CONTENT_CACHE_METRICS["misses"] += 1
    try:
        payload = loads_json_strict(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8: {contract_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be a JSON object: {contract_path}")
    normalized = normalizer(payload) if normalizer is not None else payload
    if not isinstance(normalized, dict):
        raise TypeError(f"{label} normalizer must return a JSON object")
    with _STRICT_JSON_CONTENT_CACHE_LOCK:
        _STRICT_JSON_CONTENT_CACHE[cache_key] = (fingerprint, normalized)
    return normalized


def strict_json_content_cache_metrics() -> dict[str, int]:
    with _STRICT_JSON_CONTENT_CACHE_LOCK:
        return dict(_STRICT_JSON_CONTENT_CACHE_METRICS)


def clear_strict_json_content_cache() -> None:
    with _STRICT_JSON_CONTENT_CACHE_LOCK:
        _STRICT_JSON_CONTENT_CACHE.clear()
        for key in _STRICT_JSON_CONTENT_CACHE_METRICS:
            _STRICT_JSON_CONTENT_CACHE_METRICS[key] = 0

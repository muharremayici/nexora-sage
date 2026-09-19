from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from tools.core.json_syntax import DuplicateJSONKeyError, loads_json_strict
from tools.core.strict_contract_cache import (
    clear_strict_json_content_cache,
    load_json_object_strict_cached,
    strict_json_content_cache_metrics,
)
from tools.core.unmanaged_atomic_io import native_filesystem_path


_JSON_CONTENT_CACHE: dict[tuple[str, Callable[[Any], Any] | None], tuple[str, Any]] = {}
_JSON_CONTENT_CACHE_METRICS = {"reads": 0, "hits": 0, "misses": 0, "bytes_hashed": 0}
_JSON_CONTENT_CACHE_LOCK = threading.RLock()


def _content_snapshot(path: Path) -> tuple[bytes, str]:
    content = Path(native_filesystem_path(path)).read_bytes()
    return content, hashlib.sha256(content).hexdigest()


def _is_managed_raw_artifact(path: Path) -> bool:
    try:
        from tools.core.config import RAW_DIR

        resolved = path.resolve()
        raw_dir = RAW_DIR.resolve()
        return resolved.parent == raw_dir and resolved.suffix.lower() == ".json"
    except Exception:
        return False


def load_json_file(path: str | Path, default: Any = None, bypass_proxy: bool = False) -> Any:
    """Load JSON file returning *default* on any error (missing, corrupt, etc.)."""
    path = Path(path)
    if not bypass_proxy and _is_managed_raw_artifact(path):
        try:
            from tools.core.artifact_store import STORE
            # If SQLite mode is enabled, STORE.load_raw handles it seamlessly
            return STORE.load_raw(path.stem, default)
        except Exception as exc:
            try:
                from tools.core.honesty_telemetry import record_honesty_event

                record_honesty_event(
                    component="json_io",
                    category="storage_fallback",
                    operation="load_json_file_proxy",
                    subject=str(path),
                    reason="managed raw artifact proxy failed",
                    fallback="direct_json_read_or_default",
                    claim_impact="artifact_freshness_requires_validation",
                    exception=exc,
                )
            except Exception:
                pass
            
    native_path = Path(native_filesystem_path(path))
    if not native_path.exists():
        return default
    try:
        return loads_json_strict(native_path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_json_strict(path: str | Path, bypass_proxy: bool = False) -> Any:
    """Load JSON file; raises FileNotFoundError / json.JSONDecodeError on failure."""
    path = Path(path)
    if not bypass_proxy and _is_managed_raw_artifact(path):
        from tools.core.artifact_store import STORE
        return STORE.load_raw(path.stem)
    return loads_json_strict(Path(native_filesystem_path(path)).read_text(encoding="utf-8"))


def load_json_object_strict(path: str | Path, *, label: str = "JSON contract", bypass_proxy: bool = False) -> dict[str, Any]:
    """Load a required JSON object contract; fail closed if missing, corrupt, or not an object."""
    path = Path(path)
    payload = load_json_strict(path, bypass_proxy=bypass_proxy)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be a JSON object: {path}")
    return payload


def load_json_content_cached(
    path: str | Path,
    *,
    transform: Callable[[Any], Any] | None = None,
) -> Any:
    """Load arbitrary JSON with exact-content cache identity; parsing errors remain visible to the caller."""
    value, _ = load_json_content_cached_with_identity(path, transform=transform)
    return value


def load_json_content_cached_with_identity(
    path: str | Path,
    *,
    transform: Callable[[Any], Any] | None = None,
) -> tuple[Any, str]:
    """Load arbitrary JSON and return its exact content fingerprint for dependent derived caches."""
    json_path = Path(path)
    content, fingerprint = _content_snapshot(json_path)
    cache_key = (str(json_path.resolve()), transform)
    with _JSON_CONTENT_CACHE_LOCK:
        _JSON_CONTENT_CACHE_METRICS["reads"] += 1
        _JSON_CONTENT_CACHE_METRICS["bytes_hashed"] += len(content)
        cached = _JSON_CONTENT_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            _JSON_CONTENT_CACHE_METRICS["hits"] += 1
            return cached[1], fingerprint
        _JSON_CONTENT_CACHE_METRICS["misses"] += 1
    payload = loads_json_strict(content.decode("utf-8-sig"))
    value = transform(payload) if transform is not None else payload
    with _JSON_CONTENT_CACHE_LOCK:
        _JSON_CONTENT_CACHE[cache_key] = (fingerprint, value)
    return value, fingerprint


def json_content_cache_metrics() -> dict[str, int]:
    with _JSON_CONTENT_CACHE_LOCK:
        return dict(_JSON_CONTENT_CACHE_METRICS)


def clear_json_content_cache() -> None:
    with _JSON_CONTENT_CACHE_LOCK:
        _JSON_CONTENT_CACHE.clear()
        for key in _JSON_CONTENT_CACHE_METRICS:
            _JSON_CONTENT_CACHE_METRICS[key] = 0


def is_raw_artifact_path(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.suffix.lower() == ".json" and candidate.parent.name == ".raw"


def _raw_state_connection(json_path: Path) -> sqlite3.Connection | None:
    db_path = json_path.parent / "codemaps.db"
    if not db_path.exists():
        return None
    from tools.core.operational_limits import sqlite_read_timeout_seconds

    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(
        uri,
        uri=True,
        timeout=float(sqlite_read_timeout_seconds()),
    )


def _raw_state_payload_row(json_path: Path) -> tuple[str, str | None] | None:
    connection = _raw_state_connection(json_path)
    if connection is None:
        return None
    with closing(connection) as conn:
        try:
            row = conn.execute(
                """
                SELECT payload, payload_sha, payload_bytes, storage_mode, generation_id, part_count
                FROM state_payloads WHERE name = ?;
                """,
                (json_path.stem,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = conn.execute(
                "SELECT payload, payload_sha FROM state_payloads WHERE name = ?;",
                (json_path.stem,),
            ).fetchone()
            if row is None:
                return None
            return str(row[0]), str(row[1]) if row[1] else None
        if row is None:
            return None
        if str(row[3] or "inline_json") == "inline_json":
            return str(row[0]), str(row[1]) if row[1] else None
        if str(row[3]) != "partitioned_json_v1":
            raise ValueError(f"Unsupported raw state payload storage mode: {row[3]}")
        generation_id = str(row[4] or "")
        expected_parts = int(row[5] or 0)
        expected_bytes = int(row[2] or 0)
        expected_sha = str(row[1] or "")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        actual_bytes = 0
        parts = conn.execute(
            """
            SELECT part_index, payload, payload_bytes, payload_sha
            FROM state_payload_parts
            WHERE name = ? AND generation_id = ?
            ORDER BY part_index;
            """,
            (json_path.stem, generation_id),
        )
        for expected_index, part in enumerate(parts):
            if int(part[0]) != expected_index:
                raise ValueError("Partitioned raw payload sequence is not contiguous.")
            part_bytes = bytes(part[1])
            if len(part_bytes) != int(part[2] or 0):
                raise ValueError("Partitioned raw payload part length mismatch.")
            if hashlib.sha256(part_bytes).hexdigest() != str(part[3] or ""):
                raise ValueError("Partitioned raw payload part checksum mismatch.")
            chunks.append(part_bytes)
            digest.update(part_bytes)
            actual_bytes += len(part_bytes)
        if len(chunks) != expected_parts or actual_bytes != expected_bytes:
            raise ValueError("Partitioned raw payload is incomplete.")
        if digest.hexdigest() != expected_sha:
            raise ValueError("Partitioned raw payload checksum mismatch.")
        return b"".join(chunks).decode("utf-8"), expected_sha


def _raw_state_payload_fingerprint(json_path: Path) -> str | None:
    connection = _raw_state_connection(json_path)
    if connection is None:
        return None
    with closing(connection) as conn:
        row = conn.execute(
            "SELECT payload_sha FROM state_payloads WHERE name = ?;",
            (json_path.stem,),
        ).fetchone()
        if row is None:
            return None
        if row[0]:
            return str(row[0])
        payload_row = conn.execute(
            "SELECT payload FROM state_payloads WHERE name = ?;",
            (json_path.stem,),
        ).fetchone()
    if payload_row is None:
        return None
    return hashlib.sha256(str(payload_row[0]).encode("utf-8")).hexdigest()


def _record_raw_artifact_fallback(path: Path, reason: str, exception: Exception | None = None) -> None:
    from tools.core.honesty_telemetry import record_honesty_event

    record_honesty_event(
        component="json_io",
        category="storage_fallback",
        operation="load_raw_artifact_path",
        subject=str(path),
        reason=reason,
        fallback="json_shadow_read",
        claim_impact="artifact_freshness_requires_validation",
        exception=exception,
    )


def raw_artifact_content_fingerprint(path: str | Path) -> str:
    """Return cache identity from SQLite primary payload, or the explicit JSON shadow fallback."""
    json_path = Path(path)
    if not is_raw_artifact_path(json_path):
        raise ValueError(f"Raw artifact path must point to a .raw JSON file: {json_path}")
    try:
        fingerprint = _raw_state_payload_fingerprint(json_path)
        if fingerprint is not None:
            return f"sqlite:{fingerprint}"
    except Exception as exc:
        _record_raw_artifact_fallback(
            json_path,
            "Raw artifact SQLite fingerprint could not be read.",
            exc,
        )
    if not json_path.exists():
        return "missing"
    _, fingerprint = _content_snapshot(json_path)
    return f"json_shadow:{fingerprint}"


def load_raw_artifact_path(path: str | Path, default: Any = None) -> Any:
    """Load one default or external-target raw artifact from SQLite first, then its JSON shadow."""
    json_path = Path(path)
    if not is_raw_artifact_path(json_path):
        raise ValueError(f"Raw artifact path must point to a .raw JSON file: {json_path}")
    if _is_managed_raw_artifact(json_path):
        return load_json_file(json_path, default)

    if (json_path.parent / "codemaps.db").exists():
        try:
            row = _raw_state_payload_row(json_path)
            if row is not None:
                return loads_json_strict(row[0])
            if json_path.exists():
                _record_raw_artifact_fallback(
                    json_path,
                    "External raw SQLite store has no state_payload row for this artifact.",
                )
        except Exception as exc:
            _record_raw_artifact_fallback(
                json_path,
                "External raw SQLite payload could not be read.",
                exc,
            )
    return load_json_file(json_path, default, bypass_proxy=True)


def load_raw_artifact_path_strict(path: str | Path) -> Any:
    """Load a raw artifact SQLite-first without collapsing missing and invalid states."""
    json_path = Path(path)
    if not is_raw_artifact_path(json_path):
        raise ValueError(f"Raw artifact path must point to a .raw JSON file: {json_path}")
    if _is_managed_raw_artifact(json_path):
        payload = load_json_strict(json_path)
        if payload is None:
            raise FileNotFoundError(json_path)
        return payload
    if (json_path.parent / "codemaps.db").exists():
        try:
            row = _raw_state_payload_row(json_path)
            if row is not None:
                return loads_json_strict(row[0])
            if json_path.exists():
                _record_raw_artifact_fallback(
                    json_path,
                    "External raw SQLite store has no state_payload row for this artifact.",
                )
        except Exception as exc:
            _record_raw_artifact_fallback(
                json_path,
                "External raw SQLite payload could not be read.",
                exc,
            )
    return load_json_strict(json_path, bypass_proxy=True)


def load_text_file(path: str | Path, default: str = "") -> str:
    """Load text file returning *default* if missing."""
    p = Path(path)
    if not p.exists():
        return default
    return p.read_text(encoding="utf-8", errors="replace")

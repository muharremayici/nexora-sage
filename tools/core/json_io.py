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
from tools.core.unmanaged_atomic_io import native_filesystem_path, sqlite_read_only_uri


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
    if not Path(native_filesystem_path(db_path)).is_file():
        return None
    from tools.core.operational_limits import sqlite_read_timeout_seconds

    uri = sqlite_read_only_uri(db_path)
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
        return _decode_raw_state_payload_parts(parts, expected_parts, expected_bytes, expected_sha, digest, chunks, actual_bytes)


def _decode_raw_state_payload_parts(parts, expected_parts, expected_bytes, expected_sha, digest, chunks, actual_bytes):
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


def load_raw_artifact_primary_with_identity(path: str | Path) -> tuple[Any, str] | None:
    """Read validated SQLite content and its identity together; never read a shadow."""
    json_path = Path(path)
    if not is_raw_artifact_path(json_path):
        raise ValueError("Primary content identity requires a raw JSON artifact path.")
    row = _raw_state_payload_row(json_path)
    if row is None:
        return None
    serialized, stored_sha = row
    return _decode_raw_primary_content_identity(serialized, stored_sha)


def _decode_raw_primary_content_identity(serialized, stored_sha):
    actual_sha = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    if stored_sha is not None and stored_sha != actual_sha:
        raise ValueError("Raw primary payload checksum mismatch.")
    payload = loads_json_strict(serialized)
    return payload, f"sqlite:{actual_sha}"


def raw_artifact_primary_content_fingerprint(path: str | Path) -> str | None:
    """Hash validated SQLite primary content; never certify a compatibility shadow."""
    read = load_raw_artifact_primary_with_identity(path)
    return read[1] if read is not None else None


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
    if not Path(native_filesystem_path(json_path)).exists():
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

    if Path(native_filesystem_path(json_path.parent / "codemaps.db")).is_file():
        try:
            row = _raw_state_payload_row(json_path)
            if row is not None:
                return loads_json_strict(row[0])
            if Path(native_filesystem_path(json_path)).exists():
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
    if Path(native_filesystem_path(json_path.parent / "codemaps.db")).is_file():
        try:
            row = _raw_state_payload_row(json_path)
            if row is not None:
                return loads_json_strict(row[0])
            if Path(native_filesystem_path(json_path)).exists():
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


# Private, explicit prototype only. No default reader or approval route calls it.
_PRIMARY_SNAPSHOT_CONTRACT_SHA256 = "57362044d17529d0913731621cec49987166369519f49f3bfcbfc45ef43843ac"


class PrimarySnapshotRefusal(ValueError):
    """The supplied-connection structural snapshot cannot be linked."""


def _primary_snapshot_need(condition, code):
    if not condition:
        raise PrimarySnapshotRefusal(code)


def _primary_snapshot_contract():
    path = Path(__file__).resolve().parents[2] / "config/schemas/comparison_primary_snapshot.schema.json"
    with path.open("rb") as stream:
        content = stream.read(8193)
    _primary_snapshot_need(len(content) <= 8192 and hashlib.sha256(content).hexdigest() == _PRIMARY_SNAPSHOT_CONTRACT_SHA256,
                           "primary_snapshot_contract_changed")
    return loads_json_strict(content.decode("utf-8"))["$defs"]["contract"]["const"]


def _primary_snapshot_integer(value, maximum, minimum=0):
    _primary_snapshot_need(type(value) is int and minimum <= value <= maximum, "primary_snapshot_integer_invalid")
    return value


def _primary_snapshot_digest(value):
    _primary_snapshot_need(type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value),
                           "primary_snapshot_digest_invalid")
    return value


def _primary_snapshot_blob(connection, table, rowid):
    # Only source-owned literal main-table names reach this helper.
    return connection.blobopen(table, "payload", rowid, readonly=True, name="main")


def _primary_snapshot_bytes(connection, table, rowid, maximum, metrics, expected_size=None):
    _primary_snapshot_integer(rowid, 2**53 - 1, 1)
    with _primary_snapshot_blob(connection, table, rowid) as blob:
        size = len(blob)
        _primary_snapshot_need(0 < size <= maximum, "primary_snapshot_blob_budget")
        _primary_snapshot_need(expected_size is None or size == expected_size, "primary_snapshot_blob_length")
        content = blob.read(size)
        metrics["blob_read_calls"] += 1
        metrics["blob_bytes_delivered"] += len(content)
        _primary_snapshot_need(len(content) == size, "primary_snapshot_short_read")
        extra = blob.read(1)
        metrics["blob_read_calls"] += 1
        metrics["blob_bytes_delivered"] += len(extra)
        _primary_snapshot_need(not extra, "primary_snapshot_blob_changed")
    return content


def _primary_snapshot_depth(text, maximum):
    """Resource-only nesting scan; original strict JSON decoder owns syntax."""
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            _primary_snapshot_need(depth <= maximum, "primary_snapshot_decode_depth")
        elif char in "]}":
            depth -= 1
            _primary_snapshot_need(depth >= 0, "primary_snapshot_decode_depth")


def _primary_snapshot_native_shape(value, contract):
    """Inspect fresh strict-decoder output; no second parser or metadata copy."""
    import math
    budget = [0, 0]

    def visit(item, depth):
        budget[0] += 1
        _primary_snapshot_need(depth <= contract["maximum_json_depth"] and budget[0] <= contract["decoded_nodes"],
                               "primary_snapshot_native_metadata")
        kind = type(item)
        if kind is str:
            _primary_snapshot_need(len(item) <= contract["decoded_scalar_bytes"] - budget[1], "primary_snapshot_native_metadata")
            for char in item:
                point = ord(char)
                _primary_snapshot_need(not 0xD800 <= point <= 0xDFFF, "primary_snapshot_native_metadata")
                budget[1] += 1 if point < 128 else 2 if point < 2048 else 3 if point < 65536 else 4
        elif item is None or kind in (bool, int, float):
            _primary_snapshot_need(kind is not int or abs(item) <= contract["maximum_native_integer"], "primary_snapshot_native_metadata")
            _primary_snapshot_need(kind is not float or math.isfinite(item), "primary_snapshot_native_metadata")
            budget[1] += 8
        elif kind is dict:
            for key, child in item.items():
                _primary_snapshot_need(type(key) is str, "primary_snapshot_native_metadata")
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif kind is list:
            for child in item:
                visit(child, depth + 1)
        else:
            raise PrimarySnapshotRefusal("primary_snapshot_native_metadata")
        _primary_snapshot_need(budget[1] <= contract["decoded_scalar_bytes"], "primary_snapshot_native_metadata")

    visit(value, 0)
    return budget


def read_comparison_primary_snapshot_from_connection(connection, expected):
    """Read supplied native main-DB rows, without opening/owning the connection.

    Explicit synthetic evidence path, not native origin, revision, signature or
    approval proof. The caller retains its active transaction. SQLite internals,
    callbacks/converters and hostile process/filesystem behavior are not bounded
    or authenticated by the connection type check or this metadata receipt.
    """
    _primary_snapshot_need(type(connection) is sqlite3.Connection, "primary_snapshot_connection_invalid")
    _primary_snapshot_need(connection.row_factory is None or connection.row_factory is sqlite3.Row,
                           "primary_snapshot_row_factory_invalid")
    _primary_snapshot_need(connection.text_factory is str, "primary_snapshot_text_factory_invalid")
    _primary_snapshot_need(connection.in_transaction, "primary_snapshot_transaction_required")
    _primary_snapshot_need(type(expected) is dict and len(expected) == 2 and all(type(k) is str for k in expected)
                           and set(expected) == {"full_payload_sha256", "canonical_entries_sha256"}, "primary_snapshot_expected_invalid")
    full_expected = _primary_snapshot_digest(expected["full_payload_sha256"])
    entries_expected = _primary_snapshot_digest(expected["canonical_entries_sha256"])
    contract = _primary_snapshot_contract()
    with closing(connection.execute("PRAGMA main.encoding")) as cursor:
        encoding = cursor.fetchone()[0]
    _primary_snapshot_need(encoding == "UTF-8", "primary_snapshot_database_encoding")
    metrics = {"blob_read_calls": 0, "blob_bytes_delivered": 0, "part_metadata_rows": 0}
    # Do not fetch an unbounded TEXT/BLOB cell through SQL. Metadata strings are
    # prefix-limited; blob handles expose byte length before any payload read.
    with closing(connection.execute(
        "SELECT rowid, typeof(payload), substr(payload_sha,1,65), length(payload_sha), typeof(payload_sha), "
        "payload_bytes, typeof(payload_bytes), substr(storage_mode,1,33), length(storage_mode), "
        "substr(generation_id,1,129), length(generation_id), part_count, typeof(part_count) "
        "FROM main.state_payloads WHERE name = ? LIMIT 2", (contract["artifact_id"],),
    )) as cursor:
        rows = cursor.fetchmany(2)
    _primary_snapshot_need(bool(rows), "primary_snapshot_missing")
    _primary_snapshot_need(len(rows) == 1, "primary_snapshot_ambiguous_primary")
    row = rows[0]
    rowid, kind, stored_sha, sha_length, sha_kind, size, size_kind, mode, mode_length, generation, generation_length, count, count_kind = row
    _primary_snapshot_need(kind == "text" and sha_kind == "text" and sha_length == 64, "primary_snapshot_primary_metadata")
    _primary_snapshot_digest(stored_sha)
    _primary_snapshot_need(stored_sha == full_expected, "primary_snapshot_stale_full_identity")
    _primary_snapshot_need(size_kind == count_kind == "integer", "primary_snapshot_primary_metadata")
    _primary_snapshot_integer(size, contract["maximum_payload_bytes"], 1)
    _primary_snapshot_integer(count, contract["maximum_parts"])
    _primary_snapshot_need(mode in ("inline_json", "partitioned_json_v1") and mode_length == len(mode), "primary_snapshot_storage_mode")
    if mode == "inline_json":
        _primary_snapshot_need(count == 0 and generation in (None, ""), "primary_snapshot_inline_metadata")
        raw = _primary_snapshot_bytes(connection, "state_payloads", rowid, contract["maximum_payload_bytes"], metrics, size)
        serialized = raw.decode("utf-8")
    else:
        _primary_snapshot_need(type(generation) is str and 0 < generation_length <= contract["maximum_generation_characters"]
                               and generation_length == len(generation)
                               and all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in generation)
                               and count > 0, "primary_snapshot_generation_invalid")
        manifest_raw = _primary_snapshot_bytes(connection, "state_payloads", rowid, contract["maximum_manifest_bytes"], metrics)
        manifest_text = manifest_raw.decode("utf-8")
        _primary_snapshot_depth(manifest_text, contract["maximum_json_depth"])
        manifest = loads_json_strict(manifest_text)
        _primary_snapshot_need(type(manifest) is dict and set(manifest) == {"__sage_partitioned_payload__"}, "primary_snapshot_manifest_invalid")
        descriptor = manifest["__sage_partitioned_payload__"]
        _primary_snapshot_need(type(descriptor) is dict and set(descriptor) == {
            "format", "generation_id", "part_count", "part_size_bytes", "payload_bytes", "payload_sha256"}, "primary_snapshot_manifest_invalid")
        part_size = _primary_snapshot_integer(descriptor["part_size_bytes"], contract["maximum_part_bytes"], 1)
        _primary_snapshot_integer(descriptor["part_count"], contract["maximum_parts"], 1)
        _primary_snapshot_integer(descriptor["payload_bytes"], contract["maximum_payload_bytes"], 1)
        _primary_snapshot_need(descriptor["format"] == mode and descriptor["generation_id"] == generation
                               and descriptor["part_count"] == count and descriptor["payload_bytes"] == size
                               and descriptor["payload_sha256"] == stored_sha and (size + part_size - 1) // part_size == count,
                               "primary_snapshot_manifest_mismatch")
        parts = connection.execute(
            "SELECT rowid,part_index,payload_bytes,typeof(payload),substr(payload_sha,1,65),length(payload_sha),typeof(payload_bytes) "
            "FROM main.state_payload_parts WHERE name = ? AND generation_id = ? ORDER BY part_index LIMIT ?",
            (contract["artifact_id"], generation, contract["maximum_parts"] + 1),
        )

        def bounded_parts():
            total = 0
            for index, part in enumerate(parts):
                metrics["part_metadata_rows"] += 1
                _primary_snapshot_need(index < count, "primary_snapshot_extra_part")
                part_rowid, part_index, part_bytes, part_kind, part_sha, part_sha_length, part_size_kind = part
                _primary_snapshot_integer(part_index, contract["maximum_parts"] - 1)
                _primary_snapshot_integer(part_bytes, part_size, 1)
                _primary_snapshot_need(part_index == index and part_kind == "blob" and part_size_kind == "integer"
                                       and part_sha_length == 64, "primary_snapshot_part_metadata")
                _primary_snapshot_digest(part_sha)
                expected_part_size = part_size if index < count - 1 else size - part_size * (count - 1)
                _primary_snapshot_need(part_bytes == expected_part_size and total + part_bytes <= size,
                                       "primary_snapshot_part_budget")
                value = _primary_snapshot_bytes(connection, "state_payload_parts", part_rowid, part_size, metrics, part_bytes)
                total += len(value)
                yield part_index, value, part_bytes, part_sha

        # Original sequence/length/checksum/aggregate/decode core, not another
        # partition protocol or a caller-supplied verification callback.
        try:
            serialized, _ = _decode_raw_state_payload_parts(bounded_parts(), count, size, stored_sha, hashlib.sha256(), [], 0)
        finally:
            parts.close()
    _primary_snapshot_depth(serialized, contract["maximum_json_depth"])
    payload, full_identity = _decode_raw_primary_content_identity(serialized, stored_sha)
    # The original strict decoder made this owned native tree. Inspect it in
    # place; importing compiler-layer copy helpers here would create a cycle.
    measured = _primary_snapshot_native_shape(payload, contract)
    owned = payload
    _primary_snapshot_need(type(owned) is dict and "entries" in owned and set(owned) <= set(contract["wrapper_fields"]),
                           "primary_snapshot_wrapper_invalid")
    _primary_snapshot_need(all(type(owned[k]) is dict for k in ("meta", "summary", "integrity") if k in owned),
                           "primary_snapshot_wrapper_invalid")
    _primary_snapshot_need(type(owned["entries"]) is list and len(owned["entries"]) <= contract["maximum_entries"],
                           "primary_snapshot_entries_invalid")
    projection = {"entries": owned["entries"]}
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    form_bound = 32 * contract["decoded_nodes"] + 6 * contract["decoded_scalar_bytes"]
    _primary_snapshot_need(len(canonical) <= form_bound, "primary_snapshot_canonical_budget")
    entries_identity = hashlib.sha256(canonical).hexdigest()
    _primary_snapshot_need(entries_identity == entries_expected, "primary_snapshot_stale_entries_identity")
    _primary_snapshot_need(connection.in_transaction, "primary_snapshot_transaction_changed")
    return {
        "status": "SUPPLIED_CONNECTION_PRIMARY_SNAPSHOT_LINKED_NOT_CURRENT_APPROVAL",
        "full_payload_identity": full_identity, "canonical_entries_sha256": entries_identity,
        "entries_projection": projection, "storage_mode": mode, "payload_generation_id": generation,
        "payload_bytes": size, "parts": count, "io_observation": metrics,
        "logical_cuts": {"predecode_source_bytes": contract["maximum_payload_bytes"],
            "predecode_logical_tree_node_upper": 2 * contract["maximum_payload_bytes"] + 1,
            "postdecode_nodes": measured[0], "postdecode_scalar_bytes": measured[1],
            "canonical_form_bytes_upper": form_bound},
        "flags": {name: False for name in (
            "connection_owner_authenticated", "source_origin_authenticated", "domain_generation_authenticated",
            "current_primary_revision_verified", "selected_signature_verified", "human_origin_authenticated",
            "key_origin_authenticated", "single_use_consumed", "issuer_token_created", "runtime_admission",
            "production_enabled", "publication_authority", "complete_cost_priced",
        )},
    }

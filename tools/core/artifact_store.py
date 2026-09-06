from __future__ import annotations

import logging
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_text_file
from tools.core.persistence_limits import load_persistence_limits
from tools.core.db import SQLiteManager
from tools.core.path_identity import strip_current_directory_prefix
from tools.core.stdio import best_effort_print

logger = logging.getLogger("SAGE.ArtifactStore")

_write_locks: dict[str, threading.Lock] = {}
_write_threads: dict[str, list[threading.Thread]] = {}
_shadow_worker_ids: dict[threading.Thread, str] = {}
_shadow_worker_records: dict[str, dict[str, Any]] = {}
_active_shadow_run_id = ""
_locks_mutex = threading.Lock()
_PROFILED_ARTIFACTS = {"atlas", "genome", "fractal_map", "audit_report", "quality_gate", "state_flow"}
_PROFILE_LOG_THRESHOLD_SECONDS = 0.25
_SHADOW_LIFECYCLE_IDENTITY_LIMIT = 50


class UnsafeScopedAtlasProjectionError(RuntimeError):
    """Raised before a scoped Atlas write could erase unselected project truth."""


class ArtifactPrimaryWriteError(RuntimeError):
    """Raised when the authoritative artifact store cannot commit a payload."""


class CorruptPartitionedPayloadError(RuntimeError):
    """Raised when a partitioned state payload cannot prove complete byte identity."""


def _artifact_profile_log(profile_timings: dict[str, Any]) -> None:
    message = json.dumps(profile_timings, ensure_ascii=False)
    logger.info("[ARTIFACT_STORE_PROFILE] %s", message)


def _record_store_degradation(operation: str, subject: str, exc: BaseException, fallback: str) -> None:
    try:
        from tools.core.honesty_telemetry import record_honesty_event

        record_honesty_event(
            component="artifact_store",
            category="storage_fallback",
            operation=operation,
            subject=subject,
            severity="error",
            reason="SQLite primary storage path failed",
            fallback=fallback,
            claim_impact="artifact_freshness_requires_validation",
            evidence_source="sqlite_exception",
            exception=exc,
        )
    except Exception as telemetry_exc:
        logger.error("[HONESTY] Artifact store degradation telemetry failed: %s", telemetry_exc)


def _record_store_missing_row_event(name: str, details: dict[str, Any]) -> None:
    try:
        from tools.core.honesty_telemetry import record_honesty_event

        record_honesty_event(
            component="artifact_store",
            category="storage_fallback",
            operation="load_raw_missing_sqlite_row",
            subject=name,
            severity="warning",
            reason="SQLite primary state_payloads row was missing for a managed raw artifact.",
            fallback="shadow_json_read_and_sqlite_self_heal",
            claim_impact="artifact_freshness_self_healed",
            evidence_source="missing_state_payload",
            details=details,
        )
    except Exception as telemetry_exc:
        logger.error("[HONESTY] Artifact missing-row telemetry failed: %s", telemetry_exc)


def _payload_sha(serialized: str | bytes) -> str:
    payload_bytes = serialized if isinstance(serialized, bytes) else serialized.encode("utf-8", errors="replace")
    return hashlib.sha256(payload_bytes).hexdigest()


def _payload_digest(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _payload_sha(serialized)


def _source_snapshot_projection_scope() -> dict[str, set[str]] | None:
    """Return the current surgical source snapshot scope, if one is active."""
    raw_scope = DYNAMIC_CONFIG.get("_source_snapshot_projection_scope")
    if not isinstance(raw_scope, dict):
        return None
    scope: dict[str, set[str]] = {}
    for project_key, paths in raw_scope.items():
        if not isinstance(paths, (list, tuple, set)):
            continue
        normalized = {
            str(path).replace("\\", "/").strip("/")
            for path in paths
            if str(path or "").strip()
        }
        scope[str(project_key)] = normalized
    return scope


def _source_snapshot_log(message: str) -> None:
    best_effort_print(f"[source-snapshots] {message}", flush=True)


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_violation_paths(violation: dict[str, Any], project_path: str = "") -> list[str]:
    candidates: list[str] = []
    for key in ("atlas_rel_path", "file", "path", "workspace_rel", "repo_relative_path"):
        value = str(violation.get(key) or "").replace("\\", "/").strip()
        if not value:
            continue
        if "::" in value:
            value = value.split("::", 1)[1]
        for candidate in (value, strip_current_directory_prefix(value)):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        normalized_project_path = str(project_path or "").replace("\\", "/").strip().strip("/")
        if normalized_project_path and value.startswith(normalized_project_path + "/"):
            stripped = strip_current_directory_prefix(value[len(normalized_project_path) + 1 :])
            if stripped and stripped not in candidates:
                candidates.append(stripped)
        if value.startswith("src/"):
            stripped_src = value[4:]
            if stripped_src and stripped_src not in candidates:
                candidates.append(stripped_src)
    return candidates


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def activate_shadow_write_run(run_id: str) -> None:
    """Bind asynchronous compatibility shadows to one process-local pipeline run."""

    normalized = str(run_id or "").strip()
    if not normalized:
        raise ValueError("Shadow-write run identity must be non-empty.")
    global _active_shadow_run_id
    with _locks_mutex:
        if _active_shadow_run_id and _active_shadow_run_id != normalized:
            raise RuntimeError(
                f"Shadow writes are already bound to another run: {_active_shadow_run_id}"
            )
        _active_shadow_run_id = normalized


def current_shadow_write_run_id() -> str:
    with _locks_mutex:
        return _active_shadow_run_id


def _shadow_lifecycle_snapshot_locked(run_id: str = "") -> dict[str, Any]:
    selected = [
        dict(record)
        for record in _shadow_worker_records.values()
        if not run_id or str(record.get("run_id") or "") == run_id
    ]
    selected.sort(key=lambda record: (float(record.get("started_monotonic") or 0.0), str(record.get("worker_id") or "")))
    public_workers = [
        {
            "worker_id": str(record.get("worker_id") or "not_available"),
            "run_id": str(record.get("run_id") or "not_available"),
            "artifact": str(record.get("artifact") or "not_available"),
            "thread_name": str(record.get("thread_name") or "not_available"),
            "status": str(record.get("status") or "unknown"),
            "started_at": str(record.get("started_at") or "not_available"),
            "completed_at": str(record.get("completed_at") or "not_available"),
            "error_type": str(record.get("error_type") or "none"),
        }
        for record in selected
    ]
    pending = [worker for worker in public_workers if worker["status"] == "running"]
    failed = [worker for worker in public_workers if worker["status"] == "failed"]
    artifacts = sorted({worker["artifact"] for worker in public_workers})
    identities_truncated = (
        len(public_workers) > _SHADOW_LIFECYCLE_IDENTITY_LIMIT
        or len(artifacts) > _SHADOW_LIFECYCLE_IDENTITY_LIMIT
    )
    return {
        "run_id": run_id or "all",
        "started_count": len(public_workers),
        "completed_count": sum(1 for worker in public_workers if worker["status"] == "completed"),
        "failed_count": len(failed),
        "pending_count": len(pending),
        "worker_ids": [
            worker["worker_id"] for worker in public_workers[:_SHADOW_LIFECYCLE_IDENTITY_LIMIT]
        ],
        "artifacts": artifacts[:_SHADOW_LIFECYCLE_IDENTITY_LIMIT],
        "pending_worker_ids": [
            worker["worker_id"] for worker in pending[:_SHADOW_LIFECYCLE_IDENTITY_LIMIT]
        ],
        "workers": public_workers[:_SHADOW_LIFECYCLE_IDENTITY_LIMIT],
        "identity_limit": _SHADOW_LIFECYCLE_IDENTITY_LIMIT,
        "identities_truncated": identities_truncated,
    }


def shadow_write_lifecycle_snapshot(run_id: str = "") -> dict[str, Any]:
    """Return bounded in-process lifecycle metadata without paths or payload content."""

    with _locks_mutex:
        return _shadow_lifecycle_snapshot_locked(str(run_id or "").strip())


def release_shadow_write_run(run_id: str) -> dict[str, Any]:
    """Release one run only after all of its compatibility shadows are terminal."""

    normalized = str(run_id or "").strip()
    global _active_shadow_run_id
    with _locks_mutex:
        snapshot = _shadow_lifecycle_snapshot_locked(normalized)
        if snapshot["pending_count"]:
            raise RuntimeError(
                f"Cannot release shadow-write run {normalized}; "
                f"{snapshot['pending_count']} worker(s) remain active."
            )
        for worker_id in list(_shadow_worker_records):
            if str(_shadow_worker_records[worker_id].get("run_id") or "") == normalized:
                del _shadow_worker_records[worker_id]
        if _active_shadow_run_id == normalized:
            _active_shadow_run_id = ""
        return snapshot


def _complete_shadow_worker(worker_id: str, *, status: str, error_type: str = "none") -> None:
    if not worker_id:
        return
    with _locks_mutex:
        record = _shadow_worker_records.get(worker_id)
        if record is None:
            return
        record["status"] = status
        record["completed_at"] = _utc_now()
        record["error_type"] = str(error_type or "none")


def _prune_finished_shadow_workers_locked() -> None:
    for name in list(_write_threads):
        _write_threads[name] = [thread for thread in _write_threads[name] if thread.is_alive()]
        if not _write_threads[name]:
            del _write_threads[name]
    for thread in list(_shadow_worker_ids):
        if not thread.is_alive():
            worker_id = _shadow_worker_ids.pop(thread)
            record = _shadow_worker_records.get(worker_id)
            if record is not None and str(record.get("status") or "") == "running":
                record["status"] = "failed"
                record["completed_at"] = _utc_now()
                record["error_type"] = "WorkerExitedWithoutTerminalRecord"
    for worker_id in list(_shadow_worker_records):
        record = _shadow_worker_records[worker_id]
        if (
            str(record.get("status") or "") != "running"
            and str(record.get("run_id") or "") != _active_shadow_run_id
        ):
            del _shadow_worker_records[worker_id]


def _bg_write_worker(
    lock: threading.Lock,
    path: Path,
    payload: Any,
    indent: int,
    worker_id: str = "",
) -> None:
    with lock:
        try:
            save_json_atomic(path, payload, indent=indent, bypass_proxy=True)
        except BaseException as e:
            import sys
            print(f"[SQLITE] Background backup write failed for {path.name}: {e}", file=sys.stderr)
            _complete_shadow_worker(worker_id, status="failed", error_type=type(e).__name__)
        else:
            _complete_shadow_worker(worker_id, status="completed")


def flush_shadow_writes_report(timeout: float | None = 10.0, *, run_id: str = "") -> dict[str, Any]:
    """Join selected shadow workers and return privacy-bounded lifecycle evidence."""

    normalized = str(run_id or "").strip()
    started = time.perf_counter()
    with _locks_mutex:
        threads = []
        for bucket in _write_threads.values():
            for thread in bucket:
                if not thread.is_alive():
                    continue
                worker_id = _shadow_worker_ids.get(thread, "")
                record = _shadow_worker_records.get(worker_id, {})
                if normalized and str(record.get("run_id") or "") != normalized:
                    continue
                threads.append(thread)

    if timeout is None:
        for thread in threads:
            thread.join()
    else:
        deadline = time.monotonic() + max(float(timeout), 0.0)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)

    with _locks_mutex:
        _prune_finished_shadow_workers_locked()
        snapshot = _shadow_lifecycle_snapshot_locked(normalized)
        snapshot["global_pending_count"] = sum(
            1
            for record in _shadow_worker_records.values()
            if str(record.get("status") or "") == "running"
        )
    snapshot["complete"] = snapshot["pending_count"] == 0
    snapshot["timed_out"] = bool(timeout is not None and snapshot["pending_count"])
    snapshot["wait_seconds"] = round(time.perf_counter() - started, 3)
    return snapshot


def flush_shadow_writes(timeout: float | None = 10.0) -> bool:
    """Wait for pending JSON shadow exports to finish.

    Runtime reads use SQLite as the primary source of truth, but release and
    parity validators sometimes inspect compatibility shadow JSON files. This
    helper gives those callers a deterministic sync point without making every
    engine block on disk I/O.
    """

    report = flush_shadow_writes_report(timeout=timeout)
    if report["complete"]:
        with _locks_mutex:
            for worker_id in list(_shadow_worker_records):
                if str(_shadow_worker_records[worker_id].get("run_id") or "") == "not_available":
                    del _shadow_worker_records[worker_id]
    return bool(report["complete"])

_RAW_ALIASES = {
    "atlas": "atlas.json",
    "genome": "genome.json",
    "react_ecosystem_analysis": "react_ecosystem_analysis.json",
    "react_runtime_intelligence": "react_runtime_intelligence.json",
    "react_compiler_readiness": "react_compiler_readiness.json",
}

_CONFIG_ALIASES = {
    "codemaps_config": "codemaps.config.json",
    "codemaps_discovery": "codemaps.discovery.json",
    "language_registry": "language_registry.json",
    "framework_capabilities": "framework_capabilities.json",
    "codemaps_adapters": "codemaps.adapters.json",
}


def _safe_resolve(base_dir: Path, name: str) -> Path:
    # Normalize slashes
    cleaned_name = name.replace("\\", "/").lstrip("/")
    # Resolve the path relative to base_dir
    target_path = Path(base_dir / cleaned_name).resolve()
    try:
        # relative_to will raise ValueError if target_path is not inside base_dir
        target_path.relative_to(base_dir.resolve())
    except ValueError:
        raise ValueError(f"Path traversal detected: {name} attempts to escape {base_dir}")
    return target_path


class ArtifactStore:
    """Thin artifact backend boundary.

    Supports Feature-Flag supported Hybrid SQLite (Dual-Write & Shadow Cache).
    Engines can transparently depend on this boundary without changing their logic.
    """

    def __init__(self, raw_dir: Path | None = None):
        # Feature Flag: Checked directly from codemaps.config.json
        self._raw_dir = Path(raw_dir).resolve() if raw_dir is not None else RAW_DIR
        self.use_sqlite = DYNAMIC_CONFIG.get("use_sqlite", False)
        self.backend = "hybrid_sqlite" if self.use_sqlite else "json"
        self.db_manager = SQLiteManager(self._raw_dir / "codemaps.db")
        self._schema_initialized = False
        limits = load_persistence_limits(CONFIG_DIR / "pipeline_execution_policy.json")
        self.state_payload_inline_limit_bytes = limits["state_payload_inline_limit_bytes"]
        self.state_payload_part_size_bytes = limits["state_payload_part_size_bytes"]
        self.atlas_staging_batch_size = limits["atlas_staging_batch_size"]
        self.atlas_staging_file_payload_limit_bytes = limits["atlas_staging_file_payload_limit_bytes"]

    def initialize_schema(self) -> None:
        """Initialize SQLite database schema if enabled."""
        if self.use_sqlite:
            try:
                self.db_manager.initialize_schema()
                self._schema_initialized = True
                logger.info("[SQLITE] Relational schema successfully initialized.")
            except Exception as e:
                logger.error(f"[SQLITE] Failed to initialize schema: {e}")

    def _ensure_schema(self) -> None:
        """Create the SQLite schema once before read/write access."""
        if not self.use_sqlite or self._schema_initialized:
            return
        self.db_manager.initialize_schema()
        self._schema_initialized = True

    def raw_path(self, name: str) -> Path:
        from tools.core.db import get_current_tenant
        tenant_id = get_current_tenant()
        base = self._raw_dir.parent / "tenants" / tenant_id / ".raw" if tenant_id else self._raw_dir
        base.mkdir(parents=True, exist_ok=True)
        filename = _RAW_ALIASES.get(name, f"{name}.json")
        return _safe_resolve(base, filename)

    def config_path(self, name: str) -> Path:
        from tools.core.db import get_current_tenant
        tenant_id = get_current_tenant()
        base = CONFIG_DIR.parent / "tenants" / tenant_id / "config" if tenant_id else CONFIG_DIR
        base.mkdir(parents=True, exist_ok=True)
        filename = _CONFIG_ALIASES.get(name, f"{name}.json")
        return _safe_resolve(base, filename)

    def report_path(self, name: str, suffix: str = ".md") -> Path:
        from tools.core.db import get_current_tenant
        tenant_id = get_current_tenant()
        base = REPORTS_DIR.parent / "tenants" / tenant_id / "reports" if tenant_id else REPORTS_DIR
        base.mkdir(parents=True, exist_ok=True)
        filename = name if name.endswith(suffix) else f"{name}{suffix}"
        return _safe_resolve(base, filename)

    def _load_state_payload_row(self, conn, row) -> Any:
        row_keys = set(row.keys())
        storage_mode = str(row["storage_mode"] or "inline_json") if "storage_mode" in row_keys else "inline_json"
        if storage_mode == "inline_json":
            return json.loads(row["payload"])
        if storage_mode != "partitioned_json_v1":
            raise CorruptPartitionedPayloadError(f"Unsupported state payload storage mode: {storage_mode}")

        manifest = json.loads(row["payload"])
        descriptor = manifest.get("__sage_partitioned_payload__") if isinstance(manifest, dict) else None
        if not isinstance(descriptor, dict) or descriptor.get("format") != "partitioned_json_v1":
            raise CorruptPartitionedPayloadError("Partitioned state payload manifest is missing or invalid.")
        generation_id = str(row["generation_id"] or descriptor.get("generation_id") or "")
        expected_parts = int(row["part_count"] or descriptor.get("part_count") or 0)
        expected_bytes = int(row["payload_bytes"] or descriptor.get("payload_bytes") or 0)
        expected_sha = str(row["payload_sha"] or descriptor.get("payload_sha256") or "")
        if not generation_id or expected_parts < 1 or expected_bytes < 1 or not expected_sha:
            raise CorruptPartitionedPayloadError("Partitioned state payload identity is incomplete.")

        digest = hashlib.sha256()
        actual_bytes = 0
        actual_parts = 0
        spool = tempfile.SpooledTemporaryFile(
            max_size=max(self.state_payload_inline_limit_bytes, self.state_payload_part_size_bytes),
            mode="w+b",
        )
        try:
            cursor = conn.execute(
                """
                SELECT part_index, payload, payload_bytes, payload_sha
                FROM state_payload_parts
                WHERE name = ? AND generation_id = ?
                ORDER BY part_index;
                """,
                (str(row["name"]), generation_id),
            )
            try:
                for part in cursor:
                    if int(part["part_index"]) != actual_parts:
                        raise CorruptPartitionedPayloadError("Partitioned state payload sequence is not contiguous.")
                    part_bytes = bytes(part["payload"])
                    if len(part_bytes) != int(part["payload_bytes"] or 0):
                        raise CorruptPartitionedPayloadError("Partitioned state payload part length mismatch.")
                    if _payload_sha(part_bytes) != str(part["payload_sha"] or ""):
                        raise CorruptPartitionedPayloadError("Partitioned state payload part checksum mismatch.")
                    spool.write(part_bytes)
                    digest.update(part_bytes)
                    actual_bytes += len(part_bytes)
                    actual_parts += 1
            finally:
                cursor.close()
            if actual_parts != expected_parts or actual_bytes != expected_bytes:
                raise CorruptPartitionedPayloadError("Partitioned state payload is incomplete.")
            if digest.hexdigest() != expected_sha:
                raise CorruptPartitionedPayloadError("Partitioned state payload checksum mismatch.")
            spool.seek(0)
            with io.TextIOWrapper(spool, encoding="utf-8") as text_stream:
                return json.load(text_stream)
        finally:
            if not spool.closed:
                spool.close()

    def load_raw(self, name: str, default: Any = None) -> Any:
        # Trigger path resolution/validation first to prevent path traversal bypass!
        json_path = self.raw_path(name)

        if self.use_sqlite:
            try:
                self._ensure_schema()
                sqlite_payload = None
                sqlite_row_found = False
                sqlite_payload_needs_sha = False
                sqlite_source_mtime = None
                with self.db_manager.get_connection() as conn:
                    row = conn.execute(
                        "SELECT * FROM state_payloads WHERE name = ?;",
                        (name,),
                    ).fetchone()
                    if row:
                        sqlite_payload = self._load_state_payload_row(conn, row)
                        sqlite_row_found = True
                        sqlite_payload_needs_sha = not bool(row["payload_sha"])
                        sqlite_source_mtime = row["source_mtime"]
                if sqlite_row_found:
                    # Close the read connection before a legacy-row self-heal opens
                    # its write transaction; Windows otherwise may retain a DB lock.
                    if sqlite_payload_needs_sha:
                        self._save_payload_to_state_table(
                            name,
                            sqlite_payload,
                            source_mtime=sqlite_source_mtime,
                        )
                    return sqlite_payload
            except Exception as exc:
                logger.warning("[SQLITE] Failed to load raw payload %s from database: %s. Falling back to JSON.", name, exc)
                _record_store_degradation("load_raw", name, exc, "shadow_json_read_and_sqlite_self_heal")

        if not json_path.exists():
            return default

        value = load_json_file(json_path, default, bypass_proxy=True)
        if self.use_sqlite and value is not None and not (isinstance(value, dict) and value.get("placeholder")):
            try:
                if name == "atlas" and isinstance(value, dict):
                    with self.db_manager.transaction():
                        self._save_payload_to_state_table(name, value, source_mtime=float(json_path.stat().st_mtime))
                        self._save_atlas_to_sqlite(value)
                else:
                    self._save_payload_to_state_table(name, value, source_mtime=float(json_path.stat().st_mtime))
                if name == "audit_report" and isinstance(value, dict):
                    self._save_audit_findings_to_sqlite(value)
                    self._save_artifact_facts_to_sqlite(name, value)
                if name == "quality_gate" and isinstance(value, dict):
                    self._save_artifact_facts_to_sqlite(name, value)
                _record_store_missing_row_event(
                    name,
                    {
                        "shadow_path": str(json_path),
                        "shadow_mtime": float(json_path.stat().st_mtime),
                    },
                )
            except Exception as exc:
                logger.error("[SQLITE] Failed to self-heal payload %s into database: %s", name, exc)
        return value

    def raw_metadata(self, name: str) -> dict[str, Any]:
        """Return canonical payload metadata without loading the payload body."""
        json_path = self.raw_path(name)
        if self.use_sqlite:
            try:
                self._ensure_schema()
                with self.db_manager.get_connection() as conn:
                    row = conn.execute(
                        "SELECT payload_sha, payload_bytes, storage_mode, generation_id, part_count, source_mtime, updated_at FROM state_payloads WHERE name = ?;",
                        (name,),
                    ).fetchone()
                if row:
                    return {
                        "truth_source": "sqlite_state_payloads",
                        "payload_sha": str(row["payload_sha"] or ""),
                        "payload_bytes": int(row["payload_bytes"] or 0),
                        "storage_mode": str(row["storage_mode"] or "inline_json"),
                        "generation_id": str(row["generation_id"] or ""),
                        "part_count": int(row["part_count"] or 0),
                        "source_mtime": float(row["source_mtime"] or 0.0),
                        "updated_at": str(row["updated_at"] or ""),
                    }
            except Exception as exc:
                logger.warning("[SQLITE] Failed to load raw metadata %s: %s. Falling back to JSON metadata.", name, exc)
                _record_store_degradation("raw_metadata", name, exc, "shadow_json_metadata")
        if not json_path.exists():
            return {"truth_source": "missing", "payload_sha": "", "source_mtime": 0.0, "updated_at": ""}
        return {
            "truth_source": "shadow_json_fallback",
            "payload_sha": "",
            "source_mtime": float(json_path.stat().st_mtime),
            "updated_at": "",
        }

    def save_raw(self, name: str, payload: Any, indent: int = 2) -> dict[str, float | bool | str | int]:
        profile_start = time.perf_counter()
        profile_timings: dict[str, float | bool | str] = {
            "artifact": str(name),
            "sqlite_enabled": bool(self.use_sqlite),
        }
        # Trigger path resolution/validation first!
        self.raw_path(name)
        snapshot_scope = (
            _source_snapshot_projection_scope()
            if self.use_sqlite and name == "atlas" and isinstance(payload, dict)
            else None
        )
        if self.use_sqlite:
            try:
                schema_start = time.perf_counter()
                self._ensure_schema()
                state_start = time.perf_counter()
                if name == "atlas" and isinstance(payload, dict):
                    with self.db_manager.transaction():
                        state_profile = self._save_payload_to_state_table(name, payload)
                        projection_profile = self._save_atlas_to_sqlite(payload)
                    if isinstance(projection_profile, dict):
                        profile_timings.update(projection_profile)
                else:
                    state_profile = self._save_payload_to_state_table(name, payload)
                profile_timings["schema_seconds"] = round(state_start - schema_start, 3)
                profile_timings["state_payload_seconds"] = round(time.perf_counter() - state_start, 3)
                profile_timings.update(state_profile)
            except UnsafeScopedAtlasProjectionError:
                raise
            except Exception as exc:
                if snapshot_scope:
                    raise UnsafeScopedAtlasProjectionError(
                        "Scoped Atlas primary transaction failed before commit."
                    ) from exc
                logger.error("[SQLITE] Failed to save raw payload %s to database: %s", name, exc, exc_info=True)
                raise ArtifactPrimaryWriteError(
                    f"SQLite primary artifact write failed for {name}."
                ) from exc

        if self.use_sqlite and name == "audit_report" and isinstance(payload, dict):
            try:
                audit_index_start = time.perf_counter()
                self._save_audit_findings_to_sqlite(payload)
                self._save_artifact_facts_to_sqlite(name, payload)
                profile_timings["audit_relational_index_seconds"] = round(time.perf_counter() - audit_index_start, 3)
                logger.info("[SQLITE] Audit findings successfully indexed in database.")
            except Exception as e:
                logger.error(f"[SQLITE] Failed to populate audit findings table: {e}", exc_info=True)

        if self.use_sqlite and name == "quality_gate" and isinstance(payload, dict):
            try:
                quality_index_start = time.perf_counter()
                self._save_artifact_facts_to_sqlite(name, payload)
                profile_timings["quality_facts_index_seconds"] = round(time.perf_counter() - quality_index_start, 3)
            except Exception as e:
                logger.error(f"[SQLITE] Failed to populate quality gate artifact facts: {e}", exc_info=True)

        # JSON can be deferred only after SQLite has committed the primary payload.
        # JSON-only mode writes synchronously because the file is the authoritative store.
        with _locks_mutex:
            if name not in _write_locks:
                _write_locks[name] = threading.Lock()
            file_lock = _write_locks[name]

        force_sync_shadow = os.environ.get("SAGE_SYNC_SHADOW_WRITES") == "1"
        if not self.use_sqlite:
            try:
                with file_lock:
                    save_json_atomic(self.raw_path(name), payload, indent=indent, bypass_proxy=True)
            except Exception as exc:
                raise ArtifactPrimaryWriteError(
                    f"JSON primary artifact write failed for {name}."
                ) from exc
            profile_timings["shadow_thread_started"] = False
            profile_timings["shadow_write_mode"] = "synchronous_primary_json"
        elif force_sync_shadow:
            _bg_write_worker(file_lock, self.raw_path(name), payload, indent)
            profile_timings["shadow_thread_started"] = False
            profile_timings["shadow_write_mode"] = "synchronous_bounded_process"
        else:
            worker_id = f"shadow-{uuid.uuid4().hex[:16]}"
            run_id = current_shadow_write_run_id() or "not_available"
            thread_name = f"SAGE.SaveBackup.{name}.{worker_id[-6:]}"
            thread = threading.Thread(
                target=_bg_write_worker,
                args=(file_lock, self.raw_path(name), payload, indent, worker_id),
                name=thread_name,
            )
            thread.daemon = False
            with _locks_mutex:
                _prune_finished_shadow_workers_locked()
                _shadow_worker_records[worker_id] = {
                    "worker_id": worker_id,
                    "run_id": run_id,
                    "artifact": str(name),
                    "thread_name": thread_name,
                    "status": "running",
                    "started_at": _utc_now(),
                    "started_monotonic": time.perf_counter(),
                    "completed_at": "not_available",
                    "error_type": "none",
                }
                _write_threads.setdefault(name, []).append(thread)
                _shadow_worker_ids[thread] = worker_id
            try:
                thread.start()
            except Exception as exc:
                _complete_shadow_worker(worker_id, status="failed", error_type=type(exc).__name__)
                with _locks_mutex:
                    _write_threads[name] = [item for item in _write_threads.get(name, []) if item is not thread]
                    if not _write_threads[name]:
                        del _write_threads[name]
                    _shadow_worker_ids.pop(thread, None)
                raise
            profile_timings["shadow_thread_started"] = True
            profile_timings["shadow_write_mode"] = "asynchronous"
            profile_timings["shadow_worker_id"] = worker_id
            profile_timings["shadow_run_id"] = run_id
        total_seconds = round(time.perf_counter() - profile_start, 3)
        profile_timings["total_save_raw_seconds"] = total_seconds
        if name in _PROFILED_ARTIFACTS or total_seconds >= _PROFILE_LOG_THRESHOLD_SECONDS:
            _artifact_profile_log(profile_timings)
        return profile_timings

    def _save_payload_to_state_table(
        self,
        name: str,
        payload: Any,
        source_mtime: float | None = None,
    ) -> dict[str, float | int | str]:
        serialize_start = time.perf_counter()
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        inline_limit = max(1, int(self.state_payload_inline_limit_bytes))
        part_size = max(1, int(self.state_payload_part_size_bytes))
        text_slice_size = max(1, part_size // 4)
        spool = tempfile.SpooledTemporaryFile(max_size=inline_limit, mode="w+b")
        payload_digest = hashlib.sha256()
        serialized_chars = 0
        serialized_bytes_count = 0
        encode_seconds = 0.0
        for text_piece in encoder.iterencode(payload):
            serialized_chars += len(text_piece)
            for offset in range(0, len(text_piece), text_slice_size):
                encode_start = time.perf_counter()
                encoded_piece = text_piece[offset:offset + text_slice_size].encode("utf-8", errors="replace")
                encode_seconds += time.perf_counter() - encode_start
                spool.write(encoded_piece)
                payload_digest.update(encoded_piece)
                serialized_bytes_count += len(encoded_piece)
        serialize_seconds = max(0.0, time.perf_counter() - serialize_start - encode_seconds)
        source_mtime = float(source_mtime if source_mtime is not None else time.time())
        hash_start = time.perf_counter()
        payload_sha = payload_digest.hexdigest()
        hash_seconds = time.perf_counter() - hash_start
        sqlite_start = time.perf_counter()
        storage_mode = "inline_json" if serialized_bytes_count <= inline_limit else "partitioned_json_v1"
        generation_id = "" if storage_mode == "inline_json" else f"payload-{uuid.uuid4().hex}"
        part_count = 0
        try:
            spool.seek(0)
            with self.db_manager.get_connection() as conn:
                if storage_mode == "inline_json":
                    stored_payload = spool.read().decode("utf-8")
                else:
                    part_count = (serialized_bytes_count + part_size - 1) // part_size
                    stored_payload = _compact_json({
                        "__sage_partitioned_payload__": {
                            "format": storage_mode,
                            "generation_id": generation_id,
                            "part_count": part_count,
                            "part_size_bytes": part_size,
                            "payload_bytes": serialized_bytes_count,
                            "payload_sha256": payload_sha,
                        }
                    })
                conn.execute(
                    """
                    INSERT INTO state_payloads (
                        name, payload, payload_sha, payload_bytes, storage_mode,
                        generation_id, part_count, source_mtime, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'))
                    ON CONFLICT(name) DO UPDATE SET
                        payload = excluded.payload,
                        payload_sha = excluded.payload_sha,
                        payload_bytes = excluded.payload_bytes,
                        storage_mode = excluded.storage_mode,
                        generation_id = excluded.generation_id,
                        part_count = excluded.part_count,
                        source_mtime = CASE
                            WHEN state_payloads.payload_sha = excluded.payload_sha
                            THEN state_payloads.source_mtime
                            ELSE excluded.source_mtime
                        END,
                        updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now');
                    """,
                    (
                        name,
                        stored_payload,
                        payload_sha,
                        serialized_bytes_count,
                        storage_mode,
                        generation_id or None,
                        part_count,
                        source_mtime,
                    ),
                )
                if storage_mode == "inline_json":
                    conn.execute("DELETE FROM state_payload_parts WHERE name = ?;", (name,))
                else:
                    spool.seek(0)
                    for part_index in range(part_count):
                        part_bytes = spool.read(part_size)
                        if not part_bytes:
                            raise ArtifactPrimaryWriteError("Partitioned payload ended before the declared part count.")
                        conn.execute(
                            """
                            INSERT INTO state_payload_parts (
                                name, generation_id, part_index, payload, payload_bytes, payload_sha
                            ) VALUES (?, ?, ?, ?, ?, ?);
                            """,
                            (
                                name,
                                generation_id,
                                part_index,
                                part_bytes,
                                len(part_bytes),
                                _payload_sha(part_bytes),
                            ),
                        )
                    if spool.read(1):
                        raise ArtifactPrimaryWriteError("Partitioned payload exceeded the declared part count.")
                    conn.execute(
                        "DELETE FROM state_payload_parts WHERE name = ? AND generation_id <> ?;",
                        (name, generation_id),
                    )
        finally:
            spool.close()
        sqlite_seconds = time.perf_counter() - sqlite_start
        return {
            "state_payload_chars": serialized_chars,
            "state_payload_bytes": serialized_bytes_count,
            "state_payload_serialize_seconds": round(serialize_seconds, 3),
            "state_payload_encode_seconds": round(encode_seconds, 3),
            "state_payload_hash_seconds": round(hash_seconds, 3),
            "state_payload_sqlite_seconds": round(sqlite_seconds, 3),
            "state_payload_sha256": payload_sha,
            "state_payload_storage_mode": storage_mode,
            "state_payload_part_count": part_count,
            "state_payload_part_size_bytes": part_size if part_count else 0,
        }

    def begin_atlas_staging_run(self, producer_contract: str) -> str:
        """Open a resumable, non-canonical Atlas generation receipt."""
        if not self.use_sqlite:
            return ""
        self._ensure_schema()
        run_id = f"atlas-stage-{uuid.uuid4().hex}"
        normalized_contract = str(producer_contract)
        with self.db_manager.transaction() as conn:
            # One SQLite authority cannot safely host concurrent canonical Atlas
            # writers. Opening a new writer therefore supersedes every receipt
            # that never reached a terminal state, including older producer
            # contracts left behind by a hard process stop.
            abandoned_runs = conn.execute(
                """
                UPDATE atlas_staging_runs
                SET status = 'FAILED',
                    error_type = 'superseded_interrupted_run',
                    updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE status = 'IN_PROGRESS';
                """
            ).rowcount
            conn.execute(
                """
                INSERT INTO atlas_staging_runs (run_id, status, producer_contract)
                VALUES (?, 'IN_PROGRESS', ?);
                """,
                (run_id, normalized_contract),
            )
        if abandoned_runs:
            logger.warning(
                "[ATLAS_STAGING] Recovered %s interrupted run receipt(s); reusable checkpoints were preserved.",
                abandoned_runs,
            )
        return run_id

    def load_atlas_staging_files(
        self,
        producer_contract: str,
        *,
        stage_kind: str = "atlas_file",
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Load only checksum-valid provisional files for the current producer contract."""
        if not self.use_sqlite:
            return {}
        self._ensure_schema()
        staged: dict[tuple[str, str], dict[str, Any]] = {}
        with self.db_manager.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT project_key, rel_path, payload, payload_sha
                FROM atlas_staging_files
                WHERE producer_contract = ? AND stage_kind = ?
                ORDER BY project_key, rel_path;
                """,
                (str(producer_contract), str(stage_kind)),
            ).fetchall()
        for row in rows:
            serialized = str(row["payload"])
            if _payload_sha(serialized) != str(row["payload_sha"] or ""):
                logger.warning(
                    "[ATLAS_STAGING] Ignoring corrupt provisional file %s::%s.",
                    row["project_key"],
                    row["rel_path"],
                )
                continue
            try:
                payload = json.loads(serialized)
            except json.JSONDecodeError:
                logger.warning(
                    "[ATLAS_STAGING] Ignoring invalid provisional JSON %s::%s.",
                    row["project_key"],
                    row["rel_path"],
                )
                continue
            if isinstance(payload, dict):
                staged[(str(row["project_key"]), str(row["rel_path"]))] = payload
        return staged

    def save_atlas_staging_batch(
        self,
        run_id: str,
        producer_contract: str,
        project_key: str,
        entries: list[tuple[str, dict[str, Any]]],
        *,
        stage_kind: str = "atlas_file",
    ) -> dict[str, int]:
        """Checkpoint completed Atlas files in a bounded SQLite transaction."""
        if not self.use_sqlite or not run_id or not entries:
            return {"persisted": 0, "skipped_oversize": 0}
        self._ensure_schema()
        max_payload_bytes = max(1, int(self.atlas_staging_file_payload_limit_bytes))
        max_batch_size = max(1, int(self.atlas_staging_batch_size))
        if len(entries) > max_batch_size:
            raise ValueError(
                f"Atlas staging batch exceeds configured bound: {len(entries)} > {max_batch_size}"
            )
        rows = []
        skipped_oversize = 0
        for rel_path, payload in entries:
            serialized = _compact_json(payload)
            payload_bytes = len(serialized.encode("utf-8", errors="replace"))
            if payload_bytes > max_payload_bytes:
                skipped_oversize += 1
                continue
            rows.append(
                (
                    str(project_key),
                    str(rel_path),
                    str(stage_kind),
                    str(run_id),
                    str(producer_contract),
                    serialized,
                    _payload_sha(serialized),
                    float(payload.get("mtime") or 0.0),
                    int(payload.get("size") or 0),
                )
            )

        with _locks_mutex:
            staging_lock = _write_locks.setdefault("atlas_staging", threading.Lock())
        with staging_lock, self.db_manager.transaction() as conn:
            if rows:
                conn.executemany(
                    """
                    INSERT INTO atlas_staging_files (
                        project_key, rel_path, stage_kind, run_id, producer_contract, payload,
                        payload_sha, source_mtime, size_bytes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'))
                    ON CONFLICT(project_key, rel_path, stage_kind) DO UPDATE SET
                        run_id = excluded.run_id,
                        producer_contract = excluded.producer_contract,
                        payload = excluded.payload,
                        payload_sha = excluded.payload_sha,
                        source_mtime = excluded.source_mtime,
                        size_bytes = excluded.size_bytes,
                        updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now');
                    """,
                    rows,
                )
            conn.execute(
                """
                UPDATE atlas_staging_runs
                SET processed_files = processed_files + ?,
                    skipped_oversize_files = skipped_oversize_files + ?,
                    updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE run_id = ?;
                """,
                (len(rows), skipped_oversize, str(run_id)),
            )
        return {"persisted": len(rows), "skipped_oversize": skipped_oversize}

    def record_atlas_staging_reuse(self, run_id: str, reused_files: int) -> None:
        if not self.use_sqlite or not run_id or reused_files <= 0:
            return
        with self.db_manager.get_connection() as conn:
            conn.execute(
                """
                UPDATE atlas_staging_runs
                SET reused_files = reused_files + ?,
                    updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE run_id = ?;
                """,
                (int(reused_files), str(run_id)),
            )

    def finish_atlas_staging_run(self, run_id: str, producer_contract: str, *, status: str, error_type: str = "none") -> None:
        """Finalize a staging receipt; only a completed canonical commit clears provisional files."""
        if not self.use_sqlite or not run_id:
            return
        normalized_status = str(status or "").upper()
        if normalized_status not in {"COMPLETED", "FAILED"}:
            raise ValueError(f"Unsupported Atlas staging terminal status: {status}")
        with self.db_manager.transaction() as conn:
            conn.execute(
                """
                UPDATE atlas_staging_runs
                SET status = ?, error_type = ?, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE run_id = ?;
                """,
                (normalized_status, str(error_type or "none"), str(run_id)),
            )
            if normalized_status == "COMPLETED":
                conn.execute(
                    "DELETE FROM atlas_staging_files WHERE producer_contract = ?;",
                    (str(producer_contract),),
                )

    def _save_artifact_facts_to_sqlite(self, name: str, payload: dict[str, Any]) -> None:
        facts: dict[str, Any] = {}
        if name == "audit_report":
            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            audit_scope = summary.get("audit_scope") if isinstance(summary.get("audit_scope"), dict) else {}
            by_project = summary.get("by_project") if isinstance(summary.get("by_project"), dict) else {}
            facts["summary.audit_scope"] = audit_scope
            facts["summary.by_project.keys"] = sorted(str(key) for key in by_project.keys())
        elif name == "quality_gate":
            facts["release_gate_status"] = payload.get("release_gate_status")
        else:
            return
        with self.db_manager.get_connection() as conn:
            for key, value in facts.items():
                conn.execute(
                    """
                    INSERT INTO artifact_facts (artifact_name, fact_key, fact_value, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(artifact_name, fact_key) DO UPDATE SET
                        fact_value = excluded.fact_value,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (name, key, _compact_json(value)),
                )

    def refresh_atlas_projection(self, atlas: dict[str, Any] | None = None) -> bool:
        """Refresh relational Atlas projections, including source snapshots.

        This is intentionally public for validators and repair commands that
        need to upgrade an existing SQLite database after schema evolution
        without waiting for a full Atlas regeneration.
        """

        if not self.use_sqlite:
            return False
        payload = atlas if isinstance(atlas, dict) else self.load_raw("atlas", {})
        if not isinstance(payload, dict) or not payload:
            return False
        self._ensure_schema()
        self._save_atlas_to_sqlite(payload)
        return True

    def load_config(self, name: str, default: Any = None) -> Any:
        return load_json_file(self.config_path(name), default)

    def save_config(self, name: str, payload: Any, indent: int = 2) -> None:
        save_json_atomic(self.config_path(name), payload, indent=indent)

    def load_report(self, name: str, default: str = "") -> str:
        return load_text_file(self.report_path(name), default)

    def save_report(self, name: str, content: str) -> None:
        save_text_atomic(self.report_path(name), content)

    @staticmethod
    def _insert_atlas_symbol(conn, file_id: int, sym_name: str, sym_info: dict[str, Any]) -> None:
        sym_type = sym_info.get("type", "unknown")
        line = sym_info.get("line", 0)
        char = sym_info.get("char", 0)
        end_line = sym_info.get("end_line", line)
        source_lines = str(sym_info.get("source_lines") or "").strip()
        if not source_lines and line:
            source_lines = f"L{line}-L{end_line or line}"
        export_status = (
            "exported"
            if sym_info.get("export", False) or sym_info.get("exported", False) or sym_name != "default_export"
            else "local"
        )
        conn.execute(
            """
            INSERT INTO symbols (file_id, name, type, line, char, end_line, source_lines, export_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                file_id,
                str(sym_name),
                str(sym_type),
                int(line or 0),
                int(char or 0),
                int(end_line or line or 0),
                source_lines,
                export_status,
            ),
        )

    def _assert_scoped_atlas_payload_covers_relational_projects(
        self,
        payload: dict[str, Any],
        snapshot_scope: dict[str, set[str]],
    ) -> None:
        payload_projects = {
            str(project_key)
            for project_key, project_data in payload.items()
            if project_key != "symbols" and isinstance(project_data, dict)
        }
        missing_scope_projects = sorted(set(snapshot_scope) - payload_projects)
        if missing_scope_projects:
            raise UnsafeScopedAtlasProjectionError(
                "Scoped Atlas payload omits requested projects: "
                + ", ".join(missing_scope_projects)
            )
        with self.db_manager.get_connection() as conn:
            db_projects = {
                str(row["project_key"])
                for row in conn.execute("SELECT project_key FROM projects;").fetchall()
            }
        omitted_existing_projects = sorted(db_projects - payload_projects)
        if omitted_existing_projects:
            raise UnsafeScopedAtlasProjectionError(
                "Scoped Atlas payload would erase unselected relational projects: "
                + ", ".join(omitted_existing_projects)
            )

    def _save_scoped_atlas_to_sqlite(
        self,
        payload: dict[str, Any],
        snapshot_scope: dict[str, set[str]],
        project_path_hints: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update only current-pulse Atlas rows while preserving unrelated proof state."""
        project_payloads = {
            str(project_key): project_data
            for project_key, project_data in payload.items()
            if project_key != "symbols" and isinstance(project_data, dict)
        }
        if not project_payloads or not snapshot_scope:
            return None

        self._assert_scoped_atlas_payload_covers_relational_projects(
            payload,
            snapshot_scope,
        )
        with self.db_manager.get_connection() as conn:
            db_projects = {
                str(row["project_key"])
                for row in conn.execute("SELECT project_key FROM projects;").fetchall()
            }
            db_files: dict[str, set[str]] = {}
            for row in conn.execute("SELECT project_key, rel_path FROM files;").fetchall():
                db_files.setdefault(str(row["project_key"]), set()).add(str(row["rel_path"]).replace("\\", "/"))

            expected_projects = set(project_payloads)
            baseline_diagnostic: dict[str, Any] = {
                "status": "PASS",
                "reason": "relational_baseline_matches_canonical_payload",
                "db_project_count": len(db_projects),
                "payload_project_count": len(expected_projects),
                "missing_db_projects": sorted(expected_projects - db_projects),
                "unexpected_db_projects": sorted(db_projects - expected_projects),
                "file_set_drift": [],
            }
            baseline_valid = db_projects == expected_projects
            if not baseline_valid:
                baseline_diagnostic["status"] = "DRIFT"
                baseline_diagnostic["reason"] = "project_set_mismatch"
            if baseline_valid:
                for project_key, project_data in project_payloads.items():
                    payload_paths = {
                        str(path).replace("\\", "/")
                        for path, meta in (project_data.get("files") or {}).items()
                        if isinstance(meta, dict)
                    }
                    scoped_paths = snapshot_scope.get(project_key, set())
                    db_unscoped = db_files.get(project_key, set()) - scoped_paths
                    payload_unscoped = payload_paths - scoped_paths
                    if db_unscoped != payload_unscoped:
                        missing_in_db = sorted(payload_unscoped - db_unscoped)
                        unexpected_in_db = sorted(db_unscoped - payload_unscoped)
                        baseline_diagnostic["status"] = "DRIFT"
                        baseline_diagnostic["reason"] = "unscoped_file_set_mismatch"
                        baseline_diagnostic["file_set_drift"].append(
                            {
                                "project": project_key,
                                "missing_in_db_count": len(missing_in_db),
                                "unexpected_in_db_count": len(unexpected_in_db),
                                "missing_in_db_samples": missing_in_db[:10],
                                "unexpected_in_db_samples": unexpected_in_db[:10],
                            }
                        )
                        baseline_valid = False
                        break
            self._last_scoped_atlas_baseline_diagnostic = baseline_diagnostic
            if not baseline_valid:
                logger.warning(
                    "[SQLITE] Scoped Atlas projection baseline is incomplete or drifted; "
                    "using full relational rebuild. diagnostic=%s",
                    json.dumps(baseline_diagnostic, ensure_ascii=False, sort_keys=True),
                )
                return None

            for project_key, scoped_paths in snapshot_scope.items():
                project_data = project_payloads.get(project_key)
                if not isinstance(project_data, dict):
                    logger.warning(
                        "[SQLITE] Scoped Atlas project %s is absent from the payload; using full relational rebuild.",
                        project_key,
                    )
                    return None
                project_path = str(project_data.get("root_path") or project_path_hints.get(project_key) or "")
                conn.execute(
                    """
                    INSERT INTO projects (project_key, path, type) VALUES (?, ?, ?)
                    ON CONFLICT(project_key) DO UPDATE SET path = excluded.path, type = excluded.type;
                    """,
                    (project_key, project_path, str(project_data.get("project_type", "typescript"))),
                )
                files_data = project_data.get("files") if isinstance(project_data.get("files"), dict) else {}
                for rel_path in sorted(scoped_paths):
                    normalized_rel = str(rel_path).replace("\\", "/")
                    file_meta = files_data.get(normalized_rel)
                    if not isinstance(file_meta, dict):
                        conn.execute(
                            "DELETE FROM files WHERE project_key = ? AND rel_path = ?;",
                            (project_key, normalized_rel),
                        )
                        continue
                    conn.execute(
                        """
                        INSERT INTO files (project_key, rel_path, language, size_bytes, hash)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(project_key, rel_path) DO UPDATE SET
                            language = excluded.language,
                            size_bytes = excluded.size_bytes,
                            hash = excluded.hash;
                        """,
                        (
                            project_key,
                            normalized_rel,
                            str(file_meta.get("language", "")),
                            int(file_meta.get("size", 0)),
                            str(file_meta.get("hash", "")),
                        ),
                    )

            file_rows = conn.execute("SELECT file_id, project_key, rel_path FROM files;").fetchall()
            file_id_map = {
                "{}::{}".format(row["project_key"], str(row["rel_path"]).replace("\\", "/")): int(row["file_id"])
                for row in file_rows
            }
            existing_dependencies: dict[str, set[str]] = {}
            for row in conn.execute(
                """
                SELECT sf.project_key AS project_key, sf.rel_path AS rel_path, d.import_specifier AS import_specifier
                FROM dependencies d
                JOIN files sf ON sf.file_id = d.source_file_id;
                """
            ).fetchall():
                source_ref = "{}::{}".format(row["project_key"], str(row["rel_path"]).replace("\\", "/"))
                existing_dependencies.setdefault(source_ref, set()).add(str(row["import_specifier"]))

            def resolved_dependency_specifiers(project_key: str, target_list: Any) -> set[str]:
                resolved: set[str] = set()
                for target_rel in target_list or []:
                    if not target_rel:
                        continue
                    target_text = str(target_rel)
                    target_key = target_text if "::" in target_text else f"{project_key}::{target_text}"
                    if target_key in file_id_map:
                        resolved.add(target_text)
                return resolved

            dependency_sources = {
                "{}::{}".format(project_key, str(rel_path).replace("\\", "/"))
                for project_key, paths in snapshot_scope.items()
                for rel_path in paths
                if "{}::{}".format(project_key, str(rel_path).replace("\\", "/")) in file_id_map
            }
            for project_key, project_data in project_payloads.items():
                deps_data = project_data.get("dependencies") if isinstance(project_data.get("dependencies"), dict) else {}
                for rel_path in (project_data.get("files") or {}):
                    source_ref = "{}::{}".format(project_key, str(rel_path).replace("\\", "/"))
                    current = resolved_dependency_specifiers(project_key, deps_data.get(rel_path))
                    if current != existing_dependencies.get(source_ref, set()):
                        dependency_sources.add(source_ref)

            for source_ref in sorted(dependency_sources):
                project_key, rel_path = source_ref.split("::", 1)
                source_id = file_id_map.get(source_ref)
                if not source_id:
                    continue
                conn.execute("DELETE FROM dependencies WHERE source_file_id = ?;", (source_id,))
                project_data = project_payloads[project_key]
                deps_data = project_data.get("dependencies") if isinstance(project_data.get("dependencies"), dict) else {}
                for target_rel in deps_data.get(rel_path, []) or []:
                    if not target_rel:
                        continue
                    target_key = str(target_rel) if "::" in str(target_rel) else f"{project_key}::{target_rel}"
                    target_id = file_id_map.get(target_key)
                    if target_id:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO dependencies (source_file_id, target_file_id, import_specifier)
                            VALUES (?, ?, ?);
                            """,
                            (source_id, target_id, str(target_rel)),
                        )

            for project_key, scoped_paths in snapshot_scope.items():
                project_data = project_payloads[project_key]
                files_data = project_data.get("files") if isinstance(project_data.get("files"), dict) else {}
                global_symbols = project_data.get("symbols") if isinstance(project_data.get("symbols"), list) else []
                for rel_path in sorted(scoped_paths):
                    normalized_rel = str(rel_path).replace("\\", "/")
                    file_id = file_id_map.get(f"{project_key}::{normalized_rel}")
                    file_info = files_data.get(normalized_rel)
                    if not file_id or not isinstance(file_info, dict):
                        continue
                    conn.execute("DELETE FROM symbols WHERE file_id = ?;", (file_id,))
                    projected_names: set[str] = set()
                    for sym_info in file_info.get("symbols", []) or []:
                        if not isinstance(sym_info, dict):
                            continue
                        sym_name = str(sym_info.get("name") or "").strip()
                        if not sym_name:
                            continue
                        self._insert_atlas_symbol(conn, file_id, sym_name, sym_info)
                        projected_names.add(sym_name)
                    for sym_info in global_symbols:
                        if (
                            isinstance(sym_info, dict)
                            and str(sym_info.get("file") or "").replace("\\", "/") == normalized_rel
                            and str(sym_info.get("name") or "") not in projected_names
                        ):
                            self._insert_atlas_symbol(conn, file_id, str(sym_info.get("name") or ""), sym_info)

            self._save_source_snapshots_to_sqlite(
                conn,
                payload,
                file_id_map,
                project_path_hints,
                snapshot_scope,
            )
        logger.info(
            "[SQLITE] Scoped Atlas projection updated projects=%s files=%s dependency_sources=%s without clearing unrelated findings.",
            len(snapshot_scope),
            sum(len(paths) for paths in snapshot_scope.values()),
            len(dependency_sources),
        )
        return {
            "atlas_relational_mode": "scoped",
            "atlas_scoped_projects": len(snapshot_scope),
            "atlas_scoped_files": sum(len(paths) for paths in snapshot_scope.values()),
            "atlas_dependency_sources_updated": len(dependency_sources),
        }

    def _restore_audit_findings_after_scoped_rebuild(self) -> bool:
        audit_payload = self.load_raw("audit_report", {})
        if not isinstance(audit_payload, dict) or not isinstance(audit_payload.get("violations"), list):
            logger.warning(
                "[SQLITE] Scoped Atlas full fallback could not restore canonical Audit findings; audit_report is unavailable."
            )
            return False
        self._save_audit_findings_to_sqlite(audit_payload)
        logger.warning(
            "[SQLITE] Scoped Atlas full fallback restored canonical Audit findings; freshness remains governed by the artifact chain."
        )
        return True

    def _snapshot_relational_findings(self) -> list[dict[str, Any]]:
        """Capture generic findings by stable file identity before a guarded rebuild."""
        with self.db_manager.get_connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT f.engine_name, p.project_key, p.rel_path, f.severity, f.code, f.message
                    FROM findings f
                    JOIN files p ON p.file_id = f.file_id;
                    """
                ).fetchall()
            ]

    @staticmethod
    def _restore_relational_findings(
        conn,
        file_id_map: dict[str, int],
        findings: list[dict[str, Any]],
    ) -> tuple[int, int]:
        restored = 0
        skipped = 0
        for finding in findings:
            project_key = str(finding.get("project_key") or "")
            rel_path = str(finding.get("rel_path") or "").replace("\\", "/")
            file_id = file_id_map.get(f"{project_key}::{rel_path}")
            if not file_id:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO findings (engine_name, file_id, severity, code, message)
                VALUES (?, ?, ?, ?, ?);
                """,
                (
                    str(finding.get("engine_name") or "unknown"),
                    file_id,
                    str(finding.get("severity") or "warning"),
                    str(finding.get("code") or "unknown"),
                    str(finding.get("message") or ""),
                ),
            )
            restored += 1
        return restored, skipped

    def _save_atlas_to_sqlite(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Project Atlas into SQLite, using a guarded scoped path for save-time updates."""
        project_path_hints = DYNAMIC_CONFIG.get("variations") or DYNAMIC_CONFIG.get("project_path_hints") or {}
        if not isinstance(project_path_hints, dict):
            project_path_hints = {}
        snapshot_scope = _source_snapshot_projection_scope()
        if snapshot_scope:
            scoped_profile = self._save_scoped_atlas_to_sqlite(payload, snapshot_scope, project_path_hints)
            if scoped_profile:
                return scoped_profile
            preserved_findings = self._snapshot_relational_findings()
            restored_findings, skipped_findings = self._save_full_atlas_to_sqlite(
                payload,
                project_path_hints,
                preserved_findings=preserved_findings,
            )
            restored = self._restore_audit_findings_after_scoped_rebuild()
            return {
                "atlas_relational_mode": "full_fallback",
                "atlas_scoped_fallback_diagnostic": getattr(
                    self,
                    "_last_scoped_atlas_baseline_diagnostic",
                    {
                        "status": "UNKNOWN",
                        "reason": "scoped_projection_returned_no_profile",
                    },
                ),
                "atlas_scoped_fallback_findings_preserved": restored_findings,
                "atlas_scoped_fallback_findings_skipped": skipped_findings,
                "atlas_scoped_fallback_audit_findings_restored": restored,
            }
        self._save_full_atlas_to_sqlite(payload, project_path_hints)
        return {"atlas_relational_mode": "full"}

    def _save_full_atlas_to_sqlite(
        self,
        payload: dict[str, Any],
        project_path_hints: dict[str, Any],
        preserved_findings: list[dict[str, Any]] | None = None,
    ) -> tuple[int, int]:
        """Rebuild the complete relational Atlas projection."""
        with self.db_manager.get_connection() as conn:
            # Clean start for atomic reliability
            conn.execute("DELETE FROM findings;")
            conn.execute("DELETE FROM dependencies;")
            conn.execute("DELETE FROM symbols;")
            conn.execute("DELETE FROM source_snapshots;")
            conn.execute("DELETE FROM files;")
            conn.execute("DELETE FROM projects;")

            # 1. Insert projects
            for proj_key, proj_data in payload.items():
                if proj_key == "symbols" or not isinstance(proj_data, dict):
                    continue
                project_path = str(proj_data.get("root_path") or project_path_hints.get(proj_key) or "")
                conn.execute(
                    "INSERT OR REPLACE INTO projects (project_key, path, type) VALUES (?, ?, ?);",
                    (proj_key, project_path, str(proj_data.get("project_type", "typescript")))
                )

                # 2. Insert files
                files_data = proj_data.get("files", {})
                if isinstance(files_data, dict):
                    for rel_path, file_meta in files_data.items():
                        if not isinstance(file_meta, dict):
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO files (project_key, rel_path, language, size_bytes, hash) VALUES (?, ?, ?, ?, ?);",
                            (
                                proj_key,
                                rel_path,
                                str(file_meta.get("language", "")),
                                int(file_meta.get("size", 0)),
                                str(file_meta.get("hash", ""))
                            )
                        )

            # Establish a mapping from relative path keys to file IDs
            file_id_map: dict[str, int] = {}
            for row in conn.execute("SELECT file_id, project_key, rel_path FROM files;").fetchall():
                file_id_map[f"{row['project_key']}::{row['rel_path']}"] = row['file_id']
                file_id_map[f"{row['project_key']}/{row['rel_path']}"] = row['file_id']
                file_id_map[row['rel_path']] = row['file_id']

            self._save_source_snapshots_to_sqlite(
                conn,
                payload,
                file_id_map,
                project_path_hints,
                None,
            )

            # 3. Insert symbols and dependencies
            for proj_key, proj_data in payload.items():
                if proj_key == "symbols" or not isinstance(proj_data, dict):
                    continue

                # Dependencies mapping
                deps_data = proj_data.get("dependencies", {})
                if isinstance(deps_data, dict):
                    for rel_path, target_list in deps_data.items():
                        source_key = f"{proj_key}::{rel_path}"
                        source_id = file_id_map.get(source_key)
                        if not source_id or not isinstance(target_list, list):
                            continue
                        for target_rel in target_list:
                            if not target_rel:
                                continue
                            if "::" in target_rel:
                                target_key = target_rel
                            else:
                                target_key = f"{proj_key}::{target_rel}"

                            target_id = file_id_map.get(target_key)
                            if target_id:
                                conn.execute(
                                    "INSERT OR IGNORE INTO dependencies (source_file_id, target_file_id, import_specifier) VALUES (?, ?, ?);",
                                    (source_id, target_id, str(target_rel))
                                )

                # Symbols mapping
                #
                # The per-file Atlas symbol list is the closest source of truth for
                # editor spans. The global occurrence registry is collision-preserving
                # but may omit line/end_line/source_lines, so it is only a fallback.
                projected_symbols: set[tuple[int, str]] = set()

                files_data = proj_data.get("files", {})
                if isinstance(files_data, dict):
                    for rel_path, file_info in files_data.items():
                        if not isinstance(file_info, dict):
                            continue
                        file_id = file_id_map.get(f"{proj_key}::{rel_path}")
                        if not file_id:
                            continue
                        file_symbols = file_info.get("symbols", [])
                        if not isinstance(file_symbols, list):
                            continue
                        for sym_info in file_symbols:
                            if not isinstance(sym_info, dict):
                                continue
                            sym_name = str(sym_info.get("name") or "").strip()
                            if not sym_name:
                                continue
                            self._insert_atlas_symbol(conn, file_id, sym_name, sym_info)
                            projected_symbols.add((file_id, sym_name))

                symbols_data = proj_data.get("symbols", [])
                if isinstance(symbols_data, list):
                    for sym_info in symbols_data:
                        if not isinstance(sym_info, dict):
                            continue
                        sym_name = str(sym_info.get("name") or "").strip()
                        if not sym_name:
                            continue
                        rel_path = sym_info.get("file")
                        if not rel_path:
                            continue

                        file_key = f"{proj_key}::{rel_path}"
                        file_id = file_id_map.get(file_key)
                        if not file_id or (file_id, str(sym_name)) in projected_symbols:
                            continue

                        self._insert_atlas_symbol(conn, file_id, str(sym_name), sym_info)

            restored_findings, skipped_findings = self._restore_relational_findings(
                conn,
                file_id_map,
                preserved_findings or [],
            )
        return restored_findings, skipped_findings

    def _save_source_snapshots_to_sqlite(
        self,
        conn,
        payload: dict[str, Any],
        file_id_map: dict[str, int],
        project_path_hints: dict[str, Any],
        snapshot_scope: dict[str, set[str]] | None = None,
    ) -> None:
        """Persist live source text snapshots tied to the current Atlas file IDs.

        Source snapshots are not a replacement for Atlas generation. They give
        downstream source-text analyzers a future SQLite-first read path with
        explicit freshness metadata and honest missing/error statuses.
        """

        max_bytes = int(DYNAMIC_CONFIG.get("source_snapshot_max_bytes", 2_000_000) or 2_000_000)
        scope_label = "surgical" if snapshot_scope is not None else "full"
        _source_snapshot_log(f"START projection mode={scope_label}")
        total_snapshots = 0
        status_counts: dict[str, int] = {}
        preserved_unscoped = (
            max(0, len(file_id_map) - sum(len(paths) for paths in snapshot_scope.values()))
            if snapshot_scope is not None
            else 0
        )
        for proj_key, proj_data in payload.items():
            if proj_key == "symbols" or not isinstance(proj_data, dict):
                continue
            raw_project_path = str(proj_data.get("root_path") or project_path_hints.get(proj_key) or "")
            if not raw_project_path:
                continue
            project_root = Path(raw_project_path)
            if not project_root.is_absolute():
                project_root = (ROOT / project_root).resolve()
            else:
                project_root = project_root.resolve()

            files_data = proj_data.get("files", {})
            if not isinstance(files_data, dict):
                continue
            scoped_paths = snapshot_scope.get(str(proj_key), set()) if snapshot_scope is not None else None
            if scoped_paths is not None and not scoped_paths:
                continue
            file_items = (
                [(rel_path, files_data.get(rel_path)) for rel_path in sorted(scoped_paths)]
                if scoped_paths is not None
                else files_data.items()
            )
            _source_snapshot_log(
                f"START project {proj_key} files={len(file_items)}"
                + (f" scoped={len(scoped_paths or set())}" if snapshot_scope is not None else "")
            )
            project_snapshots = 0
            project_status_counts: dict[str, int] = {}

            for rel_path, file_meta in file_items:
                if not isinstance(file_meta, dict):
                    continue
                normalized_rel = str(rel_path).replace("\\", "/")
                file_id = file_id_map.get(f"{proj_key}::{normalized_rel}")
                if not file_id:
                    continue

                status = "ok"
                error = ""
                content = ""
                content_hash = ""
                source_mtime = file_meta.get("mtime")
                size_bytes = int(file_meta.get("size", 0) or 0)
                atlas_hash = str(file_meta.get("hash") or "")

                try:
                    source_path = (project_root / normalized_rel).resolve()
                    source_path.relative_to(project_root)
                    if not source_path.exists() or not source_path.is_file():
                        status = "missing"
                    elif source_path.stat().st_size > max_bytes:
                        status = "too_large"
                        size_bytes = int(source_path.stat().st_size)
                        source_mtime = float(source_path.stat().st_mtime)
                    else:
                        content = source_path.read_text(encoding="utf-8", errors="replace")
                        content_hash = atlas_hash or _payload_sha(content)
                        size_bytes = len(content.encode("utf-8", errors="replace"))
                        source_mtime = float(source_path.stat().st_mtime)
                except Exception as exc:
                    status = "error"
                    error = str(exc)
                    try:
                        from tools.core.honesty_telemetry import record_honesty_event

                        record_honesty_event(
                            component="artifact_store",
                            category="source_snapshot",
                            operation="save_source_snapshot",
                            subject=f"{proj_key}::{normalized_rel}",
                            severity="warning",
                            reason="source snapshot could not be materialized",
                            fallback="metadata_only_snapshot",
                            claim_impact="sqlite_source_snapshot_incomplete",
                            evidence_source="source_snapshot_exception",
                            exception=exc,
                        )
                    except Exception as telemetry_exc:
                        logger.error("[HONESTY] Source snapshot telemetry failed: %s", telemetry_exc)

                if not content_hash:
                    content_hash = atlas_hash

                project_snapshots += 1
                total_snapshots += 1
                project_status_counts[status] = project_status_counts.get(status, 0) + 1
                status_counts[status] = status_counts.get(status, 0) + 1

                conn.execute(
                    """
                    INSERT INTO source_snapshots (
                        file_id, project_key, rel_path, content, content_hash, source_mtime,
                        size_bytes, encoding, status, error, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(project_key, rel_path) DO UPDATE SET
                        file_id = excluded.file_id,
                        content = excluded.content,
                        content_hash = excluded.content_hash,
                        source_mtime = excluded.source_mtime,
                        size_bytes = excluded.size_bytes,
                        encoding = excluded.encoding,
                        status = excluded.status,
                        error = excluded.error,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (
                        file_id,
                        str(proj_key),
                        normalized_rel,
                        content,
                        content_hash,
                        float(source_mtime or 0.0),
                        size_bytes,
                        "utf-8",
                        status,
                        error,
                    ),
                )
            _source_snapshot_log(
                f"PASS project {proj_key} snapshots={project_snapshots} statuses={project_status_counts}"
            )
        _source_snapshot_log(
            f"PASS projection snapshots={total_snapshots} preserved_unscoped={preserved_unscoped} statuses={status_counts}"
        )

    def _save_audit_findings_to_sqlite(self, payload: dict[str, Any]) -> None:
        """Project audit violations into the relational findings table for SQLite-first queues."""
        violations = payload.get("violations") if isinstance(payload.get("violations"), list) else []
        with self.db_manager.get_connection() as conn:
            conn.execute("DELETE FROM findings WHERE engine_name = ?;", ("audit_report",))
            project_paths = {
                str(row["project_key"]): str(row["path"] or "")
                for row in conn.execute("SELECT project_key, path FROM projects;").fetchall()
            }
            file_id_map = {
                f"{row['project_key']}::{row['rel_path']}": int(row["file_id"])
                for row in conn.execute("SELECT file_id, project_key, rel_path FROM files;").fetchall()
            }
            inserted = 0
            skipped = 0
            for violation in violations:
                if not isinstance(violation, dict):
                    continue
                project_key = str(violation.get("project_key") or violation.get("project") or "").strip()
                raw_file = str(violation.get("file") or violation.get("path") or "").strip()
                if "::" in raw_file and not project_key:
                    project_key, raw_file = raw_file.split("::", 1)
                if not project_key:
                    skipped += 1
                    continue
                file_id = None
                for candidate in _candidate_violation_paths(violation, project_paths.get(project_key, "")):
                    file_id = file_id_map.get(f"{project_key}::{candidate}")
                    if file_id:
                        break
                if not file_id:
                    skipped += 1
                    continue
                severity = str(violation.get("mode") or violation.get("severity") or violation.get("level") or "unknown")
                code = str(violation.get("rule") or "architecture_violation")
                message = json.dumps(violation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                conn.execute(
                    """
                    INSERT INTO findings (engine_name, file_id, severity, code, message)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    ("audit_report", file_id, severity, code, message),
                )
                inserted += 1
            if skipped:
                logger.info("[SQLITE] Audit findings projection skipped %s unmapped rows.", skipped)
            logger.info("[SQLITE] Audit findings projection inserted %s rows.", inserted)

    def backup(self, dest_zip_path: Path | None = None) -> Path:
        """Dump SQLite state payloads to a zip of standard raw JSON files."""
        import zipfile
        import shutil
        import tempfile
        from datetime import datetime

        backup_dir = self._raw_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if dest_zip_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest_zip_path = backup_dir / f"codemaps_backup_{timestamp}.zip"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            try:
                self._ensure_schema()
                with self.db_manager.get_connection() as conn:
                    rows = conn.execute("SELECT * FROM state_payloads;").fetchall()
                    for row in rows:
                        payload = self._load_state_payload_row(conn, row)
                        with (temp_path / f"{row['name']}.json").open("w", encoding="utf-8") as stream:
                            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except Exception as exc:
                logger.error("[BACKUP] SQLite read failed, falling back to output/.raw JSON files: %s", exc)
                for src_file in self._raw_dir.glob("*.json"):
                    shutil.copy2(src_file, temp_path / src_file.name)

            with zipfile.ZipFile(dest_zip_path, "w", zipfile.ZIP_DEFLATED) as zip_f:
                for file_path in temp_path.glob("*.json"):
                    zip_f.write(file_path, arcname=file_path.name)

        logger.info("[BACKUP] Raw artifact payloads backed up to %s", dest_zip_path)
        return dest_zip_path

    def restore(self, backup_zip_path: Path | None = None) -> bool:
        """Restore SQLite state payloads from a backup zip or active output/.raw JSON files."""
        import zipfile
        import tempfile

        if not self.use_sqlite:
            logger.error("[RESTORE] SQLite is not enabled.")
            return False

        try:
            self._ensure_schema()
        except Exception as exc:
            logger.error("[RESTORE] Schema initialization failed: %s", exc)
            return False

        if backup_zip_path and backup_zip_path.exists():
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                with zipfile.ZipFile(backup_zip_path, "r") as zip_f:
                    zip_f.extractall(temp_path)
                return self._restore_from_folder(temp_path)

        backup_dir = self._raw_dir / "backups"
        zip_files = sorted(backup_dir.glob("codemaps_backup_*.zip"), key=lambda p: p.stat().st_mtime)
        if zip_files:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                with zipfile.ZipFile(zip_files[-1], "r") as zip_f:
                    zip_f.extractall(temp_path)
                return self._restore_from_folder(temp_path)

        return self._restore_from_folder(self._raw_dir)

    def _restore_from_folder(self, folder: Path) -> bool:
        import json

        success = True
        for json_file in folder.glob("*.json"):
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
                if json_file.stem == "atlas" and isinstance(payload, dict):
                    with self.db_manager.transaction():
                        self._save_payload_to_state_table(json_file.stem, payload)
                        self._save_atlas_to_sqlite(payload)
                else:
                    self._save_payload_to_state_table(json_file.stem, payload)
            except Exception as exc:
                logger.error("[RESTORE] Failed to restore payload %s: %s", json_file.name, exc)
                success = False
        return success


STORE = ArtifactStore()


def get_adaptive_timeout(default_val: int = 180) -> int:
    try:
        from tools.core.workload_profile import adaptive_timeout_seconds, build_workload_profile

        profile = STORE.load_raw("workload_profile", default={})
        if not isinstance(profile, dict) or not profile:
            atlas = STORE.load_raw("atlas", default={})
            profile = build_workload_profile(atlas if isinstance(atlas, dict) else {})
        return adaptive_timeout_seconds(profile, default_val)
    except Exception as exc:
        _record_store_degradation("adaptive_timeout", "workload_profile", exc, "default_timeout")
    return default_val

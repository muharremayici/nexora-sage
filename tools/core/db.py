from __future__ import annotations

import sqlite3
import contextvars
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from tools.core.operational_limits import (
    sqlite_busy_timeout_ms,
    sqlite_schema_initialize_retries,
    sqlite_schema_initialize_retry_delay_ms,
    sqlite_write_timeout_seconds,
)

tenant_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant_id", default=None)
_schema_locks: dict[str, threading.RLock] = {}
_schema_locks_guard = threading.Lock()
_ambient_connections: contextvars.ContextVar[dict[str, sqlite3.Connection] | None] = (
    contextvars.ContextVar("sqlite_ambient_connections", default=None)
)

def get_current_tenant() -> str | None:
    try:
        return tenant_var.get()
    except LookupError:
        return None


class SQLiteManager:
    def __init__(self, db_path: Path):
        self.base_db_path = db_path

    @property
    def db_path(self) -> Path:
        tenant_id = get_current_tenant()
        if tenant_id:
            # Route to tenant-specific database directory
            return self.base_db_path.parent.parent / "tenants" / tenant_id / ".raw" / self.base_db_path.name
        return self.base_db_path

    @contextmanager
    def get_connection(self) -> Iterator[sqlite3.Connection]:
        """Establish connection with performance optimizations enabled."""
        db_key = str(self.db_path.resolve())
        ambient = _ambient_connections.get() or {}
        if db_key in ambient:
            yield ambient[db_key]
            return
        conn = sqlite3.connect(self.db_path, timeout=float(sqlite_write_timeout_seconds()))
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(sqlite_busy_timeout_ms())};")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Own one transaction across nested repository calls for this database."""
        db_key = str(self.db_path.resolve())
        ambient = _ambient_connections.get() or {}
        if db_key in ambient:
            yield ambient[db_key]
            return
        conn = sqlite3.connect(self.db_path, timeout=float(sqlite_write_timeout_seconds()))
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(sqlite_busy_timeout_ms())};")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        token = _ambient_connections.set({**ambient, db_key: conn})
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _ambient_connections.reset(token)
            conn.close()

    def initialize_schema(self) -> None:
        """Create relational S.A.G.E. tables and indexes if they do not exist."""
        # Ensure raw output directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db_key = str(self.db_path.resolve())
        with _schema_locks_guard:
            schema_lock = _schema_locks.setdefault(db_key, threading.RLock())
        retries = max(0, int(sqlite_schema_initialize_retries()))
        delay_seconds = max(0.001, float(sqlite_schema_initialize_retry_delay_ms()) / 1000.0)
        with schema_lock:
            for attempt in range(retries + 1):
                try:
                    self._initialize_schema_once()
                    return
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt >= retries:
                        raise
                    time.sleep(delay_seconds * (attempt + 1))

    def _initialize_schema_once(self) -> None:
        """Apply idempotent schema setup while the caller holds the local schema lock."""
        with self.get_connection() as conn:
            # WAL is a database-level setting. Keep it out of the hot connection
            # path so parallel validators do not contend on journal-mode changes.
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS state_payloads (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    payload_sha TEXT,
                    payload_bytes INTEGER,
                    storage_mode TEXT NOT NULL DEFAULT 'inline_json',
                    generation_id TEXT,
                    part_count INTEGER NOT NULL DEFAULT 0,
                    source_mtime REAL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS state_payload_parts (
                    name TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    part_index INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    payload_bytes INTEGER NOT NULL,
                    payload_sha TEXT NOT NULL,
                    PRIMARY KEY (name, generation_id, part_index),
                    FOREIGN KEY(name) REFERENCES state_payloads(name) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS atlas_staging_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    producer_contract TEXT NOT NULL,
                    processed_files INTEGER NOT NULL DEFAULT 0,
                    reused_files INTEGER NOT NULL DEFAULT 0,
                    skipped_oversize_files INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT NOT NULL DEFAULT 'none',
                    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS atlas_staging_files (
                    project_key TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    stage_kind TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    producer_contract TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    payload_sha TEXT NOT NULL,
                    source_mtime REAL,
                    size_bytes INTEGER NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_key, rel_path, stage_kind),
                    FOREIGN KEY(run_id) REFERENCES atlas_staging_runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS projects (
                    project_key TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    type TEXT NOT NULL
                );
                
                CREATE TABLE IF NOT EXISTS files (
                    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_key TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE,
                    UNIQUE(project_key, rel_path)
                );

                CREATE TABLE IF NOT EXISTS source_snapshots (
                    file_id INTEGER NOT NULL,
                    project_key TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    source_mtime REAL,
                    size_bytes INTEGER NOT NULL,
                    encoding TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE,
                    PRIMARY KEY (project_key, rel_path)
                );
                
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    char INTEGER NOT NULL,
                    end_line INTEGER,
                    source_lines TEXT,
                    export_status TEXT NOT NULL,
                    FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
                );
                
                CREATE TABLE IF NOT EXISTS dependencies (
                    dep_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_file_id INTEGER NOT NULL,
                    target_file_id INTEGER NOT NULL,
                    import_specifier TEXT NOT NULL,
                    FOREIGN KEY(source_file_id) REFERENCES files(file_id) ON DELETE CASCADE,
                    FOREIGN KEY(target_file_id) REFERENCES files(file_id) ON DELETE CASCADE,
                    UNIQUE(source_file_id, target_file_id, import_specifier)
                );
                
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine_name TEXT NOT NULL,
                    file_id INTEGER NOT NULL,
                    severity TEXT NOT NULL,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artifact_facts (
                    artifact_name TEXT NOT NULL,
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (artifact_name, fact_key)
                );

                CREATE TABLE IF NOT EXISTS target_write_leases (
                    lease_key TEXT PRIMARY KEY,
                    analysis_root TEXT NOT NULL,
                    target_file TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    source_snapshot_hash TEXT NOT NULL,
                    acquired_at_epoch REAL NOT NULL,
                    expires_at_epoch REAL NOT NULL,
                    renewed_at_epoch REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS governance_trace_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    task_fingerprint TEXT NOT NULL,
                    context_fingerprint TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    state_change TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    outcome TEXT NOT NULL,
                    failure_layer TEXT NOT NULL,
                    claim_boundary TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                
                -- Optimization Indexes for semantic searching & dependency walks
                CREATE UNIQUE INDEX IF NOT EXISTS idx_files_project_rel ON files(project_key, rel_path);
                CREATE INDEX IF NOT EXISTS idx_files_hash ON files(hash);
                CREATE INDEX IF NOT EXISTS idx_source_snapshots_file ON source_snapshots(file_id);
                CREATE INDEX IF NOT EXISTS idx_source_snapshots_hash ON source_snapshots(content_hash);
                CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
                CREATE INDEX IF NOT EXISTS idx_dependencies_source ON dependencies(source_file_id);
                CREATE INDEX IF NOT EXISTS idx_dependencies_target ON dependencies(target_file_id);
                CREATE INDEX IF NOT EXISTS idx_findings_engine ON findings(engine_name);
                CREATE INDEX IF NOT EXISTS idx_artifact_facts_name ON artifact_facts(artifact_name);
                CREATE INDEX IF NOT EXISTS idx_target_write_leases_expiry ON target_write_leases(expires_at_epoch);
                CREATE INDEX IF NOT EXISTS idx_governance_trace_events_trace ON governance_trace_events(trace_id);
                CREATE INDEX IF NOT EXISTS idx_governance_trace_events_recorded ON governance_trace_events(recorded_at);
                CREATE INDEX IF NOT EXISTS idx_state_payload_parts_generation ON state_payload_parts(name, generation_id, part_index);
                CREATE INDEX IF NOT EXISTS idx_atlas_staging_files_contract ON atlas_staging_files(producer_contract, stage_kind, project_key);
                CREATE INDEX IF NOT EXISTS idx_atlas_staging_runs_status ON atlas_staging_runs(status, updated_at);
            """)
            for statement in (
                "ALTER TABLE state_payloads ADD COLUMN payload_sha TEXT;",
                "ALTER TABLE state_payloads ADD COLUMN payload_bytes INTEGER;",
                "ALTER TABLE state_payloads ADD COLUMN storage_mode TEXT NOT NULL DEFAULT 'inline_json';",
                "ALTER TABLE state_payloads ADD COLUMN generation_id TEXT;",
                "ALTER TABLE state_payloads ADD COLUMN part_count INTEGER NOT NULL DEFAULT 0;",
                "ALTER TABLE state_payloads ADD COLUMN source_mtime REAL;",
                "ALTER TABLE state_payloads ADD COLUMN updated_at TEXT;",
                "ALTER TABLE symbols ADD COLUMN end_line INTEGER;",
                "ALTER TABLE symbols ADD COLUMN source_lines TEXT;",
            ):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

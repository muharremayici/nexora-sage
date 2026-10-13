"""Native-path and authority controls for SQLite read-only consumers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tools.core import db, json_io, sqlite_storage_maintenance as maintenance
from tools.core.unmanaged_atomic_io import native_filesystem_path, sqlite_read_only_uri
from tools import validate_entrypoints_and_failures as entrypoints


def _long_raw_dir(tmp_path: Path) -> Path:
    raw_dir = tmp_path / ("long-" + "x" * 170) / ("nested-" + "y" * 60) / "space # percent%" / ".raw"
    Path(native_filesystem_path(raw_dir)).mkdir(parents=True)
    return raw_dir


def _database(raw_dir: Path) -> Path:
    database = raw_dir / "codemaps.db"
    payload = json.dumps({"source": "sqlite"}, separators=(",", ":"))
    with closing(sqlite3.connect(native_filesystem_path(database))) as conn:
        conn.execute(
            "CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT, payload_sha TEXT, "
            "source_mtime REAL, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO state_payloads (name, payload, payload_sha) VALUES (?, ?, ?)",
            ("atlas", payload, hashlib.sha256(payload.encode()).hexdigest()),
        )
        conn.commit()
    return database


def test_native_read_only_uri_preserves_escaped_long_path_and_refuses_writes(tmp_path: Path) -> None:
    raw_dir = _long_raw_dir(tmp_path)
    database = _database(raw_dir)
    uri = sqlite_read_only_uri(database)
    assert uri.endswith("?mode=ro")
    assert "%23" in uri and "%25" in uri and "%20" in uri
    assert "%2525" not in uri
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        assert conn.execute("SELECT name FROM state_payloads").fetchone() == ("atlas",)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(
                "INSERT INTO state_payloads (name, payload, payload_sha) "
                "VALUES ('other', '{}', '')"
            )


def test_raw_state_and_maintenance_consumers_keep_sqlite_identity_and_close(tmp_path: Path, monkeypatch) -> None:
    raw_dir = _long_raw_dir(tmp_path)
    database = _database(raw_dir)
    artifact = raw_dir / "atlas.json"
    # A shadow must never replace the authoritative SQLite payload.
    Path(native_filesystem_path(artifact)).write_text('{"source":"shadow"}', encoding="utf-8")
    before = Path(native_filesystem_path(database)).read_bytes()
    assert json_io.load_raw_artifact_path(artifact) == {"source": "sqlite"}
    assert json_io.load_raw_artifact_path_strict(artifact) == {"source": "sqlite"}
    assert json_io.raw_artifact_content_fingerprint(artifact).startswith("sqlite:")
    monkeypatch.setattr(entrypoints, "RAW_DIR", raw_dir)
    fingerprints = entrypoints._canonical_state_fingerprints()
    assert isinstance(fingerprints["atlas"], dict)
    assert fingerprints["atlas"]["payload_sha"] == hashlib.sha256(
        b'{"source":"sqlite"}'
    ).hexdigest()

    connections = []
    original_connect = maintenance._connect

    def track_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(maintenance, "_connect", track_connect)
    profile = maintenance.inspect_sqlite_storage(database)
    assert profile["status"] == "OBSERVED"
    assert connections
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[-1].execute("SELECT 1")
    assert Path(native_filesystem_path(database)).read_bytes() == before


def test_missing_database_remains_missing_without_creation(tmp_path: Path) -> None:
    raw_dir = _long_raw_dir(tmp_path)
    database = raw_dir / "codemaps.db"
    assert json_io._raw_state_connection(raw_dir / "atlas.json") is None
    assert maintenance.inspect_sqlite_storage(database)["status"] == "MISSING"
    artifact = raw_dir / "atlas.json"
    Path(native_filesystem_path(artifact)).write_text('{"source":"shadow"}', encoding="utf-8")
    assert json_io.load_raw_artifact_path(artifact) == {"source": "shadow"}
    assert json_io.load_raw_artifact_path_strict(artifact) == {"source": "shadow"}
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(sqlite_read_only_uri(database), uri=True)
    assert not Path(native_filesystem_path(database)).exists()


@pytest.mark.parametrize("raise_in_body", [False, True])
def test_db_owner_read_only_handle_is_native_closed_and_non_mutating(tmp_path, raise_in_body):
    database = _database(_long_raw_dir(tmp_path))
    before = Path(native_filesystem_path(database)).read_bytes()
    manager = db.SQLiteManager(database)
    connection = None

    def read():
        nonlocal connection
        with manager.get_read_only_connection() as connection:
            assert connection.execute("SELECT name FROM state_payloads").fetchone()["name"] == "atlas"
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("DELETE FROM state_payloads")
            if raise_in_body:
                raise ValueError("caller failure")

    if raise_in_body:
        with pytest.raises(ValueError, match="caller failure"):
            read()
    else:
        read()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert Path(native_filesystem_path(database)).read_bytes() == before


def test_db_owner_read_only_missing_database_cannot_initialize(tmp_path):
    database = _long_raw_dir(tmp_path) / "codemaps.db"
    with pytest.raises(sqlite3.OperationalError):
        with db.SQLiteManager(database).get_read_only_connection():
            pytest.fail("missing primary cannot yield a handle")
    assert not Path(native_filesystem_path(database)).exists()


def test_db_owner_read_only_timeout_is_governed_and_not_ambient(tmp_path, monkeypatch):
    database = _database(tmp_path)
    manager = db.SQLiteManager(database)
    original_connect = sqlite3.connect
    calls = []

    def observe(*args, **kwargs):
        calls.append((args, kwargs))
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(db.sqlite3, "connect", observe)
    monkeypatch.setattr(db, "sqlite_read_timeout_seconds", lambda: 0.125)
    with manager.transaction() as ambient:
        with manager.get_read_only_connection() as reader:
            assert reader is not ambient
            assert reader.execute("PRAGMA busy_timeout").fetchone()[0] == 125
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.execute("DELETE FROM state_payloads")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            reader.execute("SELECT 1")
        ambient.execute("INSERT INTO state_payloads (name, payload) VALUES ('write', '{}')")
    assert calls[-1] == ((sqlite_read_only_uri(database),), {"uri": True, "timeout": 0.125})
    with closing(original_connect(native_filesystem_path(database))) as conn:
        assert conn.execute("SELECT count(*) FROM state_payloads").fetchone()[0] == 2


def test_db_owner_read_only_connection_uses_existing_tenant_route(tmp_path):
    tenant_raw = tmp_path / "tenants" / "example" / ".raw"
    tenant_raw.mkdir(parents=True)
    _database(tenant_raw)
    manager = db.SQLiteManager(tmp_path / ".raw" / "codemaps.db")
    token = db.tenant_var.set("example")
    try:
        with manager.get_read_only_connection() as conn:
            assert conn.execute("SELECT name FROM state_payloads").fetchone()["name"] == "atlas"
    finally:
        db.tenant_var.reset(token)
    assert not manager.base_db_path.exists()


@pytest.mark.parametrize(
    ("consumer", "declared", "hot", "expected_pass"),
    [
        ("tools/tests/reviewed_fixture.py", True, False, True),
        ("tools/tests/unknown_fixture.py", False, False, False),
        ("tools/engines/runtime_consumer.py", True, True, False),
    ],
)
def test_fixture_admission_does_not_exempt_unknown_or_hot_reads(
    tmp_path: Path, monkeypatch, consumer, declared, hot, expected_pass,
) -> None:
    from tools import validate_atlas_sqlite_first_access as validator

    adapter = "tools/core/atlas_io.py"
    adapter_path = tmp_path / adapter
    adapter_path.parent.mkdir(parents=True)
    adapter_path.write_text('STORE.load_raw("atlas", {})\n', encoding="utf-8")
    source = tmp_path / consumer
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        'payload = load_json_file(Path("atlas.json"))\n'
        + ('central = load_atlas_data()\n' if hot else ''), encoding="utf-8",
    )
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(validator, "CENTRAL_ADAPTER", adapter)
    monkeypatch.setattr(validator, "HOT_ATLAS_CONSUMERS", {consumer} if hot else set())
    monkeypatch.setattr(validator, "ALLOWED_DIRECT_ATLAS_PATH_REFERENCES",
                        {consumer: "isolated fixture"} if declared else {})
    monkeypatch.setattr(validator, "pipeline_declared_artifact_modules", lambda _: {})
    monkeypatch.setattr(validator, "save_json_atomic", lambda *args: None)
    monkeypatch.setattr(validator, "save_text_atomic", lambda *args: None)
    result = validator.validate()
    assert (result["summary"]["status"] == "PASS") is expected_pass
    if not declared:
        assert result["undeclared_reference_candidates"][0]["file"] == consumer
        assert {row["name"] for row in result["checks"] if not row["passed"]} == {
            "atlas_path_references_are_policy_declared",
        }
    if hot:
        assert result["summary"]["hot_direct_atlas_load_calls"] == 1
        assert {row["name"] for row in result["checks"] if not row["passed"]} == {
            "hot_agent_context_consumers_do_not_direct_load_atlas_json",
        }


def test_reviewed_fixture_references_use_exact_existing_policy_admission() -> None:
    from tools.core.sqlite_first_access_policy import (
        sqlite_first_allowed_references, sqlite_first_hot_consumers,
    )

    admitted = {
        "tools/tests/test_sqlite_read_only_uri.py",
        "tools/tests/test_react_contracts.py",
        "tools/tests/test_source_snapshot_integrity.py",
        "tools/tests/test_target_runtime_shallow_mutation_advisory.py",
        "tools/tests/test_structured_export_advisory.py",
        "tools/tests/test_target_runtime_repository_direct_read_advisory.py",
        "tools/tests/test_target_runtime_repository_override_advisory.py",
        "tools/tests/test_native_run_log_ingestion.py",
        "tools/tests/test_state_flow_call_result_alias_invocation_contract.py",
        "tools/tests/test_state_flow_call_result_property_binding_contract.py",
        "tools/tests/test_state_flow_direct_optional_alias_contract.py",
        "tools/tests/test_state_flow_import_optional_rest_alias_contract.py",
        "tools/tests/test_state_flow_nested_callback_alias_contract.py",
        "tools/tests/test_state_flow_parameter_config_callback_contract.py",
        "tools/tests/test_state_flow_returned_callback_context_contract.py",
        "tools/tests/test_state_flow_returned_callback_parameter_optional_contract.py",
        "tools/tests/test_state_flow_returned_local_callback_contract.py",
    }
    reasons = sqlite_first_allowed_references("atlas")
    assert admitted <= reasons.keys()
    assert all("isolated" in reasons[name] for name in admitted)
    assert not admitted & sqlite_first_hot_consumers("atlas")
    assert "tools/tests/*" not in reasons


def test_declared_native_atlas_reader_has_exact_sqlite_first_policy_reason() -> None:
    from tools.core.sqlite_first_access_policy import (
        sqlite_first_allowed_references, sqlite_first_hot_consumers,
    )

    reader = "tools/ingest_native_run_logs.py"
    reasons = sqlite_first_allowed_references("atlas")
    assert reader in reasons
    assert "SQLite-first" in reasons[reader]
    assert "explicit" in reasons[reader]
    assert reader not in sqlite_first_hot_consumers("atlas")
    assert "tools/*" not in reasons

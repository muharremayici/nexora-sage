"""Native-path, backup and authority controls for SQLite consumers and fixtures."""

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
        "tools/tests/test_atlas_declaration_inspection.py",
        "tools/tests/test_atlas_declaration_json_primary.py",
        "tools/tests/test_atlas_manifest_declaration_links.py",
        "tools/tests/test_atlas_manifest_source_links.py",
        "tools/tests/test_declaration_symbol_search.py",
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


def test_acquisition_fixture_has_exact_large_artifact_write_role() -> None:
    from tools.core.sqlite_first_access_policy import sqlite_first_large_allowed_references

    reasons = sqlite_first_large_allowed_references()
    role = reasons["tools/tests/test_acquisition_differential_acceptance.py"]
    assert "isolated Audit fixture" in role and "tampered-lineage refusal" in role
    assert "tools/tests/*" not in reasons and "tools/*" not in reasons


@pytest.mark.parametrize("declared,direct_read,expected_gate", [
    (True, False, None),
    (False, False, "large_artifact_path_references_are_policy_declared"),
    (True, True, "large_artifact_no_direct_json_disk_reads"),
])
def test_large_fixture_admission_preserves_unknown_and_direct_read_refusal(
    tmp_path, monkeypatch, declared, direct_read, expected_gate,
):
    from tools import validate_large_artifact_sqlite_first_access as validator

    consumer = "tools/tests/reviewed_fixture.py"
    artifact_name = "fixture-payload.json"
    path = tmp_path / consumer
    path.parent.mkdir(parents=True)
    source = (f"Path({artifact_name!r}).read_text()\n" if direct_read
              else f"save_json_atomic(Path({artifact_name!r}), {{}})\n")
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(validator, "ARTIFACTS", {artifact_name: {"hot_consumers": [], "producer": ""}})
    monkeypatch.setattr(validator, "ALLOWED_REFERENCE_FILES", {consumer: "isolated fixture"} if declared else {})
    monkeypatch.setattr(validator, "save_json_atomic", lambda *args: None)
    monkeypatch.setattr(validator, "save_text_atomic", lambda *args: None)

    result = validator.validate()
    assert {row["name"] for row in result["checks"] if not row["passed"]} == (
        {expected_gate} if expected_gate else set()
    )
    assert (result["summary"]["status"] == "PASS") is (expected_gate is None)


class _FixtureCloneComplete(Exception):
    """Stop this I/O-only probe before any downstream producer runs."""


def _exercise_fixture_clone(tmp_path, monkeypatch, driver, shape, *, missing=False):
    import os
    import sys
    import shutil
    import sqlite3
    import tempfile
    from contextlib import closing
    from tools.core import config
    from tools.core.unmanaged_atomic_io import native_filesystem_path
    from tools.tests import acquisition_lease_fixture as leases, acquisition_watchdog_fixture as watchdog
    from tools.tests import test_acquisition_differential_acceptance as acceptance

    def work_path(name, deep):
        base = tmp_path / name
        return base / ("deep-" + "x" * max(1, 240 - len(str(base)) - 6)) if deep else base

    source_work = work_path("seed", shape in {"source", "both"})
    work = work_path("clone", shape in {"destination", "both"})
    source_raw, destination_raw = source_work / "output/.raw", work / "output/.raw"
    root = tmp_path / "subject"
    root.mkdir()
    for path in [source_work, work]:
        Path(native_filesystem_path(path)).mkdir(parents=True)
    for name, value in [("runtime.json", {"workspace_root": str(root)}), ("discovery.json", {}), ("overrides.json", {})]:
        (source_work / name).write_text(json.dumps(value), encoding="utf-8")

    class StopAfterClone(dict):
        def update(self, *args, **kwargs):
            raise _FixtureCloneComplete("Native copy and online backup completed; producers not invoked")

    def bind(runtime, subject, destination, mode):
        assert mode == "normal" and subject == root and destination == work
        for name, value in {"CONFIG_FILE": work / "runtime.json", "DISCOVERY_FILE": work / "discovery.json",
                            "OVERRIDES_FILE": work / "overrides.json", "RAW_DIR": destination_raw,
                            "DYNAMIC_CONFIG": StopAfterClone()}.items():
            monkeypatch.setattr(runtime, name, value)
        Path(native_filesystem_path(destination_raw)).mkdir(parents=True)

    # Watchdog redirects tempfile and environment in its child normally. Restore
    # those globals when this deliberately bounded in-process fixture ends.
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    for key in ["TMP", "TEMP", "TMPDIR"]:
        if key in os.environ:
            monkeypatch.setenv(key, os.environ[key])
        else:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(acceptance, "_bind_fixture_runtime", bind)
    seed = {"raw_dir": str(source_raw)}

    def run():
        if driver == "packet":
            return acceptance._run_native_packets(seed, work, "normal")
        function = leases.run_native_leases if driver == "lease" else watchdog.run_native_watchdog
        return function(seed, work, "normal", repository_root=acceptance.REPO, bind_runtime=bind)

    if missing:
        with pytest.raises((FileNotFoundError, AssertionError)):
            run()
        assert not Path(native_filesystem_path(destination_raw / "codemaps.db")).exists()
        return

    Path(native_filesystem_path(source_raw)).mkdir(parents=True)
    name = "analysis_scope_authority_analysis_snapshot_lineage.json"
    artifact = source_raw / name
    copied = destination_raw / name
    Path(native_filesystem_path(artifact)).write_bytes(b'{"fixture":"native-copy"}')
    timestamp = 1700000000000000000
    os.utime(native_filesystem_path(artifact), ns=(timestamp, timestamp))
    if shape in {"source", "both"}:
        assert len(str(artifact)) > 260
        assert len(str(source_raw / "codemaps.db")) > 260
    if shape in {"destination", "both"}:
        assert len(str(copied)) > 260
        assert len(str(destination_raw / "codemaps.db")) > 260

    copied_names = []
    original_copytree, original_copy2 = shutil.copytree, shutil.copy2

    def observe_tree(source, destination, **kwargs):
        copier = kwargs.get("copy_function", original_copy2)

        def observe_copy(source, destination):
            copied_names.append(Path(os.fspath(source)).name)
            return copier(source, destination)

        kwargs["copy_function"] = observe_copy
        return original_copytree(source, destination, **kwargs)

    monkeypatch.setattr(shutil, "copytree", observe_tree)
    with closing(sqlite3.connect(native_filesystem_path(source_raw / "codemaps.db"))) as primary:
        assert primary.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        primary.execute("CREATE TABLE fixture_state (value INTEGER)")
        primary.execute("INSERT INTO fixture_state VALUES (1)")
        primary.commit()
        primary.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        primary.execute("UPDATE fixture_state SET value=2")
        primary.commit()
        assert Path(native_filesystem_path(source_raw / "codemaps.db-wal")).stat().st_size > 0
        with pytest.raises(_FixtureCloneComplete):
            run()
        with closing(sqlite3.connect(native_filesystem_path(destination_raw / "codemaps.db"))) as copied_primary:
            assert copied_primary.execute("SELECT value FROM fixture_state").fetchone() == (2,)
    assert copied_names == [name]
    assert Path(native_filesystem_path(copied)).read_bytes() == Path(native_filesystem_path(artifact)).read_bytes()
    assert Path(native_filesystem_path(copied)).stat().st_mtime_ns == timestamp
    assert config.RAW_DIR == destination_raw and not str(config.RAW_DIR).startswith("\\\\?\\")


@pytest.mark.parametrize("driver", ["packet", "lease", "watchdog"])
@pytest.mark.parametrize("shape", ["short", "source", "destination", "both"])
def test_fixture_clone_preserves_native_storage(tmp_path, monkeypatch, driver, shape):
    _exercise_fixture_clone(tmp_path, monkeypatch, driver, shape)


@pytest.mark.parametrize("driver", ["packet", "lease", "watchdog"])
def test_fixture_clone_refuses_missing_source(tmp_path, monkeypatch, driver):
    _exercise_fixture_clone(tmp_path, monkeypatch, driver, "short", missing=True)

import hashlib
import json
import os
import sqlite3
import tempfile
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.core import artifact_store as artifact_store_module
from tools.core import config as config_module
from tools.core.artifact_store import (
    ArtifactPrimaryWriteError,
    ArtifactStore,
    UnsafeScopedAtlasProjectionError,
)
from tools.core.db import SQLiteManager
from tools.core.json_io import load_raw_artifact_path
from tools.core.persistence_limits import DEFAULT_PERSISTENCE_LIMITS, load_persistence_limits
from tools.engines import generate_atlas as generate_atlas_module
from tools.engines.generate_atlas import (
    apply_global_semantic_bridge,
    bounded_atlas_snapshot_scope,
    preserve_unselected_bounded_projects,
)


def test_persistence_limits_match_existing_accessors_and_read_one_snapshot(tmp_path):
    from tools.core import operational_limits

    policy_path = tmp_path / "pipeline_execution_policy.json"
    for value in (None, 7, "12", 0, -3, "invalid", [], True):
        payload = {"operational_limits": dict.fromkeys(DEFAULT_PERSISTENCE_LIMITS, value)}
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        with patch.object(operational_limits, "CONFIG_DIR", tmp_path):
            expected = {key: operational_limits.operational_limit_seconds(key) for key in DEFAULT_PERSISTENCE_LIMITS}
        with patch.object(Path, "read_text", return_value=json.dumps(payload)) as read:
            assert load_persistence_limits(policy_path) == expected
            read.assert_called_once_with(encoding="utf-8")
    policy_path.write_text("{}", encoding="utf-8")
    assert load_persistence_limits(policy_path) == DEFAULT_PERSISTENCE_LIMITS


@pytest.mark.parametrize("content", [
    "{", "[]", '{"operational_limits":{"atlas_staging_batch_size":1,"atlas_staging_batch_size":2}}',
])
def test_persistence_limits_reject_corrupt_or_ambiguous_policy(tmp_path, content):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_persistence_limits(policy_path)


def test_persistence_limits_missing_policy_and_legacy_decoder_identity(tmp_path):
    from tools.core import json_io, json_syntax
    with pytest.raises(FileNotFoundError):
        load_persistence_limits(tmp_path / "missing.json")
    assert json_io.loads_json_strict is json_syntax.loads_json_strict
    assert json_io.DuplicateJSONKeyError is json_syntax.DuplicateJSONKeyError


def test_persistence_policy_import_does_not_bootstrap_storage_or_config():
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; import tools.core.persistence_limits; "
         "assert not ({'tools.core.config', 'tools.core.json_io', "
         "'tools.core.artifact_store', 'tools.core.operational_limits', "
         "'tools.core.db', 'sqlite3'} & set(sys.modules))"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _project(root: Path, name: str, content_hash: str) -> dict:
    return {
        "root_path": str(root),
        "project_type": "typescript",
        "files": {
            f"{name}.ts": {
                "language": "typescript",
                "size": 20,
                "hash": content_hash,
                "symbols": [],
            }
        },
        "dependencies": {f"{name}.ts": []},
        "structure": {f"{name}.ts": {}},
        "symbols": [],
    }


def _sqlite_store(root: Path, *, inline_limit: int = 1024, part_size: int = 128) -> ArtifactStore:
    store = ArtifactStore(raw_dir=root)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(root / "codemaps.db")
    store._schema_initialized = False
    store.state_payload_inline_limit_bytes = inline_limit
    store.state_payload_part_size_bytes = part_size
    store._ensure_schema()
    return store


def test_small_state_payload_remains_inline_and_byte_equivalent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _sqlite_store(root, inline_limit=4096, part_size=64)
        payload = {"MAIN": {"files": {"a.ts": {"hash": "small"}}}}

        profile = store._save_payload_to_state_table("atlas", payload)

        with store.db_manager.get_connection() as conn:
            row = conn.execute(
                "SELECT payload, payload_bytes, storage_mode, part_count FROM state_payloads WHERE name = 'atlas';"
            ).fetchone()
            part_rows = int(conn.execute(
                "SELECT COUNT(*) FROM state_payload_parts WHERE name = 'atlas';"
            ).fetchone()[0])

        expected = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert str(row["payload"]) == expected
        assert int(row["payload_bytes"]) == len(expected.encode("utf-8"))
        assert row["storage_mode"] == "inline_json"
        assert int(row["part_count"]) == 0
        assert part_rows == 0
        assert profile["state_payload_storage_mode"] == "inline_json"
        assert store.load_raw("atlas", {}) == payload


def test_legacy_inline_state_payload_migrates_without_rewrite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / "codemaps.db"
        payload = {"legacy": True}
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with closing(sqlite3.connect(db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE state_payloads (
                        name TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        payload_sha TEXT,
                        source_mtime REAL,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO state_payloads (name, payload, payload_sha, source_mtime) VALUES ('atlas', ?, '', 1.0);",
                    (serialized,),
                )

        store = _sqlite_store(root)

        assert store.load_raw("atlas", {}) == payload
        with store.db_manager.get_connection() as conn:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(state_payloads);").fetchall()}
        assert {"payload_bytes", "storage_mode", "generation_id", "part_count"}.issubset(columns)


def test_json_shadow_export_streams_without_monolithic_json_dumps() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "atlas.json"
        payload = {"MAIN": {"files": {"a.ts": {"value": "x" * 200}}}}
        with patch.object(config_module.json, "dumps", side_effect=AssertionError("monolithic dumps called")):
            config_module.save_json_atomic(path, payload, bypass_proxy=True)

        assert json.loads(path.read_text(encoding="utf-8")) == payload


def test_generated_over_inline_limit_uses_bounded_parts_without_truncation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _sqlite_store(root, inline_limit=128, part_size=64)
        payload = {"MAIN": {"files": {f"f-{index}.ts": {"value": "🚀ç" * 40} for index in range(20)}}}

        profile = store._save_payload_to_state_table("atlas", payload)

        with store.db_manager.get_connection() as conn:
            row = conn.execute(
                "SELECT payload, payload_bytes, storage_mode, generation_id, part_count FROM state_payloads WHERE name = 'atlas';"
            ).fetchone()
            parts = conn.execute(
                "SELECT part_index, length(payload) AS size FROM state_payload_parts WHERE name = 'atlas' ORDER BY part_index;"
            ).fetchall()

        assert row["storage_mode"] == "partitioned_json_v1"
        assert json.loads(row["payload"])["__sage_partitioned_payload__"]["generation_id"] == row["generation_id"]
        assert len(parts) == int(row["part_count"]) == profile["state_payload_part_count"]
        assert [int(part["part_index"]) for part in parts] == list(range(len(parts)))
        assert max(int(part["size"]) for part in parts) <= 64
        assert int(row["payload_bytes"]) > 128
        assert store.load_raw("atlas", {}) == payload


def test_partition_corruption_never_returns_a_truncated_payload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _sqlite_store(root, inline_limit=64, part_size=32)
        payload = {"value": "partition-me" * 30}
        store._save_payload_to_state_table("sample", payload)
        with store.db_manager.get_connection() as conn:
            conn.execute(
                "DELETE FROM state_payload_parts WHERE name = 'sample' AND part_index = 1;"
            )

        sentinel = object()
        assert store.load_raw("sample", sentinel) is sentinel


def test_external_sqlite_first_reader_reassembles_partitioned_payload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw_dir = Path(tmp) / ".raw"
        raw_dir.mkdir()
        store = _sqlite_store(raw_dir, inline_limit=64, part_size=32)
        payload = {"MAIN": {"files": {"a.ts": {"value": "x" * 200}}}}
        store._save_payload_to_state_table("atlas", payload)

        assert load_raw_artifact_path(raw_dir / "atlas.json") == payload


def test_full_atlas_transaction_rolls_back_partitioned_payload_and_relational_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        store = _sqlite_store(root, inline_limit=64, part_size=32)
        original = {"MAIN": _project(root, "main", "main-old")}
        updated = {"MAIN": _project(root, "main", "main-new")}

        with patch.dict("os.environ", {"SAGE_SYNC_SHADOW_WRITES": "1"}, clear=False):
            store.save_raw("atlas", original)
        original_full_save = store._save_full_atlas_to_sqlite

        def fail_after_full_relational_write(payload, project_path_hints, findings_snapshot=None):
            original_full_save(payload, project_path_hints, findings_snapshot=findings_snapshot)
            raise RuntimeError("simulated full Atlas projection failure")

        with patch.object(store, "_save_full_atlas_to_sqlite", side_effect=fail_after_full_relational_write):
            with pytest.raises(ArtifactPrimaryWriteError, match="SQLite primary artifact write failed for atlas"):
                store.save_raw("atlas", updated)

        assert store.load_raw("atlas", {}) == original
        with store.db_manager.get_connection() as conn:
            relational_hash = conn.execute(
                "SELECT hash FROM files WHERE project_key = 'MAIN' AND rel_path = 'main.ts';"
            ).fetchone()["hash"]
            generations = conn.execute(
                "SELECT DISTINCT generation_id FROM state_payload_parts WHERE name = 'atlas';"
            ).fetchall()
        assert relational_hash == "main-old"
        assert len(generations) == 1


def test_atlas_staging_preserves_failed_work_and_clears_only_after_completion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _sqlite_store(Path(tmp))
        store.atlas_staging_batch_size = 2
        producer = "atlas-staging-v1:test"
        failed_run = store.begin_atlas_staging_run(producer)
        sequencer_payload = {"mtime": 1.0, "size": 10, "fingerprint": "fp", "results": []}
        file_payload = {
            "mtime": 1.0,
            "size": 10,
            "fingerprint": "fp",
            "ast_contract_version": "test",
            "internal_deps": [],
        }

        store.save_atlas_staging_batch(
            failed_run,
            producer,
            "MAIN",
            [("a.ts", sequencer_payload)],
            stage_kind="sequencer_result",
        )
        store.save_atlas_staging_batch(
            failed_run,
            producer,
            "MAIN",
            [("a.ts", file_payload)],
            stage_kind="atlas_file",
        )
        store.finish_atlas_staging_run(failed_run, producer, status="FAILED", error_type="OverflowError")

        assert store.load_atlas_staging_files(producer, stage_kind="sequencer_result")[("MAIN", "a.ts")] == sequencer_payload
        assert store.load_atlas_staging_files(producer, stage_kind="atlas_file")[("MAIN", "a.ts")] == file_payload
        assert store.load_atlas_staging_files("atlas-staging-v1:other") == {}

        completed_run = store.begin_atlas_staging_run(producer)
        store.finish_atlas_staging_run(completed_run, producer, status="COMPLETED")
        assert store.load_atlas_staging_files(producer, stage_kind="sequencer_result") == {}
        assert store.load_atlas_staging_files(producer, stage_kind="atlas_file") == {}


def test_new_staging_run_closes_abandoned_receipt_without_deleting_checkpoints() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _sqlite_store(Path(tmp))
        producer = "atlas-staging-v1:generic-producer"
        abandoned_run = store.begin_atlas_staging_run(producer)
        payload = {
            "mtime": 1.0,
            "size": 10,
            "fingerprint": "source-fingerprint",
            "ast_contract_version": "test",
            "internal_deps": [],
        }
        store.save_atlas_staging_batch(
            abandoned_run,
            producer,
            "GENERIC_PROJECT",
            [("generated/public-api.mjs", payload)],
        )

        replacement_producer = "atlas-staging-v1:new-producer-contract"
        replacement_run = store.begin_atlas_staging_run(replacement_producer)

        with store.db_manager.get_connection() as conn:
            abandoned_receipt = conn.execute(
                "SELECT status, error_type FROM atlas_staging_runs WHERE run_id = ?;",
                (abandoned_run,),
            ).fetchone()
            replacement_receipt = conn.execute(
                "SELECT status, error_type FROM atlas_staging_runs WHERE run_id = ?;",
                (replacement_run,),
            ).fetchone()
        assert abandoned_receipt["status"] == "FAILED"
        assert abandoned_receipt["error_type"] == "superseded_interrupted_run"
        assert replacement_receipt["status"] == "IN_PROGRESS"
        assert replacement_receipt["error_type"] == "none"
        assert store.load_atlas_staging_files(replacement_producer) == {}
        assert store.load_atlas_staging_files(producer)[
            ("GENERIC_PROJECT", "generated/public-api.mjs")
        ] == payload


def test_atlas_staging_rejects_an_unbounded_batch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _sqlite_store(Path(tmp))
        store.atlas_staging_batch_size = 1
        run_id = store.begin_atlas_staging_run("producer")

        with pytest.raises(ValueError, match="exceeds configured bound"):
            store.save_atlas_staging_batch(
                run_id,
                "producer",
                "MAIN",
                [("a.ts", {}), ("b.ts", {})],
            )


@pytest.mark.parametrize("initial_parser_available", [True, False])
def test_interrupted_real_atlas_generation_rejects_changed_source_then_reuses_latest_checkpoint(initial_parser_available) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repository_root = Path(tmp) / "repository"
        project_root = repository_root / "src"
        raw_dir = repository_root / "output" / ".raw"
        project_root.mkdir(parents=True)
        raw_dir.mkdir(parents=True)
        source = project_root / "a.ts"
        source.write_text("export const value = 1;\n", encoding="utf-8")
        store = _sqlite_store(raw_dir, inline_limit=256, part_size=64)
        node_calls = []
        attempted_atlas_hashes = []
        attempted_atlases = []
        fail_first_atlas_commit = {"enabled": True}

        def observed_node_batch(command, **_kwargs):
            node_calls.append(command)
            results = [{"name": "__file_meta__", "type": "Meta", "start": 0, "end": 0, "parserStatus": "observed", "parserKind": "typescript_compiler_api", "semanticDepth": "ast_normalized"}]
            if not initial_parser_available and len(node_calls) == 1:
                results = []
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({source.resolve().as_posix(): results}),
                stderr="",
            ), 0.01

        def write_managed(path, payload, indent=2):
            if Path(path).stem == "atlas":
                canonical = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                attempted_atlas_hashes.append(hashlib.sha256(canonical).hexdigest())
                attempted_atlases.append(json.loads(canonical))
                if fail_first_atlas_commit["enabled"]:
                    raise ArtifactPrimaryWriteError("simulated terminal Atlas failure")
            return store.save_raw(Path(path).stem, payload, indent=indent)

        patches = (
            patch.object(generate_atlas_module, "ROOT", repository_root),
            patch.object(generate_atlas_module, "RAW_DIR", raw_dir),
            patch.object(generate_atlas_module, "resolve_runtime_projects", return_value={"MAIN": project_root}),
            patch.object(generate_atlas_module, "project_ownership_exclusions", return_value={"MAIN": []}),
            patch.object(generate_atlas_module, "load_previous_atlas", return_value={}),
            patch.object(generate_atlas_module, "ensure_output_dir", side_effect=lambda: raw_dir.mkdir(parents=True, exist_ok=True)),
            patch.object(generate_atlas_module, "run_observed_subprocess", side_effect=observed_node_batch),
            patch.object(generate_atlas_module, "save_json_atomic", side_effect=write_managed),
            patch.object(artifact_store_module, "STORE", store),
            patch.object(config_module, "PROJECT_FILTER", []),
            patch.dict(generate_atlas_module.DYNAMIC_CONFIG, {"use_sqlite": True}, clear=True),
            patch.dict("os.environ", {"SAGE_SYNC_SHADOW_WRITES": "1"}, clear=False),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patches[11]:
            with pytest.raises(ArtifactPrimaryWriteError, match="simulated terminal Atlas failure"):
                generate_atlas_module.generate_atlas(stale_projects=["MAIN"])

            assert len(node_calls) == 1
            with store.db_manager.get_connection() as conn:
                failed_status = conn.execute(
                    "SELECT status FROM atlas_staging_runs ORDER BY started_at DESC LIMIT 1;"
                ).fetchone()["status"]
                staged_files = int(conn.execute(
                    "SELECT COUNT(*) FROM atlas_staging_files WHERE stage_kind = 'atlas_file';"
                ).fetchone()[0])
            assert failed_status == "FAILED"
            assert staged_files == 1

            # Unchanged source must retry absent parser evidence, not lift it from
            # either the completed-file or raw sequencer-result checkpoint.
            with pytest.raises(ArtifactPrimaryWriteError, match="simulated terminal Atlas failure"):
                generate_atlas_module.generate_atlas(stale_projects=["MAIN"])
            recovered_calls = 1 if initial_parser_available else 2
            assert len(node_calls) == recovered_calls

            original_mtime = source.stat().st_mtime
            original_size = source.stat().st_size
            source.write_text("export const value = 2;\n", encoding="utf-8")
            os.utime(source, (original_mtime, original_mtime))
            assert source.stat().st_size == original_size
            with pytest.raises(ArtifactPrimaryWriteError, match="simulated terminal Atlas failure"):
                generate_atlas_module.generate_atlas(stale_projects=["MAIN"])
            assert len(node_calls) == recovered_calls + 1

            with pytest.raises(ArtifactPrimaryWriteError, match="simulated terminal Atlas failure"):
                generate_atlas_module.generate_atlas(stale_projects=["MAIN"])
            assert len(node_calls) == recovered_calls + 1
            with store.db_manager.get_connection() as conn:
                resumed_failure = conn.execute(
                    "SELECT status, reused_files FROM atlas_staging_runs ORDER BY rowid DESC LIMIT 1;"
                ).fetchone()
            assert resumed_failure["status"] == "FAILED"
            assert int(resumed_failure["reused_files"]) == 1

            fail_first_atlas_commit["enabled"] = False
            atlas, changed_files, _ = generate_atlas_module.generate_atlas(stale_projects=["MAIN"])

        assert len(node_calls) == recovered_calls + 1
        assert len(attempted_atlas_hashes) == 5
        assert len(set(attempted_atlas_hashes[2:])) == 1
        assert len({
            json.dumps(
                candidate["MAIN"]["project"]["sequencer_evidence"],
                sort_keys=True,
                separators=(",", ":"),
            )
            for candidate in attempted_atlases[2:]
        }) == 1
        assert "a.ts" in atlas["MAIN"]["files"]
        assert changed_files == ["MAIN::a.ts"]
        with store.db_manager.get_connection() as conn:
            staged_files = int(conn.execute("SELECT COUNT(*) FROM atlas_staging_files;").fetchone()[0])
            completed_resume = conn.execute(
                "SELECT status, reused_files FROM atlas_staging_runs ORDER BY rowid DESC LIMIT 1;"
            ).fetchone()
        assert staged_files == 0
        assert completed_resume["status"] == "COMPLETED"
        assert int(completed_resume["reused_files"]) == 1


def test_explicit_store_raw_dir_owns_shadow_and_database_namespace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = ArtifactStore(raw_dir=root)

        assert store.raw_path("sample") == root.resolve() / "sample.json"
        assert store.db_manager.db_path == root.resolve() / "codemaps.db"


def test_surgical_generation_preserves_unselected_project_payloads() -> None:
    previous = {
        "MAIN": {"files": {"main.ts": {"hash": "old"}}},
        "VARIANT": {"files": {"variant.ts": {"hash": "stable"}}},
    }
    current = {"MAIN": {"files": {"main.ts": {"hash": "new"}}}}

    preserved = preserve_unselected_bounded_projects(
        current,
        previous,
        bounded_projection=True,
    )

    assert preserved == ["VARIANT"]
    assert current["VARIANT"] == previous["VARIANT"]
    assert current["VARIANT"] is not previous["VARIANT"]


def test_project_filtered_generation_preserves_unselected_project_payloads() -> None:
    previous = {
        "MAIN": {"files": {"main.ts": {"hash": "old"}}},
        "COMPANION": {"files": {"tool.ts": {"hash": "stable"}}},
        "VARIANT": {"files": {"variant.ts": {"hash": "stable"}}},
    }
    current = {"MAIN": {"files": {"main.ts": {"hash": "new"}}}}

    preserved = preserve_unselected_bounded_projects(
        current,
        previous,
        bounded_projection=True,
    )

    assert preserved == ["COMPANION", "VARIANT"]
    assert set(current) == {"MAIN", "COMPANION", "VARIANT"}


def test_unbounded_generation_does_not_preserve_removed_projects() -> None:
    previous = {
        "MAIN": {"files": {"main.ts": {"hash": "old"}}},
        "REMOVED": {"files": {"removed.ts": {"hash": "old"}}},
    }
    current = {"MAIN": {"files": {"main.ts": {"hash": "new"}}}}

    preserved = preserve_unselected_bounded_projects(
        current,
        previous,
        bounded_projection=False,
    )

    assert preserved == []
    assert set(current) == {"MAIN"}


def test_project_filter_marks_every_selected_project_file_for_atomic_persistence() -> None:
    atlas = {
        "MAIN": {"files": {"src/a.ts": {}, "src/b.ts": {}}},
        "VARIANT": {"files": {"src/variant.ts": {}}},
    }

    scope = bounded_atlas_snapshot_scope(
        atlas,
        ["MAIN"],
        {},
        project_filter_active=True,
    )

    assert scope == {"MAIN": ["src/a.ts", "src/b.ts"]}


def test_scoped_baseline_drift_exposes_exact_project_and_file_reason() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        store = ArtifactStore(raw_dir=root)
        store.use_sqlite = True
        store.backend = "hybrid_sqlite"
        store.db_manager = SQLiteManager(root / "codemaps.db")
        store._schema_initialized = False
        store._ensure_schema()
        baseline = {"MAIN": _project(root, "main", "old")}
        store._save_atlas_to_sqlite(baseline)
        drifted = {"MAIN": _project(root, "main", "new")}
        drifted["MAIN"]["files"]["added.ts"] = {
            "language": "typescript",
            "size": 20,
            "hash": "added",
            "symbols": [],
        }

        result = store._save_scoped_atlas_to_sqlite(
            drifted,
            {"MAIN": {"main.ts"}},
            {"MAIN": str(root)},
        )

    assert result is None
    diagnostic = store._last_scoped_atlas_baseline_diagnostic
    assert diagnostic["status"] == "DRIFT"
    assert diagnostic["reason"] == "unscoped_file_set_mismatch"
    assert diagnostic["file_set_drift"][0]["project"] == "MAIN"
    assert diagnostic["file_set_drift"][0]["missing_in_db_samples"] == ["added.ts"]


def test_scoped_fallback_profile_carries_baseline_diagnostic() -> None:
    store = ArtifactStore()
    store.use_sqlite = True
    diagnostic = {
        "status": "DRIFT",
        "reason": "project_set_mismatch",
        "missing_db_projects": ["MAIN"],
    }
    store._last_scoped_atlas_baseline_diagnostic = diagnostic

    with (
        patch.object(
            artifact_store_module,
            "_source_snapshot_projection_scope",
            return_value={"MAIN": {"main.ts"}},
        ),
        patch.object(store, "_save_scoped_atlas_to_sqlite", return_value=None),
        patch.object(store, "_snapshot_relational_findings", return_value=[]),
        patch.object(store, "_save_full_atlas_to_sqlite", return_value=(0, 0)),
        patch.object(store, "_restore_audit_findings_after_scoped_rebuild", return_value=True),
    ):
        profile = store._save_atlas_to_sqlite(
            {"MAIN": {"files": {}}},
        )

    assert profile["atlas_relational_mode"] == "full_fallback"
    assert profile["atlas_scoped_fallback_diagnostic"] == diagnostic


def test_surgical_scope_precedes_broader_project_filter_scope() -> None:
    atlas = {"MAIN": {"files": {"src/a.ts": {}, "src/b.ts": {}}}}

    scope = bounded_atlas_snapshot_scope(
        atlas,
        ["MAIN"],
        {"MAIN": {"src/b.ts"}},
        project_filter_active=True,
    )

    assert scope == {"MAIN": ["src/b.ts"]}


def test_bounded_bridge_reads_preserved_projects_without_mutating_them() -> None:
    atlas = {
        "MAIN": {
            "files": {
                "src/client.ts": {
                    "features": [],
                    "api_candidates": ["/api/books"],
                }
            },
            "dependencies": {"src/client.ts": []},
        },
        "COMPANION": {
            "files": {
                "src/server.ts": {
                    "features": ["route_path:/api/books"],
                    "api_candidates": [],
                }
            },
            "dependencies": {"src/server.ts": []},
        },
    }
    companion_before = json.dumps(atlas["COMPANION"], sort_keys=True)

    linked = apply_global_semantic_bridge(
        atlas,
        mutable_project_keys={"MAIN"},
    )

    assert linked == 1
    assert atlas["MAIN"]["dependencies"]["src/client.ts"] == [
        "COMPANION::src/server.ts"
    ]
    assert json.dumps(atlas["COMPANION"], sort_keys=True) == companion_before


def test_full_projection_does_not_resolve_unqualified_dependency_across_projects() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        main_root = root / "main"
        variant_root = root / "variant"
        main_root.mkdir()
        variant_root.mkdir()
        (main_root / "source.ts").write_text("export const source = 1;\n", encoding="utf-8")
        (variant_root / "target.ts").write_text("export const target = 1;\n", encoding="utf-8")
        store = ArtifactStore(raw_dir=root)
        store.use_sqlite = True
        store.backend = "hybrid_sqlite"
        store.db_manager = SQLiteManager(root / "codemaps.db")
        store._schema_initialized = False
        store._ensure_schema()
        atlas = {
            "MAIN": {
                **_project(main_root, "source", "main"),
                "dependencies": {"source.ts": ["target.ts"]},
            },
            "VARIANT": _project(variant_root, "target", "variant"),
        }

        store._save_full_atlas_to_sqlite(atlas, {})

        with store.db_manager.get_connection() as conn:
            dependency_count = int(conn.execute("SELECT COUNT(*) FROM dependencies;").fetchone()[0])

    assert dependency_count == 0


def test_full_projection_resolves_explicit_cross_project_dependency() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        main_root = root / "main"
        variant_root = root / "variant"
        main_root.mkdir()
        variant_root.mkdir()
        (main_root / "source.ts").write_text("export const source = 1;\n", encoding="utf-8")
        (variant_root / "target.ts").write_text("export const target = 1;\n", encoding="utf-8")
        store = ArtifactStore(raw_dir=root)
        store.use_sqlite = True
        store.backend = "hybrid_sqlite"
        store.db_manager = SQLiteManager(root / "codemaps.db")
        store._schema_initialized = False
        store._ensure_schema()
        atlas = {
            "MAIN": {
                **_project(main_root, "source", "main"),
                "dependencies": {"source.ts": ["VARIANT::target.ts"]},
            },
            "VARIANT": _project(variant_root, "target", "variant"),
        }

        store._save_full_atlas_to_sqlite(atlas, {})

        with store.db_manager.get_connection() as conn:
            dependency_count = int(conn.execute("SELECT COUNT(*) FROM dependencies;").fetchone()[0])

    assert dependency_count == 1


def test_scoped_atlas_rejects_partial_payload_before_mutating_sqlite_or_shadow() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        (root / "variant.ts").write_text("export const variant = 1;\n", encoding="utf-8")
        store = ArtifactStore(raw_dir=root)
        store.use_sqlite = True
        store.backend = "hybrid_sqlite"
        store.db_manager = SQLiteManager(root / "codemaps.db")
        store._schema_initialized = False
        store._ensure_schema()
        full_atlas = {
            "MAIN": _project(root, "main", "main-old"),
            "VARIANT": _project(root, "variant", "variant-stable"),
        }
        store._save_payload_to_state_table("atlas", full_atlas)
        store._save_atlas_to_sqlite(full_atlas)
        before = store.load_raw("atlas", {})

        partial = {
            "MAIN": _project(root, "main", "main-new"),
        }
        with patch.dict(
            artifact_store_module.DYNAMIC_CONFIG,
            {"_source_snapshot_projection_scope": {"MAIN": ["main.ts"]}},
            clear=False,
        ):
            with pytest.raises(
                UnsafeScopedAtlasProjectionError,
                match="would erase unselected relational projects: VARIANT",
            ):
                store.save_raw("atlas", partial)

        after = store.load_raw("atlas", {})
        with store.db_manager.get_connection() as conn:
            projects = [
                str(row["project_key"])
                for row in conn.execute(
                    "SELECT project_key FROM projects ORDER BY project_key;"
                ).fetchall()
            ]
            variant_hash = str(
                conn.execute(
                    "SELECT hash FROM files WHERE project_key = ? AND rel_path = ?;",
                    ("VARIANT", "variant.ts"),
                ).fetchone()["hash"]
            )

        assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)
        assert projects == ["MAIN", "VARIANT"]
        assert variant_hash == "variant-stable"


def test_scoped_atlas_primary_transaction_rolls_back_payload_and_relational_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        store = ArtifactStore(raw_dir=root)
        store.use_sqlite = True
        store.backend = "hybrid_sqlite"
        store.db_manager = SQLiteManager(root / "codemaps.db")
        store._schema_initialized = False
        store._ensure_schema()
        original = {"MAIN": _project(root, "main", "main-old")}
        store._save_payload_to_state_table("atlas", original)
        store._save_atlas_to_sqlite(original)
        updated = {"MAIN": _project(root, "main", "main-new")}
        original_scoped_save = store._save_scoped_atlas_to_sqlite

        def fail_after_relational_write(payload, snapshot_scope, project_path_hints):
            result = original_scoped_save(payload, snapshot_scope, project_path_hints)
            raise RuntimeError(f"simulated crash after {result['atlas_relational_mode']}")

        with patch.dict(
            artifact_store_module.DYNAMIC_CONFIG,
            {"_source_snapshot_projection_scope": {"MAIN": ["main.ts"]}},
            clear=False,
        ), patch.object(
            store,
            "_save_scoped_atlas_to_sqlite",
            side_effect=fail_after_relational_write,
        ):
            with pytest.raises(
                UnsafeScopedAtlasProjectionError,
                match="primary transaction failed before commit",
            ):
                store.save_raw("atlas", updated)

        with store.db_manager.get_connection() as conn:
            persisted_payload = json.loads(
                str(
                    conn.execute(
                        "SELECT payload FROM state_payloads WHERE name = ?;",
                        ("atlas",),
                    ).fetchone()["payload"]
                )
            )
            persisted_hash = str(
                conn.execute(
                    "SELECT hash FROM files WHERE project_key = ? AND rel_path = ?;",
                    ("MAIN", "main.ts"),
                ).fetchone()["hash"]
            )

        assert persisted_payload == original
        assert persisted_hash == "main-old"


def test_scoped_atlas_tombstone_removes_payload_and_relational_row_atomically() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "deleted.ts"
        target.write_text("export const deleted = true;\n", encoding="utf-8")
        store = ArtifactStore(raw_dir=root)
        store.use_sqlite = True
        store.backend = "hybrid_sqlite"
        store.db_manager = SQLiteManager(root / "codemaps.db")
        store._schema_initialized = False
        store._ensure_schema()
        original = {"MAIN": _project(root, "deleted", "old")}
        store._save_payload_to_state_table("atlas", original)
        store._save_atlas_to_sqlite(original)
        target.unlink()
        updated = {
            "MAIN": {
                **original["MAIN"],
                "files": {},
                "dependencies": {},
                "structure": {},
                "symbols": [],
            }
        }

        with patch.dict(
            artifact_store_module.DYNAMIC_CONFIG,
            {"_source_snapshot_projection_scope": {"MAIN": ["deleted.ts"]}},
            clear=False,
        ):
            store.save_raw("atlas", updated)

        persisted = store.load_raw("atlas", {})
        with store.db_manager.get_connection() as conn:
            row = conn.execute(
                "SELECT hash FROM files WHERE project_key = ? AND rel_path = ?;",
                ("MAIN", "deleted.ts"),
            ).fetchone()
            snapshot_row = conn.execute(
                "SELECT status FROM source_snapshots WHERE project_key = ? AND rel_path = ?;",
                ("MAIN", "deleted.ts"),
            ).fetchone()

        assert persisted["MAIN"]["files"] == {}
        assert persisted["MAIN"]["structure"] == {}
        assert row is None
        assert snapshot_row is None

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core import artifact_store as artifact_store_module
from tools.core.artifact_store import ArtifactStore, UnsafeScopedAtlasProjectionError
from tools.core.db import SQLiteManager
from tools.engines.generate_atlas import (
    apply_global_semantic_bridge,
    bounded_atlas_snapshot_scope,
    preserve_unselected_bounded_projects,
)


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

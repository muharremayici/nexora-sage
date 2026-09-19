import hashlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core import artifact_store as artifact_store_module
from tools.core.artifact_store import (
    ArtifactStore,
    _iter_bounded_canonical_json_chunks,
)
from tools.core.db import SQLiteManager
from tools.engines import generate_atlas as generate_atlas_module


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


def test_bounded_canonical_chunks_preserve_exact_unicode_bytes_and_sha() -> None:
    payload = {
        "\u03a9-project": {
            "files": {
                "b.ts": {"value": "\U0001f680\u00e7" * 80},
                "a.ts": {"value": "line\\nvalue"},
            }
        }
    }
    expected = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    chunks = list(_iter_bounded_canonical_json_chunks(payload, 17))
    actual = "".join(chunks)

    assert actual == expected
    assert chunks
    assert max(len(chunk) for chunk in chunks) <= 17
    assert max(len(chunk.encode("utf-8")) for chunk in chunks) <= 68
    assert hashlib.sha256(actual.encode("utf-8")).hexdigest() == hashlib.sha256(
        expected.encode("utf-8")
    ).hexdigest()


def test_bounded_canonical_chunks_reject_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        list(_iter_bounded_canonical_json_chunks({"MAIN": {}}, 0))


def test_scoped_save_profile_exposes_canonical_and_relational_materialization_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        (root / "variant.ts").write_text("export const variant = 1;\n", encoding="utf-8")
        store = _sqlite_store(root, inline_limit=64, part_size=32)
        original = {
            "MAIN": _project(root, "main", "main-old"),
            "VARIANT": _project(root, "variant", "variant-stable"),
        }
        store._save_payload_to_state_table("atlas", original)
        store._save_full_atlas_to_sqlite(original, {})
        updated = {
            "MAIN": _project(root, "main", "main-new"),
            "VARIANT": _project(root, "variant", "variant-stable"),
        }

        with (
            patch.dict(
                artifact_store_module.DYNAMIC_CONFIG,
                {"_source_snapshot_projection_scope": {"MAIN": ["main.ts"]}},
                clear=False,
            ),
            patch.dict("os.environ", {"SAGE_SYNC_SHADOW_WRITES": "1"}, clear=False),
            patch.object(artifact_store_module, "_bg_write_worker", return_value=None),
        ):
            profile = store.save_raw("atlas", updated)

    assert profile["atlas_relational_mode"] == "scoped"
    assert profile["state_payload_generation_id"].startswith("payload-")
    assert profile["atlas_materialization_generation_id"] == profile["state_payload_generation_id"]
    assert profile["atlas_canonical_project_count"] == 2
    assert profile["atlas_canonical_file_count"] == 2
    assert profile["atlas_relational_project_count"] == 2
    assert profile["atlas_relational_file_count"] == 2
    assert profile["atlas_scoped_projects"] == 1
    assert profile["atlas_scoped_files"] == 1
    assert profile["atlas_unselected_canonical_projects"] == 1
    assert profile["atlas_dependency_sources_updated"] == 1
    assert profile["atlas_scoped_baseline_status"] == "PASS"
    assert profile["atlas_scoped_baseline_reason"] == "relational_baseline_matches_canonical_payload"
    assert profile["state_payload_stream_chunks"] > 0
    assert profile["atlas_state_payload_write_seconds"] >= 0
    assert profile["atlas_relational_index_seconds"] >= 0
    assert profile["atlas_primary_transaction_seconds"] >= 0
    assert profile["atlas_transaction_commit_seconds"] >= 0


def test_atlas_state_payload_reuse_hint_requires_exact_sqlite_identity() -> None:
    previous = {"MAIN": {"files": {"main.ts": {"hash": "stable"}}}}
    current = {"MAIN": {"files": {"main.ts": {"hash": "stable"}}}}

    assert generate_atlas_module.atlas_state_payload_reuse_hint(
        previous,
        current,
        "sqlite:canonical-sha",
    ) == {
        "expected_payload_sha": "canonical-sha",
        "equality_contract": "exact_previous_atlas_object_equality_v1",
    }
    assert generate_atlas_module.atlas_state_payload_reuse_hint(
        previous,
        {"MAIN": {"files": {"main.ts": {"hash": "changed"}}}},
        "sqlite:canonical-sha",
    ) == {}
    assert generate_atlas_module.atlas_state_payload_reuse_hint(
        previous,
        current,
        "json_shadow:canonical-sha",
    ) == {}
    assert generate_atlas_module.atlas_state_payload_reuse_hint(
        {},
        {},
        "sqlite:canonical-sha",
    ) == {}


def test_exact_unchanged_atlas_reuses_state_generation_but_reprojects_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        store = _sqlite_store(root, inline_limit=64, part_size=32)
        payload = {"MAIN": _project(root, "main", "stable")}
        with patch.dict(
            "os.environ",
            {"SAGE_SYNC_SHADOW_WRITES": "1"},
            clear=False,
        ):
            initial = store.save_raw("atlas", payload)
            with patch.dict(
                artifact_store_module.DYNAMIC_CONFIG,
                {
                    "_source_snapshot_projection_scope": {"MAIN": ["main.ts"]},
                    "_atlas_state_payload_reuse": {
                        "expected_payload_sha": initial["state_payload_sha256"],
                        "equality_contract": "exact_previous_atlas_object_equality_v1",
                    },
                },
                clear=False,
            ):
                reused = store.save_raw("atlas", payload)

    assert reused["state_payload_reuse_status"] == "reused_exact_identity"
    assert reused["shadow_write_mode"] == "reused_current_shadow"
    assert reused["shadow_thread_started"] is False
    assert reused["state_payload_reused_bytes"] == reused["state_payload_bytes"]
    assert reused["state_payload_generation_id"] == initial["state_payload_generation_id"]
    assert reused["state_payload_serialize_seconds"] == 0
    assert reused["state_payload_encode_seconds"] == 0
    assert reused["state_payload_hash_seconds"] == 0
    assert reused["state_payload_sqlite_seconds"] == 0
    assert reused["state_payload_stream_chunks"] == 0
    assert reused["atlas_relational_mode"] == "scoped"
    assert reused["atlas_scoped_baseline_status"] == "PASS"


def test_exact_state_reuse_repairs_a_stale_compatibility_shadow() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        store = _sqlite_store(root, inline_limit=64, part_size=32)
        payload = {"MAIN": _project(root, "main", "stable")}
        with patch.dict("os.environ", {"SAGE_SYNC_SHADOW_WRITES": "1"}, clear=False):
            initial = store.save_raw("atlas", payload)
            os.utime(store.raw_path("atlas"), (1.0, 1.0))
            with (
                patch.dict(
                    artifact_store_module.DYNAMIC_CONFIG,
                    {
                        "_source_snapshot_projection_scope": {"MAIN": ["main.ts"]},
                        "_atlas_state_payload_reuse": {
                            "expected_payload_sha": initial["state_payload_sha256"],
                            "equality_contract": "exact_previous_atlas_object_equality_v1",
                        },
                    },
                    clear=False,
                ),
                patch.object(artifact_store_module, "_bg_write_worker", return_value=None) as shadow_write,
            ):
                reused = store.save_raw("atlas", payload)

    assert reused["state_payload_reuse_status"] == "reused_exact_identity"
    assert reused["shadow_write_mode"] == "synchronous_bounded_process"
    shadow_write.assert_called_once()


def test_stale_state_reuse_identity_falls_back_to_canonical_rewrite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        store = _sqlite_store(root, inline_limit=64, part_size=32)
        payload = {"MAIN": _project(root, "main", "stable")}
        with (
            patch.dict("os.environ", {"SAGE_SYNC_SHADOW_WRITES": "1"}, clear=False),
            patch.object(artifact_store_module, "_bg_write_worker", return_value=None),
        ):
            initial = store.save_raw("atlas", payload)
            with patch.dict(
                artifact_store_module.DYNAMIC_CONFIG,
                {
                    "_source_snapshot_projection_scope": {"MAIN": ["main.ts"]},
                    "_atlas_state_payload_reuse": {
                        "expected_payload_sha": "stale-sha",
                        "equality_contract": "exact_previous_atlas_object_equality_v1",
                    },
                },
                clear=False,
            ):
                rewritten = store.save_raw("atlas", payload)

    assert rewritten["state_payload_reuse_status"] == "rejected"
    assert rewritten["state_payload_reuse_rejected_reason"] == "payload_sha_mismatch"
    assert rewritten["state_payload_reused_bytes"] == 0
    assert rewritten["state_payload_generation_id"] != initial["state_payload_generation_id"]
    assert rewritten["atlas_relational_mode"] == "scoped"


def test_scoped_fallback_profile_binds_drift_to_current_canonical_generation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.ts").write_text("export const main = 1;\n", encoding="utf-8")
        store = _sqlite_store(root, inline_limit=64, part_size=32)
        original = {"MAIN": _project(root, "main", "main-old")}
        store._save_payload_to_state_table("atlas", original)
        store._save_full_atlas_to_sqlite(original, {})
        updated = {"MAIN": _project(root, "main", "main-new")}
        updated["MAIN"]["files"]["unscoped.ts"] = {
            "language": "typescript",
            "size": 20,
            "hash": "unscoped",
            "symbols": [],
        }

        with (
            patch.dict(
                artifact_store_module.DYNAMIC_CONFIG,
                {"_source_snapshot_projection_scope": {"MAIN": ["main.ts"]}},
                clear=False,
            ),
            patch.dict("os.environ", {"SAGE_SYNC_SHADOW_WRITES": "1"}, clear=False),
            patch.object(artifact_store_module, "_bg_write_worker", return_value=None),
        ):
            profile = store.save_raw("atlas", updated)

    diagnostic = profile["atlas_scoped_fallback_diagnostic"]
    assert profile["atlas_relational_mode"] == "full_fallback"
    assert profile["atlas_canonical_project_count"] == 1
    assert profile["atlas_canonical_file_count"] == 2
    assert profile["atlas_relational_project_count"] == 1
    assert profile["atlas_relational_file_count"] == 2
    assert profile["atlas_scoped_projects"] == 1
    assert profile["atlas_scoped_files"] == 1
    assert profile["atlas_scoped_baseline_status"] == "DRIFT"
    assert profile["atlas_scoped_baseline_reason"] == "unscoped_file_set_mismatch"
    assert diagnostic["state_payload_generation_id"] == profile["atlas_materialization_generation_id"]



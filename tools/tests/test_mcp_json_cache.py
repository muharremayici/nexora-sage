from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from tools.core.json_io import clear_json_content_cache, json_content_cache_metrics
from tools.core.fractal_io import load_fractal_map_data
from tools.core.genome_io import load_genome_data
from tools.mcp.server import _ATLAS_CACHE, _GRAPH_CACHE, _atlas, _dependency_graph_from_raw, _load_json


def _create_state_db(raw_dir: Path) -> Path:
    db_path = raw_dir / "codemaps.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_sha TEXT)"
        )
    return db_path


def _write_state_payload(db_path: Path, name: str, payload: object) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO state_payloads (name, payload) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload = excluded.payload, payload_sha = NULL",
            (name, json.dumps(payload)),
        )


def test_mcp_config_cache_detects_same_mtime_same_size_change(tmp_path: Path) -> None:
    path = tmp_path / "contract.json"
    path.write_text('{"value":1}', encoding="utf-8")
    original = path.stat()
    clear_json_content_cache()

    assert _load_json(path) == {"value": 1}
    assert _load_json(path) == {"value": 1}
    path.write_text('{"value":2}', encoding="utf-8")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))

    assert _load_json(path) == {"value": 2}
    metrics = json_content_cache_metrics()
    assert metrics["reads"] == 3
    assert metrics["hits"] == 1
    assert metrics["misses"] == 2


def test_mcp_external_raw_artifact_prefers_sqlite_over_json_shadow(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    path = raw_dir / "signals.json"
    path.write_text(json.dumps({"source": "json-shadow"}), encoding="utf-8")
    db_path = _create_state_db(raw_dir)
    _write_state_payload(db_path, "signals", {"source": "sqlite", "revision": 1})

    assert _load_json(path) == {"source": "sqlite", "revision": 1}

    _write_state_payload(db_path, "signals", {"source": "sqlite", "revision": 2})
    assert _load_json(path) == {"source": "sqlite", "revision": 2}


def test_mcp_external_raw_artifact_falls_back_to_shadow_when_row_missing(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    path = raw_dir / "signals.json"
    path.write_text(json.dumps({"source": "json-shadow"}), encoding="utf-8")
    _create_state_db(raw_dir)

    assert _load_json(path) == {"source": "json-shadow"}


def test_mcp_atlas_cache_refreshes_from_sqlite_with_shadow_unchanged(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    (raw_dir / "atlas.json").write_text(json.dumps({"MAIN": {"source": "json-shadow"}}), encoding="utf-8")
    db_path = _create_state_db(raw_dir)
    _ATLAS_CACHE.clear()

    _write_state_payload(db_path, "atlas", {"MAIN": {"source": "sqlite", "revision": 1}})
    assert _atlas(raw_dir)["MAIN"]["revision"] == 1

    _write_state_payload(db_path, "atlas", {"MAIN": {"source": "sqlite", "revision": 2}})
    assert _atlas(raw_dir)["MAIN"]["revision"] == 2


def test_mcp_graph_cache_refreshes_from_sqlite_with_shadow_unchanged(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    (raw_dir / "circular_deps.json").write_text(
        json.dumps({"nodes": {}, "edges": [], "source": "json-shadow"}),
        encoding="utf-8",
    )
    db_path = _create_state_db(raw_dir)
    _GRAPH_CACHE.clear()

    _write_state_payload(
        db_path,
        "circular_deps",
        {"nodes": {"MAIN::a.py": {}}, "edges": [], "revision": 1},
    )
    nodes, _, _ = _dependency_graph_from_raw(raw_dir)
    assert nodes == {"MAIN::a.py": {}}

    _write_state_payload(
        db_path,
        "circular_deps",
        {"nodes": {"MAIN::b.py": {}}, "edges": [], "revision": 2},
    )
    nodes, _, _ = _dependency_graph_from_raw(raw_dir)
    assert nodes == {"MAIN::b.py": {}}


def test_external_large_artifact_readers_share_sqlite_first_path(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    db_path = _create_state_db(raw_dir)
    readers = {
        "genome": load_genome_data,
        "fractal_map": load_fractal_map_data,
    }
    for name, reader in readers.items():
        (raw_dir / f"{name}.json").write_text(
            json.dumps({"source": "json-shadow"}),
            encoding="utf-8",
        )
        _write_state_payload(db_path, name, {"source": "sqlite", "artifact": name})
        assert reader(raw_dir)["source"] == "sqlite"

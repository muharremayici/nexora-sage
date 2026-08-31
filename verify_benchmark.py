from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

workspace_dir = Path(__file__).resolve().parent
if str(workspace_dir) not in sys.path:
    sys.path.insert(0, str(workspace_dir))

from tools.core.artifact_store import STORE


ATLAS_PATH = workspace_dir / "output" / ".raw" / "atlas.json"
DB_PATH = workspace_dir / "output" / ".raw" / "codemaps.db"
TEMP_JSON = workspace_dir / "output" / ".raw" / "temp_atlas_bench.json"


def measure(fn: Callable[[], Any], repeat: int = 5) -> tuple[float, Any]:
    samples: list[float] = []
    result: Any = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples), result


def load_atlas() -> dict[str, Any]:
    with ATLAS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def project_items(atlas: dict[str, Any]):
    for project_key, project_payload in atlas.items():
        if project_key == "symbols" or not isinstance(project_payload, dict):
            continue
        yield str(project_key), project_payload


def choose_target(conn: sqlite3.Connection) -> tuple[str, str]:
    row = conn.execute(
        """
        SELECT files.project_key, files.rel_path, COUNT(symbols.symbol_id) AS symbol_count
        FROM files
        JOIN symbols ON symbols.file_id = files.file_id
        GROUP BY files.file_id
        ORDER BY symbol_count DESC
        LIMIT 1;
        """
    ).fetchone()
    if not row:
        raise RuntimeError("No symbol-bearing file found in SQLite index.")
    return str(row["project_key"]), str(row["rel_path"])


def json_symbol_lookup(atlas: dict[str, Any], project_key: str, rel_path: str) -> list[tuple[str, str]]:
    project_payload = atlas.get(project_key, {})
    symbols_payload = project_payload.get("symbols", {}) if isinstance(project_payload, dict) else {}
    if not isinstance(symbols_payload, dict):
        return []
    return [
        (str(symbol_name), str(symbol_info.get("type", "unknown")))
        for symbol_name, symbol_info in symbols_payload.items()
        if isinstance(symbol_info, dict) and str(symbol_info.get("file", "")) == rel_path
    ]


def sqlite_symbol_lookup(conn: sqlite3.Connection, project_key: str, rel_path: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT symbols.name, symbols.type
        FROM symbols
        JOIN files ON symbols.file_id = files.file_id
        WHERE files.project_key = ? AND files.rel_path = ?;
        """,
        (project_key, rel_path),
    ).fetchall()
    return [(str(row["name"]), str(row["type"])) for row in rows]


def json_dependency_top(atlas: dict[str, Any], limit: int = 5) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for project_key, project_payload in project_items(atlas):
        deps_payload = project_payload.get("dependencies", {})
        if not isinstance(deps_payload, dict):
            continue
        for rel_path, targets in deps_payload.items():
            if isinstance(targets, list):
                counts[f"{project_key}::{rel_path}"] = len(targets)
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]


def sqlite_dependency_top(conn: sqlite3.Connection, limit: int = 5) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT files.project_key, files.rel_path, COUNT(dependencies.target_file_id) AS dep_count
        FROM dependencies
        JOIN files ON dependencies.source_file_id = files.file_id
        GROUP BY dependencies.source_file_id
        ORDER BY dep_count DESC
        LIMIT ?;
        """,
        (limit,),
    ).fetchall()
    return [(f"{row['project_key']}::{row['rel_path']}", int(row["dep_count"])) for row in rows]


def write_json_snapshot(atlas: dict[str, Any]) -> None:
    with TEMP_JSON.open("w", encoding="utf-8") as handle:
        json.dump(atlas, handle, ensure_ascii=False, separators=(",", ":"))
    try:
        TEMP_JSON.unlink()
    except OSError:
        pass


def main() -> int:
    if not ATLAS_PATH.exists():
        print(f"Missing atlas: {ATLAS_PATH}")
        return 1
    if not DB_PATH.exists():
        print(f"Missing SQLite DB: {DB_PATH}")
        return 1
    if ATLAS_PATH.stat().st_size < 1024:
        print("Atlas is too small for a valid benchmark; refusing to benchmark placeholder JSON.")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    json_load_time, atlas = measure(load_atlas, repeat=3)
    project_key, rel_path = choose_target(conn)

    json_symbol_time, json_symbols = measure(lambda: json_symbol_lookup(atlas, project_key, rel_path), repeat=20)
    sqlite_symbol_time, sqlite_symbols = measure(lambda: sqlite_symbol_lookup(conn, project_key, rel_path), repeat=20)
    json_dep_time, json_deps = measure(lambda: json_dependency_top(atlas), repeat=10)
    sqlite_dep_time, sqlite_deps = measure(lambda: sqlite_dependency_top(conn), repeat=10)
    json_write_time, _ = measure(lambda: write_json_snapshot(atlas), repeat=3)
    sqlite_reindex_time, _ = measure(lambda: STORE._save_atlas_to_sqlite(atlas), repeat=1)

    symbol_match = sorted(json_symbols) == sorted(sqlite_symbols)
    dep_overlap = bool({item[0] for item in json_deps} & {item[0] for item in sqlite_deps})

    print("=" * 72)
    print("Nexora SAGE Hybrid SQLite Benchmark")
    print("=" * 72)
    print(f"Atlas JSON size: {ATLAS_PATH.stat().st_size / 1024 / 1024:.2f} MB")
    print(f"SQLite DB size:  {DB_PATH.stat().st_size / 1024 / 1024:.2f} MB")
    print(f"Target file:     {project_key}::{rel_path}")
    print()
    print("Cold JSON path")
    print(f"  JSON parse/load median:       {json_load_time * 1000:.2f} ms")
    print(f"  JSON write snapshot median:   {json_write_time * 1000:.2f} ms")
    print()
    print("Warm in-memory JSON path")
    print(f"  Symbol lookup median:         {json_symbol_time * 1000:.4f} ms ({len(json_symbols)} symbols)")
    print(f"  Dependency top-5 median:      {json_dep_time * 1000:.4f} ms")
    print()
    print("SQLite indexed path")
    print(f"  Symbol lookup median:         {sqlite_symbol_time * 1000:.4f} ms ({len(sqlite_symbols)} symbols)")
    print(f"  Dependency top-5 median:      {sqlite_dep_time * 1000:.4f} ms")
    print(f"  Full atlas reindex time:      {sqlite_reindex_time:.4f} s")
    print()
    print("Interpretation")
    cold_json_symbol = json_load_time + json_symbol_time
    cold_sqlite_symbol = sqlite_symbol_time
    cold_json_dep = json_load_time + json_dep_time
    cold_sqlite_dep = sqlite_dep_time
    print(f"  Cold symbol query speedup:    {cold_json_symbol / cold_sqlite_symbol:.1f}x")
    print(f"  Cold dependency speedup:      {cold_json_dep / cold_sqlite_dep:.1f}x")
    print("  Warm in-memory JSON can be faster for already-loaded pipeline stages.")
    print("  SQLite is most valuable for MCP/ContextOS/random access without 60MB parse.")
    print()
    print("Parity")
    print(f"  Symbol lookup parity:         {'PASS' if symbol_match else 'FAIL'}")
    print(f"  Dependency sample overlap:    {'PASS' if dep_overlap else 'WARN'}")
    print("=" * 72)

    conn.close()
    return 0 if symbol_match else 1


if __name__ == "__main__":
    raise SystemExit(main())

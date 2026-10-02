"""Free-text Audit and Module filters must not interpret SQL wildcards."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from tools.core.db import SQLiteManager
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.mcp import server


def _findings_database(raw_dir: Path) -> None:
    database = raw_dir / "codemaps.db"
    SQLiteManager(database).initialize_schema()
    with closing(sqlite3.connect(native_filesystem_path(database))) as conn:
        conn.executemany(
            "INSERT INTO projects (project_key, path, type) VALUES (?, ?, ?)",
            [("MAIN", "", "app"), ("COMPANION", "companion", "app")],
        )
        rows = [
            ("MAIN", "src/module_1/one.ts", "warn_ing", "rule_1"),
            ("MAIN", "src/moduleX1/other.ts", "warnXing", "ruleX1"),
            ("MAIN", "src/percent%mod/percent.ts", "high", "rule%"),
            ("COMPANION", "src/module_1/peer.ts", "warn_ing", "rule_1"),
        ]
        for project, path, severity, rule in rows:
            cursor = conn.execute(
                "INSERT INTO files (project_key, rel_path, language, size_bytes, hash) "
                "VALUES (?, ?, 'TypeScript', 1, 'fixture')",
                (project, path),
            )
            conn.execute(
                "INSERT INTO findings (engine_name, file_id, severity, code, message) "
                "VALUES ('audit_report', ?, ?, ?, '{}')",
                (cursor.lastrowid, severity, rule),
            )
        conn.commit()


def test_audit_rule_and_severity_filters_are_literal_and_paginated(monkeypatch, tmp_path: Path) -> None:
    _findings_database(tmp_path)
    monkeypatch.setattr(server, "_doctrine", lambda: {})
    monkeypatch.setattr(server, "_rule_guidance", lambda *args: {})

    for filters, expected_rule in (
        ({"rule": "rule_1"}, "rule_1"),
        ({"rule": "rule%"}, "rule%"),
        ({"severity": "warn_ing"}, "rule_1"),
    ):
        rows, total, ok = server._audit_violation_work_items_from_sqlite(
            tmp_path, page=1, page_size=1, project="MAIN", **filters,
        )
        assert ok is True and total == 1
        assert len(rows) == 1 and rows[0]["rule"] == expected_rule
        assert rows[0]["target_project"] == "MAIN"
        next_page, next_total, next_ok = server._audit_violation_work_items_from_sqlite(
            tmp_path, page=2, page_size=1, project="MAIN", **filters,
        )
        assert next_ok is True and next_total == 1 and next_page == []

    none, count, ok = server._audit_violation_work_items_from_sqlite(
        tmp_path, page=1, page_size=10, project="MAIN", rule="absent_%",
    )
    assert ok is True and count == 0 and none == []


def test_module_path_filter_is_literal_and_keeps_main_scope(monkeypatch, tmp_path: Path) -> None:
    _findings_database(tmp_path)
    monkeypatch.setattr(server, "_doctrine", lambda: {})
    monkeypatch.setattr(server, "_rule_guidance", lambda *args: {})
    monkeypatch.setattr(
        server, "_target_context_from_project_file",
        lambda _raw_dir, project_key, rel_path, **kwargs: {
            "target_file": rel_path,
            "target_ref": f"{project_key}::{rel_path}",
        },
    )
    for query, expected_path in (
        ("src/module_1", "src/module_1/one.ts"),
        ("src/percent%mod", "src/percent%mod/percent.ts"),
    ):
        rows, total, ok = server._module_integrity_items_from_sqlite(
            tmp_path, query, max_items=1,
        )
        assert ok is True and total == 1
        assert len(rows) == 1 and rows[0]["file"] == expected_path
        assert rows[0]["target_ref"] == f"MAIN::{expected_path}"

    assert server._audit_violation_work_items_from_sqlite(
        tmp_path / "missing", page=1, page_size=10, rule="rule_1",
    ) == ([], 0, False)

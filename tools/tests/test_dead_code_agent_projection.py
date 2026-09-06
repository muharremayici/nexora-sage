from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from tools import inspect_target
from tools.mcp import server


def _create_inspection_db(raw_dir: Path, dead_code_payload: dict | None) -> None:
    raw_dir.mkdir()
    with sqlite3.connect(raw_dir / "codemaps.db") as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_key TEXT PRIMARY KEY, path TEXT);
            CREATE TABLE files (file_id INTEGER PRIMARY KEY, project_key TEXT, rel_path TEXT);
            CREATE TABLE symbols (file_id INTEGER, name TEXT, type TEXT, line INTEGER, char INTEGER, end_line INTEGER, source_lines TEXT);
            CREATE TABLE dependencies (source_file_id INTEGER);
            CREATE TABLE findings (finding_id TEXT, file_id INTEGER, engine_name TEXT, severity TEXT, code TEXT, message TEXT);
            CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT, payload_sha TEXT, source_mtime REAL, updated_at TEXT);
            INSERT INTO projects VALUES ('MAIN', 'src');
            INSERT INTO files VALUES (1, 'MAIN', 'feature/CharactersTab.tsx');
            INSERT INTO findings VALUES (
                'finding-1', 1, 'audit_report', 'enforced', 'deep_imports',
                '{"rule":"deep_imports","detail":"Cross-lifecycle deep import"}'
            );
            """
        )
        if dead_code_payload is not None:
            conn.execute(
                "INSERT INTO state_payloads VALUES (?, ?, ?, ?, ?)",
                ("dead_code", json.dumps(dead_code_payload), "dead-code-sha", 1.0, "2026-08-08T00:00:00Z"),
            )


def _inspect(raw_dir: Path) -> dict:
    with patch.object(
        server,
        "_sqlite_file_context_from_raw",
        return_value=(
            "MAIN::feature/CharactersTab.tsx",
            {"repo_relative_path": "src/feature/CharactersTab.tsx"},
        ),
    ):
        payload = server._sqlite_file_inspection_payload(raw_dir, "src/feature/CharactersTab.tsx")
    assert payload is not None
    return payload


def _actionability(level: str, confidence: str) -> dict:
    return {
        "level": level,
        "score": 0.0,
        "policy": "dead_code_actionability_v1",
        "unusedness_confidence": confidence.lower(),
        "remediation_confidence": "unknown",
        "intent_decision_required": True,
        "mutation_proposed": False,
        "allowed_outcomes": ["delete", "complete_integration", "retain_contract", "unknown"],
        "why": "Graph evidence does not resolve intent.",
    }


def test_file_inspection_projects_high_and_medium_from_canonical_sqlite_dead_code(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    _create_inspection_db(
        raw_dir,
        {
            "meta": {"run_id": "run-21"},
            "items": [
                {
                    "project": "MAIN",
                    "file": "feature/CharactersTab.tsx",
                    "symbol": "orphaned",
                    "confidence": "HIGH",
                    "reason": "unreferenced_file_export",
                    "actionability": _actionability("manual_intent_decision", "HIGH"),
                },
                {
                    "project": "MAIN",
                    "file": "feature/CharactersTab.tsx",
                    "symbol": "partial",
                    "confidence": "MEDIUM",
                    "reason": "unconsumed_export_in_referenced_file",
                    "actionability": _actionability("review", "MEDIUM"),
                },
            ],
        },
    )
    payload = _inspect(raw_dir)

    assert payload["summary"]["dead_code_matches"] == 2
    assert payload["summary"]["audit_violations"] == 1
    assert payload["audit_violations"][0]["rule"] == "deep_imports"
    assert payload["summary"]["dead_code_matches_status"] == "AVAILABLE"
    assert [row["confidence"] for row in payload["dead_code_matches"]] == ["HIGH", "MEDIUM"]
    assert payload["dead_code_matches"][0]["actionability"]["remediation_confidence"] == "unknown"
    assert payload["meta"]["dead_code_artifact_identity"]["content_fingerprint"] == "sqlite:dead-code-sha"


def test_file_inspection_reports_unknown_when_dead_code_projection_is_missing(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    _create_inspection_db(raw_dir, None)
    payload = _inspect(raw_dir)

    assert payload["meta"]["evidence_status"] == "PARTIAL"
    assert payload["summary"]["dead_code_matches"] is None
    assert payload["summary"]["dead_code_matches_status"] == "UNKNOWN"
    assert "dead_code_matches: UNKNOWN" in inspect_target.render_agent_inspection_brief(payload)


def test_file_inspection_distinguishes_available_zero_from_unknown(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    _create_inspection_db(raw_dir, {"meta": {"run_id": "empty-run"}, "items": []})
    payload = _inspect(raw_dir)

    assert payload["meta"]["evidence_status"] == "COMPLETE"
    assert payload["summary"]["dead_code_matches"] == 0
    assert payload["summary"]["dead_code_matches_status"] == "AVAILABLE"
    brief = inspect_target.render_agent_inspection_brief(payload)
    assert "dead_code_matches: 0" in brief
    assert "dead_code_matches: UNKNOWN" not in brief

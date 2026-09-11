from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools.engines import temporal_diff


def test_corrupt_previous_snapshot_does_not_claim_first_run(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    raw = tmp_path / "raw"
    reports = tmp_path / "reports"
    snapshots.mkdir()
    raw.mkdir()
    reports.mkdir()
    (snapshots / "20260909_000000_summary.json").write_text(
        '{"generated_at": ',
        encoding="utf-8",
    )
    persisted: dict[str, dict] = {}

    def capture_json(path: Path, payload: dict) -> None:
        persisted[Path(path).name] = payload

    with (
        patch.object(temporal_diff, "SNAPSHOTS_DIR", snapshots),
        patch.object(temporal_diff, "RAW_DIR", raw),
        patch.object(temporal_diff, "REPORTS_DIR", reports),
        patch.object(
            temporal_diff,
            "_build_summary",
            return_value={"generated_at": "2026-09-09T00:00:01"},
        ),
        patch.object(temporal_diff, "_prune_old_snapshots", return_value=0),
        patch.object(temporal_diff, "save_json_atomic", side_effect=capture_json),
        patch.object(temporal_diff, "save_text_atomic"),
    ):
        temporal_diff.save_snapshot_and_diff()

    result = persisted["temporal_diff.json"]
    assert result["status"] == "previous_snapshot_unavailable"
    assert result["error_type"] == "JSONDecodeError"
    assert result["snapshot"] == "20260909_000000_summary.json"

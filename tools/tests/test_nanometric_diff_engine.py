from pathlib import Path
from unittest.mock import patch

from tools.core.json_io import load_json_file
from tools.engines import nanometric_diff_engine


def test_comparative_run_refreshes_canonical_index_report(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    reports_dir = tmp_path / "reports"
    raw_dir.mkdir()
    reports_dir.mkdir()
    genome = {
        "sharedSymbol": [
            {
                "project": "MAIN",
                "file": "src/shared.ts",
                "dna": "host-dna",
                "type": "Variable",
                "source_lines": "L1-L1",
            },
            {
                "project": "VARIANT",
                "file": "src/shared.ts",
                "dna": "variant-dna",
                "type": "Variable",
                "source_lines": "L1-L1",
            },
        ]
    }
    workspace_mode = {
        "mode": "comparative",
        "project_count": 2,
        "variant_projects": ["VARIANT"],
        "comparative_enabled": True,
    }

    with (
        patch.object(nanometric_diff_engine, "RAW_DIR", raw_dir),
        patch.object(nanometric_diff_engine, "REPORTS_DIR", reports_dir),
        patch.object(nanometric_diff_engine, "load_genome_data", return_value=genome),
        patch.object(nanometric_diff_engine, "get_workspace_mode", return_value=workspace_mode),
        patch("tools.core.config.DYNAMIC_CONFIG", {"project_roles": {"MAIN": "host", "VARIANT": "variant"}}),
    ):
        assert nanometric_diff_engine.run_nanometric_diff() is True

    raw_payload = load_json_file(raw_dir / "nanometric_diff.json", {})
    index = (reports_dir / "nanometric_diff.md").read_text(encoding="utf-8")
    assert list(raw_payload) == ["VARIANT"]
    assert "Comparison count: `1`" in index
    assert "nanometric_diff_MAIN_vs_VARIANT.md" in index
    assert "0 added, 0 removed, 1 modified files" in index
    assert (reports_dir / "nanometric_diff_MAIN_vs_VARIANT.md").exists()

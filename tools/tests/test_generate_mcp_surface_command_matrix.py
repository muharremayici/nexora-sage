from __future__ import annotations

from tools import generate_mcp_surface_command_matrix as matrix_generator
from tools import validate_mcp_surface_command_matrix as matrix_validator


def test_mcp_tool_inventory_includes_sync_and_async_tools(tmp_path, monkeypatch):
    server_path = tmp_path / "server.py"
    server_path.write_text(
        """
@mcp.tool()
def sync_tool():
    return None

@mcp.tool()
async def async_tool():
    return None

def helper():
    return None
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(matrix_generator, "SERVER_PATH", server_path)

    assert matrix_generator._mcp_tools() == {"sync_tool", "async_tool"}


def test_surface_matrix_rejects_unclassified_tool_family():
    issues = matrix_validator._row_checks(
        {
            "tool": "new_tool",
            "role": "primary_agent",
            "surface_ring": "H1_TARGET_REPO_AGENT",
            "surface_family": "unclassified",
            "review_obligations": ["no_sage_internal_noise", "manual_agent_surface_sample"],
            "quality_review": {},
        }
    )

    assert "tool_missing_surface_family" in issues


def test_primary_agent_manual_sample_obligation_requires_a_declared_alias():
    issues = matrix_validator._row_checks(
        {
            "tool": "new_primary_tool",
            "role": "primary_agent",
            "surface_ring": "H1_TARGET_REPO_AGENT",
            "surface_family": "target_inspection",
            "review_obligations": ["no_sage_internal_noise", "manual_agent_surface_sample"],
            "quality_review": {
                "sample_aliases": [],
                "present_samples": [],
                "sample_status": "NOT_REQUIRED",
            },
        }
    )

    assert "manual_agent_surface_sample_alias_missing" in issues


def test_structural_matrix_defers_executed_quality_without_reporting_missing_samples():
    status = matrix_generator._quality_status("inspect_file")

    assert status["sample_status"] == "DEFERRED_TO_AGENT_SURFACE_QUALITY_REVIEW"
    assert status["missing_samples"] == []


def test_matrix_validator_cli_main_emits_summary(monkeypatch, capsys):
    monkeypatch.setattr(
        matrix_validator,
        "build_validation",
        lambda: {"summary": {"status": "PASS"}, "matrix": {}},
    )
    monkeypatch.setattr(matrix_validator, "save_json_atomic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(matrix_validator, "save_text_atomic", lambda *_args, **_kwargs: None)

    assert matrix_validator.main() == 0
    assert '"status": "PASS"' in capsys.readouterr().out

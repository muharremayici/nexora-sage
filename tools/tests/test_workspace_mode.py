from __future__ import annotations

from unittest.mock import patch

from tools.core import workspace_mode


def test_unresolved_project_is_visible_but_not_authorized_as_host_merge_source():
    config = {
        "variations": {"MAIN": ".", "BACKEND": "backend"},
        "project_roles": {"MAIN": "host", "BACKEND": "unresolved"},
    }

    with patch.object(workspace_mode, "DYNAMIC_CONFIG", config):
        mode = workspace_mode.get_workspace_mode()
        allowed, reason = workspace_mode.is_source_allowed_for_host_merge("BACKEND", "MAIN")

    assert mode["mode"] == "multi_project_noncomparative"
    assert mode["comparative_enabled"] is False
    assert "BACKEND" not in mode["companion_projects"]
    assert "BACKEND" not in mode["variant_projects"]
    assert mode["unresolved_projects"] == ["BACKEND"]
    assert allowed is False
    assert reason == "source_relationship_unresolved"


def test_missing_non_main_role_fails_closed_as_unresolved():
    config = {
        "variations": {"MAIN": ".", "UNKNOWN_CHILD": "child"},
        "project_roles": {"MAIN": "host"},
    }

    with patch.object(workspace_mode, "DYNAMIC_CONFIG", config):
        mode = workspace_mode.get_workspace_mode()
        role = workspace_mode.get_project_role("UNKNOWN_CHILD")
        allowed, reason = workspace_mode.is_source_allowed_for_host_merge(
            "UNKNOWN_CHILD",
            "MAIN",
        )

    assert mode["unresolved_projects"] == ["UNKNOWN_CHILD"]
    assert mode["companion_projects"] == []
    assert role == "unresolved"
    assert allowed is False
    assert reason == "source_relationship_unresolved"

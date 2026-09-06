import pytest

from tools import validate_agent_semantic_contract_smoke as semantic

from tools.inspect_target import render_agent_inspection_brief
from tools.validate_agent_context_payloads import _all_project_symbol_scope_is_safe


def _payload(projects):
    return {
        "target": {"kind": "symbol", "value": "App"},
        "requested_project_scope": "all",
        "summary": {},
        "target_file_context": [
            {
                "project": project,
                "file": f"src/App{index}.tsx",
                "workspace_rel": f"packages/{project}/src/App{index}.tsx",
                "atlas_node": f"{project}::src/App{index}.tsx",
            }
            for index, project in enumerate(projects)
        ],
    }


@pytest.mark.parametrize("projects", [("MAIN",), ("MAIN", "MAIN"), ("LIBRARY", "CLIENT")])
def test_all_scope_accepts_actual_targets_without_named_variation_folder(projects):
    brief = render_agent_inspection_brief(_payload(projects))
    assert "Variations/" not in brief
    assert _all_project_symbol_scope_is_safe(brief, "App")
    if len(projects) > 1:
        assert "Do not edit yet;" in brief
        assert "Make the smallest code change" not in brief
        for index, project in enumerate(projects):
            assert f"{project}::packages/{project}/src/App{index}.tsx" in brief


def test_all_scope_rejects_wrong_scope_and_unsafe_multi_target_instructions():
    brief = render_agent_inspection_brief(_payload(("LIBRARY", "CLIENT")))
    assert not _all_project_symbol_scope_is_safe(brief.replace('project_scope: "all"', 'project_scope: "MAIN"'), "App")
    assert not _all_project_symbol_scope_is_safe(brief.replace("Do not edit yet;", "Edit now;"), "App")
    assert not _all_project_symbol_scope_is_safe(brief + "\nMake the smallest code change", "App")


def test_all_scope_rejects_missing_targets_and_wrong_query():
    payload = _payload(("MAIN",))
    payload["target_file_context"] = []
    assert not _all_project_symbol_scope_is_safe(render_agent_inspection_brief(payload), "App")
    brief = render_agent_inspection_brief(_payload(("MAIN",)))
    assert not _all_project_symbol_scope_is_safe(brief, "OtherSymbol")


@pytest.mark.parametrize("projects,ok", [(["MAIN"], True), (["MAIN", "LIBRARY"], True), (["LIBRARY"], False), ([""], False), ([], False)])
def test_semantic_search_scope_uses_actual_results_not_required_sibling_count(tmp_path, monkeypatch, projects, ok):
    source = tmp_path / "src" / "main.tsx"
    source.parent.mkdir()
    source.write_text("export const main = 1;", encoding="utf-8")
    payloads = {
        "search_symbols:main:MAIN:json": [{"project": "MAIN", "file": "src/main.tsx"}],
        "search_symbols:AppLayout:default:json": [{"project": "MAIN"}],
        "search_symbols:AppLayout:all:json": [{"project": project} for project in projects],
        "inspect_file:MAIN::src/main.tsx:json": {
            "analysis_root": str(tmp_path), "target_file_context": [{"file": "src/main.tsx"}],
        },
        "inspect_symbol:main:json": {"atlas_symbols": [{"file": "src/main.tsx"}]},
    }
    monkeypatch.setattr(semantic, "_cached_mcp_json", lambda key, _producer: payloads[key])
    report = semantic._search_and_inspection_grounding_report()
    assert report["ok"] is ok
    assert report["all_search_projects"] == sorted(projects)

from __future__ import annotations

import ast
import json
from pathlib import Path

from tools.mcp import capability_tools


def _runtime(tmp_path: Path, payload: object | None = None):
    raw_dir = tmp_path / "raw"
    reports_dir = tmp_path / "reports"
    raw_dir.mkdir()
    reports_dir.mkdir()
    calls: list[tuple[str, ...]] = []

    def load_json(_path: Path):
        return payload

    def read_json(path: Path, missing: str) -> str:
        return f"json:{path.name}:{missing}"

    def read_text(path: Path, missing: str) -> str:
        return f"text:{path.name}:{missing}"

    def run_cli(*args: str) -> str:
        calls.append(tuple(args))
        return "ok"

    return (
        capability_tools.CapabilityToolRuntime(
            raw_dir=raw_dir,
            reports_dir=reports_dir,
            load_json=load_json,
            read_json_artifact=read_json,
            read_text_artifact=read_text,
            run_cli=run_cli,
        ),
        calls,
    )


def test_capability_artifact_handlers_preserve_refresh_and_format_routes(monkeypatch, tmp_path):
    runtime, _calls = _runtime(tmp_path)
    invoked: list[str] = []
    monkeypatch.setattr(capability_tools, "run_surface_inventory", lambda: invoked.append("surface"))
    monkeypatch.setattr(capability_tools, "run_capability_registry_report", lambda: invoked.append("registry"))
    monkeypatch.setattr(capability_tools, "run_capability_activation_plan", lambda: invoked.append("activation"))
    monkeypatch.setattr(
        capability_tools,
        "validate_pipeline_execution_contract",
        lambda: invoked.append("pipeline"),
    )
    monkeypatch.setattr(
        capability_tools,
        "validate_engine_signal_contracts",
        lambda: invoked.append("signals"),
    )

    assert capability_tools.get_surface_inventory(runtime, regenerate=True).startswith("json:nexora_surface_inventory.json:")
    assert capability_tools.get_capability_registry(runtime).startswith("json:capability_registry.json:")
    assert capability_tools.get_capability_activation_plan(runtime, refresh=True, human_report=True).startswith(
        "text:capability_activation_plan.md:"
    )
    assert capability_tools.get_pipeline_execution_contract(runtime, refresh=True).startswith(
        "json:pipeline_execution_contract_validation.json:"
    )
    assert capability_tools.get_engine_signal_contracts(runtime, refresh=True).startswith(
        "json:engine_signal_contract_validation.json:"
    )
    assert capability_tools.get_artifact_provenance(runtime, human_report=True).startswith(
        "text:artifact_provenance_index.md:"
    )
    assert invoked == ["surface", "registry", "activation", "pipeline", "signals"]


def test_capability_contract_preserves_found_missing_artifact_and_overview_lanes(monkeypatch):
    registry = {"capabilities": [{"id": "cap-a"}]}
    capability = {
        "id": "cap-a",
        "artifacts": ["output/.raw/a.json"],
        "validators": ["tools/validate_a.py"],
        "claim_boundary": "bounded",
        "language_scope": ["typescript"],
        "framework_scope": ["react"],
    }
    monkeypatch.setattr(capability_tools, "load_capability_registry", lambda: registry)
    monkeypatch.setattr(
        capability_tools,
        "get_capability",
        lambda _registry, capability_id: capability if capability_id == "cap-a" else None,
    )
    monkeypatch.setattr(
        capability_tools,
        "capabilities_for_artifact",
        lambda _registry, artifact: [capability] if artifact == "output/.raw/a.json" else [],
    )
    monkeypatch.setattr(
        capability_tools,
        "build_agent_capability_map",
        lambda _registry: {"summary": {"capabilities": 1}, "capabilities": [capability]},
    )

    found = json.loads(capability_tools.get_capability_contract(capability_id="cap-a"))
    missing = json.loads(capability_tools.get_capability_contract(capability_id="missing"))
    artifact = json.loads(capability_tools.get_capability_contract(artifact="output/.raw/a.json"))
    overview = json.loads(capability_tools.get_capability_contract())

    assert found["status"] == "found"
    assert found["agent_guidance"]["claim_boundary"] == "bounded"
    assert missing == {
        "status": "not_found",
        "capability_id": "missing",
        "available_capabilities": {"capabilities": 1},
    }
    assert artifact["status"] == "found"
    assert artifact["agent_guidance"] == [
        {
            "capability_id": "cap-a",
            "validators_to_run": ["tools/validate_a.py"],
            "claim_boundary": "bounded",
        }
    ]
    assert overview["summary"] == {"capabilities": 1}


def test_pipeline_step_invocation_preserves_list_match_and_not_found_semantics(tmp_path):
    payload = {
        "steps": [
            {
                "name": "Quality Gates",
                "slug": "qualitygates",
                "category": "quality",
                "depends_on": ["atlas"],
                "heavy": True,
                "full_only": False,
                "invocation_contract": {"argv": ["sage.py", "run", "--step", "qualitygates"]},
                "execution_contract": {
                    "scheduler_class": "sequential_required",
                    "parallel_safe_after_dependencies": False,
                    "sqlite_writer": True,
                    "reasons": ["canonical writer"],
                },
            },
            {"name": "Quality Summary", "slug": "qualitysummary"},
        ]
    }
    runtime, calls = _runtime(tmp_path, payload)

    listed = json.loads(capability_tools.get_pipeline_step_invocation(runtime, refresh=True))
    found = json.loads(capability_tools.get_pipeline_step_invocation(runtime, step="Quality Gates"))
    missing = json.loads(capability_tools.get_pipeline_step_invocation(runtime, step="quality"))

    assert calls == [("run", "--list-steps")] * 3
    assert listed["status"] == "list_steps"
    assert listed["available_steps"][0]["slug"] == "qualitygates"
    assert found["status"] == "found"
    assert found["invocation"]["argv"][-1] == "qualitygates"
    assert found["execution"]["scheduler_class"] == "sequential_required"
    assert missing["status"] == "not_found"
    assert [row["slug"] for row in missing["suggestions"]] == ["qualitygates", "qualitysummary"]


def test_server_keeps_decorated_public_wrappers_and_delegates_runtime(monkeypatch):
    from tools.mcp import server

    names = {
        "get_surface_inventory",
        "get_capability_registry",
        "get_capability_contract",
        "get_capability_activation_plan",
        "get_pipeline_execution_contract",
        "get_pipeline_step_invocation",
        "get_engine_signal_contracts",
        "get_artifact_provenance",
    }
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    wrappers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    }
    assert set(wrappers) == names
    assert all(
        any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == "mcp"
            and decorator.func.attr == "tool"
            for decorator in node.decorator_list
        )
        for node in wrappers.values()
    )

    captured = {}

    def handler(runtime, *, step, refresh):
        captured.update(runtime=runtime, step=step, refresh=refresh)
        return "delegated"

    monkeypatch.setattr(server.capability_tool_handlers, "get_pipeline_step_invocation", handler)
    assert server.get_pipeline_step_invocation("qualitygates", True) == "delegated"
    assert captured["runtime"].raw_dir == server.RAW_DIR
    assert captured["step"] == "qualitygates"
    assert captured["refresh"] is True

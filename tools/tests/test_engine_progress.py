from unittest.mock import patch
from pathlib import Path

from tools.core.engine_progress import EngineProgress
from tools.core.pipeline_registry import load_pipeline_execution_policy
from tools.engines import react_ecosystem_analyzer


def test_engine_progress_is_bounded_and_phase_aware(caplog):
    contract = {
        "enabled": True,
        "progress_every_items": 2,
    }
    with patch("tools.core.engine_progress.engine_progress_contract", return_value=contract):
        progress = EngineProgress("example_engine")
        progress.start()
        progress.phase("source_scan", total=4)
        progress.advance(1, current_project="MAIN")
        progress.advance(2, current_project="MAIN")
        progress.complete("PASS", files=4)

    output = caplog.text
    assert "engine=example_engine state=START" in output
    assert "state=PHASE phase=source_scan total=4" in output
    assert "state=PROGRESS phase=source_scan completed=2 total=4" in output
    assert "completed=1" not in output
    assert "engine=example_engine state=PASS" in output


def test_p1_long_running_engines_use_the_central_progress_contract():
    policy = load_pipeline_execution_policy()
    configured = policy.get("engine_progress", {})
    assert configured.get("default", {}).get("progress_every_items") == 1000

    root = Path(__file__).resolve().parents[2]
    for source_name in (
        "dead_code_detector.py",
        "framework_route_analyzer.py",
        "react_ecosystem_analyzer.py",
        "react_frontier_intelligence.py",
    ):
        source = (root / "tools" / "engines" / source_name).read_text(encoding="utf-8")
        assert "from tools.core.engine_progress import EngineProgress" in source
        assert "EngineProgress(" in source


def test_react_ecosystem_analyzer_emits_bounded_progress_through_shared_contract(monkeypatch):
    events = []

    class RecordingProgress:
        def __init__(self, engine_id):
            events.append(("init", engine_id, {}))

        def start(self):
            events.append(("start", "", {}))

        def phase(self, name, **details):
            events.append(("phase", name, details))

        def advance(self, completed, **details):
            events.append(("advance", completed, details))

        def checkpoint(self, name, **details):
            events.append(("checkpoint", name, details))

        def complete(self, status, **details):
            events.append(("complete", status, details))

    monkeypatch.setattr(react_ecosystem_analyzer, "EngineProgress", RecordingProgress)
    monkeypatch.setattr(
        react_ecosystem_analyzer,
        "load_atlas_data",
        lambda: {"MAIN": {"files": {"src/App.tsx": {}}}},
    )
    monkeypatch.setattr(
        react_ecosystem_analyzer,
        "_read_project_file",
        lambda *args, **kwargs: "export const App = () => <main />;",
    )
    monkeypatch.setattr(react_ecosystem_analyzer, "save_json_atomic", lambda *args, **kwargs: None)
    monkeypatch.setattr(react_ecosystem_analyzer, "save_text_atomic", lambda *args, **kwargs: None)
    monkeypatch.setattr(react_ecosystem_analyzer, "report_surface_limit", lambda *_args: 20)

    payload = react_ecosystem_analyzer.run_react_ecosystem_analyzer()

    assert payload["summary"]["files_analyzed"] == 1
    assert ("init", "react_ecosystem_analyzer", {}) in events
    assert any(event[0] == "phase" and event[1] == "project_scan" for event in events)
    assert any(event[0] == "advance" and event[2].get("current_file") == "src/App.tsx" for event in events)
    assert any(event[0] == "phase" and event[1] == "artifact_write" for event in events)
    assert any(event[0] == "complete" and event[1] == "PASS" for event in events)

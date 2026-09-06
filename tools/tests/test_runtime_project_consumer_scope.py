from __future__ import annotations

import builtins
from pathlib import Path
import re
from unittest.mock import patch

from tools.core.artifact_store import _source_snapshot_log
from tools.core.runtime_project_scope import (
    canonical_project_keys,
    get_runtime_project_filter,
    project_runtime_atlas,
)
from tools.engines import react_frontier_intelligence
from tools.engines import dead_code_detector
from tools.engines.circular_dependency_finder import CircularDependencyFinder
from tools.orchestrators import orchestrator
from tools.validate_react_support import _log


def _atlas() -> dict:
    return {
        "MAIN": {
            "files": {"src/main.ts": {}},
            "dependencies": {
                "src/main.ts": [
                    "VARIANT::src/variant.ts",
                ]
            },
        },
        "VARIANT": {
            "files": {"src/variant.ts": {}},
            "dependencies": {"src/variant.ts": ["src/variant.ts"]},
        },
    }


def test_persisted_projects_do_not_expand_runtime_execution_scope(monkeypatch) -> None:
    monkeypatch.setattr("tools.core.runtime_project_scope._RUNTIME_PROJECT_FILTER", ["MAIN"])

    projected, scope = project_runtime_atlas(_atlas())

    assert list(projected) == ["MAIN"]
    assert scope["requested_projects"] == ["MAIN"]
    assert scope["analyzed_projects"] == ["MAIN"]
    assert scope["preserved_only_projects"] == ["VARIANT"]
    assert scope["target_decision_eligible"] is True
    assert canonical_project_keys(_atlas()) == {"MAIN", "VARIANT"}


def test_runtime_project_filter_does_not_leak_between_in_process_runs(monkeypatch) -> None:
    import tools.core.config as runtime_config

    monkeypatch.setattr(orchestrator, "_PROJECT_FILTER", None)
    monkeypatch.setattr(runtime_config, "PROJECT_FILTER", None)
    monkeypatch.setattr("tools.core.runtime_project_scope._RUNTIME_PROJECT_FILTER", None)

    assert orchestrator._apply_runtime_project_filter("main, variant") == ["MAIN", "VARIANT"]
    assert project_runtime_atlas(_atlas())[1]["requested_projects"] == ["MAIN", "VARIANT"]

    assert orchestrator._apply_runtime_project_filter(None) is None
    assert orchestrator._PROJECT_FILTER is None
    assert runtime_config.PROJECT_FILTER is None
    assert get_runtime_project_filter() is None
    assert project_runtime_atlas(_atlas())[1]["requested_projects"] == ["MAIN", "VARIANT"]


def test_circular_consumer_projects_persisted_atlas_before_analysis(monkeypatch) -> None:
    monkeypatch.setattr("tools.core.runtime_project_scope._RUNTIME_PROJECT_FILTER", ["MAIN"])
    monkeypatch.setattr(
        "tools.engines.circular_dependency_finder.load_atlas_data",
        _atlas,
    )

    graph, files, _self_edges, _canonical_atlas, scope = CircularDependencyFinder()._build_import_graph()

    assert graph == {}
    assert files == {"MAIN::src/main.ts"}
    assert scope["analyzed_projects"] == ["MAIN"]
    assert scope["preserved_only_projects"] == ["VARIANT"]
    assert scope["excluded_cross_project_edges"] == 1


def test_react_ts_collector_receives_runtime_projects(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(react_frontier_intelligence, "TS_COLLECTOR", tmp_path / "collector.cjs")
    react_frontier_intelligence.TS_COLLECTOR.write_text("", encoding="utf-8")
    observed: dict[str, list[str]] = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **_kwargs):
        observed["command"] = command
        return Result(), 0.01

    monkeypatch.setattr(react_frontier_intelligence, "run_observed_subprocess", fake_run)
    monkeypatch.setattr(react_frontier_intelligence, "load_json_file", lambda *_args, **_kwargs: {"summary": {}})
    monkeypatch.setattr(react_frontier_intelligence, "save_json_atomic", lambda *_args, **_kwargs: None)

    react_frontier_intelligence._collect_ts_diagnostics(projects=["MAIN"])

    assert observed["command"][-2:] == ["--projects", "MAIN"]


def test_progress_output_loss_does_not_fail_react_validation() -> None:
    with patch.object(builtins, "print", side_effect=OSError(22, "Invalid argument")):
        _log("still-running")


def test_progress_output_loss_does_not_fail_source_snapshot_projection() -> None:
    with patch.object(builtins, "print", side_effect=OSError(22, "Invalid argument")):
        _source_snapshot_log("still-running")


def test_dead_code_progress_uses_execution_project_count() -> None:
    source = Path(dead_code_detector.__file__).read_text(encoding="utf-8")

    assert 'analyzed_project_count = len(atlas)' in source
    assert 'total=analyzed_project_count' in source
    assert 'preserved_projects=preserved_project_count' in source
    assert 'total=len(valid_projects)' not in source


def test_every_direct_atlas_consumer_declares_runtime_scope_authority() -> None:
    engines_dir = Path(__file__).resolve().parents[1] / "engines"
    direct_traversal = re.compile(r"for\s+.+\s+in\s+.+atlas\.items\(\)")
    bespoke_scope_guards = {
        "audit.py": "runtime_audit_project_scope",
        "generate_atlas.py": "PROJECT_FILTER",
        "nuclear_processor.py": "resolve_runtime_projects",
        "ui_runtime_contract_analyzer.py": "resolve_runtime_projects",
    }
    missing: list[str] = []

    for path in sorted(engines_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not direct_traversal.search(source):
            continue
        if "project_runtime_atlas" in source:
            continue
        required_marker = bespoke_scope_guards.get(path.name)
        if required_marker and required_marker in source:
            continue
        missing.append(path.name)

    assert missing == []


def test_source_snapshot_validator_is_read_only_and_runtime_scoped() -> None:
    validator = Path(__file__).resolve().parents[1] / "validate_source_snapshot_store.py"
    source = validator.read_text(encoding="utf-8")

    assert "project_runtime_atlas" in source
    assert '"--projects"' in source
    assert "refresh_atlas_projection" not in source

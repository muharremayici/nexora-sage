import argparse
from pathlib import Path

import pytest

import codemaps
from tools.core import bootstrap_env
from tools.core import config as core_config
from tools import external_target_preflight

from tools.core.installation_preflight import (
    build_installation_plan,
    installation_feature_dependencies,
)


def _target(*, languages=None, react=False, status="PASS", exists=True):
    return {
        "target": {
            "root": "C:/fixture",
            "exists": exists,
            "is_dir": exists,
        },
        "summary": {
            "status": status,
            "attention_reasons": [],
            "language_counts": languages or {},
            "react_signal": react,
            "dependency_evidence": {
                "javascript_node": {"status": "present" if languages else "not_observed"}
            },
            "analysis_authority": {},
        },
    }


def _machine(*, node=True, npm=True, git=True, features=True):
    feature_states = {
        name: {
            "available": features,
            "required_by_profile": True,
            "package": package,
        }
        for name, (_module, package, _attribute) in installation_feature_dependencies().items()
    }
    return {
        "python": {
            "available": True,
            "version": "3.11.9",
            "minor": "3.11",
            "requirement": ">=3.11",
            "requirement_satisfied": True,
            "release_tested": True,
            "tested_versions": ["3.11", "3.12", "3.13", "3.14"],
        },
        "pip": {"available": True, "path": "python -m pip"},
        "node": {"available": node, "path": "node" if node else None, "version": "v20.0.0" if node else None},
        "npm": {"available": npm, "path": "npm" if npm else None},
        "git": {"available": git, "path": "git" if git else None},
        "python_features": feature_states,
    }


def test_python_only_target_does_not_require_node_or_npm():
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(languages={"python": 3}),
        machine=_machine(node=False, npm=False),
        bundled_typescript_available=False,
    )

    assert plan["summary"]["status"] == "READY"
    assert plan["summary"]["node_required_for_target"] is False
    assert not plan["actions"]
    assert not plan["blockers"]


def test_react_target_without_node_fails_closed_with_explicit_remediation():
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(languages={"typescript": 4}, react=True),
        machine=_machine(node=False, npm=False),
        bundled_typescript_available=False,
    )

    assert plan["summary"]["status"] == "BLOCKED"
    assert plan["summary"]["node_required_for_target"] is True
    assert {row["id"] for row in plan["blockers"]} == {"node_runtime_required_for_target_ast"}
    assert plan["mutation_boundary"]["system_runtime_auto_install_allowed"] is False


def test_node_outside_release_reference_is_attention_not_compatibility_failure():
    machine = _machine()
    machine["node"]["version"] = "v24.18.0"
    plan = build_installation_plan(
        ".",
        target_preflight=_target(languages={"typescript": 3}, react=True),
        machine=machine,
        bundled_typescript_available=True,
    )

    assert plan["summary"]["status"] == "ATTENTION"
    assert {row["id"] for row in plan["attention"]} == {
        "node_runtime_outside_release_reference"
    }
    assert plan["blockers"] == []


def test_typescript_target_plans_only_sage_local_npm_install_when_runtime_is_absent():
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(languages={"typescript": 4}),
        machine=_machine(),
        bundled_typescript_available=False,
    )

    assert plan["summary"]["status"] == "READY_WITH_INSTALL_ACTIONS"
    assert [row["id"] for row in plan["actions"]] == ["install_sage_node_ast_dependencies"]
    assert plan["actions"][0]["target_repository_mutation"] is False


def test_missing_default_human_and_ai_features_produce_one_python_install_action():
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(languages={"python": 2}),
        machine=_machine(features=False),
    )

    assert plan["summary"]["status"] == "READY_WITH_INSTALL_ACTIONS"
    assert [row["id"] for row in plan["actions"]] == ["install_sage_python_dependencies"]
    assert set(plan["actions"][0]["missing_features"]) == set(installation_feature_dependencies())


def test_unsupported_python_runtime_blocks_without_automatic_system_install():
    machine = _machine()
    machine["python"].update({
        "version": "3.10.14",
        "minor": "3.10",
        "requirement_satisfied": False,
        "release_tested": False,
    })
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(languages={"python": 2}),
        machine=machine,
    )

    blocker = next(row for row in plan["blockers"] if row["id"] == "supported_python_runtime_required")
    assert plan["summary"]["status"] == "BLOCKED"
    assert blocker["automatic_install"] is False
    assert plan["mutation_boundary"]["system_runtime_auto_install_allowed"] is False


def test_missing_pip_blocks_when_default_profile_dependencies_are_missing():
    machine = _machine(features=False)
    machine["pip"]["available"] = False
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(languages={"python": 2}),
        machine=machine,
    )

    assert plan["summary"]["status"] == "BLOCKED"
    assert {row["id"] for row in plan["blockers"]} == {
        "pip_required_for_sage_dependencies"
    }
    assert plan["actions"] == []


def test_skip_deps_with_missing_required_features_fails_closed():
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(languages={"python": 2}),
        machine=_machine(features=False),
        dependency_install_enabled=False,
    )

    assert plan["summary"]["status"] == "BLOCKED"
    assert "required_dependencies_missing_while_install_disabled" in {
        row["id"] for row in plan["blockers"]
    }


def test_missing_git_is_attention_after_source_and_target_are_present():
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(languages={"python": 2}),
        machine=_machine(git=False),
    )

    assert plan["summary"]["status"] == "ATTENTION"
    assert {row["id"] for row in plan["attention"]} == {"git_unavailable_for_source_acquisition"}


def test_unusable_target_blocks_before_installation():
    plan = build_installation_plan(
        Path("C:/fixture"),
        target_preflight=_target(status="FAIL", exists=False),
        machine=_machine(),
    )

    assert plan["summary"]["status"] == "BLOCKED"
    assert "target_preflight_not_usable" in {row["id"] for row in plan["blockers"]}


def test_default_profile_includes_watchdog_and_mcp():
    dependencies = installation_feature_dependencies()

    assert "watch-live" in dependencies
    assert "mcp-runtime" in dependencies


def test_embedded_sage_root_is_excluded_from_target_language_inventory(monkeypatch, tmp_path):
    target = tmp_path / "target"
    sage_root = target / "Kurulum-Özel-7f3"
    (target / "src" / "app").mkdir(parents=True)
    (sage_root / "tools").mkdir(parents=True)
    (target / "package.json").write_text(
        '{"dependencies":{"react":"19.0.0"},"workspaces":["*"]}',
        encoding="utf-8",
    )
    (target / "src" / "app" / "layout.tsx").write_text(
        "export default function Layout() { return null; }\n",
        encoding="utf-8",
    )
    (sage_root / "sage.py").write_text("print('tool')\n", encoding="utf-8")
    (sage_root / "tools" / "worker.py").write_text("print('tool')\n", encoding="utf-8")
    (sage_root / "package.json").write_text(
        '{"dependencies":{"zustand":"5.0.0"}}',
        encoding="utf-8",
    )
    topology_builder = external_target_preflight.external_target_repository_topology

    def topology_with_selected_sage(root, scope_projection):
        topology = topology_builder(root, scope_projection)
        topology["selected_projects"]["SAGE_COMPANION"] = "Kurulum-Özel-7f3"
        return topology

    monkeypatch.setattr(external_target_preflight, "CODE_MAPS_DIR", sage_root)
    monkeypatch.setattr(
        external_target_preflight,
        "external_target_repository_topology",
        topology_with_selected_sage,
    )
    monkeypatch.setattr(core_config, "CODE_MAPS_DIR", sage_root)

    payload = external_target_preflight.build_preflight(target)
    summary = payload["summary"]

    assert summary["language_counts"] == {"typescript": 1}
    assert summary["repository_language_counts"] == {"typescript": 1}
    assert summary["inventory_evidence"]["excluded_embedded_sage_roots"] == ["Kurulum-Özel-7f3"]
    assert "Kurulum-Özel-7f3" in summary["analysis_scope"]["project_ownership_exclusions"]["MAIN"]
    assert "Kurulum-Özel-7f3/package.json" not in summary["dependency_evidence"]["javascript_node"]["evaluated_manifest_files"]
    assert summary["dependency_evidence"]["javascript_node"]["workspace_all_declared_dependency_name_count"] == 0


def test_legacy_product_named_directory_is_not_excluded_without_runtime_identity(tmp_path):
    target = tmp_path / "target"
    legacy_named_directory = target / "Legacy Product Folder"
    legacy_named_directory.mkdir(parents=True)
    (target / "service.ts").write_text("export const service = true;\n", encoding="utf-8")
    (legacy_named_directory / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")

    payload = external_target_preflight.build_preflight(target)

    assert payload["summary"]["repository_language_counts"] == {
        "python": 1,
        "typescript": 1,
    }
    assert payload["summary"]["inventory_evidence"]["excluded_embedded_sage_roots"] == []
    assert payload["summary"]["inventory_evidence"]["installation_root_exclusion"] == {
        "mode": "resolved_runtime_installation_root",
        "directory_name_matching": False,
        "self_target_behavior": "include",
    }


def test_sage_self_target_is_not_excluded(monkeypatch, tmp_path):
    sage_root = tmp_path / "renamed-runtime-root"
    sage_root.mkdir()
    (sage_root / "sage.py").write_text("print('self')\n", encoding="utf-8")
    monkeypatch.setattr(external_target_preflight, "CODE_MAPS_DIR", sage_root)
    monkeypatch.setattr(core_config, "CODE_MAPS_DIR", sage_root)

    payload = external_target_preflight.build_preflight(sage_root)

    assert payload["summary"]["repository_language_counts"] == {"python": 1}
    assert payload["summary"]["inventory_evidence"]["excluded_embedded_sage_roots"] == []
    assert payload["summary"]["analysis_scope"]["project_ownership_exclusions"]["MAIN"] == []

def test_public_plan_only_forwards_target_without_doctor_or_analysis(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(codemaps, "PUBLIC_DISTRIBUTION_MANIFEST", tmp_path / "absent.json")
    monkeypatch.setattr(
        codemaps,
        "cmd_doctor",
        lambda _args: (_ for _ in ()).throw(AssertionError("doctor must not run")),
    )
    monkeypatch.setattr(codemaps, "run_command", lambda command, **_kwargs: commands.append(command) or 0)

    result = codemaps.cmd_init(
        argparse.Namespace(
            setup_only=False,
            full=False,
            plan_only=True,
            skip_deps=False,
            target_root=str(tmp_path),
        )
    )

    assert result == 0
    assert commands == [[
        "python",
        str(codemaps.BOOTSTRAP_SCRIPT),
        "--mode",
        "preflight",
        "--plan-only",
        "--target-root",
        str(tmp_path),
    ]]


def test_public_distribution_hides_maintainer_only_cli_surface(monkeypatch, tmp_path):
    manifest = tmp_path / "PUBLIC_DISTRIBUTION_MANIFEST.json"
    manifest.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(codemaps, "PUBLIC_DISTRIBUTION_MANIFEST", manifest)

    parser = codemaps.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    public_commands = set(subparsers.choices)
    maintainer_commands = {
        "release-check",
        "self-audit",
        "work-package",
        "ledger-update",
        "fixture-import",
        "fixture-seed",
        "fixture-promote",
        "universal-proof-run",
        "phase-status",
        "transcript-template",
        "truth-sync",
        "ci-check",
    }

    assert not (public_commands & maintainer_commands)
    assert {"init", "run", "watch", "doctor", "mcp", "backup", "restore"} <= public_commands
    assert "release-check" not in parser.format_help()
    assert "python sage.py init --target-root <repository>" in parser.epilog
    assert "python sage.py run --target-root <repository> --profile daily" in parser.epilog
    assert "python sage.py run --full" not in parser.epilog


def test_progress_wrapper_records_exact_target_preflight_duration(monkeypatch):
    recorded = []
    expected = {"summary": {"status": "READY"}}
    monkeypatch.setattr(
        bootstrap_env,
        "heartbeat_cadence_selection",
        lambda _scope: {"interval_seconds": 30, "basis": "test"},
    )
    monkeypatch.setattr(
        bootstrap_env,
        "local_duration_guidance",
        lambda _guidance: {
            "basis": "insufficient_exact_phase_samples",
            "phases": {"canonical_target_preflight": {"status": "unavailable", "sample_count": 0}},
        },
    )
    monkeypatch.setattr(bootstrap_env, "build_installation_plan", lambda *_args, **_kwargs: expected)
    monkeypatch.setattr(
        bootstrap_env,
        "record_execution_duration",
        lambda trace_type, identifier, duration: recorded.append((trace_type, identifier, duration)),
    )

    result = bootstrap_env._build_installation_plan_with_progress(
        Path("C:/fixture"), dependency_install_enabled=True
    )

    assert result is expected
    assert recorded[0][0:2] == (
        "subprocess_execution",
        "installation canonical target preflight",
    )


def test_failed_progress_sample_uses_non_authoritative_duration_identifier(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        bootstrap_env,
        "heartbeat_cadence_selection",
        lambda _scope: {"interval_seconds": 30, "basis": "test"},
    )
    monkeypatch.setattr(
        bootstrap_env,
        "local_duration_guidance",
        lambda _guidance: {
            "basis": "insufficient_exact_phase_samples",
            "phases": {"canonical_target_preflight": {"status": "unavailable", "sample_count": 0}},
        },
    )
    monkeypatch.setattr(
        bootstrap_env,
        "build_installation_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    monkeypatch.setattr(
        bootstrap_env,
        "record_execution_duration",
        lambda trace_type, identifier, duration: recorded.append((trace_type, identifier, duration)),
    )

    with pytest.raises(RuntimeError, match="synthetic failure"):
        bootstrap_env._build_installation_plan_with_progress(
            Path("C:/fixture"), dependency_install_enabled=True
        )

    assert recorded[0][0:2] == (
        "subprocess_execution",
        "installation canonical target preflight failed",
    )


def test_post_install_verification_reuses_frozen_target_profile(monkeypatch):
    calls = []
    target = {"root": "C:/fixture", "exists": True, "is_dir": True, "preflight_status": "PASS"}
    monkeypatch.setattr(bootstrap_env, "install_deps", lambda _plan: None)
    monkeypatch.setattr(
        bootstrap_env,
        "build_installation_plan",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {
            "summary": {"status": "READY"},
            "actions": [],
        },
    )
    monkeypatch.setattr(bootstrap_env, "write_installation_plan", lambda _plan: None)
    monkeypatch.setattr(bootstrap_env, "render_console_lines", lambda _plan: [])

    assert bootstrap_env._install_and_verify({"actions": [], "target": target}, Path("C:/fixture"))
    assert calls[0][1]["target_profile"] is target

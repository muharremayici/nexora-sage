import argparse
from pathlib import Path

import pytest

import codemaps
from tools.core import bootstrap_env
from tools.core import config as core_config
from tools.core import installation_preflight
from tools import external_target_preflight

from tools.core.installation_preflight import (
    build_embedded_host_footprint,
    build_installation_plan,
    installation_feature_dependencies,
    load_installation_preflight_contract,
    node_ast_cache_state,
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
    action = plan["actions"][0]
    assert action["target_repository_mutation"] is False
    assert action["dependency_authority"] == "sage_runtime"
    assert action["operation_class"] == "network_package_acquisition"
    assert plan["summary"]["target_native_install_action_count"] == 0
    assert plan["mutation_boundary"]["target_native_dependency_auto_install_allowed"] is False


def test_node_ast_cache_binds_manifest_lock_and_installed_version(tmp_path):
    engines = tmp_path / "tools" / "engines"
    marker = engines / "node_modules" / "typescript" / "package.json"
    marker.parent.mkdir(parents=True)
    (engines / "package.json").write_text(
        '{"dependencies":{"typescript":"5.9.3"}}',
        encoding="utf-8",
    )
    (engines / "package-lock.json").write_text(
        '{"packages":{"node_modules/typescript":{"version":"5.9.3"}}}',
        encoding="utf-8",
    )
    marker.write_text('{"version":"5.9.3"}', encoding="utf-8")
    state = node_ast_cache_state(
        {
            "package_manifest": "tools/engines/package.json",
            "bundled_typescript_marker": (
                "tools/engines/node_modules/typescript/package.json"
            ),
        },
        root=tmp_path,
    )

    assert state["status"] == "VERIFIED"
    assert state["content_identity"].startswith("sha256:")
    assert state["errors"] == []


def test_node_ast_cache_rejects_installed_version_mismatch(tmp_path):
    engines = tmp_path / "tools" / "engines"
    marker = engines / "node_modules" / "typescript" / "package.json"
    marker.parent.mkdir(parents=True)
    (engines / "package.json").write_text(
        '{"dependencies":{"typescript":"5.9.3"}}',
        encoding="utf-8",
    )
    (engines / "package-lock.json").write_text(
        '{"packages":{"node_modules/typescript":{"version":"5.9.3"}}}',
        encoding="utf-8",
    )
    marker.write_text('{"version":"5.8.0"}', encoding="utf-8")
    state = node_ast_cache_state(
        {
            "package_manifest": "tools/engines/package.json",
            "bundled_typescript_marker": (
                "tools/engines/node_modules/typescript/package.json"
            ),
        },
        root=tmp_path,
    )

    assert state["status"] == "REINSTALL_REQUIRED"
    assert "installed_version_mismatch" in state["errors"]


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
    assert plan["blockers"][0]["outcome_class"] == "MISSING_MANAGER"
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


def test_embedded_installation_emits_one_non_mutating_root_exclusion(tmp_path):
    target = tmp_path / "host"
    installation = target / "Renamed SAGE Runtime"
    (installation / ".pytest_cache").mkdir(parents=True)
    footprint = build_embedded_host_footprint(
        target,
        installation,
        contract=load_installation_preflight_contract(),
        target_profile={
            "target_policy": {
                "summary": {
                    "declared_tools": ["typescript", "eslint", "jest", "unknown-tool"]
                }
            }
        },
    )

    assert footprint["installation_mode"] == "embedded"
    assert footprint["applicability"] == "operator_review_required"
    assert footprint["embedded_root"] == "Renamed SAGE Runtime"
    assert footprint["root_exclusion"] == {
        "directory": "Renamed SAGE Runtime/",
        "recursive_glob": "Renamed SAGE Runtime/**",
    }
    assert footprint["detected_host_tools"] == ["eslint", "jest", "typescript"]
    assert {row["id"] for row in footprint["host_tool_guidance"]} == {
        "eslint",
        "jest",
        "typescript",
        "search",
        "packaging",
    }
    assert all(
        row["recommended_exclusion"] == footprint["root_exclusion"]
        and row["target_mutation_performed"] is False
        for row in footprint["host_tool_guidance"]
    )
    assert footprint["target_configuration_mutation"] == {
        "allowed": False,
        "performed": False,
    }
    cache = next(
        row for row in footprint["observed_surfaces"] if row["path"] == ".pytest_cache"
    )
    assert cache["host_access"]["status"] == "traversable"


@pytest.mark.parametrize(
    ("target_name", "installation_name", "expected_mode", "expected_applicability"),
    [
        ("target", "target", "self_target", "not_applicable_sage_self_analysis"),
        (
            "target",
            "central-runtime",
            "central_external",
            "not_applicable_installation_outside_target",
        ),
    ],
)
def test_non_embedded_installations_do_not_emit_host_ignore_guidance(
    tmp_path,
    target_name,
    installation_name,
    expected_mode,
    expected_applicability,
):
    target = tmp_path / target_name
    installation = target if installation_name == target_name else tmp_path / installation_name
    target.mkdir()
    if installation != target:
        installation.mkdir()
    footprint = build_embedded_host_footprint(
        target,
        installation,
        contract=load_installation_preflight_contract(),
        target_profile={"target_policy": {"summary": {"declared_tools": ["eslint"]}}},
    )

    assert footprint["installation_mode"] == expected_mode
    assert footprint["applicability"] == expected_applicability
    assert footprint["embedded_root"] is None
    assert footprint["host_tool_guidance"] == []
    assert footprint["traversal"]["status"] == "not_applicable"


def test_embedded_installation_plan_surfaces_guidance_without_executable_action(tmp_path):
    target = tmp_path / "host"
    installation = target / "sage-runtime"
    installation.mkdir(parents=True)
    target_profile = {
        "root": str(target),
        "exists": True,
        "is_dir": True,
        "preflight_status": "PASS",
        "attention_reasons": [],
        "language_counts": {"python": 1},
        "language_families": ["python"],
        "react_signal": False,
        "node_manifest_status": "not_observed",
        "analysis_authority": {},
        "target_policy": {"summary": {"declared_tools": ["eslint"]}},
    }
    plan = build_installation_plan(
        target,
        target_profile=target_profile,
        machine=_machine(),
        installation_root=installation,
    )

    assert plan["summary"]["status"] == "ATTENTION"
    assert plan["summary"]["installation_mode"] == "embedded"
    assert plan["actions"] == []
    assert {row["id"] for row in plan["attention"]} == {
        "embedded_sage_host_tool_isolation_guidance"
    }
    assert plan["mutation_boundary"]["target_host_tool_configuration_mutation_allowed"] is False
    assert plan["installation_footprint"]["root_exclusion"]["recursive_glob"] == "sage-runtime/**"
    rendered = installation_preflight.render_installation_plan(plan)
    console = installation_preflight.render_console_lines(plan)
    assert "## Embedded Host-Tool Footprint" in rendered
    assert "review exclusion `sage-runtime/**`" in rendered
    assert any("installation_mode=embedded" in line for line in console)


def test_embedded_footprint_reports_blocked_declared_path_without_changing_permissions(
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "host"
    installation = target / "sage-runtime"
    installation.mkdir(parents=True)
    original_probe = installation_preflight._host_access_observation

    def blocked_cache(path):
        if path.name == ".pytest_cache":
            return {
                "status": "blocked",
                "error_type": "PermissionError",
                "message": "fixture denied",
            }
        return original_probe(path)

    monkeypatch.setattr(
        installation_preflight,
        "_host_access_observation",
        blocked_cache,
    )
    footprint = build_embedded_host_footprint(
        target,
        installation,
        contract=load_installation_preflight_contract(),
        target_profile={"target_policy": {"summary": {"declared_tools": []}}},
    )

    assert footprint["traversal"] == {
        "status": "blocked",
        "blocked_paths": [".pytest_cache"],
    }
    assert footprint["permission_mutation"] == {
        "allowed": False,
        "performed": False,
    }


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

    def topology_with_selected_sage(root, scope_projection, **kwargs):
        topology = topology_builder(root, scope_projection, **kwargs)
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


def test_explicit_init_reuses_one_preflight_for_plan_and_discovery_transport(monkeypatch):
    target_root = Path("C:/fixture")
    preflight = {
        "meta": {"kind": "external_target_preflight", "run_id": "preflight-fixture"},
        "target": {"root": str(target_root), "output_dir": "C:/output/fixture"},
        "summary": {"status": "PASS"},
    }
    calls = []
    expected_plan = {"summary": {"status": "READY"}}
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
        external_target_preflight,
        "build_preflight",
        lambda root, projects=None: calls.append(("build", root, projects)) or preflight,
    )
    monkeypatch.setattr(
        external_target_preflight,
        "persist_preflight",
        lambda payload: calls.append(("persist", payload)) or payload,
    )
    monkeypatch.setattr(
        external_target_preflight,
        "preflight_receipt_transport",
        lambda payload: {
            "path": "C:/output/fixture/.raw/external_target_preflight.json",
            "sha256": "a" * 64,
            "run_id": payload["meta"]["run_id"],
        },
    )

    def build_plan(root, **kwargs):
        calls.append(("plan", root, kwargs))
        return expected_plan

    monkeypatch.setattr(bootstrap_env, "build_installation_plan", build_plan)
    monkeypatch.setattr(bootstrap_env, "record_execution_duration", lambda *_args: None)
    transport_environment_keys = (
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
        "CODEMAPS_TARGET_PROJECTS",
    )
    original_transport_environment = {
        key: bootstrap_env.os.environ.get(key) for key in transport_environment_keys
    }

    with monkeypatch.context() as transport_environment:
        for key in transport_environment_keys:
            transport_environment.setenv(key, f"preexisting-{key.lower()}")
        result = bootstrap_env._build_installation_plan_with_progress(
            target_root,
            dependency_install_enabled=True,
            persist_target_preflight=True,
            projects="MAIN",
        )

        assert bootstrap_env.os.environ["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] == "a" * 64
        assert bootstrap_env.os.environ["CODEMAPS_TARGET_PROJECTS"] == "MAIN"

    assert {
        key: bootstrap_env.os.environ.get(key) for key in transport_environment_keys
    } == original_transport_environment

    assert result is expected_plan
    assert [call[0] for call in calls] == ["build", "persist", "plan"]
    assert calls[0][2] == "MAIN"
    assert calls[-1][2]["target_preflight"] is preflight


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

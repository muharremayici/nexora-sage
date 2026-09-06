import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.core import config
from tools.core.artifact_validator import ensure_valid_payload
from tools.core.scoped_graph_projection import build_scoped_graph_advisories
from tools.core.watchdog_runtime_contract import (
    validate_watchdog_audit_scope,
    validate_watchdog_artifact_identity,
    watchdog_artifact_path,
)
from tools.orchestrators import watchdog


REFRESH_POLICY = {
    "recommended_refresh_modes": [
        "auto_daily",
        "auto_full",
        "auto_release_deep",
    ],
    "auto_refresh": {
        "command_by_mode": {
            "auto_daily": ["run", "--profile", "daily"],
            "auto_full": ["run", "--full"],
            "auto_release_deep": ["run", "--profile", "release-deep"],
        },
    },
}


def test_scoped_artifacts_project_into_each_active_runtime_namespace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        default_raw = root / "default" / ".raw"
        external_a_raw = root / "external-a" / ".raw"
        external_b_raw = root / "external-b" / ".raw"

        with patch.object(config, "RAW_DIR", default_raw):
            default_audit = watchdog_artifact_path("audit")
            default_graph = watchdog_artifact_path("graph_advisories")
        with patch.object(config, "RAW_DIR", external_a_raw):
            external_a_audit = watchdog_artifact_path("audit")
            external_a_graph = watchdog_artifact_path("graph_advisories")
        with patch.object(config, "RAW_DIR", external_b_raw):
            external_b_audit = watchdog_artifact_path("audit")
            external_b_graph = watchdog_artifact_path("graph_advisories")

    assert default_audit == default_raw / "watchdog_audit_report.json"
    assert default_graph == default_raw / "watchdog_graph_advisories.json"
    assert external_a_audit == external_a_raw / "watchdog_audit_report.json"
    assert external_a_graph == external_a_raw / "watchdog_graph_advisories.json"
    assert external_b_audit == external_b_raw / "watchdog_audit_report.json"
    assert external_b_graph == external_b_raw / "watchdog_graph_advisories.json"
    assert len({default_audit, external_a_audit, external_b_audit}) == 3

    default_audit.parent.mkdir(parents=True)
    external_a_audit.parent.mkdir(parents=True)
    default_audit.write_bytes(b"default")
    default_mtime = default_audit.stat().st_mtime_ns
    external_a_audit.write_bytes(b"external-a")
    assert default_audit.read_bytes() == b"default"
    assert default_audit.stat().st_mtime_ns == default_mtime
    assert external_a_audit.read_bytes() == b"external-a"


def test_scoped_identity_rejects_cross_target_or_cross_generation_payload() -> None:
    expected = {
        "status": "BOUND",
        "artifact_root": "C:/sage/output/external_targets/a/.raw",
        "atlas_snapshot_id": "snapshot-a",
        "target_descriptor": {
            "system_scope": "SAGE_ON_REPOSITORY",
            "acquisition_mode": "EXTERNAL_TARGET",
            "profile_id": "target_repository_default",
            "subject_root": "C:/targets/a",
            "analysis_root": "C:/targets/a",
            "artifact_strategy": "EXTERNAL_TARGET_NAMESPACE",
            "external_target": True,
        },
    }
    matching = {"artifact_identity": dict(expected)}
    cross_target = {
        "artifact_identity": {
            **expected,
            "target_descriptor": {**expected["target_descriptor"], "subject_root": "C:/targets/b"},
        }
    }
    stale_generation = {
        "artifact_identity": {**expected, "atlas_snapshot_id": "snapshot-old"}
    }

    assert validate_watchdog_artifact_identity(matching, expected)["identity_match"]
    assert not validate_watchdog_artifact_identity(cross_target, expected)["identity_match"]
    assert not validate_watchdog_artifact_identity(stale_generation, expected)["identity_match"]

    scoped_audit = {
        **matching,
        "meta": {"kind": "watchdog_audit_report", "version": "test"},
        "audit_scope": {
            "scope_kind": "scoped_change",
            "full_repository_claim": False,
            "requested_file_count": 1,
            "requested_files": ["MAIN::src/App.tsx"],
            "audited_file_count": 1,
            "audited_files": ["MAIN::src/App.tsx"],
            "unresolved_requested_files": [],
            "scope_status": "complete",
        },
    }
    assert validate_watchdog_audit_scope(
        scoped_audit,
        ["MAIN::src/App.tsx"],
        expected,
    )["scope_match"]
    assert not validate_watchdog_audit_scope(
        {**scoped_audit, **cross_target},
        ["MAIN::src/App.tsx"],
        expected,
    )["scope_match"]


def test_scoped_schemas_require_bound_target_and_generation_identity() -> None:
    identity = {
        "status": "BOUND",
        "artifact_root": "C:/sage/output/external_targets/a/.raw",
        "atlas_snapshot_id": "snapshot-a",
        "target_descriptor": {
            "system_scope": "SAGE_ON_REPOSITORY",
            "acquisition_mode": "EXPLICIT_TARGET",
            "profile_id": "target_repository_default",
            "subject_root": "C:/targets/a",
            "analysis_root": "C:/targets/a",
            "artifact_strategy": "EXTERNAL_TARGET_NAMESPACE",
            "external_target": True,
        },
    }
    graph = build_scoped_graph_advisories({}, [])
    graph["artifact_identity"] = identity
    ensure_valid_payload("watchdog_graph_advisories", graph)

    audit = {
        "meta": {"kind": "watchdog_audit_report", "version": "test"},
        "artifact_identity": identity,
        "summary": {
            "total": 0,
            "by_rule": {},
            "audit_scope": {},
            "rule_taxonomy": {},
            "remediation_backlog": [],
        },
        "audit_scope": {
            "scope_kind": "scoped_change",
            "full_repository_claim": False,
            "atlas_project_count": 1,
            "audited_project_count": 1,
            "audited_projects": ["MAIN"],
            "violation_project_count": 0,
            "requested_file_count": 1,
            "requested_files": ["MAIN::src/App.tsx"],
            "audited_file_count": 1,
            "audited_files": ["MAIN::src/App.tsx"],
            "unresolved_requested_files": [],
            "scope_status": "complete",
        },
        "atlas_project_count": 1,
        "audited_project_count": 1,
        "audited_projects": ["MAIN"],
        "violation_project_count": 0,
        "violations": [],
        "report_sections": [],
    }
    ensure_valid_payload("watchdog_audit_report", audit)


def test_watchdog_refresh_actions_preserve_default_target_semantics() -> None:
    descriptor = {
        "system_scope": "SAGE_ON_REPOSITORY",
        "acquisition_mode": "DEFAULT_REPOSITORY",
        "profile_id": "target_repository_default",
        "subject_root": "C:/workspace/default",
        "artifact_root": "C:/sage/output/.raw",
        "external_target": False,
    }

    actions = watchdog._watchdog_refresh_actions(REFRESH_POLICY, descriptor)

    assert [action["status"] for action in actions] == ["BOUND"] * 3
    assert actions[0]["argv"] == ["python", "sage.py", "run", "--profile", "daily"]
    assert all("--target-root" not in action["argv"] for action in actions)
    assert all(action["target_binding"]["subject_root"] == descriptor["subject_root"] for action in actions)


def test_watchdog_refresh_actions_bind_every_external_command_to_target() -> None:
    target_root = "C:/Hedef Deposu/İçerik"
    descriptor = {
        "system_scope": "SAGE_ON_REPOSITORY",
        "acquisition_mode": "EXPLICIT_TARGET",
        "profile_id": "target_repository_default",
        "subject_root": target_root,
        "artifact_root": "C:/sage/output/external_targets/hedef/.raw",
        "external_target": True,
    }

    actions = watchdog._watchdog_refresh_actions(REFRESH_POLICY, descriptor)

    assert [action["id"] for action in actions] == [
        "auto_daily",
        "auto_full",
        "auto_release_deep",
    ]
    for action in actions:
        assert action["status"] == "BOUND"
        assert action["argv"][-2:] == ["--target-root", target_root]
        assert target_root in action["command"]
        assert action["target_binding"]["acquisition_mode"] == "EXPLICIT_TARGET"


def test_watchdog_external_action_without_subject_root_is_blocked() -> None:
    descriptor = {
        "system_scope": "SAGE_ON_REPOSITORY",
        "acquisition_mode": "EXPLICIT_TARGET",
        "external_target": True,
    }

    action = watchdog._target_bound_watchdog_action(
        "auto_full",
        ["run", "--full"],
        descriptor,
    )

    assert action["status"] == "BLOCKED"
    assert action["argv"] == []
    assert action["command"] == ""


def test_watchdog_auto_refresh_executes_the_same_target_bound_action() -> None:
    target_root = "C:/Hedef Deposu/İçerik"
    descriptor = {
        "system_scope": "SAGE_ON_REPOSITORY",
        "acquisition_mode": "EXPLICIT_TARGET",
        "profile_id": "target_repository_default",
        "subject_root": target_root,
        "analysis_root": target_root,
        "artifact_root": "C:/sage/output/external_targets/hedef/.raw",
        "external_target": True,
    }
    plan = watchdog._watchdog_auto_refresh_plan(
        {
            "auto_refresh": {
                "mode": "auto_daily",
                "default_mode": "advisory",
                "allowed_modes": ["advisory", "auto_daily"],
                "command_by_mode": {"auto_daily": ["run", "--profile", "daily"]},
            }
        },
        True,
        descriptor,
    )
    session = {
        "target_descriptor": descriptor,
        "target_repository_deep_proof_debt": {"auto_refresh": plan},
    }
    handler = watchdog.CodeMapsHandler.__new__(watchdog.CodeMapsHandler)

    with patch.object(watchdog.subprocess, "run") as run:
        run.return_value.returncode = 0
        assert handler._maybe_run_deep_proof_auto_refresh(session)

    command = run.call_args.args[0]
    child_env = run.call_args.kwargs["env"]
    assert Path(command[1]).name == "sage.py"
    assert command[2:] == ["run", "--profile", "daily", "--target-root", target_root]
    assert child_env["PYTHONIOENCODING"] == "utf-8"
    assert child_env["PYTHONUTF8"] == "1"

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core import capability_activation
from tools.core.artifact_registry import artifact_path_for_storage_root
from tools.core.architecture_blueprints import architecture_governance_context
from tools.core.contextos_mcp import build_surgical_operation_packet
from tools.core.mcp_tool_profiles import project_mcp_tool_names
from tools.mcp import server

from tools.core.capability_activation import relevant_activation_context
from tools.core.capability_registry import build_agent_capability_map, load_capability_registry
from tools.core.config import CONFIG_DIR
from tools.core.config import CODE_MAPS_DIR
from tools.core.json_io import load_json_object_strict
from tools.core.reality_scope import (
    SAGE_DEVELOPER_PROJECTION_ID,
    SAGE_SELF_TARGET_PROFILE_ID,
    TARGET_REPOSITORY_PROJECTION_ID,
    build_reality_scope_projection,
    build_reality_target_workflow,
    capability_scope_assignments,
    projection_capability_scopes,
    system_scope_ids,
    validate_reality_target_profiles,
)


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DISTRIBUTION = (ROOT / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file()


class RealityScopeProjectionTests(unittest.TestCase):
    def test_capability_scope_assignments_are_complete_and_disjoint(self):
        registry = load_capability_registry()
        capability_ids = {
            str(row.get("id"))
            for row in registry.get("capabilities", [])
            if isinstance(row, dict) and row.get("id")
        }
        assignments = capability_scope_assignments()
        assigned_ids = set().union(*assignments.values())

        self.assertEqual(set(assignments), system_scope_ids())
        self.assertEqual(assigned_ids, capability_ids)
        for capability_id in capability_ids:
            self.assertEqual(sum(capability_id in values for values in assignments.values()), 1)

    def test_target_projection_excludes_sage_development_registries_and_capabilities(self):
        projection = build_reality_scope_projection(TARGET_REPOSITORY_PROJECTION_ID)
        capability_ids = {str(row.get("id")) for row in projection.get("capabilities", [])}

        self.assertEqual(projection.get("meta", {}).get("system_scope"), "SAGE_ON_REPOSITORY")
        self.assertNotIn("lessons", projection)
        self.assertNotIn("work_items", projection)
        self.assertNotIn("roadmap_phases", projection)
        self.assertNotIn("release_proof", capability_ids)
        self.assertNotIn("plugin_extension_foundation", capability_ids)
        self.assertNotIn("bidirectional_capability_transfer_matrix", projection)
        self.assertEqual(
            projection.get("scope_boundary", {}).get("excluded_registries"),
            [
                "audit_lesson_registry",
                "sage_work_item_registry",
                "sage_execution_wave_registry",
                "roadmap_phase_registry",
            ],
        )

    def test_developer_projection_owns_bidirectional_transfer_decisions(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
            return
        projection = build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
        transfer_rows = projection.get("bidirectional_capability_transfer_matrix", [])

        self.assertTrue(transfer_rows)
        self.assertTrue(all(row.get("transfer_direction") for row in transfer_rows))
        self.assertIn("lessons", projection)
        self.assertIn("work_items", projection)
        self.assertIn("roadmap_phases", projection)
        self.assertNotIn("execution_plan", build_reality_scope_projection(TARGET_REPOSITORY_PROJECTION_ID))

    def test_developer_execution_plan_respects_active_package_lifecycle(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
            return
        projection = build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
        execution_plan = projection.get("execution_plan", {})
        active_package = load_json_object_strict(
            CONFIG_DIR / "sage_active_work_package.json",
            label="SAGE active work package",
        ).get("active_package", {})

        if active_package.get("status") == "in_progress":
            self.assertEqual(execution_plan.get("selection_basis"), "active_work_package")
            self.assertEqual(execution_plan.get("current_wave"), active_package.get("execution_wave"))
        else:
            self.assertEqual(execution_plan.get("selection_basis"), "first_open_delivery_wave")
            self.assertEqual(execution_plan.get("current_wave"), execution_plan.get("next_open_delivery_wave"))

    def test_target_agent_capability_map_applies_scope_filter(self):
        target_scopes = projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID)
        capability_map = build_agent_capability_map(
            load_capability_registry(),
            allowed_system_scopes=target_scopes,
        )
        capability_ids = {str(row.get("id")) for row in capability_map.get("capabilities", [])}

        self.assertEqual(capability_map.get("summary", {}).get("system_scope_filter"), sorted(target_scopes))
        self.assertIn("agent_surface", capability_ids)
        self.assertNotIn("release_proof", capability_ids)

    def test_target_activation_projection_filters_summary_and_project_rows(self):
        target_scopes = projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID)
        plan = {
            "summary": {
                "status": "PASS",
                "enabled_capability_ids": ["agent_surface", "release_proof"],
                "disabled_capability_ids": ["third_party_plugin_runtime"],
            },
            "projects": [
                {
                    "project": "MAIN",
                    "enabled_capabilities": [
                        {"id": "agent_surface", "status": "enabled"},
                        {"id": "release_proof", "status": "enabled"},
                    ],
                    "disabled_capabilities": [],
                }
            ],
        }
        projection = relevant_activation_context(
            plan,
            include_disabled=True,
            allowed_system_scopes=target_scopes,
        )

        self.assertEqual(projection["summary"]["enabled_capability_ids"], ["agent_surface"])
        self.assertEqual(projection["summary"]["disabled_capability_ids"], [])
        self.assertEqual([row["id"] for row in projection["projects"][0]["capabilities"]], ["agent_surface"])
        self.assertNotIn("full_plan_mcp_tool", projection["surface_policy"])
        self.assertNotIn("full_plan_artifact", projection["surface_policy"])
        self.assertEqual(projection["surface_policy"]["full_plan_followup"]["availability"], "unavailable")

    def test_private_operator_retains_its_activation_followup_outside_target_surface(self):
        if PUBLIC_DISTRIBUTION:
            self.skipTest("Private operator generation is not a public target-profile capability.")
        from tools import generate_nexora_operator_packet as operator_packets

        plan = {"summary": {"status": "PASS", "projects": 1}, "projects": []}
        with (
            patch.object(operator_packets, "_load", return_value={}),
            patch.object(operator_packets, "load_capability_activation_plan", return_value=plan),
        ):
            packet = operator_packets.build_operator_packet()
        self.assertEqual(packet["capability_activation"]["surface_policy"]["full_plan_mcp_tool"], "get_capability_activation_plan")
        self.assertEqual(packet["capability_activation"]["surface_policy"]["full_plan_artifact"], "output/.raw/capability_activation_plan.json")
        self.assertNotIn("capability_activation", packet["target_repository_agent_surface"])
        for field, tool, artifact in (
            ("pipeline_execution_contract", "get_pipeline_execution_contract", "pipeline_execution_contract_validation.json"),
            ("engine_signal_contract", "get_engine_signal_contracts", "engine_signal_contract_validation.json"),
        ):
            self.assertEqual(packet[field]["mcp_tool"], tool)
            self.assertEqual(packet[field]["artifact"], "output/.raw/" + artifact)
            self.assertNotIn(field, packet["target_repository_agent_surface"])

    def test_private_operator_retains_oracle_followup_and_seal_evidence(self):
        if PUBLIC_DISTRIBUTION:
            self.skipTest("Private operator generation is not a public target-profile capability.")
        from tools import generate_nexora_operator_packet as operator_packets
        from tools.core.mcp_tool_profiles import project_mcp_tool_names

        oracle = {"summary": {"status": "PROPOSE_SEAL"}, "projects": [
            {"project": "MAIN", "recommended_profile": "MODULAR_FLAT", "classification_status": "CLASSIFIED"},
        ]}
        ledger = {"entries": [{"gate": "architecture_doctrine_seal", "scope": "MAIN",
                              "decision": "approved", "revoked_by": ""}]}
        inputs = {"architecture_oracle.json": oracle, "hitl_approval_ledger.json": ledger}
        with (
            patch.object(operator_packets, "_load", side_effect=lambda path: inputs.get(path.name, {})),
            patch.object(operator_packets, "load_capability_activation_plan", return_value={}),
        ):
            packet = operator_packets.build_operator_packet()
        context = packet["architecture_governance_context"]
        self.assertEqual(context["mcp_tool"], "get_architecture_oracle")
        self.assertNotIn("followup", context)
        self.assertEqual(context["seal_state"], "HUMAN_SEALED")
        self.assertEqual(context["sealed_project_count"], 1)
        self.assertFalse(context["policy_activation_allowed"])
        self.assertEqual(context["effective_policy_status"], "NOT_MATERIALIZED")
        self.assertIn("output/.raw/architecture_oracle.json", context["source_artifacts"])
        self.assertNotIn("architecture_governance_context", packet["target_repository_agent_surface"])
        self.assertIn(context["mcp_tool"], project_mcp_tool_names(ROOT, "sage_operator_debug")["visible_tools"])

    def test_sage_self_target_workflow_resolves_explicit_root_and_preserves_proof_boundary(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)
            return
        workflow = build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)
        profile = workflow["profile"]

        self.assertEqual(profile["system_scope"], "SAGE_ON_SAGE")
        self.assertEqual(profile["actor_surface"], "sage_maintainer_agent")
        self.assertEqual(profile["analysis_root_token"], workflow["analysis_root"])
        self.assertEqual(profile["watchdog_support"]["status"], "supported_for_isolated_self_target")
        self.assertEqual(profile["watchdog_support"]["session_debt_field"], "sage_self_release_proof_debt")
        self.assertEqual(profile["watchdog_support"]["release_proof_behavior"], "explicit_only_non_recursive")
        self.assertIn("SAGE release readiness", profile["proof_boundary"]["does_not_prove"])
        for row in profile["bootstrap_tools"]:
            self.assertEqual(row["arguments"]["target_root"], workflow["analysis_root"])
        analysis_call = next(row for row in profile["bootstrap_tools"] if row["tool"] == "run_external_target_analysis")
        self.assertTrue(analysis_call["arguments"]["refresh"])
        self.assertTrue(all("\\" not in command for command in profile["validation_commands"]))

    def test_public_distribution_cannot_escalate_target_analysis_to_sage_self(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "PUBLIC_DISTRIBUTION_MANIFEST.json"
            manifest.write_text("{}\n", encoding="utf-8")
            with (
                patch(
                    "tools.core.reality_scope.PUBLIC_DISTRIBUTION_MANIFEST_PATH",
                    manifest,
                ),
                self.assertRaisesRegex(ValueError, "private maintainer authority"),
            ):
                build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)

    def test_sage_self_target_profile_tools_exist(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                validate_reality_target_profiles()
            return
        validation = validate_reality_target_profiles()

        self.assertEqual(validation["issues"], [])
        self.assertIn("inspect_file", validation["sage_self_tools"])

    def test_sage_self_watchdog_descriptor_and_mcp_scope_are_isolated(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)
            return
        from tools.core.watchdog_target_context import current_watchdog_target_descriptor
        from tools.mcp.server import _watchdog_session_roots

        isolated_raw_dir = (
            CODE_MAPS_DIR / "output" / "external_targets" / "sage_self_fixture" / ".raw"
        )
        with (
            patch(
                "tools.core.watchdog_target_context.DYNAMIC_CONFIG",
                {"_target_root_override": {"enabled": True, "target_root": str(CODE_MAPS_DIR)}},
            ),
            patch("tools.core.watchdog_target_context.RAW_DIR", isolated_raw_dir),
            patch.dict(
                "tools.core.watchdog_target_context.os.environ",
                {
                    "SAGE_ACTOR_PROFILE": "sage_operator_debug",
                    "SAGE_REALITY_TARGET_PROFILE": "sage_self",
                },
                clear=False,
            ),
        ):
            descriptor = current_watchdog_target_descriptor()

        self.assertEqual(descriptor["system_scope"], "SAGE_ON_SAGE")
        self.assertEqual(descriptor["profile_id"], "sage_self")
        self.assertEqual(descriptor["proof_debt_field"], "sage_self_release_proof_debt")
        self.assertEqual(descriptor["release_proof_behavior"], "explicit_only_non_recursive")
        self.assertEqual(
            Path(descriptor["artifact_root"]).resolve(),
            isolated_raw_dir.resolve(),
        )
        self.assertEqual(
            Path(descriptor["proof_authority_root"]).resolve(),
            (CODE_MAPS_DIR / "output" / ".raw").resolve(),
        )
        self.assertNotEqual(
            Path(descriptor["artifact_root"]).resolve(),
            Path(descriptor["proof_authority_root"]).resolve(),
        )

        with (
            patch("tools.mcp.server._ACTIVE_MCP_PROFILE", "sage_operator_debug"),
            patch.dict(
                "tools.mcp.server.os.environ",
                {
                    "SAGE_ACTOR_PROFILE": "sage_operator_debug",
                    "SAGE_REALITY_TARGET_PROFILE": "sage_self",
                },
                clear=False,
            ),
        ):
            raw_dir, reports_dir, resolved_root = _watchdog_session_roots(
                str(CODE_MAPS_DIR), "sage_self"
            )
        self.assertEqual(resolved_root, str(CODE_MAPS_DIR.resolve()))
        self.assertIn("external_targets", str(raw_dir))
        self.assertIn("external_targets", str(reports_dir))
        with self.assertRaises(ValueError):
            _watchdog_session_roots(str(CODE_MAPS_DIR), "")

    def test_installation_root_equality_does_not_auto_escalate_watchdog_authority(self):
        from tools.core.watchdog_target_context import current_watchdog_target_descriptor

        with (
            patch(
                "tools.core.watchdog_target_context.DYNAMIC_CONFIG",
                {"_target_root_override": {"enabled": True, "target_root": str(CODE_MAPS_DIR)}},
            ),
            patch.dict("tools.core.watchdog_target_context.os.environ", {}, clear=True),
        ):
            descriptor = current_watchdog_target_descriptor()

        self.assertEqual(descriptor["system_scope"], "SAGE_ON_REPOSITORY")
        self.assertEqual(descriptor["acquisition_mode"], "EXPLICIT_TARGET")
        self.assertEqual(descriptor["profile_id"], "target_repository_default")
        self.assertFalse(descriptor["proof_debt_field"] == "sage_self_release_proof_debt")

    def test_default_repository_watchdog_descriptor_has_canonical_execution_identity(self):
        from tools.core.watchdog_target_context import current_watchdog_target_descriptor

        with patch("tools.core.watchdog_target_context.DYNAMIC_CONFIG", {}):
            descriptor = current_watchdog_target_descriptor()

        self.assertEqual(descriptor["system_scope"], "SAGE_ON_REPOSITORY")
        self.assertEqual(descriptor["acquisition_mode"], "DEFAULT_WORKSPACE")
        self.assertEqual(descriptor["profile_id"], "target_repository_default")
        self.assertEqual(descriptor["artifact_strategy"], "DEFAULT_WORKSPACE_NAMESPACE")
        self.assertTrue(descriptor["subject_root"])


def _oracle_inputs(case: str) -> tuple[dict, dict, dict]:
    if case == "missing":
        return {}, {}, {}
    oracle = {
        "summary": {"status": "PROPOSE_SEAL", "top_recommended_profile": "MODULAR_FLAT"},
        "repository_blueprint": {"project_ids": ["MAIN", "OTHER"], "status": "ADVISORY_ONLY"},
        "projects": [
            {"project": "MAIN", "recommended_profile": "MODULAR_FLAT",
             "classification_status": "INSUFFICIENT_SOURCE_EVIDENCE" if case == "insufficient" else "CLASSIFIED",
             "confidence": 0.9, "seal_ready": case != "insufficient",
             "project_system_kind": {"kind": "application"},
             "project_language_capability": {"status": "ENGINE_AVAILABLE"}},
            {"project": "OTHER", "recommended_profile": "MINIMAL", "classification_status": "CLASSIFIED"},
        ],
    }
    ledger = {"entries": []}
    if case in ("sealed", "activated", "insufficient"):
        ledger["entries"] = [{"gate": "architecture_doctrine_seal", "scope": "MAIN",
                              "decision": "approved", "revoked_by": ""}]
    policy = {}
    if case == "activated":
        policy = {"summary": {"status": "PASS"}, "projects": {
            "MAIN": {"status": "ACTIVATED", "policy_activation_allowed": True,
                     "feature_flags": {"architecture_sensitive_rules": "enforce"}},
        }}
    return oracle, ledger, policy


@pytest.mark.parametrize("case", ["missing", "proposal", "sealed", "activated", "insufficient"])
def test_target_oracle_followup_is_unavailable_without_losing_architecture(tmp_path: Path, case: str) -> None:
    raw = tmp_path / ".raw"
    raw.mkdir()
    oracle, ledger, policy = _oracle_inputs(case)
    for name, value in (("architecture_oracle", oracle), ("hitl_approval_ledger", ledger),
                        ("effective_architecture_policy", policy)):
        if value:
            (raw / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    signals = {"active_signals": [{"node_key": "MAIN::src/example.ts", "relative_path": "src/example.ts"}]}
    with patch.object(capability_activation, "build_capability_activation_plan", side_effect=AssertionError("Private planner called")):
        packet = build_surgical_operation_packet(signals, project_scope="MAIN", raw_dir=raw)
    context = packet["architecture_governance_context"]
    assert "mcp_tool" not in context
    assert context["followup"]["availability"] == "unavailable"
    assert context["followup"]["reason"] == "maintainer_only"
    assert "does not authorize" in context["followup"]["claim_boundary"]
    assert "does not prove" in context["followup"]["claim_boundary"]
    assert context["selected_project_count"] == (0 if case == "missing" else 1)
    assert [row["project"] for row in context["projects"]] == ([] if case == "missing" else ["MAIN"])
    assert context["seal_state"] == ("HUMAN_SEALED" if case in ("sealed", "activated") else "PROPOSAL_ONLY")
    assert context["sealed_project_count"] == (1 if case in ("sealed", "activated") else 0)
    assert context["policy_activation_allowed"] is (case == "activated")
    assert context["effective_policy_status"] == ("PASS" if case == "activated" else "NOT_MATERIALIZED")
    assert context["repository_blueprint_applicability"] == ("NOT_GENERATED" if case == "missing" else "PROJECT_FILTER_SCOPE_MISMATCH")
    assert context["source_artifacts"] == [
        "output/.raw/architecture_oracle.json", "output/.raw/effective_architecture_policy.json",
        "config/architecture_doctrine.json", "output/.raw/hitl_approval_ledger.json",
    ]
    assert "does not prove a compiled or activated effective policy" in context["agent_rule"]
    if case != "missing":
        assert context["projects"][0]["project_language_capability"] == {"status": "ENGINE_AVAILABLE"}
    if case == "insufficient":
        assert context["projects"][0]["seal_state"] == "EVIDENCE_INSUFFICIENT"
    for profile in ("target_repository_default", "target_repository_followup"):
        assert "get_architecture_oracle" not in project_mcp_tool_names(ROOT, profile)["visible_tools"]


@pytest.mark.parametrize("case", ["missing", "proposal", "sealed", "activated", "insufficient"])
def test_oracle_followup_projection_changes_only_access_guidance(case: str) -> None:
    oracle, ledger, policy = _oracle_inputs(case)
    target = architecture_governance_context(oracle, ledger, policy, project_ids={"MAIN"})
    private = architecture_governance_context(oracle, ledger, policy, project_ids={"MAIN"},
                                             include_private_followup=True)
    assert private["mcp_tool"] == "get_architecture_oracle"
    assert "followup" not in private
    assert {k: v for k, v in target.items() if k != "followup"} == {
        k: v for k, v in private.items() if k != "mcp_tool"
    }
    if not (ROOT / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file():
        assert private["mcp_tool"] in project_mcp_tool_names(ROOT, "sage_operator_debug")["visible_tools"]


@pytest.mark.parametrize("profile", ["target_repository_default", "target_repository_followup"])
def test_target_profiles_reject_private_oracle_before_execution(profile: str) -> None:
    instance = server.ProfiledFastMCP("oracle-profile-guard")
    instance.set_visible_tools(profile, set(project_mcp_tool_names(ROOT, profile)["visible_tools"]))
    with patch.object(instance, "_record_governance_trace") as recorder:
        with pytest.raises(ValueError, match="get_architecture_oracle.*unavailable in profile"):
            asyncio.run(instance.call_tool("get_architecture_oracle", {}))
    assert recorder.call_args.args[4:6] == ("failure", "tool_visibility")


def _recovery_from_response(body: str) -> dict:
    if body.lstrip().startswith("{"):
        return json.loads(body)["recovery"]
    line = next(line for line in body.splitlines() if line.startswith("recovery: "))
    return json.loads(line.removeprefix("recovery: "))


def _set_recovery_profile(monkeypatch, profile: str) -> set[str]:
    names = set(project_mcp_tool_names(ROOT, profile)["visible_tools"])
    monkeypatch.setattr(server.mcp, "active_tool_profile", profile)
    monkeypatch.setattr(server.mcp, "_visible_tool_names", frozenset(names))
    monkeypatch.setattr(server, "_ACTIVE_MCP_PROFILE", profile)
    monkeypatch.delenv(server.SAGE_ACTOR_PROFILE_ENV, raising=False)
    monkeypatch.setenv(server.SAGE_REALITY_TARGET_PROFILE_ENV,
                      server.SAGE_SELF_REALITY_PROFILE if profile == "sage_operator_debug" else "")
    return names


@pytest.mark.parametrize("profile", ["target_repository_default", "target_repository_followup"])
@pytest.mark.parametrize("external", [False, True])
def test_recovery_missing_analysis_never_advertises_hidden_heavy_call(monkeypatch, profile, external):
    _set_recovery_profile(monkeypatch, profile)
    target_root = 'C:/fixture/Örnek repo; other "scope"' if external else ""
    required_files = [artifact_path_for_storage_root(Path("."), artifact).name
                      for artifact in ("atlas", "test_impact_report")]
    body = server._missing_target_artifact_brief("get_test_impact", "MAIN::src/A.ts", target_root,
                                                 required_files)
    assert "run_external_target_analysis" not in body
    recovery = _recovery_from_response(body)
    assert recovery["availability"] == "unavailable"
    assert recovery["target_root"] == target_root
    assert recovery["analysis_root"] == (target_root or str(server.ANALYZED_REPOSITORY_ROOT))
    assert recovery["execution_authority"] == "not_granted"
    assert recovery["refresh_executed"] is False
    assert "mcp_call" not in recovery and "cli_manual_alternative" not in recovery
    assert all(name in body for name in required_files)
    assert "another repository or stale workspace" in body


@pytest.mark.parametrize("profile", ["target_repository_followup"])
@pytest.mark.parametrize("output_format", ["brief", "json"])
@pytest.mark.parametrize("case", ["missing_packet", "missing_surface", "refresh"])
def test_recovery_external_operator_packet_preserves_access_and_never_runs_analysis(
    tmp_path, monkeypatch, profile, output_format, case,
):
    _set_recovery_profile(monkeypatch, profile)
    target = tmp_path / 'repo Örnek with spaces'
    target.mkdir()
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda root: tmp_path / ".raw")
    monkeypatch.setattr(server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    monkeypatch.setattr(server, "_load_json", lambda path: {} if case == "missing_surface" else None)
    def forbidden(*args, **kwargs):
        raise AssertionError("Recovery advice executed a producer or CLI")
    for name in ("_run_cli", "run_operator_packet", "run_agent_contract", "run_nexora_brief"):
        monkeypatch.setattr(server, name, forbidden)
    body = server.get_operator_packet(target_root=str(target), refresh=case == "refresh", format=output_format)
    assert "run_external_target_analysis" not in body
    if case == "refresh" and profile == "target_repository_followup":
        assert "BLOCKED" in body
        return
    recovery = _recovery_from_response(body)
    assert recovery["availability"] == "unavailable"
    assert recovery["target_root"] == str(target)
    assert recovery["refresh_executed"] is False
    assert recovery["execution_authority"] == "not_granted"


@pytest.mark.parametrize("profile", ["target_repository_followup"])
@pytest.mark.parametrize("case", ["missing", "missing_report", "stale", "current"])
def test_recovery_watchdog_guidance_uses_current_visibility_and_exact_target(
    tmp_path, monkeypatch, profile, case,
):
    from copy import deepcopy
    import inspect
    visible = _set_recovery_profile(monkeypatch, profile)
    target = tmp_path / "repo Örnek with spaces"
    target.mkdir()
    raw = tmp_path / ".raw"
    raw.mkdir()
    session = {"summary": {"input_origin": "explicit_files", "analysis_status": "analyzed"},
        "producer": {"kind": "fixture", "command": ["python", "sage.py", "watch"]},
        "target_descriptor": {"system_scope": "SAGE_ON_REPOSITORY", "acquisition_mode": "external_target",
            "profile_id": "target_repository_default", "subject_root": str(target), "artifact_strategy": "external_target"},
        "path_contract": {"analysis_root": str(target)}, "violations": [], "input_files": ["src/A.ts"]}
    before = deepcopy(session)
    if case in ("stale", "current"):
        (raw / "watchdog_session.json").write_text(json.dumps(session), encoding="utf-8")
    monkeypatch.setattr(server, "_watchdog_session_roots", lambda root, profile_id: (raw, tmp_path / "reports", str(target)))
    ledger = {"summary": {"current_violation_count": 0}}
    monkeypatch.setattr(server, "_load_json", lambda path: deepcopy(session if path.name == "watchdog_session.json" else ledger))
    monkeypatch.setattr(server, "_raw_artifact_source_mtime", lambda name, path: 2 if case == "stale" and name == "watchdog_pulse_ledger" else 1)
    monkeypatch.setattr(server, "_record_mcp_call_result", lambda name, started, result, **kwargs: result)
    monkeypatch.setattr(server, "_run_cli", lambda *args, **kwargs: pytest.fail("Read-only guidance invoked CLI"))
    body = server.get_watchdog_session(target_root=str(target), human_report=case == "missing_report")
    recovery = _recovery_from_response(body)
    assert recovery["target_root"] == str(target)
    assert recovery["profile_id"] == ""
    assert recovery["execution_authority"] == "not_granted" and recovery["refresh_executed"] is False
    if "run_watchdog_once" in visible:
        call = recovery["mcp_call"]
        assert call["tool"] in visible
        inspect.signature(server.run_watchdog_once).bind(**call["arguments"])
        assert call["arguments"]["target_root"] == str(target)
        assert call["arguments"]["profile_id"] == ""
    else:
        assert "run_watchdog_once" not in body
        assert recovery["availability"] == "unavailable" and "mcp_call" not in recovery
    cli = recovery["cli_manual_alternative"]
    assert cli["argv"] == ["python", "sage.py", "watch", "--once", "--target-root", str(target), "--path", "<explicit source path>"]
    assert cli["cwd"] == str(server.BASE_DIR)
    assert cli["execution_authority"] == "not_granted"
    from codemaps import build_parser
    parsed = build_parser().parse_args(cli["argv"][2:])
    assert parsed.func.__name__ == "cmd_watch" and parsed.once is True
    assert parsed.target_root == str(target) and parsed.path == ["<explicit source path>"]
    assert session == before
    if case == "stale":
        assert "stale_against_pulse_ledger" in body and "historical_session_not_current" in body
    if case == "current":
        assert "latest_session_artifact" in body and "not release proof" in body


def test_recovery_profile_authority_matches_distribution():
    from tools.core.mcp_tool_profiles import available_mcp_tool_profiles
    profiles = available_mcp_tool_profiles(ROOT)
    assert "target_repository_followup" in profiles
    if PUBLIC_DISTRIBUTION:
        assert "sage_operator_debug" not in profiles
        with pytest.raises(ValueError, match="Unknown MCP tool profile"):
            project_mcp_tool_names(ROOT, "sage_operator_debug")
    else:
        assert "sage_operator_debug" in profiles


def test_recovery_external_analysis_is_not_visible_in_any_declared_profile():
    from tools.core.mcp_tool_profiles import available_mcp_tool_profiles
    for profile in available_mcp_tool_profiles(ROOT):
        assert "run_external_target_analysis" not in project_mcp_tool_names(ROOT, profile)["visible_tools"]


@pytest.mark.parametrize("profile_id", ["unsupported-profile", "target_repository_default"])
def test_recovery_invalid_watchdog_profile_rejects_before_advice(monkeypatch, profile_id):
    _set_recovery_profile(monkeypatch, "target_repository_followup")
    monkeypatch.setattr(server, "_record_mcp_call_result", lambda name, started, result, **kwargs: result)
    body = server.get_watchdog_session(profile_id=profile_id)
    assert "invalid_watchdog_target_scope" in body
    assert "recovery:" not in body and "run_watchdog_once" not in body


@pytest.mark.parametrize("profile", ["target_repository_default", "target_repository_followup"])
@pytest.mark.parametrize("tool", ["run_watchdog_once", "run_external_target_analysis"])
def test_recovery_target_profiles_still_reject_hidden_calls(profile, tool):
    instance = server.ProfiledFastMCP("recovery-profile-guard")
    instance.set_visible_tools(profile, set(project_mcp_tool_names(ROOT, profile)["visible_tools"]))
    with patch.object(instance, "_record_governance_trace") as recorder:
        with pytest.raises(ValueError, match=tool + ".*unavailable in profile"):
            asyncio.run(instance.call_tool(tool, {}))
    assert recorder.call_args.args[4:6] == ("failure", "tool_visibility")


if __name__ == "__main__":
    unittest.main()

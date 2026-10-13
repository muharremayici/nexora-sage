import json
import ast
import inspect
from copy import deepcopy
from pathlib import Path

import pytest

from tools.mcp import server as mcp_server
from tools.core.artifact_registry import artifact_path_for_storage_root
from tools.core.contextos_mcp import render_active_signals
from tools.core.agent_surface_target_visibility import (
    is_evidence_blocked_empty_result,
    is_review_only_empty_result,
    is_structured_precondition_block,
    is_successful_surgical_packet,
    target_visibility_status,
)


ROOT = Path(__file__).resolve().parents[2]


def test_empty_contextos_projection_is_visible_without_inventing_a_target() -> None:
    contract = json.loads((ROOT / "config" / "agent_surface_seal_contract.json").read_text(encoding="utf-8"))
    policy = contract["manual_pack_selection_policy"]
    for scope, claim in (("unknown", "not_established"), ("bounded", "supported")):
        for output_format in ("markdown", "json"):
            body = render_active_signals(
                {"active_signals": [], "meta": {"current_change_scope": scope, "current_turn_claim": claim}},
                output_format=output_format,
                resolve_absolute_path=lambda path, project: ROOT / path,
            )
            assert target_visibility_status({"body": body}, "", policy) == "fail_closed_or_clean_no_direct_target"
            assert target_visibility_status({"body": body}, "src/example.ts", policy) == "review_target_available"


def test_empty_contextos_heading_or_status_alone_does_not_excuse_a_missing_target() -> None:
    contract = json.loads((ROOT / "config" / "agent_surface_seal_contract.json").read_text(encoding="utf-8"))
    policy = contract["manual_pack_selection_policy"]
    for body in (
        "# ContextOS: No Active Surgery Signals\nstatus: no_active_signals\nEdit the repository now.",
        json.dumps({"status": "no_active_signals", "active_signals": [], "agent_directive": "Edit now."}),
        "",
    ):
        assert target_visibility_status({"body": body}, "", policy) == "missing_review_target"


def test_read_only_watchdog_session_is_visible_in_target_followup_and_operator_profiles() -> None:
    contract = json.loads((ROOT / "config" / "agent_surface_contract.json").read_text(encoding="utf-8"))
    profiles = contract["ai_agent_readiness"]["tool_context_profiles"]

    assert "get_watchdog_session" in profiles["target_repository_followup"]["include_tools"]
    if (ROOT / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file():
        assert "sage_operator_debug" not in profiles
    else:
        assert "get_watchdog_session" in profiles["sage_operator_debug"]["include_tools"]


def test_actor_gateway_is_coreachable_with_every_declared_eligible_tool() -> None:
    actor_contract = json.loads((ROOT / "config" / "actor_interaction_contract.json").read_text(encoding="utf-8"))
    surface = actor_contract["adapter_contract"]["conformance_evidence"]["adapters"]["mcp"]["available_surfaces"]["dispatch_actor_request"]
    projections = {
        profile: set(mcp_server.project_mcp_tool_names(ROOT, profile)["visible_tools"])
        for profile in ("target_repository_default", "target_repository_followup")
    }

    unreachable = [
        tool_name
        for tool_name in surface["eligible_tools"]
        if not any(
            {"dispatch_actor_request", tool_name}.issubset(visible_tools)
            for visible_tools in projections.values()
        )
    ]

    assert unreachable == []


def test_external_target_default_scope_remains_main_first() -> None:
    assert mcp_server._default_agent_scope_allows(
        {"project": "MAIN", "file": "src/StructureTab.tsx"},
        target_root="C:/external/repo",
        explicit_filter="StructureTab",
    )
    assert not mcp_server._default_agent_scope_allows(
        {"project": "LINGUASCRIBE_MASTER", "file": "src/StructureTab.tsx"},
        target_root="C:/external/repo",
        explicit_filter="StructureTab",
    )
    assert mcp_server._default_agent_scope_allows(
        {"project": "LINGUASCRIBE_MASTER", "file": "src/StructureTab.tsx"},
        target_root="C:/external/repo",
        explicit_filter="LINGUASCRIBE_MASTER::src/StructureTab.tsx",
    )


def test_violation_queue_trust_does_not_inherit_unconsumed_quality_or_signal_freshness() -> None:
    required = [
        "artifact_present:atlas",
        "artifact_present:analysis_scope_authority",
        "artifact_present:audit_report",
        "sqlite_primary:atlas",
        "sqlite_primary:analysis_scope_authority",
        "sqlite_primary:audit_report",
        "scope_authority:present",
        "scope_authority:claim_usable",
        "scope_authority:audit_identity_matches",
        "audit_scope:present",
        "audit_scope:atlas_project_count_matches",
        "audit_scope:audited_projects_are_atlas_subset",
        "audit_scope:violation_projects_are_audited_subset",
        "freshness:audit_not_older_than_atlas",
        "freshness:scope_not_older_than_atlas",
    ]
    trust = {
        "status": "FAIL",
        "checks": [
            *[
                {"name": name, "passed": True, "severity": "error", "details": "current"}
                for name in required
            ],
            {
                "name": "freshness:quality_gate_not_older_than_audit",
                "passed": False,
                "severity": "warning",
                "details": "stale",
            },
            {
                "name": "freshness_contract:agent_surface_action_chain_not_stale",
                "passed": False,
                "severity": "error",
                "details": "signals stale",
            },
        ],
        "scope": {"audited_projects": ["MAIN"]},
        "freshness": {"atlas_mtime": 1, "audit_report_mtime": 2},
    }

    projected = mcp_server._audit_queue_trust_projection(trust)

    assert projected["status"] == "PASS"
    assert projected["profile"] == "audit_queue"
    assert "does not prove Quality Gate" in projected["claim_boundary"]


def test_structured_blocked_empty_result_does_not_require_invented_target() -> None:
    sample = {
        "body": json.dumps(
            {
                "status": "INCOMPLETE_EVIDENCE",
                "items": [],
                "target_proof": {
                    "verdict": "BLOCKED",
                    "required_action": "Refresh target proof.",
                },
                "policy_boundary": {
                    "mutation_allowed_by_this_packet": False,
                },
            }
        )
    }

    assert is_evidence_blocked_empty_result(sample) is True
    assert (
        target_visibility_status(sample, "", {"allowed_no_review_target_markers": []})
        == "fail_closed_or_clean_no_direct_target"
    )


def test_markdown_blocked_empty_result_does_not_require_invented_target() -> None:
    sample = {
        "body": """# Queue
```yaml
status: "INCOMPLETE_EVIDENCE"
target_proof:
  verdict: "BLOCKED"
  required_action: "Refresh target proof."
policy_boundary:
  mutation_allowed_by_this_packet: false
items:
  []
```
"""
    }

    assert is_evidence_blocked_empty_result(sample) is True


def test_silent_or_mutating_empty_result_still_requires_target() -> None:
    silent = {
        "body": json.dumps(
            {
                "status": "INCOMPLETE_EVIDENCE",
                "items": [],
                "target_proof": {"verdict": "BLOCKED"},
                "policy_boundary": {
                    "mutation_allowed_by_this_packet": False,
                },
            }
        )
    }
    mutating = {
        "body": json.dumps(
            {
                "status": "INCOMPLETE_EVIDENCE",
                "items": [],
                "target_proof": {
                    "verdict": "BLOCKED",
                    "required_action": "Refresh target proof.",
                },
                "policy_boundary": {
                    "mutation_allowed_by_this_packet": True,
                },
            }
        )
    }

    assert is_evidence_blocked_empty_result(silent) is False
    assert is_evidence_blocked_empty_result(mutating) is False


def test_structured_review_only_empty_result_does_not_require_invented_target() -> None:
    sample = {
        "body": json.dumps(
            {
                "status": "ACCEPTED",
                "total_candidates": 0,
                "items": [],
                "target_proof": {
                    "verdict": "REVIEW_REQUIRED",
                    "root_binding": "BOUND",
                    "cockpit": {
                        "freshness": "CURRENT",
                        "snapshot_binding": "BOUND",
                    },
                    "unknowns": [],
                    "blocking_evidence": [],
                },
                "policy_boundary": {
                    "human_approval_required": True,
                    "safe_default": "review_only",
                    "mutation_allowed_by_this_packet": False,
                },
                "validation": {"mode": "review_only_until_human_approval"},
            }
        )
    }

    assert is_review_only_empty_result(sample) is True
    assert (
        target_visibility_status(sample, "", {"allowed_no_review_target_markers": []})
        == "fail_closed_or_clean_no_direct_target"
    )


def test_markdown_review_only_empty_result_does_not_require_invented_target() -> None:
    sample = {
        "body": """# Merge Review Queue
```yaml
status: \"ACCEPTED\"
total_candidates: 0
target_proof:
  verdict: \"REVIEW_REQUIRED\"
  root_binding: \"BOUND\"
  cockpit_freshness: \"CURRENT\"
  cockpit_snapshot_binding: \"BOUND\"
policy_boundary:
  human_approval_required: true
  safe_default: review_only
  mutation_allowed_by_this_packet: false
items:
  []
validation:
  mode: \"review_only_until_human_approval\"
```
"""
    }

    assert is_review_only_empty_result(sample) is True


def test_nonempty_or_mutating_review_queue_still_requires_target() -> None:
    base = {
        "status": "ACCEPTED",
        "total_candidates": 0,
        "items": [],
        "target_proof": {
            "verdict": "REVIEW_REQUIRED",
            "root_binding": "BOUND",
            "cockpit": {"freshness": "CURRENT", "snapshot_binding": "BOUND"},
            "unknowns": [],
            "blocking_evidence": [],
        },
        "policy_boundary": {
            "human_approval_required": True,
            "safe_default": "review_only",
            "mutation_allowed_by_this_packet": False,
        },
        "validation": {"mode": "review_only_until_human_approval"},
    }
    nonempty = {**base, "total_candidates": 1, "items": [{"source_file": "variation.ts"}]}
    mutating = {
        **base,
        "policy_boundary": {**base["policy_boundary"], "mutation_allowed_by_this_packet": True},
    }
    malformed_count = {**base, "total_candidates": "not-a-count"}

    assert is_review_only_empty_result({"body": json.dumps(nonempty)}) is False
    assert is_review_only_empty_result({"body": json.dumps(mutating)}) is False
    assert is_review_only_empty_result({"body": json.dumps(malformed_count)}) is False


def test_structured_precondition_block_requires_action_boundary_and_expected_tool() -> None:
    sample = {
        "body": json.dumps(
            {
                "status": "INVALID_CONTEXT",
                "blocking": True,
                "tool": "get_surgical_operation_packet",
                "required_action": "Run target analysis.",
                "claim_boundary": "No surgical claim is available before analysis.",
            }
        )
    }

    assert (
        is_structured_precondition_block(
            sample,
            expected_tool="get_surgical_operation_packet",
        )
        is True
    )
    assert is_structured_precondition_block(sample, expected_tool="inspect_file") is False


def test_arbitrary_or_incomplete_error_is_not_a_precondition_block() -> None:
    arbitrary_error = {"body": json.dumps({"status": "ERROR", "blocking": True})}
    missing_boundary = {
        "body": json.dumps(
            {
                "status": "INVALID_CONTEXT",
                "blocking": True,
                "tool": "get_surgical_operation_packet",
                "required_action": "Run target analysis.",
            }
        )
    }

    assert is_structured_precondition_block(arbitrary_error) is False
    assert is_structured_precondition_block(missing_boundary) is False


def test_complete_precondition_block_needs_no_invented_review_target() -> None:
    complete = {
        "body": """# Invalid Repository Context
status: INVALID_CONTEXT
blocking: true
tool: get_violation_work_queue
artifact_trust: FAIL
required_action: Refresh target analysis.
claim_boundary: No queue item is served from stale evidence.
"""
    }
    incomplete = {
        "body": """# Invalid Repository Context
status: INVALID_CONTEXT
blocking: true
tool: get_violation_work_queue
required_action: Refresh target analysis.
"""
    }

    assert target_visibility_status(complete, "", {}) == "fail_closed_or_clean_no_direct_target"
    assert target_visibility_status(incomplete, "", {}) == "missing_review_target"


def test_structured_precondition_markdown_preserves_fail_closed_semantics() -> None:
    sample = {
        "body": """# Invalid Repository Context
status: INCOMPLETE_EVIDENCE
blocking: true
tool: get_merge_review_queue
artifact_trust: FAIL
required_action: Refresh target analysis.
claim_boundary: No merge item is available from stale evidence.
"""
    }

    assert (
        is_structured_precondition_block(
            sample,
            expected_tool="get_merge_review_queue",
        )
        is True
    )


def test_successful_surgical_packet_is_distinct_from_safe_precondition_block() -> None:
    successful = {
        "body": """# Repository Surgical Brief
analysis_root: C:/target
inspect_first:
  - src/App.tsx
"""
    }
    blocked = {
        "body": """# Invalid Repository Context
status: INCOMPLETE_EVIDENCE
blocking: true
tool: get_surgical_operation_packet
required_action: Run target analysis.
claim_boundary: No surgical claim is available.
"""
    }

    assert is_successful_surgical_packet(successful) is True
    assert is_successful_surgical_packet(blocked) is False
    assert is_structured_precondition_block(
        blocked,
        expected_tool="get_surgical_operation_packet",
    ) is True


def test_external_full_analysis_reuses_current_primary_packet_without_second_run(
    tmp_path,
    monkeypatch,
) -> None:
    cli_calls = []
    monkeypatch.setattr(mcp_server, "_valid_external_target", lambda _root: tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "_resolve_mcp_execution_identity",
        lambda **_kwargs: {"system_scope": "SAGE_ON_REPOSITORY"},
    )
    monkeypatch.setattr(
        mcp_server,
        "_run_cli",
        lambda *args, **_kwargs: cli_calls.append(args) or "OK",
    )
    monkeypatch.setattr(mcp_server, "_run_python_script", lambda *_args: "OK")
    monkeypatch.setattr(mcp_server, "get_surgical_operation_packet", lambda **_kwargs: "ready")
    monkeypatch.setattr(
        mcp_server,
        "is_successful_surgical_packet",
        lambda sample: sample["body"] == "ready",
    )
    monkeypatch.setattr(mcp_server, "_read_text_artifact", lambda *_args: "")

    result = json.loads(
        mcp_server.run_external_target_analysis(
            str(tmp_path),
            full=True,
            include_brief=True,
        )
    )

    assert result["status"] == "PASS"
    assert result["context_closure_requested"] is True
    assert result["context_closure_succeeded"] is True
    assert result["context_closure_mode"] == "reused_current_primary_full_run"
    assert len(cli_calls) == 1
    assert "--full" in cli_calls[0]
    assert "AI Context Generator" in cli_calls[0]
    assert "--ai-context" in cli_calls[0]


def test_external_full_analysis_retries_self_contained_context_only_when_packet_is_not_current(
    tmp_path,
    monkeypatch,
) -> None:
    cli_calls = []
    packets = iter(["blocked", "ready"])
    monkeypatch.setattr(mcp_server, "_valid_external_target", lambda _root: tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "_resolve_mcp_execution_identity",
        lambda **_kwargs: {"system_scope": "SAGE_ON_REPOSITORY"},
    )
    monkeypatch.setattr(
        mcp_server,
        "_run_cli",
        lambda *args, **_kwargs: cli_calls.append(args) or "OK",
    )
    monkeypatch.setattr(mcp_server, "_run_python_script", lambda *_args: "OK")
    monkeypatch.setattr(
        mcp_server,
        "get_surgical_operation_packet",
        lambda **_kwargs: next(packets),
    )
    monkeypatch.setattr(
        mcp_server,
        "is_successful_surgical_packet",
        lambda sample: sample["body"] == "ready",
    )
    monkeypatch.setattr(mcp_server, "_read_text_artifact", lambda *_args: "")

    result = json.loads(
        mcp_server.run_external_target_analysis(
            str(tmp_path),
            full=True,
            include_brief=True,
        )
    )

    assert result["status"] == "PASS"
    assert result["context_closure_mode"] == "agent_context_recovery"
    assert len(cli_calls) == 2
    assert cli_calls[1][:-1] == cli_calls[0]
    assert cli_calls[1][-1] == "--refresh"
    assert "AI Context Generator" in cli_calls[1]
    assert result["surgical_packet"] == "ready"


def _primary_followup_profile(monkeypatch, profile):
    visible = mcp_server.project_mcp_tool_names(ROOT, profile)["visible_tools"]
    monkeypatch.setattr(mcp_server.mcp, "active_tool_profile", profile)
    monkeypatch.setattr(mcp_server.mcp, "_visible_tool_names", frozenset(visible))
    return set(visible)


def _primary_followup_from_body(body):
    if body.lstrip().startswith("{"):
        return json.loads(body)["primary_tool_followup"]
    line = next(line for line in body.splitlines() if line.startswith("primary_tool_followup: "))
    return json.loads(line.removeprefix("primary_tool_followup: "))


def _assert_primary_followup(route, target_root, visible):
    policy = mcp_server.merge_review_validation_policy()
    canonical_profile = mcp_server._agent_surface_contract()["ai_agent_readiness"]["default_tool_context_profile"]
    assert route["required_profile"] == canonical_profile
    assert route["target_root"] == target_root
    assert route["execution_authority"] == "not_granted"
    assert route["call_templates_are_not_bound_calls"] is True
    assert "separate configured connection" in route["connection_rule"]
    assert "same" in route["connection_rule"] and "target" in route["connection_rule"]
    assert route["profile_config_command_argv"] == ["python", "sage.py", "mcp", "--profile", canonical_profile, "--print-config"]
    assert [(r["tool"], r["call_template"]) for r in route["tools"]] == [
        (r["tool"], r["call"]) for r in policy["pre_approval_tools"]]
    for row in route["tools"]:
        call = ast.parse(row["call_template"], mode="eval").body
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        assert call.func.id == row["tool"]
        kwargs = {k.arg: ("MAIN::src/Örnek.tsx" if isinstance(k.value, ast.Name)
                          else ast.literal_eval(k.value)) for k in call.keywords}
        kwargs["target_root"] = target_root
        inspect.signature(getattr(mcp_server, row["tool"])).bind(**kwargs)
        assert row["callable_in_current_profile"] == (row["tool"] in visible)
        assert row["callable_in_required_profile"] is True
        assert row["availability"] == ("current_profile" if row["tool"] in visible else "profile_transition_required")
        assert "arguments" not in row


@pytest.mark.parametrize("profile", ["target_repository_default", "target_repository_followup"])
@pytest.mark.parametrize("target_root", ["", 'C:/fixture/Örnek repo; "scope"'])
def test_primary_followup_routes_from_actual_connection_without_expanding_profiles(monkeypatch, profile, target_root):
    visible = _primary_followup_profile(monkeypatch, profile)
    before = deepcopy(mcp_server._agent_surface_contract())
    route = mcp_server._target_primary_tool_followup(target_root)
    _assert_primary_followup(route, target_root, visible)
    assert mcp_server._agent_surface_contract() == before
    assert set(mcp_server.mcp._visible_tool_names) == visible


@pytest.mark.parametrize("profile", ["target_repository_default", "target_repository_followup"])
@pytest.mark.parametrize("output_format", ["brief", "json"])
@pytest.mark.parametrize("candidate", [False, True])
def test_primary_followup_real_merge_queue_uses_canonical_argument_templates(tmp_path, monkeypatch, profile, output_format, candidate):
    from tools.core.analysis_snapshot_lineage import payload_sha256
    visible = _primary_followup_profile(monkeypatch, profile)
    raw = tmp_path / ".raw"
    raw.mkdir()
    target_root = str(tmp_path / "Örnek repo")
    cockpit = {"decisions": [{"candidate": "card", "action": "Import With Review",
        "source": "VARIANT", "target_path": "src/Card.tsx",
        "evidence": {"source_contract_file": "VARIANT::src/Card.tsx"}}] if candidate else []}
    (raw / "merge_decision_cockpit.json").write_text(json.dumps(cockpit), encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: root)
    monkeypatch.setattr(mcp_server, "_ensure_agent_artifact_chain_current", lambda *a, **k: {"status": "PASS"})
    monkeypatch.setattr("tools.core.target_repository_proof.build_target_repository_proof", lambda **k: {
        "summary": {"verdict": "APPROVAL_REQUIRED"},
        "evidence": [{"artifact_id": "merge_decision_cockpit", "content_sha256": payload_sha256(cockpit)}]})
    original_load = mcp_server._load_json
    monkeypatch.setattr(mcp_server, "_load_json", lambda path: deepcopy(cockpit)
                        if path.name == "merge_decision_cockpit.json" else original_load(path))
    monkeypatch.setattr(mcp_server, "_target_file_from_ref", lambda *a: "src/Card.tsx")
    monkeypatch.setattr(mcp_server, "_resolve_target_node_from_raw", lambda *a: (
        "VARIANT::src/Card.tsx", {"project": "VARIANT", "file": "src/Card.tsx"}))
    monkeypatch.setattr(mcp_server, "_target_path_status", lambda *a, **k: {})
    body = mcp_server.get_merge_review_queue(target_root=target_root, format=output_format)
    route = _primary_followup_from_body(body)
    _assert_primary_followup(route, target_root, visible)
    assert "inspect_file(target=" not in body
    assert "file_path=source_ref" in body
    if output_format == "json":
        payload = json.loads(body)
        assert payload["policy_boundary"]["mutation_allowed_by_this_packet"] is False
        assert len(payload["items"]) == int(candidate)
        assert all(row["call"] in payload["do"][1] for row in payload["validation"]["pre_approval_tools"])
    else:
        assert "mutation_allowed_by_this_packet: false" in body


@pytest.mark.parametrize("profile", ["target_repository_default", "target_repository_followup"])
@pytest.mark.parametrize("output_format", ["brief", "json"])
@pytest.mark.parametrize("external", [False, True])
def test_primary_followup_real_operator_packet_keeps_target_connection_binding(tmp_path, monkeypatch, profile, output_format, external):
    visible = _primary_followup_profile(monkeypatch, profile)
    raw = tmp_path / ".raw"
    raw.mkdir()
    (raw / "nexora_operator_packet.json").write_text("{}", encoding="utf-8")
    target_root = str(tmp_path / "Örnek repo") if external else ""
    surface = {"analysis_root": str(mcp_server.ANALYZED_REPOSITORY_ROOT),
        "directives": [{"id": "orientation", "target_files": ["src/Örnek.tsx"]}]}
    before = deepcopy(surface)
    monkeypatch.setattr(mcp_server, "RAW_DIR", raw)
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    original_load = mcp_server._load_json
    monkeypatch.setattr(mcp_server, "_load_json", lambda path: {"target_repository_agent_surface": deepcopy(surface)}
                        if path.name == "nexora_operator_packet.json" else original_load(path))
    monkeypatch.setattr(mcp_server, "_run_cli", lambda *a, **k: pytest.fail("Read-only advice ran CLI"))
    body = mcp_server.get_operator_packet(target_root=target_root, format=output_format)
    _assert_primary_followup(_primary_followup_from_body(body), target_root, visible)
    assert surface == before
    if output_format == "brief":
        assert "Inspect the bounded evidence only" in body
        machine_line = next(line for line in body.splitlines() if line.startswith("  machine_projection: "))
        call = ast.parse(json.loads(machine_line.split(": ", 1)[1]), mode="eval").body
        arguments = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
        inspect.signature(mcp_server.get_operator_packet).bind(**arguments)
        assert arguments == {"format": "json", "target_root": target_root}


@pytest.mark.parametrize("profile", ["target_repository_followup"])
@pytest.mark.parametrize("stale", [False, True])
def test_primary_followup_watchdog_preserves_currentness_and_exact_root(tmp_path, monkeypatch, profile, stale):
    visible = _primary_followup_profile(monkeypatch, profile)
    raw = tmp_path / ".raw"
    raw.mkdir()
    (raw / "watchdog_session.json").write_text("{}", encoding="utf-8")
    target_root = str(tmp_path / "Örnek repo")
    session = {"summary": {"input_origin": "explicit_files"}, "producer": {"kind": "fixture", "command": ["watch"]},
        "target_descriptor": {"system_scope": "SAGE_ON_REPOSITORY", "acquisition_mode": "external_target",
            "profile_id": "target_repository_default", "subject_root": target_root, "artifact_strategy": "external_target"},
        "path_contract": {"analysis_root": target_root}, "input_files": ["src/Örnek.tsx"]}
    monkeypatch.setattr(mcp_server, "_watchdog_session_roots", lambda *a: (raw, tmp_path / "reports", target_root))
    original_load = mcp_server._load_json
    monkeypatch.setattr(mcp_server, "_load_json", lambda path: deepcopy(session) if path.name == "watchdog_session.json"
                        else {"summary": {"current_violation_count": 0}} if path.name == "watchdog_pulse_ledger.json" else original_load(path))
    monkeypatch.setattr(mcp_server, "_raw_artifact_source_mtime", lambda name, path: 2 if stale and name == "watchdog_pulse_ledger" else 1)
    monkeypatch.setattr(mcp_server, "_record_mcp_call_result", lambda name, started, result, **k: result)
    monkeypatch.setattr(mcp_server, "_run_cli", lambda *a, **k: pytest.fail("Read-only advice ran CLI"))
    body = mcp_server.get_watchdog_session(target_root=target_root)
    _assert_primary_followup(_primary_followup_from_body(body), target_root, visible)
    assert ("historical_session_not_current" in body) == stale
    assert "not repo-wide release proof" in body


@pytest.mark.parametrize("profile", ["target_repository_default", "target_repository_followup"])
@pytest.mark.parametrize("output_format", ["brief", "json"])
def test_primary_followup_actual_folder_single_target_branch_is_routed(tmp_path, monkeypatch, profile, output_format):
    visible = _primary_followup_profile(monkeypatch, profile)
    target_root = str(tmp_path)
    (tmp_path / "A.ts").write_text("export function A() {}", encoding="utf-8")
    payload = {"target": {"kind": "folder", "value": "."}, "files": [{"project": "MAIN", "file": "A.ts"}],
        "target_files": ["A.ts"], "target_refs": ["MAIN::A.ts"],
        "atlas_symbols": [{"project": "MAIN", "file": "A.ts", "symbol": "A", "line": 1, "end_line": 1, "target_ref": "MAIN::A.ts"}],
        "target_path_status": {"target_spans": [{"symbol": "A", "start_line": 1, "end_line": 1}], "target_source_snippets": []}}
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: tmp_path / ".raw")
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    monkeypatch.setattr(mcp_server, "write_inspection", lambda *a, **k: deepcopy(payload))
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: root)
    monkeypatch.setattr(mcp_server, "_add_agent_inspection_path_fields", lambda *a: None)
    monkeypatch.setattr(mcp_server, "_add_inspection_grounding_status", lambda *a, **k: None)
    body = mcp_server.inspect_folder(".", target_root=target_root, format=output_format)
    _assert_primary_followup(_primary_followup_from_body(body), target_root, visible)
    if output_format == "brief":
        assert "call inspect_file(target_file, line_start=start_line, line_end=end_line)" in body


def test_primary_followup_reads_changed_central_template_not_duplicated_prose(monkeypatch):
    _primary_followup_profile(monkeypatch, "target_repository_followup")
    policy = mcp_server.merge_review_validation_policy()
    policy["pre_approval_tools"][1]["call"] = "get_impact_radius(target_node=source_ref, depth=3)"
    monkeypatch.setattr(mcp_server, "merge_review_validation_policy", lambda: deepcopy(policy))
    body = mcp_server._render_merge_review_queue_brief({"validation": policy})
    assert policy["pre_approval_tools"][1]["call"] in body.split("do:", 1)[1]
    assert "depth=3" in _primary_followup_from_body(body)["tools"][1]["call_template"]


@pytest.mark.parametrize("case", ["missing", "invalid", "non_object"])
def test_primary_followup_missing_profile_contract_fails_closed(monkeypatch, case):
    _primary_followup_profile(monkeypatch, "target_repository_followup")
    contract = deepcopy(mcp_server._agent_surface_contract())
    contract["ai_agent_readiness"]["default_tool_context_profile"] = "" if case == "missing" else "not-a-profile"
    if case == "non_object":
        contract["ai_agent_readiness"] = ["not-a-contract"]
    monkeypatch.setattr(mcp_server, "_agent_surface_contract", lambda: contract)
    route = mcp_server._target_primary_tool_followup("")
    assert route["status"] == "unavailable" and route["tools"] == []
    assert "profile_config_command_argv" not in route
    assert route["execution_authority"] == "not_granted"


@pytest.mark.parametrize("verdict", ["BLOCKED", "UNKNOWN"])
@pytest.mark.parametrize("output_format", ["brief", "json"])
def test_primary_followup_does_not_turn_blocked_merge_proof_into_candidates(tmp_path, monkeypatch, verdict, output_format):
    _primary_followup_profile(monkeypatch, "target_repository_followup")
    target_root = str(tmp_path / "Örnek repo")
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: tmp_path / ".raw")
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: root)
    monkeypatch.setattr(mcp_server, "_ensure_agent_artifact_chain_current", lambda *a, **k: {"status": "PASS"})
    monkeypatch.setattr("tools.core.target_repository_proof.build_target_repository_proof",
                        lambda **k: {"summary": {"verdict": verdict}})
    body = mcp_server.get_merge_review_queue(target_root=target_root, format=output_format)
    assert "INCOMPLETE_EVIDENCE" in body
    if output_format == "json":
        payload = json.loads(body)
        assert payload["items"] == [] and payload["policy_boundary"]["mutation_allowed_by_this_packet"] is False
    else:
        route = _primary_followup_from_body(body)
        assert route["target_root"] == target_root
        assert "mutation_allowed_by_this_packet: false" in body


def test_primary_followup_surgical_context_does_not_use_registered_long_snippet_candidate(tmp_path, monkeypatch):
    _primary_followup_profile(monkeypatch, "target_repository_followup")
    raw = tmp_path / ".raw"
    raw.mkdir()
    artifact_path_for_storage_root(raw, "atlas").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_find_symbol_matches", lambda *a, **k: [
        {"name": "large", "file": "src/A.ts", "project": "MAIN", "type": "function"}])
    monkeypatch.setattr(mcp_server, "_target_context_from_project_file", lambda *a, **k: {
        "target_file": "src/A.ts", "target_ref": "MAIN::src/A.ts"})
    monkeypatch.setattr(mcp_server, "_bounded_source_snippets", lambda *a, **k: pytest.fail("Unexpected snippet branch"))
    body = mcp_server.get_surgical_context("large", target_root=str(tmp_path))
    assert "matched_by_search" in body and "MAIN::src/A.ts" in body
    assert "inspect_file(" not in body


def test_external_full_analysis_fails_closed_when_recovery_packet_remains_not_current(
    tmp_path,
    monkeypatch,
) -> None:
    cli_calls = []
    monkeypatch.setattr(mcp_server, "_valid_external_target", lambda _root: tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "_resolve_mcp_execution_identity",
        lambda **_kwargs: {"system_scope": "SAGE_ON_REPOSITORY"},
    )
    monkeypatch.setattr(
        mcp_server,
        "_run_cli",
        lambda *args, **_kwargs: cli_calls.append(args) or "OK",
    )
    monkeypatch.setattr(mcp_server, "_run_python_script", lambda *_args: "OK")
    monkeypatch.setattr(
        mcp_server,
        "get_surgical_operation_packet",
        lambda **_kwargs: "blocked",
    )
    monkeypatch.setattr(
        mcp_server,
        "is_successful_surgical_packet",
        lambda sample: sample["body"] == "ready",
    )
    monkeypatch.setattr(mcp_server, "_read_text_artifact", lambda *_args: "")

    result = json.loads(
        mcp_server.run_external_target_analysis(
            str(tmp_path),
            full=True,
            include_brief=True,
        )
    )

    assert result["status"] == "FAIL"
    assert result["context_closure_mode"] == "agent_context_recovery"
    assert result["context_closure_succeeded"] is True
    assert result["surgical_packet_succeeded"] is False
    assert len(cli_calls) == 2

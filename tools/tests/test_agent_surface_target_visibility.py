import json
from pathlib import Path

from tools.mcp import server as mcp_server
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

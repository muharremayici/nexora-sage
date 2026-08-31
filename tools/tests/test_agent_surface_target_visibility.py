import json
from pathlib import Path

from tools.mcp import server as mcp_server
from tools.core.agent_surface_target_visibility import (
    is_evidence_blocked_empty_result,
    is_structured_precondition_block,
    is_successful_surgical_packet,
    target_visibility_status,
)


ROOT = Path(__file__).resolve().parents[2]


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
        "artifact_present:audit_report",
        "sqlite_primary:atlas",
        "sqlite_primary:audit_report",
        "audit_scope:present",
        "audit_scope:atlas_project_count_matches",
        "audit_scope:audited_projects_are_atlas_subset",
        "audit_scope:violation_projects_are_audited_subset",
        "freshness:audit_not_older_than_atlas",
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

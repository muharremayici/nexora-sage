import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from tools.mcp import server


def test_patch_applicability_timeout_remains_unknown_and_does_not_prescribe_payload_repair(tmp_path: Path) -> None:
    timed_out = subprocess.CompletedProcess(
        ["git", "apply", "--check"],
        returncode=124,
        stdout="",
        stderr="Timed out after 30.0s",
    )
    with patch.object(server, "run_observed_subprocess", return_value=(timed_out, 30.0)):
        applicability = server._check_exact_patch_applicability(
            "@@ -1,1 +1,1 @@\n-old\n+new\n",
            tmp_path,
        )

    assert applicability["status"] == "UNKNOWN"
    assert applicability["timed_out"] is True
    assert applicability["reason"] == "patch_applicability_oracle_timeout"

    brief = server._render_patch_validation_brief(
        {
            "status": "PASS",
            "violations": [],
            "target_exists": True,
            "target_indexed": True,
            "target_file": "src/example.ts",
            "target_ref": "MAIN::src/example.ts",
            "patch_applicability": applicability,
            "target_native_policy_validation": {"status": "NOT_RUN"},
        },
        "src/example.ts",
    )

    assert 'next_action: "retry_bounded_applicability_or_request_review"' in brief
    assert 'status: "INCOMPLETE_EVIDENCE"' in brief
    assert "repair_exact_patch_payload_before_apply" not in brief
    assert "incomplete evidence" in brief.lower()
    assert "safe_to_apply: false" in brief


def test_patch_validation_never_exposes_overall_pass_when_applicability_is_unknown(tmp_path: Path) -> None:
    with (
        patch.object(
            server,
            "_target_path_status",
            return_value={
                "target_ref": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "exists": True,
                "indexed": True,
                "inside_root": True,
            },
        ),
        patch.object(server, "_analysis_snapshot_id", return_value="snapshot-20"),
        patch.object(server, "_operator_privileged_projection_allowed", return_value=False),
        patch.object(
            server,
            "resolve_execution_identity",
            return_value={"system_scope": "target_repository", "subject_root": str(tmp_path)},
        ),
        patch(
            "tools.engines.mcp_governance_engine.validate_proposed_patch",
            return_value={
                "status": "PASS",
                "safe_to_apply": True,
                "violations": [],
                "existing_violation_count": 0,
                "resolved_violation_count": 0,
            },
        ),
        patch(
            "tools.core.seal_impact_guard.mutating_agent_surface_block",
            return_value={"blocked": False},
        ),
        patch.object(
            server,
            "_check_exact_patch_applicability",
            return_value={
                "status": "UNKNOWN",
                "oracle": "git_apply_check",
                "returncode": 124,
                "timed_out": True,
                "mutation_performed": False,
                "reason": "patch_applicability_oracle_timeout",
            },
        ),
    ):
        payload = server.validate_patch(
            "src/example.ts",
            "@@ -1,1 +1,1 @@\n-old\n+new",
            format="json",
        )

    parsed = json.loads(payload)
    assert parsed["status"] == "INCOMPLETE_EVIDENCE"
    assert parsed["overall_status"] == "INCOMPLETE_EVIDENCE"
    assert parsed["governance_status"] == "PASS"
    assert parsed["governance_validation_passed"] is True
    assert parsed["safe_to_apply"] is False
    assert parsed["application_readiness"] == "INCOMPLETE_APPLICABILITY_EVIDENCE"

import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import codemaps

from tools.core.pipeline_run_receipts import pipeline_run_status, start_pipeline_run_receipt


def _start(
    temp_dir: str,
    *,
    run_id: str,
    pid: int | None = None,
    acquisition_mode: str = "DEFAULT_WORKSPACE",
):
    root = Path(temp_dir)
    return start_pipeline_run_receipt(
        command_profile="run:test-receipt",
        scope="SAGE_ON_REPOSITORY",
        projects=["MAIN"],
        context={"acquisition_mode": acquisition_mode, "subject_root": "private-target"},
        run_id=run_id,
        db_path=root / "codemaps.db",
        shadow_path=root / "pipeline_run_receipt.json",
        pid=pid,
    )


def _mark_process_exit_ready(recorder) -> None:
    shadow_details = {
        "shadow_worker_started_count": 1,
        "shadow_worker_completed_count": 1,
        "shadow_worker_failed_count": 0,
        "shadow_worker_pending_count": 0,
        "shadow_worker_global_pending_count": 0,
        "shadow_worker_ids": ["shadow-test"],
        "shadow_artifacts": ["atlas"],
        "shadow_worker_identities_truncated": False,
        "shadow_flush_timed_out": False,
        "shadow_flush_timeout_seconds": 15,
        "shadow_flush_wait_seconds": 0.25,
    }
    recorder.progress("pipeline_completed")
    recorder.progress("evidence_closeout", **shadow_details)
    recorder.progress("process_exit_ready", **shadow_details)


def test_active_receipt_blocks_duplicate_retry_and_survives_lost_console() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-active")
        assert recorder is not None
        payload = pipeline_run_status("sage-run-active", db_path=Path(temp_dir) / "codemaps.db")
        assert payload["status"] == "ACTIVE"
        assert payload["run_id"] == "sage-run-active"
        assert "Do not retry" in payload["retry_guidance"]
        assert payload["duration_guidance"]["basis"] == "insufficient_exact_profile_samples"


def test_receipt_projects_exact_claim_owned_execution_plan() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-target-quality")
        assert recorder is not None
        recorder.progress(
            "execution_planned",
            active_steps=["Atlas", "Quality Gates"],
            execution_claim_profile="target-quality",
            execution_claim_target_step="Quality Gates",
            execution_claim_step_count=19,
            execution_claim_boundary="bounded_target_static_quality",
            execution_claim_release_authority=False,
            execution_claim_projects=["MAIN"],
            execution_claim_excluded_steps=["uiruntimecontractanalyzer"],
            execution_claim_cost_status="UNKNOWN",
            execution_claim_cost_basis="insufficient_exact_profile_samples",
        )

        payload = pipeline_run_status(
            "sage-run-target-quality",
            db_path=Path(temp_dir) / "codemaps.db",
        )

        assert payload["execution_claim_plan"]["profile"] == "target-quality"
        assert payload["execution_claim_plan"]["step_count"] == 19
        assert payload["execution_claim_plan"]["claim_boundary"] == "bounded_target_static_quality"
        assert payload["execution_claim_plan"]["project_scope"]["requested_projects"] == ["MAIN"]
        assert payload["execution_claim_plan"]["excluded_direct_dependency_slugs"] == [
            "uiruntimecontractanalyzer"
        ]


def test_terminal_pass_is_execution_only_not_engineering_proof() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-pass")
        assert recorder is not None
        with patch("tools.core.pipeline_run_receipts.record_execution_duration"):
            recorder.progress("step_started", step_id="Atlas", step_status="running")
            recorder.progress("step_completed", step_id="Atlas", step_status="success")
            _mark_process_exit_ready(recorder)
            recorder.terminal(
                terminal_status="PASS",
                exit_code=0,
                governance_verdict="PASS",
                completed_steps=["Atlas"],
                evidence_identities=["atlas"],
                scope_details={
                    "scope_topology_authority_id": "sha256:topology-test",
                    "scope_authority_id": "sha256:test",
                    "scope_evidence_status": "BOUNDED_PROJECT_SELECTION",
                    "scope_claim": "explicit_project_selection",
                    "scope_full_repository_claim_eligible": False,
                    "scope_effective_project_count": 1,
                    "scope_mismatch_reasons": [],
                    "scope_consumer_checks": ["audit:CONSISTENT"],
                    "scope_evidence_artifact": "analysis_scope_authority",
                },
            )
        payload = pipeline_run_status("sage-run-pass", db_path=Path(temp_dir) / "codemaps.db")
        assert payload["status"] == "PASS"
        assert payload["terminal"]["exit_code"] == 0
        assert payload["steps"] == {"Atlas": "success"}
        assert payload["evidence_identities"] == ["atlas"]
        assert payload["lifecycle"]["pipeline_completed_at"]
        assert payload["lifecycle"]["evidence_closeout_at"]
        assert payload["lifecycle"]["process_exit_ready_at"]
        assert payload["lifecycle"]["terminal_at"]
        assert payload["shadow_write_closeout"]["completed_count"] == 1
        assert payload["shadow_write_closeout"]["pending_count"] == 0
        assert payload["engineering_claim_validity"] == "not_evaluated_by_execution_receipt"
        assert payload["analysis_scope_evidence"]["topology_authority_id"] == "sha256:topology-test"
        assert payload["analysis_scope_evidence"]["evidence_status"] == "BOUNDED_PROJECT_SELECTION"
        assert payload["analysis_scope_evidence"]["effective_project_count"] == 1
        assert payload["analysis_scope_evidence"]["consumer_checks"] == ["audit:CONSISTENT"]
        assert "does not prove repository correctness" in payload["claim_boundary"]


def test_terminal_fails_closed_before_process_exit_readiness() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-premature-terminal")
        assert recorder is not None
        with patch("tools.core.pipeline_run_receipts.record_execution_duration"):
            try:
                recorder.terminal(terminal_status="PASS", exit_code=0)
            except RuntimeError as exc:
                assert "process_exit_ready" in str(exc)
            else:
                raise AssertionError("premature terminal receipt was accepted")

        payload = pipeline_run_status(
            "sage-run-premature-terminal",
            db_path=Path(temp_dir) / "codemaps.db",
        )
        assert payload["status"] == "ACTIVE"
        assert payload["terminal"]["status"] is None


def test_missing_terminal_with_dead_process_is_not_reported_as_failure_or_safe_retry() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-crash", pid=2_147_483_647)
        assert recorder is not None
        payload = pipeline_run_status("sage-run-crash", db_path=Path(temp_dir) / "codemaps.db")
        assert payload["status"] == "INTERRUPTED_UNCONFIRMED"
        assert "Human review is required" in payload["retry_guidance"]


def test_stale_heartbeat_with_live_process_remains_active() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-stale", pid=os.getpid())
        assert recorder is not None
        payload = pipeline_run_status(
            "sage-run-stale",
            db_path=Path(temp_dir) / "codemaps.db",
            now_epoch=time.time() + 10_000,
        )
        assert payload["status"] == "ACTIVE_STALE_HEARTBEAT"
        assert "Do not retry" in payload["retry_guidance"]


def test_busy_attempt_names_the_active_run_without_authorizing_eviction() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-blocked")
        assert recorder is not None
        with patch("tools.core.pipeline_run_receipts.record_execution_duration"):
            recorder.progress(
                "blocked_by_active_run",
                active_run_id="sage-run-owner",
                active_pid=1234,
            )
            _mark_process_exit_ready(recorder)
            recorder.terminal(
                terminal_status="BLOCKED",
                exit_code=1,
                interruption_reason="PipelineBusyError",
            )
        payload = pipeline_run_status("sage-run-blocked", db_path=Path(temp_dir) / "codemaps.db")
        assert payload["status"] == "BLOCKED"
        assert payload["blocking_run"] == {"run_id": "sage-run-owner", "pid": 1234}
        assert "Review the terminal reason" in payload["retry_guidance"]


def test_receipt_persists_fingerprint_not_raw_context() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-private")
        assert recorder is not None
        raw_database = (Path(temp_dir) / "codemaps.db").read_bytes()
        assert b"private-target" not in raw_database
        assert (Path(temp_dir) / "pipeline_run_receipt.json").is_file()


def test_unknown_run_fails_closed_without_starting_work() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        payload = pipeline_run_status("missing", db_path=Path(temp_dir) / "codemaps.db")
        assert payload["status"] == "NOT_FOUND"
        assert "No execution or engineering conclusion" in payload["claim_boundary"]


def test_external_target_receipt_preserves_target_bound_status_query() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(
            temp_dir,
            run_id="sage-run-external",
            acquisition_mode="EXPLICIT_TARGET",
        )
        assert recorder is not None
        payload = pipeline_run_status("sage-run-external", db_path=Path(temp_dir) / "codemaps.db")
        assert "--target-root <repository>" in payload["status_query"]
        assert "sage-run-external" in payload["status_query"]


def test_unknown_process_liveness_never_authorizes_retry() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-unknown-process", pid=1234)
        assert recorder is not None
        with patch("tools.core.pipeline_run_receipts._process_alive", return_value=None):
            payload = pipeline_run_status(
                "sage-run-unknown-process",
                db_path=Path(temp_dir) / "codemaps.db",
            )
        assert payload["status"] == "UNKNOWN"
        assert "Do not launch a duplicate" in payload["retry_guidance"]


def test_shadow_failure_does_not_orphan_sqlite_receipt() -> None:
    with tempfile.TemporaryDirectory() as temp_dir, patch(
        "tools.core.pipeline_run_receipts.PipelineRunRecorder._write_shadow",
        side_effect=OSError("shadow unavailable"),
    ):
        recorder = _start(temp_dir, run_id="sage-run-shadow-degraded")
        assert recorder is not None
        payload = pipeline_run_status(
            "sage-run-shadow-degraded",
            db_path=Path(temp_dir) / "codemaps.db",
        )
        assert payload["status"] == "ACTIVE"


def test_duration_telemetry_failure_does_not_erase_terminal_receipt() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = _start(temp_dir, run_id="sage-run-duration-degraded")
        assert recorder is not None
        with patch(
            "tools.core.pipeline_run_receipts.record_execution_duration",
            side_effect=OSError("telemetry unavailable"),
        ):
            _mark_process_exit_ready(recorder)
            recorder.terminal(terminal_status="PASS", exit_code=0)
        payload = pipeline_run_status(
            "sage-run-duration-degraded",
            db_path=Path(temp_dir) / "codemaps.db",
        )
        assert recorder.terminal_recorded is True
        assert payload["status"] == "PASS"


def test_unbounded_run_status_wait_does_not_invent_an_outer_transport_timeout() -> None:
    args = SimpleNamespace(
        target_root=None,
        run_id="sage-run-active",
        wait=True,
        timeout_seconds=0,
        poll_seconds=5,
        json=False,
    )
    with (
        patch.object(codemaps, "_target_root_env", return_value=None),
        patch.object(codemaps, "run_command", return_value=0) as run_command,
    ):
        assert codemaps.cmd_run_status(args) == 0

    assert run_command.call_args.kwargs["timeout"] is None


def test_bounded_run_status_wait_allows_query_closeout_margin() -> None:
    args = SimpleNamespace(
        target_root=None,
        run_id="sage-run-active",
        wait=True,
        timeout_seconds=45,
        poll_seconds=5,
        json=False,
    )
    with (
        patch.object(codemaps, "_target_root_env", return_value=None),
        patch.object(codemaps, "run_command", return_value=0) as run_command,
    ):
        assert codemaps.cmd_run_status(args) == 0

    assert run_command.call_args.kwargs["timeout"] == 75.0

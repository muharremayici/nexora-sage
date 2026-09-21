import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.core.governance_trace import (
    UNKNOWN_VALUE,
    activate_trace,
    activate_trace_storage,
    current_trace_storage,
    current_trace_id,
    load_trace_events,
    record_agent_handoff_trace,
    record_trace_event,
    record_release_proof_step_trace,
    record_watchdog_session_trace,
    reset_trace,
    reset_trace_storage,
)


class GovernanceTraceTests(unittest.TestCase):
    def test_trace_records_unknowns_without_raw_task_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codemaps.db"
            event = record_trace_event(
                event_type="mcp_tool_call",
                principal="mcp_profile:target_repository_default",
                tool_name="inspect_file",
                context_fingerprint="fingerprint",
                policy_version="v1",
                outcome="success",
                failure_layer="none",
                details={"argument_keys": ["target_file"]},
                db_path=db_path,
            )
            stored = load_trace_events(trace_id=event["trace_id"], db_path=db_path)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["task_fingerprint"], UNKNOWN_VALUE)
            self.assertEqual(stored[0]["details"], {"argument_keys": ["target_file"]})

    def test_unknown_event_type_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                record_trace_event(event_type="invented", db_path=Path(temp_dir) / "codemaps.db")

    def test_trace_details_drop_undeclared_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            event = record_trace_event(
                event_type="mcp_tool_call",
                outcome="success",
                failure_layer="none",
                details={"argument_keys": ["target_file"], "raw_prompt": "do not retain this"},
                db_path=Path(temp_dir) / "codemaps.db",
            )
            self.assertEqual(event["details"]["argument_keys"], ["target_file"])
            self.assertNotIn("raw_prompt", event["details"])
            self.assertEqual(event["details"]["omitted_detail_key_count"], 1)

    def test_invalid_outcome_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                record_trace_event(
                    event_type="mcp_tool_call",
                    outcome="invented",
                    failure_layer="none",
                    db_path=Path(temp_dir) / "codemaps.db",
                )

    def test_concurrent_trace_initialization_preserves_each_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codemaps.db"

            def record(index: int) -> str:
                return record_trace_event(
                    event_type="mcp_tool_call",
                    tool_name=f"tool_{index}",
                    outcome="success",
                    failure_layer="none",
                    db_path=db_path,
                )["trace_id"]

            with ThreadPoolExecutor(max_workers=2) as pool:
                trace_ids = list(pool.map(record, [1, 2]))
            stored = load_trace_events(limit=10, db_path=db_path)
            self.assertEqual({item["trace_id"] for item in stored}, set(trace_ids))

    def test_event_specific_retention_does_not_evict_nested_trace_family(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codemaps.db"
            record_trace_event(
                event_type="agent_handoff",
                trace_id="handoff-preserved",
                outcome="success",
                failure_layer="none",
                db_path=db_path,
            )
            for trace_id in ("mcp-old", "mcp-current"):
                record_trace_event(
                    event_type="mcp_tool_call",
                    trace_id=trace_id,
                    outcome="success",
                    failure_layer="none",
                    db_path=db_path,
                    max_events=1,
                )
            stored = load_trace_events(limit=10, db_path=db_path)
            self.assertEqual({item["trace_id"] for item in stored}, {"handoff-preserved", "mcp-current"})

    def test_watchdog_trace_keeps_counts_but_not_file_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codemaps.db"
            session = {
                "summary": {"changed_files": 2, "violation_count": 1, "elapsed_seconds": 1.2},
                "target_repository_deep_proof_debt": {"status": "due"},
                "watchdog_pulse_ledger": {"pulse_id": "pulse-123"},
                "changed_files": ["src/private.ts"],
            }
            from unittest.mock import patch

            with patch("tools.core.governance_trace.RAW_DIR", Path(temp_dir)):
                event = record_watchdog_session_trace(session)
            self.assertEqual(event["trace_id"], "watchdog:pulse-123")
            self.assertEqual(event["details"]["changed_file_count"], 2)
            self.assertNotIn("changed_files", event["details"])

    def test_release_proof_trace_excludes_command_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from unittest.mock import patch

            result = {"id": "source_contracts", "started_at": "2026-01-01T00:00:00Z", "duration_seconds": 1.5, "passed": False, "required": True, "timed_out": False, "timeout_basis": "policy", "raw_artifact": "output/.raw/a.json", "raw_artifact_sha256": "digest", "stdout_tail": "do not retain"}
            with patch("tools.core.governance_trace.RAW_DIR", Path(temp_dir)):
                event = record_release_proof_step_trace(result)
            self.assertEqual(event["details"]["step_id"], "source_contracts")
            self.assertFalse(event["details"]["timed_out"])
            self.assertEqual(event["details"]["execution_status"], "COMPLETED")
            self.assertNotIn("stdout_tail", event["details"])

    def test_release_proof_trace_preserves_dependency_block_without_command_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from unittest.mock import patch

            result = {
                "id": "consumer",
                "started_at": "2026-01-01T00:00:00Z",
                "duration_seconds": 0,
                "passed": False,
                "required": True,
                "timed_out": False,
                "timeout_basis": "not_started_dependency_failure",
                "raw_artifact": None,
                "raw_artifact_sha256": None,
                "execution_status": "BLOCKED_BY_FAILED_DEPENDENCY",
                "blocked_by_failed_dependencies": ["producer"],
                "command": "must not be retained",
            }
            with patch("tools.core.governance_trace.RAW_DIR", Path(temp_dir)):
                event = record_release_proof_step_trace(result)
            self.assertEqual(
                event["state_change"],
                "release_proof_step_blocked_by_failed_dependency",
            )
            self.assertEqual(
                event["details"]["execution_status"],
                "BLOCKED_BY_FAILED_DEPENDENCY",
            )
            self.assertEqual(event["details"]["blocked_dependency_count"], 1)
            self.assertEqual(event["details"]["advisory_dependency_failure_count"], 0)
            self.assertNotIn("command", event["details"])

    def test_release_proof_trace_counts_nonblocking_dependency_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from unittest.mock import patch

            result = {
                "id": "consumer",
                "started_at": "2026-01-01T00:00:00Z",
                "duration_seconds": 1,
                "passed": True,
                "required": True,
                "timed_out": False,
                "execution_status": "COMPLETED",
                "blocked_by_failed_dependencies": [],
                "advisory_failed_dependencies": ["optional_metrics"],
            }
            with patch("tools.core.governance_trace.RAW_DIR", Path(temp_dir)):
                event = record_release_proof_step_trace(result)

            self.assertEqual(event["details"]["blocked_dependency_count"], 0)
            self.assertEqual(event["details"]["advisory_dependency_failure_count"], 1)

    def test_agent_handoff_trace_keeps_packet_metadata_without_source_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            packet = {
                "meta": {"kind": "contextos_surgical_operation_packet"},
                "summary": {"returned_focus_files": 1, "integrity": "grounded"},
                "source_grounding": {"drift_check_status": "match", "target_source_snippets": [{"code": "secret source"}]},
            }
            from unittest.mock import patch

            with patch("tools.core.governance_trace.RAW_DIR", Path(temp_dir)):
                event = record_agent_handoff_trace(packet, trace_id="sage-trace-linked")
            self.assertEqual(event["trace_id"], "sage-trace-linked")
            self.assertEqual(event["details"]["packet_kind"], "contextos_surgical_operation_packet")
            self.assertNotIn("target_source_snippets", event["details"])

    def test_call_trace_context_is_scoped_and_reset(self):
        token = activate_trace("sage-trace-scoped")
        try:
            self.assertEqual(current_trace_id(), "sage-trace-scoped")
        finally:
            reset_trace(token)
        self.assertIsNone(current_trace_id())

    def test_call_local_storage_routes_nested_trace_without_touching_default_db(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_raw = root / "default" / ".raw"
            operational_db = root / "operational" / "mcp_call_telemetry.db"
            token = activate_trace_storage(operational_db)
            try:
                with patch("tools.core.governance_trace.RAW_DIR", default_raw):
                    record_agent_handoff_trace(
                        {
                            "meta": {"kind": "contextos_surgical_operation_packet"},
                            "summary": {"returned_focus_files": 0, "integrity": "bounded"},
                            "source_grounding": {"drift_check_status": "match"},
                        },
                        trace_id="sage-trace-operational",
                    )
                    self.assertEqual(current_trace_storage(), operational_db.resolve())
            finally:
                reset_trace_storage(token)
            self.assertTrue(operational_db.exists())
            self.assertFalse((default_raw / "codemaps.db").exists())
            self.assertIsNone(current_trace_storage())

    def test_surgical_packet_exposes_only_opaque_handoff_reference_in_trace_context(self):
        from unittest.mock import patch
        from tools.mcp import server

        packet = {
            "meta": {"kind": "contextos_surgical_operation_packet"},
            "summary": {"returned_focus_files": 1, "integrity": "grounded"},
            "agent_action_directives": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            token = activate_trace("sage-trace-packet")
            try:
                with (
                    patch("tools.core.governance_trace.RAW_DIR", Path(temp_dir)),
                    patch(
                        "tools.mcp.server._ensure_agent_artifact_chain_current",
                        return_value={
                            "status": "PASS",
                            "blocking": False,
                            "checks": [
                                {"name": "artifact_present:atlas", "passed": True, "severity": "error"},
                                {"name": "sqlite_primary:atlas", "passed": True, "severity": "warning"},
                            ],
                        },
                    ),
                    patch("tools.core.contextos_mcp.build_surgical_operation_packet", return_value=packet),
                    patch(
                        "tools.core.surgical_packet_inputs.evaluate_surgical_packet_inputs",
                        return_value=(
                            {"status": "PASS", "blocked_inputs": [], "omitted_inputs": [], "inputs": []},
                            {"signals": {}, "circular_deps": {}, "live_surface_priority_pack": {}},
                        ),
                    ),
                    patch("tools.mcp.server._record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
                ):
                    rendered = server.get_surgical_operation_packet(format="json")
            finally:
                reset_trace(token)
        rendered_packet = json.loads(rendered)
        trace = rendered_packet["operation_trace"]
        self.assertEqual(trace["status"], "recorded")
        self.assertEqual(trace["trace_id"], "sage-trace-packet")
        self.assertNotIn("source", trace)

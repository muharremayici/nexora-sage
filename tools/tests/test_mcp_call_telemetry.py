import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core.mcp_call_telemetry import (
    MCP_OPERATIONAL_DB_ENV,
    activate_mcp_call_capture,
    current_mcp_call_capture,
    load_mcp_call_telemetry,
    mcp_operational_db_path,
    mcp_operational_honesty_path,
    persist_completed_mcp_call,
    record_mcp_call_result,
    reset_mcp_call_capture,
)
from tools.core.honesty_telemetry import (
    activate_honesty_telemetry_path,
    record_honesty_event,
    reset_honesty_telemetry_path,
)


class MCPCallTelemetryTests(unittest.TestCase):
    def test_record_without_active_capture_does_not_leak_into_later_calls(self) -> None:
        self.assertEqual(current_mcp_call_capture(), {})
        record_mcp_call_result("direct_python_call", time.perf_counter(), "fixture")
        self.assertEqual(current_mcp_call_capture(), {})

    def _record(self, *, db_path: Path, trace_id: str, target_root: str = "") -> None:
        with patch.dict(os.environ, {MCP_OPERATIONAL_DB_ENV: str(db_path)}):
            token = activate_mcp_call_capture()
            try:
                started = time.perf_counter()
                record_mcp_call_result(
                    "get_watchdog_session",
                    started,
                    "PRIVATE_RESPONSE_BODY",
                    status="fail_closed",
                    fail_closed_reason="watchdog_session_missing",
                )
                persist_completed_mcp_call(
                    tool_name="get_watchdog_session",
                    profile="target_repository_default",
                    arguments={"target_root": target_root, "format": "json"},
                    started=started,
                    outcome="success",
                    failure_layer="none",
                    trace_id=trace_id,
                    result="PRIVATE_RESPONSE_BODY",
                )
            finally:
                reset_mcp_call_capture(token)

    def test_product_global_sqlite_authority_excludes_target_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "operational" / "mcp_call_telemetry.db"
            target_root = str(Path(temp_dir) / "customer-secret-repository")
            self._record(db_path=db_path, trace_id="trace-a", target_root=target_root)

            with patch.dict(os.environ, {MCP_OPERATIONAL_DB_ENV: str(db_path)}):
                ledger = load_mcp_call_telemetry()

            self.assertEqual(ledger["meta"]["physical_ssot"], "SQLite governance_trace_events")
            self.assertEqual(ledger["meta"]["storage_authority"], "product_global_operational")
            self.assertEqual(len(ledger["entries"]), 1)
            self.assertEqual(ledger["entries"][0]["target_mode"], "explicit_target")
            self.assertEqual(ledger["entries"][0]["payload_chars"], len("PRIVATE_RESPONSE_BODY"))
            persisted_bytes = db_path.read_bytes()
            self.assertNotIn(target_root.encode("utf-8"), persisted_bytes)
            self.assertNotIn(b"PRIVATE_RESPONSE_BODY", persisted_bytes)
            self.assertFalse(db_path.with_name(db_path.name + "-wal").exists())
            self.assertFalse(db_path.with_name(db_path.name + "-shm").exists())
            self.assertFalse((db_path.parent / "mcp_call_telemetry_ledger.json").exists())

    def test_missing_ledger_read_is_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "missing" / "mcp_call_telemetry.db"
            with patch.dict(os.environ, {MCP_OPERATIONAL_DB_ENV: str(db_path)}):
                ledger = load_mcp_call_telemetry()
            self.assertEqual(ledger["entries"], [])
            self.assertEqual(ledger["summary_by_tool"], {})
            self.assertFalse(db_path.exists())
            self.assertFalse(db_path.parent.exists())

    def test_retention_prunes_the_physical_sqlite_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "operational" / "mcp_call_telemetry.db"
            policy = {
                "id": "mcp_call_telemetry",
                "enabled": True,
                "max_entries": 2,
                "max_summary_tools": 50,
            }
            with patch("tools.core.mcp_call_telemetry._retention_policy", return_value=policy):
                for index in range(3):
                    self._record(db_path=db_path, trace_id=f"trace-{index}")
                with patch.dict(os.environ, {MCP_OPERATIONAL_DB_ENV: str(db_path)}):
                    ledger = load_mcp_call_telemetry()
            self.assertEqual([row["trace_id"] for row in ledger["entries"]], ["trace-1", "trace-2"])

    def test_call_capture_is_context_scoped(self) -> None:
        outer = activate_mcp_call_capture()
        try:
            record_mcp_call_result("outer", time.perf_counter(), "one")
            self.assertEqual(current_mcp_call_capture()["tool_name"], "outer")
            inner = activate_mcp_call_capture()
            try:
                self.assertEqual(current_mcp_call_capture(), {})
                record_mcp_call_result("inner", time.perf_counter(), "two")
                self.assertEqual(current_mcp_call_capture()["tool_name"], "inner")
            finally:
                reset_mcp_call_capture(inner)
            self.assertEqual(current_mcp_call_capture()["tool_name"], "outer")
        finally:
            reset_mcp_call_capture(outer)
        self.assertEqual(current_mcp_call_capture(), {})

    def test_operational_paths_ignore_target_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir).resolve() / "mcp" / "telemetry.db"
            with patch.dict(
                os.environ,
                {
                    MCP_OPERATIONAL_DB_ENV: str(db_path),
                    "CODEMAPS_TARGET_ROOT": str(Path(temp_dir) / "unrelated-target"),
                },
            ):
                self.assertEqual(mcp_operational_db_path(), db_path)
                self.assertEqual(mcp_operational_honesty_path(), db_path.parent / "honesty_telemetry.json")

    def test_hidden_tool_failure_writes_one_sanitized_operational_event(self) -> None:
        from tools.mcp.server import ProfiledFastMCP

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "operational" / "mcp_call_telemetry.db"
            private_target = str(Path(temp_dir) / "private-target")
            test_mcp = ProfiledFastMCP("telemetry-failure-test")
            test_mcp.set_visible_tools("target_repository_default", set())
            with patch.dict(os.environ, {MCP_OPERATIONAL_DB_ENV: str(db_path)}):
                with self.assertRaisesRegex(ValueError, "unavailable in profile"):
                    asyncio.run(test_mcp.call_tool("hidden_private_tool", {"target_root": private_target}))
                ledger = load_mcp_call_telemetry()

            self.assertEqual(len(ledger["entries"]), 1)
            event = ledger["entries"][0]
            self.assertEqual(event["outcome"], "failure")
            self.assertEqual(event["failure_layer"], "tool_visibility")
            self.assertEqual(event["target_mode"], "explicit_target")
            self.assertNotIn(private_target.encode("utf-8"), db_path.read_bytes())

    def test_call_local_honesty_path_does_not_write_target_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            operational_path = Path(temp_dir) / "operational" / "honesty_telemetry.json"
            target_default = Path(temp_dir) / "target" / "honesty_telemetry.json"
            with patch("tools.core.honesty_telemetry.TELEMETRY_PATH", target_default):
                token = activate_honesty_telemetry_path(operational_path)
                try:
                    record_honesty_event(
                        component="mcp.test",
                        category="caught_error",
                        operation="persist_trace",
                        reason="validation probe",
                        evidence_source="validation_probe",
                    )
                finally:
                    reset_honesty_telemetry_path(token)

            self.assertTrue(operational_path.exists())
            self.assertFalse(target_default.exists())
            payload = json.loads(operational_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["meta"]["physical_ssot"], "json_file")


if __name__ == "__main__":
    unittest.main()

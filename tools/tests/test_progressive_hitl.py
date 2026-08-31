from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core.hitl_ledger_state import effective_active_approvals, summarize_ledger_entries
from tools.core.hitl_policy import active_approval, authorize_action, watchdog_scope
from tools.orchestrators.watchdog import CodeMapsHandler


class ProgressiveHitlTests(unittest.TestCase):
    def test_watchdog_scope_is_deterministic_for_watch_root(self):
        scope = watchdog_scope(Path("workspace"))
        self.assertTrue(scope.startswith("watchdog:"))
        self.assertTrue(scope.endswith("/workspace"))

    def test_watchdog_session_paths_preserve_analysis_root_relative_path(self):
        handler = CodeMapsHandler(display_root=Path("tools/core"))

        self.assertEqual(
            handler._session_repo_relative_path("tools/core/agent_packet_budget.py"),
            "tools/core/agent_packet_budget.py",
        )
        self.assertEqual(handler._shorten_path("tools/core/agent_packet_budget.py"), "agent_packet_budget.py")

    def test_active_approval_requires_matching_gate_scope_and_expiry(self):
        scope = "watchdog:/repo"
        ledger = {
            "entries": [
                {
                    "id": "approved",
                    "gate": "watchdog_auto_restore",
                    "scope": scope,
                    "decision": "approved",
                    "expires_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }
        with patch("tools.hitl_approval_ledger.verify_ledger", return_value={"status": "PASS", "invalid_entries": []}):
            self.assertTrue(active_approval("watchdog_auto_restore", scope, ledger=ledger)["approved"])
            self.assertFalse(active_approval("watchdog_auto_restore", "watchdog:/other", ledger=ledger)["approved"])

    def test_latest_non_approval_and_expiry_remove_effective_authority(self):
        scope = "watchdog:/repo"
        ledger = {
            "entries": [
                {
                    "id": "old-approved",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "gate": "watchdog_auto_restore",
                    "scope": scope,
                    "decision": "approved",
                    "expires_at": "2999-01-01T00:00:00+00:00",
                },
                {
                    "id": "new-rejected",
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "gate": "watchdog_auto_restore",
                    "scope": scope,
                    "decision": "rejected",
                },
                {
                    "id": "expired-approved",
                    "created_at": "2026-01-03T00:00:00+00:00",
                    "gate": "watchdog_auto_restore",
                    "scope": "watchdog:/expired",
                    "decision": "approved",
                    "expires_at": "2000-01-01T00:00:00+00:00",
                },
            ]
        }
        with patch("tools.hitl_approval_ledger.verify_ledger", return_value={"status": "PASS", "invalid_entries": []}):
            denied = active_approval("watchdog_auto_restore", scope, ledger=ledger)
            expired = active_approval("watchdog_auto_restore", "watchdog:/expired", ledger=ledger)

        self.assertFalse(denied["approved"])
        self.assertEqual(denied["reason"], "latest_decision_rejected")
        self.assertFalse(expired["approved"])
        self.assertEqual(expired["reason"], "approval_expired")
        self.assertEqual(effective_active_approvals(ledger["entries"]), [])
        self.assertEqual(summarize_ledger_entries(ledger["entries"])["active_approvals"], 0)

    def test_authorize_action_routes_soft_and_hard_modes_through_single_gate(self):
        soft = authorize_action("architecture_doctrine_proposal", "oracle:MAIN")
        self.assertTrue(soft["approved"])
        self.assertEqual(soft["approval_mode"], "soft")

        hard = authorize_action("watchdog_auto_restore", "watchdog:/missing")
        self.assertFalse(hard["approved"])
        self.assertEqual(hard["gate"], "watchdog_auto_restore")
        self.assertEqual(hard["fallback"], "advise_without_mutation")

    def test_enforce_mode_never_restores_without_active_approval(self):
        handler = CodeMapsHandler(mode="ENFORCE", display_root=Path("workspace"))
        denial = {
            "approved": False,
            "gate": "watchdog_auto_restore",
            "scope": "watchdog:/workspace",
            "reason": "matching_approval_not_found",
        }
        with (
            patch("tools.core.hitl_policy.authorize_watchdog_restore", return_value=denial),
            patch.object(handler, "display_remediation_advice") as advise,
            patch("tools.orchestrators.watchdog.subprocess.run") as run,
        ):
            restored = handler.trigger_circuit_breaker(["src/example.ts"], [{"file": "src/example.ts", "rule": "test"}])

        self.assertFalse(restored)
        run.assert_not_called()
        advise.assert_called_once()


if __name__ == "__main__":
    unittest.main()

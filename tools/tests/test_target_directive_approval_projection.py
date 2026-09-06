from __future__ import annotations

import unittest

from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS
from tools.core.contextos_mcp import (
    _directive_for_priority_finding,
    _directive_for_violation,
    build_agent_action_directives,
    target_directive_approval_projection,
)


class TargetDirectiveApprovalProjectionTests(unittest.TestCase):
    @staticmethod
    def _audit_directive(rule: str, mode: str, file_path: str) -> dict:
        return _directive_for_violation(
            {
                "project": "MAIN",
                "file": file_path,
                "rule": rule,
                "detail": f"Synthetic {mode} authority regression.",
            },
            {
                "rule_taxonomy": {
                    "profiles": {
                        rule: {
                            "label": rule.replace("_", " ").title(),
                            "mode": mode,
                            "layer": "architecture",
                            "rationale": f"The {rule} contract protects its declared architecture boundary.",
                        }
                    }
                }
            },
            index=1,
        )

    def test_doctrine_modes_project_explainable_authority_decisions(self) -> None:
        cases = [
            ("heal_state_owner", "heal", "src/state/sessionStore.ts", True),
            ("enforce_public_api", "enforced", "src/api/publishBook.ts", True),
            ("advise_component_size", "advisory", "src/editor/ChapterToolbar.tsx", False),
        ]

        for rule, mode, file_path, expected_approval in cases:
            with self.subTest(mode=mode, file=file_path):
                directive = self._audit_directive(rule, mode, file_path)
                self.assertEqual(directive["rule_mode"], mode)
                self.assertEqual(directive["approval_decision_source"], "rule_mode")
                self.assertIs(directive["human_approval_required"], expected_approval)
                self.assertIn(f"Rule mode '{mode}'", directive["approval_reason"])
                self.assertIn(rule, directive["approval_reason"])

    def test_no_active_directive_explains_approval_not_required(self) -> None:
        directive = build_agent_action_directives(
            {"active_signals": []},
            audit_report={"violations": []},
            quality_gate={"checks": []},
            max_items=1,
        )[0]

        self.assertEqual(directive["rule_mode"], "informational")
        self.assertEqual(directive["approval_decision_source"], "rule_mode")
        self.assertIs(directive["human_approval_required"], False)
        self.assertIn("does not require", directive["approval_reason"])

    def test_high_risk_advisory_finding_exposes_both_authority_inputs(self) -> None:
        directive = _directive_for_priority_finding(
            {
                "scoped_file": "src/payments/CheckoutBoundary.tsx",
                "classification": "cross_boundary_payment_change",
                "risk_tier": "high",
                "why_problematic": "The proposed edit crosses a payment authorization boundary.",
                "recommended_action": "Inspect the payment boundary before proposing a mutation.",
            },
            index=1,
        )

        self.assertEqual(directive["rule_mode"], "advisory")
        self.assertEqual(directive["approval_decision_source"], "explicit_override")
        self.assertIs(directive["human_approval_required"], True)
        self.assertIn("risk tier is 'high'", directive["approval_reason"])
        self.assertIn("despite rule mode 'advisory'", directive["approval_reason"])
        self.assertIn("requires explicit human approval", directive["approval_reason"])

    def test_reason_is_bounded_by_the_shared_compact_packet_budget(self) -> None:
        projection = target_directive_approval_projection(
            {
                "mode": "heal",
                "rationale": "word " * (COMPACT_AGENT_PACKET_TOKENS * 2),
            }
        )

        self.assertLessEqual(
            len(projection["approval_reason"]),
            COMPACT_AGENT_PACKET_TOKENS // 4,
        )
        self.assertTrue(projection["approval_reason"].endswith("."))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.core.artifact_validator import validate_payload
from tools.core.contextos_mcp import _directive_for_violation
from tools.core.pipeline_policy import resolve_loc_finding_semantics
from tools.engines import mcp_governance_engine
from tools.engines.mcp_governance_engine import _loc_violation_key


class LocFindingSemanticsTests(unittest.TestCase):
    @staticmethod
    def _audit_payload(violation):
        return {
            "meta": {"kind": "audit_report", "version": "test"},
            "summary": {
                "total": 1,
                "by_rule": {violation["rule"]: 1},
                "audit_scope": {},
                "rule_taxonomy": {},
                "remediation_backlog": [],
            },
            "audit_scope": {
                "atlas_project_count": 1,
                "audited_project_count": 1,
                "audited_projects": ["MAIN"],
                "violation_project_count": 1,
            },
            "atlas_project_count": 1,
            "audited_project_count": 1,
            "audited_projects": ["MAIN"],
            "violation_project_count": 1,
            "violations": [violation],
            "report_sections": [],
        }

    def test_dedicated_kind_preserves_rule_and_threshold(self):
        finding = resolve_loc_finding_semantics(
            "hook",
            240,
            symbol_name="useWorkspace",
            start_line=11,
            end_line=250,
        )

        self.assertEqual(finding["rule"], "loc_limits_hook")
        self.assertEqual(finding["symbol_kind"], "hook")
        self.assertEqual(finding["threshold_kind"], "hook")
        self.assertEqual(finding["limit"], 200)

    def test_configured_generic_kind_uses_its_own_threshold(self):
        finding = resolve_loc_finding_semantics(
            "function",
            180,
            symbol_name="buildReport",
            start_line=20,
            end_line=199,
        )

        self.assertEqual(finding["rule"], "loc_limits_symbol")
        self.assertEqual(finding["symbol_kind"], "function")
        self.assertEqual(finding["threshold_kind"], "function")
        self.assertEqual(finding["limit"], 150)

    def test_unknown_threshold_kind_is_explicitly_defaulted(self):
        finding = resolve_loc_finding_semantics("arrow", 450, symbol_name="handler")

        self.assertEqual(finding["rule"], "loc_limits_symbol")
        self.assertEqual(finding["symbol_kind"], "arrow")
        self.assertEqual(finding["threshold_kind"], "default")
        self.assertEqual(finding["limit"], 400)

    def test_agent_directive_carries_structured_loc_semantics(self):
        finding = resolve_loc_finding_semantics(
            "class",
            520,
            symbol_name="WorkspaceIndex",
            start_line=5,
            end_line=524,
        )
        violation = {
            **finding,
            "project": "MAIN",
            "file": "src/workspace.py",
            "detail": "Oversized class.",
            "recommended_action": "Review class responsibilities.",
        }

        directive = _directive_for_violation(violation, None, index=1)

        self.assertEqual(directive["rule"], "loc_limits_symbol")
        self.assertEqual(directive["finding_semantics"]["symbol_kind"], "class")
        self.assertEqual(directive["finding_semantics"]["threshold_kind"], "class")
        self.assertEqual(directive["finding_semantics"]["start_line"], 5)
        self.assertIn("L5-L524", directive["action"])
        self.assertIn("not proof that decomposition is automatically correct", directive["action"])
        self.assertEqual(_loc_violation_key(violation), ("loc_limits_symbol", "WorkspaceIndex"))

    def test_patch_validation_uses_the_same_generic_semantics(self):
        symbols = [
            {
                "name": "build_workspace",
                "type": "function",
                "line": 10,
                "endLine": 179,
                "features": [],
            }
        ]
        with TemporaryDirectory() as workspace, patch.object(
            mcp_governance_engine,
            "run_ast_sequencer",
            return_value=symbols,
        ):
            result = mcp_governance_engine.validate_proposed_patch(
                "module.py",
                "def build_workspace():\n    return None\n",
                workspace_root=workspace,
            )

        finding = result["violations"][0]
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(finding["rule"], "loc_limits_symbol")
        self.assertEqual(finding["symbol_kind"], "function")
        self.assertEqual(finding["threshold_kind"], "function")
        self.assertEqual(finding["start_line"], 10)
        self.assertEqual(finding["end_line"], 179)

    def test_audit_schema_rejects_loc_finding_without_semantics(self):
        violation = {
            "project": "MAIN",
            "file": "src/example.ts",
            "rule": "loc_limits_symbol",
            "detail": "Oversized function.",
            "recommended_action": "Review responsibilities.",
        }

        errors = validate_payload("audit_report", self._audit_payload(violation))

        self.assertTrue(any("symbol_kind" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

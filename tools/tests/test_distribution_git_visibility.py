from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import validate_distribution_hardening as distribution


class DistributionGitVisibilityTests(unittest.TestCase):
    def _help_for_surface(self, scope: str) -> str:
        contract = distribution.load_json_file(distribution.CLI_COMMAND_CONTRACT_PATH, {})
        validation = contract["validation"]
        surface_id = "public" if scope == "installed-package" else "development"
        expected: set[str] = set()
        for row in validation["top_level_command_visibility"]["classes"].values():
            if scope == "installed-package" and not row["public_distribution"]:
                continue
            expected.update(row["commands"])
        phrases = validation["help_surfaces"][surface_id]["required_phrases"]
        return f"usage: sage.py {{{','.join(sorted(expected))}}}\n" + "\n".join(phrases)

    def test_installed_package_authority_surface_requires_public_commands_and_omits_private_docs(self) -> None:
        checks = distribution._authority_surface_checks(
            "installed-package",
            self._help_for_surface("installed-package"),
            walkthrough_present=False,
            walkthrough_text="",
            checklist_present=False,
            checklist_text="",
        )
        self.assertTrue(all(check["passed"] for check in checks), checks)

    def test_installed_package_authority_surface_rejects_private_command_leak(self) -> None:
        help_text = self._help_for_surface("installed-package").replace("}", ",release-check}", 1)
        checks = distribution._authority_surface_checks(
            "installed-package",
            help_text,
            walkthrough_present=False,
            walkthrough_text="",
            checklist_present=False,
            checklist_text="",
        )
        command_check = next(
            check for check in checks if check["name"] == "cli_command_surface_matches_authority_profile"
        )
        self.assertFalse(command_check["passed"])
        self.assertIn("release-check", command_check["details"])

    def test_installed_package_authority_surface_rejects_private_document_leak(self) -> None:
        checks = distribution._authority_surface_checks(
            "installed-package",
            self._help_for_surface("installed-package"),
            walkthrough_present=True,
            walkthrough_text="python sage.py release-check",
            checklist_present=True,
            checklist_text="| 6 | Distribution hardening | performance_ledger release-check",
        )
        failed = {check["name"] for check in checks if not check["passed"]}
        self.assertEqual(
            {"private_release_walkthrough_omitted", "private_release_checklist_omitted"},
            failed,
        )

    def test_source_checkout_authority_surface_preserves_private_commands_and_docs(self) -> None:
        checks = distribution._authority_surface_checks(
            "source-checkout",
            self._help_for_surface("source-checkout"),
            walkthrough_present=True,
            walkthrough_text="python sage.py release-check",
            checklist_present=True,
            checklist_text="| 6 | Distribution hardening | performance_ledger release-check",
        )
        self.assertTrue(all(check["passed"] for check in checks), checks)

    def test_development_folder_discovery_uses_installation_metadata_not_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            running = root / "Rastgele-Çalışan-Kök"
            sibling = root / "Başka-Bir-SAGE-Kurulumu"
            legacy_named = root / "Legacy Product Folder - Copy"
            for candidate in (running, sibling):
                candidate.mkdir()
                (candidate / "sage.py").write_text("", encoding="utf-8")
                (candidate / "codemaps.py").write_text("", encoding="utf-8")
                (candidate / "pyproject.toml").write_text(
                    "\n".join(
                        (
                            "[project]",
                            'name = "nexora-sage"',
                            "[tool.nexora_sage.distribution]",
                            'mode = "source_checkout"',
                        )
                    ),
                    encoding="utf-8",
                )
            legacy_named.mkdir()
            (legacy_named / "sage.py").write_text("", encoding="utf-8")
            with patch.object(distribution, "CODE_MAPS_DIR", running):
                discovered = distribution._sage_development_folders()

        self.assertEqual([sibling, running], discovered)

    @patch.object(distribution, "_source_checkout_visibility_checks")
    def test_installed_package_scope_does_not_inherit_checkout_git_checks(self, checkout_checks) -> None:
        self.assertEqual([], distribution._scope_specific_checks("installed-package"))
        checkout_checks.assert_not_called()

    @patch.object(distribution, "_source_checkout_visibility_checks")
    def test_source_checkout_scope_preserves_git_checks(self, checkout_checks) -> None:
        checkout_checks.return_value = [{"name": "sentinel"}]
        self.assertEqual([{"name": "sentinel"}], distribution._scope_specific_checks("source-checkout"))
        checkout_checks.assert_called_once_with()

    def test_validation_scopes_write_distinct_artifact_names(self) -> None:
        self.assertEqual(
            "installed_distribution_hardening_validation",
            distribution._artifact_stem("installed-package"),
        )
        self.assertEqual(
            "distribution_hardening_validation",
            distribution._artifact_stem("source-checkout"),
        )

    @patch.object(distribution.time, "sleep")
    @patch.object(distribution, "run_observed_subprocess")
    def test_unknown_git_probe_retries_and_preserves_attempt_evidence(self, observed, _sleep) -> None:
        unknown = type("Result", (), {"returncode": 128, "stderr": "temporary git error"})()
        visible = type("Result", (), {"returncode": 1, "stderr": ""})()
        observed.side_effect = [(unknown, 0.1), (visible, 0.2)]
        probe = distribution._git_check_ignore(Path("candidate"))
        self.assertEqual("visible", probe["state"])
        self.assertEqual(2, len(probe["attempts"]))
        self.assertEqual(128, probe["attempts"][0]["returncode"])
        self.assertEqual("temporary git error", probe["attempts"][0]["stderr"])

    @patch.object(distribution, "run_observed_subprocess")
    def test_visible_git_probe_does_not_retry(self, observed) -> None:
        visible = type("Result", (), {"returncode": 1, "stderr": ""})()
        observed.return_value = (visible, 0.1)
        probe = distribution._git_check_ignore(Path("candidate"))
        self.assertEqual("visible", probe["state"])
        self.assertEqual(1, len(probe["attempts"]))
        self.assertEqual(1, observed.call_count)

    def test_absent_generated_archives_do_not_require_legacy_ignore_pattern(self) -> None:
        with patch.object(distribution, "_sage_development_folders", return_value=[distribution.CODE_MAPS_DIR]):
            with patch.object(distribution, "_generated_sage_archives", return_value=[]):
                with patch.object(
                    distribution,
                    "_git_check_ignore",
                    return_value={"state": "visible", "attempts": []},
                ):
                    checks = distribution._source_checkout_visibility_checks()

        archive_check = next(check for check in checks if check["name"] == "generated_sage_archives_are_git_ignored")
        self.assertTrue(archive_check["passed"])

    def test_visible_generated_archive_still_fails_closed(self) -> None:
        archive = distribution.CODE_MAPS_DIR.parent / "nexora-sage.zip"
        with patch.object(distribution, "_sage_development_folders", return_value=[distribution.CODE_MAPS_DIR]):
            with patch.object(distribution, "_generated_sage_archives", return_value=[archive]):
                with patch.object(
                    distribution,
                    "_git_check_ignore",
                    return_value={"state": "visible", "attempts": []},
                ):
                    checks = distribution._source_checkout_visibility_checks()

        archive_check = next(check for check in checks if check["name"] == "generated_sage_archives_are_git_ignored")
        self.assertFalse(archive_check["passed"])


if __name__ == "__main__":
    unittest.main()

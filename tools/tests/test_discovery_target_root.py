from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.orchestrators.discovery import resolve_discovery_root, workspace_root_reference


class DiscoveryTargetRootTests(unittest.TestCase):
    def test_explicit_target_root_overrides_installation_parent(self):
        with tempfile.TemporaryDirectory() as install_tmp, tempfile.TemporaryDirectory() as target_tmp:
            install_root = Path(install_tmp)
            target_root = Path(target_tmp)

            resolved = resolve_discovery_root(
                code_maps_dir=install_root,
                environment={"CODEMAPS_TARGET_ROOT": str(target_root)},
            )

        self.assertEqual(resolved, target_root.resolve())

    def test_public_projection_requires_explicit_target_root(self):
        with tempfile.TemporaryDirectory() as install_tmp:
            install_root = Path(install_tmp)
            (install_root / "PUBLIC_DISTRIBUTION_MANIFEST.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "requires an explicit target repository"):
                resolve_discovery_root(code_maps_dir=install_root, environment={})

    def test_internal_legacy_layout_retains_parent_default_without_public_marker(self):
        with tempfile.TemporaryDirectory() as parent_tmp:
            install_root = Path(parent_tmp) / "sage-install"
            install_root.mkdir()

            resolved = resolve_discovery_root(code_maps_dir=install_root, environment={})

        self.assertEqual(resolved, install_root.parent.resolve())

    def test_workspace_reference_is_relative_to_installation(self):
        with tempfile.TemporaryDirectory() as parent_tmp:
            parent = Path(parent_tmp)
            install_root = parent / "sage-install"
            target_root = parent / "target-repository"
            install_root.mkdir()
            target_root.mkdir()

            reference = workspace_root_reference(target_root, code_maps_dir=install_root)

        self.assertEqual(reference, "../target-repository")


if __name__ == "__main__":
    unittest.main()

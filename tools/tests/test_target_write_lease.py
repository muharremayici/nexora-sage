import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.core.mcp_tool_profiles import project_mcp_tool_names
from tools.core.target_write_lease import (
    acquire_target_write_lease,
    inspect_target_write_lease,
    release_target_write_lease,
    target_write_lease_actions,
)


class TargetWriteLeaseTests(unittest.TestCase):
    def test_same_target_is_serialized_while_distinct_files_remain_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codemaps.db"
            root = Path(temp_dir) / "target"
            first = acquire_target_write_lease(
                db_path,
                analysis_root=root,
                target_file="src/App.tsx",
                actor_id="agent-a",
                source_snapshot_hash="hash-a",
            )
            self.assertEqual(first["status"], "acquired")

            busy = acquire_target_write_lease(
                db_path,
                analysis_root=root,
                target_file="src/App.tsx",
                actor_id="agent-b",
                source_snapshot_hash="hash-a",
            )
            self.assertEqual(busy["status"], "busy")
            self.assertGreater(busy["retry_after_seconds"], 0)

            independent = acquire_target_write_lease(
                db_path,
                analysis_root=root,
                target_file="src/Other.tsx",
                actor_id="agent-b",
                source_snapshot_hash="hash-b",
            )
            self.assertEqual(independent["status"], "acquired")

            released = release_target_write_lease(
                db_path,
                analysis_root=root,
                target_file="src/App.tsx",
                actor_id="agent-a",
            )
            self.assertTrue(released["released"])
            self.assertEqual(
                inspect_target_write_lease(db_path, analysis_root=root, target_file="src/App.tsx")["status"],
                "available",
            )

    def test_only_owner_can_renew_or_release_lease(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codemaps.db"
            root = Path(temp_dir) / "target"
            acquire_target_write_lease(
                db_path,
                analysis_root=root,
                target_file="src/App.tsx",
                actor_id="agent-a",
                source_snapshot_hash="old",
            )
            renewed = acquire_target_write_lease(
                db_path,
                analysis_root=root,
                target_file="src/App.tsx",
                actor_id="agent-a",
                source_snapshot_hash="new",
            )
            self.assertEqual(renewed["status"], "renewed")
            self.assertEqual(renewed["lease"]["source_snapshot_hash"], "new")

            denied = release_target_write_lease(
                db_path,
                analysis_root=root,
                target_file="src/App.tsx",
                actor_id="agent-b",
            )
            self.assertEqual(denied["status"], "not_owner")

    def test_concurrent_acquire_has_one_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codemaps.db"
            root = Path(temp_dir) / "target"

            def acquire(actor_id: str) -> str:
                return acquire_target_write_lease(
                    db_path,
                    analysis_root=root,
                    target_file="src/App.tsx",
                    actor_id=actor_id,
                    source_snapshot_hash="hash-a",
                )["status"]

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(acquire, ["agent-a", "agent-b"]))
            self.assertEqual(statuses.count("acquired"), 1)
            self.assertEqual(statuses.count("busy"), 1)

    def test_lease_tool_is_followup_only(self):
        root = Path(__file__).resolve().parents[2]
        default_tools = project_mcp_tool_names(root, "target_repository_default")["visible_tools"]
        followup_tools = project_mcp_tool_names(root, "target_repository_followup")["visible_tools"]
        self.assertNotIn("manage_target_write_lease", default_tools)
        self.assertIn("manage_target_write_lease", followup_tools)
        self.assertEqual(target_write_lease_actions(), {"acquire", "release"})

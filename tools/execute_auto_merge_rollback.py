from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import SCRIPTS_DIR, save_json_atomic


def execute_rollback(apply: bool = False) -> dict:
    manifest_path = SCRIPTS_DIR / "auto_merge_rollback_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing rollback manifest: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = []
    for operation in manifest.get("operations", []):
        destination = Path(str(operation.get("destination") or ""))
        backup = Path(str(operation.get("backup_path") or ""))
        rollback = operation.get("rollback") or {}
        action = "noop"
        status = "skipped"

        if backup.exists():
            action = "restore_backup"
            status = "planned"
            if apply:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, destination)
                status = "restored"
        elif rollback.get("if_destination_missing_before_merge") == "delete_destination":
            action = "delete_destination"
            status = "planned" if destination.exists() else "not_needed"
            if apply and destination.exists():
                destination.unlink()
                status = "deleted"

        results.append(
            {
                "destination": str(destination),
                "backup_path": str(backup),
                "action": action,
                "status": status,
            }
        )

    payload = {
        "meta": {
            "kind": "auto_merge_rollback_execution",
            "version": "v1",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "mode": "apply" if apply else "dry_run",
            "source_manifest": str(manifest_path),
        },
        "summary": {
            "operations": len(results),
            "changed": sum(1 for item in results if item["status"] in {"restored", "deleted"}),
            "planned": sum(1 for item in results if item["status"] == "planned"),
        },
        "results": results,
    }
    save_json_atomic(SCRIPTS_DIR / "auto_merge_rollback_execution.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or apply auto_merge rollback manifest.")
    parser.add_argument("--apply", action="store_true", help="Apply rollback actions. Default is dry-run.")
    args = parser.parse_args()
    payload = execute_rollback(apply=args.apply)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

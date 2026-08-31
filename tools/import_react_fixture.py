from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.json_io import load_json_file
from tools.core.config import CONFIG_DIR, save_json_atomic, save_text_atomic

FIXTURE_CONFIG_PATH = CONFIG_DIR / "react_fixture_matrix.json"
def _resolve(path_text: str) -> Path:
    p = Path(path_text)
    if p.is_absolute():
        return p
    return (CODE_MAPS_DIR / p).resolve()


def _fixture_root_from_entry(entry: dict[str, Any], fixture_id: str) -> Path:
    artifact_root = entry.get("artifact_root")
    if isinstance(artifact_root, str) and artifact_root.strip():
        root = Path(artifact_root)
        return root if root.is_absolute() else (CODE_MAPS_DIR / root).resolve()
    return (CODE_MAPS_DIR / "output" / ".fixtures" / fixture_id).resolve()


def run_import(
    fixture_id: str,
    source_root: str,
    set_enabled: bool,
    set_required: bool,
    create_if_missing: bool,
    dry_run: bool,
) -> dict[str, Any]:
    cfg = load_json_file(FIXTURE_CONFIG_PATH, {})
    if not isinstance(cfg, dict):
        cfg = {}
    fixtures = cfg.get("fixtures", [])
    if not isinstance(fixtures, list):
        fixtures = []
        cfg["fixtures"] = fixtures

    fixture_entry = None
    for row in fixtures:
        if isinstance(row, dict) and str(row.get("id") or "") == fixture_id:
            fixture_entry = row
            break

    if fixture_entry is None and create_if_missing:
        fixture_entry = {
            "id": fixture_id,
            "label": fixture_id.replace("_", " ").title(),
            "enabled": False,
            "required": False,
            "artifact_root": f"output/.fixtures/{fixture_id}",
        }
        fixtures.append(fixture_entry)

    if fixture_entry is None:
        return {"ok": False, "reason": "fixture_not_found", "fixture_id": fixture_id}

    src_root = _resolve(source_root)
    src_matrix = src_root / "react_support_matrix.json"
    src_validation = src_root / "react_support_validation.json"
    if not src_matrix.exists() or not src_validation.exists():
        return {
            "ok": False,
            "reason": "missing_source_artifacts",
            "source_root": str(src_root),
            "missing": [
                str(src_matrix) if not src_matrix.exists() else None,
                str(src_validation) if not src_validation.exists() else None,
            ],
        }

    dest_root = _fixture_root_from_entry(fixture_entry, fixture_id)
    dest_matrix = dest_root / "react_support_matrix.json"
    dest_validation = dest_root / "react_support_validation.json"
    manifest = dest_root / "fixture_ingest_manifest.json"

    if set_enabled:
        fixture_entry["enabled"] = True
    if set_required:
        fixture_entry["required"] = True
    fixture_entry["source_root"] = str(src_root)

    action = {
        "fixture_id": fixture_id,
        "source_root": str(src_root),
        "destination_root": str(dest_root),
        "set_enabled": bool(set_enabled),
        "set_required": bool(set_required),
        "dry_run": bool(dry_run),
    }

    if dry_run:
        return {"ok": True, "action": action, "config_written": False, "files_copied": False}

    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_matrix, dest_matrix)
    shutil.copy2(src_validation, dest_validation)
    manifest_payload = {
        "fixture_id": fixture_id,
        "ingested_at": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(src_root),
        "artifacts": ["react_support_matrix.json", "react_support_validation.json"],
    }
    save_text_atomic(manifest, json.dumps(manifest_payload, indent=2, ensure_ascii=False))
    save_json_atomic(FIXTURE_CONFIG_PATH, cfg)

    return {"ok": True, "action": action, "config_written": True, "files_copied": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import React support artifacts into a fixture root and optionally update fixture config.")
    parser.add_argument("--fixture-id", required=True, help="Fixture id from config/react_fixture_matrix.json")
    parser.add_argument("--source-root", required=True, help="Folder containing react_support_matrix.json and react_support_validation.json")
    parser.add_argument("--set-enabled", action="store_true", help="Set fixture enabled=true in config.")
    parser.add_argument("--set-required", action="store_true", help="Set fixture required=true in config.")
    parser.add_argument("--create-if-missing", action="store_true", help="Create fixture entry if not present.")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print intended actions without writing.")
    args = parser.parse_args()

    result = run_import(
        fixture_id=args.fixture_id,
        source_root=args.source_root,
        set_enabled=args.set_enabled,
        set_required=args.set_required,
        create_if_missing=args.create_if_missing,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())


from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_file
from tools.core.release_proof_steps import load_release_proof_provenance_embed_targets


def run() -> dict:
    index_path = RAW_DIR / "artifact_provenance_index.json"
    index = load_json_file(index_path, {})
    config_hash = ((index.get("meta") or {}) if isinstance(index, dict) else {}).get("config_hash")
    artifact_rows = {
        str(row.get("path", "")).replace("\\", "/"): row
        for row in (index.get("artifacts", []) if isinstance(index, dict) else [])
        if isinstance(row, dict)
    }
    updated = []
    for path in load_release_proof_provenance_embed_targets():
        if not path.exists():
            continue
        payload = load_json_file(path, {})
        if not isinstance(payload, dict):
            continue
        rel = path.relative_to(CODE_MAPS_DIR).as_posix()
        row = artifact_rows.get(rel, {})
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        meta["provenance"] = {
            "embedded_at": datetime.now(timezone.utc).isoformat(),
            "artifact_provenance_index": "output/.raw/artifact_provenance_index.json",
            "config_hash": config_hash,
            "artifact_sha256_before_embed": row.get("sha256"),
            "artifact_bytes_before_embed": row.get("bytes"),
        }
        payload["meta"] = meta
        save_json_atomic(path, payload)
        updated.append(rel)
    result = {
        "meta": {
            "kind": "embed_artifact_provenance_result",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "summary": {"updated": len(updated)},
        "updated": updated,
    }
    save_json_atomic(RAW_DIR / "artifact_provenance_embed_result.json", result)
    return result


def main() -> int:
    result = run()
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

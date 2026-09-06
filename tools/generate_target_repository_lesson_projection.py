from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_registry import artifact_path_for_storage_root
from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.target_repository_lesson_projection import build_target_repository_lesson_projection


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a target-owned lesson projection from target-local evidence.")
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    args = parser.parse_args()
    payload = build_target_repository_lesson_projection(target_id=args.target_id, target_root=args.target_root, raw_dir=args.raw_dir)
    output = artifact_path_for_storage_root(args.raw_dir, "target_repository_lesson_projection")
    save_json_atomic(output, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] != "PARTIAL" else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.target_repository_action_memory import build_target_repository_action_memory


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a read-only target action projection from existing SAGE authorities.")
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or (args.raw_dir / "target_repository_action_memory.json")
    payload = build_target_repository_action_memory(target_root=args.target_root, raw_dir=args.raw_dir)
    save_json_atomic(output, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] in {"COMPLETE", "EMPTY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

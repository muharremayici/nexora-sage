from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.target_repository_proof import build_target_repository_proof


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a bounded proof envelope for one analyzed target repository.")
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--mode", choices=("baseline", "change", "merge"), default="baseline")
    parser.add_argument("--repository-reference", help="Descriptive commit or snapshot reference; never evidence authority.")
    parser.add_argument("--evidence-not-before")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    output = args.output or (args.raw_dir / "target_repository_proof_bundle.json")
    payload = build_target_repository_proof(
        target_root=args.target_root,
        raw_dir=args.raw_dir,
        mode=args.mode,
        repository_reference=args.repository_reference,
        evidence_not_before=args.evidence_not_before,
    )
    save_json_atomic(output, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["verdict"] in {"PASS", "REVIEW_REQUIRED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

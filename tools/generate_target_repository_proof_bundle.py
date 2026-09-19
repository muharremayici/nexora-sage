from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.target_repository_proof import CONTRACT_PATH, build_target_repository_proof


def main() -> int:
    contract = load_json_object_strict(
        CONTRACT_PATH,
        label="target repository proof contract",
    )
    modes = tuple(str(mode) for mode in (contract.get("modes") or {}))
    successful_verdicts = set(
        str(verdict)
        for verdict in (contract.get("public_cli") or {}).get(
            "successful_verdicts",
            [],
        )
    )
    default_mode = str((contract.get("public_cli") or {}).get("default_mode") or "")
    if not modes or not successful_verdicts or default_mode not in modes:
        raise RuntimeError("Target repository proof execution contract is invalid")
    parser = argparse.ArgumentParser(description="Generate a bounded proof envelope for one analyzed target repository.")
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--mode", choices=modes, default=default_mode)
    parser.add_argument("--projects", help="Comma-separated project scope that must match the analyzed scope authority.")
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
        projects=args.projects,
    )
    save_json_atomic(output, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["verdict"] in successful_verdicts else 1


if __name__ == "__main__":
    raise SystemExit(main())

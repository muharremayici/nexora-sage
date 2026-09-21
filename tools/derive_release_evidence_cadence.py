from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.release_evidence_cadence import (  # noqa: E402
    build_release_evidence_cadence_receipt,
)


def _read_input(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Cadence request must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive a content-bound central release-evidence cadence receipt."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Request JSON path, or '-' to read from stdin.",
    )
    args = parser.parse_args()
    request = _read_input(args.input)
    context = request.get("context")
    changed_paths = request.get("changed_paths")
    metadata = request.get("semantic_metadata_documents")
    if not isinstance(context, dict):
        raise ValueError("context must be a JSON object")
    if not isinstance(changed_paths, list):
        raise ValueError("changed_paths must be a JSON array")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("semantic_metadata_documents must be a JSON object")
    receipt = build_release_evidence_cadence_receipt(
        context,
        changed_paths=changed_paths,
        semantic_metadata_documents=metadata,
        root=ROOT,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if receipt.get("status") != "BLOCKED" else 3


if __name__ == "__main__":
    raise SystemExit(main())

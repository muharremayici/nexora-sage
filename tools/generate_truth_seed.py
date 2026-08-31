from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.json_io import load_json_file
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic

DEAD_CODE_PATH = RAW_DIR / "dead_code.json"
DECISION_EVIDENCE_PATH = RAW_DIR / "decision_evidence.json"
SEED_OUTPUT_PATH = RAW_DIR / "signal_truth_seed.json"
SEED_REPORT_PATH = REPORTS_DIR / "signal_truth_seed.md"
def _slug(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return token or "case"


def _confidence_rank(value: str) -> int:
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return order.get(str(value or "").upper(), 3)


def _build_dead_seed(dead_items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    sorted_items = sorted(
        [row for row in dead_items if isinstance(row, dict)],
        key=lambda row: (
            _confidence_rank(str(row.get("confidence", ""))),
            str(row.get("project", "")),
            str(row.get("file", "")),
            str(row.get("symbol", "")),
        ),
    )
    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in sorted_items:
        key = (str(row.get("project", "")), str(row.get("file", "")), str(row.get("symbol", "")))
        if key in seen:
            continue
        seen.add(key)
        case = {
            "id": f"seed_{_slug(row.get('symbol', 'symbol'))}_{len(cases) + 1}",
            "enabled": False,
            "expected": "present",
            "match": {
                "project": row.get("project", "*"),
                "file": row.get("file", "*"),
                "symbol": row.get("symbol", "*"),
                "confidence": row.get("confidence", "*"),
            },
            "label": "seed_candidate_manual_verify",
        }
        cases.append(case)
        if len(cases) >= limit:
            break
    return cases


def _build_merge_seed(
    trusted: list[dict[str, Any]],
    review: list[dict[str, Any]],
    trusted_limit: int,
    review_limit: int,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for bucket_name, rows, limit in (
        ("trusted_top_candidates", trusted, trusted_limit),
        ("review_queue_examples", review, review_limit),
    ):
        count = 0
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            case = {
                "id": f"seed_{_slug(str(row.get('name', 'candidate')))}_{bucket_name}_{count + 1}",
                "enabled": False,
                "expected": "present",
                "match": {
                    "name": row.get("name", "*"),
                    "source": row.get("source", "*"),
                    "bucket": bucket_name,
                    "target": row.get("target", "*"),
                },
                "label": "seed_candidate_manual_verify",
            }
            cases.append(case)
            count += 1
            if count >= limit:
                break
    return cases


def run_seed(dead_limit: int, trusted_limit: int, review_limit: int) -> dict[str, Any]:
    dead_payload = load_json_file(DEAD_CODE_PATH, {})
    decision_payload = load_json_file(DECISION_EVIDENCE_PATH, {})

    dead_items = dead_payload.get("items", []) if isinstance(dead_payload, dict) else []
    deep = decision_payload.get("focus_deep_dive", {}) if isinstance(decision_payload, dict) else {}
    trusted = deep.get("trusted_top_candidates", []) if isinstance(deep, dict) else []
    review = deep.get("review_queue_examples", []) if isinstance(deep, dict) else []

    dead_cases = _build_dead_seed(dead_items if isinstance(dead_items, list) else [], dead_limit)
    merge_cases = _build_merge_seed(
        trusted if isinstance(trusted, list) else [],
        review if isinstance(review, list) else [],
        trusted_limit,
        review_limit,
    )

    payload = {
        "meta": {"kind": "signal_truth_seed", "version": "v1"},
        "source": {
            "dead_code_path": str(DEAD_CODE_PATH).replace("\\", "/"),
            "decision_evidence_path": str(DECISION_EVIDENCE_PATH).replace("\\", "/"),
        },
        "summary": {
            "dead_seed_cases": len(dead_cases),
            "merge_seed_cases": len(merge_cases),
        },
        "dead_code_truth_seed": dead_cases,
        "merge_truth_seed": merge_cases,
    }
    save_json_atomic(SEED_OUTPUT_PATH, payload)

    lines = [
        "# Signal Truth Seed",
        "",
        f"- Dead code seed cases: `{len(dead_cases)}`",
        f"- Merge seed cases: `{len(merge_cases)}`",
        "",
        "Bu dosya otomatik üretilir. `enabled=false` adayları manuel doğruladıktan sonra golden truth set'e taşı.",
    ]
    save_text_atomic(SEED_REPORT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate seed truth-case candidates from current artifacts.")
    parser.add_argument("--dead-limit", type=int, default=20, help="Max dead-code seed case count.")
    parser.add_argument("--merge-trusted-limit", type=int, default=12, help="Max trusted merge seed cases.")
    parser.add_argument("--merge-review-limit", type=int, default=8, help="Max review merge seed cases.")
    args = parser.parse_args()

    payload = run_seed(
        dead_limit=max(1, int(args.dead_limit or 1)),
        trusted_limit=max(1, int(args.merge_trusted_limit or 1)),
        review_limit=max(1, int(args.merge_review_limit or 1)),
    )
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


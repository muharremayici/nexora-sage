from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.quality_gate_ci import build_pr_matrix_receipt, current_git_tree_sha


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a content-bound receipt after the PR runtime matrix succeeds."
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--pull-request-number", required=True, type=int)
    parser.add_argument("--pull-request-head-sha", required=True)
    parser.add_argument("--event-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-run-id", required=True, type=int)
    parser.add_argument("--workflow-run-attempt", required=True, type=int)
    args = parser.parse_args()

    try:
        payload = build_pr_matrix_receipt(
            pull_request_number=args.pull_request_number,
            pull_request_head_sha=args.pull_request_head_sha,
            event_sha=args.event_sha,
            source_tree_sha=current_git_tree_sha(),
            repository=args.repository,
            workflow_run_id=args.workflow_run_id,
            workflow_run_attempt=args.workflow_run_attempt,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(f"[quality-gate-receipt] FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        "[quality-gate-receipt] PASS "
        f"tree={payload['source']['source_tree_sha']} runtimes={len(payload['proof']['python_versions'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

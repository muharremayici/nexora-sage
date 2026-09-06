"""Public CLI adapter for polling a durable pipeline run receipt."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.pipeline_run_receipts import pipeline_run_status, render_pipeline_run_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Query a Nexora SAGE pipeline run without launching another run.")
    parser.add_argument("--run-id", default="", help="Durable run id. Omit to query the latest run.")
    parser.add_argument("--wait", action="store_true", help="Continue polling this receipt until terminal state or wait timeout.")
    parser.add_argument("--timeout-seconds", type=float, default=0, help="Bounded observation timeout; never terminates or retries the run.")
    parser.add_argument("--poll-seconds", type=float, default=5, help="Polling cadence while --wait is active.")
    parser.add_argument("--json", action="store_true", help="Print the structured receipt projection.")
    args = parser.parse_args()

    started = time.monotonic()
    payload = pipeline_run_status(args.run_id)
    terminal = {"PASS", "FAILED", "BLOCKED", "INTERRUPTED", "INTERRUPTED_UNCONFIRMED", "NOT_FOUND"}
    while args.wait and payload.get("status") not in terminal:
        delay = max(0.25, float(args.poll_seconds or 5))
        if args.timeout_seconds > 0:
            remaining = float(args.timeout_seconds) - (time.monotonic() - started)
            if remaining <= 0:
                break
            delay = min(delay, remaining)
        time.sleep(delay)
        payload = pipeline_run_status(str(payload.get("run_id") or args.run_id))

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_pipeline_run_status(payload))
    return 0 if payload.get("status") != "NOT_FOUND" else 2


if __name__ == "__main__":
    raise SystemExit(main())

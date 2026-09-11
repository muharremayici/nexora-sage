from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.core.node_ast_worker import NodeAstWorkerSession


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
SEQUENCER = CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs"


def _worker_command(*, max_requests: int = 200) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    return [
        node,
        str(SEQUENCER),
        "--worker-jsonl",
        "--max-requests",
        str(max_requests),
        "--max-rss-bytes",
        str(805306368),
    ]


def test_session_reuses_one_node_process_across_acknowledged_chunks():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = root / "first.ts"
        second = root / "second.ts"
        first.write_text("export const first = 1;\n", encoding="utf-8")
        second.write_text("export const second = 2;\n", encoding="utf-8")
        with NodeAstWorkerSession(_worker_command(), cwd=CODE_MAPS_DIR) as session:
            first_line, first_metrics = session.request(
                request_id="first-request",
                paths=[str(first)],
                timeout_seconds=20,
            )
            second_line, second_metrics = session.request(
                request_id="second-request",
                paths=[str(second)],
                timeout_seconds=20,
            )

    first_payload = json.loads(first_line)
    second_payload = json.loads(second_line)
    assert first_metrics["worker_process_starts"] == 1
    assert second_metrics["worker_process_starts"] == 0
    assert first_payload["batchMeta"]["requestId"] == "first-request"
    assert second_payload["batchMeta"]["requestId"] == "second-request"


def test_session_retires_at_request_limit_and_starts_a_fresh_worker():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "sample.ts"
        target.write_text("export const sample = true;\n", encoding="utf-8")
        with NodeAstWorkerSession(_worker_command(max_requests=1), cwd=CODE_MAPS_DIR) as session:
            first_line, first_metrics = session.request(
                request_id="bounded-1",
                paths=[str(target)],
                timeout_seconds=20,
            )
            second_line, second_metrics = session.request(
                request_id="bounded-2",
                paths=[str(target)],
                timeout_seconds=20,
            )

    assert json.loads(first_line)["batchMeta"]["workerRetireReason"] == "request_limit"
    assert json.loads(second_line)["batchMeta"]["workerRetireReason"] == "request_limit"
    assert first_metrics["worker_process_starts"] == 1
    assert second_metrics["worker_process_starts"] == 1
    assert second_metrics["request_replays"] == 0


def test_session_restarts_failed_worker_and_replays_only_unacknowledged_request():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        worker_script = root / "crash_once_worker.py"
        marker = root / "crashed.marker"
        worker_script.write_text(
            """
import json
import sys
from pathlib import Path

marker = Path(sys.argv[1])
for line in sys.stdin:
    request = json.loads(line)
    if not marker.exists():
        marker.write_text("crashed", encoding="utf-8")
        raise SystemExit(7)
    paths = request["paths"]
    print(json.dumps({
        "batchMeta": {
            "protocolVersion": 1,
            "requestId": request["requestId"],
            "filesRequested": len(paths),
            "filesReported": len(paths),
        },
        "results": {path: [] for path in paths},
    }), flush=True)
""".strip()
            + "\n",
            encoding="utf-8",
        )
        with NodeAstWorkerSession(
            [sys.executable, "-u", str(worker_script), str(marker)],
            cwd=root,
            heartbeat_seconds=1,
        ) as session:
            line, metrics = session.request(
                request_id="replay-me-once",
                paths=["C:/repo/only.ts"],
                timeout_seconds=10,
            )

    payload = json.loads(line)
    assert payload["batchMeta"]["requestId"] == "replay-me-once"
    assert metrics == {
        "worker_process_starts": 2,
        "worker_restarts": 1,
        "request_replays": 1,
    }


def test_generate_atlas_bounded_pool_reduces_chunk_process_starts():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(120):
            (root / f"module_{index:03d}.ts").write_text(
                f"export const value{index} = {index};\n",
                encoding="utf-8",
            )
        def run_atlas(session_pool: bool) -> subprocess.CompletedProcess[str]:
            env = dict(os.environ)
            for key in (
                "CODEMAPS_TARGET_PREFLIGHT_RECEIPT",
                "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
                "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE",
                "CODEMAPS_TARGET_PROJECTS",
            ):
                env.pop(key, None)
            env["CODEMAPS_TARGET_ROOT"] = str(root)
            env["CODEMAPS_AST_SESSION_POOL"] = "1" if session_pool else "0"
            env["CODEMAPS_AST_BATCH_WORKERS"] = "2"
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from tools.engines.generate_atlas import generate_atlas; generate_atlas(dry_run=True)",
                ],
                cwd=CODE_MAPS_DIR,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )

        legacy_result = run_atlas(False)
        pool_result = run_atlas(True)

    assert legacy_result.returncode == 0, legacy_result.stderr or legacy_result.stdout
    assert pool_result.returncode == 0, pool_result.stderr or pool_result.stdout
    assert "ast_jobs=5" in legacy_result.stderr
    assert "ast_jobs=5" in pool_result.stderr

    def lifecycle(result: subprocess.CompletedProcess[str]) -> tuple[str, int]:
        line = next(
            item for item in result.stderr.splitlines()
            if "Atlas Node AST lifecycle |" in item
        )
        starts = int(line.split("process_starts=", 1)[1].split(" ", 1)[0])
        return line, starts

    legacy_line, legacy_starts = lifecycle(legacy_result)
    pool_line, pool_starts = lifecycle(pool_result)
    assert legacy_starts == 5
    assert 1 <= pool_starts <= 2
    assert pool_starts < legacy_starts
    assert "fallback_starts=0 " in legacy_line
    assert "fallback_starts=0 " in pool_line
    assert "files_requested=120 " in legacy_line
    assert "files_requested=120 " in pool_line

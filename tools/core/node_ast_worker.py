from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.core.python_runtime_env import utf8_subprocess_env
from tools.core.subprocess_telemetry import process_group_popen_kwargs, terminate_process_tree


class NodeAstWorkerError(RuntimeError):
    def __init__(self, message: str, metrics: dict[str, int]):
        super().__init__(message)
        self.metrics = dict(metrics)


class NodeAstWorkerSession:
    """Run-scoped, bounded JSON-lines transport for the Node AST sequencer."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        log: Callable[[str], None] | None = None,
        heartbeat_seconds: float = 10.0,
    ) -> None:
        self.command = [str(part) for part in command]
        self.cwd = Path(cwd)
        self.log = log
        self.heartbeat_seconds = max(1.0, float(heartbeat_seconds or 10.0))
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[str | None] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._lock = threading.Lock()

    def _read_stdout(self, process: subprocess.Popen[str], output_queue: queue.Queue[str | None]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output_queue.put(line.rstrip("\r\n"))
        finally:
            output_queue.put(None)

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            self._stderr_tail.append(line.rstrip("\r\n"))

    def _start(self) -> None:
        self._stdout_queue = queue.Queue()
        self._stderr_tail.clear()
        process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            env=utf8_subprocess_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **process_group_popen_kwargs(),
        )
        self._process = process
        threading.Thread(
            target=self._read_stdout,
            args=(process, self._stdout_queue),
            daemon=True,
        ).start()
        threading.Thread(target=self._read_stderr, args=(process,), daemon=True).start()

    def _stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            terminate_process_tree(process)
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass

    def restart(self) -> None:
        with self._lock:
            self._stop()

    def request(
        self,
        *,
        request_id: str,
        paths: list[str],
        timeout_seconds: float,
    ) -> tuple[str, dict[str, int]]:
        payload = json.dumps(
            {
                "protocolVersion": 1,
                "requestId": str(request_id),
                "paths": [str(path) for path in paths],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        metrics = {
            "worker_process_starts": 0,
            "worker_restarts": 0,
            "request_replays": 0,
        }
        with self._lock:
            for attempt in range(2):
                process = self._process
                if process is None or process.poll() is not None:
                    self._start()
                    metrics["worker_process_starts"] += 1
                    process = self._process
                assert process is not None
                try:
                    if process.stdin is None:
                        raise BrokenPipeError("worker stdin unavailable")
                    process.stdin.write(payload + "\n")
                    process.stdin.flush()
                    started = time.perf_counter()
                    limit = max(1.0, float(timeout_seconds or 1.0))
                    while True:
                        elapsed = time.perf_counter() - started
                        remaining = limit - elapsed
                        if remaining <= 0:
                            raise TimeoutError(f"worker response timed out after {limit:.1f}s")
                        try:
                            line = self._stdout_queue.get(
                                timeout=min(self.heartbeat_seconds, remaining)
                            )
                        except queue.Empty:
                            if self.log is not None:
                                self.log(
                                    "WAIT atlas_node_worker "
                                    f"request_id={request_id} runtime_seconds={elapsed:.1f} "
                                    f"timeout_seconds={limit:.1f}"
                                )
                            continue
                        if line is None:
                            stderr = "\n".join(self._stderr_tail)
                            raise RuntimeError(
                                f"worker exited before response rc={process.poll()} stderr={stderr[-2000:]}"
                            )
                        try:
                            response = json.loads(line)
                            batch_meta = response.get("batchMeta", {}) if isinstance(response, dict) else {}
                            retire_after_response = bool(
                                isinstance(batch_meta, dict)
                                and batch_meta.get("workerRetireReason")
                            )
                        except (json.JSONDecodeError, TypeError, ValueError):
                            retire_after_response = False
                        if retire_after_response:
                            self._stop()
                        return line, metrics
                except (BrokenPipeError, OSError, RuntimeError, TimeoutError) as exc:
                    self._stop()
                    if attempt == 0:
                        metrics["worker_restarts"] += 1
                        metrics["request_replays"] += 1
                        continue
                    raise NodeAstWorkerError(str(exc), metrics) from exc
        raise NodeAstWorkerError("worker request failed without response", metrics)

    def close(self) -> None:
        with self._lock:
            self._stop()

    def __enter__(self) -> "NodeAstWorkerSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

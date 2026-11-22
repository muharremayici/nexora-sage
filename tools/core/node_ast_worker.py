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
from tools.core.owned_text_bridge import OwnedSourceInput, OwnedTextBridgeError, validate_owned_response, observe_owned_wire_source
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
        self._owned_mode = "--owned-text-worker-jsonl" in self.command
        self._owned_response_limit: int | None = None

    def _read_stdout(self, process: subprocess.Popen[str], output_queue: queue.Queue[str | None]) -> None:
        assert process.stdout is not None
        try:
            if self._owned_mode:
                assert self._owned_response_limit is not None
                raw = process.stdout.readline(self._owned_response_limit + 1)
                if not raw:
                    return
                if len(raw) > self._owned_response_limit or not raw.endswith(b"\n"):
                    output_queue.put('{"batchMeta":{},"error":"owned_response_frame_refused"}')
                else:
                    try:
                        output_queue.put(raw[:-1].decode("utf-8", errors="strict"))
                    except UnicodeError:
                        output_queue.put('{"batchMeta":{},"error":"owned_response_encoding_refused"}')
                return
            for line in process.stdout:
                output_queue.put(line.rstrip("\r\n"))
        finally:
            output_queue.put(None)

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        if self._owned_mode:
            # Parser diagnostics can contain source text. Drain without retaining
            # it; private transport errors expose only bounded error codes.
            while process.stderr.read(4096):
                pass
            return
        for line in process.stderr:
            self._stderr_tail.append(line.rstrip("\r\n"))

    def _start(self) -> None:
        self._stdout_queue = queue.Queue()
        self._stderr_tail.clear()
        if self._owned_mode and self._owned_response_limit is None:
            raise RuntimeError("owned_response_limit_missing")
        stream_options = {"text": False, "bufsize": 0} if self._owned_mode else {
            "text": True, "encoding": "utf-8", "errors": "replace", "bufsize": 1,
        }
        process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            env=utf8_subprocess_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **stream_options,
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
        if self._owned_mode and process.poll() is None:
            # A private writer may be blocked on a full pipe. Stop that child
            # before closing its binary stdin, then use the existing wait path.
            terminate_process_tree(process)
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
        if self._owned_mode:
            raise NodeAstWorkerError("owned_worker_requires_owned_request", {})
        payload = json.dumps(
            {
                "protocolVersion": 1,
                "requestId": str(request_id),
                "paths": [str(path) for path in paths],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return self._exchange(payload, request_id=request_id, timeout_seconds=timeout_seconds)

    def request_owned(
        self, *, source: OwnedSourceInput, request_id: str, timeout_seconds: float,
        wire_base=None,
    ) -> tuple[str, dict[str, int]]:
        if not self._owned_mode:
            raise NodeAstWorkerError("owned_request_requires_private_worker", {})
        try:
            if not isinstance(source, OwnedSourceInput):
                raise OwnedTextBridgeError("owned_input_invalid")
            payload, expected = source.request(request_id)
            self._owned_response_limit = json.loads(source.policy_json)["max_response_bytes"]
        except (OwnedTextBridgeError, ValueError, TypeError, AttributeError, UnicodeError):
            raise NodeAstWorkerError("owned_request_refused", {}) from None
        if wire_base is None:
            line, metrics = self._exchange(payload, request_id=request_id, timeout_seconds=timeout_seconds)
        else:
            reference = {'request_id': expected['requestId'], 'source_bytes': expected['source']['bytes'],
                'source_sha256': expected['source']['sha256'], 'source_policy_sha256': expected['policySha256']}
            line, metrics = self._exchange(payload, request_id=request_id, timeout_seconds=timeout_seconds,
                wire_base=wire_base, source_reference=reference)
        try:
            validate_owned_response(line, expected)
        except OwnedTextBridgeError as exc:
            self.close()
            raise NodeAstWorkerError(str(exc), metrics) from None
        return line, metrics

    @staticmethod
    def _write_owned(process: subprocess.Popen, wire: bytes, output_queue: queue.Queue) -> None:
        try:
            assert process.stdin is not None
            view = memoryview(wire)
            while view:
                written = process.stdin.write(view)
                if not written:
                    raise BrokenPipeError("owned_write_failed")
                view = view[written:]
        except (OSError, ValueError):
            output_queue.put(None)

    def _exchange(
        self, payload: str, *, request_id: str, timeout_seconds: float,
        wire_base=None, source_reference=None,
    ) -> tuple[str, dict[str, int]]:
        # Serialize once. A transport replay uses these identical bytes and
        # never reopens the source path or invokes its first-read owner again.
        wire = (payload + "\n").encode("utf-8") if self._owned_mode else payload + "\n"
        metrics = {
            "worker_process_starts": 0,
            "worker_restarts": 0,
            "request_replays": 0,
        }
        with self._lock:
            if self._owned_mode and wire_base is not None:
                observe_owned_wire_source(wire_base, wire, source_reference)
            for attempt in range(2):
                if not self._owned_mode:
                    process = self._process
                    if process is None or process.poll() is not None:
                        self._start()
                        metrics["worker_process_starts"] += 1
                try:
                    process = self._process
                    if self._owned_mode and (process is None or process.poll() is not None):
                        self._start()
                        metrics["worker_process_starts"] += 1
                        process = self._process
                    assert process is not None
                    if process.stdin is None:
                        raise BrokenPipeError("worker stdin unavailable")
                    started = time.perf_counter()
                    if self._owned_mode:
                        threading.Thread(target=self._write_owned,
                            args=(process, wire, self._stdout_queue), daemon=True).start()
                    else:
                        process.stdin.write(wire)
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
                            if self._owned_mode:
                                raise RuntimeError("owned_worker_no_response")
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
                    if self._owned_mode:
                        raise NodeAstWorkerError("owned_transport_failed", metrics) from None
                    raise NodeAstWorkerError(str(exc), metrics) from exc
        raise NodeAstWorkerError("worker request failed without response", metrics)

    def close(self) -> None:
        with self._lock:
            self._stop()

    def __enter__(self) -> "NodeAstWorkerSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

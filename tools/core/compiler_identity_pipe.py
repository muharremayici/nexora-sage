"""Neutral owned-child lifetime for fixed first-party identity facades only.

Endpoint paths and checks are supplied by trusted code, never request metadata.
This module owns no reader, grant, scope interpreter or result validator.
"""
from contextlib import contextmanager
import math
import os
import shutil
import subprocess
from tools.core import compiler_metadata_dispatch as shared
from tools.core.compiler_stream_driver import _PipeExchange, _parse_line


def _identity_node():
    executable = shutil.which("node")
    shared._need(executable is not None, "transport_node_unavailable", "prepare")
    return executable


@contextmanager
def _owned_identity_pipe(executable, child, wire, c, clock, check, limits):
    process = exchange = node = None
    error = None
    try:
        check("before_launch")
        shared._need(c["node_attempts"] == 0, "transport_already_launched", "launch")
        remaining = math.floor((clock.end - clock.last) * 1000)
        shared._need(remaining > 0, "transport_deadline", "launch")
        c["node_attempts"] += 1
        process = subprocess.Popen([executable, str(child), str(limits["child_input_bytes"]),
                                    str(limits["child_output_bytes"]), str(remaining)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                                   cwd=child.parents[2], env=dict(os.environ, NODE_OPTIONS="", NODE_PATH=""))
        exchange = _PipeExchange(process, limits["child_output_bytes"], clock.end, None)
        exchange.start()
        exchange.write(wire, close=True)
        node = _parse_line(exchange)
        c["node_outcome_received"] = True
        shared._need(exchange.read()[0] == "eof", "transport_extra_output", "child")
        status = process.wait(timeout=max(0, clock.end - clock.now()))
        exchange._wait(lambda: exchange.stderr_done)
        shared._need(status == 0 and exchange.stderr_bytes == 0, "transport_child_error", "child")
        c["node_returns"] += 1
        # The fixed caller validates its own result inside this lifetime. Its
        # primary exception reaches cleanup without a generic callback router.
        yield node
    except BaseException as exc:
        error = exc
        raise
    finally:
        try:
            if exchange is not None:
                try:
                    reaped, threads = exchange.cleanup()
                    c.update(node_reaped=reaped, io_threads_remaining=threads)
                except BaseException as exc:
                    if error is not None:
                        c["secondary_errors"].append(exc)
                        raise error
                    raise
            elif process is not None:
                try:
                    reaped, threads = _PipeExchange(process, limits["child_output_bytes"], clock.end, None).cleanup()
                    c.update(node_reaped=reaped, io_threads_remaining=threads)
                except BaseException as exc:
                    if error is not None:
                        c["secondary_errors"].append(exc)
                        raise error
                    raise
        finally:
            process = exchange = node = None
    shared._need(c["node_attempts"] == c["node_returns"] == 1
                 and c["node_reaped"] and c["io_threads_remaining"] == 0, "transport_cleanup", "settlement")

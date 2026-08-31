from __future__ import annotations

from tools.core.stdio import configure_utf8_stdio


class RecordingStream:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail

    def reconfigure(self, **kwargs: str) -> None:
        self.calls.append(kwargs)
        if self.fail:
            raise OSError("stream unavailable")


def test_configure_utf8_stdio_updates_both_current_process_streams() -> None:
    stdout = RecordingStream()
    stderr = RecordingStream()

    assert configure_utf8_stdio(stdout, stderr)
    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_configure_utf8_stdio_reports_best_effort_stream_failure() -> None:
    assert not configure_utf8_stdio(RecordingStream(fail=True), RecordingStream())

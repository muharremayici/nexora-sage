from __future__ import annotations

import time
from typing import Any

from tools.core.pipeline_registry import validator_execution_contract


class ValidatorProgress:
    def __init__(self, validator_id: str):
        self.validator_id = str(validator_id)
        contract = validator_execution_contract(self.validator_id)
        observability = contract.get("observability", {}) if isinstance(contract, dict) else {}
        self.every_items = max(1, int(observability.get("progress_every_items", 50) or 50))
        self.every_seconds = max(1.0, float(observability.get("progress_every_seconds", 5) or 5))
        self.started_at = time.perf_counter()
        self.last_emit_at = self.started_at
        self.last_emit_count = 0
        self.total = 0
        self.phase_name = "starting"

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self.started_at, 3)

    def _emit(self, state: str, **details: Any) -> None:
        fields = " ".join(f"{key}={value}" for key, value in details.items() if value not in (None, ""))
        suffix = f" {fields}" if fields else ""
        print(f"[{self.validator_id}] {state}{suffix}", flush=True)

    def start(self) -> None:
        self._emit("START", elapsed_seconds=self._elapsed())

    def phase(self, name: str, *, total: int | None = None, **details: Any) -> None:
        self.phase_name = str(name)
        if total is not None:
            self.total = max(0, int(total))
        self.last_emit_at = time.perf_counter()
        self.last_emit_count = 0
        self._emit("PHASE", name=self.phase_name, total=self.total or None, elapsed_seconds=self._elapsed(), **details)

    def advance(self, completed: int, **details: Any) -> None:
        completed = max(0, int(completed))
        now = time.perf_counter()
        item_due = completed - self.last_emit_count >= self.every_items
        time_due = now - self.last_emit_at >= self.every_seconds
        final_item = bool(self.total and completed >= self.total)
        if not (item_due or time_due or final_item):
            return
        self.last_emit_at = now
        self.last_emit_count = completed
        self._emit(
            "PROGRESS",
            phase=self.phase_name,
            completed=completed,
            total=self.total or None,
            elapsed_seconds=self._elapsed(),
            **details,
        )

    def complete(self, status: str, **details: Any) -> None:
        self._emit(str(status).upper(), elapsed_seconds=self._elapsed(), **details)

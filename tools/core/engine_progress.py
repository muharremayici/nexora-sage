from __future__ import annotations

import time
from typing import Any

from tools.core.logger import logger
from tools.core.heartbeat_cadence import record_execution_duration
from tools.core.operational_limits import pipeline_step_heartbeat_selection
from tools.core.pipeline_registry import load_pipeline_execution_policy


def engine_progress_contract(engine_id: str) -> dict[str, Any]:
    """Return the centralized bounded-progress contract for one engine."""

    policy = load_pipeline_execution_policy()
    configured = policy.get("engine_progress", {}) if isinstance(policy, dict) else {}
    default = configured.get("default", {}) if isinstance(configured, dict) else {}
    engines = configured.get("engines", {}) if isinstance(configured, dict) else {}
    override = engines.get(str(engine_id), {}) if isinstance(engines, dict) else {}
    return {
        **(default if isinstance(default, dict) else {}),
        **(override if isinstance(override, dict) else {}),
    }


class EngineProgress:
    """Emit bounded, phase-aware engine telemetry without per-file log noise."""

    def __init__(self, engine_id: str):
        self.engine_id = str(engine_id)
        contract = engine_progress_contract(self.engine_id)
        if "enabled" not in contract or "progress_every_items" not in contract:
            raise ValueError(f"[ENGINE_PROGRESS] Missing policy contract for engine '{self.engine_id}'")
        self.enabled = bool(contract.get("enabled", True))
        self.every_items = max(1, int(contract["progress_every_items"]))
        self.heartbeat_selection = pipeline_step_heartbeat_selection(scope="engine")
        self.every_seconds = float(self.heartbeat_selection["interval_seconds"])
        self.started_at = time.perf_counter()
        self.last_emit_at = self.started_at
        self.last_emit_count = 0
        self.total = 0
        self.phase_name = "starting"

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self.started_at, 3)

    def _emit(self, state: str, **details: Any) -> None:
        if not self.enabled:
            return
        fields = " ".join(f"{key}={value}" for key, value in details.items() if value not in (None, ""))
        suffix = f" {fields}" if fields else ""
        logger.info("[ENGINE_PROGRESS] engine=%s state=%s%s", self.engine_id, state, suffix)

    def start(self) -> None:
        self._emit(
            "START",
            elapsed_seconds=self._elapsed(),
            heartbeat_seconds=self.every_seconds,
            cadence_basis=self.heartbeat_selection.get("basis"),
            cadence_samples=self.heartbeat_selection.get("sample_count"),
        )

    def phase(self, name: str, *, total: int | None = None, **details: Any) -> None:
        self.phase_name = str(name)
        if total is not None:
            self.total = max(0, int(total))
        self.last_emit_at = time.perf_counter()
        self.last_emit_count = 0
        self._emit("PHASE", phase=self.phase_name, total=self.total or None, elapsed_seconds=self._elapsed(), **details)

    def checkpoint(self, name: str, **details: Any) -> None:
        self._emit("CHECKPOINT", phase=self.phase_name, name=str(name), elapsed_seconds=self._elapsed(), **details)

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
        record_execution_duration("engine_execution", self.engine_id, self._elapsed())

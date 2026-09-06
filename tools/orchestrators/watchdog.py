import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import threading
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.vendor_bootstrap import inject_vendor_paths
from tools.core.python_runtime_env import python_subprocess_env
from tools.core.stdio import configure_utf8_stdio

BASE_DIR = Path(_ROOT)
_VENDOR_PATHS = inject_vendor_paths(BASE_DIR)

configure_utf8_stdio()

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
    WATCHDOG_IMPORT_ERROR = ""
except ImportError as exc:
    WATCHDOG_AVAILABLE = False
    WATCHDOG_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

    class FileSystemEventHandler:  # pragma: no cover - fallback shim
        pass

    Observer = None

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.table import Table
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

    def _strip_rich_markup(value):
        text = str(value)
        return re.sub(r"\[/?[^\]]+\]", "", text)

    class _FallbackConsole:
        def print(self, *args, **kwargs):
            print(*(_strip_rich_markup(arg) for arg in args))

        def clear(self):
            return None

    class _FallbackPanel:
        @staticmethod
        def fit(text, **kwargs):
            return text

        def __new__(cls, text, **kwargs):
            return text

    class _FallbackTable:
        def __init__(self, title=None, **kwargs):
            self.title = title
            self.rows = []

        def add_column(self, *args, **kwargs):
            return None

        def add_row(self, *values):
            self.rows.append(values)

        def __str__(self):
            lines = [self.title] if self.title else []
            lines.extend(" | ".join(_strip_rich_markup(v) for v in row) for row in self.rows)
            return "\n".join(_strip_rich_markup(line) for line in lines)

    @contextmanager
    def _fallback_progress(*args, **kwargs):
        class _Progress:
            def add_task(self, *args, **kwargs):
                return 0
        yield _Progress()

    class _FallbackBox:
        DOUBLE_EDGE = "DOUBLE_EDGE"
        ROUNDED = "ROUNDED"
        SIMPLE = "SIMPLE"
        DOUBLE = "DOUBLE"

    box = _FallbackBox()
    Console = _FallbackConsole
    Panel = _FallbackPanel
    Table = _FallbackTable
    Progress = _fallback_progress

    class SpinnerColumn:  # pragma: no cover - fallback shim
        pass

    class TextColumn:  # pragma: no cover - fallback shim
        def __init__(self, *args, **kwargs):
            pass

orchestrator = importlib.import_module("tools.orchestrators.orchestrator")
console = Console()

from tools.core.language_registry import watch_extensions
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.operational_limits import watchdog_git_restore_timeout_seconds
from tools.core.config import CODE_MAPS_DIR, ROOT
from tools.core.decision_ownership import find_owned_decision_copies
from tools.core.path_identity import strip_current_directory_prefix
from tools.core.source_files import is_analysis_source_file
from tools.core.watchdog_runtime_contract import (
    load_watchdog_runtime_contract,
    validate_watchdog_audit_scope,
    watchdog_artifact_path,
    watchdog_integrity_state,
)

WATCH_EXTENSIONS = watch_extensions()
IGNORED_PATH_FRAGMENTS = {"output", ".raw", "__pycache__"}


def _sage_decision_ownership_violations(files) -> list[dict]:
    violations: list[dict] = []
    sage_root = CODE_MAPS_DIR.resolve()
    for file_label in files:
        path = Path(str(file_label)).resolve()
        try:
            relative = str(path.relative_to(sage_root)).replace("\\", "/")
        except ValueError:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="watchdog",
                category="caught_error",
                operation="sage_decision_ownership_guard",
                subject=str(path),
                reason="watchdog could not inspect a changed SAGE source file for central decision reconstruction",
                fallback="omit_ownership_finding_and_degrade_self_governance_claim",
                claim_impact="sage_self_watchdog_ownership_guard_degraded",
                exception=exc,
            )
            continue
        for finding in find_owned_decision_copies(relative, content):
            violations.append(
                {
                    "rule": finding["rule"],
                    "file": relative,
                    "repo_relative_path": relative,
                    "line": finding["line"],
                    "detail": (
                        f"Local decision set reconstructs '{finding['domain']}' ({finding['relationship']}); "
                        f"owner={finding['owner_file']}:{finding['owner_pointer']}."
                    ),
                    "recommended_action": finding["consumer_rule"],
                    "evidence": finding,
                }
            )
    return violations


def _watchdog_deep_proof_debt_policy(target_descriptor: dict | None = None) -> dict:
    descriptor_policy = (target_descriptor or {}).get("proof_debt_policy")
    if isinstance(descriptor_policy, dict) and descriptor_policy:
        return descriptor_policy
    try:
        from tools.core.pipeline_registry import load_pipeline_execution_policy

        policy = load_pipeline_execution_policy()
        mode = (policy.get("execution_modes") or {}).get("watchdog_save_pulse") or {}
        debt_policy = mode.get("deep_proof_debt_policy") or {}
        return debt_policy if isinstance(debt_policy, dict) else {}
    except Exception as exc:
        from tools.core.honesty_telemetry import record_honesty_event

        record_honesty_event(
            component="watchdog",
            category="caught_error",
            operation="load_deep_proof_debt_policy",
            subject="config/pipeline_execution_policy.json",
            reason="watchdog could not load deep proof debt policy",
            fallback="minimal_watchdog_proof_boundary",
            claim_impact="watchdog_proof_debt_degraded",
            exception=exc,
        )
        return {}


def _session_proof_debt(session: dict) -> dict:
    descriptor = session.get("target_descriptor") if isinstance(session.get("target_descriptor"), dict) else {}
    debt_field = str(descriptor.get("proof_debt_field") or "target_repository_deep_proof_debt")
    debt = session.get(debt_field)
    return debt if isinstance(debt, dict) else {}


def _target_bound_watchdog_action(
    mode: str,
    command_args: list,
    target_descriptor: dict | None,
) -> dict:
    descriptor = target_descriptor if isinstance(target_descriptor, dict) else {}
    argv = ["python", "sage.py", *[str(item) for item in command_args]]
    external_target = bool(descriptor.get("external_target"))
    subject_root = str(descriptor.get("subject_root") or "").strip()
    status = "BOUND"
    if external_target:
        if not subject_root:
            status = "BLOCKED"
        else:
            argv.extend(["--target-root", subject_root])
    return {
        "id": str(mode),
        "status": status,
        "argv": argv if status == "BOUND" else [],
        "command": subprocess.list2cmdline(argv) if status == "BOUND" else "",
        "target_binding": {
            key: descriptor.get(key)
            for key in (
                "system_scope",
                "acquisition_mode",
                "profile_id",
                "subject_root",
                "artifact_root",
                "external_target",
            )
        },
    }


def _watchdog_refresh_actions(debt_policy: dict, target_descriptor: dict | None) -> list[dict]:
    auto_policy = debt_policy.get("auto_refresh") if isinstance(debt_policy.get("auto_refresh"), dict) else {}
    commands = auto_policy.get("command_by_mode") if isinstance(auto_policy.get("command_by_mode"), dict) else {}
    modes = debt_policy.get("recommended_refresh_modes")
    modes = modes if isinstance(modes, list) else []
    return [
        _target_bound_watchdog_action(str(mode), commands.get(mode), target_descriptor)
        for mode in modes
        if isinstance(mode, str) and isinstance(commands.get(mode), list) and commands.get(mode)
    ]


def _watchdog_auto_refresh_plan(
    debt_policy: dict,
    debt_due: bool,
    target_descriptor: dict | None = None,
) -> dict:
    auto_policy = debt_policy.get("auto_refresh") if isinstance(debt_policy.get("auto_refresh"), dict) else {}
    mode = str(auto_policy.get("mode") or auto_policy.get("default_mode") or "advisory").strip()
    allowed = auto_policy.get("allowed_modes")
    allowed_modes = {str(item) for item in allowed} if isinstance(allowed, list) else {"advisory"}
    if mode not in allowed_modes:
        mode = str(auto_policy.get("default_mode") or "advisory").strip()
    commands = auto_policy.get("command_by_mode") if isinstance(auto_policy.get("command_by_mode"), dict) else {}
    command_args = commands.get(mode) if isinstance(commands.get(mode), list) else []
    timeouts = auto_policy.get("timeout_seconds_by_mode") if isinstance(auto_policy.get("timeout_seconds_by_mode"), dict) else {}
    try:
        timeout_seconds = int(timeouts.get(mode) or 0)
    except (TypeError, ValueError):
        timeout_seconds = 0
    enabled = debt_due and mode.startswith("auto_") and bool(command_args)
    if mode == "auto_release_deep" and auto_policy.get("release_deep_requires_explicit_policy") is not True:
        enabled = False
    action = _target_bound_watchdog_action(mode, command_args, target_descriptor)
    enabled = enabled and action["status"] == "BOUND"
    return {
        "mode": mode,
        "enabled": enabled,
        "status": "scheduled" if enabled else ("disabled" if mode == "advisory" else "not_due"),
        "command_args": command_args,
        "action": action,
        "timeout_seconds": timeout_seconds if timeout_seconds > 0 else None,
        "agent_rule": auto_policy.get(
            "agent_rule",
            "Automatic watchdog deep-proof refresh is policy-controlled and disabled unless explicitly enabled.",
        ),
    }


def _watchdog_pulse_ledger_policy(debt_policy: dict) -> dict:
    from tools.core.artifact_registry import artifact_metadata

    policy = debt_policy.get("pulse_ledger") if isinstance(debt_policy.get("pulse_ledger"), dict) else {}
    canonical_artifact = Path(artifact_metadata()["watchdog_pulse_ledger"]["path"]).name
    try:
        max_entries = int(policy.get("max_entries") or 200)
    except (TypeError, ValueError):
        max_entries = 200
    return {
        "enabled": bool(policy.get("enabled", True)),
        "artifact": str(policy.get("artifact") or canonical_artifact),
        "max_entries": max(10, max_entries),
        "unread_status": str(policy.get("unread_status") or "unresolved_unread"),
        "resolved_status": str(policy.get("resolved_status") or "resolved_by_absence_in_next_pulse"),
        "agent_rule": str(
            policy.get("agent_rule")
            or "Latest watchdog_session may be overwritten; unresolved pulse findings must remain queryable."
        ),
    }


def _run_surgical_pipeline_with_heartbeats(files: list[str], watchdog_profile: str) -> None:
    """Keep the watchdog visible while Atlas/bootstrap work runs before scheduler steps exist."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from tools.core.heartbeat_cadence import record_execution_duration
    from tools.core.operational_limits import pipeline_step_heartbeat_selection

    heartbeat_selection = pipeline_step_heartbeat_selection(scope="watchdog")
    interval = int(heartbeat_selection["interval_seconds"])
    started_at = time.time()
    logger.info(
        "[WATCHDOG] phase=surgical_pipeline heartbeat_seconds=%s cadence_basis=%s cadence_samples=%s",
        interval,
        heartbeat_selection.get("basis"),
        heartbeat_selection.get("sample_count"),
    )
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="sage-watchdog-pulse") as executor:
        future = executor.submit(
            orchestrator.run_surgical_pipeline,
            changed_files=files,
            watchdog_profile=watchdog_profile,
        )
        try:
            while True:
                try:
                    future.result(timeout=interval)
                    return
                except TimeoutError:
                    elapsed = time.time() - started_at
                    logger.info(
                        "[WATCHDOG_HEARTBEAT] phase=surgical_pipeline changed_files=%s elapsed_seconds=%.1f next_update_within_seconds=%s cadence_basis=%s",
                        len(files),
                        elapsed,
                        interval,
                        heartbeat_selection.get("basis"),
                    )
        finally:
            record_execution_duration("watchdog_pipeline_execution", watchdog_profile, time.time() - started_at)


def _watchdog_violation_fingerprint(violation: dict) -> str:
    payload = {
        "target_ref": str(violation.get("target_ref") or ""),
        "file": str(violation.get("repo_relative_path") or violation.get("workspace_rel") or violation.get("file") or ""),
        "rule": str(violation.get("rule") or violation.get("code") or ""),
        "detail": str(violation.get("detail") or violation.get("message") or "")[:500],
    }
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _load_watchdog_pulse_ledger(path: Path) -> dict:
    try:
        from tools.core.json_io import load_json_file

        payload = load_json_file(path, None)
        if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
            return payload
    except Exception as exc:
        from tools.core.honesty_telemetry import record_honesty_event

        record_honesty_event(
            component="watchdog",
            category="caught_error",
            operation="load_watchdog_pulse_ledger",
            subject=str(path),
            reason="Watchdog pulse ledger could not be loaded from SQLite-first artifact proxy.",
            fallback="empty_pulse_ledger",
            claim_impact="watchdog_unread_findings_degraded",
            exception=exc,
        )
    return {"meta": {"kind": "watchdog_pulse_ledger", "version": "v1"}, "entries": []}


def _update_watchdog_pulse_ledger(
    raw_dir: Path,
    files: list[str],
    violations: list[dict],
    debt_policy: dict,
    target_descriptor: dict | None = None,
) -> dict:
    from tools.core.config import save_json_atomic

    policy = _watchdog_pulse_ledger_policy(debt_policy)
    if not policy["enabled"]:
        return {"status": "disabled", "unresolved_unread_count": 0, "artifact": policy["artifact"]}
    path = raw_dir / policy["artifact"]
    generated_at = datetime.now(timezone.utc).isoformat()
    pulse_hash_basis = json.dumps(
        {"generated_at": generated_at, "files": sorted(str(item) for item in files)},
        sort_keys=True,
        ensure_ascii=False,
    )
    pulse_id = hashlib.sha256(pulse_hash_basis.encode("utf-8")).hexdigest()[:16]
    ledger = _load_watchdog_pulse_ledger(path)
    if isinstance(target_descriptor, dict):
        ledger["target_descriptor"] = target_descriptor
    entries = [entry for entry in ledger.get("entries", []) if isinstance(entry, dict)]
    from tools.core.watchdog_proof_debt import advance_watchdog_proof_debt

    proof_authority_root = (
        Path(str(target_descriptor.get("proof_authority_root"))).resolve()
        if isinstance(target_descriptor, dict) and target_descriptor.get("proof_authority_root")
        else raw_dir
    )
    proof_debt_state = advance_watchdog_proof_debt(
        ledger.get("proof_debt_state"),
        debt_policy,
        raw_dir=proof_authority_root,
        generated_at=generated_at,
        changed_file_count=len(files),
        violation_count=len(violations),
    )
    current_by_hash = {
        _watchdog_violation_fingerprint(violation): violation
        for violation in violations
        if isinstance(violation, dict)
    }
    current_hashes = set(current_by_hash)
    unresolved_status = policy["unread_status"]
    resolved_status = policy["resolved_status"]
    existing_unresolved = {
        str(entry.get("violation_hash")): entry
        for entry in entries
        if str(entry.get("status") or "") == unresolved_status
    }
    for violation_hash, entry in existing_unresolved.items():
        if violation_hash not in current_hashes:
            entry["status"] = resolved_status
            entry["resolved_at"] = generated_at
            entry["resolved_by_pulse_id"] = pulse_id
            entry["resolution_reason"] = "Violation fingerprint was absent from the next watchdog pulse."
    for violation_hash, violation in current_by_hash.items():
        existing = existing_unresolved.get(violation_hash)
        if existing:
            existing["last_seen_at"] = generated_at
            existing["last_seen_pulse_id"] = pulse_id
            existing["seen_count"] = int(existing.get("seen_count") or 1) + 1
            continue
        entries.append(
            {
                "violation_hash": violation_hash,
                "status": unresolved_status,
                "first_seen_at": generated_at,
                "last_seen_at": generated_at,
                "first_seen_pulse_id": pulse_id,
                "last_seen_pulse_id": pulse_id,
                "seen_count": 1,
                "target_ref": violation.get("target_ref") or "",
                "repo_relative_path": violation.get("repo_relative_path")
                or violation.get("workspace_rel")
                or violation.get("file")
                or "",
                "rule": violation.get("rule") or violation.get("code") or "",
                "detail": violation.get("detail") or violation.get("message") or "",
            }
        )
    entries = entries[-policy["max_entries"] :]
    unresolved_count = sum(1 for entry in entries if str(entry.get("status") or "") == unresolved_status)
    payload = {
        "meta": {
            "kind": "watchdog_pulse_ledger",
            "version": "v1",
            "generated_at": generated_at,
            "generator": "tools.orchestrators.watchdog",
        },
        "policy": policy,
        "latest_pulse": {
            "pulse_id": pulse_id,
            "changed_files": [str(item) for item in files],
            "current_violation_hashes": sorted(current_hashes),
        },
        "summary": {
            "status": "HAS_UNRESOLVED" if unresolved_count else "CLEAR",
            "entries": len(entries),
            "unresolved_unread_count": unresolved_count,
            "current_violation_count": len(current_hashes),
        },
        "proof_debt_state": proof_debt_state,
        "entries": entries,
    }
    save_json_atomic(path, payload)
    return {
        "status": payload["summary"]["status"],
        "artifact": policy["artifact"],
        "pulse_id": pulse_id,
        "entries": len(entries),
        "unresolved_unread_count": unresolved_count,
        "current_violation_count": len(current_hashes),
        "current_violation_hashes": sorted(current_hashes),
        "proof_debt_state": proof_debt_state,
        "agent_rule": policy["agent_rule"],
    }


class CodeMapsHandler(FileSystemEventHandler):
    def __init__(self, debounce_seconds=1.5, display_root: Path | None = None, mode: str = "ADVISE"):
        self.debounce_seconds = debounce_seconds
        self.display_root = display_root
        self.mode = mode.upper()
        self.timer = None
        self.changed_files = set()
        self.change_events = {}
        self.lock = threading.Lock()
        self.is_running = False
        self.pending_followup = False

    def _schedule_trigger_locked(self):
        if self.timer:
            self.timer.cancel()
        self.timer = threading.Timer(self.debounce_seconds, self.trigger_pipeline)
        self.timer.start()

    def _track_event_path(
        self,
        raw_path: str | None,
        event_kind: str = "modify",
        related_path: str | None = None,
    ):
        if not raw_path:
            return
        filepath = Path(raw_path)
        if filepath.suffix not in WATCH_EXTENSIONS:
            return
        if any(fragment in str(filepath) for fragment in IGNORED_PATH_FRAGMENTS):
            return

        with self.lock:
            path_text = str(filepath)
            self.changed_files.add(path_text)
            previous = self.change_events.get(path_text, {})
            effective_kind = (
                "create"
                if previous.get("event_kind") == "create" and event_kind == "modify"
                else event_kind
            )
            self.change_events[path_text] = {
                "path": path_text,
                "event_kind": effective_kind,
                "related_path": str(related_path or previous.get("related_path") or ""),
            }
            if self.is_running:
                self.pending_followup = True
                return
            self._schedule_trigger_locked()

    def on_modified(self, event):
        if event.is_directory:
            return
        self._track_event_path(event.src_path, "modify")

    def on_created(self, event):
        if event.is_directory:
            return
        self._track_event_path(event.src_path, "create")

    def on_deleted(self, event):
        if event.is_directory:
            return
        self._track_event_path(event.src_path, "delete")

    def on_moved(self, event):
        if event.is_directory:
            return
        src_path = getattr(event, "src_path", None)
        dest_path = getattr(event, "dest_path", None)
        self._track_event_path(src_path, "rename_from", related_path=dest_path)
        self._track_event_path(dest_path, "rename_to", related_path=src_path)

    def trigger_pipeline(self):
        with self.lock:
            self.timer = None
            if self.is_running:
                self.pending_followup = True
                return

            files_to_process = list(self.changed_files)
            events_to_process = [
                dict(self.change_events.get(file_path) or {"path": file_path, "event_kind": "modify"})
                for file_path in files_to_process
            ]
            self.changed_files.clear()
            for file_path in files_to_process:
                self.change_events.pop(file_path, None)
            self.is_running = True
            self.pending_followup = False

        self.run_analysis(
            files_to_process,
            watchdog_profile="live",
            input_origin="filesystem_event",
            acquisition={
                "mode": "filesystem_events",
                "change_events": events_to_process,
                "selected_existing_files": [
                    row["path"] for row in events_to_process if row.get("event_kind") not in {"delete", "rename_from"}
                ],
                "selected_tombstones": [
                    row["path"] for row in events_to_process if row.get("event_kind") in {"delete", "rename_from"}
                ],
                "omitted_existing_count": 0,
                "omitted_tombstone_count": 0,
                "tombstone_coverage_complete": True,
            },
        )

        with self.lock:
            self.is_running = False
            if self.changed_files:
                self.pending_followup = False
                self._schedule_trigger_locked()

    def run_analysis(
        self,
        files,
        watchdog_profile: str = "live",
        input_origin: str = "explicit_scope",
        acquisition: dict | None = None,
    ):
        from tools.core.config import RAW_DIR

        console.clear()
        console.print(
            Panel.fit(
                "[bold cyan]Nexora SAGE Watchdog[/bold cyan]\n"
                f"[dim]Incremental analysis active | {datetime.now().strftime('%H:%M:%S')}[/dim]",
                box=box.DOUBLE_EDGE,
                border_style="bright_blue",
            )
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            input_label = "sample files" if input_origin == "smoke_sample" else "changed files"
            progress.add_task(description=f"Analyzing {len(files)} {input_label}...", total=None)

            start_time = time.time()
            try:
                _run_surgical_pipeline_with_heartbeats(files, watchdog_profile)
                elapsed = time.time() - start_time

                normalized_scope = orchestrator.normalize_changed_file_scope(files)
                violations = []
                scope_validation = {
                    "status": "invalid",
                    "scope_match": False,
                    "scope_status": "empty",
                    "reason": "scoped_audit_not_loaded",
                }
                audit_report_path = watchdog_artifact_path("audit")
                try:
                    report_data = load_json_file(audit_report_path, {})
                    from tools.core.watchdog_runtime_contract import watchdog_artifact_identity

                    scope_validation = validate_watchdog_audit_scope(
                        report_data,
                        normalized_scope,
                        watchdog_artifact_identity(),
                    )
                    if scope_validation.get("status") == "valid":
                        violations = report_data.get("violations", []) if isinstance(report_data, dict) else []
                except Exception as exc:
                    from tools.core.honesty_telemetry import record_honesty_event

                    record_honesty_event(
                        component="watchdog",
                        category="caught_error",
                        operation="load_watchdog_audit_report",
                        subject=str(audit_report_path),
                        reason="watchdog could not load audit violations after surgical pipeline",
                        fallback="unknown_scope_integrity",
                        claim_impact="watchdog_clean_claim_disabled",
                        exception=exc,
                    )
                    violations = []

                acquisition = acquisition if isinstance(acquisition, dict) else {}
                if int(acquisition.get("omitted_tombstone_count") or 0) > 0:
                    scope_validation = dict(scope_validation)
                    scope_validation.update(
                        {
                            "status": "invalid",
                            "scope_match": False,
                            "scope_status": "partial",
                            "reason": "indexed_tombstones_omitted_from_directory_sample",
                        }
                    )
                scope_validation["acquisition"] = {
                    "mode": str(acquisition.get("mode") or input_origin),
                    "selected_existing_count": len(acquisition.get("selected_existing_files") or []),
                    "selected_tombstone_count": len(acquisition.get("selected_tombstones") or []),
                    "omitted_existing_count": int(acquisition.get("omitted_existing_count") or 0),
                    "omitted_tombstone_count": int(acquisition.get("omitted_tombstone_count") or 0),
                    "tombstone_coverage_complete": bool(acquisition.get("tombstone_coverage_complete", True)),
                }

                violations.extend(_sage_decision_ownership_violations(files))

                self.display_summary(
                    files,
                    elapsed,
                    violations,
                    scope_validation,
                    input_origin=input_origin,
                    acquisition=acquisition,
                )
                return True
            except Exception as exc:
                console.print(f"[bold red][FAIL] Analysis failed:[/bold red] {exc}")
                return False

    def display_summary(
        self,
        files,
        elapsed,
        violations=None,
        scope_validation=None,
        input_origin: str = "explicit_scope",
        acquisition: dict | None = None,
    ):
        violations = violations or []
        scope_validation = scope_validation if isinstance(scope_validation, dict) else {}
        integrity_states = load_watchdog_runtime_contract()["integrity_states"]
        integrity_state = watchdog_integrity_state(violations, scope_validation)
        if integrity_state == integrity_states["clean"]:
            integrity = f"[bold green]{integrity_state}[/bold green]"
        elif integrity_state == integrity_states["violations"]:
            integrity = f"[bold red]{integrity_state} ({len(violations)})[/bold red]"
        else:
            integrity = f"[bold yellow]{integrity_state}[/bold yellow]"
        session = self.write_session_report(
            files,
            elapsed,
            violations,
            scope_validation,
            integrity_state,
            input_origin=input_origin,
            acquisition=acquisition,
        )
        debt = _session_proof_debt(session) if isinstance(session, dict) else {}
        auto_refresh = debt.get("auto_refresh") if isinstance(debt.get("auto_refresh"), dict) else {}

        table = Table(
            title="[bold green][OK] Incremental analysis completed[/bold green]",
            box=box.ROUNDED,
            border_style=(
                "green"
                if integrity_state == integrity_states["clean"]
                else ("red" if integrity_state == integrity_states["violations"] else "yellow")
            ),
        )
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row(
            "Sample files" if input_origin == "smoke_sample" else "Changed files",
            str(len(files)),
        )
        table.add_row("Input origin", input_origin)
        table.add_row("Elapsed", f"{elapsed:.2f}s")
        table.add_row("Integrity", integrity)
        table.add_row(
            "Audit scope",
            (
                f"coverage={scope_validation.get('scope_status', 'unknown')} / "
                f"current_request={scope_validation.get('request_match', False)} / "
                f"complete={scope_validation.get('scope_match', False)}"
            ),
        )
        table.add_row("Mode", "Incremental / cache-aware")
        table.add_row("Claim boundary", "surgical_change_context")
        table.add_row("Deep-proof debt", str(debt.get("status") or "unknown"))
        table.add_row(
            "Debt counters",
            f"pulses={debt.get('pulses_since_broad_refresh')} changed_files={debt.get('changed_files_since_broad_refresh')}",
        )
        if debt.get("due_reasons"):
            table.add_row("Debt reasons", ", ".join(str(item) for item in debt.get("due_reasons", [])))
        table.add_row("Auto-refresh", f"{auto_refresh.get('mode', 'advisory')} / {auto_refresh.get('status', 'unknown')}")
        console.print(table)

        if integrity_state == integrity_states["unknown"]:
            console.print(
                Panel(
                    "Watchdog could not prove that scoped Audit covered the exact current change set. "
                    "No clean claim was emitted; rerun the current pulse before acting on absence of findings.",
                    title="S.A.G.E. Scope Unknown",
                    border_style="yellow",
                )
            )

        if debt.get("status") == "due":
            refresh_sequence = debt.get("recommended_refresh_sequence") if isinstance(debt.get("recommended_refresh_sequence"), list) else []
            lines = [
                "[bold yellow]Target-repository deep-proof debt is due.[/bold yellow]",
                str(debt.get("agent_rule") or ""),
                "",
                "[bold]Repo-wide artifacts not proven by this watchdog pulse:[/bold]",
            ]
            lines.extend(f"- {item}" for item in debt.get("repo_wide_artifacts_not_proven_by_watchdog", []))
            if refresh_sequence:
                lines.extend(["", "[bold]Recommended refresh sequence:[/bold]"])
                lines.extend(f"- {item}" for item in refresh_sequence)
            lines.extend(
                [
                    "",
                    f"Auto-refresh policy: {auto_refresh.get('mode', 'advisory')} / {auto_refresh.get('status', 'unknown')}",
                ]
            )
            console.print(Panel("\n".join(lines), title="S.A.G.E. Proof Debt", border_style="yellow"))

        if violations:
            violation_table = Table(
                title="[bold red][WARN] Policy violations[/bold red]",
                box=box.SIMPLE,
                header_style="bold red",
            )
            violation_table.add_column("File", style="yellow")
            violation_table.add_column("Rule", style="red")
            violation_table.add_column("Detail", style="dim")

            for violation in violations[:5]:
                file_label = self._violation_display_file(violation)
                violation_table.add_row(file_label, violation.get("rule", ""), violation.get("detail", ""))

            if len(violations) > 5:
                violation_table.add_row("...", "more", f"+{len(violations) - 5} items")

            console.print(violation_table)

            if self.mode == "ENFORCE":
                self.trigger_circuit_breaker(files, violations)
            elif self.mode == "ADVISE":
                self.display_remediation_advice(violations)
            else:
                console.print("[dim red]Review the report or re-run targeted pipeline steps after fixes.[/dim red]")

        self._maybe_run_deep_proof_auto_refresh(session)
        console.print("\n[dim]Watching for the next change burst...[/dim]")

    def _maybe_run_deep_proof_auto_refresh(self, session: dict):
        debt = _session_proof_debt(session) if isinstance(session, dict) else {}
        descriptor = session.get("target_descriptor") if isinstance(session, dict) and isinstance(session.get("target_descriptor"), dict) else {}
        auto_refresh = debt.get("auto_refresh") if isinstance(debt.get("auto_refresh"), dict) else {}
        if not auto_refresh.get("enabled"):
            return False
        if descriptor.get("release_proof_behavior") == "explicit_only_non_recursive":
            console.print("[yellow]Watchdog auto-refresh blocked: self-target proof remains explicit and non-recursive.[/yellow]")
            return False
        action = auto_refresh.get("action") if isinstance(auto_refresh.get("action"), dict) else {}
        action_argv = action.get("argv") if isinstance(action.get("argv"), list) else []
        action_argv = [str(item) for item in action_argv if str(item).strip()]
        if action.get("status") != "BOUND" or len(action_argv) < 3:
            return False
        command = [sys.executable, str(BASE_DIR / "sage.py"), *action_argv[2:]]
        console.print(
            Panel(
                "\n".join(
                    [
                        "[bold yellow]Watchdog auto-refresh is enabled by policy.[/bold yellow]",
                        f"Mode: {auto_refresh.get('mode')}",
                        f"Command: {' '.join(command)}",
                        f"Timeout: {auto_refresh.get('timeout_seconds') or 'not configured'}s",
                    ]
                ),
                title="S.A.G.E. Auto Refresh",
                border_style="yellow",
            )
        )
        try:
            timeout_seconds = auto_refresh.get("timeout_seconds")
            completed = subprocess.run(
                command,
                cwd=str(BASE_DIR),
                check=False,
                env=python_subprocess_env(
                    os.environ,
                    code_maps_dir=BASE_DIR,
                    vendor_paths=_VENDOR_PATHS,
                ),
                timeout=timeout_seconds,
            )
            status = "completed" if completed.returncode == 0 else f"failed:{completed.returncode}"
            console.print(f"[yellow]Watchdog auto-refresh {status}.[/yellow]")
            return completed.returncode == 0
        except subprocess.TimeoutExpired as exc:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="watchdog",
                category="caught_error",
                operation="auto_refresh_deep_proof_debt_timeout",
                subject=" ".join(command),
                severity="error",
                reason="watchdog auto-refresh exceeded its policy timeout",
                fallback="manual_refresh_sequence",
                claim_impact="watchdog_proof_debt_unresolved",
                exception=exc,
            )
            console.print(f"[bold red]Watchdog auto-refresh timed out:[/bold red] {exc}")
            return False
        except Exception as exc:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="watchdog",
                category="caught_error",
                operation="auto_refresh_deep_proof_debt",
                subject=" ".join(command),
                severity="error",
                reason="watchdog auto-refresh command failed before completion",
                fallback="manual_refresh_sequence",
                claim_impact="watchdog_proof_debt_unresolved",
                exception=exc,
            )
            console.print(f"[bold red]Watchdog auto-refresh failed:[/bold red] {exc}")
            return False

    def display_remediation_advice(self, violations):
        """Displays educative remediation prompts without rolling back."""
        prompt = self.generate_agentic_remediation_prompt(violations, reverted=False)
        console.print(
            Panel(
                f"[bold yellow]ARCHITECTURAL GUIDANCE[/bold yellow]\n\n{prompt}",
                title="S.A.G.E. Advisor",
                border_style="yellow",
            )
        )

    def trigger_circuit_breaker(self, files, violations):
        """Automatically rolls back changes and generates educative remediation prompts."""
        from tools.core.hitl_policy import authorize_watchdog_restore

        approval = authorize_watchdog_restore(self.display_root or Path.cwd())
        if not approval.get("approved"):
            console.print(
                Panel(
                    "[bold yellow]ENFORCEMENT REQUIRES HUMAN APPROVAL[/bold yellow]\n"
                    "No files were restored. Watchdog has fallen back to advisory behavior.\n"
                    f"Gate: {approval.get('gate')}\nScope: {approval.get('scope')}\nReason: {approval.get('reason')}",
                    border_style="yellow",
                )
            )
            self.display_remediation_advice(violations)
            return False
        console.print(
            Panel(
                "[bold red]CIRCUIT BREAKER TRIGGERED[/bold red]\n"
                "Architectural policy violation detected. Rolling back changes to preserve system integrity.",
                border_style="bold red",
            )
        )
        
        for file_path in files:
            try:
                subprocess.run(
                    ["git", "restore", str(file_path)],
                    check=False,
                    capture_output=True,
                    timeout=watchdog_git_restore_timeout_seconds(),
                )
            except subprocess.TimeoutExpired as exc:
                from tools.core.honesty_telemetry import record_honesty_event

                record_honesty_event(
                    component="watchdog",
                    category="caught_error",
                    operation="git_restore_timeout",
                    subject=str(file_path),
                    severity="error",
                    reason="watchdog circuit breaker git restore exceeded its timeout",
                    fallback="advisory_remediation_prompt",
                    claim_impact="auto_restore_not_proven",
                    exception=exc,
                )
            except Exception as exc:
                from tools.core.honesty_telemetry import record_honesty_event

                record_honesty_event(
                    component="watchdog",
                    category="caught_error",
                    operation="git_restore",
                    subject=str(file_path),
                    severity="error",
                    reason="watchdog circuit breaker could not restore a changed file",
                    fallback="advisory_remediation_prompt",
                    claim_impact="auto_restore_not_proven",
                    exception=exc,
                )
        
        prompt = self.generate_agentic_remediation_prompt(violations, reverted=True)
        
        # Log the remediation
        from tools.core.config import LOGS_DIR, save_text_atomic
        remediation_log_path = LOGS_DIR / "remediation.log"
        log_entry = (
            f"[{datetime.now().isoformat()}] CIRCUIT_BREAKER_ROLLBACK\n"
            f"Files: {', '.join(map(str, files))}\n"
            f"Violations: {len(violations)}\n"
            f"Remediation Prompt:\n{prompt}\n"
            f"{'='*80}\n"
        )
        
        # Simple append logic for logs
        if remediation_log_path.exists():
            with open(remediation_log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        else:
            save_text_atomic(remediation_log_path, log_entry)

        console.print(
            Panel(
                f"[bold yellow]AGENTIC REMEDIATION PROMPT[/bold yellow]\n\n{prompt}",
                title="S.A.G.E. Guidance",
                border_style="yellow",
            )
        )
        return True

    def generate_agentic_remediation_prompt(self, violations, reverted: bool = False):
        """Generates a high-fidelity prompt for the AI agent to understand and fix the violation."""
        v_list = []
        for v in violations[:3]:
            file_label = self._violation_display_file(v)
            v_list.append(
                f"- [FILE] {file_label}\n  [TARGET_REF] {v.get('target_ref', '')}\n"
                f"  [RULE] {v.get('rule')}\n  [DETAIL] {v.get('detail')}\n"
                f"  [SUGGESTION] {v.get('recommended_action', 'Adhere to architectural boundaries defined in the doctrine.')}"
            )
        
        opening = (
            "STOP! Your recent change has been automatically reverted by Nexora S.A.G.E. because it violated "
            "the project's architectural integrity rules."
            if reverted
            else "Architectural policy violations were detected. No files were reverted because watchdog is running in ADVISE mode."
        )
        closing = (
            "I have restored the files to their previous state. Please re-evaluate your approach and try again."
            if reverted
            else "Review the findings above, then decide whether to fix them, mark intentional exceptions, or rerun in ENFORCE mode."
        )
        violations_text = "\n".join(v_list)
        prompt = (
            f"{opening}\n\n"
            "REASON FOR REJECTION:\n"
            f"{violations_text}\n\n"
            "HOW TO PROCEED:\n"
            "1. Review the 'architecture_doctrine.json' and the component's public API contract.\n"
            "2. Do not attempt to bypass these boundaries by direct imports or mutable state hacks.\n"
            "3. Use the prescribed abstraction layers (e.g., Services, Repositories, or Hexagonal ports).\n"
            "4. If you believe this is a false positive, verify the 'evidence ladder' in the audit report.\n\n"
            f"{closing}"
        )
        return prompt

    def _violation_display_file(self, violation: dict) -> str:
        repo_path = (
            violation.get("repo_relative_path")
            or violation.get("workspace_rel")
            or violation.get("file", "")
        )
        return self._shorten_path(repo_path)

    def write_session_report(
        self,
        files,
        elapsed,
        violations=None,
        scope_validation=None,
        integrity_state: str | None = None,
        input_origin: str = "explicit_scope",
        acquisition: dict | None = None,
    ):
        from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic

        violations = violations or []
        scope_validation = scope_validation if isinstance(scope_validation, dict) else {}
        runtime_contract = load_watchdog_runtime_contract()
        integrity_state = integrity_state or watchdog_integrity_state(violations, scope_validation)
        shortened_files = [self._session_repo_relative_path(file_label) for file_label in files]
        acquisition = acquisition if isinstance(acquisition, dict) else {}
        change_events = []
        for row in acquisition.get("change_events") or []:
            if not isinstance(row, dict):
                continue
            change_events.append(
                {
                    "path": self._session_repo_relative_path(row.get("path") or ""),
                    "event_kind": str(row.get("event_kind") or "unknown"),
                    "related_path": (
                        self._session_repo_relative_path(row.get("related_path"))
                        if row.get("related_path")
                        else ""
                    ),
                }
            )
        from tools.core.watchdog_target_context import current_watchdog_target_descriptor

        target_descriptor = current_watchdog_target_descriptor()
        debt_policy = _watchdog_deep_proof_debt_policy(target_descriptor)
        target_descriptor["proof_debt_policy"] = debt_policy
        debt_field = str(target_descriptor.get("proof_debt_field") or "target_repository_deep_proof_debt")
        selected_tombstones = {
            self._session_repo_relative_path(path)
            for path in acquisition.get("selected_tombstones") or []
        }
        actual_changed_files = (
            sorted(selected_tombstones)
            if input_origin == "smoke_sample"
            else shortened_files
        )
        pulse_ledger = _update_watchdog_pulse_ledger(
            RAW_DIR,
            actual_changed_files,
            violations[:25],
            debt_policy,
            target_descriptor,
        )
        debt_state = pulse_ledger.get("proof_debt_state") if isinstance(pulse_ledger.get("proof_debt_state"), dict) else {}
        debt_thresholds = debt_state.get("thresholds") if isinstance(debt_state.get("thresholds"), dict) else {}
        violation_threshold = debt_thresholds.get("violation_warning_threshold")
        debt_status = str(debt_state.get("status") or "due")
        skipped_repo_wide = debt_policy.get("repo_wide_artifacts_not_proven_by_watchdog")
        skipped_repo_wide = skipped_repo_wide if isinstance(skipped_repo_wide, list) else []
        recommended_refresh_actions = _watchdog_refresh_actions(debt_policy, target_descriptor)
        recommended_refresh = [
            str(action.get("command"))
            for action in recommended_refresh_actions
            if action.get("status") == "BOUND" and action.get("command")
        ]
        proof_follow_up = str(recommended_refresh[-1]) if recommended_refresh else "not_available_policy_contract_incomplete"
        producer_command_raw = os.environ.get("SAGE_WATCHDOG_PRODUCER_COMMAND", "")
        try:
            producer_command = json.loads(producer_command_raw) if producer_command_raw else []
        except json.JSONDecodeError:
            producer_command = []
        if not isinstance(producer_command, list):
            producer_command = []
        producer = {
            "kind": (
                "synthetic_smoke_validation"
                if input_origin == "smoke_sample"
                else "live_filesystem_observer"
                if input_origin == "filesystem_event"
                else "explicit_scope_invocation"
            ),
            "command": [str(item) for item in producer_command],
            "command_status": "captured" if producer_command else "not_provided",
        }
        session = {
            "meta": {
                "kind": "watchdog_session",
                "version": "v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generator": "tools.orchestrators.watchdog",
            },
            "summary": {
                "input_origin": input_origin,
                "input_files": len(files),
                "changed_files": len(actual_changed_files),
                "sample_files": len(shortened_files) if input_origin == "smoke_sample" else 0,
                "selected_existing_files": len(acquisition.get("selected_existing_files") or []),
                "selected_tombstones": len(acquisition.get("selected_tombstones") or []),
                "omitted_existing_files": int(acquisition.get("omitted_existing_count") or 0),
                "omitted_tombstones": int(acquisition.get("omitted_tombstone_count") or 0),
                "elapsed_seconds": round(float(elapsed), 3),
                "integrity": integrity_state,
                "violation_count": len(violations),
                "mode": "Incremental / cache-aware",
                "interface": "terminal_rich_plus_persistent_report",
                "evidence_scope": str(runtime_contract["evidence_scope"]),
                "claim_boundary": "surgical_change_context",
                "not_release_proof": True,
            },
            "scoped_audit_validation": scope_validation,
            "path_contract": {
                "analysis_root": str(ROOT),
                "open_files_with": "analysis_root + input_files or violations[].repo_relative_path",
                "target_ref_usage": "Use target_ref for SAGE/MCP follow-up references, not as a filesystem path.",
            },
            "target_descriptor": target_descriptor,
            "producer": producer,
            "proof_boundary": {
                "agent_rule": (
                    "Treat this watchdog packet as save-time context only. It can guide the next edit, "
                    "but it does not prove full repository health, dead-code completeness, circular-dependency "
                    "freshness, or release readiness."
                ),
                "requires_deeper_profile_for_release_claim": True,
                "recommended_deep_proof": proof_follow_up,
            },
            "input_origin": input_origin,
            "input_files": shortened_files,
            "changed_files": actual_changed_files,
            "sample_files": shortened_files if input_origin == "smoke_sample" else [],
            "change_events": change_events,
            "acquisition": {
                "mode": str(acquisition.get("mode") or input_origin),
                "selected_existing_files": sorted(
                    self._session_repo_relative_path(path)
                    for path in acquisition.get("selected_existing_files") or []
                ),
                "selected_tombstones": sorted(selected_tombstones),
                "omitted_existing_count": int(acquisition.get("omitted_existing_count") or 0),
                "omitted_tombstone_count": int(acquisition.get("omitted_tombstone_count") or 0),
                "tombstone_coverage_complete": bool(acquisition.get("tombstone_coverage_complete", True)),
            },
            "violations": violations[:25],
        }
        session[debt_field] = {
            "status": debt_status,
            "debt_name": debt_policy.get("debt_name") or "not_available_policy_contract_incomplete",
            "reason": (
                "Watchdog proof debt crossed one or more centrally declared risk thresholds; run the "
                "declared proof follow-up before trusting broader claims."
                if debt_status == "due"
                else "Pulse count, accumulated change scope and live violations remain below the configured deep-proof thresholds."
            ),
            "pulse_warning_threshold": debt_policy.get("pulse_warning_threshold"),
            "changed_file_warning_threshold": debt_policy.get("changed_file_warning_threshold"),
            "violation_warning_threshold": violation_threshold,
            "due_reasons": debt_state.get("due_reasons", []),
            "pulses_since_broad_refresh": debt_state.get("pulses_since_broad_refresh"),
            "changed_files_since_broad_refresh": debt_state.get("changed_files_since_broad_refresh"),
            "broad_refresh_detected": debt_state.get("broad_refresh_detected", False),
            "broad_proof_observation": debt_state.get("broad_proof_observation", {}),
            "policy_contract_error": debt_state.get("policy_contract_error"),
            "repo_wide_artifacts_not_proven_by_watchdog": skipped_repo_wide,
            "recommended_refresh_sequence": recommended_refresh,
            "recommended_refresh_actions": recommended_refresh_actions,
            "agent_rule": debt_policy.get(
                "agent_rule",
                "Watchdog is a live constitutional guard and ContextOS signal loop, not full target-repository proof.",
            ),
        }
        session[debt_field]["auto_refresh"] = _watchdog_auto_refresh_plan(
            debt_policy,
            debt_status == "due",
            target_descriptor,
        )
        session["watchdog_pulse_ledger"] = pulse_ledger
        try:
            from tools.core.governance_trace import record_watchdog_session_trace

            trace = record_watchdog_session_trace(session)
            session["governance_trace"] = {
                "status": "recorded",
                "trace_id": trace["trace_id"],
                "claim_boundary": trace["claim_boundary"],
            }
        except Exception as exc:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="watchdog",
                category="caught_error",
                operation="record_governance_trace",
                subject="watchdog_session",
                severity="warning",
                reason="Watchdog session trace could not be persisted.",
                fallback="session_report_without_governance_trace",
                claim_impact="failure_attribution_trace_incomplete",
                exception=exc,
            )
            session["governance_trace"] = {
                "status": "not_available",
                "reason": "trace_persistence_failed",
            }
        save_json_atomic(RAW_DIR / "watchdog_session.json", session)
        save_text_atomic(REPORTS_DIR / "watchdog_session.md", self.render_session_report(session))
        return session

    def render_session_report(self, session):
        meta = session.get("meta", {})
        summary = session.get("summary", {})
        lines = [
            "# Watchdog Session",
            "",
            f"- generated_at: `{meta.get('generated_at')}`",
            f"- input_origin: `{summary.get('input_origin')}`",
            f"- input_files: `{summary.get('input_files')}`",
            f"- changed_files: `{summary.get('changed_files')}`",
            f"- elapsed_seconds: `{summary.get('elapsed_seconds')}`",
            f"- integrity: `{summary.get('integrity')}`",
            f"- violation_count: `{summary.get('violation_count')}`",
            f"- mode: `{summary.get('mode')}`",
            f"- interface: `{summary.get('interface')}`",
            f"- evidence_scope: `{summary.get('evidence_scope')}`",
            f"- claim_boundary: `{summary.get('claim_boundary')}`",
            f"- not_release_proof: `{summary.get('not_release_proof')}`",
            f"- scoped_audit_status: `{(session.get('scoped_audit_validation') or {}).get('status')}`",
            f"- scoped_audit_scope_status: `{(session.get('scoped_audit_validation') or {}).get('scope_status')}`",
            f"- scoped_audit_request_match: `{(session.get('scoped_audit_validation') or {}).get('request_match')}`",
            f"- scoped_audit_scope_match: `{(session.get('scoped_audit_validation') or {}).get('scope_match')}`",
            "",
            "## Proof Boundary",
            "",
            f"- agent_rule: {(session.get('proof_boundary') or {}).get('agent_rule', '')}",
            f"- requires_deeper_profile_for_release_claim: `{(session.get('proof_boundary') or {}).get('requires_deeper_profile_for_release_claim')}`",
            f"- recommended_deep_proof: `{(session.get('proof_boundary') or {}).get('recommended_deep_proof', '')}`",
            "",
            "## Target Repository Deep-Proof Debt",
            "",
            f"- status: `{_session_proof_debt(session).get('status')}`",
            f"- debt_name: `{_session_proof_debt(session).get('debt_name')}`",
            f"- reason: {_session_proof_debt(session).get('reason', '')}",
            f"- due_reasons: `{', '.join(str(item) for item in _session_proof_debt(session).get('due_reasons', [])) or 'none'}`",
            f"- pulses_since_broad_refresh: `{_session_proof_debt(session).get('pulses_since_broad_refresh')}`",
            f"- changed_files_since_broad_refresh: `{_session_proof_debt(session).get('changed_files_since_broad_refresh')}`",
            f"- broad_refresh_detected: `{_session_proof_debt(session).get('broad_refresh_detected')}`",
            f"- policy_contract_error: `{_session_proof_debt(session).get('policy_contract_error')}`",
            f"- agent_rule: {_session_proof_debt(session).get('agent_rule', '')}",
            f"- auto_refresh_mode: `{(_session_proof_debt(session).get('auto_refresh') or {}).get('mode')}`",
            f"- auto_refresh_status: `{(_session_proof_debt(session).get('auto_refresh') or {}).get('status')}`",
            f"- pulse_ledger_status: `{((session.get('watchdog_pulse_ledger') or {}).get('status'))}`",
            f"- pulse_ledger_unresolved_unread_count: `{((session.get('watchdog_pulse_ledger') or {}).get('unresolved_unread_count'))}`",
            f"- governance_trace_status: `{((session.get('governance_trace') or {}).get('status'))}`",
            f"- governance_trace_id: `{((session.get('governance_trace') or {}).get('trace_id', ''))}`",
            "- repo_wide_artifacts_not_proven_by_watchdog:",
        ]
        lines.extend(
            f"  - `{item}`"
            for item in _session_proof_debt(session).get(
                "repo_wide_artifacts_not_proven_by_watchdog", []
            )
        )
        lines.extend(
            [
                "- recommended_refresh_sequence:",
            ]
        )
        lines.extend(
            f"  - `{item}`"
            for item in _session_proof_debt(session).get(
                "recommended_refresh_sequence", []
            )
        )
        lines.extend(
            [
                "",
            "## Path Contract",
            "",
            f"- analysis_root: `{(session.get('path_contract') or {}).get('analysis_root', '')}`",
            f"- open_files_with: `{(session.get('path_contract') or {}).get('open_files_with', '')}`",
            f"- target_ref_usage: {(session.get('path_contract') or {}).get('target_ref_usage', '')}",
            "",
            "## Input Files",
            "",
            ]
        )
        input_files = session.get("input_files") or session.get("changed_files") or []
        if input_files:
            lines.extend(f"- `{file_label}`" for file_label in input_files)
        else:
            lines.append("- None.")

        lines.extend(["", "## Violations", ""])
        violations = session.get("violations") or []
        if violations:
            lines.extend(["| Target file | Target ref | Rule | Detail |", "|---|---|---|---|"])
            for violation in violations:
                repo_path = (
                    violation.get("repo_relative_path")
                    or violation.get("workspace_rel")
                    or violation.get("file", "")
                )
                lines.append(
                    f"| `{self._shorten_path(repo_path)}` | `{violation.get('target_ref', '')}` | `{violation.get('rule', '')}` | {violation.get('detail', '')} |"
                )
        else:
            lines.append("- None.")
        return "\n".join(lines) + "\n"

    def _shorten_path(self, file_label: str) -> str:
        normalized = str(file_label or "").replace("\\", "/")
        if not normalized:
            return normalized
        try:
            from tools.core.config import ROOT

            path_obj = Path(file_label)
            if path_obj.is_absolute():
                try:
                    return path_obj.resolve().relative_to(ROOT).as_posix()
                except ValueError:
                    pass
        except Exception as exc:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="watchdog",
                category="caught_error",
                operation="normalize_display_path",
                subject=str(file_label),
                reason="watchdog could not normalize display path against repository root",
                fallback="raw_path_label",
                claim_impact="none",
                exception=exc,
            )
        if self.display_root:
            root = str(self.display_root).replace("\\", "/").rstrip("/")
            if normalized.startswith(root + "/"):
                return normalized[len(root) + 1 :]
            if normalized == root:
                return "."
        return normalized

    @staticmethod
    def _session_repo_relative_path(file_label: str) -> str:
        """Keep session evidence openable from analysis_root; display shortening is not truth."""
        normalized = str(file_label or "").replace("\\", "/").strip()
        if not normalized:
            return normalized
        try:
            from tools.core.config import ROOT

            path_obj = Path(file_label)
            if path_obj.is_absolute():
                return path_obj.resolve().relative_to(ROOT).as_posix()
            if ".." in path_obj.parts:
                return (Path.cwd() / path_obj).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return normalized
        except Exception as exc:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="watchdog",
                category="caught_error",
                operation="normalize_session_repo_relative_path",
                subject=str(file_label),
                reason="watchdog could not normalize session evidence against analysis root",
                fallback="raw_path_label",
                claim_impact="watchdog_session_path_grounding_degraded",
                exception=exc,
            )
        return strip_current_directory_prefix(normalized)


def _canonical_indexed_watch_paths() -> dict[Path, dict]:
    """Project current Atlas membership into filesystem identities for event acquisition."""
    from tools.core.projects_registry import resolve_runtime_projects

    atlas = orchestrator.load_atlas_data()
    projects = resolve_runtime_projects(ROOT)
    indexed: dict[Path, dict] = {}
    for project_key, project_data in (atlas.items() if isinstance(atlas, dict) else []):
        project_root = projects.get(str(project_key))
        files = project_data.get("files") if isinstance(project_data, dict) else None
        if project_root is None or not isinstance(files, dict):
            continue
        for rel_path in files:
            try:
                absolute = (Path(project_root) / str(rel_path)).resolve()
                absolute.relative_to(ROOT.resolve())
            except (OSError, ValueError):
                continue
            indexed[absolute] = {
                "project": str(project_key),
                "rel_path": str(rel_path).replace("\\", "/"),
                "target_ref": f"{project_key}::{str(rel_path).replace(chr(92), '/')}",
            }
    return indexed


def _watch_change_event(path: Path, indexed_paths: dict[Path, dict]) -> dict:
    resolved = path.resolve()
    indexed = indexed_paths.get(resolved)
    exists = resolved.is_file()
    event_kind = "modify" if exists and indexed else "create" if exists else "delete" if indexed else "unknown"
    return {
        "path": str(resolved),
        "event_kind": event_kind,
        "related_path": "",
        "previously_indexed": bool(indexed),
        "target_ref": str((indexed or {}).get("target_ref") or ""),
    }


def collect_smoke_file_selection(
    watch_path: Path,
    limit: int = 5,
    indexed_paths: dict[Path, dict] | None = None,
) -> dict:
    indexed_paths = indexed_paths if indexed_paths is not None else _canonical_indexed_watch_paths()
    resolved_watch_path = watch_path.resolve()
    exact_indexed = resolved_watch_path in indexed_paths
    if watch_path.is_file() or (not watch_path.exists() and exact_indexed):
        supported = (
            watch_path.suffix.lower() in WATCH_EXTENSIONS
            and is_analysis_source_file(str(watch_path))
        )
        selected = [str(watch_path)] if supported else []
        event = _watch_change_event(watch_path, indexed_paths) if supported else None
        tombstone = bool(event and event["event_kind"] == "delete")
        return {
            "mode": "exact_tombstone" if tombstone else "exact_file",
            "selected_files": selected,
            "selected_existing_files": [] if tombstone else selected,
            "selected_tombstones": selected if tombstone else [],
            "change_events": [event] if event else [],
            "candidate_count": 1 if supported else 0,
            "omitted_count": 0,
            "candidate_existing_count": 0 if tombstone else (1 if supported else 0),
            "candidate_tombstone_count": 1 if tombstone else 0,
            "omitted_existing_count": 0,
            "omitted_tombstone_count": 0,
            "tombstone_coverage_complete": True,
        }

    source_candidates: list[str] = []
    supporting_candidates: list[str] = []
    for file_path in sorted(watch_path.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in WATCH_EXTENSIONS:
            continue
        relative_parts = {part.lower() for part in file_path.relative_to(watch_path).parts}
        if relative_parts.intersection(IGNORED_PATH_FRAGMENTS):
            continue
        target = source_candidates if is_analysis_source_file(file_path) else supporting_candidates
        target.append(str(file_path))
    existing_candidates = source_candidates + supporting_candidates
    tombstone_candidates = []
    for indexed_path in sorted(indexed_paths, key=lambda item: item.as_posix().lower()):
        try:
            indexed_path.relative_to(resolved_watch_path)
        except ValueError:
            continue
        if not indexed_path.exists():
            tombstone_candidates.append(str(indexed_path))
    selected_tombstones = tombstone_candidates[:limit]
    remaining = max(0, limit - len(selected_tombstones))
    selected_existing = existing_candidates[:remaining]
    selected = selected_tombstones + selected_existing
    omitted_tombstones = max(0, len(tombstone_candidates) - len(selected_tombstones))
    omitted_existing = max(0, len(existing_candidates) - len(selected_existing))
    return {
        "mode": "directory_tombstone_sample" if tombstone_candidates else "directory_sample",
        "selected_files": selected,
        "selected_existing_files": selected_existing,
        "selected_tombstones": selected_tombstones,
        "change_events": [
            _watch_change_event(Path(path), indexed_paths)
            for path in selected
        ],
        "candidate_count": len(existing_candidates) + len(tombstone_candidates),
        "omitted_count": omitted_existing + omitted_tombstones,
        "candidate_existing_count": len(existing_candidates),
        "candidate_tombstone_count": len(tombstone_candidates),
        "omitted_existing_count": omitted_existing,
        "omitted_tombstone_count": omitted_tombstones,
        "tombstone_coverage_complete": omitted_tombstones == 0,
    }


def collect_smoke_files(watch_path: Path, limit: int = 5) -> list[str]:
    """Compatibility projection for callers that only need selected paths."""
    return collect_smoke_file_selection(watch_path, limit=limit)["selected_files"]


def resolve_watch_input_path(path_to_watch) -> Path:
    """Resolve public watch paths against the analyzed repository without allowing escape."""
    raw_path = Path(path_to_watch).expanduser()
    repository_root = ROOT.resolve()
    installation_root = CODE_MAPS_DIR.resolve()
    candidates = (
        [raw_path.resolve()]
        if raw_path.is_absolute()
        else [
            (repository_root / raw_path).resolve(),
            (installation_root / raw_path).resolve(),
        ]
    )
    allowed = []
    for candidate in candidates:
        try:
            candidate.relative_to(repository_root)
        except ValueError:
            continue
        if candidate not in allowed:
            allowed.append(candidate)

    if not allowed:
        raise ValueError(
            f"Watch path must remain inside the analyzed repository root: {repository_root}"
        )
    return next((candidate for candidate in allowed if candidate.exists()), allowed[0])


def run_watchdog_once(path_to_watch, debounce, mode="ADVISE"):
    os.environ["SAGE_SYNC_SHADOW_WRITES"] = "1"
    try:
        watch_path = resolve_watch_input_path(path_to_watch)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return 1
    selection = collect_smoke_file_selection(watch_path)
    files = selection["selected_files"]
    if not files:
        location = "at" if watch_path.is_file() else "under or previously indexed at"
        console.print(
            f"[bold yellow]Warning:[/bold yellow] No supported analysis source file found {location} {watch_path}."
        )
        return 1

    selection_mode = str(selection["mode"])
    input_origin = "explicit_scope" if selection_mode in {"exact_file", "exact_tombstone"} else "smoke_sample"
    display_root = watch_path.parent if selection_mode in {"exact_file", "exact_tombstone"} else watch_path
    handler = CodeMapsHandler(debounce_seconds=debounce, display_root=display_root, mode=mode)
    event_by_path = {
        str(row.get("path")): str(row.get("event_kind") or "unknown")
        for row in selection.get("change_events") or []
        if isinstance(row, dict)
    }
    selected_listing = "\n".join(
        f"- [{event_by_path.get(str(Path(file_path).resolve()), 'unknown')}] {file_path}"
        for file_path in files
    )
    console.print(
        Panel(
            "[bold cyan]Nexora SAGE Watchdog Smoke[/bold cyan]\n\n"
            f"Path: [yellow]{watch_path}[/yellow]\n"
            f"Selection mode: [bold white]{selection_mode}[/bold white]\n"
            f"Selected files: [bold white]{len(files)}[/bold white]\n"
            f"Candidate files: [bold white]{selection['candidate_count']}[/bold white]\n"
            f"Omitted files: [bold white]{selection['omitted_count']}[/bold white]\n"
            f"Selected tombstones: [bold white]{len(selection.get('selected_tombstones') or [])}[/bold white]\n"
            f"Omitted tombstones: [bold white]{selection.get('omitted_tombstone_count', 0)}[/bold white]\n"
            f"{selected_listing}\n"
            f"Governance Mode: [bold {'green' if mode == 'ENFORCE' else 'yellow' if mode == 'ADVISE' else 'blue'}]{mode}[/]\n"
            "Mode: [bold cyan]single incremental pulse[/bold cyan]",
            title="Nexora SAGE",
            subtitle="Watchdog smoke validation",
            box=box.DOUBLE,
        )
    )
    analysis_succeeded = handler.run_analysis(
        files,
        watchdog_profile="smoke",
        input_origin=input_origin,
        acquisition=selection,
    )
    if analysis_succeeded is False:
        console.print(
            "[bold red]Error:[/bold red] Watchdog once did not commit the requested incremental generation."
        )
        return 2
    from tools.core.artifact_store import flush_shadow_writes

    if not flush_shadow_writes(timeout=10.0):
        console.print("[bold red]Error:[/bold red] Watchdog once could not close all shadow writes.")
        return 2
    return 0


def start_watchdog(path_to_watch, debounce, mode="ADVISE"):
    if not WATCHDOG_AVAILABLE:
        console.print(
            "[bold red]Error:[/bold red] watchdog live imports are unavailable. "
            f"import_error={WATCHDOG_IMPORT_ERROR or 'not_available'}. "
            "Use `--once` for smoke validation or repair the installed package/runtime path."
        )
        return 1

    try:
        watch_path = resolve_watch_input_path(path_to_watch)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return 1
    if not watch_path.exists():
        console.print(f"[bold red]Error:[/bold red] Directory {watch_path} not found.")
        return 1
    if not watch_path.is_dir():
        console.print(
            "[bold red]Error:[/bold red] Live watchdog requires a directory. "
            "Use `watch --once --path <file>` for an exact-file pulse."
        )
        return 1

    event_handler = CodeMapsHandler(debounce_seconds=debounce, display_root=watch_path, mode=mode)
    observer = Observer()
    observer.schedule(event_handler, str(watch_path), recursive=True)

    console.clear()
    console.print(
        Panel(
            "[bold green][LAUNCH] Nexora SAGE Watchdog ready[/bold green]\n\n"
            f"Watching: [yellow]{watch_path}[/yellow]\n"
            f"Debounce: [bold white]{debounce}s[/bold white]\n"
            f"Governance Mode: [bold {'green' if mode == 'ENFORCE' else 'yellow' if mode == 'ADVISE' else 'blue'}]{mode}[/]\n"
            "Mode: [bold cyan]Incremental / cache-aware[/bold cyan]\n\n"
            "Press [bold red]Ctrl+C[/bold red] to stop.",
            title="Nexora SAGE",
            subtitle="Repository-agnostic analysis service",
            box=box.DOUBLE,
        )
    )

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    return 0


if __name__ == "__main__":
    from tools.core.config import DEFAULT_WATCH_PATH, CONFIG_FILE, _load_json

    config_data = _load_json(CONFIG_FILE)
    default_mode = config_data.get("governance", {}).get("mode", "ADVISE")

    parser = argparse.ArgumentParser(description="Run the Nexora SAGE incremental watchdog service.")
    parser.add_argument("--path", default=str(DEFAULT_WATCH_PATH), help="Directory to watch")
    parser.add_argument("--debounce", type=float, default=1.5, help="Seconds to wait after edits settle")
    parser.add_argument("--once", action="store_true", help="Run a single smoke incremental pulse instead of starting the long-lived observer")
    parser.add_argument("--mode", choices=["OBSERVE", "ADVISE", "ENFORCE"], default=default_mode, help=f"Governance mode (default from config: {default_mode})")
    args = parser.parse_args()

    if args.once:
        raise SystemExit(run_watchdog_once(args.path, args.debounce, mode=args.mode))
    raise SystemExit(start_watchdog(args.path, args.debounce, mode=args.mode))

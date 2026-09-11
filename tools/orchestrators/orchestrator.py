import argparse
import atexit
from functools import wraps
import json
import os
import socket
import sys
import time
import re
import uuid
from pathlib import Path

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CODE_MAPS_DIR = Path(_ROOT)

from tools.core.vendor_bootstrap import inject_vendor_paths

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
_VENDOR_PATHS = inject_vendor_paths(CODE_MAPS_DIR)

from tools.core.logger import logger
from tools.core.honesty_telemetry import record_honesty_event
from tools.core.config import DYNAMIC_CONFIG, RAW_DIR, ROOT, ensure_output_dir, save_json_atomic
from tools.core.execution_identity import (
    SAGE_ACTOR_PROFILE_ENV,
    SAGE_REALITY_TARGET_PROFILE_ENV,
    resolve_execution_identity,
)
from tools.core.runtime_project_scope import get_runtime_project_filter, set_runtime_project_filter
from tools.core.artifact_freshness_contract import artifact_state_meta
from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.core.pipeline_registry import (
    catalog_args_from_execution_policy,
    explicit_step_freshness_reuse_plan,
    filter_catalog_for_system_scope,
    load_pipeline_execution_policy,
    normalize_step_slug,
    pipeline_lock_policy,
    step_registry_from_catalog,
)
from tools.core.advisory_file_lock import AdvisoryFileLock
from tools.core.artifact_store import (
    activate_shadow_write_run,
    current_shadow_write_run_id,
    flush_shadow_writes_report,
    release_shadow_write_run,
    shadow_write_lifecycle_snapshot,
)
from tools.core.operational_limits import artifact_shadow_flush_timeout_seconds, pipeline_step_heartbeat_seconds
from tools.core.pipeline_run_receipts import PipelineRunRecorder, start_pipeline_run_receipt
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.analysis_scope_authority import (
    INCOMPLETE_EVIDENCE,
    reconcile_consumer_scope_authorities,
    runtime_scope_authority,
    scope_receipt_details,
)
import tools.core.cache_manager as cache_manager


_PROJECT_FILTER = None
_PIPELINE_LOCK_PATH = CODE_MAPS_DIR / str(pipeline_lock_policy().get("endpoint") or ".pipeline_run.lock")
_PIPELINE_LOCK_DIAGNOSTIC_PATH = CODE_MAPS_DIR / str(
    pipeline_lock_policy().get("diagnostic_endpoint") or ".pipeline_run.lock.metadata.json"
)
_PIPELINE_LOCK_HANDLE: AdvisoryFileLock | None = None
_PIPELINE_LOCK_HELD = False
_PIPELINE_LOCK_LAST_HEARTBEAT = 0.0
_PIPELINE_LOCK_PROCESS_INSTANCE_ID = uuid.uuid4().hex
_PIPELINE_LOCK_ATEXIT_REGISTERED = False
_PIPELINE_RUN_RECEIPT: PipelineRunRecorder | None = None
_PIPELINE_TERMINAL_DETAILS: dict = {}
_PIPELINE_SHADOW_RUN_ID = ""


class PipelineBusyError(RuntimeError):
    def __init__(self, message: str, *, active_run_id: str = "", active_pid: int | None = None):
        super().__init__(message)
        self.active_run_id = str(active_run_id or "")
        self.active_pid = active_pid


def _pipeline_command_profile(args) -> str:
    if getattr(args, "step", None):
        return f"run:step:{normalize_step_name(str(args.step))}"
    if getattr(args, "from_step", None):
        return f"run:from:{normalize_step_name(str(args.from_step))}"
    if getattr(args, "profile", None):
        return f"run:{args.profile}"
    if bool(getattr(args, "full", False)):
        return "run:full"
    return "run:default"


def _pipeline_receipt_progress(phase: str, **details) -> None:
    if _PIPELINE_RUN_RECEIPT is None:
        return
    try:
        _PIPELINE_RUN_RECEIPT.progress(phase, **details)
    except Exception as exc:
        record_honesty_event(
            component="orchestrator",
            category="caught_error",
            operation="pipeline_run_receipt_progress",
            subject=_PIPELINE_RUN_RECEIPT.run_id,
            severity="warning",
            reason="Pipeline progress receipt could not be persisted.",
            fallback="continue_with_pipeline_log_and_terminal_receipt_attempt",
            claim_impact="invocation_progress_observability_degraded",
            exception=exc,
        )
        logger.warning("[RUN_RECEIPT] progress persistence failed: %s", exc)


def _pipeline_receipt_terminal(
    *,
    terminal_status: str,
    exit_code: int,
    interruption_reason: str = "",
    governance_verdict: str = "NOT_EVALUATED",
    completed_steps: list[str] | None = None,
    failed_steps: list[str] | None = None,
    skipped_steps: list[str] | None = None,
    evidence_identities: list[str] | None = None,
    scope_details: dict | None = None,
) -> bool:
    if _PIPELINE_RUN_RECEIPT is not None and _PIPELINE_RUN_RECEIPT.terminal_recorded:
        return True
    run_id = _PIPELINE_RUN_RECEIPT.run_id if _PIPELINE_RUN_RECEIPT is not None else _PIPELINE_SHADOW_RUN_ID
    if not run_id:
        return True
    try:
        timeout_seconds = float(artifact_shadow_flush_timeout_seconds())
        observed = shadow_write_lifecycle_snapshot(run_id)
        process_observed = shadow_write_lifecycle_snapshot()
        logger.info(
            "[SHADOW_CLOSEOUT] run_id=%s started=%s pending=%s timeout_seconds=%s",
            run_id,
            observed.get("started_count", 0),
            observed.get("pending_count", 0),
            timeout_seconds,
        )
        _pipeline_receipt_progress(
            "shadow_flush_started",
            shadow_worker_started_count=observed.get("started_count", 0),
            shadow_worker_pending_count=observed.get("pending_count", 0),
            shadow_worker_global_pending_count=process_observed.get("pending_count", 0),
            shadow_worker_ids=observed.get("worker_ids", []),
            shadow_artifacts=observed.get("artifacts", []),
            shadow_worker_identities_truncated=observed.get("identities_truncated", False),
            shadow_flush_timeout_seconds=timeout_seconds,
        )
        flush_timed_out = False
        total_wait_seconds = 0.0
        while True:
            report = flush_shadow_writes_report(timeout=timeout_seconds, run_id=run_id)
            total_wait_seconds += float(report.get("wait_seconds") or 0.0)
            global_pending_count = int(report.get("global_pending_count") or 0)
            if report.get("complete") and global_pending_count:
                process_report = flush_shadow_writes_report(timeout=timeout_seconds)
                total_wait_seconds += float(process_report.get("wait_seconds") or 0.0)
                global_pending_count = int(process_report.get("global_pending_count") or 0)
                report["global_pending_count"] = global_pending_count
            if report.get("complete") and global_pending_count == 0:
                break
            flush_timed_out = True
            logger.warning(
                "[SHADOW_CLOSEOUT] run_id=%s timeout=true run_pending=%s "
                "process_pending=%s waited_seconds=%.3f; continuing bounded wait",
                run_id,
                report.get("pending_count", 0),
                global_pending_count,
                total_wait_seconds,
            )
            _pipeline_receipt_progress(
                "shadow_flush_timeout",
                shadow_worker_started_count=report.get("started_count", 0),
                shadow_worker_completed_count=report.get("completed_count", 0),
                shadow_worker_failed_count=report.get("failed_count", 0),
                shadow_worker_pending_count=report.get("pending_count", 0),
                shadow_worker_global_pending_count=global_pending_count,
                shadow_worker_ids=report.get("worker_ids", []),
                shadow_artifacts=report.get("artifacts", []),
                shadow_worker_identities_truncated=report.get("identities_truncated", False),
                shadow_flush_timed_out=True,
                shadow_flush_timeout_seconds=timeout_seconds,
                shadow_flush_wait_seconds=round(total_wait_seconds, 3),
            )
            _heartbeat_pipeline_lock(force=True)

        closeout_details = {
            "shadow_worker_started_count": report.get("started_count", 0),
            "shadow_worker_completed_count": report.get("completed_count", 0),
            "shadow_worker_failed_count": report.get("failed_count", 0),
            "shadow_worker_pending_count": report.get("pending_count", 0),
            "shadow_worker_global_pending_count": report.get("global_pending_count", 0),
            "shadow_worker_ids": report.get("worker_ids", []),
            "shadow_artifacts": report.get("artifacts", []),
            "shadow_worker_identities_truncated": report.get("identities_truncated", False),
            "shadow_flush_timed_out": flush_timed_out,
            "shadow_flush_timeout_seconds": timeout_seconds,
            "shadow_flush_wait_seconds": round(total_wait_seconds, 3),
        }
        logger.info(
            "[SHADOW_CLOSEOUT] run_id=%s complete=true completed=%s failed=%s "
            "waited_seconds=%.3f timed_out=%s",
            run_id,
            closeout_details["shadow_worker_completed_count"],
            closeout_details["shadow_worker_failed_count"],
            total_wait_seconds,
            flush_timed_out,
        )
        _pipeline_receipt_progress("evidence_closeout", **closeout_details)
        if _PIPELINE_RUN_RECEIPT is None:
            release_shadow_write_run(run_id)
            print(
                f"[RUN_RECEIPT] lifecycle_status=DEGRADED run_id={run_id} "
                "process_exit_ready=true queryable_receipt=false",
                file=sys.stderr,
                flush=True,
            )
            return True
        if not _PIPELINE_RUN_RECEIPT.evidence_closeout_recorded:
            raise RuntimeError("Evidence closeout receipt was not persisted.")
        released = release_shadow_write_run(run_id)
        closeout_details["shadow_worker_global_pending_count"] = 0
        closeout_details["shadow_worker_pending_count"] = released.get("pending_count", 0)
        _pipeline_receipt_progress("process_exit_ready", **closeout_details)
        if not _PIPELINE_RUN_RECEIPT.process_exit_ready_recorded:
            raise RuntimeError("Process-exit readiness receipt was not persisted.")
        _PIPELINE_RUN_RECEIPT.terminal(
            terminal_status=terminal_status,
            exit_code=exit_code,
            interruption_reason=interruption_reason,
            governance_verdict=governance_verdict,
            completed_steps=completed_steps,
            failed_steps=failed_steps,
            skipped_steps=skipped_steps,
            evidence_identities=evidence_identities,
            scope_details=scope_details,
        )
        return True
    except Exception as exc:
        record_honesty_event(
            component="orchestrator",
            category="caught_error",
            operation="pipeline_run_receipt_terminal",
            subject=run_id,
            severity="error",
            reason="Pipeline terminal receipt could not be persisted.",
            fallback="preserve_process_exit_and_pipeline_log",
            claim_impact="invocation_terminal_state_unavailable",
            exception=exc,
        )
        logger.error("[RUN_RECEIPT] terminal persistence failed: %s", exc)
        return False


def _apply_runtime_project_filter(projects: str | None) -> list[str] | None:
    global _PROJECT_FILTER
    normalized = (
        [project.strip().upper() for project in projects.split(",") if project.strip()]
        if isinstance(projects, str) and projects.strip()
        else None
    )
    _PROJECT_FILTER = normalized
    import tools.core.config as cfg

    cfg.PROJECT_FILTER = normalized
    set_runtime_project_filter(normalized)
    return list(normalized) if normalized is not None else None


def _release_pipeline_lock():
    global _PIPELINE_LOCK_HANDLE, _PIPELINE_LOCK_HELD
    if not _PIPELINE_LOCK_HELD or _PIPELINE_LOCK_HANDLE is None:
        return
    try:
        payload = _lock_payload(state=str(pipeline_lock_policy().get("release_state") or "released"))
        payload["released_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        _write_pipeline_lock(payload)
    finally:
        _PIPELINE_LOCK_HANDLE.release()
        _PIPELINE_LOCK_HANDLE = None
        _PIPELINE_LOCK_HELD = False


def _release_pipeline_lock_at_exit():
    receipt_unready = _PIPELINE_RUN_RECEIPT is not None and not _PIPELINE_RUN_RECEIPT.terminal_recorded
    shadow_run_unready = bool(current_shadow_write_run_id())
    if receipt_unready or shadow_run_unready:
        logger.error(
            "[RUN_RECEIPT] Process is exiting without terminal readiness; "
            "leaving diagnostic lock metadata active for post-mortem inspection."
        )
        return
    _release_pipeline_lock()


def _lock_payload(state: str = "active") -> dict:
    now_epoch = time.time()
    now_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_epoch))
    payload = {
        "lock_protocol": str(pipeline_lock_policy().get("protocol") or "os_advisory_file_lock_v1"),
        "state": state,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "process_instance_id": _PIPELINE_LOCK_PROCESS_INSTANCE_ID,
        "started_at": now_text,
        "heartbeat_at": now_text,
        "heartbeat_epoch": now_epoch,
        "command": " ".join(sys.argv),
    }
    run_id = _PIPELINE_RUN_RECEIPT.run_id if _PIPELINE_RUN_RECEIPT is not None else _PIPELINE_SHADOW_RUN_ID
    if run_id:
        payload["run_id"] = run_id
    return payload


def _write_pipeline_lock(payload: dict):
    if _PIPELINE_LOCK_HANDLE is None:
        raise RuntimeError("Cannot update pipeline lock metadata without holding the OS advisory lock.")
    _PIPELINE_LOCK_HANDLE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        save_json_atomic(_PIPELINE_LOCK_DIAGNOSTIC_PATH, payload)
    except Exception as exc:
        logger.warning(
            "[PIPELINE] Lock ownership remains active, but readable diagnostic metadata could not be updated: %s",
            exc,
        )


def _heartbeat_pipeline_lock(force: bool = False):
    global _PIPELINE_LOCK_LAST_HEARTBEAT
    if not _PIPELINE_LOCK_HELD:
        return
    now = time.time()
    heartbeat_interval = float(pipeline_step_heartbeat_seconds(scope="pipeline_lock"))
    if not force and now - _PIPELINE_LOCK_LAST_HEARTBEAT < heartbeat_interval:
        return
    try:
        if _PIPELINE_LOCK_HANDLE is None:
            raise RuntimeError("Pipeline lock heartbeat requested without an advisory lock handle.")
        details = json.loads(_PIPELINE_LOCK_HANDLE.read_held_text())
    except Exception as exc:
        record_honesty_event(
            component="orchestrator",
            category="caught_error",
            operation="pipeline_lock_heartbeat_read",
            subject=str(_PIPELINE_LOCK_PATH),
            severity="warning",
            reason="Could not read pipeline lock heartbeat payload; rebuilding lock payload.",
            fallback="rebuild_pipeline_lock_payload",
            claim_impact="runtime_observability_degraded",
            exception=exc,
        )
        logger.warning("Could not read pipeline lock heartbeat payload; rebuilding it: %s", exc)
        details = _lock_payload()
    details["pid"] = os.getpid()
    details["heartbeat_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    details["heartbeat_epoch"] = now
    details.setdefault("command", " ".join(sys.argv))
    try:
        _write_pipeline_lock(details)
        _PIPELINE_LOCK_LAST_HEARTBEAT = now
    except Exception as exc:
        logger.warning("Could not update pipeline lock heartbeat: %s", exc)


def _lock_heartbeat_age(details: dict) -> float | None:
    try:
        heartbeat_epoch = float(details.get("heartbeat_epoch"))
    except (TypeError, ValueError):
        return None
    return max(0.0, time.time() - heartbeat_epoch)


def _acquire_pipeline_lock():
    global _PIPELINE_LOCK_HANDLE, _PIPELINE_LOCK_HELD, _PIPELINE_LOCK_ATEXIT_REGISTERED
    candidate = AdvisoryFileLock(_PIPELINE_LOCK_PATH)
    if not candidate.acquire():
        details = {}
        try:
            details = json.loads(candidate.read_text())
        except Exception as exc:
            details = load_json_file(_PIPELINE_LOCK_DIAGNOSTIC_PATH, {})
            if not isinstance(details, dict) or not details:
                logger.info(
                    "[PIPELINE] OS advisory lock is held; holder metadata is unavailable (%s). "
                    "Ownership remains authoritative; diagnostics are limited.",
                    exc,
                )
                details = {}
        holder = details.get("pid")
        host = details.get("host")
        started = details.get("started_at")
        heartbeat_age = _lock_heartbeat_age(details)
        holder_info = f" (pid={holder}{', host=' + str(host) if host else ''})" if holder else ""
        started_info = f" since {started}" if started else ""
        heartbeat_info = f"; heartbeat age={heartbeat_age:.0f}s" if heartbeat_age is not None else "; heartbeat unavailable"
        active_run_id = str(details.get("run_id") or "")
        _pipeline_receipt_progress(
            "blocked_by_active_run",
            active_run_id=active_run_id or "not_available",
            active_pid=int(holder) if str(holder or "").isdigit() else 0,
        )
        raise PipelineBusyError(
            f"!! SYSTEM BUSY !! Another Nexora SAGE pipeline run holds the OS advisory lock{holder_info}{started_info}{heartbeat_info}.\n"
            f"Active run_id={active_run_id or 'not_available'}. Query it before retrying. "
            "Wait for it to finish. Do not remove .pipeline_run.lock; its metadata is diagnostic only.",
            active_run_id=active_run_id,
            active_pid=int(holder) if str(holder or "").isdigit() else None,
        )
    _PIPELINE_LOCK_HANDLE = candidate
    _PIPELINE_LOCK_HELD = True
    try:
        _write_pipeline_lock(_lock_payload())
        _PIPELINE_LOCK_LAST_HEARTBEAT = time.time()
    except Exception:
        _release_pipeline_lock()
        raise
    if not _PIPELINE_LOCK_ATEXIT_REGISTERED:
        atexit.register(_release_pipeline_lock_at_exit)
        _PIPELINE_LOCK_ATEXIT_REGISTERED = True


def _release_pipeline_lock_on_exit(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        terminal_ready = _PIPELINE_RUN_RECEIPT is None
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                terminal_status, exit_code = "INTERRUPTED", 130
            elif isinstance(exc, PipelineBusyError):
                terminal_status, exit_code = "BLOCKED", 1
            elif isinstance(exc, SystemExit):
                exit_code = int(exc.code) if isinstance(exc.code, int) else 1
                terminal_status = "PASS" if exit_code == 0 else "FAILED"
            else:
                terminal_status, exit_code = "FAILED", 1
            details = dict(_PIPELINE_TERMINAL_DETAILS)
            terminal_ready = _pipeline_receipt_terminal(
                terminal_status=str(details.get("terminal_status") or terminal_status),
                exit_code=int(details.get("exit_code") if "exit_code" in details else exit_code),
                interruption_reason=str(details.get("interruption_reason") or f"{type(exc).__name__}: {exc}"),
                governance_verdict=str(details.get("governance_verdict") or "NOT_EVALUATED"),
                completed_steps=details.get("completed_steps"),
                failed_steps=details.get("failed_steps"),
                skipped_steps=details.get("skipped_steps"),
                evidence_identities=details.get("evidence_identities"),
                scope_details=details.get("scope_details"),
            )
            raise
        else:
            details = dict(_PIPELINE_TERMINAL_DETAILS)
            terminal_ready = _pipeline_receipt_terminal(
                terminal_status=str(details.get("terminal_status") or ("FAILED" if result is False else "PASS")),
                exit_code=int(details.get("exit_code") if "exit_code" in details else pipeline_process_exit_code(result)),
                interruption_reason=str(details.get("interruption_reason") or ("explicit_preflight_rejection" if result is False else "none")),
                governance_verdict=str(details.get("governance_verdict") or "NOT_EVALUATED"),
                completed_steps=details.get("completed_steps"),
                failed_steps=details.get("failed_steps"),
                skipped_steps=details.get("skipped_steps"),
                evidence_identities=details.get("evidence_identities"),
                scope_details=details.get("scope_details"),
            )
            return result
        finally:
            if terminal_ready:
                _release_pipeline_lock()
            else:
                logger.error(
                    "[RUN_RECEIPT] Pipeline lock retained because process-exit readiness was not recorded."
                )

    return wrapped


def run_step(name, func, *args, **kwargs):
    logger.info(f"==== STARTING STEP: {name} ====")
    start_time = time.time()
    try:
        # Final Summary
        if kwargs.pop("returns_success", False):
            success = func(*args, **kwargs)
        else:
            success = func(*args, **kwargs)
            if success is None: success = True # Default pass

        elapsed = time.time() - start_time
        logger.info(f"STEP COMPLETED: {name} (Time: {elapsed:.2f}s)\n")
        return success, elapsed
    except Exception as exc:
        logger.exception(f"FATAL ERROR in step '{name}': {exc}")
        return False, time.time() - start_time


# [Phase 5.6] Pipeline Global Cache (Shared across Parallel Engines)
PIPELINE_CACHE = {
    "keyword": None,
    "ui": None,
    "landscape": None,
    "atlas": None,
    "fractal": None
}

def pre_warm_cache(force_refresh=False, atlas=None):
    from tools.core.config import RAW_DIR
    import json
    from collections import defaultdict
    
    # [Phase 6] Prevent redundant warming unless forced (e.g. after a fresh Atlas run)
    if PIPELINE_CACHE.get("__warmed__") and not force_refresh:
        return True

    atlas_source = "provided_runtime_payload" if isinstance(atlas, dict) else "sqlite_first_artifact_store"
    logger.info("[WATCHDOG] Warming/Refreshing persistent RAM cache... atlas_source=%s", atlas_source)
    
    try:
        # Pre-load Atlas into Orchestrator cache
        atlas_payload = atlas if isinstance(atlas, dict) else load_atlas_data()
        if atlas_payload:
            PIPELINE_CACHE["atlas"] = atlas_payload
            
        # [Phase 6.2] Inject into Engine Globals for surgical speed
        from tools.engines.keyword_scanner import GLOBAL_KEYWORD_CACHE as KGC
        from tools.engines.ui_mapper import GLOBAL_UI_CACHE as UGC
        from tools.engines.landscape_mapper import GLOBAL_LANDSCAPE_CACHE as LGC
        from tools.engines.generate_atlas import GLOBAL_ATLAS_CACHE as GAC
        
        if PIPELINE_CACHE["atlas"]:
            GAC.clear()
            GAC.update(PIPELINE_CACHE["atlas"])
        
        # Keywords
        kw_p = RAW_DIR / 'keyword_scanner_all.json'
        data = load_json_file(kw_p, {})
        if isinstance(data, dict) and data:
            processed = defaultdict(lambda: defaultdict(list))
            for cat, items in data.items():
                for item in items:
                    if 'project' in item: processed[cat][item['project']].append(item)
            KGC['all'] = processed
            
        # UI
        ui_p = RAW_DIR / 'ui_architecture_map.json'
        data = load_json_file(ui_p, {})
        if isinstance(data, dict) and data:
            f_cache = {}
            for pkey, cats in data.items():
                for cat_name, files in cats.items():
                    for f in files: f_cache[(pkey, f['file'])] = f
            UGC['data'] = data
            UGC['file_cache'] = f_cache
            
        # Landscape
        data = load_json_file(RAW_DIR / 'landscape_map.json', {})
        if isinstance(data, dict) and data:
            LGC['data'] = data
            
        # Fractal
        from tools.core.fractal_io import load_fractal_map_data

        data = load_fractal_map_data()
        if isinstance(data, dict) and data:
            PIPELINE_CACHE["fractal"] = data.get("projects", {})
            
        PIPELINE_CACHE["__warmed__"] = True
        logger.info("[OK] [PRE-WARM] Engine Globals Synchronized.")
        return True
    except Exception as e:
        logger.warning(f"Cache refresh failed: {e}")
        return False


def _pending_cache_consumer_names(steps, successful_steps):
    return [
        str(step.get("name") or "")
        for step in steps
        if str(step.get("name") or "") not in successful_steps
    ]

def build_step_catalog(args, stale_projects=None, changed_files=None, atlas=None):
    stale_str = ",".join(stale_projects) if stale_projects else None
    watchdog_live_scope = str(getattr(args, "watchdog_profile", "") or "").lower() in {"live", "smoke"}

    def run_keyword_stats():
        from tools.engines.keyword_scanner import KeywordScanner
        scanner = KeywordScanner(stale_projects=stale_projects)
        scanner.run("stats", changed_files=changed_files)

    def run_keyword_main():
        from tools.engines.keyword_scanner import KeywordScanner
        scanner = KeywordScanner(stale_projects=stale_projects)
        scanner.run("keywords", changed_files=changed_files)

    def run_keyword_gems():
        from tools.engines.keyword_scanner import KeywordScanner
        scanner = KeywordScanner(stale_projects=stale_projects)
        scanner.run("gems", changed_files=changed_files, write_standalone=False)

    def run_ui_impl():
        from tools.engines.ui_mapper import UIMapper
        mapper = UIMapper(stale_projects=stale_projects)
        result = mapper.scan_projects(changed_files=changed_files)
        mapper.generate_report(result)

    def run_landscape_impl():
        from tools.engines.landscape_mapper import LandscapeMapper
        mapper = LandscapeMapper(stale_projects=stale_projects)
        mapper.generate_full_report(changed_files=changed_files)

    def run_hexagonal_binder_impl():
        from tools.engines.hexagonal_binder import run_hexagonal_binder as run_hex_impl
        run_hex_impl(changed_files=changed_files)

    def run_fractal_impl():
        from tools.engines.fractal_mapper import run_fractal_mapper
        run_fractal_mapper(stale_projects=stale_projects, changed_files=changed_files)

    def run_audit_impl():
        from tools.engines.audit import analyze_project
        return analyze_project(
            changed_files=changed_files if watchdog_live_scope else None,
            atlas=atlas,
        )

    def run_atlas_placeholder():
        return []

    def run_nuclear_impl():
        from tools.engines.nuclear_processor import run_nuclear
        return run_nuclear(stale_projects=stale_projects, changed_files=changed_files, use_cache=not getattr(args, "force", False))

    def run_architecture_oracle_impl():
        from tools.engines.architecture_oracle import run_architecture_oracle
        run_architecture_oracle()

    def run_dead_code_impl():
        from tools.engines.dead_code_detector import DeadCodeDetector
        detector = DeadCodeDetector()
        detector.run()

    def run_circular_impl():
        from tools.engines.circular_dependency_finder import CircularDependencyFinder
        finder = CircularDependencyFinder()
        finder.run()

    def run_project_dna_profile_impl():
        from tools.engines.project_dna_profiler import run
        run()

    def run_health_impl():
        from tools.engines.health_score import calculate_health_score
        calculate_health_score()

    def run_react_support_impl():
        from tools.validate_react_support import main as run_react_support_main
        exit_code = run_react_support_main()
        if exit_code != 0:
            raise RuntimeError("React Support Matrix validation failed")

    def run_react_capability_probe_impl():
        from tools.validate_react_support import run_probe
        run_probe()

    def run_risk_impl():
        from tools.engines.module_risk_matrix import generate_module_risk_matrix
        generate_module_risk_matrix()

    def run_temporal_impl():
        from tools.engines.temporal_diff import save_snapshot_and_diff
        save_snapshot_and_diff()

    def run_ai_ctx_impl():
        from tools.engines.ai_context_generator import generate_ai_context
        generate_ai_context()

    def run_closure_walk_impl():
        from tools.engines.closure_walker import run_closure_walker_readiness
        run_closure_walker_readiness()

    def run_cmo_dashboard_impl():
        from tools.engines.cmo_dashboard import run_cmo_dashboard as run_cmo_impl
        run_cmo_impl()

    def run_evidence_impl():
        from tools.engines.decision_evidence import main as run_evidence
        run_evidence()

    def run_nanometric_diff_impl():
        from tools.engines.nanometric_diff_engine import run_nanometric_diff
        run_nanometric_diff()

    def run_merge_script_generator_impl():
        from tools.engines.merge_script_generator import run_merge_script_generator
        run_merge_script_generator()

    def run_oracle_validation_impl():
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from tools.engines.validation_oracle import run_validation_oracle
        from tools.core.config import RAW_DIR, save_json_atomic
        from tools.core.analysis_scope_authority import (
            INCOMPLETE_EVIDENCE,
            bind_consumer_projects,
            load_scope_authority_for_consumer,
        )
        from tools.core.json_io import load_json_file
        from tools.core.projects_registry import resolve_runtime_projects
        from tools.core.workload_profile import build_workload_profile, oracle_worker_count
        projects = [project for project in resolve_runtime_projects(ROOT) if project != "MAIN"]
        atlas = load_atlas_data()
        _scope_artifact, scope_authority = load_scope_authority_for_consumer(RAW_DIR)
        observed_projects: list[str] = []
        project_statuses: dict[str, str] = {}
        if not projects:
            scope_authority = bind_consumer_projects(
                scope_authority,
                layer="validation_oracle",
                observed_projects=[],
                expected_projects=[],
            )
            save_json_atomic(RAW_DIR / "validation_oracle_scope.json", {
                "meta": {"kind": "validation_oracle_scope", "version": "v1"},
                "scope_authority": scope_authority,
                "scope_projection": {
                    "purpose": "non_main_sanctuary_validation",
                    "selection_rule": "effective_runtime_projects_excluding_MAIN",
                    "expected_projects": [],
                    "observed_projects": [],
                    "repository_ontology_redefinition": False,
                },
                "project_statuses": {},
            })
            return

        workload_profile = load_json_file(RAW_DIR / "workload_profile.json", {})
        if not isinstance(workload_profile, dict) or not workload_profile:
            workload_profile = build_workload_profile(atlas if isinstance(atlas, dict) else {})
        workers = oracle_worker_count(workload_profile, len(projects))
        logger.info("[ORACLE] Parallel validation enabled: projects=%s workers=%s", len(projects), workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_validation_oracle, project): project for project in projects}
            for future in as_completed(futures):
                project = futures[future]
                try:
                    result = future.result()
                    if isinstance(result, dict):
                        status = str(result.get("status") or "UNKNOWN").upper()
                        observed_projects.append(project)
                        project_statuses[project] = status
                        if status == "FAIL":
                            logger.warning("[ORACLE] Validation failed for %s: %s", project, result.get("error"))
                        elif status == "NOT_APPLICABLE":
                            logger.info("[ORACLE] Validation not applicable for %s", project)
                except Exception as exc:
                    logger.warning("[ORACLE] Validation worker crashed for %s: %s", project, exc)
                    project_statuses[project] = "WORKER_CRASH"
        scope_authority = bind_consumer_projects(
            scope_authority,
            layer="validation_oracle",
            observed_projects=observed_projects,
            expected_projects=projects,
        )
        save_json_atomic(RAW_DIR / "validation_oracle_scope.json", {
            "meta": {"kind": "validation_oracle_scope", "version": "v1"},
            "scope_authority": scope_authority,
            "scope_projection": {
                "purpose": "non_main_sanctuary_validation",
                "selection_rule": "effective_runtime_projects_excluding_MAIN",
                "expected_projects": sorted(projects),
                "observed_projects": sorted(observed_projects),
                "repository_ontology_redefinition": False,
            },
            "project_statuses": dict(sorted(project_statuses.items())),
        })
        if scope_authority.get("layer_consistency") == INCOMPLETE_EVIDENCE:
            raise RuntimeError(
                "Validation Oracle project scope diverged from the shared repository scope authority."
            )

    def run_blast_radius_impl():
        from tools.engines.blast_radius_engine import run_blast_radius
        run_blast_radius()

    def run_self_healing_generator_impl():
        from tools.engines.self_healing_generator import run_self_healing_generator
        run_self_healing_generator()

    def run_quality_gates_impl():
        from tools.engines.quality_gate import run_quality_gates
        run_quality_gates()

    def run_live_surface_impl():
        from tools.engines.live_surface_analyzer import run_live_surface_analyzer
        run_live_surface_analyzer()

    def run_quality_review_impl():
        from tools.engines.quality_review_engine import run_quality_review
        run_quality_review()

    def run_proof_obligations_impl():
        from tools.engines.proof_obligations_engine import run_proof_obligations
        run_proof_obligations()

    def run_artifact_contract_validator_impl():
        from tools.engines.artifact_contract_validator import run_artifact_contract_validator
        run_artifact_contract_validator()

    def run_master_report_impl():
        from tools.engines.master_report_generator import generate_master_report
        generate_master_report()

    def run_variation_engine_impl():
        from tools.engines.variation_manager import analyze_variation
        analyze_variation()

    def run_test_impact_matcher_impl():
        from tools.engines.test_impact_matcher import run_test_impact_matcher
        target = getattr(args, "target", None)
        run_test_impact_matcher(target)

    def run_confidence_engine_impl():
        from tools.engines.confidence_engine import run_confidence_engine
        target = getattr(args, "target", None)
        run_confidence_engine(target)

    def run_local_telemetry_impl():
        from tools.engines.local_telemetry_engine import run_local_telemetry_report
        run_local_telemetry_report()

    def run_quant_impl():
        from tools.engines.quant_engine import run_quant_engine
        run_quant_engine(
            focus_files=changed_files if watchdog_live_scope else None,
            atlas=atlas,
        )

    def run_host_merge_intelligence_impl():
        from tools.engines.host_merge_intelligence import run_host_merge_intelligence
        run_host_merge_intelligence()

    def run_ui_runtime_contracts_impl():
        from tools.engines.ui_runtime_contract_analyzer import run_ui_runtime_contract_analyzer
        run_ui_runtime_contract_analyzer()

    def run_framework_routes_impl():
        from tools.engines.framework_route_analyzer import run_framework_route_analyzer
        run_framework_route_analyzer()

    def run_ui_smoke_specs_impl():
        from tools.engines.ui_smoke_spec_generator import run_ui_smoke_spec_generator
        run_ui_smoke_spec_generator()

    def run_ui_smoke_execution_impl():
        from tools.engines.ui_smoke_execution_report import run_ui_smoke_execution_report
        run_ui_smoke_execution_report()

    def run_next_boundary_impl():
        from tools.engines.next_boundary_analyzer import run_next_boundary_analyzer
        run_next_boundary_analyzer()

    def run_state_data_graph_impl():
        from tools.engines.state_data_graph_analyzer import run_state_data_graph_analyzer
        run_state_data_graph_analyzer()

    def run_a11y_i18n_contracts_impl():
        from tools.engines.a11y_i18n_contract_analyzer import run_a11y_i18n_contract_analyzer
        run_a11y_i18n_contract_analyzer()

    def run_react_ecosystem_impl():
        from tools.engines.react_ecosystem_analyzer import run_react_ecosystem_analyzer
        run_react_ecosystem_analyzer()

    def run_react_runtime_intelligence_impl():
        from tools.engines.react_runtime_intelligence import run_react_runtime_intelligence
        run_react_runtime_intelligence()

    def run_react_compiler_readiness_impl():
        from tools.engines.react_compiler_readiness import run_react_compiler_readiness
        run_react_compiler_readiness()

    def run_react_frontier_intelligence_impl():
        from tools.engines.react_frontier_intelligence import run_react_frontier_intelligence
        run_react_frontier_intelligence()

    def run_merge_dependency_packager_impl():
        from tools.engines.merge_dependency_packager import run_merge_dependency_packager
        run_merge_dependency_packager()

    def run_merge_simulation_impl():
        from tools.engines.merge_simulation_engine import run_merge_simulation_engine
        run_merge_simulation_engine()

    def run_merge_decision_cockpit_impl():
        from tools.engines.merge_decision_cockpit import run_merge_decision_cockpit
        run_merge_decision_cockpit()

    def run_ai_task_packs_impl():
        from tools.engines.ai_task_pack_generator import run_ai_task_pack_generator
        run_ai_task_pack_generator()

    def run_merge_intelligence_regression_impl():
        from tools.validate_merge_intelligence_regression import run_validation
        run_validation()

    def run_adapter_registry_impl():
        from tools.engines.adapter_registry_report import run_adapter_registry_report
        run_adapter_registry_report()

    def run_release_readiness_impl():
        from tools.engines.release_readiness_report import run_release_readiness_report
        run_release_readiness_report()

    def run_operator_packet_impl():
        from tools.generate_nexora_operator_packet import run as run_operator_packet
        run_operator_packet()

    def run_distribution_hardening_impl():
        from tools.validate_distribution_hardening import run_validation
        run_validation()

    def run_entrypoint_failure_validation_impl():
        from tools.validate_entrypoints_and_failures import run_validation
        from tools.core.config import RAW_DIR, save_json_atomic
        save_json_atomic(RAW_DIR / "entrypoint_failure_validation.json", run_validation())

    def run_scoped_host_analyzer_impl(scope_path=None):
        from tools.engines.scoped_host_analyzer import run_scoped_host_analyzer
        run_scoped_host_analyzer(scope_path=scope_path)

    def run_state_flow_scanner_impl():
        from tools.engines.state_flow_scanner import run_state_flow_scanner
        run_state_flow_scanner(changed_files=changed_files, atlas=atlas)

    def run_clone_detector_impl():
        from tools.engines.clone_detector import run_clone_detector
        run_clone_detector()

    def run_surgical_readiness_impl():
        from tools.engines.safety_gate_calculator import calculate_surgical_readiness
        calculate_surgical_readiness()

    CATALOG_STEPS = [
        {"name": "Atlas", "func": run_atlas_placeholder, "kwargs": {}, "heavy": True, "category": "core", "depends_on": []},
        {"name": "Project DNA Profile", "func": run_project_dna_profile_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Atlas"]},
        {"name": "Nuclear Sequencing", "func": run_nuclear_impl, "kwargs": {}, "heavy": True, "category": "core", "depends_on": ["Atlas"]},
        {"name": "Architecture Oracle", "func": run_architecture_oracle_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Atlas"]},
        {"name": "Fractal Mapping", "func": run_fractal_impl, "kwargs": {}, "heavy": True, "category": "core", "depends_on": ["Nuclear Sequencing", "Circular Dependency Finder", "AST Structural Diff", "Hexagonal Port-Adapter Binder"]},
        {"name": "Decision Evidence", "func": run_evidence_impl, "kwargs": {}, "heavy": True, "category": "core", "depends_on": ["Fractal Mapping"]},
        {"name": "Keyword Stats", "func": run_keyword_stats, "kwargs": {}, "heavy": True, "category": "core", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Keyword Scanner", "func": run_keyword_main, "kwargs": {}, "heavy": True, "category": "core", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Gem Scorer", "func": run_keyword_gems, "kwargs": {}, "heavy": True, "category": "core", "depends_on": ["Nuclear Sequencing", "Keyword Stats", "Keyword Scanner"]},
        {"name": "UI Mapper", "func": run_ui_impl, "kwargs": {}, "heavy": True, "category": "core", "depends_on": []},
        {"name": "Adapter Registry", "func": run_adapter_registry_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": []},
        {"name": "Framework Route Analyzer", "func": run_framework_routes_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["UI Mapper"]},
        {"name": "Landscape Mapper", "func": run_landscape_impl, "kwargs": {}, "heavy": True, "category": "core", "depends_on": []},
        {"name": "Dead Code Detector", "func": run_dead_code_impl, "kwargs": {}, "heavy": True, "category": "core", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Circular Dependency Finder", "func": run_circular_impl, "kwargs": {}, "heavy": True, "full_only": True, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Audit", "func": run_audit_impl, "kwargs": {}, "heavy": False, "skip_when": args.skip_audit, "category": "core", "depends_on": ["Nuclear Sequencing", "Architecture Oracle"]},
        {"name": "AST Structural Diff", "func": run_nanometric_diff_impl, "kwargs": {}, "heavy": True, "full_only": True, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Variation Engine", "func": run_variation_engine_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Test-Impact Matcher", "func": run_test_impact_matcher_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Confidence Engine", "func": run_confidence_engine_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Nuclear Sequencing", "Circular Dependency Finder"]},
        {"name": "Local Dev Telemetry", "func": run_local_telemetry_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Hexagonal Port-Adapter Binder", "func": run_hexagonal_binder_impl, "kwargs": {}, "heavy": True, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
        {"name": "State Flow Scanner", "func": run_state_flow_scanner_impl, "kwargs": {}, "heavy": True, "full_only": True, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
        {"name": "React Support Matrix", "func": run_react_support_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Project DNA Profile", "React Capability Probe", "Nuclear Sequencing", "State Flow Scanner"]},
        {"name": "Semantic Clone Detector", "func": run_clone_detector_impl, "kwargs": {}, "heavy": True, "full_only": True, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
        {"name": "Blast Radius Engine", "func": run_blast_radius_impl, "kwargs": {}, "heavy": False, "full_only": True, "category": "derived", "depends_on": ["Atlas", "Circular Dependency Finder"]},
        {"name": "Auto-Merge Script Generator", "func": run_merge_script_generator_impl, "kwargs": {}, "heavy": False, "full_only": True, "category": "derived", "depends_on": ["Fractal Mapping", "Audit", "Blast Radius Engine"]},
        {"name": "Oracle Validation Gate", "func": run_oracle_validation_impl, "kwargs": {}, "heavy": False, "full_only": True, "release_deep_only": True, "category": "derived", "depends_on": ["Auto-Merge Script Generator"]},
        {"name": "Self-Healing Generator", "func": run_self_healing_generator_impl, "kwargs": {}, "heavy": False, "full_only": True, "category": "derived", "depends_on": ["Audit"]},
        {"name": "Health Score", "func": run_health_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Nuclear Sequencing", "Audit", "Dead Code Detector", "Circular Dependency Finder", "Keyword Scanner", "Gem Scorer"]},
        {"name": "Module Risk Matrix", "func": run_risk_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Audit", "Nuclear Sequencing", "Dead Code Detector", "Circular Dependency Finder"]},
        {"name": "Temporal Diff", "func": run_temporal_impl, "kwargs": {}, "heavy": False, "full_only": True, "category": "derived", "depends_on": ["Health Score", "Module Risk Matrix"]},
        {"name": "Host Merge Intelligence", "func": run_host_merge_intelligence_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Nuclear Sequencing", "Fractal Mapping"]},
        {"name": "UI Runtime Contract Analyzer", "func": run_ui_runtime_contracts_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Framework Route Analyzer", "Host Merge Intelligence"]},
        {"name": "UI Smoke Spec Generator", "func": run_ui_smoke_specs_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["UI Runtime Contract Analyzer"]},
        {"name": "UI Smoke Execution Readiness", "func": run_ui_smoke_execution_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["UI Smoke Spec Generator"]},
        {"name": "Next Boundary Analyzer", "func": run_next_boundary_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Atlas"]},
        {"name": "State/Data Graph Analyzer", "func": run_state_data_graph_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["State Flow Scanner"]},
        {"name": "A11y/i18n Contract Analyzer", "func": run_a11y_i18n_contracts_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Atlas"]},
        {"name": "React Ecosystem Analyzer", "func": run_react_ecosystem_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Atlas"]},
        {"name": "React Runtime Intelligence", "func": run_react_runtime_intelligence_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Atlas", "React Ecosystem Analyzer", "UI Smoke Execution Readiness"]},
        {"name": "React Compiler Readiness", "func": run_react_compiler_readiness_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["React Runtime Intelligence", "React Ecosystem Analyzer"]},
        {"name": "React Frontier Intelligence", "func": run_react_frontier_intelligence_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Atlas"]},
        {"name": "Merge Dependency Packager", "func": run_merge_dependency_packager_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Atlas", "UI Runtime Contract Analyzer"]},
        {"name": "Merge Simulation Engine", "func": run_merge_simulation_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Merge Dependency Packager", "UI Smoke Execution Readiness", "React Frontier Intelligence"]},
        {"name": "Merge Decision Cockpit", "func": run_merge_decision_cockpit_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Merge Simulation Engine", "UI Runtime Contract Analyzer", "Merge Dependency Packager", "UI Smoke Spec Generator"]},
        {"name": "AI Task Pack Generator", "func": run_ai_task_packs_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Merge Decision Cockpit"]},
        {"name": "Merge Intelligence Regression", "func": run_merge_intelligence_regression_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Framework Route Analyzer", "UI Runtime Contract Analyzer", "UI Smoke Spec Generator", "Merge Dependency Packager", "Merge Simulation Engine", "Merge Decision Cockpit", "AI Task Pack Generator"]},
        {"name": "Distribution Hardening", "func": run_distribution_hardening_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": []},
        {"name": "Entrypoint Failure Drills", "func": run_entrypoint_failure_validation_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Atlas", "Health Score", "Quality Gates"]},
        {"name": "Scoped Host Analyzer", "func": run_scoped_host_analyzer_impl, "kwargs": {"scope_path": getattr(args, "scope", None)}, "heavy": False, "include_when": hasattr(args, "scope") and bool(args.scope), "category": "derived", "depends_on": ["Atlas", "Fractal Mapping", "Audit", "Nuclear Sequencing"]},
        {"name": "React Capability Probe", "func": run_react_capability_probe_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": []},
        {"name": "Proof Obligations", "func": run_proof_obligations_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Atlas", "Project DNA Profile", "Nuclear Sequencing", "Health Score", "React Support Matrix", "State Flow Scanner", "Dead Code Detector", "Circular Dependency Finder", "Audit", "Oracle Validation Gate"]},
        {"name": "Quality Review Oracle", "func": run_quality_review_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Atlas", "Nuclear Sequencing", "Proof Obligations", "Health Score", "React Support Matrix", "State Flow Scanner", "Dead Code Detector", "Circular Dependency Finder", "Oracle Validation Gate"]},
        {"name": "Quality Gates", "func": run_quality_gates_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Atlas", "Project DNA Profile", "Nuclear Sequencing", "Audit", "Fractal Mapping", "State Flow Scanner", "Blast Radius Engine", "React Support Matrix", "Dead Code Detector", "Health Score", "UI Runtime Contract Analyzer", "Merge Dependency Packager", "Merge Simulation Engine", "Merge Decision Cockpit", "Proof Obligations", "Quality Review Oracle"]},
        {"name": "Live Surface Analyzer", "func": run_live_surface_impl, "kwargs": {}, "heavy": False, "full_only": True, "category": "derived", "depends_on": ["Semantic Clone Detector", "Dead Code Detector", "Oracle Validation Gate"]},
        {"name": "Release Readiness", "func": run_release_readiness_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Quality Gates", "Adapter Registry", "Distribution Hardening", "Entrypoint Failure Drills", "Merge Intelligence Regression", "Merge Decision Cockpit", "AI Task Pack Generator"]},
        {"name": "Artifact Contract Validation", "func": run_artifact_contract_validator_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Fractal Mapping", "Quality Gates", "Health Score", "Release Readiness", "Master Report Generation", "Nexora Operator Packet"]},
        {"name": "Master Report Generation", "func": run_master_report_impl, "kwargs": {}, "heavy": False, "category": "core", "depends_on": ["Quality Gates", "Release Readiness", "Health Score", "Module Risk Matrix", "Decision Evidence", "Live Surface Analyzer", "Adapter Registry", "Distribution Hardening", "Entrypoint Failure Drills", "UI Smoke Spec Generator", "UI Smoke Execution Readiness", "Next Boundary Analyzer", "State/Data Graph Analyzer", "A11y/i18n Contract Analyzer", "React Ecosystem Analyzer", "React Runtime Intelligence", "React Compiler Readiness", "React Frontier Intelligence", "Merge Dependency Packager", "Merge Simulation Engine", "Merge Decision Cockpit", "AI Task Pack Generator", "Merge Intelligence Regression"]},
        {"name": "ContextOS Quant Engine", "func": run_quant_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Circular Dependency Finder", "Audit"]},
        {"name": "Nexora Operator Packet", "func": run_operator_packet_impl, "kwargs": {}, "heavy": False, "category": "derived", "depends_on": ["Release Readiness", "Quality Gates", "Merge Decision Cockpit", "AI Task Pack Generator", "ContextOS Quant Engine"]},
        {"name": "AI Context Generator", "func": run_ai_ctx_impl, "kwargs": {}, "heavy": False, "include_when": args.full or args.ai_context, "category": "derived", "depends_on": ["Health Score", "Module Risk Matrix", "Audit", "Dead Code Detector", "Circular Dependency Finder", "Landscape Mapper", "Temporal Diff", "Quality Gates", "Host Merge Intelligence", "Framework Route Analyzer", "UI Runtime Contract Analyzer", "UI Smoke Spec Generator", "UI Smoke Execution Readiness", "Next Boundary Analyzer", "State/Data Graph Analyzer", "A11y/i18n Contract Analyzer", "React Ecosystem Analyzer", "React Runtime Intelligence", "React Compiler Readiness", "React Frontier Intelligence", "Merge Dependency Packager", "Merge Simulation Engine", "Merge Decision Cockpit", "AI Task Pack Generator", "Hexagonal Port-Adapter Binder", "Semantic Clone Detector", "Blast Radius Engine", "State Flow Scanner", "Fractal Mapping", "Live Surface Analyzer", "ContextOS Quant Engine"], "scope_dependencies": {"SAGE_ON_SAGE": ["Nexora Operator Packet"]}},
        {"name": "Surgical Safety Gate", "func": run_surgical_readiness_impl, "kwargs": {}, "heavy": False, "full_only": True, "category": "derived", "depends_on": ["Module Risk Matrix", "Host Merge Intelligence"]},
        {"name": "CMO Dashboard", "func": run_cmo_dashboard_impl, "kwargs": {}, "heavy": False, "full_only": True, "category": "derived", "depends_on": ["Decision Evidence", "Host Merge Intelligence", "Health Score", "Blast Radius Engine"]},
        {"name": "Closure Walker", "func": run_closure_walk_impl, "kwargs": {}, "heavy": False, "full_only": True, "category": "derived", "depends_on": ["Nuclear Sequencing"]},
    ]

    return CATALOG_STEPS


def should_include_step(step, args):
    if step.get("skip_when"):
        return False
    profile_keep_slugs = _profile_keep_slugs(execution_profile(args))
    profile_keeps_step = normalize_step_slug(step.get("name", "")) in profile_keep_slugs
    profile_requests_full = (
        bool(str(getattr(args, "profile", "") or "").strip() or getattr(args, "force", False))
        and _profile_policy(execution_profile(args)).get("include_full_only") is True
    )
    if step.get("full_only") and not args.full and not profile_keeps_step and not profile_requests_full:
        return False
    if (
        step.get("release_deep_only")
        and not getattr(args, "step", None)
        and not getattr(args, "from_step", None)
        and execution_profile(args) not in {"release-deep", "deep"}
    ):
        return False
    if "include_when" in step:
        return bool(step["include_when"])
    return True

def normalize_step_name(name):
    return normalize_step_slug(name)

def find_step_matches(steps, query):
    normalized_query = normalize_step_name(query)
    exact = [step for step in steps if normalize_step_name(step["name"]) == normalized_query]
    if exact:
        return exact
    return [step for step in steps if normalized_query in normalize_step_name(step["name"])]

def format_step_list(steps):
    return ", ".join(step["name"] for step in steps)

def step_dependency_closure(catalog, step_name):
    by_name = {step["name"]: step for step in catalog}
    seen = set()

    def visit(name):
        if name in seen or name not in by_name:
            return
        seen.add(name)
        for dep in by_name[name].get("depends_on", []):
            visit(dep)

    visit(step_name)
    return seen

def steps_exact(selected, keep_names):
    """Keep a surgical step set without pulling heavyweight dependency closures."""
    return [step for step in selected if step["name"] in keep_names]

def classify_step_mode(step):
    return "standalone-ready" if not step.get("depends_on") else "dependency-bound"


def _watchdog_semantic_trigger(rule_id, changed_files, atlas=None) -> dict:
    try:
        from tools.core.watchdog_semantic_triggers import evaluate_watchdog_semantic_trigger

        result = evaluate_watchdog_semantic_trigger(rule_id, changed_files, atlas=atlas)
    except Exception as exc:
        logger.warning("[FAST] [SURGERY] Semantic trigger probe failed rule=%s; keeping selected steps: %s", rule_id, exc)
        return {"rule_id": rule_id, "relevant": True, "reason": "trigger_probe_failed_keep_for_safety"}
    logger.info(
        "[FAST] [WATCHDOG] Semantic trigger rule=%s step_set=%s relevant=%s reason=%s unknown_files=%s",
        rule_id,
        result.get("step_set"),
        result.get("relevant"),
        result.get("reason"),
        len(result.get("unknown_files", [])),
    )
    return result


def execution_profile(args) -> str:
    profile = str(getattr(args, "profile", "") or "").strip().lower()
    if profile:
        return profile
    if getattr(args, "force", False):
        return "release-deep"
    return "full"


def execution_mode_for_run(
    args,
    *,
    atlas_cached: bool | None = None,
    changed_files_override=None,
) -> str:
    """Classify the current run into the machine-readable execution mode matrix."""
    if getattr(args, "step", None) or getattr(args, "from_step", None):
        return "explicit_step"
    if changed_files_override is not None:
        return "watchdog_save_pulse"
    profile = execution_profile(args)
    if profile in {"release-deep", "deep"}:
        return "release_deep"
    if profile == "daily":
        return "daily"
    if atlas_cached is False:
        return "first_repo_onboarding"
    return "normal_full"


def pipeline_completion_governance_verdict(
    successful_steps: set[str],
    quality_gate: dict | None,
) -> str:
    """Keep engine execution health separate from an evaluated governance verdict."""
    if "Quality Gates" not in successful_steps:
        return "NOT_EVALUATED"
    quality_gate = quality_gate if isinstance(quality_gate, dict) else {}
    release_status = str(quality_gate.get("release_gate_status") or "").strip().upper()
    passed = quality_gate.get("passed")
    if release_status:
        return "PASS" if release_status == "PASS" and passed is not False else "FAIL"
    if passed is True:
        return "PASS"
    if passed is False:
        return "FAIL"
    return "INCOMPLETE_EVIDENCE"


def _profile_policy(profile: str) -> dict:
    policy = load_pipeline_execution_policy()
    profiles = policy.get("execution_profiles", {}) if isinstance(policy.get("execution_profiles"), dict) else {}
    value = profiles.get(str(profile or "").strip().lower(), {})
    return value if isinstance(value, dict) else {}


def _execution_mode_policy(mode: str) -> dict:
    policy = load_pipeline_execution_policy()
    modes = policy.get("execution_modes", {}) if isinstance(policy.get("execution_modes"), dict) else {}
    value = modes.get(str(mode or "").strip(), {})
    return value if isinstance(value, dict) else {}


def _step_profile_guidance() -> dict:
    policy = load_pipeline_execution_policy()
    guidance = policy.get("step_profile_guidance", {}) if isinstance(policy.get("step_profile_guidance"), dict) else {}
    return {normalize_step_slug(slug): value for slug, value in guidance.items() if isinstance(value, dict)}


def _step_watchdog_behavior(step_name: str) -> str:
    guidance = _step_profile_guidance().get(normalize_step_slug(step_name), {})
    behavior = guidance.get("watchdog_behavior", guidance.get("daily_behavior", "kept"))
    return str(behavior or "kept").strip().lower()


def _filter_watchdog_live_steps(step_names: set[str]) -> tuple[set[str], set[str]]:
    kept = set()
    removed = set()
    for name in step_names:
        behavior = _step_watchdog_behavior(name)
        if behavior in {"skipped", "release_deep_only"}:
            removed.add(name)
        else:
            kept.add(name)
    return kept, removed


def _profile_keep_slugs(profile: str) -> set[str]:
    config = _profile_policy(profile)
    keep = config.get("keep_slugs", []) if isinstance(config.get("keep_slugs"), list) else []
    return {normalize_step_slug(item) for item in keep if str(item).strip()}


def _smart_trigger_step_slugs(name: str, field: str = "keep_slugs") -> set[str]:
    policy = load_pipeline_execution_policy()
    sets = policy.get("smart_trigger_step_sets", {}) if isinstance(policy.get("smart_trigger_step_sets"), dict) else {}
    config = sets.get(str(name or "").strip(), {})
    values = config.get(field, []) if isinstance(config, dict) and isinstance(config.get(field), list) else []
    return {normalize_step_slug(item) for item in values if str(item).strip()}


def _steps_with_policy_slugs(selected, set_name: str, field: str = "keep_slugs"):
    slugs = _smart_trigger_step_slugs(set_name, field)
    return [step for step in selected if normalize_step_slug(step.get("name", "")) in slugs]


def apply_execution_profile(selected, args):
    profile = execution_profile(args)
    if getattr(args, "step", None) or getattr(args, "from_step", None):
        return selected
    profile_config = _profile_policy(profile)
    mode = str(profile_config.get("mode") or "").strip().lower()
    if profile in {"release-deep", "deep"} or mode == "all":
        logger.info("[PROFILE] release-deep profile active: heavyweight validation and merge intelligence enabled.")
        return selected
    if profile == "daily" or mode == "keep_slugs":
        keep_slugs = _profile_keep_slugs(profile)
        filtered = [step for step in selected if normalize_step_slug(step["name"]) in keep_slugs]
        removed = [step["name"] for step in selected if normalize_step_slug(step["name"]) not in keep_slugs]
        if removed:
            logger.info(
                "[PROFILE] daily profile active: skipped %s deep/release steps: %s",
                len(removed),
                ", ".join(removed[:12]) + ("..." if len(removed) > 12 else ""),
            )
        return filtered
    return selected


def apply_capability_activation(selected, catalog, args, changed_files=None):
    """Filter capability-bound steps using Project DNA outside release/explicit runs."""
    if (
        getattr(args, "step", None)
        or getattr(args, "from_step", None)
        or getattr(args, "force", False)
        or execution_profile(args) in {"release-deep", "deep"}
    ):
        return selected
    try:
        from tools.core.pipeline_registry import step_registry_from_catalog
        from tools.engines.capability_activation_planner import activation_plan_requires_refresh, build_capability_activation_plan

        if activation_plan_requires_refresh(changed_files):
            logger.info("[CAPABILITY] Dependency/config manifest changed; preserving pipeline until Project DNA refreshes.")
            return selected

        logger.info("[CAPABILITY] Refreshing lightweight Project DNA projection before capability filtering.")
        plan = build_capability_activation_plan(refresh_dna=True)
        projects = [project for project in plan.get("projects", []) if isinstance(project, dict)]
        if plan.get("summary", {}).get("status") != "PASS" or not projects:
            logger.warning("[CAPABILITY] Activation plan is empty or invalid; preserving the selected pipeline.")
            return selected
        enabled = {
            str(row.get("id"))
            for project in projects
            for row in project.get("enabled_capabilities", [])
            if isinstance(row, dict) and row.get("status") == "enabled"
        }
        architecture_policy_steps = {
            str(step_name)
            for step_name in (
                plan.get("summary", {})
                .get("architecture_policy_step_policy", {})
                .get("always_preserve_steps", [])
            )
            if str(step_name).strip()
        }
        contracts = {
            str(step.get("name")): {str(item) for item in step.get("capabilities", [])}
            for step in step_registry_from_catalog(catalog).get("steps", [])
            if isinstance(step, dict)
        }
        filtered = []
        removed = []
        for step in selected:
            if str(step.get("name")) in architecture_policy_steps:
                filtered.append(step)
                continue
            capability_ids = contracts.get(str(step.get("name")), set())
            if not capability_ids or capability_ids & enabled:
                filtered.append(step)
            else:
                removed.append(str(step.get("name")))
        if removed:
            logger.info(
                "[CAPABILITY] Project DNA skipped %s irrelevant capability-bound steps: %s",
                len(removed),
                ", ".join(removed[:12]) + ("..." if len(removed) > 12 else ""),
            )
        return filtered
    except Exception as exc:
        logger.warning("[CAPABILITY] Activation filter unavailable; preserving the selected pipeline: %s", exc)
        return selected


def write_pipeline_step_registry(catalog):
    from tools.core.config import RAW_DIR
    save_json_atomic(RAW_DIR / "pipeline_step_registry.json", step_registry_from_catalog(catalog))

def normalize_changed_file_scope(changed_files):
    """Normalize raw filesystem/watchdog paths into PROJECT::relative atlas nodes."""
    if not changed_files:
        return []
    from pathlib import Path
    from tools.core.config import CODE_MAPS_DIR, DYNAMIC_CONFIG, ROOT
    from tools.core.projects_registry import resolve_runtime_projects

    projects = resolve_runtime_projects(ROOT)
    atlas = load_atlas_data()

    def atlas_rel_for(project_key, rel_path):
        rel_norm = str(rel_path or "").replace("\\", "/").strip("/")
        project_data = atlas.get(str(project_key), {}) if isinstance(atlas, dict) else {}
        files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
        if isinstance(files, dict) and rel_norm in files:
            return rel_norm
        if isinstance(files, dict):
            for atlas_rel, file_meta in files.items():
                if not isinstance(file_meta, dict):
                    continue
                candidates = {
                    str(file_meta.get("workspace_rel") or "").replace("\\", "/").strip("/"),
                    str(file_meta.get("repo_relative_path") or "").replace("\\", "/").strip("/"),
                    str(file_meta.get("target_ref") or "").split("::", 1)[-1].replace("\\", "/").strip("/"),
                }
                if rel_norm in candidates:
                    return str(atlas_rel).replace("\\", "/").strip("/")
        return rel_norm

    normalized = []
    seen = set()
    for raw in changed_files:
        text = str(raw or "").replace("\\", "/").strip()
        if not text:
            continue
        if "::" in text:
            project, rel = text.split("::", 1)
            rel_norm = atlas_rel_for(project.strip(), rel)
            node = f"{project.strip()}::{rel_norm}"
        else:
            try:
                path = Path(text)
                if not path.is_absolute():
                    candidates = [Path.cwd() / path, ROOT / path]
                    if not (DYNAMIC_CONFIG.get("_target_root_override") or {}).get("enabled"):
                        candidates.append(CODE_MAPS_DIR / path)
                    path = next((candidate for candidate in candidates if candidate.exists()), ROOT / path)
                resolved = path.resolve()
            except Exception as exc:
                logger.debug("[PATH] Could not resolve changed file path %r; using raw path fallback: %s", text, exc)
                resolved = Path(text)
            node = None
            for pkey, ppath in sorted(projects.items(), key=lambda item: len(str(item[1])), reverse=True):
                try:
                    rel = resolved.relative_to(ppath.resolve()).as_posix()
                except Exception as exc:
                    logger.debug("[PATH] Changed file %r is not under project %s: %s", str(resolved), pkey, exc)
                    continue
                node = f"{pkey}::{atlas_rel_for(pkey, rel)}"
                break
            if node is None:
                try:
                    rel_root = resolved.relative_to(ROOT).as_posix()
                    node = f"MAIN::{rel_root[4:] if rel_root.startswith('src/') else rel_root}"
                except Exception as exc:
                    logger.debug("[PATH] Changed file %r is outside ROOT; preserving raw node: %s", str(resolved), exc)
                    node = text
        if node and node not in seen:
            normalized.append(node)
            seen.add(node)
    return normalized

def has_cached_atlas():
    from tools.core.config import ROOT
    from tools.core.projects_registry import resolve_projects
    from tools.engines.generate_atlas import cached_atlas_covers_projects, load_previous_atlas

    expected_projects = resolve_projects(ROOT).keys()
    atlas_data = load_previous_atlas()
    return cached_atlas_covers_projects(atlas_data, expected_projects)


def missing_execution_mode_artifacts(mode_policy: dict, raw_dir: Path | None = None) -> list[str]:
    """Return contract-required artifacts that have not been materialized."""
    required = [
        str(item).strip()
        for item in mode_policy.get("required_artifacts", [])
        if str(item).strip()
    ]
    artifact_root = Path(raw_dir or RAW_DIR)
    return [artifact for artifact in required if not artifact_state_meta(artifact_root, artifact).get("exists")]


def resolve_requested_step(catalog: list[dict], requested_step: str) -> str:
    """Resolve one explicit step or reject unknown/ambiguous requests."""

    matches = find_step_matches(catalog, requested_step)
    if len(matches) == 1:
        return str(matches[0]["name"])
    if not matches:
        raise ValueError(f"requested step does not match an available pipeline step: {requested_step}")
    raise ValueError(
        f"requested step is ambiguous: {requested_step}; matches={format_step_list(matches)}"
    )

def select_steps_smart(
    catalog,
    args,
    changed_files=None,
    dna_changed_files=None,
    should_run_heavy=False,
    atlas=None,
    required_artifacts_missing=None,
):
    selected = [step for step in catalog if should_include_step(step, args)]
    selected = apply_execution_profile(selected, args)
    selected = apply_capability_activation(selected, catalog, args, changed_files=changed_files)

    if getattr(args, "from_step", None):
        match = find_step_matches(selected, args.from_step)
        if len(match) == 1:
            start_index = next(
                idx for idx, step in enumerate(selected)
                if step["name"] == match[0]["name"]
            )
            selected = selected[start_index:]
        elif len(match) == 0:
            logger.warning("[STEP] --from-step did not match a pipeline step: %s", args.from_step)
        else:
            logger.warning(
                "[STEP] --from-step is ambiguous for '%s': %s",
                args.from_step,
                format_step_list(match),
            )
    
    if args.step:
        target_name = resolve_requested_step(catalog, args.step)
        deps = step_dependency_closure(catalog, target_name)
        return [step for step in catalog if step["name"] in deps]

    if not args.smart_trigger or args.force:
        return selected

    changed_suffixes = {str(item).split("::", 1)[-1].lower() for item in (changed_files or [])}
    react_delta = any(path.endswith((".ts", ".tsx", ".jsx", ".js")) for path in changed_suffixes)

    # [Phase 9] DNA-Agnostic Surgical Gating
    if changed_files is not None and dna_changed_files is not None and not react_delta:
        if len(changed_files) > 0 and len(dna_changed_files) == 0:
            logger.info("[FAST] [SURGERY] DNA Unchanged (Comment/Formatting only). Triggering Fast-Path No-Op.")
            return _steps_with_policy_slugs(selected, "dna_unchanged_fast_path")

    if not changed_files:
        if not args.force:
             missing = [str(item) for item in (required_artifacts_missing or []) if str(item).strip()]
             if missing:
                 logger.info(
                     "[COMPLETENESS] No source delta, but execution-mode artifacts are missing: %s. "
                     "Bypassing Absolute Zero until the declared mode contract is materialized.",
                     ", ".join(missing),
                 )
                 return selected
             logger.info("[SMART] No file changes detected. Absolute Zero speed active.")
             return _steps_with_policy_slugs(selected, "absolute_zero")
        return selected

    if (changed_files is not None and len(changed_files) <= 10) or args.scope:
        # [Phase 9] Absolute Surgical Lockdown: Only run validation essentials
        logger.info(f"[FAST] [SURGERY] Surgical Pulse Active ({len(changed_files) if changed_files else 0} files). Bypassing heavy engines.")
        watchdog_profile = str(getattr(args, "watchdog_profile", "live") or "live").lower()
        surgical_gate = {step["name"] for step in _steps_with_policy_slugs(selected, "watchdog_surgical_gate")}
        if watchdog_profile in {"live", "smoke"}:
            state_flow_trigger = _watchdog_semantic_trigger("state_flow", changed_files, atlas=atlas)
            if not state_flow_trigger.get("relevant"):
                state_flow_removed = {
                    step["name"]
                    for step in _steps_with_policy_slugs(
                        selected,
                        str(state_flow_trigger["step_set"]),
                        "remove_slugs",
                    )
                }
                surgical_gate -= state_flow_removed
                logger.info(
                    "[FAST] [WATCHDOG] State-flow gates skipped; changed files carry no Zustand/TanStack/client-action signal: %s",
                    ", ".join(sorted(state_flow_removed)),
                )
            surgical_gate, removed = _filter_watchdog_live_steps(surgical_gate)
            logger.info(
                "[FAST] [WATCHDOG] Live surgical profile active; skipped %s policy-heavy gates: %s",
                len(removed),
                ", ".join(sorted(removed)) if removed else "none",
            )
        return steps_exact(selected, surgical_gate)

    return selected

def run_surgical_pipeline(changed_files=None, force=False, watchdog_profile="live"):
    normalized_changed_files = normalize_changed_file_scope(changed_files or [])
    selected_projects = sorted(
        {
            item.split("::", 1)[0]
            for item in normalized_changed_files
            if "::" in item
        }
    )

    class MockArgs:
        def __init__(self):
            self.step = None
            self.from_step = None
            self.skip_audit = False
            self.full = True
            self.force = force
            self.projects = ",".join(selected_projects) if selected_projects else None
            self.scope = None
            self.ai_context = False
            self.smart_trigger = True
            self.watchdog_profile = watchdog_profile
            self.profile = "daily" if watchdog_profile in {"live", "smoke"} else "full"
    previous_project_filter = list(_PROJECT_FILTER) if isinstance(_PROJECT_FILTER, list) else _PROJECT_FILTER
    import tools.core.config as cfg

    previous_runtime_filter = list(cfg.PROJECT_FILTER) if isinstance(cfg.PROJECT_FILTER, list) else cfg.PROJECT_FILTER
    previous_scope_filter = get_runtime_project_filter()
    try:
        return main(MockArgs(), changed_files_override=normalized_changed_files)
    finally:
        globals()["_PROJECT_FILTER"] = previous_project_filter
        cfg.PROJECT_FILTER = previous_runtime_filter
        set_runtime_project_filter(previous_scope_filter)


def _pipeline_worker_count() -> int:
    env_value = os.getenv("CODEMAPS_PIPELINE_WORKERS", "").strip()
    if env_value.isdigit():
        parsed = int(env_value)
        if parsed > 0:
            return parsed
    try:
        from tools.core.workload_profile import pipeline_worker_count

        return pipeline_worker_count()
    except Exception as exc:
        logger.warning("[PIPELINE] Workload policy unavailable; using CPU fallback for worker count: %s", exc)
        cpu = os.cpu_count() or 4
        return max(2, min(4, cpu))


def _step_execution_contracts(steps: list[dict]) -> dict[str, dict]:
    """Return scheduler contracts for the selected runnable steps."""
    try:
        payload = step_registry_from_catalog(steps)
    except Exception as exc:
        logger.warning(f"[PIPELINE] Execution policy unavailable; falling back to DAG scheduling: {exc}")
        return {}
    contracts: dict[str, dict] = {}
    for step in payload.get("steps", []) or []:
        contract = dict(step.get("execution_contract") or {})
        contract["writes_artifacts"] = list(step.get("writes_artifacts", []) or [])
        contracts[str(step.get("name") or "")] = contract
    return contracts


def _is_sequential_step(name: str, execution_contracts: dict[str, dict]) -> bool:
    contract = execution_contracts.get(name) or {}
    return contract.get("scheduler_class") == "sequential_required"


def _resolve_pipeline_execution_identity(dynamic_config: dict | None = None) -> dict:
    runtime_config = dynamic_config if isinstance(dynamic_config, dict) else DYNAMIC_CONFIG
    target_override = runtime_config.get("_target_root_override") or {}
    return resolve_execution_identity(
        target_root=str(target_override.get("target_root") or "") if target_override.get("enabled") else "",
        actor_profile=str(os.environ.get(SAGE_ACTOR_PROFILE_ENV) or ""),
        reality_profile=str(os.environ.get(SAGE_REALITY_TARGET_PROFILE_ENV) or ""),
        public_distribution=(CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file(),
        installation_root=Path(__file__).resolve().parents[2],
        default_repository_root=ROOT,
    )


def _scope_authority_artifact(scope_authority: dict, *, stage: str) -> dict:
    return {
        "meta": {
            "kind": "analysis_scope_authority",
            "version": "v1",
            "stage": stage,
            "authority": "shared_repository_analysis_scope",
        },
        "scope_authority": scope_authority,
    }


def _consumer_scope_authority(layer: str, payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    if layer == "audit":
        audit_scope = payload.get("audit_scope")
        return audit_scope.get("scope_authority") if isinstance(audit_scope, dict) else None
    if layer == "quality_gate":
        return (
            payload.get("analysis_scope_authority")
            if isinstance(payload.get("analysis_scope_authority"), dict)
            else None
        )
    return payload.get("scope_authority") if isinstance(payload.get("scope_authority"), dict) else None


def validate_scoped_host_analyzer_scope(scope: str | None) -> Path | None:
    if not scope:
        return None
    scope_path = Path(scope)
    candidate = scope_path if scope_path.is_absolute() else ROOT / scope_path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("--scope must remain inside the analyzed repository root") from exc
    if not resolved.exists():
        raise ValueError(f"--scope path does not exist: {scope}")
    if not resolved.is_dir():
        raise ValueError(
            "--scope is a directory-only filter for Scoped Host Analyzer; "
            "use `python sage.py watch --once --path <file>` for exact-file surgical refresh"
        )
    return resolved


def pipeline_process_exit_code(result) -> int:
    """Map explicit preflight rejection to a process failure without changing normal completion."""
    return 2 if result is False else 0


@_release_pipeline_lock_on_exit
def main(args=None, changed_files_override=None):
    global _PROJECT_FILTER, _PIPELINE_RUN_RECEIPT, _PIPELINE_TERMINAL_DETAILS, _PIPELINE_SHADOW_RUN_ID
    total_start = time.time()
    _PIPELINE_RUN_RECEIPT = None
    _PIPELINE_TERMINAL_DETAILS = {}
    _PIPELINE_SHADOW_RUN_ID = ""

    if args is None:
        parser = argparse.ArgumentParser(description="Nexora SAGE Analysis Pipeline")
        parser.add_argument("--step", type=str, help="Run a specific step by name")
        parser.add_argument("--from-step", type=str, help="Run from a specific step onwards")
        parser.add_argument("--list-steps", action="store_true", help="List runnable pipeline steps and exit")
        parser.add_argument("--skip-audit", action="store_true", help="Skip the audit step")
        parser.add_argument("--full", action="store_true", help="Run everything including explorers")
        parser.add_argument("--force", action="store_true", help="Force run all heavy engines, ignoring smart cache")
        parser.add_argument("--refresh", action="store_true", help="Refresh stale project truth without escalating claim profile or bypassing capability applicability")
        parser.add_argument("--projects", type=str, help="Comma-separated list of projects to scan (e.g. MAIN,ZENITH). Default: all")
        parser.add_argument(
            "--scope",
            type=str,
            help="Directory-only report filter for Scoped Host Analyzer; does not bound Atlas or pipeline execution.",
        )
        parser.add_argument("--target", type=str, help="Normalized target file node for Test-Impact Matcher")
        parser.add_argument("--ai-context", action="store_true", help="Generate AI context index after pipeline completes")
        parser.add_argument("--no-smart", action="store_false", dest="smart_trigger", default=True, help="Disable smart engine gating")
        parser.add_argument(
            "--profile",
            choices=["daily", "full", "release-deep"],
            default=None,
            help="Execution profile: daily skips deep/release-cost engines, full preserves default pipeline, release-deep runs explicit heavyweight validation.",
        )
        args = parser.parse_args()

    if getattr(args, "list_steps", False):
        catalog = build_step_catalog(
            catalog_args_from_execution_policy(load_pipeline_execution_policy()),
            stale_projects=None,
            changed_files=None,
        )
        write_pipeline_step_registry(catalog)
        print("Runnable steps:")
        for step in catalog:
            slug = normalize_step_name(step["name"])
            mode = classify_step_mode(step)
            deps = ", ".join(step.get("depends_on", [])) or "-"
            print(f"- {step['name']}  [slug: {slug}]  [mode: {mode}]  [deps: {deps}]")
        return True

    try:
        scoped_host_path = validate_scoped_host_analyzer_scope(getattr(args, "scope", None))
    except ValueError as exc:
        logger.error("[SCOPE] %s", exc)
        return False
    if scoped_host_path is not None:
        logger.info(
            "[SCOPE] Scoped Host Analyzer report filter=%s; Atlas and pipeline execution remain profile/project scoped.",
            scoped_host_path,
        )

    ensure_output_dir()
    execution_identity = _resolve_pipeline_execution_identity()
    requested_projects = (
        [item.strip().upper() for item in str(getattr(args, "projects", "") or "").split(",") if item.strip()]
    )
    _PIPELINE_SHADOW_RUN_ID = os.environ.get("CODEMAPS_EXTERNAL_RUN_ID", "").strip() or f"sage-run-{uuid.uuid4()}"
    _PIPELINE_RUN_RECEIPT = start_pipeline_run_receipt(
        command_profile=_pipeline_command_profile(args),
        scope=str(execution_identity.get("system_scope") or "SAGE_ON_REPOSITORY"),
        projects=requested_projects,
        context={
            "acquisition_mode": execution_identity.get("acquisition_mode"),
            "system_scope": execution_identity.get("system_scope"),
            "subject_root": execution_identity.get("subject_root"),
            "actor_profile": execution_identity.get("actor_profile"),
            "reality_profile": execution_identity.get("reality_profile"),
        },
        run_id=_PIPELINE_SHADOW_RUN_ID,
    )
    activate_shadow_write_run(_PIPELINE_SHADOW_RUN_ID)
    _acquire_pipeline_lock()
    _heartbeat_pipeline_lock(force=True)
    _pipeline_receipt_progress("lock_acquired")

    _apply_runtime_project_filter(getattr(args, "projects", None))
    if _PROJECT_FILTER:
        logger.info(f"Project filter active: {_PROJECT_FILTER}")

    logger.info("\n" + "=" * 80)
    logger.info("PIPELINE ANALYSIS SESSION STARTED".center(80))
    logger.info("=" * 80 + "\n")
    logger.info("Initialized Nexora SAGE Pipeline")

    refresh_requested = bool(getattr(args, "refresh", False))
    invalidation_reason = "force" if args.force else "refresh" if refresh_requested else ""
    stale_projects = cache_manager.get_stale_projects(
        args.force or refresh_requested,
        invalidation_reason=invalidation_reason,
    )
    _heartbeat_pipeline_lock()
    changed_files = None
    dna_changed_files = None
    explicit_step_name = None
    atlas_data = None

    if args.step:
        preview_catalog = build_step_catalog(args, stale_projects=None, changed_files=None)
        try:
            explicit_step_name = resolve_requested_step(preview_catalog, args.step)
        except ValueError as exc:
            logger.error("[STEP] %s", exc)
            raise SystemExit(2) from None
    
    if _PROJECT_FILTER:
        stale_projects = [p for p in stale_projects if p in _PROJECT_FILTER]
        if not stale_projects and args.force:
            stale_projects = _PROJECT_FILTER

    atlas_cached = has_cached_atlas()
    run_mode = execution_mode_for_run(
        args,
        atlas_cached=atlas_cached,
        changed_files_override=changed_files_override,
    )
    run_mode_policy = _execution_mode_policy(run_mode)
    if run_mode_policy:
        logger.info(
            "[MODE] %s active: profile=%s scope=%s claim=%s",
            run_mode,
            run_mode_policy.get("profile"),
            run_mode_policy.get("scope"),
            run_mode_policy.get("claim_boundary"),
        )
    else:
        logger.warning("[MODE] %s active without policy entry; execution contract should be refreshed.", run_mode)
    skip_atlas_bootstrap = False
    if explicit_step_name:
        preview_catalog = build_step_catalog(args, stale_projects=None, changed_files=None)
        deps = step_dependency_closure(preview_catalog, explicit_step_name)
        skip_atlas_bootstrap = (
            explicit_step_name != "Atlas"
            and ("Atlas" not in deps or (atlas_cached and not stale_projects and not args.force))
        )

    if skip_atlas_bootstrap:
        logger.info(f"[STEP] Running '{explicit_step_name}' without Atlas bootstrap.")
        changed_files = []
        dna_changed_files = []
    elif atlas_cached and not stale_projects and not args.force and changed_files_override is None:
        logger.info("[CACHE] Atlas is current; reusing cached atlas artifact without regeneration.")
        changed_files = []
        dna_changed_files = []
    elif changed_files_override is not None:
        logger.info("[PHASE 6] Using provided surgical change scope...")
        changed_files = normalize_changed_file_scope(changed_files_override)
        changed_projects = sorted({item.split("::", 1)[0] for item in changed_files if "::" in item})
        surgical_stale_projects = sorted(set(stale_projects or []) | set(changed_projects))
        from tools.engines.generate_atlas import generate_atlas
        atlas_data, _, atlas_dna_changed_files = generate_atlas(
            stale_projects=surgical_stale_projects,
            dry_run=False,
            surgical_files=changed_files,
        )
        dna_changed_files = []
        dna_changed_set = set()
        for item in atlas_dna_changed_files or []:
            text = str(item).replace("\\", "/")
            if "::" in text:
                dna_changed_set.add(text)
            else:
                for project in changed_projects:
                    dna_changed_set.add(f"{project}::{text}")
        for item in changed_files:
            if item in dna_changed_set:
                dna_changed_files.append(item)
    else:
        _pipeline_receipt_progress("atlas_refresh")
        logger.info("[PHASE 3.11] Running Atlas Pilot to determine change scope...")
        from tools.engines.generate_atlas import generate_atlas
        _heartbeat_pipeline_lock(force=True)
        atlas_data, changed_files, dna_changed_files = generate_atlas(stale_projects=stale_projects)
        _heartbeat_pipeline_lock(force=True)
        if args.force:
            logger.info("[PIPELINE] Force mode active; downstream engines will run in full mode.")
            changed_files = None

    scope_atlas = atlas_data if isinstance(atlas_data, dict) else load_atlas_data()
    analysis_scope_authority = runtime_scope_authority(
        dynamic_config=DYNAMIC_CONFIG,
        projects=get_runtime_project_filter(),
        atlas=scope_atlas if isinstance(scope_atlas, dict) else {},
        raw_dir=RAW_DIR,
    )
    scope_authority_artifact = _scope_authority_artifact(
        analysis_scope_authority,
        stage="POST_ATLAS",
    )
    save_json_atomic(RAW_DIR / "analysis_scope_authority.json", scope_authority_artifact)
    write_current_atlas_lineage(
        artifact_id="analysis_scope_authority",
        producer="tools.orchestrators.orchestrator",
        artifact_payload=scope_authority_artifact,
        atlas=scope_atlas if isinstance(scope_atlas, dict) else {},
    )
    logger.info(
        "[SCOPE_AUTHORITY] stage=POST_ATLAS status=%s authority_id=%s effective_projects=%s "
        "indexed_sources=%s claim_eligible_sources=%s reasons=%s",
        analysis_scope_authority.get("evidence_status"),
        analysis_scope_authority.get("scope_authority_id"),
        len(analysis_scope_authority.get("effective_runtime_projects") or {}),
        analysis_scope_authority.get("indexed_source_file_count"),
        analysis_scope_authority.get("claim_eligible_source_file_count"),
        ",".join(analysis_scope_authority.get("incomplete_reasons") or []) or "none",
    )

    should_run_heavy = len(stale_projects) > 0 or args.force
    runtime_atlas = atlas_data if run_mode == "watchdog_save_pulse" else None
    catalog = build_step_catalog(args, stale_projects, changed_files, atlas=runtime_atlas)
    write_pipeline_step_registry(catalog)
    execution_policy = load_pipeline_execution_policy()
    scope_policy = execution_policy.get("step_system_scope_policy", {})
    active_system_scope = str(execution_identity.get("system_scope") or "")
    scoped_catalog, scope_report = filter_catalog_for_system_scope(
        catalog,
        active_system_scope,
        execution_policy,
    )
    excluded_scope_steps = [str(row.get("name") or "") for row in scope_report.get("excluded_steps", [])]
    logger.info(
        "[SYSTEM_SCOPE] scope=%s acquisition=%s subject=%s allowed=%s excluded=%s%s",
        active_system_scope,
        execution_identity.get("acquisition_mode"),
        execution_identity.get("subject_root"),
        scope_report.get("allowed_steps"),
        len(excluded_scope_steps),
        f" steps={', '.join(excluded_scope_steps)}" if excluded_scope_steps else "",
    )
    
    for step in catalog:
        if step["name"] == "Atlas":
            step["func"] = lambda: changed_files
            
    try:
        steps = select_steps_smart(
            scoped_catalog,
            args,
            changed_files,
            dna_changed_files,
            should_run_heavy,
            atlas=runtime_atlas,
            required_artifacts_missing=missing_execution_mode_artifacts(run_mode_policy),
        )
    except ValueError as exc:
        logger.error("[STEP] Requested step is unavailable in the active system scope: %s", exc)
        raise SystemExit(2) from None
    if run_mode == "watchdog_save_pulse":
        from tools.core.watchdog_runtime_contract import load_watchdog_runtime_contract

        transport = load_watchdog_runtime_contract()["current_pulse_atlas_transport"]
        transport_status = "provided" if isinstance(runtime_atlas, dict) else "sqlite_first_fallback"
        logger.info(
            "[WATCHDOG_TRANSPORT] atlas=%s authority=%s lifetime=%s access=%s consumers=%s",
            transport_status,
            transport.get("authority"),
            transport.get("lifetime"),
            transport.get("consumer_access"),
            ",".join(str(item) for item in transport.get("consumers", [])),
        )
    if explicit_step_name:
        freshness_plan = explicit_step_freshness_reuse_plan(
            catalog,
            explicit_step_name,
            stale_projects=stale_projects,
            force=bool(args.force),
        )
        reused_steps = set(freshness_plan.get("reused_steps", []))
        if reused_steps:
            steps = [step for step in steps if step["name"] not in reused_steps]
            logger.info(
                "%s explicit_step=%s reused_dependencies=%s retained=%s reason=%s",
                load_pipeline_execution_policy().get("explicit_step_freshness", {}).get("telemetry_prefix", "[FRESHNESS]"),
                explicit_step_name,
                ", ".join(sorted(reused_steps)),
                ", ".join(freshness_plan.get("retained_steps", [])),
                freshness_plan.get("reason"),
            )
    execution_contracts = _step_execution_contracts(steps)
    all_step_names = [s["name"] for s in steps]
    _pipeline_receipt_progress(
        "execution_planned",
        active_steps=all_step_names[:50],
    )
    successful_steps = set()
    if any(s["name"] == "Atlas" for s in steps):
        successful_steps.add("Atlas")
        
    failed_steps = set()
    skipped_steps = set()
    running_steps = {} # name -> future
    running_step_started_at: dict[str, float] = {}
    running_step_last_heartbeat: dict[str, float] = {}
    pipeline_metrics = []
    from tools.core.operational_limits import pipeline_step_heartbeat_selection

    pipeline_heartbeat_selection = pipeline_step_heartbeat_selection(scope="pipeline")
    pipeline_heartbeat_interval = int(pipeline_heartbeat_selection["interval_seconds"])
    logger.info(
        "[PIPELINE] Heartbeat cadence seconds=%s basis=%s samples=%s",
        pipeline_heartbeat_interval,
        pipeline_heartbeat_selection.get("basis"),
        pipeline_heartbeat_selection.get("sample_count"),
    )
    
    cache_policy = str(run_mode_policy.get("runtime_cache_policy") or "broad_pre_warm")
    pending_cache_consumers = _pending_cache_consumer_names(steps, successful_steps)
    if not pending_cache_consumers:
        logger.info("[CACHE] Pre-warm skipped; execution plan has no pending cache consumers.")
    elif cache_policy == "skip_unrelated_broad_pre_warm":
        logger.info("[CACHE] Watchdog hot path skipped unrelated Keyword/UI/Landscape/Fractal pre-warm.")
    else:
        pre_warm_cache(atlas=scope_atlas)

        from tools.engines.keyword_scanner import GLOBAL_KEYWORD_CACHE
        from tools.engines.ui_mapper import GLOBAL_UI_CACHE
        from tools.engines.landscape_mapper import GLOBAL_LANDSCAPE_CACHE
        from tools.engines.fractal_mapper import GLOBAL_FRACTAL_CACHE

        if PIPELINE_CACHE.get("keyword"): GLOBAL_KEYWORD_CACHE["all"] = PIPELINE_CACHE["keyword"]
        if PIPELINE_CACHE.get("ui"): GLOBAL_UI_CACHE.update(PIPELINE_CACHE["ui"] if isinstance(PIPELINE_CACHE["ui"], dict) else {})
        if PIPELINE_CACHE.get("landscape"): GLOBAL_LANDSCAPE_CACHE.update(PIPELINE_CACHE["landscape"])
        if PIPELINE_CACHE.get("fractal"): GLOBAL_FRACTAL_CACHE.update(PIPELINE_CACHE["fractal"])

    import concurrent.futures
    pipeline_workers = _pipeline_worker_count()
    logger.info(f"[PIPELINE] Reactive executor workers: {pipeline_workers}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=pipeline_workers) as executor:
        future_to_step = {}
        while len(successful_steps) + len(failed_steps) + len(skipped_steps) < len(steps):
            _heartbeat_pipeline_lock()
            sequential_running = any(_is_sequential_step(name, execution_contracts) for name in running_steps)
            launched_sequential = False
            for step in steps:
                name = step["name"]
                if name in successful_steps or name in failed_steps or name in skipped_steps or name in running_steps:
                    continue
                step_is_sequential = _is_sequential_step(name, execution_contracts)
                if sequential_running:
                    continue
                if step_is_sequential and running_steps:
                    continue
                if launched_sequential:
                    continue
                deps = step.get("depends_on", [])
                active_deps = [d for d in deps if d in all_step_names]
                if all(d in successful_steps for d in active_deps):
                    if step_is_sequential:
                        logger.info(f"[LAUNCH] Sequential Policy Launch: {name}")
                        launched_sequential = True
                    else:
                        logger.info(f"[LAUNCH] Reactive Launch: {name}")
                    future = executor.submit(run_step, name, step["func"], **step["kwargs"])
                    _pipeline_receipt_progress(
                        "step_started",
                        step_id=name,
                        step_status="running",
                    )
                    running_steps[name] = future
                    started_at = time.time()
                    running_step_started_at[name] = started_at
                    running_step_last_heartbeat[name] = started_at
                    future_to_step[future] = name
                elif any(d in failed_steps or d in skipped_steps for d in active_deps):
                    logger.warning(f"[SKIP] Reactive Skip: {name} (Dependency Failure)")
                    skipped_steps.add(name)
                    pipeline_metrics.append({"name": name, "status": "skipped", "seconds": 0.0})

            if not running_steps:
                if len(successful_steps) + len(failed_steps) + len(skipped_steps) < len(steps):
                    logger.error("[FAIL] Deadlock detected in reactive orchestrator!")
                    failed_steps.add("__reactive_deadlock__")
                    pipeline_metrics.append({"name": "__reactive_deadlock__", "status": "failed", "seconds": 0.0})
                    break
                continue

            done, _ = concurrent.futures.wait(running_steps.values(), return_when=concurrent.futures.FIRST_COMPLETED, timeout=0.1)
            now = time.time()
            for running_name in sorted(running_steps):
                started_at = running_step_started_at.get(running_name, now)
                last_heartbeat = running_step_last_heartbeat.get(running_name, started_at)
                if now - last_heartbeat >= pipeline_heartbeat_interval:
                    logger.info(
                        "[PIPELINE_HEARTBEAT] step=%s elapsed_seconds=%.1f next_update_within_seconds=%s",
                        running_name,
                        now - started_at,
                        pipeline_heartbeat_interval,
                    )
                    running_step_last_heartbeat[running_name] = now
                    _heartbeat_pipeline_lock(force=True)
                    _pipeline_receipt_progress(
                        "heartbeat",
                        active_steps=sorted(running_steps)[:50],
                    )
            for future in done:
                finished_name = future_to_step.pop(future, None)
                if not finished_name:
                    continue
                running_steps.pop(finished_name, None)
                running_step_started_at.pop(finished_name, None)
                running_step_last_heartbeat.pop(finished_name, None)
                try:
                    success, elapsed = future.result()
                    if success:
                        successful_steps.add(finished_name)
                        pipeline_metrics.append({"name": finished_name, "status": "success", "seconds": round(elapsed, 2)})
                        _pipeline_receipt_progress(
                            "step_completed",
                            step_id=finished_name,
                            step_status="success",
                        )
                    else:
                        failed_steps.add(finished_name)
                        pipeline_metrics.append({"name": finished_name, "status": "failed", "seconds": round(elapsed, 2)})
                        _pipeline_receipt_progress(
                            "step_completed",
                            step_id=finished_name,
                            step_status="failed",
                        )
                except Exception as exc:
                    logger.error(f"[FAIL] Step '{finished_name}' exception: {exc}")
                    failed_steps.add(finished_name)
                    pipeline_metrics.append({"name": finished_name, "status": "failed", "seconds": 0.0})
                    _pipeline_receipt_progress(
                        "step_completed",
                        step_id=finished_name,
                        step_status="failed",
                    )

    _pipeline_receipt_progress("pipeline_completed")
    total_time = time.time() - total_start
    try:
        from tools.core.config import REPORTS_DIR
        save_json_atomic(REPORTS_DIR / "pipeline_metrics.json", {
            "total_seconds": round(total_time, 2),
            "steps": pipeline_metrics
        })
    except Exception as e:
        logger.error(f"Failed to generate pipeline_metrics.json: {e}")

    cache_manager.update_cache(changed_files=changed_files)
    logger.info("\n" + "=" * 80)
    logger.info(f"PIPELINE ANALYSIS COMPLETED in {total_time:.2f}s".center(80))
    quality_gate_payload = (
        load_json_file(RAW_DIR / "quality_gate.json", {})
        if "Quality Gates" in successful_steps
        else {}
    )
    consumer_scope_authorities: dict[str, dict | None] = {}
    if "Audit" in successful_steps and run_mode != "watchdog_save_pulse":
        consumer_scope_authorities["audit"] = _consumer_scope_authority(
            "audit",
            load_json_file(RAW_DIR / "audit_report.json", {}),
        )
    if "Architecture Oracle" in successful_steps:
        consumer_scope_authorities["architecture_oracle"] = _consumer_scope_authority(
            "architecture_oracle",
            load_json_file(RAW_DIR / "architecture_oracle.json", {}),
        )
    if "Oracle Validation Gate" in successful_steps:
        consumer_scope_authorities["validation_oracle"] = _consumer_scope_authority(
            "validation_oracle",
            load_json_file(RAW_DIR / "validation_oracle_scope.json", {}),
        )
    if "Quality Gates" in successful_steps:
        consumer_scope_authorities["quality_gate"] = _consumer_scope_authority(
            "quality_gate",
            quality_gate_payload,
        )
    analysis_scope_authority = reconcile_consumer_scope_authorities(
        analysis_scope_authority,
        consumer_scope_authorities,
    )
    scope_details = scope_receipt_details(analysis_scope_authority)
    governance_verdict = pipeline_completion_governance_verdict(successful_steps, quality_gate_payload)
    if analysis_scope_authority.get("evidence_status") == INCOMPLETE_EVIDENCE:
        governance_verdict = INCOMPLETE_EVIDENCE
    logger.info(
        (
            f"  Step execution: completed={len(successful_steps)} | "
            f"skipped={len(skipped_steps)} | failed={len(failed_steps)}"
        ).center(80)
    )
    logger.info(f"  Governance verdict: {governance_verdict}".center(80))
    logger.info(
        (
            f"  Scope evidence: {analysis_scope_authority.get('evidence_status')} | "
            f"claim={analysis_scope_authority.get('claim_scope')} | "
            f"consumers={analysis_scope_authority.get('consumer_scope_consistency')}"
        ).center(80)
    )
    logger.info("=" * 80 + "\n")

    evidence_identities = sorted(
        {
            str(artifact)
            for step_name in successful_steps
            for artifact in execution_contracts.get(step_name, {}).get("writes_artifacts", [])
            if str(artifact)
        }
    )
    _PIPELINE_TERMINAL_DETAILS = {
        "terminal_status": "FAILED" if failed_steps else "PASS",
        "exit_code": 1 if failed_steps else 0,
        "interruption_reason": "pipeline_step_failure" if failed_steps else "none",
        "governance_verdict": governance_verdict,
        "completed_steps": sorted(successful_steps),
        "failed_steps": sorted(failed_steps),
        "skipped_steps": sorted(skipped_steps),
        "evidence_identities": evidence_identities,
        "scope_details": scope_details,
    }
    if failed_steps:
        sys.exit(1)

if __name__ == "__main__":
    from tools.core.config import ensure_output_dir
    ensure_output_dir()
    raise SystemExit(pipeline_process_exit_code(main()))

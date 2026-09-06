import argparse
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CODE_MAPS_DIR = Path(_ROOT)
PUBLIC_DISTRIBUTION_MANIFEST = CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json"
from tools.core.vendor_bootstrap import inject_vendor_paths
from tools.core.operational_limits import bootstrap_command_timeout_seconds, cli_pipeline_refresh_timeout_seconds
from tools.core.python_runtime_env import isolated_python_subprocess_env
from tools.core.mcp_runtime_config import build_mcp_runtime_contract
from tools.core.stdio import configure_utf8_stdio
from tools.core.heartbeat_cadence import (
    heartbeat_cadence_selection,
    local_duration_guidance,
    record_execution_duration,
)
from tools.core.installation_preflight import (
    build_installation_plan,
    installation_feature_dependencies,
    render_console_lines,
    write_installation_plan,
)
from tools.core.subprocess_telemetry import process_group_popen_kwargs, terminate_process_tree

VENDOR_PATHS = inject_vendor_paths(CODE_MAPS_DIR)

configure_utf8_stdio()

TOOLS_DIR = CODE_MAPS_DIR / "tools"
ENGINES_DIR = TOOLS_DIR / "engines"
COMPILER_SCRIPT = TOOLS_DIR / "config_compiler.py"
GOVERNANCE_SYNC_SCRIPT = TOOLS_DIR / "governance_sync.py"
LIFECYCLE_VALIDATOR_SCRIPT = TOOLS_DIR / "validate_lifecycle.py"
INIT_SCRIPT = TOOLS_DIR / "orchestrators" / "discovery.py"
PIPELINE_SCRIPT = TOOLS_DIR / "orchestrators" / "orchestrator.py"
WATCHDOG_SCRIPT = TOOLS_DIR / "orchestrators" / "watchdog.py"
MCP_SCRIPT = TOOLS_DIR / "mcp" / "server.py"
AUTO_DOCTRINE_SCRIPT = TOOLS_DIR / "auto_doctrine.py"
PROJECT_TRUTH_SYNC_SCRIPT = TOOLS_DIR / "sync_project_truth_layers.py"

RUNTIME_DEPENDENCIES = {}

FEATURE_DEPENDENCIES = installation_feature_dependencies()


def log(message, symbol="[BOOTSTRAP]"):
    print(f"{symbol} {message}", flush=True)


def _installation_duration_guidance():
    try:
        guidance = local_duration_guidance("installation_preflight")
        phase = guidance.get("phases", {}).get("canonical_target_preflight", {})
        if phase.get("status") == "available":
            return (
                f"p50_seconds={phase.get('p50_seconds')} "
                f"p95_seconds={phase.get('p95_seconds')} samples={phase.get('sample_count')}"
            )
        return f"unknown basis={guidance.get('basis')} samples={phase.get('sample_count', 0)}"
    except Exception as exc:
        return f"unknown basis=duration_policy_unavailable error={type(exc).__name__}"


def _build_installation_plan_with_progress(target_root, *, dependency_install_enabled):
    cadence = heartbeat_cadence_selection("subprocess")
    interval = max(1, int(cadence.get("interval_seconds") or 15))
    started = time.perf_counter()
    stop_event = threading.Event()
    log(
        "phase=canonical_target_preflight status=started "
        f"expected_duration={_installation_duration_guidance()} "
        f"heartbeat_seconds={interval} heartbeat_basis={cadence.get('basis')}",
        "[INSTALL-PLAN]",
    )

    def heartbeat():
        while not stop_event.wait(interval):
            elapsed = round(time.perf_counter() - started, 1)
            log(
                f"phase=canonical_target_preflight status=running elapsed_seconds={elapsed}",
                "[INSTALL-PLAN]",
            )

    worker = threading.Thread(target=heartbeat, name="installation-preflight-heartbeat", daemon=True)
    worker.start()
    status = "failed"
    try:
        plan = build_installation_plan(
            target_root,
            dependency_install_enabled=dependency_install_enabled,
        )
        status = "completed"
        return plan
    finally:
        stop_event.set()
        worker.join(timeout=1)
        duration = time.perf_counter() - started
        duration_identifier = (
            "installation canonical target preflight"
            if status == "completed"
            else "installation canonical target preflight failed"
        )
        record_execution_duration(
            "subprocess_execution",
            duration_identifier,
            duration,
        )
        log(
            f"phase=canonical_target_preflight status={status} elapsed_seconds={round(duration, 1)}",
            "[INSTALL-PLAN]",
        )


def module_available(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def import_target_available(module_name, attribute_name=None):
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return False
    if not attribute_name:
        return True
    return hasattr(module, attribute_name)


def find_vendor_access_issues():
    issues = []
    for vendor_dir in VENDOR_PATHS:
        for package_dir in ("json_repair", "rich", "mcp", "watchdog"):
            target = vendor_dir / package_dir
            if not target.exists():
                continue
            try:
                os.listdir(target)
            except PermissionError:
                issues.append(f"{vendor_dir.name}:{package_dir}")
    return issues


def check_python_dependencies(fail_on_missing=False):
    missing = []
    for label, (module_name, package_name, attribute_name) in RUNTIME_DEPENDENCIES.items():
        if not import_target_available(module_name, attribute_name):
            missing.append((label, package_name))

    if not missing:
        log("Python runtime dependencies verified.", "[OK]")
        return True

    package_list = ", ".join(sorted({package for _, package in missing}))
    level = "[FAIL]" if fail_on_missing else "[WARN]"
    log(f"Missing Python runtime dependencies: {package_list}", level)
    log("Install with `pip install -r requirements.txt` or `pip install -e .`.", "[INFO]")
    if fail_on_missing:
        sys.exit(1)
    return False


def check_feature_dependencies():
    missing = []
    for feature_name, (module_name, package_name, attribute_name) in FEATURE_DEPENDENCIES.items():
        if not import_target_available(module_name, attribute_name):
            missing.append((feature_name, package_name))
    if not missing:
        log("Default human-and-AI profile runtimes verified.", "[OK]")
        return True

    for feature_name, package_name in missing:
        log(f"Default profile runtime unavailable: {feature_name} ({package_name})", "[WARN]")
    return False


def run_command(args, cwd=None, optional=False, telemetry_identifier=None, timeout_seconds=None):
    import sys
    import os
    if args and args[0] == "python":
        args[0] = sys.executable
    safe_env = isolated_python_subprocess_env(
        os.environ,
        code_maps_dir=CODE_MAPS_DIR,
        vendor_paths=VENDOR_PATHS,
    )
    started = time.perf_counter()
    proc_handle = None
    try:
        timeout = int(timeout_seconds or bootstrap_command_timeout_seconds())
        proc_handle = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            env=safe_env,
            **process_group_popen_kwargs(),
        )
        try:
            returncode = proc_handle.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process_tree(proc_handle)
            try:
                proc_handle.wait(timeout=5)
                cleanup_status = "process_tree_terminated"
            except subprocess.TimeoutExpired:
                terminate_process_tree(proc_handle)
                cleanup_status = "process_tree_cleanup_incomplete"
            try:
                from tools.core.config import RAW_DIR, save_json_atomic

                save_json_atomic(
                    RAW_DIR / "bootstrap_child_closeout.json",
                    {
                        "meta": {
                            "kind": "bootstrap_child_closeout",
                            "version": "v1",
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                        },
                        "status": "TIMED_OUT",
                        "command": [str(item) for item in args],
                        "child_pid": proc_handle.pid,
                        "timeout_seconds": timeout,
                        "cleanup_status": cleanup_status,
                        "lock_recovery": (
                            "Do not delete the pipeline lock manually. Inspect the recorded child PID and "
                            "rerun only after no owned process remains."
                        ),
                    },
                )
            except Exception as receipt_exc:
                log(f"Bootstrap timeout receipt could not be persisted: {receipt_exc}", "[WARN]")
            log(
                f"Command timed out after {timeout}s; owned process tree cleanup={cleanup_status} "
                f"pid={proc_handle.pid}.",
                "[FAIL]",
            )
            raise
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, args)
        return True
    except subprocess.CalledProcessError as exc:
        if optional:
            log(f"Optional step failed: {' '.join(args)} ({exc.returncode})", "[WARN]")
            return False
        raise
    finally:
        if telemetry_identifier:
            record_execution_duration(
                "subprocess_execution",
                str(telemetry_identifier),
                time.perf_counter() - started,
            )


def check_env(installation_plan=None):
    log("Checking target-aware environment...")
    import sys
    import os
    safe_env = isolated_python_subprocess_env(
        os.environ,
        code_maps_dir=CODE_MAPS_DIR,
        vendor_paths=VENDOR_PATHS,
    )
    try:
        subprocess.run([sys.executable, "--version"], check=True, capture_output=True, env=safe_env, timeout=bootstrap_command_timeout_seconds())
        log("Python verified.", "[OK]")
    except Exception:
        log("Python not found in PATH.", "[FAIL]")
        raise SystemExit(1)

    check_python_dependencies(fail_on_missing=False)
    check_feature_dependencies()
    if installation_plan:
        for line in render_console_lines(installation_plan):
            print(line)
    vendor_access_issues = find_vendor_access_issues()
    if vendor_access_issues:
        details = ", ".join(vendor_access_issues)
        log(f"Vendor package directories are present but unreadable: {details}", "[WARN]")


def install_deps(installation_plan):
    actions = installation_plan.get("actions", [])
    if not actions:
        log("No SAGE-local dependency installation is required.", "[OK]")
        return

    for action in actions:
        command = action.get("command", [])
        if not command:
            continue
        action_id = action.get("id", "unknown")
        cwd = action.get("cwd")
        log(f"Executing planned dependency action: {action_id}")
        run_command(command, cwd=Path(cwd) if cwd else None)


def run_init():
    log("Running discovery proposal generation...")
    run_command(["python", str(INIT_SCRIPT)])


def run_auto_doctrine():
    log("Executing auto-doctrine template generation...")
    run_command(["python", str(AUTO_DOCTRINE_SCRIPT)])


def compile_runtime_config():
    log("Compiling runtime config from discovery + overrides...")
    run_command(["python", str(COMPILER_SCRIPT), "--apply"])


def sync_governance():
    log("Refreshing workspace-scoped overrides from discovery...")
    run_command(["python", str(GOVERNANCE_SYNC_SCRIPT), "--apply"])


def run_preflight_validation(optional=False):
    log("Refreshing validation-critical artifacts (quality gates chain)...")
    run_command(["python", str(PIPELINE_SCRIPT), "--step", "qualitygates"], optional=optional)
    log("Running lifecycle preflight validation...")
    run_command(["python", str(LIFECYCLE_VALIDATOR_SCRIPT)], optional=optional)


def run_refresh():
    run_init()
    run_auto_doctrine()
    sync_governance()
    compile_runtime_config()
    log("Synchronizing workspace truth layers (prune stale project truth)...")
    run_command(["python", str(PROJECT_TRUTH_SYNC_SCRIPT), "--prune-stale"])


def run_pulse():
    log("Executing initial full pipeline...")
    run_command(
        ["python", str(PIPELINE_SCRIPT), "--full", "--force"],
        telemetry_identifier="bootstrap_full_pipeline",
        timeout_seconds=cli_pipeline_refresh_timeout_seconds(),
    )


def generate_mcp_snippet():
    log("Generating MCP configuration preview...", "[MCP]")
    runtime = build_mcp_runtime_contract(
        source_env=os.environ,
        code_maps_dir=CODE_MAPS_DIR,
        vendor_paths=VENDOR_PATHS,
        mcp_script=MCP_SCRIPT,
    )

    print("\n" + "=" * 60)
    print("NEXORA SAGE MCP CONFIGURATION".center(60))
    print("=" * 60)
    print(json.dumps(runtime["client_config"], indent=2, ensure_ascii=False))
    print("=" * 60)
    print("Copy the JSON above into your MCP settings if needed.\n")


def _install_and_verify(installation_plan, target_root):
    install_deps(installation_plan)
    verified_plan = build_installation_plan(
        target_root,
        target_profile=installation_plan.get("target"),
        dependency_install_enabled=False,
    )
    write_installation_plan(verified_plan)
    for line in render_console_lines(verified_plan):
        print(line)
    if verified_plan.get("summary", {}).get("status") == "BLOCKED":
        log("Dependency verification remains blocked after planned actions.", "[FAIL]")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Nexora SAGE bootstrap and workspace refresh entrypoint.")
    parser.add_argument(
        "--mode",
        choices=["refresh", "setup", "full", "preflight", "dependencies"],
        default="full",
        help="refresh: discovery/governance/config, setup: env+deps+refresh without analysis, dependencies: install deps only, full: env+deps+refresh+pipeline, preflight: env only",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Do not install missing SAGE-local dependencies; fail closed when they are required.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Inspect target and machine requirements without installing dependencies or running discovery/analysis.",
    )
    parser.add_argument(
        "--target-root",
        help="Explicit repository root to discover and initialize. Required by public product projections.",
    )
    args = parser.parse_args()

    if PUBLIC_DISTRIBUTION_MANIFEST.is_file() and not args.target_root:
        parser.error(
            "this public SAGE distribution requires --target-root <repository>"
        )

    if args.target_root:
        target_root = Path(args.target_root).expanduser()
        target_root = (
            (Path.cwd() / target_root).resolve()
            if not target_root.is_absolute()
            else target_root.resolve()
        )
        if not target_root.is_dir():
            parser.error(f"--target-root is not a directory: {target_root}")
        os.environ["CODEMAPS_TARGET_ROOT"] = str(target_root)
        log(f"Explicit analyzed repository: {target_root}", "[TARGET]")
    else:
        target_root = CODE_MAPS_DIR.parent.resolve()
        log(f"Embedded/default analyzed repository: {target_root}", "[TARGET]")

    print("\n" + " NEXORA SAGE BOOTSTRAP ".center(60, "="))
    print("Target-aware discovery/config bootstrap\n")

    installation_plan = _build_installation_plan_with_progress(
        target_root,
        dependency_install_enabled=not args.skip_deps,
    )
    write_installation_plan(installation_plan)
    check_env(installation_plan)
    if installation_plan.get("summary", {}).get("status") == "BLOCKED":
        print("[FAIL] Installation preflight is blocked; no dependency or target mutation was attempted.")
        return 2

    if args.plan_only:
        print("[OK] Read-only installation plan completed.")
        return 0

    if args.mode == "preflight":
        print("[OK] Environment preflight completed.")
        return 0

    if args.mode == "dependencies":
        if installation_plan.get("actions"):
            if not _install_and_verify(installation_plan, target_root):
                return 2
            print("[OK] Dependency bootstrap completed.")
        else:
            print("[OK] No dependency bootstrap action was required.")
        return 0

    if args.mode == "refresh":
        run_refresh()
        print("[OK] Workspace refresh completed.")
        print("[NEXT] Run 'python sage.py run --full --force' for a full analysis.\n")
        return 0

    if args.mode == "setup":
        started = time.perf_counter()
        if installation_plan.get("actions") and not _install_and_verify(installation_plan, target_root):
            return 2
        run_refresh()
        generate_mcp_snippet()
        record_execution_duration("subprocess_execution", "bootstrap_setup_only", time.perf_counter() - started)
        print("[OK] Setup-only bootstrap completed without running analysis.")
        print("[NEXT] Run 'python sage.py run --profile daily' or another explicit analysis profile.\n")
        return 0

    if installation_plan.get("actions") and not _install_and_verify(installation_plan, target_root):
        return 2

    run_refresh()
    run_pulse()
    generate_mcp_snippet()

    print("[OK] Bootstrap completed.")
    print("[NEXT] Run 'python sage.py watch' for live monitoring.")
    print("[AI] Add 'SKILL.md' to your AI context if needed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

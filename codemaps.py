import argparse
import copy
import hashlib
import importlib
import importlib.util
import json
import os
import site
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from tools.core.vendor_bootstrap import inject_vendor_paths
from tools.core.python_runtime_env import isolated_python_subprocess_env, python_subprocess_env
from tools.core.mcp_runtime_config import build_mcp_runtime_contract
from tools.core.installation_preflight import installation_feature_dependencies
from tools.core.stdio import configure_utf8_stdio


CODE_MAPS_DIR = Path(__file__).resolve().parent
PUBLIC_DISTRIBUTION_MANIFEST = CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json"
CLI_COMMAND_CONTRACT = CODE_MAPS_DIR / "config" / "cli_command_contract.json"
TARGET_REPOSITORY_PROOF_CONTRACT = (
    CODE_MAPS_DIR / "config" / "target_repository_proof_contract.json"
)
VENDOR_PATHS = inject_vendor_paths(CODE_MAPS_DIR)


def is_public_distribution() -> bool:
    """Return whether this checkout is an authorized public projection."""
    return PUBLIC_DISTRIBUTION_MANIFEST.is_file()


def top_level_command_visibility() -> dict[str, dict]:
    """Load the exhaustive top-level CLI visibility contract."""
    payload = json.loads(CLI_COMMAND_CONTRACT.read_text(encoding="utf-8"))
    validation = payload.get("validation") if isinstance(payload, dict) else None
    visibility = (
        validation.get("top_level_command_visibility")
        if isinstance(validation, dict)
        else None
    )
    classes = visibility.get("classes") if isinstance(visibility, dict) else None
    if not isinstance(classes, dict) or not classes:
        raise RuntimeError("CLI command visibility contract is missing or empty")
    return classes


def visible_top_level_cli_commands(*, public_distribution: bool) -> set[str]:
    classes = top_level_command_visibility()
    declared: list[str] = []
    visible: set[str] = set()
    for class_id, row in classes.items():
        if not isinstance(row, dict):
            raise RuntimeError(f"Invalid CLI visibility class: {class_id}")
        commands = row.get("commands")
        if not isinstance(commands, list) or not all(isinstance(item, str) and item for item in commands):
            raise RuntimeError(f"Invalid CLI command list for visibility class: {class_id}")
        declared.extend(commands)
        if not public_distribution or row.get("public_distribution") is True:
            visible.update(commands)
    duplicates = sorted({name for name in declared if declared.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"CLI commands belong to multiple visibility classes: {duplicates}")
    return visible


def target_repository_proof_cli_contract() -> dict[str, object]:
    """Load target-proof parser and execution policy from its canonical contract."""
    payload = json.loads(
        TARGET_REPOSITORY_PROOF_CONTRACT.read_text(encoding="utf-8")
    )
    public_cli = payload.get("public_cli") if isinstance(payload, dict) else None
    modes = payload.get("modes") if isinstance(payload, dict) else None
    authority = payload.get("authority") if isinstance(payload, dict) else None
    policies = public_cli.get("refresh_policies") if isinstance(public_cli, dict) else None
    default_policy = (
        public_cli.get("default_refresh_policy")
        if isinstance(public_cli, dict)
        else None
    )
    refresh_profile = (
        public_cli.get("refresh_execution_profile")
        if isinstance(public_cli, dict)
        else None
    )
    default_mode = public_cli.get("default_mode") if isinstance(public_cli, dict) else None
    allowed_verdicts = (
        authority.get("allowed_verdicts") if isinstance(authority, dict) else None
    )
    successful_verdicts = (
        public_cli.get("successful_verdicts")
        if isinstance(public_cli, dict)
        else None
    )
    if (
        not isinstance(policies, list)
        or not policies
        or not all(isinstance(item, str) and item for item in policies)
        or not isinstance(default_policy, str)
        or default_policy not in policies
        or not isinstance(refresh_profile, str)
        or not refresh_profile
        or not isinstance(modes, dict)
        or not modes
        or not isinstance(default_mode, str)
        or default_mode not in modes
        or not isinstance(allowed_verdicts, list)
        or not allowed_verdicts
        or not isinstance(successful_verdicts, list)
        or not successful_verdicts
        or not set(successful_verdicts).issubset(set(allowed_verdicts))
    ):
        raise RuntimeError("Target repository proof CLI contract is invalid")
    return {
        "refresh_policies": tuple(policies),
        "default_refresh_policy": default_policy,
        "refresh_execution_profile": refresh_profile,
        "modes": tuple(modes),
        "default_mode": default_mode,
        "allowed_verdicts": tuple(str(item) for item in allowed_verdicts),
        "successful_verdicts": tuple(str(item) for item in successful_verdicts),
    }


def _apply_top_level_cli_visibility(subparsers, *, public_distribution: bool) -> None:
    """Prune one canonical parser through the machine-readable visibility contract."""
    actual = set(subparsers.choices)
    declared = visible_top_level_cli_commands(public_distribution=False)
    if actual != declared:
        missing = sorted(actual - declared)
        stale = sorted(declared - actual)
        raise RuntimeError(
            "CLI visibility contract is not exhaustive: "
            f"unclassified={missing}, declared_without_parser={stale}"
        )
    visible = visible_top_level_cli_commands(public_distribution=public_distribution)
    for command in sorted(actual - visible):
        subparsers.choices.pop(command, None)
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if action.dest in visible
    ]


def _product_version() -> str:
    """Read the public version from the packaging SSOT."""
    try:
        payload = tomllib.loads((CODE_MAPS_DIR / "pyproject.toml").read_text(encoding="utf-8"))
        return str((payload.get("project") or {}).get("version") or "unknown")
    except (OSError, tomllib.TOMLDecodeError):
        return "unknown"

configure_utf8_stdio()

from tools.core.config import (
    CONFIG_DIR,
    DISCOVERY_FILE,
    OVERRIDES_FILE,
    CONFIG_FILE,
    RAW_DIR,
    ROOT,
    _target_output_slug,
    BOOTSTRAP_FILE as BOOTSTRAP_SCRIPT,
)
from tools.core.execution_identity import resolve_execution_identity
from tools.core.json_io import load_json_file
from tools.core.runtime_config_identity import runtime_config_needs_compile
from tools.core.validate_execution_profiles import (
    optional_validator_options,
    refresh_command_specs,
    remediation_options,
    doctor_validator_profile,
    resolve_validate_execution_profile,
    validation_profile_options,
    validator_commands_for_profile,
    validator_commands_for_set_id,
)
from tools.core.operational_limits import (
    cli_command_timeout_seconds,
    cli_pipeline_refresh_timeout_seconds,
    install_proof_timeout_seconds,
    setup_interactive_timeout_seconds,
    setup_wizard_step_timeout_seconds,
    sqlite_maintenance_full_vacuum_free_space_percent,
    sqlite_maintenance_incremental_page_limit,
    sqlite_write_timeout_seconds,
    pipeline_step_heartbeat_seconds,
)
from tools.core.init_execution_contract import init_mode_contract, render_init_preflight

DISCOVERY_SCRIPT = CODE_MAPS_DIR / "tools" / "orchestrators" / "discovery.py"
PIPELINE_SCRIPT = CODE_MAPS_DIR / "tools" / "orchestrators" / "orchestrator.py"
WATCHDOG_SCRIPT = CODE_MAPS_DIR / "tools" / "orchestrators" / "watchdog.py"
MCP_SCRIPT = CODE_MAPS_DIR / "tools" / "mcp" / "server.py"
PURGE_SCRIPT = CODE_MAPS_DIR / "tools" / "utils" / "system_purge.py"
LIFECYCLE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_lifecycle.py"
ENTRYPOINT_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_entrypoints_and_failures.py"
REACT_SUPPORT_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_react_support.py"
WATCHDOG_STRESS_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_watchdog_stress.py"
REACT_FIXTURE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_react_fixtures.py"
REACT_EDGE_CASE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_react_edge_cases.py"
ZUSTAND_SELECTOR_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_zustand_selector_contract.py"
REACT_TRANSITIVE_PROPAGATION_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_react_transitive_propagation.py"
REACT_V11_CONTRACT_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_react_v11_contracts.py"
REACT_TAXONOMY_REPORT_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_react_fixture_family_taxonomy_report.py"
REACT_UNIVERSAL_READINESS_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_react_universal_readiness.py"
PERFORMANCE_BUDGET_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_performance_budget.py"
DISTRIBUTION_HARDENING_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_distribution_hardening.py"
UNIVERSAL_PROOF_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_universal_proof.py"
STALE_REMEDIATION_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_stale_remediation.py"
SIGNAL_REGRESSION_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_signal_regression.py"
ARTIFACT_TRUST_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_artifact_trust.py"
SOURCE_CONTRACT_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_source_contracts.py"
POLYGLOT_CAPABILITY_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_polyglot_capabilities.py"
LANGUAGE_AGNOSTIC_SYMBOL_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_language_agnostic_symbols.py"
FRAMEWORK_CAPABILITY_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_framework_capabilities.py"
TEST_IMPACT_PROFILE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_test_impact_profiles.py"
REGISTRY_FOUNDATION_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_registry_foundation.py"
ARCHITECTURE_ORACLE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_architecture_oracle.py"
CLAIM_GUARD_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_claim_guard.py"
SQLITE_ARTIFACT_PARITY_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_sqlite_artifact_parity.py"
SQLITE_PROXY_COVERAGE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_sqlite_proxy_coverage.py"
MERGE_INTELLIGENCE_REGRESSION_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_merge_intelligence_regression.py"
CONTEXTOS_SIGNAL_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_contextos_signals.py"
CONTEXTOS_CONTRACT_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_contextos_contracts.py"
EXTERNAL_REACT_SMOKE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_external_react_smoke.py"
EXTERNAL_POLYGLOT_SMOKE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_external_polyglot_smoke.py"
L4_POLICY_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_l4_policy.py"
DEAD_CODE_GROUNDTRUTH_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_dead_code_groundtruth.py"
PERFORMANCE_LEDGER_UPDATER = CODE_MAPS_DIR / "tools" / "update_performance_ledger.py"
REACT_FIXTURE_IMPORTER = CODE_MAPS_DIR / "tools" / "import_react_fixture.py"
REACT_FIXTURE_SEED_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_react_fixture_seed.py"
PHASE_STATUS_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_phase_status.py"
TRANSCRIPT_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_clean_machine_transcript.py"
PROJECT_TRUTH_SYNC = CODE_MAPS_DIR / "tools" / "sync_project_truth_layers.py"
CI_RELEASE_CHECK = CODE_MAPS_DIR / "tools" / "ci_release_check.py"
SAGE_SELF_AUDIT_RUNNER = CODE_MAPS_DIR / "tools" / "run_sage_self_audit.py"
EXTERNAL_TARGET_PREFLIGHT = CODE_MAPS_DIR / "tools" / "external_target_preflight.py"
EXTERNAL_TARGET_INDEX = CODE_MAPS_DIR / "tools" / "generate_external_target_index.py"
TARGET_REPOSITORY_PROOF_GENERATOR = (
    CODE_MAPS_DIR / "tools" / "generate_target_repository_proof_bundle.py"
)
INSPECT_TARGET = CODE_MAPS_DIR / "tools" / "inspect_target.py"
NEXORA_BRIEF_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_nexora_brief.py"
NEXORA_AGENT_CONTRACT_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_nexora_agent_contract.py"
NEXORA_OPERATOR_PACKET_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_nexora_operator_packet.py"
MCP_AGENT_SURFACE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_mcp_agent_surface.py"
HITL_APPROVAL_LEDGER = CODE_MAPS_DIR / "tools" / "hitl_approval_ledger.py"
HITL_DECISION_REQUESTS = CODE_MAPS_DIR / "tools" / "hitl_decision_requests.py"
HITL_GOVERNANCE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_hitl_governance.py"
HITL_LIFECYCLE_SMOKE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_hitl_lifecycle_smoke.py"
NEXORA_AGENT_RESPONSE_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_nexora_agent_response.py"
NEXORA_AGENT_RESPONSE_LEDGER = CODE_MAPS_DIR / "tools" / "nexora_agent_response_ledger.py"
NEXORA_AGENT_HANDOFF_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_nexora_agent_handoff.py"
REPORT_FRESHNESS_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_report_freshness_index.py"
NEXORA_SURFACE_INVENTORY_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_nexora_surface_inventory.py"
TEST_GAP_REPORT_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_test_gap_report.py"
AI_AGENT_READINESS_REPORT_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_ai_agent_readiness_report.py"
PRODUCT_REPORTS_VALIDATOR = CODE_MAPS_DIR / "tools" / "validate_product_reports.py"
NEXORA_DEMO_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_nexora_demo.py"
INSTALLATION_PROOF_GENERATOR = CODE_MAPS_DIR / "tools" / "generate_installation_proof.py"
NEXORA_DASHBOARD_GENERATOR = CODE_MAPS_DIR / "tools" / "engines" / "html_dashboard_generator.py"
INTERACTIVE_DASHBOARD = CODE_MAPS_DIR / "tools" / "engines" / "dashboard.py"
SETUP_WIZARD = CODE_MAPS_DIR / "tools" / "orchestrators" / "setup_wizard.py"
STUDIO_MAPPER = CODE_MAPS_DIR / "tools" / "engines" / "studio_mapper.py"
RUNTIME_DEPENDENCIES = {}

FEATURE_DEPENDENCIES = installation_feature_dependencies()


def _safe_command_env(source_env=None):
    import os
    source = source_env or os.environ
    return isolated_python_subprocess_env(
        source,
        code_maps_dir=CODE_MAPS_DIR,
        vendor_paths=VENDOR_PATHS,
    )


def _with_git_safe_directory(env, target_path):
    """Preserve invocation-scoped Git config while trusting one analyzed root."""
    scoped = dict(env)
    try:
        count = max(0, int(scoped.get("GIT_CONFIG_COUNT", "0")))
    except (TypeError, ValueError):
        count = 0
    target_value = str(Path(target_path).resolve())
    for index in range(count):
        if (
            scoped.get(f"GIT_CONFIG_KEY_{index}") == "safe.directory"
            and str(scoped.get(f"GIT_CONFIG_VALUE_{index}") or "") == target_value
        ):
            return scoped
    scoped[f"GIT_CONFIG_KEY_{count}"] = "safe.directory"
    scoped[f"GIT_CONFIG_VALUE_{count}"] = target_value
    scoped["GIT_CONFIG_COUNT"] = str(count + 1)
    return scoped


def run_command(args, env=None, timeout=cli_command_timeout_seconds()):
    import sys
    if timeout == cli_command_timeout_seconds():
        try:
            from tools.core.artifact_store import get_adaptive_timeout
            timeout = get_adaptive_timeout(cli_command_timeout_seconds())
        except Exception:
            pass
    if args and args[0] == "python":
        args[0] = sys.executable
    if env is None:
        env = _safe_command_env()
    return subprocess.run(args, cwd=str(CODE_MAPS_DIR), check=False, env=env, timeout=timeout).returncode



def compileall_command():
    """Compile sources without touching repo-local __pycache__ files.

    Windows/OneDrive can lock existing __pycache__ entries. The release gate only
    needs syntax validation, so disable bytecode writes with -B.
    """
    return [
        "python",
        "-B",
        "-m",
        "compileall",
        "-q",
        "codemaps.py",
        "sage.py",
        "tools",
    ]


def _target_root_env(args):
    target_root = getattr(args, "target_root", None)
    if not target_root:
        return None
    target_path = Path(target_root).expanduser()
    if not target_path.is_absolute():
        target_path = (Path.cwd() / target_path).resolve()
    else:
        target_path = target_path.resolve()
    if not target_path.exists() or not target_path.is_dir():
        print(f"[TARGET] Invalid target root: {target_path}")
        return False
    env = _with_git_safe_directory(_safe_command_env(os.environ), target_path)
    env["CODEMAPS_TARGET_ROOT"] = str(target_path)
    reuse_mode = str(env.get("CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE") or "").strip()
    reuse_authorized = (
        reuse_mode == "installation_proof"
        and bool(env.get("CODEMAPS_TARGET_PREFLIGHT_RECEIPT"))
        and bool(env.get("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"))
    )
    if not reuse_authorized:
        env.pop("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", None)
        env.pop("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", None)
        env.pop("CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", None)
        env.pop("CODEMAPS_TARGET_PROJECTS", None)
    if getattr(args, "projects", None):
        env["CODEMAPS_TARGET_PROJECTS"] = str(args.projects)
    print(f"[TARGET] Analyzing external target root: {target_path}")
    return env


def _run_external_target_preflight_if_needed(args, target_env=None):
    target_root = getattr(args, "target_root", None)
    if not target_root:
        return 0
    if (
        isinstance(target_env, dict)
        and target_env.get("CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE") == "installation_proof"
    ):
        try:
            from tools.core.config import load_external_target_preflight_receipt
            from tools.external_target_preflight import persist_preflight, preflight_receipt_transport

            target_path = Path(str(target_env["CODEMAPS_TARGET_ROOT"])).resolve()
            receipt = load_external_target_preflight_receipt(
                target_path,
                source_env=target_env,
                require_freshness=True,
            )
            if receipt is None:
                raise RuntimeError("installation-proof Preflight receipt is missing")
            previous_run_id = str(receipt.get("meta", {}).get("run_id") or "")
            previous_hash = str(target_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"])
            rebound = copy.deepcopy(receipt)
            rebound_meta = rebound.setdefault("meta", {})
            rebound_meta.pop("run_id", None)
            rebound_meta.pop("artifact_semantics", None)
            rebound_meta["receipt_reuse"] = {
                "mode": "installation_proof",
                "reused_from_run_id": previous_run_id,
                "reused_from_sha256": previous_hash,
                "freshness_check": "target_observation_identity_match",
            }
            target_output_dir = (
                CODE_MAPS_DIR
                / "output"
                / "external_targets"
                / _target_output_slug(str(target_path))
            )
            external_run_id = str(target_env.get("CODEMAPS_EXTERNAL_RUN_ID") or "")
            if not external_run_id:
                raise RuntimeError("installation-proof Preflight reuse lacks an external generation identity")
            rebound.setdefault("target", {})["output_dir"] = str(
                target_output_dir / "generations" / external_run_id
            )
            persist_preflight(rebound)
            transport = preflight_receipt_transport(rebound)
            target_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT"] = transport["path"]
            target_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] = transport["sha256"]
            print(
                "[TARGET] Reused the init Preflight receipt after a current target-observation freshness check."
            )
            return 0
        except Exception as exc:
            print(f"[TARGET] Preflight receipt reuse failed closed: {type(exc).__name__}: {exc}")
            return 2
    if getattr(args, "skip_target_preflight", False):
        return 0
    cmd = ["python", str(EXTERNAL_TARGET_PREFLIGHT), str(Path(target_root).expanduser())]
    if getattr(args, "projects", None):
        cmd.extend(["--projects", args.projects])
    result = (
        run_command(cmd, env=target_env)
        if isinstance(target_env, dict) and target_env.get("CODEMAPS_EXTERNAL_RUN_ID")
        else run_command(cmd)
    )
    if result != 0 or not isinstance(target_env, dict):
        return result
    target_path = Path(str(target_env["CODEMAPS_TARGET_ROOT"])).resolve()
    target_output_dir = (
        CODE_MAPS_DIR
        / "output"
        / "external_targets"
        / _target_output_slug(str(target_path))
    )
    external_run_id = str(target_env.get("CODEMAPS_EXTERNAL_RUN_ID") or "")
    if external_run_id:
        target_output_dir = target_output_dir / "generations" / external_run_id
    receipt_path = (target_output_dir / ".raw" / "external_target_preflight.json").resolve()
    try:
        from tools.external_target_preflight import preflight_receipt_transport

        payload = load_json_file(receipt_path, {})
        transport = preflight_receipt_transport(payload)
    except Exception as exc:
        print(f"[TARGET] Preflight receipt transport failed: {type(exc).__name__}: {exc}")
        return 2
    target_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT"] = transport["path"]
    target_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] = transport["sha256"]
    return 0


def _begin_external_target_generation(target_env: dict, *, prefix: str = "sage-run"):
    from tools.core.external_target_generation import (
        begin_external_target_generation,
        new_external_target_run_id,
    )

    run_id = new_external_target_run_id(prefix)
    target_env["CODEMAPS_EXTERNAL_RUN_ID"] = run_id
    target_path = Path(str(target_env["CODEMAPS_TARGET_ROOT"])).resolve()
    target_dir = CODE_MAPS_DIR / "output" / "external_targets" / _target_output_slug(str(target_path))
    begin_external_target_generation(target_dir, run_id, target_path)
    return target_dir, run_id


def _runtime_truth_status() -> dict:
    discovery_exists = DISCOVERY_FILE.exists()
    overrides_exists = OVERRIDES_FILE.exists()
    config_exists = CONFIG_FILE.exists()
    stale_config = False
    freshness_reason = "runtime_config_missing" if not config_exists else "source_truth_missing"
    if config_exists and discovery_exists and overrides_exists:
        try:
            stale_config = runtime_config_needs_compile(DISCOVERY_FILE, OVERRIDES_FILE, CONFIG_FILE)
            freshness_reason = "content_identity_mismatch" if stale_config else "content_identity_match"
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            stale_config = True
            freshness_reason = f"content_identity_unavailable:{type(exc).__name__}"
    return {
        "discovery_exists": discovery_exists,
        "overrides_exists": overrides_exists,
        "config_exists": config_exists,
        "stale_config": stale_config,
        "freshness_reason": freshness_reason,
    }


def ensure_runtime_truth(preferred_scope: str = "run", *, allow_self_heal: bool = True) -> int:
    status = _runtime_truth_status()
    missing_discovery = not status["discovery_exists"]
    missing_overrides = not status["overrides_exists"]
    missing_config = not status["config_exists"]
    stale_config = status["stale_config"]

    if not (missing_discovery or missing_overrides or missing_config or stale_config):
        return 0

    if not allow_self_heal:
        if missing_discovery or missing_overrides or missing_config:
            print(
                f"[{preferred_scope.upper()}] Runtime truth is incomplete. Run `python sage.py init` in the Nexora SAGE workspace first.",
                flush=True,
            )
            return 1
        print(
            f"[{preferred_scope.upper()}] Runtime truth is stale; external target mode will not rewrite Nexora SAGE config.",
            flush=True,
        )
        return 0

    print(
        f"[{preferred_scope.upper()}] Runtime truth check: self-heal required "
        f"(reason={status['freshness_reason']}).",
        flush=True,
    )
    if missing_discovery or missing_overrides:
        print(f"[{preferred_scope.upper()}] Repairing missing discovery/config truth...", flush=True)
        refresh_chain = []
        if missing_discovery:
            refresh_chain.append(["python", str(DISCOVERY_SCRIPT)])
        if missing_overrides:
            refresh_chain.append(["python", str(CODE_MAPS_DIR / "tools" / "governance_sync.py"), "--apply"])
        if missing_config or stale_config or missing_discovery or missing_overrides:
            refresh_chain.append(["python", str(CODE_MAPS_DIR / "tools" / "config_compiler.py"), "--apply"])
        for step_cmd in refresh_chain:
            step_code = run_command(step_cmd)
            if step_code != 0:
                print(
                    f"[{preferred_scope.upper()}] Self-heal step failed: {' '.join(step_cmd[1:])}",
                    flush=True,
                )
                return step_code
        return 0

    if missing_config or stale_config:
        print(f"[{preferred_scope.upper()}] Compiling runtime config...", flush=True)
        compile_code = run_command(["python", str(CODE_MAPS_DIR / "tools" / "config_compiler.py"), "--apply"])
        if compile_code != 0:
            print(f"[{preferred_scope.upper()}] Runtime config compile failed.", flush=True)
            return compile_code

    return 0


def print_json(path: Path):
    if not path.exists():
        print(f"[WARN] Missing file: {path.name}")
        return 1
    try:
        data = load_json_file(path, {})
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"[FAIL] Could not read {path.name}: {exc}")
        return 1


def module_available(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def import_target_available(module_name, attribute_name=None):
    package_name = str(module_name or "").split(".", 1)[0]

    try:
        module = importlib.import_module(module_name)
    except PermissionError:
        blocked_sys_paths = _blocked_sys_paths_for_package(package_name)
        if not blocked_sys_paths:
            return False
        original_sys_path = list(sys.path)
        try:
            sys.path = [entry for entry in original_sys_path if entry not in blocked_sys_paths]
            importlib.invalidate_caches()
            module = importlib.import_module(module_name)
        except Exception:
            return False
        finally:
            sys.path = original_sys_path
    except Exception:
        return False
    if not attribute_name:
        return True
    return hasattr(module, attribute_name)


def _optional_dependency_roots() -> list[Path]:
    roots = list(VENDOR_PATHS)
    roots.extend(
        [
            CODE_MAPS_DIR / "vendor_runtime",
            CODE_MAPS_DIR / ".vendor",
            CODE_MAPS_DIR / ".vendor_user",
            CODE_MAPS_DIR.parent / "codemaps_vendor",
        ]
    )
    try:
        roots.append(Path(site.getusersitepackages()))
    except Exception:
        pass

    unique_roots = []
    seen = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(root)
    return unique_roots


def _blocked_sys_paths_for_package(package_name: str) -> set[str]:
    blocked: set[str] = set()
    if not package_name:
        return blocked
    for root in _optional_dependency_roots():
        package_path = root / package_name
        if not package_path.exists():
            continue
        try:
            os.listdir(package_path)
        except PermissionError:
            blocked.add(str(root))
    return blocked


def _permission_blocked_locations(package_name: str) -> list[str]:
    blocked = []
    for root in _optional_dependency_roots():
        package_path = root / package_name
        if not package_path.exists():
            continue
        try:
            os.listdir(package_path)
        except PermissionError:
            blocked.append(f"{root.name}:{package_name}")
    return blocked


def get_vendor_access_issues():
    issues = []
    for vendor_dir in _optional_dependency_roots():
        if not vendor_dir.exists():
            continue
        for package_dir in ("json_repair", "rich", "mcp", "watchdog"):
            target = vendor_dir / package_dir
            if not target.exists():
                continue
            try:
                os.listdir(target)
            except PermissionError:
                issues.append(f"{vendor_dir.name}:{package_dir}")
    return issues


def get_missing_runtime_dependencies():
    missing = []
    for label, (module_name, package_name, attribute_name) in RUNTIME_DEPENDENCIES.items():
        if not import_target_available(module_name, attribute_name):
            missing.append((label, package_name))
    return missing


def get_missing_feature_dependencies():
    missing = []
    blocked = []
    for feature_name, (module_name, package_name, attribute_name) in FEATURE_DEPENDENCIES.items():
        if import_target_available(module_name, attribute_name):
            continue
        blocked_locations = _permission_blocked_locations(package_name)
        if blocked_locations:
            blocked.append((feature_name, package_name, blocked_locations))
        else:
            missing.append((feature_name, package_name))
    return missing, blocked


def _python_environment_summary() -> dict:
    try:
        user_site = site.getusersitepackages()
    except Exception:
        user_site = ""
    return {
        "executable": sys.executable,
        "version": sys.version.split()[0],
        "user_site": user_site,
        "vendor_paths": [str(path) for path in VENDOR_PATHS],
        "path_head": sys.path[:5],
    }


def _artifact_needs_refresh(target: Path, dependencies: list[Path]) -> bool:
    if not target.exists():
        return True
    target_mtime = _mtime(target)
    for dependency in dependencies:
        if not dependency.exists():
            return True
        if _mtime(dependency) > target_mtime:
            return True
    return False


def _refresh_stale_validation_artifacts() -> int:
    refresh_plan = [
        {
            "name": "circular_deps",
            "target": RAW_DIR / "circular_deps.json",
            "deps": [RAW_DIR / "atlas.json"],
            "command": ["python", "-m", "tools.engines.circular_dependency_finder"],
        },
        {
            "name": "dead_code",
            "target": RAW_DIR / "dead_code.json",
            "deps": [RAW_DIR / "atlas.json"],
            "command": ["python", "-m", "tools.engines.dead_code_detector"],
        },
        {
            "name": "health_score",
            "target": RAW_DIR / "health_score.json",
            "deps": [RAW_DIR / "genome.json", RAW_DIR / "dead_code.json", RAW_DIR / "circular_deps.json"],
            "command": ["python", "-m", "tools.engines.health_score"],
        },
        {
            "name": "quality_gate",
            "target": RAW_DIR / "quality_gate.json",
            "deps": [RAW_DIR / "health_score.json", RAW_DIR / "react_support_matrix.json"],
            "command": ["python", "-m", "tools.engines.quality_gate"],
        },
        {
            "name": "contextos_signals",
            "target": RAW_DIR / "signals.json",
            "deps": [RAW_DIR / "circular_deps.json", RAW_DIR / "audit_report.json"],
            "command": ["python", "-m", "tools.engines.quant_engine"],
        },
    ]

    refreshed = []
    for item in refresh_plan:
        if not _artifact_needs_refresh(item["target"], item["deps"]):
            continue
        print(f"[VALIDATE] stale artifact detected: {item['name']} -> refreshing")
        code = run_command(item["command"])
        if code != 0:
            print(f"[VALIDATE] stale artifact refresh failed: {item['name']}")
            return code
        refreshed.append(item["name"])

    if refreshed:
        print(f"[VALIDATE] stale artifact refresh complete: {', '.join(refreshed)}")
    return 0


def cmd_init(args):
    print("=== INITIALIZING NEXORA SAGE v1 ===", flush=True)
    target_root = getattr(args, "target_root", None)
    projects = getattr(args, "projects", None)
    skip_deps = bool(getattr(args, "skip_deps", False))
    plan_only = bool(getattr(args, "plan_only", False))
    if PUBLIC_DISTRIBUTION_MANIFEST.is_file() and not target_root:
        print(
            "[TARGET] BLOCKED: this public SAGE distribution requires an explicit "
            "repository. Run `python sage.py init --target-root <repository>`."
        )
        return 2

    if plan_only:
        print("\n--- Read-only Installation Plan ---", flush=True)
        plan_cmd = ["python", str(BOOTSTRAP_SCRIPT), "--mode", "preflight", "--plan-only"]
        if skip_deps:
            plan_cmd.append("--skip-deps")
        if target_root:
            plan_cmd.extend(["--target-root", str(target_root)])
        if projects:
            plan_cmd.extend(["--projects", str(projects)])
        return run_command(plan_cmd, timeout=None)

    mode = "full" if bool(getattr(args, "full", False)) else "setup_only"
    mode_contract = init_mode_contract(mode)
    print("\n--- Execution Preflight ---")
    for line in render_init_preflight(mode, target_root=target_root):
        print(line)

    print("\n--- Phase 0: Pre-flight Check ---")
    doctor_args = argparse.Namespace(include_validate=False)
    doctor_exit = cmd_doctor(doctor_args)
    if doctor_exit != 0:
        print("[WARNING] Generic doctor found issues; target-aware bootstrap will decide whether they block this repository.")
    else:
        print("[OK] Environment is healthy.")

    if not skip_deps:
        print("\n--- Phase 1: Target-aware dependency installation enabled ---")
    else:
        print("\n--- Phase 1: Dependency installation disabled; missing required dependencies will block ---")

    print("\n--- Phase 2: Discovery and Initialization ---")
    full_cmd = ["python", str(BOOTSTRAP_SCRIPT), "--mode", str(mode_contract["bootstrap_mode"])]
    if skip_deps:
        full_cmd.append("--skip-deps")
    if target_root:
        full_cmd.extend(["--target-root", str(target_root)])
    if projects:
        full_cmd.extend(["--projects", str(projects)])
    init_code = run_command(full_cmd, timeout=setup_wizard_step_timeout_seconds())
    if init_code != 0:
        print(f"\n[FAIL] Initialization bootstrap failed with exit code {init_code}.")
        return init_code

    print("\n=== NEXORA SAGE INITIALIZED ===")
    return 0


def cmd_refresh(args):
    return run_command(["python", str(BOOTSTRAP_SCRIPT), "--mode", "refresh"])


def cmd_run(args):
    if getattr(args, "list_steps", False) and not getattr(args, "target_root", None):
        return run_command(["python", str(PIPELINE_SCRIPT), "--list-steps"], timeout=None)

    target_env = _target_root_env(args)
    if target_env is False:
        return 2
    external_generation = None
    if target_env:
        external_generation = _begin_external_target_generation(target_env)
    preflight_code = _run_external_target_preflight_if_needed(args, target_env)
    if preflight_code != 0:
        if external_generation:
            from tools.core.external_target_generation import finalize_external_target_generation

            finalize_external_target_generation(
                *external_generation,
                exit_code=preflight_code,
                require_audit=False,
            )
        return preflight_code
    truth_code = ensure_runtime_truth("run", allow_self_heal=target_env is None)
    if truth_code != 0:
        if external_generation:
            from tools.core.external_target_generation import finalize_external_target_generation

            finalize_external_target_generation(
                *external_generation,
                exit_code=truth_code,
                require_audit=False,
            )
        return truth_code

    cmd = ["python", str(PIPELINE_SCRIPT)]
    if getattr(args, "list_steps", False):
        cmd.append("--list-steps")
    if args.full:
        cmd.append("--full")
    if args.force:
        cmd.append("--force")
    if getattr(args, "refresh", False):
        cmd.append("--refresh")
    if getattr(args, "profile", None):
        cmd.extend(["--profile", args.profile])
    if args.step:
        cmd.extend(["--step", args.step])
    if args.from_step:
        cmd.extend(["--from-step", args.from_step])
    if args.projects:
        cmd.extend(["--projects", args.projects])
    if args.scope:
        cmd.extend(["--scope", args.scope])
    if args.ai_context:
        cmd.append("--ai-context")
    if args.skip_audit:
        cmd.append("--skip-audit")
    run_code = run_command(cmd, env=target_env, timeout=None)
    if target_env:
        from tools.core.external_target_generation import finalize_external_target_generation

        target_dir, run_id = external_generation
        generation = finalize_external_target_generation(
            target_dir,
            run_id,
            exit_code=run_code,
            require_audit=not bool(args.skip_audit),
            require_preflight=not bool(args.skip_target_preflight),
        )
        if run_code == 0 and generation.get("state") != "VALIDATED":
            print(f"[TARGET] Generation validation failed: {generation.get('validation_error')}")
            run_code = 3
        run_command(["python", str(EXTERNAL_TARGET_INDEX)])
    return run_code


def cmd_run_status(args):
    target_env = _target_root_env(args)
    if target_env is False:
        return 2
    if target_env and args.run_id:
        target_env["CODEMAPS_EXTERNAL_RUN_ID"] = str(args.run_id)
    cmd = ["python", str(CODE_MAPS_DIR / "tools" / "query_pipeline_run_receipt.py")]
    if args.run_id:
        cmd.extend(["--run-id", args.run_id])
    if args.wait:
        cmd.append("--wait")
    if args.timeout_seconds:
        cmd.extend(["--timeout-seconds", str(args.timeout_seconds)])
    if args.poll_seconds:
        cmd.extend(["--poll-seconds", str(args.poll_seconds)])
    if args.json:
        cmd.append("--json")
    if args.wait and float(args.timeout_seconds or 0) <= 0:
        timeout = None
    elif args.wait:
        timeout = max(60.0, float(args.timeout_seconds) + 30.0)
    else:
        timeout = 60
    return run_command(cmd, env=target_env, timeout=timeout)


def cmd_watch(args):
    raw_paths = getattr(args, "path", None)
    path_values = (
        [str(value) for value in raw_paths if str(value).strip()]
        if isinstance(raw_paths, (list, tuple))
        else [str(raw_paths)]
        if raw_paths
        else []
    )
    if not getattr(args, "once", False) and len(path_values) > 1:
        print("[WATCH] Live watchdog accepts one directory; repeated --path is only valid with --once.")
        return 2
    target_env = _target_root_env(args)
    if target_env is False:
        return 2
    external_generation = None
    if target_env:
        external_generation = _begin_external_target_generation(target_env, prefix="sage-watch")

    def close_external_watch(exit_code: int, reason: str) -> None:
        if external_generation:
            from tools.core.external_target_generation import close_external_target_generation_without_promotion

            close_external_target_generation_without_promotion(
                *external_generation,
                exit_code=exit_code,
                reason=reason,
            )

    preflight_code = _run_external_target_preflight_if_needed(args, target_env)
    if preflight_code != 0:
        close_external_watch(preflight_code, "external_watch_preflight_failed")
        return preflight_code
    truth_code = ensure_runtime_truth("watch", allow_self_heal=target_env is None)
    if truth_code != 0:
        close_external_watch(truth_code, "external_watch_runtime_truth_failed")
        return truth_code
    if not import_target_available("watchdog.events", "FileSystemEventHandler"):
        blocked = _permission_blocked_locations("watchdog")
        if blocked:
            print(
                "[WATCH] watch-live dependency is installed but blocked by filesystem permissions: "
                + ", ".join(blocked)
            )
        else:
            print("[WATCH] Missing optional dependency: watchdog. Install via `pip install -r requirements.txt`.")
        close_external_watch(1, "external_watch_dependency_unavailable")
        return 1

    env = python_subprocess_env(
        target_env or os.environ,
        code_maps_dir=CODE_MAPS_DIR,
        vendor_paths=VENDOR_PATHS,
    )

    cmd = ["python", "-m", "tools.orchestrators.watchdog"]
    for path_value in path_values:
        cmd.extend(["--path", path_value])
    if args.debounce is not None:
        cmd.extend(["--debounce", str(args.debounce)])
    if args.once:
        cmd.append("--once")
        env["SAGE_SYNC_SHADOW_WRITES"] = "1"
    public_command = ["python", "sage.py", "watch"]
    if args.target_root:
        public_command.extend(["--target-root", str(args.target_root)])
    for path_value in path_values:
        public_command.extend(["--path", path_value])
    if args.debounce is not None:
        public_command.extend(["--debounce", str(args.debounce)])
    if args.once:
        public_command.append("--once")
    env["SAGE_WATCHDOG_PRODUCER_COMMAND"] = json.dumps(public_command)
    
    try:
        watch_code = run_command(cmd, env=env, timeout=None)
    except BaseException:
        close_external_watch(130, "external_watch_interrupted")
        raise
    close_external_watch(watch_code, "external_watch_is_not_atomic_current_eligible")
    return watch_code


def cmd_validate(args):
    profile = resolve_validate_execution_profile(args)
    runtime_truth_mode = str(profile.get("runtime_truth_mode") or "")
    artifact_refresh_mode = str(profile.get("artifact_refresh_mode") or "")
    print(
        f"[VALIDATE] profile={profile['id']} cost_tier={profile['cost_tier']} "
        f"runtime_truth={runtime_truth_mode} artifact_refresh={artifact_refresh_mode}",
        flush=True,
    )
    print(f"[VALIDATE] claim_boundary: {profile['claim_boundary']}", flush=True)
    if runtime_truth_mode == "refresh":
        truth_code = ensure_runtime_truth("validate")
    elif runtime_truth_mode == "require_existing":
        truth_code = ensure_runtime_truth("validate snapshot", allow_self_heal=False)
    elif runtime_truth_mode == "not_required":
        truth_code = 0
        print("[VALIDATE] Runtime truth is intentionally not required for this source-clean profile.", flush=True)
    else:
        raise ValueError(f"Unsupported validation runtime truth mode: {runtime_truth_mode}")
    if truth_code != 0:
        return truth_code

    def refresh_commands() -> list[tuple[list[str], int]]:
        commands: list[tuple[list[str], int]] = []
        for spec in refresh_command_specs():
            kind = spec.get("kind")
            if kind == "pipeline_step":
                command = ["python", str(PIPELINE_SCRIPT), "--step", spec["step"]]
            elif kind == "module":
                command = ["python", "-m", spec["module"]]
            elif kind == "script":
                command = ["python", str(CODE_MAPS_DIR / spec["path"])]
            else:
                raise ValueError(f"Unsupported validation refresh command kind: {kind}")
            timeout = cli_pipeline_refresh_timeout_seconds() if spec.get("timeout_class") == "pipeline_refresh" else cli_command_timeout_seconds()
            commands.append((command, timeout))
        return commands

    should_refresh = artifact_refresh_mode == "refresh"
    if should_refresh:
        print("[VALIDATE] Refreshing validation-critical artifacts...", flush=True)
        for command, cmd_timeout in refresh_commands():
            refresh_code = run_command(command, timeout=cmd_timeout)
            if refresh_code != 0:
                print(f"[VALIDATE] Failed to refresh validation dependency chain: {' '.join(command[2:])}")
                return refresh_code
        freshness_code = _refresh_stale_validation_artifacts()
        if freshness_code != 0:
            return freshness_code
    elif artifact_refresh_mode == "existing_only":
        print("[VALIDATE] Using existing validation-critical artifacts; no refresh requested.", flush=True)
    elif artifact_refresh_mode != "not_applicable":
        raise ValueError(f"Unsupported validation artifact refresh mode: {artifact_refresh_mode}")

    commands = validator_commands_for_profile(profile)
    selected_optional = [
        option
        for option in optional_validator_options()
        if bool(getattr(args, f"validate_optional_{option.get('id')}", False))
    ]
    if selected_optional and artifact_refresh_mode == "not_applicable":
        raise ValueError("Source-clean validation does not accept runtime-backed optional validator flags")
    for option in selected_optional:
        command = ["python", str(CODE_MAPS_DIR / str(option["path"]))]
        if option.get("supports_baseline_update") and getattr(args, "validate_update_signal_baseline", False):
            command.append("--update-baseline")
        commands.append(command)

    def run_validators_once():
        exit_code = 0
        for command in commands:
            code = run_command(command)
            if code != 0:
                exit_code = code
        return exit_code

    overall = run_validators_once()
    if overall != 0 and getattr(args, "validate_auto_remediate_stale", False):
        if not should_refresh:
            print("[VALIDATE] Validator failure detected; this profile forbids refresh remediation. Rerun the live_refresh profile.")
            return overall
        print("[VALIDATE] Validator failure detected; running stale remediation refresh and retry once...")
        for command, cmd_timeout in refresh_commands():
            refresh_code = run_command(command, timeout=cmd_timeout)
            if refresh_code != 0:
                print(f"[VALIDATE] Remediation refresh failed: {' '.join(command[2:])}")
                return refresh_code
        freshness_code = _refresh_stale_validation_artifacts()
        if freshness_code != 0:
            return freshness_code
        overall = run_validators_once()

    if overall == 0:
        print("[VALIDATE] validators: PASS")
    return overall


def _doctor_execution_identity() -> dict:
    return resolve_execution_identity(
        public_distribution=is_public_distribution(),
        installation_root=CODE_MAPS_DIR,
        default_repository_root=ROOT,
    )


def _doctor_capability_release_blocks(system_scope: str, matrix_status: str | None) -> bool:
    return system_scope == "SAGE_ON_SAGE" and bool(matrix_status) and matrix_status != "PASS"


def _run_doctor_quick_validators(system_scope: str, max_seconds: int | None = None) -> int:
    truth_code = ensure_runtime_truth("doctor")
    if truth_code != 0:
        return truth_code

    profile = doctor_validator_profile(system_scope)
    commands = validator_commands_for_set_id(str(profile.get("validator_set_id") or ""))
    deadline = None
    if max_seconds is not None and max_seconds > 0:
        deadline = time.monotonic() + max_seconds

    exit_code = 0
    for command in commands:
        timeout = None
        if deadline is not None:
            timeout = max(1, deadline - time.monotonic())
            if timeout <= 1:
                print("- validators: TIMEOUT (quick validator budget exhausted)")
                return 124
        import sys
        if command and command[0] == "python":
            command[0] = sys.executable
        safe_env = _safe_command_env()
        try:
            code = subprocess.run(command, cwd=str(CODE_MAPS_DIR), check=False, env=safe_env, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            print(f"- validators: TIMEOUT ({Path(command[1]).name})")
            return 124
        if code != 0:
            exit_code = code
    return exit_code


def _capability_doctor_summary() -> dict:
    required_fields = {
        "id",
        "title",
        "domain",
        "language_scope",
        "maturity",
        "engines",
        "artifacts",
        "validators",
        "claim_boundary",
    }
    registry = load_json_file(CONFIG_DIR / "capability_registry.json", {})
    capabilities = registry.get("capabilities", []) if isinstance(registry, dict) else []
    invalid = []
    for capability in capabilities if isinstance(capabilities, list) else []:
        if not isinstance(capability, dict):
            invalid.append("<non-object>")
            continue
        missing = sorted(field for field in required_fields if field not in capability)
        if missing:
            invalid.append(f"{capability.get('id') or '<missing-id>'}:{','.join(missing)}")

    proof = load_json_file(RAW_DIR / "release_proof_bundle.json", {})
    matrix = proof.get("capability_release_matrix", {}) if isinstance(proof, dict) else {}
    matrix_summary = matrix.get("summary", {}) if isinstance(matrix, dict) else {}
    return {
        "registry_exists": bool(registry),
        "capabilities": len(capabilities) if isinstance(capabilities, list) else 0,
        "invalid": invalid,
        "release_matrix_status": matrix_summary.get("status"),
        "release_matrix_capabilities": matrix_summary.get("capabilities"),
        "release_matrix_counts": matrix_summary.get("status_counts"),
    }


def cmd_doctor(args):
    exit_code = 0
    execution_identity = _doctor_execution_identity()
    system_scope = str(execution_identity.get("system_scope") or "")
    print("[DOCTOR] Nexora SAGE workspace health")
    print(f"- Root: {CODE_MAPS_DIR}")
    print(f"- system-scope: {system_scope}")
    env_summary = _python_environment_summary()
    print(f"- python-env: {env_summary['executable']} ({env_summary['version']})")
    print(f"- python-user-site: {env_summary['user_site'] or 'n/a'}")
    if getattr(args, "verbose_env", False):
        print(f"- python-vendor-paths: {json.dumps(env_summary['vendor_paths'], ensure_ascii=False)}")
        print(f"- python-path-head: {json.dumps(env_summary['path_head'], ensure_ascii=False)}")
    for label, path in (
        ("discovery", DISCOVERY_FILE),
        ("overrides", OVERRIDES_FILE),
        ("config", CONFIG_FILE),
    ):
        status = "OK" if path.exists() else "MISSING"
        print(f"- {label}: {status} ({path.name})")
        if not path.exists():
            exit_code = 1

    capability_health = _capability_doctor_summary()
    if not capability_health["registry_exists"]:
        print("- capability-registry: MISSING (config/capability_registry.json)")
        exit_code = 1
    elif capability_health["invalid"]:
        print(f"- capability-registry: INVALID ({'; '.join(capability_health['invalid'][:5])})")
        exit_code = 1
    else:
        print(f"- capability-registry: OK ({capability_health['capabilities']} capabilities)")
    matrix_status = capability_health.get("release_matrix_status")
    if getattr(args, "skip_release_proof", False):
        print("- capability-release: SKIPPED (doctor --skip-release-proof)")
    elif system_scope != "SAGE_ON_SAGE":
        observed = f"; observed={matrix_status}" if matrix_status else ""
        print(
            "- capability-release: NOT REQUIRED "
            f"(repository doctor; SAGE self-release evidence is non-authoritative{observed})"
        )
    elif matrix_status:
        print(
            "- capability-release: "
            f"{matrix_status} ({capability_health.get('release_matrix_capabilities')} capabilities; "
            f"{capability_health.get('release_matrix_counts')})"
        )
        if _doctor_capability_release_blocks(system_scope, str(matrix_status)):
            exit_code = 1
    else:
        print("- capability-release: NOT GENERATED (run tools/run_release_proof_bundle.py for full proof)")

    missing_dependencies = get_missing_runtime_dependencies()
    if missing_dependencies:
        package_list = ", ".join(sorted({package for _, package in missing_dependencies}))
        print(f"- runtime-deps: MISSING ({package_list})")
        print("- install-hint: pip install -r requirements.txt  or  pip install -e .")
        exit_code = 1
    else:
        print("- runtime-deps: OK")

    missing_features, blocked_features = get_missing_feature_dependencies()
    if missing_features:
        feature_list = ", ".join(f"{feature} ({package})" for feature, package in missing_features)
        print(f"- feature-deps: OPTIONAL MISSING ({feature_list})")
        print("- feature-deps-impact: core analysis/release checks are unaffected; install optional deps for MCP/watch live.")
    elif blocked_features:
        blocked_list = ", ".join(
            f"{feature} ({package}) blocked at [{', '.join(locations)}]"
            for feature, package, locations in blocked_features
        )
        print(f"- feature-deps: OPTIONAL BLOCKED ({blocked_list})")
        print("- feature-deps-impact: core analysis/release checks are unaffected; repair permissions for MCP/watch live.")
    else:
        print("- feature-deps: OK")

    if getattr(args, "repair_optional_deps", False) and (missing_features or blocked_features):
        optional_packages = sorted({package for _, package in missing_features} | {package for _, package, _ in blocked_features})
        if optional_packages:
            print(f"- optional-deps-repair: attempting pip --user install for {', '.join(optional_packages)}")
            failed_repairs = []
            repaired = []
            for package_name in optional_packages:
                repair_cmd = ["python", "-m", "pip", "install", "--user", package_name]
                repair_code = run_command(repair_cmd)
                if repair_code != 0:
                    failed_repairs.append(package_name)
                else:
                    repaired.append(package_name)

            if failed_repairs:
                print(f"- optional-deps-repair: FAILED ({', '.join(failed_repairs)})")
                if repaired:
                    print(f"- optional-deps-repair: PARTIAL SUCCESS ({', '.join(repaired)})")
                print("- optional-deps-repair: non-fatal (optional feature dependencies)")
                missing_features, blocked_features = get_missing_feature_dependencies()
                if missing_features:
                    feature_list = ", ".join(f"{feature} ({package})" for feature, package in missing_features)
                    print(f"- feature-deps-after-repair: OPTIONAL MISSING ({feature_list})")
                elif blocked_features:
                    blocked_list = ", ".join(
                        f"{feature} ({package}) blocked at [{', '.join(locations)}]"
                        for feature, package, locations in blocked_features
                    )
                    print(f"- feature-deps-after-repair: OPTIONAL BLOCKED ({blocked_list})")
                else:
                    print("- feature-deps-after-repair: OK")
            else:
                print("- optional-deps-repair: OK")
                missing_features, blocked_features = get_missing_feature_dependencies()
                if missing_features:
                    feature_list = ", ".join(f"{feature} ({package})" for feature, package in missing_features)
                    print(f"- feature-deps-after-repair: OPTIONAL MISSING ({feature_list})")
                elif blocked_features:
                    blocked_list = ", ".join(
                        f"{feature} ({package}) blocked at [{', '.join(locations)}]"
                        for feature, package, locations in blocked_features
                    )
                    print(f"- feature-deps-after-repair: OPTIONAL BLOCKED ({blocked_list})")
                else:
                    print("- feature-deps-after-repair: OK")

    vendor_access_issues = get_vendor_access_issues()
    if vendor_access_issues:
        # Vendor paths may include optional feature packages; do not hard-fail doctor
        # unless mandatory runtime deps are already missing.
        print(f"- vendor-access: WARN ({', '.join(vendor_access_issues)})")
        if missing_dependencies:
            exit_code = 1
    else:
        print("- vendor-access: OK")

    if args.include_validate:
        doctor_heavy = bool(getattr(args, "heavy", False))
        max_seconds = getattr(args, "max_seconds", None)
        if doctor_heavy:
            print("- validators: running (heavy)")
            validate_code = cmd_validate(
                argparse.Namespace(
                    react=True,
                    react_fixtures=True,
                    react_edge_cases=True,
                    performance_budget=True,
                    watchdog_stress=True,
                    distribution_hardening=True,
                    universal_proof=False,
                    mcp_agent_surface=True,
                    stale_remediation=True,
                    deadcode_groundtruth=True,
                    auto_remediate_stale=True,
                    refresh=True,
                )
            )
        else:
            profile = doctor_validator_profile(system_scope)
            print(
                "- validators: running (quick snapshot; "
                f"scope={system_scope}; profile={profile.get('id')})"
            )
            print(f"- validator-claim-boundary: {profile.get('claim_boundary')}")
            validate_code = _run_doctor_quick_validators(
                system_scope,
                max_seconds=max_seconds,
            )
        if validate_code != 0:
            exit_code = validate_code
            print("- validators: FAILED")
        else:
            print("- validators: PASS")
    print(f"- overall: {'OK' if exit_code == 0 else 'ATTENTION NEEDED'}")
    return exit_code


def cmd_mcp(args):
    try:
        runtime = build_mcp_runtime_contract(
            source_env=os.environ,
            code_maps_dir=CODE_MAPS_DIR,
            vendor_paths=VENDOR_PATHS,
            mcp_script=MCP_SCRIPT,
            requested_profile=args.profile,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 2
    env = runtime["process_env"]

    if args.print_config:
        print(json.dumps(runtime["client_config"], indent=2, ensure_ascii=False))
        return 0
    if not import_target_available("mcp.server.fastmcp", "FastMCP"):
        blocked = _permission_blocked_locations("mcp")
        if blocked:
            print("[ERROR] Nexora SAGE MCP runtime is blocked by filesystem permissions at: " + ", ".join(blocked))
        else:
            print(
                "[ERROR] Nexora SAGE MCP runtime is unavailable. Install optional dependencies via "
                "`pip install -r requirements.txt` and ensure mcp.server.fastmcp is importable."
            )
        return 1

    return run_command(["python", str(MCP_SCRIPT)], env=env, timeout=None)


def cmd_purge(args):
    cmd = ["python", str(PURGE_SCRIPT), "--mode", args.mode]
    if args.confirm:
        cmd.append("--confirm")
    return run_command(cmd)


def cmd_external_targets(args):
    return run_command(["python", str(EXTERNAL_TARGET_INDEX)])


def _emit_target_proof_terminal(**fields) -> None:
    print(json.dumps({"operation": "target_repository_proof", **fields}, ensure_ascii=False))


def _optional_file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def cmd_target_proof(args):
    cli_contract = target_repository_proof_cli_contract()
    if args.refresh_policy not in cli_contract["refresh_policies"]:
        _emit_target_proof_terminal(
            status="BLOCKED",
            reason="invalid_refresh_policy",
            refresh_policy=str(args.refresh_policy),
        )
        return 2
    target_env = _target_root_env(args)
    if target_env is False or target_env is None:
        _emit_target_proof_terminal(
            status="BLOCKED",
            reason="invalid_or_missing_target_root",
            refresh_policy=str(args.refresh_policy),
        )
        return 2

    from tools.core.external_target_generation import (
        external_target_output_slug,
        resolve_current_external_target_generation,
    )

    target_path = Path(str(target_env["CODEMAPS_TARGET_ROOT"])).resolve()
    target_dir = (
        CODE_MAPS_DIR
        / "output"
        / "external_targets"
        / external_target_output_slug(str(target_path))
    )
    current_dir, pointer, current_reason = resolve_current_external_target_generation(
        target_dir
    )
    refresh_performed = False
    should_refresh = args.refresh_policy == "always" or (
        args.refresh_policy == "if-missing" and current_dir is None
    )
    if should_refresh:
        refresh_performed = True
        refresh_code = cmd_run(
            argparse.Namespace(
                list_steps=False,
                target_root=str(target_path),
                projects=args.projects,
                full=False,
                force=False,
                refresh=True,
                profile=str(cli_contract["refresh_execution_profile"]),
                step=None,
                from_step=None,
                scope=None,
                ai_context=False,
                skip_audit=False,
                skip_target_preflight=False,
            )
        )
        if refresh_code != 0:
            _emit_target_proof_terminal(
                status="BLOCKED",
                reason="target_refresh_failed",
                refresh_policy=str(args.refresh_policy),
                refresh_performed=True,
                refresh_exit_code=int(refresh_code),
                target_root=str(target_path),
            )
            return refresh_code
        current_dir, pointer, current_reason = resolve_current_external_target_generation(
            target_dir
        )

    if current_dir is None:
        _emit_target_proof_terminal(
            status="BLOCKED",
            reason="validated_current_generation_unavailable",
            current_reason=current_reason,
            refresh_policy=str(args.refresh_policy),
            refresh_performed=refresh_performed,
            target_root=str(target_path),
        )
        return 2

    run_id = str(pointer.get("run_id") or "")
    target_env["CODEMAPS_EXTERNAL_RUN_ID"] = run_id
    command = [
        "python",
        str(TARGET_REPOSITORY_PROOF_GENERATOR),
        "--target-root",
        str(target_path),
        "--mode",
        str(args.mode),
    ]
    if args.projects:
        command.extend(["--projects", str(args.projects)])
    proof_path = current_dir / ".raw" / "target_repository_proof_bundle.json"
    proof_identity_before = _optional_file_sha256(proof_path)
    proof_code = run_command(command, env=target_env)
    proof_identity_after = _optional_file_sha256(proof_path)
    proof_refreshed = (
        proof_identity_after is not None
        and proof_identity_after != proof_identity_before
    )
    proof_payload = load_json_file(proof_path, {})
    proof_summary = (
        proof_payload.get("summary", {}) if isinstance(proof_payload, dict) else {}
    )
    proof_verdict = (
        str(proof_summary.get("verdict") or "")
        if isinstance(proof_summary, dict)
        else ""
    )
    summary_valid = (
        proof_refreshed
        and proof_verdict in cli_contract["allowed_verdicts"]
    )
    exit_status_consistent = (
        summary_valid
        and (proof_code == 0)
        == (proof_verdict in cli_contract["successful_verdicts"])
    )
    if not proof_refreshed:
        terminal_status = "BLOCKED"
        terminal_reason = "proof_artifact_not_refreshed"
        final_code = 3
    elif not summary_valid:
        terminal_status = "BLOCKED"
        terminal_reason = "proof_summary_unavailable_or_invalid"
        final_code = 3
    elif not exit_status_consistent:
        terminal_status = "BLOCKED"
        terminal_reason = "proof_exit_status_mismatch"
        final_code = 3
    else:
        terminal_status = proof_verdict
        terminal_reason = "proof_builder_completed"
        final_code = proof_code
    _emit_target_proof_terminal(
        status=terminal_status,
        reason=terminal_reason,
        target_root=str(target_path),
        requested_projects=str(args.projects or ""),
        mode=str(args.mode),
        refresh_policy=str(args.refresh_policy),
        refresh_performed=refresh_performed,
        generation_run_id=run_id,
        proof_artifact=str(proof_path),
        proof_exit_code=int(proof_code),
        proof_artifact_refreshed=proof_refreshed,
        claim_boundary="This is bounded target-repository evidence, not SAGE release authority or mutation permission.",
    )
    return final_code


def cmd_inspect(args):
    cmd = ["python", str(INSPECT_TARGET)]
    if args.file:
        cmd.extend(["--file", args.file])
    elif args.folder:
        cmd.extend(["--folder", args.folder])
    elif args.symbol:
        cmd.extend(["--symbol", args.symbol])
    return run_command(cmd)


def cmd_brief(args):
    return run_command(["python", str(NEXORA_BRIEF_GENERATOR)])


def cmd_agent_contract(args):
    return run_command(["python", str(NEXORA_AGENT_CONTRACT_GENERATOR)])


def cmd_operator_packet(args):
    return run_command(["python", str(NEXORA_OPERATOR_PACKET_GENERATOR)])


def cmd_approval_ledger(args):
    cmd = ["python", str(HITL_APPROVAL_LEDGER), args.ledger_command]
    if args.ledger_command == "record":
        cmd.extend(["--gate", args.gate, "--decision", args.decision, "--scope", args.scope])
        if args.actor:
            cmd.extend(["--actor", args.actor])
        if args.rationale:
            cmd.extend(["--rationale", args.rationale])
        for evidence in args.evidence or []:
            cmd.extend(["--evidence", evidence])
        if args.expires_at:
            cmd.extend(["--expires-at", args.expires_at])
        if args.request_id:
            cmd.extend(["--request-id", args.request_id])
    return run_command(cmd)


def cmd_decision_request(args):
    cmd = ["python", str(HITL_DECISION_REQUESTS), args.request_command]
    if args.request_command == "create":
        cmd.extend(
            [
                "--gate",
                args.gate,
                "--scope",
                args.scope,
                "--proposed-action",
                args.proposed_action,
                "--risk",
                args.risk,
            ]
        )
        if args.rationale:
            cmd.extend(["--rationale", args.rationale])
        for evidence in args.evidence or []:
            cmd.extend(["--evidence", evidence])
        if args.confidence:
            cmd.extend(["--confidence", args.confidence])
        if args.requested_by:
            cmd.extend(["--requested-by", args.requested_by])
    elif args.request_command == "status":
        cmd.extend(["--request-id", args.request_id, "--status", args.status])
        if args.reason:
            cmd.extend(["--reason", args.reason])
        if args.linked_ledger_entry:
            cmd.extend(["--linked-ledger-entry", args.linked_ledger_entry])
    return run_command(cmd)


def cmd_mcp_surface(args):
    return run_command(["python", str(MCP_AGENT_SURFACE_VALIDATOR)])


def cmd_hitl_governance(args):
    return run_command(["python", str(HITL_GOVERNANCE_VALIDATOR)])


def cmd_hitl_lifecycle(args):
    return run_command(["python", str(HITL_LIFECYCLE_SMOKE_VALIDATOR)])


def cmd_agent_response(args):
    cmd = ["python", str(NEXORA_AGENT_RESPONSE_VALIDATOR)]
    if args.template_smoke:
        cmd.append("--template-smoke")
    else:
        cmd.extend(["--file", args.file])
    return run_command(cmd)


def cmd_response_ledger(args):
    cmd = ["python", str(NEXORA_AGENT_RESPONSE_LEDGER), args.response_ledger_command]
    if args.response_ledger_command == "record":
        cmd.extend(["--file", args.file])
        if args.task_id:
            cmd.extend(["--task-id", args.task_id])
        if args.actor:
            cmd.extend(["--actor", args.actor])
    return run_command(cmd)


def cmd_agent_handoff(args):
    return run_command(["python", str(NEXORA_AGENT_HANDOFF_GENERATOR)])


def cmd_freshness(args):
    return run_command(["python", str(REPORT_FRESHNESS_GENERATOR)])


def cmd_surface_inventory(args):
    return run_command(["python", str(NEXORA_SURFACE_INVENTORY_GENERATOR)])


def cmd_interactive(args):
    return run_command(["python", str(INTERACTIVE_DASHBOARD)])


def cmd_setup(args):
    # Setup is interactive, we run it directly with current sys.stdin
    try:
        import os
        safe_env = _safe_command_env()
        result = subprocess.run([sys.executable, str(SETUP_WIZARD)], cwd=str(CODE_MAPS_DIR), check=False, env=safe_env, timeout=setup_interactive_timeout_seconds())
        return result.returncode
    except Exception as e:
        print(f"Error launching setup wizard: {e}")
        return 1


def cmd_mapping(args):
    try:
        import os
        safe_env = _safe_command_env()
        result = subprocess.run([sys.executable, str(STUDIO_MAPPER)], cwd=str(CODE_MAPS_DIR), check=False, env=safe_env, timeout=setup_interactive_timeout_seconds())
        return result.returncode
    except Exception as e:
        print(f"Error launching studio mapper: {e}")
        return 1


def cmd_diagnose(args):
    """Human-readable diagnostic tool using the Reasoning Trace logic."""
    audit_path = RAW_DIR / "audit_report.json"
    doctrine_path = DOCTRINE_FILE

    audit = load_json_file(audit_path, {})
    if not isinstance(audit, dict) or not audit:
        print("Audit report not found. Run 'python sage.py refresh' first.")
        return 1

    with open(doctrine_path, "r", encoding="utf-8") as f:
        doctrine = json.load(f)
        
    labels = doctrine.get("violation_labels", {})
    remediation_policy = doctrine.get("audit_remediation_policy", {}).get("waves", {})
    
    target = args.file.lower().replace("\\", "/")
    violations = audit.get("violations", [])
    matches = [v for v in violations if target in v.get("file", "").lower().replace("\\", "/")]
    
    if not matches:
        print(f"No violations found for: {args.file}")
        return 0
        
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    console = Console()
    
    table = Table(title=f"Diagnostics: {args.file}", box=box.HEAVY)
    table.add_column("Rule", style="red")
    table.add_column("Description", style="white")
    table.add_column("Remediation", style="green")
    
    for v in matches:
        rule_id = v.get("rule", "")
        label = labels.get(rule_id, rule_id)
        policy = remediation_policy.get(rule_id, {})
        action = policy.get("action", "See doctrine for details.")
        table.add_row(rule_id, label, action)
        
    console.print(table)
    return 0


def cmd_release_check(args):
    phase = str(getattr(args, "phase", "all") or "all").lower()
    if phase == "v1":
        print("[RELEASE-CHECK] Running Nexora SAGE v1 focused release gate...")
        commands = [
            compileall_command(),
            ["python", "-m", "tools.engines.adapter_registry_report"],
            ["python", str(FRAMEWORK_CAPABILITY_VALIDATOR)],
            ["python", str(SOURCE_CONTRACT_VALIDATOR)],
            ["python", str(DISTRIBUTION_HARDENING_VALIDATOR)],
            ["python", str(REACT_SUPPORT_VALIDATOR)],
            ["python", str(REACT_FIXTURE_VALIDATOR)],
            ["python", str(REACT_EDGE_CASE_VALIDATOR)],
            ["python", str(ZUSTAND_SELECTOR_VALIDATOR)],
            ["python", str(REACT_TRANSITIVE_PROPAGATION_VALIDATOR)],
            ["python", str(REACT_V11_CONTRACT_VALIDATOR)],
            ["python", str(REACT_TAXONOMY_REPORT_GENERATOR)],
            ["python", str(REACT_UNIVERSAL_READINESS_VALIDATOR)],
            ["python", str(EXTERNAL_REACT_SMOKE_VALIDATOR)],
            ["python", "-m", "tools.engines.react_immutability_purity_cage"],
            ["python", str(CODE_MAPS_DIR / "tools" / "validate_react_immutability_purity_cage.py")],
            ["python", str(CODE_MAPS_DIR / "tools" / "validate_cage_mvp_contracts.py")],
            ["python", str(CODE_MAPS_DIR / "tools" / "validate_principle_packs.py")],
            ["python", str(CODE_MAPS_DIR / "tools" / "validate_capability_registry.py")],
            ["python", str(CODE_MAPS_DIR / "tools" / "validate_project_dna_profile.py")],
            ["python", str(CODE_MAPS_DIR / "tools" / "validate_capability_activation_plan.py")],
            ["python", str(MCP_AGENT_SURFACE_VALIDATOR)],
            ["python", str(NEXORA_AGENT_CONTRACT_GENERATOR)],
            ["python", str(HITL_APPROVAL_LEDGER), "init"],
            ["python", str(HITL_DECISION_REQUESTS), "init"],
            ["python", str(NEXORA_OPERATOR_PACKET_GENERATOR)],
            ["python", str(HITL_GOVERNANCE_VALIDATOR)],
            ["python", str(CONTEXTOS_SIGNAL_VALIDATOR)],
            ["python", str(CONTEXTOS_CONTRACT_VALIDATOR)],
            ["python", str(TEST_GAP_REPORT_GENERATOR)],
            ["python", str(AI_AGENT_READINESS_REPORT_GENERATOR)],
            ["python", str(CODE_MAPS_DIR / "tools" / "generate_react_corpus_saturation_report.py")],
            ["python", str(PRODUCT_REPORTS_VALIDATOR)],
            ["python", str(CODE_MAPS_DIR / "tools" / "validate_react_corpus_semantic_audit.py")],
            ["python", str(REGISTRY_FOUNDATION_VALIDATOR)],
            ["python", str(ARCHITECTURE_ORACLE_VALIDATOR)],
            ["python", str(MERGE_INTELLIGENCE_REGRESSION_VALIDATOR)],
            ["python", str(SQLITE_ARTIFACT_PARITY_VALIDATOR)],
            ["python", str(SQLITE_PROXY_COVERAGE_VALIDATOR)],
            ["python", str(UNIVERSAL_PROOF_VALIDATOR)],
            ["python", str(MCP_AGENT_SURFACE_VALIDATOR)],
            ["python", "-m", "tools.engines.release_readiness_report"],
            ["python", str(CLAIM_GUARD_VALIDATOR)],
            ["python", "-m", "tools.engines.artifact_contract_validator"],
        ]
        for command in commands:
            code = run_command(command)
            if code != 0:
                print(f"[RELEASE-CHECK] v1 gate failed: {' '.join(command)}")
                return code
        print("[RELEASE-CHECK] v1 focused release gate: PASS")
        return 0

    print("[RELEASE-CHECK] Refreshing release readiness dependency closure once...")
    if not getattr(args, "skip_refresh", False):
        refresh_code = run_command(["python", str(PIPELINE_SCRIPT), "--step", "Release Readiness"], timeout=cli_pipeline_refresh_timeout_seconds())
        if refresh_code != 0:
            print("[RELEASE-CHECK] Release readiness refresh failed.")
            return refresh_code
        freshness_code = _refresh_stale_validation_artifacts()
        if freshness_code != 0:
            return freshness_code
    else:
        print("[RELEASE-CHECK] Snapshot-only mode; skipping pipeline refresh.")
        freshness_code = _refresh_stale_validation_artifacts()
        if freshness_code != 0:
            return freshness_code

    print("[RELEASE-CHECK] Running release gate validator chain...")
    validate_args = argparse.Namespace(
        react=True,
        react_fixtures=True,
        react_edge_cases=True,
        performance_budget=True,
        watchdog_stress=True,
        distribution_hardening=True,
        universal_proof=True,
        mcp_agent_surface=True,
        stale_remediation=True,
        signal_regression=True,
        update_signal_baseline=False,
        deadcode_groundtruth=True,
        auto_remediate_stale=True,
        refresh=False,
    )
    validate_code = cmd_validate(validate_args)
    if validate_code != 0:
        return validate_code
    if phase == "validators":
        print("[RELEASE-CHECK] validator phase: PASS")
        return 0

    print("[RELEASE-CHECK] Updating performance ledger...")
    ledger_code = run_command(["python", str(PERFORMANCE_LEDGER_UPDATER), "--notes", "release-check"])
    if ledger_code != 0:
        print("[RELEASE-CHECK] Performance ledger update failed.")
        return ledger_code

    print("[RELEASE-CHECK] Rebuilding release evidence envelope...")
    evidence_commands = [
        ["python", "-m", "tools.engines.adapter_registry_report"],
        ["python", str(MCP_AGENT_SURFACE_VALIDATOR)],
        ["python", "-m", "tools.engines.release_readiness_report"],
        ["python", str(NEXORA_BRIEF_GENERATOR)],
        ["python", str(NEXORA_AGENT_CONTRACT_GENERATOR)],
        ["python", str(FRAMEWORK_CAPABILITY_VALIDATOR)],
        ["python", str(POLYGLOT_CAPABILITY_VALIDATOR)],
        ["python", str(LANGUAGE_AGNOSTIC_SYMBOL_VALIDATOR)],
        ["python", str(TEST_IMPACT_PROFILE_VALIDATOR)],
        ["python", str(REGISTRY_FOUNDATION_VALIDATOR)],
        ["python", str(CONTEXTOS_SIGNAL_VALIDATOR)],
        ["python", str(CONTEXTOS_CONTRACT_VALIDATOR)],
        ["python", str(TEST_GAP_REPORT_GENERATOR)],
        ["python", str(AI_AGENT_READINESS_REPORT_GENERATOR)],
        ["python", str(PRODUCT_REPORTS_VALIDATOR)],
        ["python", str(EXTERNAL_REACT_SMOKE_VALIDATOR)],
        ["python", str(EXTERNAL_POLYGLOT_SMOKE_VALIDATOR)],
        ["python", "-m", "tools.engines.master_report_generator"],
        ["python", "-m", "tools.engines.ai_context_generator"],
        ["python", "-m", "tools.engines.artifact_contract_validator"],
    ]
    for command in evidence_commands:
        code = run_command(command)
        if code != 0:
            print(f"[RELEASE-CHECK] Evidence rebuild failed: {' '.join(command[2:])}")
            return code

    readiness_path = RAW_DIR / "release_readiness.json"
    try:
        readiness = load_json_file(readiness_path, {})
    except Exception as exc:
        print(f"[RELEASE-CHECK] Could not read release readiness evidence: {exc}")
        return 1
    if readiness.get("readiness") != "PRODUCTION_READY":
        print(f"[RELEASE-CHECK] release gate: FAIL ({readiness.get('readiness')})")
        return 1

    print("[RELEASE-CHECK] release gate: PASS")
    return 0


def cmd_self_audit(args):
    command = ["python", "-B", str(SAGE_SELF_AUDIT_RUNNER)]
    if getattr(args, "profile", None):
        command.extend(["--profile", str(args.profile)])
    return run_command(command, timeout=cli_pipeline_refresh_timeout_seconds())


def cmd_work_package(args):
    from tools.core.artifact_store import get_adaptive_timeout
    from tools.core.config import REPORTS_DIR, save_json_atomic, save_text_atomic
    from tools.core.sage_active_work_package import active_work_package
    from tools.core.work_package_receipts import (
        build_work_package_closeout_proposal,
        propose_work_package_evidence,
        render_work_package_closeout_proposal,
    )
    from tools.generate_semantic_diff_lesson_projection import changed_files_from_git
    from tools.validate_sage_work_package_closure import run as validate_package_closure

    receipt_projection = propose_work_package_evidence()
    if args.work_package_action == "preflight":
        payload = {
            "meta": {"kind": "sage_work_package_mutation_preflight", "version": "v1"},
            "package": receipt_projection.get("package"),
            "mutation_preflight": receipt_projection.get("mutation_preflight"),
            "authority": receipt_projection.get("authority"),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload.get("mutation_preflight", {}).get("ready") is True else 1

    loop = load_json_file(CONFIG_DIR / "sage_development_loop_contract.json", {})
    projection_contract = (
        loop.get("semantic_diff_review", {})
        .get("lesson_application_levels", {})
        .get("impacted", {})
        .get("projection_contract", {})
    )
    baseline_timeout = int(projection_contract.get("git_discovery_timeout_seconds") or 0)
    if baseline_timeout < 1:
        raise RuntimeError("missing_git_discovery_timeout_seconds")
    changed_files = changed_files_from_git(timeout_seconds=get_adaptive_timeout(baseline_timeout))
    payload = build_work_package_closeout_proposal(
        package=active_work_package(),
        receipt_projection=receipt_projection,
        closure_validation=validate_package_closure(),
        changed_files=changed_files,
    )
    save_json_atomic(RAW_DIR / "sage_work_package_closeout_proposal.json", payload)
    save_text_atomic(
        REPORTS_DIR / "sage_work_package_closeout_proposal.md",
        render_work_package_closeout_proposal(payload),
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("status") == "EVIDENCE_READY_HUMAN_ACTION_REQUIRED" else 1


def cmd_ledger_update(args):
    cmd = ["python", str(PERFORMANCE_LEDGER_UPDATER), "--notes", args.notes]
    if args.repo_band:
        cmd.extend(["--repo-band", args.repo_band])
    if args.run_id:
        cmd.extend(["--run-id", args.run_id])
    return run_command(cmd)


def cmd_fixture_import(args):
    cmd = [
        "python",
        str(REACT_FIXTURE_IMPORTER),
        "--fixture-id",
        args.fixture_id,
        "--source-root",
        args.source_root,
    ]
    if args.set_enabled:
        cmd.append("--set-enabled")
    if args.set_required:
        cmd.append("--set-required")
    if args.create_if_missing:
        cmd.append("--create-if-missing")
    if args.dry_run:
        cmd.append("--dry-run")
    return run_command(cmd)


def cmd_fixture_seed(args):
    cmd = [
        "python",
        str(REACT_FIXTURE_SEED_GENERATOR),
        "--project",
        args.project,
        "--dest-root",
        args.dest_root,
    ]
    return run_command(cmd)


def cmd_fixture_promote(args):
    seed_cmd = [
        "python",
        str(REACT_FIXTURE_SEED_GENERATOR),
        "--project",
        args.project,
        "--dest-root",
        args.source_root,
    ]
    seed_code = run_command(seed_cmd)
    if seed_code != 0:
        return seed_code

    import_cmd = [
        "python",
        str(REACT_FIXTURE_IMPORTER),
        "--fixture-id",
        args.fixture_id,
        "--source-root",
        args.source_root,
        "--set-enabled",
        "--set-required",
    ]
    if args.create_if_missing:
        import_cmd.append("--create-if-missing")
    import_code = run_command(import_cmd)
    if import_code != 0:
        return import_code

    validate_args = argparse.Namespace(
        react=True,
        react_fixtures=True,
        react_edge_cases=True,
        performance_budget=False,
        watchdog_stress=False,
        distribution_hardening=False,
        universal_proof=bool(args.with_universal_proof),
        mcp_agent_surface=False,
        stale_remediation=True,
        signal_regression=False,
        update_signal_baseline=False,
        deadcode_groundtruth=False,
        auto_remediate_stale=True,
    )
    return cmd_validate(validate_args)


def cmd_universal_proof_run(args):
    import_cmd = [
        "python",
        str(REACT_FIXTURE_IMPORTER),
        "--fixture-id",
        args.fixture_id,
        "--source-root",
        args.source_root,
        "--set-enabled",
        "--set-required",
    ]
    if args.create_if_missing:
        import_cmd.append("--create-if-missing")
    import_code = run_command(import_cmd)
    if import_code != 0:
        return import_code

    validate_args = argparse.Namespace(
        react=True,
        react_fixtures=True,
        react_edge_cases=True,
        performance_budget=False,
        watchdog_stress=False,
        distribution_hardening=False,
        universal_proof=True,
        mcp_agent_surface=False,
        stale_remediation=True,
        deadcode_groundtruth=False,
        auto_remediate_stale=True,
    )
    return cmd_validate(validate_args)


def cmd_phase_status(args):
    return run_command(["python", str(PHASE_STATUS_GENERATOR)])


def cmd_transcript_template(args):
    cmd = [
        "python",
        str(TRANSCRIPT_GENERATOR),
        "--operator",
        args.operator,
        "--target-root",
        args.target_root,
        "--machine-baseline",
        args.machine_baseline,
    ]
    if args.force:
        cmd.append("--force")
    return run_command(cmd)


def cmd_truth_sync(args):
    cmd = ["python", str(PROJECT_TRUTH_SYNC)]
    if args.no_create:
        cmd.append("--no-create")
    return run_command(cmd)


def cmd_ci_check(args):
    cmd = ["python", str(CI_RELEASE_CHECK)]
    if args.skip_release_check:
        cmd.append("--skip-release-check")
    return run_command(cmd)


def cmd_demo(args):
    return run_command(["python", str(NEXORA_DEMO_GENERATOR)])


def cmd_install_proof(args):
    cmd = ["python", str(INSTALLATION_PROOF_GENERATOR), "--level", args.level]
    if args.skip_deps:
        cmd.append("--skip-deps")
    if args.max_doctor_seconds:
        cmd.extend(["--max-doctor-seconds", str(args.max_doctor_seconds)])
    if args.target_root:
        cmd.extend(["--target-root", str(args.target_root)])
    if args.projects:
        cmd.extend(["--projects", str(args.projects)])
    return run_command(cmd, timeout=install_proof_timeout_seconds(args.level))


def cmd_dashboard(args):
    return run_command(["python", str(NEXORA_DASHBOARD_GENERATOR)])


def cmd_storage_maintenance(args):
    from tools.core.artifact_store import STORE
    from tools.core.pipeline_registry import pipeline_lock_policy
    from tools.core.sqlite_storage_maintenance import (
        maintenance_succeeded,
        run_sqlite_storage_maintenance,
    )

    if not STORE.use_sqlite:
        print(json.dumps({"status": "REFUSED_SQLITE_DISABLED", "applied": False}))
        return 1

    lock_endpoint = str(pipeline_lock_policy().get("endpoint") or ".pipeline_run.lock")

    def emit_progress(phase, details):
        print(
            "[SQLITE_MAINTENANCE] "
            + json.dumps({"phase": phase, **details}, ensure_ascii=False, sort_keys=True),
            flush=True,
        )

    result = run_sqlite_storage_maintenance(
        STORE.db_manager.db_path,
        lock_path=CODE_MAPS_DIR / lock_endpoint,
        apply=bool(args.apply),
        confirmed=bool(args.confirm),
        max_pages=(
            int(args.max_pages)
            if args.max_pages is not None
            else sqlite_maintenance_incremental_page_limit()
        ),
        full_vacuum_free_space_percent=sqlite_maintenance_full_vacuum_free_space_percent(),
        timeout_seconds=sqlite_write_timeout_seconds(),
        progress_interval_seconds=pipeline_step_heartbeat_seconds(),
        progress=emit_progress,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if maintenance_succeeded(result) else 1


def cmd_backup(args):
    try:
        from pathlib import Path

        from tools.core.artifact_store import STORE

        dest = Path(args.dest) if args.dest else None
        result_path = STORE.backup(dest)
        print(f"[BACKUP SUCCESS] SQLite state payloads backed up to: {result_path}")
        return 0
    except Exception as exc:
        print(f"[BACKUP ERROR] Failed to perform backup: {exc}")
        return 1


def cmd_restore(args):
    try:
        from pathlib import Path

        from tools.core.artifact_store import STORE

        source = Path(args.source) if args.source else None
        success = STORE.restore(source)
        if success:
            print("[RESTORE SUCCESS] SQLite state payloads restored and atlas relational index repopulated.")
            return 0
        print("[RESTORE ERROR] Restore operation did not complete successfully.")
        return 1
    except Exception as exc:
        print(f"[RESTORE ERROR] Failed to perform restore: {exc}")
        return 1


def build_parser():
    from tools.core.pipeline_registry import load_pipeline_execution_policy

    target_proof_cli = target_repository_proof_cli_contract()
    execution_profile_choices = sorted(
        load_pipeline_execution_policy().get("execution_profiles", {})
    )
    if is_public_distribution():
        common_flows = [
            "`python sage.py init --target-root <repository>`",
            "`python sage.py run --target-root <repository> --profile daily`",
            "`python sage.py mcp --print-config`",
        ]
    else:
        common_flows = [
            "`python sage.py doctor`",
            "`python sage.py run --profile daily`",
            "`python sage.py run --full`",
        ]
        common_flows.append("`python sage.py release-check`")
    common_flows.append(
        "`python sage.py install-proof --level release --skip-deps "
        "--target-root <repository> --projects MAIN`"
    )
    common_flows.append(
        "`python sage.py target-proof --target-root <repository> "
        "--projects MAIN --mode baseline --refresh-policy current`"
    )
    parser = argparse.ArgumentParser(
        description="Nexora SAGE CLI (Sovereign Architectural Governance Engine).",
        epilog=(
            f"Common flows: {', '.join(common_flows)}. "
            "`codemaps.py` is internal implementation and should not be used as the public command."
        ),
    )
    parser.add_argument("--version", action="version", version=f"Nexora SAGE {_product_version()}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="First-time setup: preflight and workspace preparation.",
        description=(
            "Prepare the environment and refresh workspace truth without claiming analysis freshness. "
            "Run a named daily analysis after setup, or request the heavyweight release-deep bootstrap "
            "explicitly with --full."
        ),
    )
    init_parser.add_argument("--skip-deps", action="store_true", help="Skip dependency installation.")
    init_mode_group = init_parser.add_mutually_exclusive_group()
    init_mode_group.add_argument(
        "--setup-only",
        action="store_true",
        help="Explicit legacy spelling for the default setup-only behavior.",
    )
    init_mode_group.add_argument(
        "--full",
        action="store_true",
        help="Explicitly run the heavyweight release-deep forced analysis after setup.",
    )
    init_mode_group.add_argument(
        "--plan-only",
        action="store_true",
        help="Inspect machine and target requirements without dependency installation, discovery, or analysis.",
    )
    init_parser.add_argument(
        "--target-root",
        help="Repository root to initialize. Public product projections require this explicit boundary.",
    )
    init_parser.add_argument(
        "--projects",
        help="Optional comma-separated project filter for the adopted target workspace.",
    )
    init_parser.set_defaults(func=cmd_init)

    demo_parser = subparsers.add_parser(
        "demo",
        help="Generate a quick Nexora SAGE demo snapshot from current artifacts.",
        description="Writes output/demo/nexora_sage_demo.md and output/.raw/nexora_sage_demo.json without running the full pipeline.",
    )
    demo_parser.set_defaults(func=cmd_demo)

    install_proof_parser = subparsers.add_parser(
        "install-proof",
        help="Generate a machine-local installation proof report.",
        description=(
            "Runs a bounded onboarding proof and writes output/.raw/installation_proof.json "
            "plus output/reports/installation_proof.md. Use --level release for friend-machine proof."
        ),
    )
    install_proof_parser.add_argument("--level", choices=["smoke", "daily", "release"], default="smoke")
    install_proof_parser.add_argument("--skip-deps", action="store_true", help="Pass --skip-deps to init for daily/release proof levels.")
    install_proof_parser.add_argument("--max-doctor-seconds", type=int, default=90)
    install_proof_parser.add_argument("--target-root", help="Repository root for target-bound daily/release installation proof.")
    install_proof_parser.add_argument("--projects", help="Comma-separated project filter for the installation proof daily run.")
    install_proof_parser.set_defaults(func=cmd_install_proof)

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Generate a lightweight Nexora SAGE HTML dashboard.",
        description="Writes output/reports/nexora_sage_dashboard.html from current JSON artifacts.",
    )
    dashboard_parser.set_defaults(func=cmd_dashboard)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Refresh workspace truth.",
        description="Regenerate discovery, reseed workspace-scoped overrides, compile runtime config, and run preflight lifecycle validation.",
    )
    refresh_parser.set_defaults(func=cmd_refresh)

    run_parser = subparsers.add_parser(
        "run",
        help="Run the analysis pipeline.",
        description="Run the full pipeline or a dependency-aware targeted step such as quality gates or AI context generation.",
    )
    run_parser.add_argument("--full", action="store_true", help="Run the full pipeline.")
    run_parser.add_argument("--force", action="store_true", help="Force heavy steps to rebuild instead of lifting from cache.")
    run_parser.add_argument("--refresh", action="store_true", help="Refresh stale project truth while preserving normal profile, claim, and capability applicability boundaries.")
    run_parser.add_argument(
        "--profile",
        choices=execution_profile_choices,
        help="Execution profile id from config/pipeline_execution_policy.json; each profile preserves its declared scope and claim boundary.",
    )
    run_parser.add_argument("--step", help="Run a specific step with its required upstream dependencies.")
    run_parser.add_argument("--from-step", help="Run from a specific step onward.")
    run_parser.add_argument("--projects", help="Comma-separated project filter.")
    run_parser.add_argument(
        "--scope",
        help="Directory-only report filter for Scoped Host Analyzer; does not bound Atlas or pipeline execution.",
    )
    run_parser.add_argument("--target-root", help="Analyze another repository/folder for this run without rewriting Nexora SAGE runtime config.")
    run_parser.add_argument("--skip-target-preflight", action="store_true", help="Skip external target preflight when --target-root is used.")
    run_parser.add_argument("--ai-context", action="store_true", help="Generate AI context at the end of the run.")
    run_parser.add_argument("--skip-audit", action="store_true", help="Skip the audit step.")
    run_parser.add_argument("--list-steps", action="store_true", help="List runnable pipeline steps and exit.")
    run_parser.set_defaults(func=cmd_run)

    run_status_parser = subparsers.add_parser(
        "run-status",
        help="Query a durable pipeline run receipt without starting another run.",
        description="Poll the latest or named pipeline run through its SQLite-first receipt. A transport timeout never authorizes an automatic retry.",
    )
    run_status_parser.add_argument("--run-id", default="", help="Durable run id. Omit to query the latest run.")
    run_status_parser.add_argument("--target-root", help="Read receipts from an external target repository namespace.")
    run_status_parser.add_argument("--wait", action="store_true", help="Wait for this receipt to reach a terminal state without rerunning work.")
    run_status_parser.add_argument("--timeout-seconds", type=float, default=0, help="Bounded observation timeout; does not terminate the run.")
    run_status_parser.add_argument("--poll-seconds", type=float, default=5, help="Polling cadence while --wait is active.")
    run_status_parser.add_argument("--json", action="store_true", help="Print the structured receipt projection.")
    run_status_parser.set_defaults(func=cmd_run_status)

    watch_parser = subparsers.add_parser(
        "watch",
        help="Start live incremental analysis.",
        description="Run the watchdog service for long-lived, change-driven re-analysis during active development, or execute a single smoke pulse.",
    )
    watch_parser.add_argument(
        "--path",
        action="append",
        help="Exact source path; repeat with --once for one bounded change set, or pass one directory for smoke/live watch. Overrides the compiled default.",
    )
    watch_parser.add_argument("--target-root", help="Watch/analyze another repository/folder without rewriting Nexora SAGE runtime config.")
    watch_parser.add_argument("--debounce", type=float, help="Debounce seconds before a pulse is triggered.")
    watch_parser.add_argument("--once", action="store_true", help="Run one smoke incremental pulse and exit.")
    watch_parser.set_defaults(func=cmd_watch)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Run validator suites.",
        description="Run a contract-owned validation profile. The default refreshes runtime-backed evidence; snapshot and source-clean modes state narrower proof boundaries.",
    )
    for profile_option in validation_profile_options():
        validate_parser.add_argument(
            profile_option["public_flag"],
            dest=f"validate_profile_{profile_option['id']}",
            action="store_true",
            help=profile_option["help"],
        )
    for option in optional_validator_options():
        validate_parser.add_argument(
            str(option["public_flag"]),
            dest=f"validate_optional_{option['id']}",
            action="store_true",
            help=str(option["help"]),
        )
    remediation = remediation_options()
    validate_parser.add_argument(
        remediation["baseline_update_flag"],
        dest="validate_update_signal_baseline",
        action="store_true",
        help=remediation["baseline_update_help"],
    )
    validate_parser.add_argument(
        remediation["public_flag"],
        dest="validate_auto_remediate_stale",
        action="store_true",
        help=remediation["help"],
    )
    validate_parser.set_defaults(func=cmd_validate)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check workspace health.",
        description="Verify required config files exist and optionally run validator suites for a quick health assessment.",
    )
    doctor_parser.add_argument("--include-validate", action="store_true", help="Also run validators.")
    doctor_parser.add_argument("--quick", action="store_true", help="Use the quick snapshot validator set when --include-validate is enabled (default).")
    doctor_parser.add_argument("--heavy", action="store_true", help="Run the full heavyweight validator chain when --include-validate is enabled.")
    doctor_parser.add_argument("--max-seconds", type=int, default=90, help="Maximum seconds for quick doctor validators.")
    doctor_parser.add_argument("--skip-release-proof", action="store_true", help="Skip existing release proof bundle status when checking local installation health.")
    doctor_parser.add_argument("--repair-optional-deps", action="store_true", help="Attempt pip --user repair for blocked/missing optional feature dependencies.")
    doctor_parser.add_argument("--verbose-env", action="store_true", help="Print Python import path details for environment mismatch debugging.")
    doctor_parser.set_defaults(func=cmd_doctor)

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run MCP server or print MCP config.",
        description="Start the MCP server for AI tooling, or print a ready-to-copy MCP configuration block.",
    )
    mcp_parser.add_argument("--print-config", action="store_true", help="Print MCP configuration JSON.")
    mcp_parser.add_argument(
        "--profile",
        help="Machine-readable MCP tool visibility profile (default: target_repository_default).",
    )
    mcp_parser.set_defaults(func=cmd_mcp)

    show_parser = subparsers.add_parser(
        "show-config",
        help="Print compiled runtime config.",
        description="Print the compiled runtime truth from codemaps.config.json.",
    )
    show_parser.set_defaults(func=lambda args: print_json(CONFIG_FILE))

    purge_parser = subparsers.add_parser(
        "purge",
        help="Clean system outputs and caches.",
        description="Safe-guarded system cleanup. Use --mode deep to force re-discovery.",
    )
    purge_parser.add_argument("--mode", choices=["standard", "deep", "total", "external-targets"], default="standard", help="Cleanup depth.")
    purge_parser.add_argument("--confirm", action="store_true", help="Auto-confirm total purge.")
    purge_parser.set_defaults(func=cmd_purge)

    external_targets_parser = subparsers.add_parser(
        "external-targets",
        help="Index external target analysis runs.",
        description="Generate output/external_targets/index.json and output/reports/external_target_runs.md.",
    )
    external_targets_parser.set_defaults(func=cmd_external_targets)

    target_proof_parser = subparsers.add_parser(
        "target-proof",
        help="Build bounded proof for one analyzed target repository.",
        description="Reuse or explicitly refresh one validated external-target generation, then delegate proof construction to the canonical target repository proof builder.",
    )
    target_proof_parser.add_argument("--target-root", required=True, help="Analyzed repository root.")
    target_proof_parser.add_argument("--projects", help="Comma-separated project scope; when present it must match the analyzed scope authority.")
    target_proof_parser.add_argument(
        "--mode",
        choices=list(target_proof_cli["modes"]),
        default=str(target_proof_cli["default_mode"]),
    )
    target_proof_parser.add_argument(
        "--refresh-policy",
        choices=list(target_proof_cli["refresh_policies"]),
        default=str(target_proof_cli["default_refresh_policy"]),
        help="Use validated current evidence, refresh only when missing, or always run one full refresh before proof.",
    )
    target_proof_parser.set_defaults(func=cmd_target_proof)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inspect a file, folder, or symbol from existing artifacts.",
        description="Reads current Nexora SAGE artifacts and writes a focused inspection report without running a full pipeline.",
    )
    inspect_group = inspect_parser.add_mutually_exclusive_group(required=True)
    inspect_group.add_argument("--file", help="Repository-relative file path or file fragment.")
    inspect_group.add_argument("--folder", help="Repository-relative folder path or folder fragment.")
    inspect_group.add_argument("--symbol", help="Symbol/component/function name.")
    inspect_parser.set_defaults(func=cmd_inspect)

    brief_parser = subparsers.add_parser(
        "brief",
        help="Generate the AI/human surface brief.",
        description="Writes output/.raw/nexora_brief.json for AI tools and output/reports/nexora_brief.md as the human first-read report.",
    )
    brief_parser.set_defaults(func=cmd_brief)

    agent_contract_parser = subparsers.add_parser(
        "agent-contract",
        help="Generate the AI worker / Human-in-the-Loop contract.",
        description="Writes output/.raw/nexora_agent_contract.json and output/reports/nexora_agent_contract.md.",
    )
    agent_contract_parser.set_defaults(func=cmd_agent_contract)

    operator_packet_parser = subparsers.add_parser(
        "operator-packet",
        help="Generate the one-call AI operator handoff packet.",
        description="Writes output/.raw/nexora_operator_packet.json and output/reports/nexora_operator_packet.md.",
    )
    operator_packet_parser.set_defaults(func=cmd_operator_packet)

    approval_parser = subparsers.add_parser(
        "approval-ledger",
        help="Manage the Human-in-the-Loop approval ledger.",
        description="Records or renders human approval decisions for risky AI actions.",
    )
    approval_subparsers = approval_parser.add_subparsers(dest="ledger_command", required=True)
    approval_init = approval_subparsers.add_parser("init", help="Create or refresh the approval ledger report.")
    approval_init.set_defaults(func=cmd_approval_ledger)
    approval_verify = approval_subparsers.add_parser("verify", help="Verify HITL ledger HMAC signatures and hash chain.")
    approval_verify.set_defaults(func=cmd_approval_ledger)
    approval_record = approval_subparsers.add_parser("record", help="Record a human approval decision.")
    approval_record.add_argument("--gate", required=True)
    approval_record.add_argument("--decision", required=True, choices=["approved", "rejected", "deferred", "revoked"])
    approval_record.add_argument("--scope", required=True)
    approval_record.add_argument("--actor", default="human")
    approval_record.add_argument("--rationale", default="")
    approval_record.add_argument("--evidence", action="append", default=[])
    approval_record.add_argument("--expires-at", dest="expires_at", default="")
    approval_record.add_argument("--request-id", dest="request_id", default="")
    approval_record.set_defaults(func=cmd_approval_ledger)

    request_parser = subparsers.add_parser(
        "decision-request",
        help="Manage AI-created HITL decision requests.",
        description="Creates or renders structured requests for human approval before risky AI actions.",
    )
    request_subparsers = request_parser.add_subparsers(dest="request_command", required=True)
    request_init = request_subparsers.add_parser("init", help="Create or refresh the decision request report.")
    request_init.set_defaults(func=cmd_decision_request)
    request_create = request_subparsers.add_parser("create", help="Create a decision request for human review.")
    request_create.add_argument("--gate", required=True)
    request_create.add_argument("--scope", required=True)
    request_create.add_argument("--proposed-action", dest="proposed_action", required=True)
    request_create.add_argument("--risk", required=True, choices=["low", "medium", "high", "critical"])
    request_create.add_argument("--rationale", default="")
    request_create.add_argument("--evidence", action="append", default=[])
    request_create.add_argument("--confidence", default="medium")
    request_create.add_argument("--requested-by", dest="requested_by", default="ai_agent")
    request_create.set_defaults(func=cmd_decision_request)
    request_status = request_subparsers.add_parser("status", help="Update a decision request status.")
    request_status.add_argument("--request-id", dest="request_id", required=True)
    request_status.add_argument("--status", required=True, choices=["open", "closed", "superseded"])
    request_status.add_argument("--reason", default="")
    request_status.add_argument("--linked-ledger-entry", dest="linked_ledger_entry", default="")
    request_status.set_defaults(func=cmd_decision_request)

    mcp_surface_parser = subparsers.add_parser(
        "mcp-surface",
        help="Validate MCP parity for the AI worker / HITL surface.",
        description="Checks that MCP exposes the required agent-facing Nexora tools.",
    )
    mcp_surface_parser.set_defaults(func=cmd_mcp_surface)

    hitl_governance_parser = subparsers.add_parser(
        "hitl-governance",
        help="Validate HITL contract, approval gates, operator packet, and approval ledger.",
        description="Writes output/.raw/hitl_governance_validation.json and output/reports/hitl_governance_validation.md.",
    )
    hitl_governance_parser.set_defaults(func=cmd_hitl_governance)

    hitl_lifecycle_parser = subparsers.add_parser(
        "hitl-lifecycle",
        help="Run isolated HITL lifecycle smoke validation.",
        description="Validates request -> ledger decision -> request close -> response ledger flow in temp storage.",
    )
    hitl_lifecycle_parser.set_defaults(func=cmd_hitl_lifecycle)

    agent_response_parser = subparsers.add_parser(
        "agent-response",
        help="Validate Nexora AI agent response shape.",
        description="Checks verdict/evidence/confidence/risk/human-approval/source-artifact response contract.",
    )
    agent_response_group = agent_response_parser.add_mutually_exclusive_group(required=True)
    agent_response_group.add_argument("--template-smoke", action="store_true", help="Write and validate the canonical template.")
    agent_response_group.add_argument("--file", help="Validate an agent response JSON file.")
    agent_response_parser.set_defaults(func=cmd_agent_response)

    response_ledger_parser = subparsers.add_parser(
        "response-ledger",
        help="Manage the validated AI response ledger.",
        description="Records validated AI answers for later human audit.",
    )
    response_ledger_subparsers = response_ledger_parser.add_subparsers(dest="response_ledger_command", required=True)
    response_ledger_init = response_ledger_subparsers.add_parser("init", help="Create or refresh the response ledger.")
    response_ledger_init.set_defaults(func=cmd_response_ledger)
    response_ledger_record = response_ledger_subparsers.add_parser("record", help="Validate and record an agent response JSON file.")
    response_ledger_record.add_argument("--file", required=True)
    response_ledger_record.add_argument("--task-id", dest="task_id", default="")
    response_ledger_record.add_argument("--actor", default="ai_agent")
    response_ledger_record.set_defaults(func=cmd_response_ledger)

    agent_handoff_parser = subparsers.add_parser(
        "agent-handoff",
        help="Generate a pasteable AI agent handoff prompt.",
        description="Writes output/.raw/nexora_agent_handoff.json and output/reports/nexora_agent_handoff.md.",
    )
    agent_handoff_parser.set_defaults(func=cmd_agent_handoff)

    freshness_parser = subparsers.add_parser(
        "freshness",
        help="Generate the report/raw artifact freshness index.",
        description="Writes output/.raw/report_freshness_index.json and output/reports/report_freshness_index.md.",
    )
    freshness_parser.set_defaults(func=cmd_freshness)

    surface_inventory_parser = subparsers.add_parser(
        "surface-inventory",
        help="Generate the Nexora CLI/MCP/artifact capability inventory.",
        description="Writes output/.raw/nexora_surface_inventory.json and output/reports/nexora_surface_inventory.md.",
    )
    surface_inventory_parser.set_defaults(func=cmd_surface_inventory)

    interactive_parser = subparsers.add_parser(
        "interactive",
        help="Launch the interactive CLI dashboard.",
        description="Real-time terminal visualization of workspace stats, violations, and critical nodes.",
    )
    interactive_parser.set_defaults(func=cmd_interactive)

    setup_parser = subparsers.add_parser(
        "setup",
        help="Launch the legacy interactive SAGE development setup wizard.",
        description="Private development compatibility surface; public onboarding uses explicit-target init.",
    )
    setup_parser.set_defaults(func=cmd_setup)

    mapping_parser = subparsers.add_parser(
        "mapping",
        help="Customize legacy development doctrine mappings.",
        description="Private development compatibility surface for architecture_doctrine.json.",
    )
    mapping_parser.set_defaults(func=cmd_mapping)

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Diagnose architectural violations for a specific file.",
        description="Provides Reasoning Trace: Why the file is failing and how to fix it.",
    )
    diagnose_parser.add_argument("file", help="Path or partial path to the file to diagnose.")
    diagnose_parser.set_defaults(func=cmd_diagnose)

    maintainer_subparsers = subparsers

    release_parser = maintainer_subparsers.add_parser(
        "release-check",
        help="Run full release gate validation chain.",
        description="Runs validate with React support, fixture matrix, performance budget, watchdog stress, stale auto-remediation, then updates performance ledger.",
    )
    release_parser.add_argument(
        "--with-universal-proof",
        action="store_true",
        help="Compatibility flag; universal proof is included by default in the release gate.",
    )
    release_parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Validate the current release evidence snapshot without refreshing pipeline artifacts.",
    )
    release_parser.add_argument(
        "--phase",
        choices=["all", "validators", "v1"],
        default="all",
        help="Run the full release check, validator gate phase, or focused v1 gate.",
    )
    release_parser.set_defaults(func=cmd_release_check)

    self_audit_parser = maintainer_subparsers.add_parser(
        "self-audit",
        help="Audit SAGE itself through a contract-owned release-proof closure.",
        description="Runs the selected SAGE developer/auditor self-governance profile. This is not target-repository analysis, full public release proof, or a human seal.",
    )
    self_audit_parser.add_argument(
        "--profile",
        default="",
        help="Profile id from config/sage_self_audit_contract.json; defaults to the contract-owned profile.",
    )
    self_audit_parser.set_defaults(func=cmd_self_audit)

    work_package_parser = maintainer_subparsers.add_parser(
        "work-package",
        help="Inspect SAGE self-development mutation preflight or compose a non-authoritative closeout proposal.",
        description="Uses current package/source fingerprints and governed receipts. It never edits closure, approval or human-seal state.",
    )
    work_package_parser.add_argument("work_package_action", choices=["preflight", "close"])
    work_package_parser.set_defaults(func=cmd_work_package)

    ledger_parser = maintainer_subparsers.add_parser(
        "ledger-update",
        help="Update performance ledger artifacts from latest performance validation.",
        description="Appends a new row to performance ledger JSON/Markdown reports using the latest performance budget validation metrics.",
    )
    ledger_parser.add_argument("--repo-band", choices=["S", "M", "L"], help="Override inferred repository size band.")
    ledger_parser.add_argument("--notes", default="manual-ledger-update", help="Ledger note for this run.")
    ledger_parser.add_argument("--run-id", help="Optional explicit run identifier.")
    ledger_parser.set_defaults(func=cmd_ledger_update)

    fixture_import_parser = maintainer_subparsers.add_parser(
        "fixture-import",
        help="Import external React fixture artifacts into configured fixture roots.",
        description="Copies react support artifacts into a fixture artifact root and optionally updates fixture config enabled/required flags.",
    )
    fixture_import_parser.add_argument("--fixture-id", required=True, help="Fixture id from react_fixture_matrix.json")
    fixture_import_parser.add_argument("--source-root", required=True, help="Folder containing react_support_matrix.json and react_support_validation.json")
    fixture_import_parser.add_argument("--set-enabled", action="store_true", help="Set fixture enabled=true in config.")
    fixture_import_parser.add_argument("--set-required", action="store_true", help="Set fixture required=true in config.")
    fixture_import_parser.add_argument("--create-if-missing", action="store_true", help="Create fixture entry if missing.")
    fixture_import_parser.add_argument("--dry-run", action="store_true", help="Validate and print actions without writing.")
    fixture_import_parser.set_defaults(func=cmd_fixture_import)

    fixture_seed_parser = maintainer_subparsers.add_parser(
        "fixture-seed",
        help="Generate single-project React fixture artifacts from current workspace matrix.",
        description="Creates react_support_matrix.json and react_support_validation.json for one project under a target seed folder.",
    )
    fixture_seed_parser.add_argument("--project", required=True, help="Project key from output/.raw/react_support_matrix.json by_project")
    fixture_seed_parser.add_argument("--dest-root", required=True, help="Destination folder for generated fixture artifacts")
    fixture_seed_parser.set_defaults(func=cmd_fixture_seed)

    fixture_promote_parser = maintainer_subparsers.add_parser(
        "fixture-promote",
        help="Seed + import + validate fixture in one command.",
        description="Generates fixture artifacts from one project, imports them as enabled+required, then runs validate chain.",
    )
    fixture_promote_parser.add_argument("--project", required=True, help="Project key to seed from workspace matrix (example: LINGUASCRIBE_MASTER)")
    fixture_promote_parser.add_argument("--fixture-id", required=True, help="Fixture id in react_fixture_matrix.json")
    fixture_promote_parser.add_argument("--source-root", required=True, help="Seed destination/import source folder")
    fixture_promote_parser.add_argument("--create-if-missing", action="store_true", help="Create fixture entry if missing before import.")
    fixture_promote_parser.add_argument("--with-universal-proof", action="store_true", help="Include universal proof validator in chained validation.")
    fixture_promote_parser.set_defaults(func=cmd_fixture_promote)

    universal_run_parser = maintainer_subparsers.add_parser(
        "universal-proof-run",
        help="Import one external fixture and run universal proof validation.",
        description="Imports fixture artifacts (enabled+required) then runs validate --react --react-fixtures --universal-proof.",
    )
    universal_run_parser.add_argument("--fixture-id", required=True, help="Fixture id from react_fixture_matrix.json")
    universal_run_parser.add_argument("--source-root", required=True, help="Folder containing react_support_matrix.json and react_support_validation.json")
    universal_run_parser.add_argument("--create-if-missing", action="store_true", help="Create fixture entry if missing.")
    universal_run_parser.set_defaults(func=cmd_universal_proof_run)

    phase_status_parser = maintainer_subparsers.add_parser(
        "phase-status",
        help="Generate release phase status artifacts from validator outputs.",
        description="Reads validator artifacts and writes release phase status JSON/Markdown reports.",
    )
    phase_status_parser.set_defaults(func=cmd_phase_status)

    transcript_parser = maintainer_subparsers.add_parser(
        "transcript-template",
        help="Generate clean-machine setup transcript template.",
        description="Creates output/reports/clean_machine_setup_transcript.md template for Phase 6 evidence.",
    )
    transcript_parser.add_argument("--operator", default="HITL", help="Operator identifier.")
    transcript_parser.add_argument(
        "--target-root",
        default="<TARGET_REPOSITORY>",
        help="Repository path to record in the transcript template.",
    )
    transcript_parser.add_argument(
        "--machine-baseline",
        choices=["bare_os", "fresh_prerequisites", "preprovisioned"],
        default="fresh_prerequisites",
        help="Classify the machine prerequisite baseline.",
    )
    transcript_parser.add_argument("--force", action="store_true", help="Overwrite existing transcript.")
    transcript_parser.set_defaults(func=cmd_transcript_template)

    truth_sync_parser = maintainer_subparsers.add_parser(
        "truth-sync",
        help="Sync per-project truth-layer templates.",
        description="Ensures config/golden/projects/<PROJECT_KEY> truth files exist for every discovered variation key.",
    )
    truth_sync_parser.add_argument("--no-create", action="store_true", help="Only report layer coverage, do not create missing files.")
    truth_sync_parser.set_defaults(func=cmd_truth_sync)

    ci_parser = maintainer_subparsers.add_parser(
        "ci-check",
        help="Run CI-style release verification.",
        description="Runs unit contract tests, release-check, and phase-status, then writes CI release evidence artifacts.",
    )
    ci_parser.add_argument("--skip-release-check", action="store_true", help="Only run fast CI checks without the heavy release-check.")
    ci_parser.set_defaults(func=cmd_ci_check)

    storage_maintenance_parser = subparsers.add_parser(
        "storage-maintenance",
        help="Inspect SQLite allocation or explicitly reclaim reusable pages.",
        description=(
            "Read-only by default. --apply --confirm acquires the shared SAGE pipeline lock, "
            "checkpoints WAL, verifies SQLite integrity, enforces disk-space preflight and "
            "uses bounded incremental vacuum when available or offline full VACUUM otherwise."
        ),
    )
    storage_maintenance_parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the planned reclamation instead of reporting storage telemetry only.",
    )
    storage_maintenance_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required with --apply; confirms an offline SQLite maintenance window.",
    )
    storage_maintenance_parser.add_argument(
        "--max-pages",
        type=int,
        help="Maximum pages for an incremental-vacuum database; ignored by full VACUUM.",
    )
    storage_maintenance_parser.set_defaults(func=cmd_storage_maintenance)

    backup_parser = subparsers.add_parser(
        "backup",
        help="Backup SQLite state payloads to a JSON zip package.",
        description="Dumps SQLite state_payloads to raw JSON files and packages them into a timestamped disaster-recovery archive.",
    )
    backup_parser.add_argument("--dest", help="Destination path for backup zip file.")
    backup_parser.set_defaults(func=cmd_backup)

    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore SQLite database from a JSON zip package or active raw files.",
        description="Restores and repopulates codemaps.db from backed up JSON files inside a zip, latest backup, or active output/.raw files.",
    )
    restore_parser.add_argument("--source", help="Source path for backup zip file. Defaults to latest backup or active output/.raw files.")
    restore_parser.set_defaults(func=cmd_restore)

    _apply_top_level_cli_visibility(
        subparsers,
        public_distribution=is_public_distribution(),
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    
    # Read-only storage inspection must not create or migrate a missing database.
    # Its explicit apply path acquires the shared pipeline lock before schema setup.
    if args.command != "storage-maintenance":
        try:
            from tools.core.artifact_store import STORE
            STORE.initialize_schema()
        except Exception as e:
            print(f"Warning: Failed to initialize SQLite schema on startup: {e}")
        
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

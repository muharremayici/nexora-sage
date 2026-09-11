import json
import hashlib
import sys
import os
import re
import posixpath
import time
import sqlite3
import difflib
import tempfile
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path
from typing import Any

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.vendor_bootstrap import inject_vendor_paths
from tools.core.config import ROOT as CONFIG_ROOT
from tools.core.agent_surface_target_visibility import is_successful_surgical_packet
from tools.core.mcp_tool_profiles import (
    MCP_TOOL_PROFILE_ENV,
    project_mcp_tool_names,
    requires_sage_developer_mutation_preflight,
    resolve_mcp_tool_profile,
)
from tools.core.contextos_mcp import (
    target_directive_actionability_projection,
    target_directive_approval_projection,
)
from tools.core.python_runtime_env import isolated_python_subprocess_env
from tools.core.import_classifier import import_specifier_from_audit_detail
from tools.core.external_target_generation import resolve_external_target_artifact_dir

BASE_DIR = Path(_ROOT)
TARGET_ROOT = Path(CONFIG_ROOT)
VENDOR_PATHS = inject_vendor_paths(BASE_DIR)

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover - runtime environment guard
    print(
        "Nexora SAGE MCP runtime is unavailable. "
        "Install a compatible `mcp` package and ensure `mcp.server.fastmcp` is importable.",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


class ProfiledFastMCP(FastMCP):
    def set_visible_tools(self, profile: str, tool_names: set[str]) -> None:
        self.active_tool_profile = profile
        self._visible_tool_names = frozenset(tool_names)

    async def list_tools(self):
        tools = await super().list_tools()
        allowed = getattr(self, "_visible_tool_names", None)
        return tools if allowed is None else [tool for tool in tools if tool.name in allowed]

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        started = time.perf_counter()
        from tools.core.governance_trace import (
            activate_trace,
            activate_trace_storage,
            new_trace_id,
            reset_trace,
            reset_trace_storage,
        )
        from tools.core.honesty_telemetry import (
            activate_honesty_telemetry_path,
            reset_honesty_telemetry_path,
        )
        from tools.core.mcp_call_telemetry import (
            activate_mcp_call_capture,
            mcp_operational_db_path,
            mcp_operational_honesty_path,
            reset_mcp_call_capture,
        )

        operational_db = mcp_operational_db_path()
        operational_honesty = mcp_operational_honesty_path()
        trace_id = new_trace_id()
        trace_token = activate_trace(trace_id)
        trace_storage_token = activate_trace_storage(operational_db)
        honesty_token = activate_honesty_telemetry_path(operational_honesty)
        capture_token = activate_mcp_call_capture()
        profile = str(getattr(self, "active_tool_profile", "unknown"))
        allowed = getattr(self, "_visible_tool_names", None)
        failure_layer = "tool_execution"
        try:
            if allowed is not None and name not in allowed:
                failure_layer = "tool_visibility"
                raise ValueError(f"MCP tool {name!r} is unavailable in profile {profile!r}")
            if requires_sage_developer_mutation_preflight(BASE_DIR, profile, name):
                from tools.core.work_package_receipts import propose_work_package_evidence

                preflight = propose_work_package_evidence().get("mutation_preflight", {})
                if not isinstance(preflight, dict) or preflight.get("ready") is not True:
                    failure_layer = "mutation_preflight"
                    raise ValueError(
                        f"MCP tool {name!r} is blocked until the current SAGE developer learning bootstrap is loaded"
                    )
            result = await super().call_tool(name, arguments)
        except Exception:
            self._record_governance_trace(
                name,
                arguments,
                profile,
                started,
                "failure",
                failure_layer,
                trace_id,
                result=None,
            )
            raise
        else:
            self._record_governance_trace(
                name,
                arguments,
                profile,
                started,
                "success",
                "none",
                trace_id,
                result=result,
            )
            return result
        finally:
            reset_mcp_call_capture(capture_token)
            reset_honesty_telemetry_path(honesty_token)
            reset_trace_storage(trace_storage_token)
            reset_trace(trace_token)

    @staticmethod
    def _record_governance_trace(
        name: str,
        arguments: dict[str, Any],
        profile: str,
        started: float,
        outcome: str,
        failure_layer: str,
        trace_id: str | None,
        result: Any = None,
    ) -> None:
        try:
            from tools.core.mcp_call_telemetry import persist_completed_mcp_call

            persist_completed_mcp_call(
                tool_name=name,
                profile=profile,
                arguments=arguments if isinstance(arguments, dict) else {},
                started=started,
                outcome=outcome,
                failure_layer=failure_layer,
                trace_id=trace_id,
                result=result,
            )
        except Exception as exc:
            try:
                from tools.core.honesty_telemetry import record_honesty_event

                record_honesty_event(
                    component="mcp.server",
                    category="caught_error",
                    operation="record_governance_trace",
                    subject=name,
                    severity="warning",
                    reason="Local governance trace could not be persisted.",
                    fallback="return_or_raise_original_mcp_result_without_trace_event",
                    claim_impact="failure_attribution_trace_incomplete",
                    exception=exc,
                )
            except Exception:
                pass


mcp = ProfiledFastMCP("Nexora SAGE")
_ACTOR_GATEWAY_DISPATCH_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "actor_gateway_dispatch_context",
    default=None,
)

OUTPUT_DIR = BASE_DIR / "output"
RAW_DIR = OUTPUT_DIR / ".raw"
REPORTS_DIR = OUTPUT_DIR / "reports"
SCRIPTS_DIR = OUTPUT_DIR / "scripts"
TOOLS_DIR = BASE_DIR / "tools"
CLI_PATH = BASE_DIR / "sage.py"
SAFE_SYMBOL_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
from tools.generate_nexora_agent_contract import build_approval_gates, run as run_agent_contract
from tools.core.execution_identity import (
    SAGE_ACTOR_PROFILE_ENV,
    SAGE_OPERATOR_ACTOR_PROFILE,
    SAGE_REALITY_TARGET_PROFILE_ENV,
    SAGE_SELF_REALITY_PROFILE,
    resolve_execution_identity,
)
from tools.generate_nexora_agent_handoff import run as run_agent_handoff
from tools.generate_nexora_brief import run as run_nexora_brief
from tools.generate_nexora_operator_packet import run as run_operator_packet
from tools.generate_nexora_surface_inventory import run as run_surface_inventory
from tools.hitl_approval_ledger import init_ledger, record_decision, verify_ledger
from tools.hitl_decision_requests import create_request, init_requests, update_request_status
from tools.inspect_target import (
    AGENT_INSPECTION_MAX_TARGET_SPANS,
    project_dead_code_matches_for_file,
    render_agent_inspection_brief,
    write_inspection,
)
from tools.nexora_agent_response_ledger import init_ledger as init_response_ledger, record_response
from tools.validate_nexora_agent_response import response_template, validate_response, run_template_smoke
from tools.validate_hitl_governance import run as run_hitl_governance_validation
from tools.validate_hitl_lifecycle_smoke import run as run_hitl_lifecycle_smoke
from tools.core.json_io import (
    is_raw_artifact_path,
    load_json_content_cached,
    load_json_file,
    load_raw_artifact_path,
    raw_artifact_content_fingerprint,
)
from tools.core.artifact_freshness_contract import artifact_state_meta
from tools.core.atlas_io import load_atlas_data
from tools.core.text_normalizer import deep_repair, repair_text
from tools.core.config import DOCTRINE, ROOT as ANALYZED_REPOSITORY_ROOT
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.artifact_trust import build_artifact_trust_summary
from tools.core.audit_rules import build_rule_taxonomy
from tools.core.agent_command_contracts import (
    command_contract_summary_for_agent,
    merge_review_validation_policy,
    target_repo_validation_policy,
    target_repo_validation_tools,
    target_package_script_command,
)
from tools.core.agent_packet_budget import (
    BOUNDED_AGENT_PACKET_TOKENS,
    DEFAULT_AGENT_PACKET_BUDGET_TOKENS,
    DEFAULT_WATCHDOG_SESSION_PACKET_BUDGET_TOKENS,
    context_budget_profile,
)
from tools.core.agent_snippet_renderer import render_target_source_snippets
from tools.core.mcp_call_telemetry import (
    load_mcp_call_telemetry,
    record_mcp_call_result,
    render_mcp_call_telemetry_brief,
)
from tools.core.operational_limits import patch_applicability_timeout_seconds, sqlite_read_timeout_seconds
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.core.capability_registry import (
    build_agent_capability_map,
    capabilities_for_artifact,
    get_capability,
    load_capability_registry,
)
from tools.core.reality_scope import (
    SAGE_DEVELOPER_PROJECTION_ID,
    SAGE_SELF_TARGET_PROFILE_ID,
    build_reality_scope_projection,
    build_reality_target_workflow,
)
from tools.core.lesson_projection import project_impacted_lessons, resolve_lesson_projection_scope
from tools.core.sage_active_work_package import active_work_package
from tools.core.work_package_receipts import record_work_package_operation_safely
from tools.core.path_identity import strip_current_directory_prefix
from tools.core.test_impact_profiles import command_for_test, confidence_value, extract_logical_base_name, is_test_path
from tools.engines.capability_activation_planner import run as run_capability_activation_plan
from tools.engines.capability_registry_report import run_capability_registry_report
from tools.validate_engine_signal_contracts import validate_engine_signal_contracts
from tools.validate_pipeline_execution_contract import validate_pipeline_execution_contract


def _with_context_budget(
    yaml_lines: list[str],
    *,
    budget_tokens: int = DEFAULT_AGENT_PACKET_BUDGET_TOKENS,
) -> list[str]:
    profile = context_budget_profile("\n".join(yaml_lines), budget_tokens=budget_tokens)
    return [
        *yaml_lines,
        "context_budget:",
        "  estimator: " + json.dumps(profile["estimator"], ensure_ascii=False),
        f"  estimated_tokens: {int(profile['estimated_tokens'])}",
        f"  budget_tokens: {int(profile['budget_tokens'])}",
        "  status: " + json.dumps(profile["status"], ensure_ascii=False),
        f"  exact_tokenizer: {str(bool(profile['exact_tokenizer'])).lower()}",
    ]


def _record_mcp_call_result(
    tool_name: str,
    started: float,
    result: str,
    *,
    status: str = "ok",
    fail_closed_reason: str = "",
) -> str:
    try:
        record_mcp_call_result(
            tool_name,
            started,
            result,
            status=status,
            fail_closed_reason=fail_closed_reason,
        )
    except Exception as exc:
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="mcp.server",
                category="caught_error",
                operation="record_mcp_call_result",
                subject=tool_name,
                reason="MCP per-call telemetry could not be persisted.",
                fallback="return_tool_result_without_call_ledger_update",
                claim_impact="local_wait_guidance_degraded",
                exception=exc,
            )
        except Exception:
            pass
    return result


def _load_json(path: Path) -> Any:
    if is_raw_artifact_path(path):
        return deep_repair(load_raw_artifact_path(path, None))
    if not path.exists():
        return None
    try:
        return load_json_content_cached(path, transform=deep_repair)
    except Exception as exc:
        from tools.core.honesty_telemetry import record_honesty_event

        record_honesty_event(
            component="mcp.server",
            category="storage_fallback",
            operation="load_json_content_cached",
            subject=str(path),
            reason="Content-aware MCP JSON load failed.",
            fallback="uncached_json_read_or_none",
            claim_impact="mcp_json_freshness_requires_validation",
            exception=exc,
        )
        return deep_repair(load_json_file(path, None, bypass_proxy=True))


def _raw_artifact_source_mtime(name: str, shadow_path: Path) -> float:
    try:
        from tools.core.artifact_trust import _state_payload_meta

        meta = _state_payload_meta(RAW_DIR, name)
        if meta.get("exists"):
            return float(meta.get("source_mtime") or 0.0)
    except Exception as exc:
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="mcp.server",
                category="caught_error",
                operation="raw_artifact_source_mtime",
                subject=name,
                reason="SQLite state_payload metadata could not be read for MCP artifact freshness comparison.",
                fallback="shadow_file_mtime_if_available",
                claim_impact="mcp_artifact_currentness_degraded",
                exception=exc,
            )
        except Exception:
            pass
    try:
        return float(shadow_path.stat().st_mtime)
    except OSError:
        return 0.0


def _agent_surface_contract() -> dict[str, Any]:
    payload = _load_json(BASE_DIR / "config" / "agent_surface_contract.json")
    return payload if isinstance(payload, dict) else {}


def _patch_validation_next_action_contract(action_id: str) -> dict[str, str]:
    contract = _agent_surface_contract()
    actions = contract.get("patch_validation_next_actions") if isinstance(contract.get("patch_validation_next_actions"), dict) else {}
    action = actions.get(action_id) if isinstance(actions.get(action_id), dict) else {}
    instruction = str(action.get("instruction") or "").strip()
    agent_rule = str(action.get("agent_rule") or "").strip()
    if instruction and agent_rule:
        return {"id": action_id, "instruction": instruction, "agent_rule": agent_rule}
    return {
        "id": "agent_surface_contract_missing_patch_validation_next_action",
        "instruction": "Stop and ask the operator to repair config/agent_surface_contract.json before using this patch validation result.",
        "agent_rule": f"Missing patch_validation_next_actions contract entry for {action_id}.",
    }


def _work_queue_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    policy = contract.get("work_queue_brief_policy") if isinstance(contract.get("work_queue_brief_policy"), dict) else {}
    max_visible_items = int(policy.get("max_visible_items") or 1) if isinstance(policy, dict) else 1
    return {
        "max_visible_items": max(1, max_visible_items),
        "omission_field": str(policy.get("omission_field") or "omitted_work_items"),
        "agent_rule": str(policy.get("agent_rule") or "Brief projection policy is missing from config/agent_surface_contract.json."),
    }


def _symbol_search_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    policy = contract.get("symbol_search_policy") if isinstance(contract.get("symbol_search_policy"), dict) else {}
    return {
        "max_candidate_rows_per_source": max(1, int(policy.get("max_candidate_rows_per_source") or 1)),
        "ambiguity_preview_items": max(1, int(policy.get("ambiguity_preview_items") or 1)),
        "agent_rule": str(policy.get("agent_rule") or "Symbol search policy is missing from config/agent_surface_contract.json."),
    }


def _test_impact_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    policy = contract.get("test_impact_brief_policy") if isinstance(contract.get("test_impact_brief_policy"), dict) else {}
    return {
        "command_contract_projection": str(policy.get("command_contract_projection") or "grouped_by_execution_scope"),
        "max_contract_groups": max(1, int(policy.get("max_contract_groups") or 4)),
        "example_commands_per_group": max(1, int(policy.get("example_commands_per_group") or 1)),
        "agent_rule": str(policy.get("agent_rule") or "Test-impact command contract policy is missing from config/agent_surface_contract.json."),
    }


def _confidence_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    policy = contract.get("confidence_brief_policy") if isinstance(contract.get("confidence_brief_policy"), dict) else {}
    return policy if isinstance(policy, dict) else {}


def _confidence_recommended_action(action_key: str) -> str:
    policy = _confidence_brief_policy()
    actions = policy.get("recommended_actions") if isinstance(policy.get("recommended_actions"), dict) else {}
    action = str(actions.get(action_key) or "").strip()
    fallback = str(policy.get("missing_policy_action") or "").strip()
    return action or fallback or "confidence_action_unavailable_due_to_invalid_contract"


def _state_flow_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    policy = contract.get("state_flow_brief_policy") if isinstance(contract.get("state_flow_brief_policy"), dict) else {}
    return {
        "sample_keys_limit": max(1, int(policy.get("sample_keys_limit") or 1)),
        "sample_targets_limit": max(1, int(policy.get("sample_targets_limit") or 1)),
        "max_items_semantics": str(policy.get("max_items_semantics") or "State-flow projection policy is missing from config/agent_surface_contract.json."),
        "agent_rule": str(policy.get("agent_rule") or "State-flow projection policy is missing from config/agent_surface_contract.json."),
    }


def _supporting_context_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    policy = (
        contract.get("supporting_context_brief_policy")
        if isinstance(contract.get("supporting_context_brief_policy"), dict)
        else {}
    )
    return policy if isinstance(policy, dict) else {}


def _supporting_context_title(surface: str, fallback_title: str) -> str:
    policy = _supporting_context_brief_policy()
    titles = policy.get("surface_titles") if isinstance(policy.get("surface_titles"), dict) else {}
    title = str(titles.get(surface) or "").strip()
    if title:
        return title
    return str(fallback_title or policy.get("default_title") or "Target Repository Context Brief")


def _supporting_context_actions(surface: str, *, empty_result: bool = False) -> list[str]:
    policy = _supporting_context_brief_policy()
    if empty_result:
        empty_actions = policy.get("empty_result_actions") if isinstance(policy.get("empty_result_actions"), dict) else {}
        rows = empty_actions.get(surface) if isinstance(empty_actions.get(surface), list) else []
        actions = [str(row) for row in rows if str(row).strip()]
        if actions:
            return actions
    surface_actions = policy.get("surface_actions") if isinstance(policy.get("surface_actions"), dict) else {}
    rows = surface_actions.get(surface) if isinstance(surface_actions.get(surface), list) else []
    actions = [str(row) for row in rows if str(row).strip()]
    if actions:
        return actions
    default_rows = policy.get("default_actions") if isinstance(policy.get("default_actions"), list) else []
    default_actions = [str(row) for row in default_rows if str(row).strip()]
    return default_actions or ["Inspect the referenced target files before editing."]


def _safe_symbol_artifact_name(name: str) -> str:
    raw_name = str(name or "").strip()
    if not raw_name:
        raise ValueError("Symbol name is required.")
    sanitized = SAFE_SYMBOL_RE.sub("_", raw_name).strip("._")
    digest = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:12]
    return f"closure_{sanitized or 'symbol'}_{digest}.json"


def _resolve_raw_artifact(filename: str) -> Path:
    candidate = (RAW_DIR / filename).resolve()
    raw_root = RAW_DIR.resolve()
    if candidate.parent != raw_root:
        raise ValueError("Resolved artifact path escaped the raw output directory.")
    return candidate


def _run_cli(*args: str, env_overrides: dict[str, str] | None = None) -> str:
    try:
        safe_env = isolated_python_subprocess_env(
            os.environ,
            code_maps_dir=BASE_DIR,
            vendor_paths=VENDOR_PATHS,
        )
        if env_overrides:
            safe_env.update({str(key): str(value) for key, value in env_overrides.items()})
        from tools.core.artifact_store import get_adaptive_timeout
        result, _duration = run_observed_subprocess(
            [sys.executable, str(CLI_PATH), *args],
            cwd=BASE_DIR,
            label="mcp_cli",
            timeout=get_adaptive_timeout(180),
            env=safe_env,
            log=None,
        )
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
        if result.returncode == 0:
            return output or "OK"
        return f"Command failed ({result.returncode}):\n{output}".strip()
    except Exception as exc:
        return f"CLI execution error: {exc}"


def _run_python_script(script: Path, *args: str) -> str:
    try:
        safe_env = isolated_python_subprocess_env(
            os.environ,
            code_maps_dir=BASE_DIR,
            vendor_paths=VENDOR_PATHS,
        )
        from tools.core.artifact_store import get_adaptive_timeout
        result, _duration = run_observed_subprocess(
            [sys.executable, str(script), *args],
            cwd=BASE_DIR,
            label=f"mcp_script_{script.name}",
            timeout=get_adaptive_timeout(180),
            env=safe_env,
            log=None,
        )
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
        if result.returncode == 0:
            return output or "OK"
        return f"Command failed ({result.returncode}):\n{output}".strip()
    except Exception as exc:
        return f"Script execution error: {exc}"


def _script_succeeded(output: str) -> bool:
    text = str(output or "")
    return not (text.startswith("Command failed") or text.startswith("Script execution error"))


def _trust_failure_names(summary: dict[str, Any]) -> set[str]:
    failures = summary.get("failures") if isinstance(summary.get("failures"), list) else []
    return {str(row.get("name") or "") for row in failures if isinstance(row, dict)}


def _trust_warning_names(summary: dict[str, Any]) -> set[str]:
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    return {str(row.get("name") or "") for row in warnings if isinstance(row, dict)}


def _ensure_agent_artifact_chain_current(raw_dir: Path, target_root: str = "") -> dict[str, Any]:
    """Inspect agent-facing evidence freshness without mutating repository state."""

    summary = build_artifact_trust_summary(raw_dir)
    if summary.get("status") == "PASS":
        summary["auto_refresh"] = {"attempted": False, "reason": "already_current"}
        return summary

    command = (
        f'python sage.py run --step "Quality Gates" --target-root "{target_root}" --refresh'
        if target_root
        else 'python sage.py run --step "Quality Gates" --refresh'
    )
    summary["auto_refresh"] = {
        "attempted": False,
        "reason": "read_only_query_surface",
        "required_action": "Refresh evidence explicitly, then repeat this query.",
        "command": command,
    }
    return summary


def _audit_queue_recovery_plan(target_root: str = "", project: str = "MAIN") -> dict[str, Any]:
    """Return the bounded public recovery operation for the Audit-backed queue."""

    command_argv = ["python", "sage.py", "run", "--step", "Audit"]
    normalized_project = str(project or "MAIN").strip().upper()
    if normalized_project and normalized_project != "*":
        command_argv.extend(["--projects", normalized_project])
    if target_root:
        command_argv.extend(["--target-root", str(target_root)])

    rendered_parts: list[str] = []
    for part in command_argv:
        text = str(part)
        rendered_parts.append(f'"{text}"' if any(char.isspace() for char in text) else text)
    return {
        "attempted": False,
        "reason": "read_only_query_surface",
        "required_action": "Refresh the Atlas/Audit evidence consumed by this queue, then repeat this query.",
        "command": " ".join(rendered_parts),
        "command_argv": command_argv,
        "consumer": "get_violation_work_queue",
        "authority_chain": ["atlas", "audit_report"],
        "requested_projects": [normalized_project] if normalized_project and normalized_project != "*" else ["*"],
        "refresh_semantics": "dependency_correct_explicit_step_without_global_refresh",
        "predicted_steps": ["Atlas if stale", "Nuclear Sequencing", "Audit"],
        "predicted_duration": {
            "status": "unavailable",
            "reason": "pipeline_run_terminal_receipt_contract_pending",
        },
        "claim_boundary": (
            "This recovery repairs only the Audit-backed queue authority. "
            "It does not refresh Quality Gate, Signals, ContextOS or release proof."
        ),
    }


def _surgical_packet_recovery_plan(
    input_evidence: dict[str, Any],
    *,
    target_root: str = "",
    project: str = "MAIN",
) -> dict[str, Any]:
    """Return one producer closure for a blocked legacy packet generation."""

    normalized_project = str(project or "MAIN").strip().upper()
    command_argv = ["python", "sage.py", "run", "--profile", "daily"]
    if normalized_project and normalized_project != "*":
        command_argv.extend(["--projects", normalized_project])
    if target_root:
        command_argv.extend(["--target-root", str(target_root)])
    command_argv.append("--refresh")
    rendered_parts = [
        f'"{part}"' if any(char.isspace() for char in str(part)) else str(part)
        for part in command_argv
    ]
    blocked_inputs = sorted(
        str(item)
        for item in (input_evidence.get("blocked_inputs") or [])
        if str(item).strip()
    )
    recovery_reason = (
        "packet_artifact_trust_recovery_required"
        if blocked_inputs == ["artifact_trust_chain"]
        else "required_packet_input_generation_mismatch"
    )
    return {
        "attempted": False,
        "reason": recovery_reason,
        "required_action": (
            "Regenerate the bounded daily producer closure, then repeat the packet request."
        ),
        "command": " ".join(rendered_parts),
        "command_argv": command_argv,
        "consumer": "get_surgical_operation_packet",
        "blocked_required_inputs": blocked_inputs,
        "requested_projects": (
            [normalized_project]
            if normalized_project and normalized_project != "*"
            else ["*"]
        ),
        "execution_profile": "daily",
        "dependency_closure": {
            "selection": "canonical_pipeline_dag_for_daily_profile",
            "scope": "requested_projects",
            "exact_steps_known_before_dispatch": False,
        },
        "predicted_duration": {
            "status": "unavailable",
            "reason": "pipeline_run_terminal_receipt_contract_pending",
        },
        "refresh_semantics": "required_input_generation_recovery",
        "claim_boundary": (
            "This command repairs the packet input generation for the requested project. "
            "It is not release proof or a repository-wide readiness claim."
        ),
    }


def _mcp_recommended_tool_action(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    required_profile: str,
) -> dict[str, Any]:
    """Describe a recommended MCP call without pretending a hidden tool is callable."""

    current_profile = str(getattr(mcp, "active_tool_profile", "unknown"))
    current_tools = set(getattr(mcp, "_visible_tool_names", frozenset()))
    required_projection = project_mcp_tool_names(BASE_DIR, required_profile)
    required_tools = set(required_projection.get("visible_tools") or [])
    callable_now = tool_name in current_tools
    callable_after_transition = tool_name in required_tools
    availability = (
        "current_profile"
        if callable_now
        else "profile_transition_required"
        if callable_after_transition
        else "unavailable"
    )
    return {
        "tool": tool_name,
        "arguments": dict(arguments),
        "availability": availability,
        "current_profile": current_profile,
        "callable_in_current_profile": callable_now,
        "required_profile": required_profile,
        "callable_in_required_profile": callable_after_transition,
        "profile_transition_required": not callable_now and callable_after_transition,
        "profile_config_command_argv": [
            "python",
            "sage.py",
            "mcp",
            "--profile",
            required_profile,
            "--print-config",
        ],
        "return_profile": current_profile if not callable_now and callable_after_transition else "",
        "claim_boundary": (
            "Profile metadata describes tool visibility only. It does not grant mutation authority, "
            "acquire a lease or validate a patch."
        ),
    }


def _artifact_trust_blocks_actor_context(trust_summary: dict[str, Any]) -> bool:
    """Reject repository evidence unless the canonical trust summary is current."""

    return not isinstance(trust_summary, dict) or str(trust_summary.get("status") or "").upper() != "PASS"


def _analysis_snapshot_id(raw_dir: Path) -> str:
    commit = _load_json(raw_dir / "atlas_commit.json") or {}
    return str(commit.get("snapshot_id") or "") if isinstance(commit, dict) else ""


def _audit_queue_trust_projection(trust_summary: dict[str, Any]) -> dict[str, Any]:
    """Project only the evidence consumed by the Audit-backed work queue."""
    required_names = {
        "artifact_present:atlas",
        "artifact_present:analysis_scope_authority",
        "artifact_present:audit_report",
        "sqlite_primary:atlas",
        "sqlite_primary:analysis_scope_authority",
        "sqlite_primary:audit_report",
        "scope_authority:present",
        "scope_authority:claim_usable",
        "scope_authority:audit_identity_matches",
        "audit_scope:present",
        "audit_scope:atlas_project_count_matches",
        "audit_scope:audited_projects_are_atlas_subset",
        "audit_scope:violation_projects_are_audited_subset",
        "freshness:audit_not_older_than_atlas",
        "freshness:scope_not_older_than_atlas",
    }
    checks = [
        row
        for row in (trust_summary.get("checks") or [])
        if isinstance(row, dict) and str(row.get("name") or "") in required_names
    ]
    failures = [row for row in checks if not row.get("passed") and row.get("severity") == "error"]
    warnings = [row for row in checks if not row.get("passed") and row.get("severity") == "warning"]
    missing_checks = sorted(required_names - {str(row.get("name") or "") for row in checks})
    if missing_checks:
        failures.append(
            {
                "name": "audit_queue_trust_contract_complete",
                "passed": False,
                "severity": "error",
                "details": f"missing_checks={missing_checks}",
            }
        )
    return {
        "status": "FAIL" if failures else "PASS",
        "profile": "audit_queue",
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "scope": trust_summary.get("scope"),
        "freshness": trust_summary.get("freshness"),
        "auto_refresh": trust_summary.get("auto_refresh"),
        "claim_boundary": (
            "This projection proves only the current Atlas/Audit evidence consumed by the violation queue. "
            "It does not prove Quality Gate, Signals, ContextOS or release readiness."
        ),
    }


def _invalid_actor_context_payload(tool_name: str, trust_summary: dict[str, Any]) -> dict[str, Any]:
    refresh = trust_summary.get("auto_refresh") if isinstance(trust_summary, dict) else {}
    refresh = refresh if isinstance(refresh, dict) else {}
    return {
        "status": "INVALID_CONTEXT",
        "blocking": True,
        "tool": tool_name,
        "artifact_trust": {
            "status": trust_summary.get("status", "UNKNOWN") if isinstance(trust_summary, dict) else "UNKNOWN",
            "scope": trust_summary.get("scope") if isinstance(trust_summary, dict) else None,
            "freshness": trust_summary.get("freshness") if isinstance(trust_summary, dict) else None,
            "failures": (trust_summary.get("failures") or [])[:10] if isinstance(trust_summary, dict) else [],
            "warnings": (trust_summary.get("warnings") or [])[:10] if isinstance(trust_summary, dict) else [],
            "auto_refresh": refresh,
        },
        "required_action": "Refresh or rerun analysis for the explicit target repository, then repeat the request.",
        "refresh_command": str(refresh.get("command") or ""),
        "recovery_plan": refresh,
        "claim_boundary": "No queue item is served from stale, failed or unreadable repository evidence.",
    }


def _render_invalid_actor_context_brief(payload: dict[str, Any]) -> str:
    return (
        "# Invalid Repository Context\n\n"
        f"status: {payload.get('status', 'INVALID_CONTEXT')}\n"
        f"blocking: {str(payload.get('blocking') is True).lower()}\n"
        f"tool: {payload.get('tool', '')}\n"
        f"artifact_trust: {(payload.get('artifact_trust') or {}).get('status', 'UNKNOWN')}\n"
        f"required_action: {payload.get('required_action', '')}\n"
        f"refresh_command: {payload.get('refresh_command', '')}\n"
        f"claim_boundary: {payload.get('claim_boundary', '')}\n"
    )


def _actor_result_status(result: Any) -> str:
    payload = result if isinstance(result, dict) else None
    if payload is None and isinstance(result, str):
        try:
            candidate = json.loads(result)
            payload = candidate if isinstance(candidate, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
    return str((payload or {}).get("status") or "")


def _read_json_artifact(path: Path, missing_message: str) -> str:
    data = _load_json(path)
    if data is None:
        return missing_message
    return json.dumps(data, indent=2, ensure_ascii=False)


def _read_text_artifact(path: Path, missing_message: str) -> str:
    if not path.exists():
        return missing_message
    return path.read_text(encoding="utf-8", errors="replace")


def _canonical_openable_path(value: Any, analysis_root: Path) -> dict[str, str]:
    reported = str(value or "").replace("\\", "/").strip()
    if not reported:
        return {"reported_path": "", "openable_path": "", "path_status": "missing"}
    resolved_root = analysis_root.resolve()
    candidates = [reported]
    if not reported.startswith("src/"):
        candidates.append("src/" + reported)
    for candidate in candidates:
        normalized = posixpath.normpath(candidate)
        if normalized in {"", ".", ".."} or normalized.startswith("../") or Path(normalized).is_absolute():
            continue
        if re.match(r"^[A-Za-z]:", normalized):
            continue
        resolved_candidate = (analysis_root / normalized).resolve()
        try:
            canonical = resolved_candidate.relative_to(resolved_root).as_posix()
        except ValueError:
            continue
        if resolved_candidate.exists():
            status = "openable" if canonical == reported else "normalized_to_openable_path"
            return {"reported_path": reported, "openable_path": canonical, "path_status": status}
    return {"reported_path": reported, "openable_path": reported, "path_status": "not_openable"}


def _count_rows_by_field(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(field) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _nested_payload_value(payload: dict[str, Any], dotted_path: str) -> Any:
    value: Any = payload
    for part in str(dotted_path or "").split("."):
        if not part or not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _self_governance_suspicion_state(
    sources: list[dict[str, Any]], *, base_dir: Path = BASE_DIR
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        artifact_rel = str(source.get("artifact") or "").replace("\\", "/")
        artifact_path = base_dir / artifact_rel
        payload = _load_json(artifact_path) if artifact_rel else None
        accepted_values = [str(value) for value in source.get("accepted_values", [])]
        observed_status = _nested_payload_value(payload or {}, str(source.get("status_path") or "status"))
        freshness_sources = [str(value).replace("\\", "/") for value in source.get("freshness_sources", [])]
        resolved_sources: dict[str, list[Path]] = {}
        for value in freshness_sources:
            matches = sorted(base_dir.glob(value)) if any(token in value for token in ("*", "?", "[")) else [base_dir / value]
            resolved_sources[value] = [path for path in matches if path.is_file()]
        missing_sources = [value for value, paths in resolved_sources.items() if not paths]
        artifact_mtime = artifact_path.stat().st_mtime if artifact_path.exists() else 0.0
        newer_sources = [
            value
            for value, paths in resolved_sources.items()
            if any(path.stat().st_mtime > artifact_mtime for path in paths)
        ]
        if not artifact_path.exists():
            evidence_status = "missing"
        elif missing_sources:
            evidence_status = "source_missing"
        elif newer_sources:
            evidence_status = "stale"
        elif str(observed_status) not in accepted_values:
            evidence_status = "attention"
        else:
            evidence_status = "current"
        rows.append(
            {
                "id": source.get("id"),
                "artifact": artifact_rel,
                "release_proof_step": source.get("release_proof_step"),
                "purpose": source.get("purpose"),
                "evidence_status": evidence_status,
                "observed_status": observed_status,
                "accepted_values": accepted_values,
                "missing_sources": missing_sources,
                "newer_sources": newer_sources,
                "summary": (payload or {}).get("summary", {}) if isinstance(payload, dict) else {},
            }
        )
    return rows


def _build_sage_learning_system_payload(
    changed_files: list[str] | None = None,
    failure_families: list[str] | None = None,
) -> dict[str, Any]:
    loop_contract = _load_json(BASE_DIR / "config" / "sage_development_loop_contract.json")
    scope_projection = build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
    self_target_workflow = build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)
    audit_progress = _load_json(BASE_DIR / "config" / "manual_adversarial_audit_progress.json")

    loop_contract_valid = (
        isinstance(loop_contract, dict)
        and loop_contract.get("meta", {}).get("kind") == "nexora.sage_development_loop_contract"
    )
    operating_rules = loop_contract.get("operating_rules", {}) if loop_contract_valid else {}
    bootstrap_sequence = loop_contract.get("fresh_agent_bootstrap", []) if loop_contract_valid else []
    work_package_cycle = loop_contract.get("work_package_cycle", []) if loop_contract_valid else []
    manual_review_triggers = loop_contract.get("manual_review_triggers", []) if loop_contract_valid else []
    suspicion_sources = loop_contract.get("self_governance_suspicion_sources", []) if loop_contract_valid else []
    suspicion_state = _self_governance_suspicion_state(suspicion_sources if isinstance(suspicion_sources, list) else [])
    durable_state_sources = loop_contract.get("durable_state_sources", {}) if loop_contract_valid else {}
    try:
        active_package = active_work_package()
        active_package_status = "ok"
    except Exception:
        active_package = {"status": "not_available", "reason": "active_work_package_ledger_missing_or_invalid"}
        active_package_status = "missing_or_invalid"
    package_closure = loop_contract.get("package_closure", {}) if loop_contract_valid else {}
    validation_commands = loop_contract.get("validation_commands", []) if loop_contract_valid else []
    semantic_diff_review = loop_contract.get("semantic_diff_review", {}) if loop_contract_valid else {}

    lesson_rows = [row for row in scope_projection.get("lessons", []) if isinstance(row, dict)]
    work_items = [row for row in scope_projection.get("work_items", []) if isinstance(row, dict)]
    execution_plan = (
        scope_projection.get("execution_plan")
        if isinstance(scope_projection.get("execution_plan"), dict)
        else {}
    )

    coverage_rows = []
    if isinstance(audit_progress, dict):
        coverage = audit_progress.get("source_layer_coverage_progress")
        if isinstance(coverage, dict) and isinstance(coverage.get("layers"), list):
            coverage_rows = [row for row in coverage.get("layers", []) if isinstance(row, dict)]

    open_work_items = [row for row in work_items if str(row.get("status") or "").lower() not in {"closed", "done"}]
    blocking_work_items = [
        row
        for row in open_work_items
        if str(row.get("priority") or "") in {"P0", "P0.5"} or bool(row.get("release_blocking"))
    ]
    impacted_contract = (
        semantic_diff_review.get("lesson_application_levels", {})
        .get("impacted", {})
        .get("projection_contract", {})
        if isinstance(semantic_diff_review, dict)
        else {}
    )
    projection_scope = resolve_lesson_projection_scope(
        changed_files=changed_files,
        failure_families=failure_families,
        active_package=active_package if active_package_status == "ok" else {},
        work_items=work_items,
        current_wave=execution_plan.get("current_wave"),
        contract=impacted_contract if isinstance(impacted_contract, dict) else {},
    )
    impacted_projection = (
        project_impacted_lessons(
            lesson_rows,
            changed_files=projection_scope["changed_files"],
            failure_families=projection_scope["failure_families"],
            contract=impacted_contract if isinstance(impacted_contract, dict) else {},
        )
        if projection_scope["ready"]
        else {
            "meta": {"kind": "semantic_diff_impacted_lesson_projection", "version": "v1"},
            "summary": {"status": projection_scope["projection_status"], "changed_files": 0, "matched_lessons": 0},
            "lessons": [],
            "unmatched_changed_files": [],
            "limits": {"semantic_inference": False, "full_registry_replay": False},
        }
    )
    impacted_projection["scope"] = {
        "status": projection_scope["status"],
        "source": projection_scope["source"],
        "reason": projection_scope["reason"],
        "changed_files": len(projection_scope["changed_files"]),
        "failure_families": len(projection_scope["failure_families"]),
    }

    return {
        "meta": {
            "kind": "sage_learning_system",
            "version": "1.0.0",
            "audience": "sage_developer_or_auditor",
            "role": "debug_provenance",
            "target_repo_agent_default": False,
            "system_scope": scope_projection.get("meta", {}).get("system_scope"),
            "scope_projection": SAGE_DEVELOPER_PROJECTION_ID,
        },
        "sources": {
            "development_loop_contract": "config/sage_development_loop_contract.json",
            "scope_taxonomy": "config/agent_surface_taxonomy.json",
            "execution_wave_registry": "config/sage_execution_wave_registry.json",
            **(durable_state_sources if isinstance(durable_state_sources, dict) else {}),
        },
        "sage_self_target_workflow": self_target_workflow,
        "operating_rules": operating_rules if isinstance(operating_rules, dict) else {},
        "fresh_agent_bootstrap": bootstrap_sequence if isinstance(bootstrap_sequence, list) else [],
        "work_package_cycle": work_package_cycle if isinstance(work_package_cycle, list) else [],
        "manual_review_triggers": manual_review_triggers if isinstance(manual_review_triggers, list) else [],
        "self_governance_suspicion_state": suspicion_state,
        "package_closure": package_closure if isinstance(package_closure, dict) else {},
        "semantic_diff_impacted_lessons": impacted_projection,
        "execution_plan": execution_plan,
        "active_work_package": active_package,
        "summary": {
            "development_loop_contract_status": "ok" if loop_contract_valid else "missing_or_invalid",
            "lesson_count": len(lesson_rows),
            "lesson_priority_counts": _count_rows_by_field(lesson_rows, "priority"),
            "lesson_status_counts": _count_rows_by_field(lesson_rows, "status"),
            "work_item_count": len(work_items),
            "open_work_item_count": len(open_work_items),
            "blocking_work_item_count": len(blocking_work_items),
            "self_governance_suspicion_sources": len(suspicion_state),
            "self_governance_attention_sources": len(
                [row for row in suspicion_state if row.get("evidence_status") != "current"]
            ),
            "open_work_item_priority_counts": _count_rows_by_field(open_work_items, "priority"),
            "current_execution_wave": execution_plan.get("current_wave"),
            "active_work_package_status": active_package_status,
            "manual_audit_current_layer": (audit_progress or {}).get("current_layer_id")
            if isinstance(audit_progress, dict)
            else None,
            "manual_audit_current_status": (audit_progress or {}).get("current_layer_status")
            if isinstance(audit_progress, dict)
            else None,
            "source_layer_coverage_count": len(coverage_rows),
            "source_layer_status_counts": _count_rows_by_field(coverage_rows, "status"),
        },
        "open_work_items": [
            {
                "id": row.get("id"),
                "priority": row.get("priority"),
                "status": row.get("status"),
                "owner_layer": row.get("owner_layer"),
                "target_release": row.get("target_release"),
                "next_action": row.get("next_action"),
            }
            for row in open_work_items
        ],
        "validation_commands": validation_commands if isinstance(validation_commands, list) else [],
    }


def _render_sage_learning_system_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    rules = payload.get("operating_rules", {}) if isinstance(payload, dict) else {}
    lines = [
        "# SAGE Learning System",
        "",
        "- audience: `sage_developer_or_auditor`",
        "- target_repo_agent_default: `false`",
        f"- lessons: `{summary.get('lesson_count')}`",
        f"- open_work_items: `{summary.get('open_work_item_count')}`",
        f"- blocking_work_items: `{summary.get('blocking_work_item_count')}`",
        f"- self_governance_suspicion_sources: `{summary.get('self_governance_suspicion_sources')}`",
        f"- self_governance_attention_sources: `{summary.get('self_governance_attention_sources')}`",
        f"- development_loop_contract_status: `{summary.get('development_loop_contract_status')}`",
        f"- current_execution_wave: `{summary.get('current_execution_wave')}`",
        f"- manual_audit_current_layer: `{summary.get('manual_audit_current_layer')}`",
        f"- manual_audit_current_status: `{summary.get('manual_audit_current_status')}`",
        "",
        "## Operating Rules",
        "",
    ]
    for key, value in rules.items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Fresh Agent Bootstrap", ""])
    for item in payload.get("fresh_agent_bootstrap", []) or []:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('order')}. {item.get('id')}`: {item.get('action')} (`{item.get('source')}`)")
    lines.extend(["", "## Work Package Cycle", ""])
    for item in payload.get("work_package_cycle", []) or []:
        lines.append(f"- `{item}`")
    lines.extend(["", "## Execution Waves", ""])
    execution_plan = payload.get("execution_plan") if isinstance(payload.get("execution_plan"), dict) else {}
    for wave in execution_plan.get("waves", []) or []:
        if isinstance(wave, dict):
            lines.append(
                f"- `{wave.get('id')}` `{wave.get('computed_status')}`: "
                f"{wave.get('open_work_items')}/{wave.get('work_items')} open - {wave.get('title')}"
            )
    lines.extend(["", "## Package Closure", ""])
    closure = payload.get("package_closure", {}) if isinstance(payload.get("package_closure"), dict) else {}
    for item in closure.get("required", []) or []:
        lines.append(f"- `{item}`")
    projection = payload.get("semantic_diff_impacted_lessons", {}) if isinstance(payload, dict) else {}
    projection_summary = projection.get("summary", {}) if isinstance(projection, dict) else {}
    projection_scope = projection.get("scope", {}) if isinstance(projection.get("scope"), dict) else {}
    lines.extend(
        [
            "",
            "## Semantic Diff Impacted Lessons",
            "",
            f"- status: `{projection_summary.get('status')}`",
            f"- changed_files: `{projection_summary.get('changed_files')}`",
            f"- matched_lessons: `{projection_summary.get('matched_lessons')}`",
            f"- omitted_lessons: `{projection_summary.get('omitted_lessons')}`",
            f"- scope_status: `{projection_scope.get('status')}`",
            f"- scope_source: `{projection_scope.get('source')}`",
            f"- scope_reason: `{projection_scope.get('reason')}`",
            f"- scope_changed_files: `{projection_scope.get('changed_files')}`",
            f"- scope_failure_families: `{projection_scope.get('failure_families')}`",
        ]
    )
    for row in projection.get("lessons", []) or []:
        lines.append(
            f"- `{row.get('priority')} {row.get('id')}` via "
            f"`{', '.join(row.get('matched_strong_signals', []))}`"
        )
    for path in projection.get("unmatched_changed_files", []) or []:
        lines.append(f"- unmatched: `{path}`")
    workflow = payload.get("sage_self_target_workflow", {}) if isinstance(payload, dict) else {}
    profile = workflow.get("profile", {}) if isinstance(workflow, dict) else {}
    watchdog = profile.get("watchdog_support", {}) if isinstance(profile, dict) else {}
    lines.extend(
        [
            "",
            "## Explicit SAGE Self-Target Workflow",
            "",
            f"- profile: `{workflow.get('meta', {}).get('profile_id')}`",
            f"- system_scope: `{profile.get('system_scope')}`",
            f"- analysis_root: `{workflow.get('analysis_root')}`",
            f"- artifact_strategy: `{profile.get('artifact_strategy')}`",
            f"- watchdog_support: `{watchdog.get('status')}`",
        ]
    )
    for row in profile.get("bootstrap_tools", []) or []:
        if isinstance(row, dict):
            lines.append(f"- bootstrap: `{row.get('tool')}` arguments=`{row.get('arguments')}`")
    lines.append(f"- surgical_tools: `{profile.get('surgical_tools')}`")
    lines.append(f"- proof_boundary: `{profile.get('proof_boundary')}`")
    lines.extend(["", "## Self-Governance Suspicion State", ""])
    for item in payload.get("self_governance_suspicion_state", []) or []:
        lines.append(
            f"- `{item.get('id')}`: `{item.get('evidence_status')}` "
            f"(observed=`{item.get('observed_status')}`, step=`{item.get('release_proof_step')}`)"
        )
    lines.extend(["", "## Validation Commands", ""])
    for command in payload.get("validation_commands", []) or []:
        lines.append(f"- `{command}`")
    lines.extend(["", "## Open Work Items", ""])
    open_items = payload.get("open_work_items", []) if isinstance(payload, dict) else []
    if not open_items:
        lines.append("- None.")
    else:
        for item in open_items:
            lines.append(
                "- "
                + f"`{item.get('id')}` "
                + f"priority=`{item.get('priority')}` "
                + f"owner=`{item.get('owner_layer')}` "
                + f"target_release=`{item.get('target_release')}`"
            )
            lines.append(f"  next_action: {item.get('next_action')}")
    return "\n".join(lines) + "\n"


def _valid_external_target(target_root: str) -> Path | None:
    target = Path(str(target_root or "")).expanduser()
    if not target:
        return None
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    else:
        target = target.resolve()
    if not target.exists() or not target.is_dir():
        return None
    return target


def _analysis_root_display(target_root: str = "") -> str:
    if target_root:
        target = _valid_external_target(target_root)
        return str(target) if target is not None else str(target_root)
    return str(ANALYZED_REPOSITORY_ROOT)


def _target_output_slug(target_root: str) -> str:
    target_path = Path(target_root)
    stem = target_path.name or "external_target"
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in stem).strip("_") or "external_target"
    digest = hashlib.sha256(str(target_path).encode("utf-8")).hexdigest()[:10]
    return f"{safe}_{digest}"


def _raw_dir_for_target(target_root: str = "") -> Path:
    if not target_root:
        return RAW_DIR
    target = _valid_external_target(target_root)
    if target is None:
        raise ValueError(f"Invalid external target root: {target_root}")
    target_dir = BASE_DIR / "output" / "external_targets" / _target_output_slug(str(target))
    artifact_dir, _reason = resolve_external_target_artifact_dir(target_dir)
    return artifact_dir / ".raw"


def _reports_dir_for_target(target_root: str = "") -> Path:
    if not target_root:
        return REPORTS_DIR
    target = _valid_external_target(target_root)
    if target is None:
        raise ValueError(f"Invalid external target root: {target_root}")
    target_dir = BASE_DIR / "output" / "external_targets" / _target_output_slug(str(target))
    artifact_dir, _reason = resolve_external_target_artifact_dir(target_dir)
    return artifact_dir / "reports"


def _active_mcp_actor_profile() -> str:
    active_profile = str(
        globals().get("_ACTIVE_MCP_PROFILE")
        or os.environ.get(MCP_TOOL_PROFILE_ENV)
        or "target_repository_default"
    ).strip()
    declared_actor = str(os.environ.get(SAGE_ACTOR_PROFILE_ENV) or active_profile).strip()
    if declared_actor != active_profile:
        raise ValueError(
            "MCP actor identity does not match the active tool visibility profile"
        )
    return active_profile


def _resolve_mcp_execution_identity(
    *,
    target_root: str = "",
    reality_profile: str | None = None,
) -> dict[str, Any]:
    resolved_reality = (
        str(os.environ.get(SAGE_REALITY_TARGET_PROFILE_ENV) or "").strip()
        if reality_profile is None
        else str(reality_profile or "").strip()
    )
    return resolve_execution_identity(
        target_root=target_root,
        actor_profile=_active_mcp_actor_profile(),
        reality_profile=resolved_reality,
        public_distribution=(BASE_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file(),
        installation_root=BASE_DIR,
        default_repository_root=ANALYZED_REPOSITORY_ROOT,
    )


def _watchdog_session_roots(target_root: str = "", profile_id: str = "") -> tuple[Path, Path, str]:
    """Resolve one watchdog subject without allowing a self-target fallback."""
    resolved_profile = str(profile_id or "").strip()
    resolved_target_root = str(target_root or "").strip()
    if resolved_profile:
        from tools.core.reality_scope import SAGE_SELF_TARGET_PROFILE_ID, build_reality_target_workflow

        if resolved_profile != SAGE_SELF_TARGET_PROFILE_ID:
            raise ValueError(f"Unsupported watchdog target profile: {resolved_profile}")
        workflow = build_reality_target_workflow(resolved_profile)
        expected_root = Path(str(workflow.get("analysis_root") or "")).resolve()
        if resolved_target_root:
            supplied_root = _valid_external_target(resolved_target_root)
            if supplied_root is None or supplied_root != expected_root:
                raise ValueError("watchdog profile target root does not match its canonical analysis root")
        resolved_target_root = str(expected_root)
        execution_identity = _resolve_mcp_execution_identity(
            target_root=resolved_target_root,
            reality_profile=resolved_profile,
        )
        if execution_identity.get("system_scope") != "SAGE_ON_SAGE":
            raise ValueError("The sage_self watchdog requires private SAGE_ON_SAGE authority")
    elif resolved_target_root:
        target = _valid_external_target(resolved_target_root)
        if target is None:
            raise ValueError(f"Invalid external target root: {resolved_target_root}")
        if target == BASE_DIR.resolve():
            raise ValueError("SAGE self-target watchdog requires profile_id=sage_self")
        resolved_target_root = str(target)

    if not resolved_target_root:
        return RAW_DIR, REPORTS_DIR, ""
    return (
        _raw_dir_for_target(resolved_target_root),
        _reports_dir_for_target(resolved_target_root),
        resolved_target_root,
    )


_ATLAS_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_GRAPH_CACHE: dict[tuple[str, str], tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]] = {}


def _artifact_cache_key(path: Path) -> tuple[str, str]:
    return (str(path.resolve()), raw_artifact_content_fingerprint(path))


def _atlas(raw_dir: Path | None = None) -> dict:
    source_raw_dir = raw_dir or RAW_DIR
    atlas_path = source_raw_dir / "atlas.json"
    cache_key = _artifact_cache_key(atlas_path)
    cached = _ATLAS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    atlas = load_atlas_data(source_raw_dir)
    _ATLAS_CACHE.clear()
    _ATLAS_CACHE[cache_key] = atlas
    return atlas


def _host_key(atlas: dict) -> str:
    config_path = BASE_DIR / "config" / "codemaps.config.json"
    if config_path.exists():
        cfg = _load_json(config_path) or {}
        roles = cfg.get("project_roles", {})
        host_key = next((k for k, v in roles.items() if v == "host" and k in atlas), None)
        if host_key:
            return host_key
    return list(atlas.keys())[0] if atlas else "MAIN"


def _audit() -> dict:
    return _load_json(RAW_DIR / "audit_report.json") or {}


def _doctrine() -> dict:
    return _load_json(BASE_DIR / "config" / "architecture_doctrine.json") or {}


def _normalize_project_name(project: str, atlas: dict) -> str:
    if project in atlas:
        return project
    lowered = project.lower()
    for candidate in atlas.keys():
        if candidate.lower() == lowered:
            return candidate
    return project


def _file_context_from_atlas(project_data: dict, project_key: str, rel_path: str) -> dict[str, Any]:
    normalized_rel = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    files = project_data.get("files") or {}
    candidate_rels = [
        normalized_rel,
        f"{normalized_rel}.ts",
        f"{normalized_rel}.tsx",
        f"{normalized_rel}.js",
        f"{normalized_rel}.jsx",
        f"{normalized_rel}/index.ts",
        f"{normalized_rel}/index.tsx",
        f"{normalized_rel}/index.js",
        f"{normalized_rel}/index.jsx",
    ]
    meta = files.get(normalized_rel) if isinstance(files, dict) else None
    if not isinstance(meta, dict) and isinstance(files, dict):
        for candidate in candidate_rels[1:]:
            candidate_meta = files.get(candidate)
            if isinstance(candidate_meta, dict):
                meta = candidate_meta
                normalized_rel = candidate
                break
    if not isinstance(meta, dict) and isinstance(files, dict):
        for _key, value in files.items():
            workspace_rel = str(value.get("workspace_rel") or "").replace("\\", "/") if isinstance(value, dict) else ""
            if isinstance(value, dict) and workspace_rel in candidate_rels:
                meta = value
                normalized_rel = str(_key).replace("\\", "/")
                break
    if not isinstance(meta, dict):
        meta = {}
    workspace_rel = str(meta.get("workspace_rel") or normalized_rel).replace("\\", "/")
    return {
        "repo_relative_path": workspace_rel,
        "atlas_relative_path": normalized_rel,
        "atlas_node": f"{project_key}::{normalized_rel}" if project_key and normalized_rel else "",
    }


def _sqlite_file_context_from_raw(raw_dir: Path, target: str) -> tuple[str, dict[str, Any]] | None:
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return None
    target_text = str(target or "").replace("\\", "/").strip().strip("/")
    if target_text.startswith("./"):
        target_text = target_text[2:].strip("/")
    if not target_text:
        return None

    project_filter = ""
    rel = target_text
    if "::" in target_text:
        project_filter, rel = target_text.split("::", 1)
    rel = str(rel or "").replace("\\", "/").strip().strip("/")
    if rel.startswith("./"):
        rel = rel[2:].strip("/")
    if not rel:
        return None

    candidate_rels = [
        rel,
        f"{rel}.ts",
        f"{rel}.tsx",
        f"{rel}.js",
        f"{rel}.jsx",
        f"{rel}/index.ts",
        f"{rel}/index.tsx",
        f"{rel}/index.js",
        f"{rel}/index.jsx",
    ]
    if rel.startswith("src/"):
        atlas_rel = rel[4:]
        candidate_rels.extend(
            [
                atlas_rel,
                f"{atlas_rel}.ts",
                f"{atlas_rel}.tsx",
                f"{atlas_rel}.js",
                f"{atlas_rel}.jsx",
                f"{atlas_rel}/index.ts",
                f"{atlas_rel}/index.tsx",
                f"{atlas_rel}/index.js",
                f"{atlas_rel}/index.jsx",
            ]
        )
    candidate_rels = list(dict.fromkeys(candidate_rels))
    placeholders = ",".join("?" for _ in candidate_rels)
    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            conn.row_factory = sqlite3.Row
            project_path = ""
            if project_filter:
                project_row = conn.execute(
                    "SELECT project_key, path FROM projects WHERE lower(project_key) = lower(?) LIMIT 1;",
                    (project_filter,),
                ).fetchone()
                project_key = str(project_row["project_key"]) if project_row else project_filter
                project_path = str(project_row["path"] or "").replace("\\", "/").strip("/") if project_row else ""
                if project_path in {".", "./"}:
                    project_path = ""
                if project_path.startswith("./"):
                    project_path = project_path[2:].strip("/")
                if project_path and rel.startswith(f"{project_path}/"):
                    stripped_rel = rel[len(project_path) + 1 :].strip("/")
                    candidate_rels.extend(
                        [
                            stripped_rel,
                            f"{stripped_rel}.ts",
                            f"{stripped_rel}.tsx",
                            f"{stripped_rel}.js",
                            f"{stripped_rel}.jsx",
                            f"{stripped_rel}/index.ts",
                            f"{stripped_rel}/index.tsx",
                            f"{stripped_rel}/index.js",
                            f"{stripped_rel}/index.jsx",
                        ]
                    )
                    candidate_rels = list(dict.fromkeys(candidate_rels))
                    placeholders = ",".join("?" for _ in candidate_rels)
                row = conn.execute(
                    f"SELECT file_id, project_key, rel_path FROM files WHERE project_key = ? AND rel_path IN ({placeholders}) LIMIT 1;",
                    (project_key, *candidate_rels),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    SELECT files.file_id, files.project_key, files.rel_path, projects.path
                    FROM files
                    LEFT JOIN projects ON projects.project_key = files.project_key
                    WHERE files.rel_path IN ({placeholders})
                    ORDER BY CASE WHEN upper(files.project_key) = 'MAIN' THEN 0 ELSE 1 END, files.project_key, files.rel_path
                    LIMIT 1;
                    """,
                    tuple(candidate_rels),
                ).fetchone()
            if not row:
                return None
            project_key = str(row["project_key"])
            rel_path = str(row["rel_path"]).replace("\\", "/").strip("/")
            if rel_path.startswith("./"):
                rel_path = rel_path[2:].strip("/")
            if not project_path:
                try:
                    project_path = str(row["path"] or "").replace("\\", "/").strip("/")
                    if project_path in {".", "./"}:
                        project_path = ""
                    if project_path.startswith("./"):
                        project_path = project_path[2:].strip("/")
                except Exception:
                    project_path = ""
            if project_path and rel_path and not rel_path.startswith(f"{project_path}/") and rel_path != project_path:
                repo_relative_path = f"{project_path}/{rel_path}".strip("/")
            elif rel.startswith("src/") and not rel_path.startswith("src/"):
                repo_relative_path = f"src/{rel_path}"
            elif rel != rel_path and rel.endswith(rel_path):
                repo_relative_path = rel
            else:
                repo_relative_path = rel_path
            context = {
                "file_id": int(row["file_id"]),
                "repo_relative_path": repo_relative_path,
                "atlas_relative_path": rel_path,
                "atlas_node": f"{project_key}::{rel_path}",
                "source": "sqlite_files",
            }
            return context["atlas_node"], context
    except Exception as exc:
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="mcp.server",
                category="storage_fallback",
                operation="sqlite_file_context_lookup",
                subject=target_text,
                reason="SQLite file lookup failed while resolving an agent target.",
                fallback="atlas_payload_lookup",
                claim_impact="target_path_resolution_requires_atlas_fallback",
                exception=exc,
            )
        except Exception:
            pass
        return None


def _find_symbol_matches(query: str, project: str | None = None, raw_dir: Path | None = None) -> list[dict]:
    matches = []
    query_lower = query.lower()
    search_limit = int(_symbol_search_policy()["max_candidate_rows_per_source"])

    def match_score(name: str, file_path: str) -> tuple[int, str]:
        q = query_lower.strip()
        name_l = str(name or "").lower()
        file_l = str(file_path or "").replace("\\", "/").lower()
        filename = Path(file_l).name
        stem = Path(filename).stem
        tokens = [token for token in re.split(r"[^a-z0-9]+", f"{name_l}/{file_l}") if token]
        segments = [segment for segment in file_l.split("/") if segment]
        if q in {name_l, filename, stem}:
            return (0, file_l)
        if q in tokens:
            return (1, file_l)
        if q in {Path(segment).stem for segment in segments}:
            return (2, file_l)
        if name_l.startswith(q) or filename.startswith(q) or stem.startswith(q):
            return (3, file_l)
        if q in name_l or q in file_l:
            return (4, file_l)
        return (99, file_l)

    def repo_relative(project_path: str, rel_path: str) -> str:
        project_path = str(project_path or "").replace("\\", "/").strip("/")
        if project_path in {".", "./"}:
            project_path = ""
        if project_path.startswith("./"):
            project_path = project_path[2:].strip("/")
        rel_path = str(rel_path or "").replace("\\", "/").strip("/")
        if rel_path.startswith("./"):
            rel_path = rel_path[2:].strip("/")
        if project_path and rel_path and rel_path != project_path and not rel_path.startswith(f"{project_path}/"):
            return f"{project_path}/{rel_path}".strip("/")
        return rel_path

    db_path = (raw_dir or RAW_DIR) / "codemaps.db"
    if db_path.exists():
        try:
            like = f"%{query_lower}%"
            project_filter = str(project or "").strip()
            project_filter_active = bool(project_filter and project_filter.lower() not in {"*", "all", "any"})
            with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
                conn.row_factory = sqlite3.Row
                params: list[Any] = [like, like]
                project_clause = ""
                if project_filter_active:
                    project_clause = "AND lower(files.project_key) = lower(?)"
                    params.append(project_filter)
                symbol_rows = conn.execute(
                    f"""
                    SELECT
                        symbols.name,
                        symbols.type,
                        symbols.line,
                        symbols.char,
                        symbols.end_line,
                        symbols.source_lines,
                        files.project_key,
                        files.rel_path,
                        projects.path,
                        (
                            SELECT COUNT(*)
                            FROM dependencies
                            WHERE dependencies.source_file_id = files.file_id
                        ) AS dependency_count
                    FROM symbols
                    JOIN files ON files.file_id = symbols.file_id
                    LEFT JOIN projects ON projects.project_key = files.project_key
                    WHERE (lower(symbols.name) LIKE ? OR lower(files.rel_path) LIKE ?)
                    {project_clause}
                    ORDER BY
                        CASE
                            WHEN lower(symbols.name) = ? THEN 0
                            WHEN lower(files.rel_path) = ? THEN 1
                            ELSE 2
                        END,
                        lower(files.rel_path),
                        symbols.line,
                        lower(symbols.name)
                    LIMIT ?;
                    """,
                    tuple([*params, query_lower, query_lower, search_limit + 1]),
                ).fetchall()
                symbol_rows_truncated = len(symbol_rows) > search_limit
                symbol_rows = symbol_rows[:search_limit]
                for row in symbol_rows:
                    project_key = str(row["project_key"] or "")
                    rel_path = str(row["rel_path"] or "")
                    repo_rel = repo_relative(str(row["path"] or ""), rel_path)
                    score, _sort_path = match_score(str(row["name"] or ""), repo_rel)
                    matches.append(
                        {
                            "name": row["name"],
                            "project": project_key,
                            "file": rel_path,
                            "repo_relative_path": repo_rel,
                            "atlas_node": f"{project_key}::{rel_path}" if project_key and rel_path else "",
                            "type": row["type"],
                            "line": int(row["line"] or 0),
                            "char": int(row["char"] or 0),
                            "end_line": int(row["end_line"] or row["line"] or 0),
                            "source_lines": row["source_lines"],
                            "dependencies": int(row["dependency_count"] or 0),
                            "match_score": score,
                        }
                    )

                file_params: list[Any] = [like]
                if project_filter_active:
                    file_params.append(project_filter)
                file_rows = conn.execute(
                    f"""
                    SELECT files.project_key, files.rel_path, projects.path,
                           (
                               SELECT COUNT(*)
                               FROM dependencies
                               WHERE dependencies.source_file_id = files.file_id
                           ) AS dependency_count
                    FROM files
                    LEFT JOIN projects ON projects.project_key = files.project_key
                    WHERE lower(files.rel_path) LIKE ?
                    {project_clause}
                    LIMIT ?;
                    """,
                    tuple([*file_params, search_limit + 1]),
                ).fetchall()
                file_rows_truncated = len(file_rows) > search_limit
                file_rows = file_rows[:search_limit]
                seen_file_rows = {
                    (str(item.get("project") or ""), str(item.get("file") or ""), str(item.get("type") or ""))
                    for item in matches
                }
                for row in file_rows:
                    project_key = str(row["project_key"] or "")
                    rel_path = str(row["rel_path"] or "")
                    repo_rel = repo_relative(str(row["path"] or ""), rel_path)
                    dedupe_key = (project_key, rel_path, "File")
                    if dedupe_key in seen_file_rows:
                        continue
                    score, _sort_path = match_score(Path(repo_rel).name, repo_rel)
                    matches.append(
                        {
                            "name": Path(repo_rel).name,
                            "project": project_key,
                            "file": rel_path,
                            "repo_relative_path": repo_rel,
                            "atlas_node": f"{project_key}::{rel_path}" if project_key and rel_path else "",
                            "type": "File",
                            "dependencies": int(row["dependency_count"] or 0),
                            "match_score": score,
                        }
                    )
            matches.sort(
                key=lambda row: (
                    int(row.get("match_score") if row.get("match_score") is not None else 99),
                    0 if str(row.get("project") or "").upper() == "MAIN" else 1,
                    str(row.get("repo_relative_path") or row.get("file") or ""),
                    str(row.get("name") or ""),
                )
            )
            search_truncated = symbol_rows_truncated or file_rows_truncated
            for match in matches:
                match["search_truncated"] = search_truncated
            return matches
        except Exception as exc:
            try:
                from tools.core.honesty_telemetry import record_honesty_event

                record_honesty_event(
                    component="mcp.server",
                    category="storage_fallback",
                    operation="sqlite_symbol_search",
                    subject=str(query or ""),
                    reason="SQLite symbol/file search failed while building agent search results.",
                    fallback="atlas_payload_symbol_scan",
                    claim_impact="search_symbols_requires_atlas_fallback",
                    exception=exc,
                )
            except Exception:
                pass

    atlas = _atlas(raw_dir=raw_dir)
    symbol_fallback_matches: list[dict[str, Any]] = []
    file_fallback_matches: list[dict[str, Any]] = []

    for project_key, project_data in atlas.items():
        if project and _normalize_project_name(project, atlas) != project_key:
            continue
        for symbol_info in (project_data.get("symbols") or []):
            if not isinstance(symbol_info, dict):
                continue
            symbol_name = str(symbol_info.get("name") or "")
            if not symbol_name:
                continue
            if query_lower not in symbol_name.lower():
                continue
            file_context = _file_context_from_atlas(project_data, project_key, str(symbol_info.get("file") or ""))
            repo_rel = str(file_context.get("repo_relative_path") or symbol_info.get("file") or "")
            score, _sort_path = match_score(symbol_name, repo_rel)
            symbol_fallback_matches.append(
                {
                    "name": symbol_name,
                    "project": project_key,
                    "file": symbol_info.get("file"),
                    "repo_relative_path": repo_rel,
                    "atlas_node": file_context.get("atlas_node"),
                    "type": symbol_info.get("type"),
                    "dependencies": len(symbol_info.get("dependencies") or []),
                    "match_score": score,
                }
            )
        files = project_data.get("files") or {}
        if isinstance(files, dict):
            for rel_path, file_info in files.items():
                workspace_rel = str((file_info or {}).get("workspace_rel") or rel_path) if isinstance(file_info, dict) else str(rel_path)
                if query_lower not in str(rel_path).lower() and query_lower not in workspace_rel.lower():
                    continue
                file_context = _file_context_from_atlas(project_data, project_key, str(rel_path))
                repo_rel = str(file_context.get("repo_relative_path") or workspace_rel)
                score, _sort_path = match_score(Path(workspace_rel).name, repo_rel)
                file_fallback_matches.append(
                    {
                        "name": Path(workspace_rel).name,
                        "project": project_key,
                        "file": rel_path,
                        "repo_relative_path": repo_rel,
                        "atlas_node": file_context.get("atlas_node"),
                        "type": "File",
                        "dependencies": len((file_info or {}).get("imports") or []) if isinstance(file_info, dict) else 0,
                        "match_score": score,
                    }
                )
    search_truncated = (
        len(symbol_fallback_matches) > search_limit
        or len(file_fallback_matches) > search_limit
    )
    matches = symbol_fallback_matches[:search_limit] + file_fallback_matches[:search_limit]
    matches.sort(
        key=lambda row: (
            int(row.get("match_score") if row.get("match_score") is not None else 99),
            0 if str(row.get("project") or "").upper() == "MAIN" else 1,
            str(row.get("repo_relative_path") or row.get("file") or ""),
            str(row.get("name") or ""),
        )
    )
    for match in matches:
        match["search_truncated"] = search_truncated
    return matches


def _render_symbol_search_brief(query: str, matches: list[dict], analysis_root: str = "") -> str:
    display_root = analysis_root or _analysis_root_display()
    search_truncated = any(bool(row.get("search_truncated")) for row in matches)
    yaml_lines = [
        "mission:",
        "  - Use these target-repository search results to choose the smallest safe inspection scope.",
        "task:",
        "  analysis_root: " + json.dumps(display_root, ensure_ascii=False),
        f"  query: {json.dumps(query, ensure_ascii=False)}",
        f"  returned: {len(matches)}",
        f"  search_truncated: {str(search_truncated).lower()}",
        f"  returned_count_semantics: {json.dumps('lower_bound' if search_truncated else 'complete_match_set', ensure_ascii=False)}",
        "path_contract:",
        "  open_files_with: \"analysis_root + file\"",
        "  sage_refs_only_for: \"MCP follow-up calls, never filesystem access\"",
        "matches:",
    ]
    if matches:
        for row in matches[:10]:
            target_file = row.get("repo_relative_path") or row.get("file") or ""
            project = row.get("project") or ""
            target_ref = f"{project}::{target_file}" if project and target_file else target_file
            match_score = int(row.get("match_score") if row.get("match_score") is not None else 99)
            yaml_lines.append("  - name: " + json.dumps(row.get("name") or "", ensure_ascii=False))
            yaml_lines.append("    type: " + json.dumps(row.get("type") or "", ensure_ascii=False))
            yaml_lines.append("    project: " + json.dumps(project, ensure_ascii=False))
            yaml_lines.append("    file: " + json.dumps(target_file, ensure_ascii=False))
            yaml_lines.append("    target_ref: " + json.dumps(target_ref, ensure_ascii=False))
            yaml_lines.append("    match_quality: " + json.dumps("exact_or_token" if match_score <= 2 else "substring", ensure_ascii=False))
            if int(row.get("line") or 0) > 0:
                yaml_lines.append(f"    line: {int(row.get('line') or 0)}")
                if int(row.get("end_line") or 0) > 0:
                    yaml_lines.append(f"    end_line: {int(row.get('end_line') or row.get('line') or 0)}")
                if row.get("source_lines"):
                    yaml_lines.append("    source_lines: " + json.dumps(row.get("source_lines") or "", ensure_ascii=False))
                yaml_lines.append(f"    char: {int(row.get('char') or 0)}")
            yaml_lines.append(f"    dependencies: {int(row.get('dependencies') or 0)}")
            yaml_lines.append("    next_tool: " + json.dumps(f"inspect_file(file_path={json.dumps(target_ref, ensure_ascii=False)})", ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    yaml_lines.extend(
        [
            "next_step:",
            "  - Pick one returned target_ref, call inspect_file(file_path=target_ref) for scoped evidence, then open the returned file path under the analyzed repository root.",
            "  - If no match is returned, verify the path/query or refresh the target analysis.",
            "do_not:",
            "  - Do not edit from search results alone.",
            "  - Do not broaden scope without inspecting the chosen file or symbol first.",
        ]
    )
    return "\n".join(
        [
            "# Target Search Brief",
            "",
            "Use this brief to pick a target-repository file or symbol before inspection.",
            "",
            "```yaml",
            *_with_context_budget(yaml_lines),
            "```",
            "",
        ]
    )


def _missing_target_artifact_brief(tool_name: str, target: str, target_root: str, missing_artifacts: list[str]) -> str:
    yaml_lines = [
        "mission:",
        "  - Do not infer this result from another repository or stale workspace artifacts.",
        "task:",
        f"  tool: {json.dumps(tool_name, ensure_ascii=False)}",
        f"  target: {json.dumps(target, ensure_ascii=False)}",
        f"  target_root: {json.dumps(target_root or '<current SAGE workspace>', ensure_ascii=False)}",
        "status: missing_required_target_artifacts",
        "missing_artifacts:",
        *[f"  - {json.dumps(item, ensure_ascii=False)}" for item in missing_artifacts],
        "next_step:",
        "  - Run or refresh analysis for this exact target_root before relying on this tool.",
        "  - If this is an external repository, call run_external_target_analysis(target_root=..., full=true).",
        "do_not:",
        "  - Do not use impact, test, confidence, or upstream data from the SAGE source workspace for this target repository.",
    ]
    return "\n".join(["# Target Evidence Missing", "", "```yaml", *_with_context_budget(yaml_lines), "```", ""])


def _invalid_external_target_brief(tool_name: str, target_root: str) -> str:
    yaml_lines = [
        "mission:",
        "  - Do not analyze or edit a repository until the target root is a real local directory.",
        "task:",
        f"  tool: {json.dumps(tool_name, ensure_ascii=False)}",
        f"  target_root: {json.dumps(target_root or '', ensure_ascii=False)}",
        "  status: invalid_external_target_root",
        "  target_exists: false",
        "next_step:",
        "  - Provide an absolute or current-workspace-relative path to the repository root.",
        "  - Run external_target_preflight(target_root=...) before running analysis.",
        "do_not:",
        "  - Do not fall back to the current SAGE workspace for this external target.",
        "  - Do not infer target repository facts from stale or unrelated artifacts.",
    ]
    return "\n".join(["# Invalid External Target", "", "```yaml", *_with_context_budget(yaml_lines), "```", ""])


def _resolve_target_node_from_raw(raw_dir: Path, target: str, *, allow_atlas_fallback: bool = True) -> tuple[str, dict[str, Any]]:
    target_text = str(target or "").replace("\\", "/").strip().strip("/")
    sqlite_context = _sqlite_file_context_from_raw(raw_dir, target_text)
    if sqlite_context is not None:
        return sqlite_context
    if not allow_atlas_fallback:
        if "::" in target_text:
            project_key, rel = target_text.split("::", 1)
        else:
            project_key, rel = "MAIN", target_text
        rel = str(rel or "").replace("\\", "/").strip().strip("/")
        return f"{project_key}::{rel}", {
            "repo_relative_path": rel,
            "atlas_relative_path": rel,
            "atlas_node": f"{project_key}::{rel}",
            "source": "sqlite_files_miss",
        }
    atlas = _atlas(raw_dir=raw_dir)
    if "::" in target_text:
        project, rel = target_text.split("::", 1)
        project_key = _normalize_project_name(project, atlas)
        project_data = atlas.get(project_key, {})
        context = _file_context_from_atlas(project_data, project_key, rel)
        return context.get("atlas_node") or f"{project_key}::{rel}", context
    for project_key, project_data in atlas.items():
        files = project_data.get("files") or {}
        if not isinstance(files, dict):
            continue
        for rel_path, file_info in files.items():
            workspace_rel = str((file_info or {}).get("workspace_rel") or rel_path).replace("\\", "/").strip("/")
            if target_text in {str(rel_path).replace("\\", "/").strip("/"), workspace_rel}:
                context = _file_context_from_atlas(project_data, project_key, str(rel_path))
                return context.get("atlas_node") or f"{project_key}::{rel_path}", context
    project_key = _host_key(atlas)
    project_data = atlas.get(project_key, {})
    context = _file_context_from_atlas(project_data, project_key, target_text)
    return context.get("atlas_node") or f"{project_key}::{target_text}", context


def _dependency_graph_payload_from_atlas(raw_dir: Path) -> dict[str, Any]:
    atlas = _atlas(raw_dir=raw_dir)
    nodes: dict[str, Any] = {}
    edges: list[dict[str, Any]] = []
    atlas_nodes: set[str] = set()
    for project, project_data in atlas.items():
        if not isinstance(project_data, dict):
            continue
        files = project_data.get("files") or {}
        if not isinstance(files, dict):
            continue
        for rel_path, file_info in files.items():
            atlas_rel = str(rel_path).replace("\\", "/").strip("/")
            if not atlas_rel:
                continue
            source_node = f"{project}::{atlas_rel}"
            atlas_nodes.add(source_node)
            nodes[source_node] = {"source": "atlas", "file": atlas_rel, "project": project}
            imports = []
            if isinstance(file_info, dict):
                imports = file_info.get("internal_deps") or file_info.get("imports") or []
            for dep in imports if isinstance(imports, list) else []:
                dep_rel = str(dep or "").replace("\\", "/").strip("/")
                if not dep_rel or "://" in dep_rel:
                    continue
                target_node = dep_rel if "::" in dep_rel else f"{project}::{dep_rel}"
                edges.append({"source": source_node, "target": target_node, "kind": "atlas_import"})
                atlas_nodes.add(target_node)
                nodes.setdefault(target_node, {"source": "atlas", "file": dep_rel.split("::", 1)[-1], "project": project})
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "dependency_graph_source": "atlas_imports_fallback",
            "limits": [
                "Derived from Atlas internal imports when circular_deps.json is not available.",
                "Use for static dependency orientation; cycle classification may require the circular dependency engine.",
            ],
            "atlas_node_count": len(atlas_nodes),
            "edge_count": len(edges),
        },
    }


def _dependency_graph_payload_from_raw(raw_dir: Path) -> dict[str, Any]:
    deps_path = raw_dir / "circular_deps.json"
    deps = _load_json(deps_path) or {}
    if isinstance(deps, dict) and deps:
        deps.setdefault("meta", {})
        if isinstance(deps.get("meta"), dict):
            deps["meta"].setdefault("dependency_graph_source", "circular_deps")
        return deps
    return _dependency_graph_payload_from_atlas(raw_dir)


def _dependency_graph_source(raw_dir: Path) -> str:
    payload = _dependency_graph_payload_from_raw(raw_dir)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return str(meta.get("dependency_graph_source") or "unknown")


def _dependency_graph_from_raw(raw_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    deps_path = raw_dir / "circular_deps.json"
    atlas_path = raw_dir / "atlas.json"
    deps_payload = _load_json(deps_path)
    cache_key = _artifact_cache_key(deps_path if isinstance(deps_payload, dict) and deps_payload else atlas_path)
    cached = _GRAPH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    deps = _dependency_graph_payload_from_raw(raw_dir)
    nodes = deps.get("nodes") if isinstance(deps, dict) else {}
    edges = deps.get("edges") if isinstance(deps, dict) else []
    nodes = nodes if isinstance(nodes, dict) else {}
    edges = edges if isinstance(edges, list) else []
    reverse: dict[str, list[str]] = {str(node): [] for node in nodes}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target:
            reverse.setdefault(target, []).append(source)
            reverse.setdefault(source, reverse.get(source, []))
    result = (nodes, edges, reverse)
    _GRAPH_CACHE.clear()
    _GRAPH_CACHE[cache_key] = result
    return result


def _reachable_dependents(start_node: str, reverse: dict[str, list[str]]) -> list[str]:
    visited: set[str] = set()
    queue = [start_node]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        queue.extend(reverse.get(current, []))
    return sorted(node for node in visited if node != start_node)


def _reachable_dependents_limited(start_node: str, reverse: dict[str, list[str]], max_depth: int) -> list[str]:
    if max_depth <= 0:
        return _reachable_dependents(start_node, reverse)
    visited: set[str] = set()
    returned: set[str] = set()
    queue: list[tuple[str, int]] = [(start_node, 0)]
    while queue:
        current, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if depth >= max_depth:
            continue
        for dependent in reverse.get(current, []):
            returned.add(dependent)
            queue.append((dependent, depth + 1))
    returned.discard(start_node)
    return sorted(returned)


def _precomputed_blast_counts(raw_dir: Path, resolved_node: str) -> dict[str, int]:
    payload = _load_json(raw_dir / "blast_radius.json") or {}
    rows = payload.get("blast_radius") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("file") or "") == resolved_node:
            return {
                "direct": int(row.get("direct_dependents") or 0),
                "transitive": int(row.get("transitive_dependents") or 0),
            }
    return {}


def _repo_relative_from_node(raw_dir: Path, node: str) -> str:
    project, rel = node.split("::", 1) if "::" in node else ("", node)
    sqlite_context = _sqlite_file_context_from_raw(raw_dir, node)
    if sqlite_context is not None:
        _resolved_node, context = sqlite_context
        return str(context.get("repo_relative_path") or rel)
    atlas = _atlas(raw_dir=raw_dir)
    project_data = atlas.get(project, {}) if project else {}
    return str(_file_context_from_atlas(project_data, project, rel).get("repo_relative_path") or rel)


def _target_ref_from_context(resolved_node: str, context: dict[str, Any]) -> str:
    project, rel = resolved_node.split("::", 1) if "::" in str(resolved_node or "") else ("", str(resolved_node or ""))
    repo_rel = str((context or {}).get("repo_relative_path") or rel).replace("\\", "/").strip("/")
    return repair_text(f"{project}::{repo_rel}" if project else repo_rel)


def _target_ref_from_node(raw_dir: Path, node: str) -> str:
    project, _rel = node.split("::", 1) if "::" in node else ("", node)
    repo_rel = _repo_relative_from_node(raw_dir, node)
    return repair_text(f"{project}::{repo_rel}" if project else repo_rel)


def _target_file_from_ref(raw_dir: Path, target_ref: str) -> str:
    if "::" not in str(target_ref or ""):
        return repair_text(str(target_ref or ""))
    return repair_text(_repo_relative_from_node(raw_dir, target_ref))


def _split_target_ref(target_ref: str) -> tuple[str, str]:
    text = str(target_ref or "").replace("\\", "/").strip()
    if "::" not in text:
        return "", text.strip("/")
    project, rel_path = text.split("::", 1)
    return project.strip(), rel_path.strip("/")


def _safe_positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _bounded_source_snippets(
    content: str,
    spans: list[dict[str, Any]],
    *,
    max_snippets: int = 3,
    max_lines_per_snippet: int = 40,
    max_total_chars: int = 4000,
) -> list[dict[str, Any]]:
    """Return small, line-numbered source excerpts for agent surgery packets."""
    if not content:
        return []
    source_lines = content.splitlines()
    if not spans:
        selected = source_lines[: min(len(source_lines), max_lines_per_snippet)]
        numbered = "\n".join(f"{1 + idx}: {line}" for idx, line in enumerate(selected))
        if not numbered.strip():
            return []
        return [
            {
                "symbol": "file_start",
                "source_lines": f"L1-L{len(selected)}",
                "snippet_status": "included_no_symbol_span",
                "line_count": len(selected),
                "code": numbered[:max_total_chars],
            }
        ]
    snippets: list[dict[str, Any]] = []
    seen_ranges: set[tuple[int, int]] = set()
    used_chars = 0
    for span in spans:
        if len(snippets) >= max_snippets or not isinstance(span, dict):
            break
        start_line = int(span.get("start_line") or 0)
        end_line = int(span.get("end_line") or start_line or 0)
        if start_line <= 0 or end_line < start_line:
            continue
        coordinate = (start_line, end_line)
        if coordinate in seen_ranges:
            continue
        seen_ranges.add(coordinate)
        line_count = end_line - start_line + 1
        if line_count > max_lines_per_snippet:
            head_count = max_lines_per_snippet
            tail_count = 0
            if line_count >= max_lines_per_snippet * 3 and max_lines_per_snippet >= 20:
                tail_count = max(8, max_lines_per_snippet // 4)
                head_count = max_lines_per_snippet - tail_count
            head = source_lines[start_line - 1 : start_line - 1 + head_count]
            tail_start_line = max(start_line + head_count, end_line - tail_count + 1) if tail_count else 0
            tail = source_lines[tail_start_line - 1 : end_line] if tail_start_line else []
            numbered_parts = [f"{start_line + idx}: {line}" for idx, line in enumerate(head)]
            omitted_between = max(0, (tail_start_line - start_line - head_count) if tail_start_line else 0)
            if tail and omitted_between > 0:
                numbered_parts.append(f"... {omitted_between} omitted lines inside large symbol ...")
                numbered_parts.extend(f"{tail_start_line + idx}: {line}" for idx, line in enumerate(tail))
            selected_line_count = len(head) + len(tail)
            numbered = "\n".join(numbered_parts)
            projected_chars = used_chars + len(numbered)
            target_ref = str(span.get("target_ref") or "").strip()
            next_start = min(end_line, start_line + head_count)
            next_end = min(end_line, next_start + max_lines_per_snippet - 1)
            omitted_end_line = (tail_start_line - 1) if tail_start_line else end_line
            omitted_range = f"L{next_start}-L{omitted_end_line}" if next_start <= omitted_end_line else ""
            follow_up_if_needed = (
                f"Call inspect_file(file_path={json.dumps(target_ref)}, line_start={next_start}, line_end={next_end}) "
                "only if you need to inspect that omitted body chunk before editing outside the shown boundary snippet."
                if target_ref and next_start <= end_line
                else "Call inspect_file(file_path=target_ref, line_start=<needed_start>, line_end=<needed_end>) only for the omitted body chunk needed by the intended edit."
            )
            if projected_chars > max_total_chars:
                snippets.append(
                    {
                        "symbol": span.get("symbol") or "",
                        "source_lines": span.get("source_lines") or f"L{start_line}-L{end_line}",
                        "snippet_status": "omitted_context_budget",
                        "line_count": line_count,
                        "max_total_chars": max_total_chars,
                    }
                )
                break
            snippets.append(
                {
                    "symbol": span.get("symbol") or "",
                    "source_lines": span.get("source_lines") or f"L{start_line}-L{end_line}",
                    "snippet_status": "partial_included_span_too_large",
                    "line_count": line_count,
                    "max_lines": max_lines_per_snippet,
                    "shown_lines": selected_line_count,
                    "omitted_lines": max(0, line_count - selected_line_count),
                    "omitted_range": omitted_range,
                    "next_chunk_lines": f"L{next_start}-L{next_end}" if next_start <= end_line else "",
                    "one_shot_edit_ready": False,
                    "snippet_role": "orientation",
                    "snippet_strategy": "boundary_slice_for_large_symbol" if tail else "first_slice_for_orientation",
                    "snippet_purpose": "orientation_not_complete_edit_context",
                    "omitted_context_policy": "do_not_read_or_edit_all_omitted_lines_by_default",
                    "edit_scope": "do_not_edit_omitted_lines_without_follow_up",
                    "follow_up_if_needed": follow_up_if_needed,
                    "code": numbered,
                }
            )
            used_chars = projected_chars
            continue
        selected = source_lines[start_line - 1 : end_line]
        numbered = "\n".join(f"{start_line + idx}: {line}" for idx, line in enumerate(selected))
        if not numbered.strip():
            continue
        projected_chars = used_chars + len(numbered)
        if projected_chars > max_total_chars:
            snippets.append(
                {
                    "symbol": span.get("symbol") or "",
                    "source_lines": span.get("source_lines") or f"L{start_line}-L{end_line}",
                    "snippet_status": "omitted_context_budget",
                    "line_count": line_count,
                    "max_total_chars": max_total_chars,
                }
            )
            break
        snippets.append(
            {
                "symbol": span.get("symbol") or "",
                "source_lines": span.get("source_lines") or f"L{start_line}-L{end_line}",
                "snippet_status": "included",
                "code": numbered,
            }
        )
        used_chars = projected_chars
    return snippets


def _source_snapshot_content_for_ref(raw_dir: Path, target_ref: str) -> str:
    project_key, rel_path = _split_target_ref(target_ref)
    try:
        _resolved_node, context = _sqlite_file_context_from_raw(raw_dir, target_ref)
        project_key = str(context.get("atlas_node") or "").split("::", 1)[0] or project_key
        rel_path = str(context.get("atlas_relative_path") or rel_path)
    except Exception:
        pass
    if not project_key or not rel_path:
        return ""
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return ""
    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            row = conn.execute(
                """
                SELECT content
                FROM source_snapshots
                WHERE project_key = ? AND rel_path = ? AND status = 'ok'
                LIMIT 1;
                """,
                (project_key, rel_path.replace("\\", "/").strip("/")),
            ).fetchone()
        return str(row[0] or "") if row else ""
    except Exception as exc:
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="mcp.server",
                category="caught_error",
                operation="source_snapshot_content_for_ref",
                subject=target_ref,
                reason="SQLite source snapshot content lookup failed while building evidence snippets.",
                fallback="omit_evidence_source_snippets",
                claim_impact="agent_may_need_target_file_inspection_for_evidence_line",
                exception=exc,
            )
        except Exception:
            pass
        return ""


def _evidence_source_snippets(content: str, evidence_items: list[str], *, max_snippets: int = 3) -> list[dict[str, Any]]:
    if not content or not evidence_items:
        return []
    lines = content.splitlines()
    snippets: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for evidence in evidence_items:
        if len(snippets) >= max_snippets:
            break
        evidence_text = str(evidence or "").strip()
        if not evidence_text:
            continue
        needles: list[str] = []
        if " imports " in evidence_text:
            imported = import_specifier_from_audit_detail(evidence_text)
            if imported:
                needles.append(imported)
        symbol_match = re.search(r"\bsymbol:([A-Za-z_$][\w.$]*)", evidence_text)
        if symbol_match:
            symbol = symbol_match.group(1)
            needles.extend(
                [
                    f"def {symbol}",
                    f"class {symbol}",
                    f"function {symbol}",
                    f"const {symbol}",
                    f"let {symbol}",
                    f"var {symbol}",
                ]
            )
        needles.extend(re.findall(r"['\"]([^'\"]+)['\"]", evidence_text))
        if not needles:
            continue
        for line_no, line in enumerate(lines, start=1):
            if line_no in seen_lines:
                continue
            if not any(needle and needle in line for needle in needles):
                continue
            start = max(1, line_no - 1)
            end = min(len(lines), line_no + 1)
            selected = lines[start - 1 : end]
            numbered = "\n".join(f"{start + idx}: {value}" for idx, value in enumerate(selected))
            snippets.append(
                {
                    "evidence": evidence_text,
                    "source_lines": f"L{start}-L{end}",
                    "matched_line": line_no,
                    "snippet_status": "included_evidence_line",
                    "code": numbered,
                }
            )
            seen_lines.add(line_no)
            break
    return snippets


def _line_evidence_snippets(
    content: str,
    needles: list[str],
    *,
    evidence_label: str,
    status: str,
    max_snippets: int = 2,
    context_lines: int = 1,
    import_lines_only: bool = False,
) -> list[dict[str, Any]]:
    if not content or not needles:
        return []
    lines = content.splitlines()
    snippets: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    lowered_needles = [needle.lower() for needle in needles if str(needle or "").strip()]
    if not lowered_needles:
        return []
    for line_no, line in enumerate(lines, start=1):
        if len(snippets) >= max_snippets:
            break
        if line_no in seen_lines:
            continue
        lowered_line = line.lower()
        if import_lines_only and not (
            re.match(r"^\s*(?:from\s+\S+\s+import\b|import\s+)", line)
            or re.search(r"\b(?:from\s+['\"]|require\s*\(|import\s*\()", line)
        ):
            continue
        if not any(needle in lowered_line for needle in lowered_needles):
            continue
        start = max(1, line_no - max(0, context_lines))
        end = min(len(lines), line_no + max(0, context_lines))
        selected = lines[start - 1 : end]
        numbered = "\n".join(f"{start + idx}: {value}" for idx, value in enumerate(selected))
        snippets.append(
            {
                "evidence": evidence_label,
                "source_lines": f"L{start}-L{end}",
                "matched_line": line_no,
                "snippet_status": status,
                "code": numbered,
            }
        )
        seen_lines.add(line_no)
    return snippets


def _test_source_snippets(
    content: str,
    test_file: str,
    *,
    target_file: str = "",
    target_import_needles: list[str] | None = None,
    target_symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    base = _base_without_test_suffix(test_file)
    exact_import_needles = [
        str(value or "").strip()
        for value in target_import_needles or []
        if str(value or "").strip()
    ]
    if exact_import_needles:
        snippets = _line_evidence_snippets(
            content,
            exact_import_needles,
            evidence_label=f"bounded test target-import evidence for {test_file}",
            status="included_test_target_import_line",
            max_snippets=2,
            context_lines=1,
            import_lines_only=True,
        )
        if snippets:
            return snippets
    target_needles: list[str] = []
    target_path = str(target_file or "").replace("\\", "/").strip("/")
    if target_path:
        target_without_ext = re.sub(r"\.[^.\/]+$", "", target_path)
        target_needles.extend(
            [
                target_path,
                target_without_ext,
                f"@/{target_without_ext}",
            ]
        )
    for symbol in target_symbols or []:
        symbol_text = str(symbol or "").strip()
        if symbol_text and symbol_text not in target_needles:
            target_needles.append(symbol_text)
    if target_needles:
        snippets = _line_evidence_snippets(
            content,
            target_needles,
            evidence_label=f"bounded test target-link evidence for {test_file}",
            status="included_test_target_link_line",
            max_snippets=2,
            context_lines=1,
        )
        if snippets:
            return snippets
    assertion_needles = [
        "describe(",
        "it(",
        "test(",
        "expect(",
        "assert",
        "render(",
    ]
    snippets = _line_evidence_snippets(
        content,
        assertion_needles,
        evidence_label=f"bounded test assertion/context evidence for {test_file}",
        status="included_test_evidence_line",
        max_snippets=2,
        context_lines=1,
    )
    if snippets:
        return snippets
    fallback_needles = [
        base,
    ]
    return _line_evidence_snippets(
        content,
        fallback_needles,
        evidence_label=f"bounded test import evidence for {test_file}",
        status="included_test_evidence_line",
        max_snippets=2,
        context_lines=1,
    )


def _target_import_needles_from_file_info(file_info: dict[str, Any], target_file: str) -> list[str]:
    target_path = str(target_file or "").replace("\\", "/").strip("/")
    target_without_ext = re.sub(r"\.[^.\/]+$", "", target_path)
    if not target_without_ext:
        return []
    needles: list[str] = []
    for record in file_info.get("import_records") or []:
        if not isinstance(record, dict):
            continue
        source = str(record.get("source") or "").replace("\\", "/").strip("/")
        source_without_ext = re.sub(r"\.[^.\/]+$", "", source)
        if source != target_path and source_without_ext != target_without_ext:
            continue
        raw_source = str(record.get("raw_source") or "").strip()
        if raw_source and raw_source not in needles:
            needles.append(raw_source)
    return needles


def _attach_test_source_snippets(raw_dir: Path, payload: dict[str, Any], *, max_tests_with_snippets: int = 3) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    project = str(payload.get("target_project") or _project_from_ref(str(payload.get("target_ref") or "")) or "MAIN")
    tests = payload.get("impacted_tests") if isinstance(payload.get("impacted_tests"), list) else []
    if not tests:
        return payload
    normalized_tests: list[dict[str, Any]] = []
    snippets_attached = 0
    target_status = payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}
    target_file = str(payload.get("target_file") or payload.get("target") or "").replace("\\", "/").strip("/")
    target_symbols = [
        str(span.get("symbol") or "")
        for span in (target_status.get("target_spans") if isinstance(target_status.get("target_spans"), list) else [])
        if isinstance(span, dict) and span.get("symbol")
    ]
    atlas = _atlas(raw_dir=raw_dir)
    for row in tests:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        test_file = str(item.get("repo_relative_path") or item.get("file") or "").replace("\\", "/").strip("/")
        if test_file and snippets_attached < max_tests_with_snippets:
            content = _source_snapshot_content_for_ref(raw_dir, f"{project}::{test_file}")
            project_files = (atlas.get(project) or {}).get("files") or {}
            file_info = next(
                (
                    value
                    for rel_path, value in project_files.items()
                    if isinstance(value, dict)
                    and test_file
                    in {
                        str(rel_path).replace("\\", "/").strip("/"),
                        str(value.get("repo_relative_path") or "").replace("\\", "/").strip("/"),
                        str(value.get("workspace_rel") or "").replace("\\", "/").strip("/"),
                    }
                ),
                {},
            )
            snippets = _test_source_snippets(
                content,
                test_file,
                target_file=target_file,
                target_import_needles=_target_import_needles_from_file_info(file_info, target_file),
                target_symbols=target_symbols,
            )
            if snippets:
                item["source_snippets"] = snippets
                item["source_snippet_status"] = "included"
                snippets_attached += 1
            else:
                item["source_snippet_status"] = "not_found_in_source_snapshot"
                item["source_snippet_note"] = "No bounded import/assertion evidence line was found in the source snapshot; use the run command and inspect the test file if needed."
        elif test_file:
            item["source_snippet_status"] = "omitted_context_budget"
            item["source_snippet_note"] = "Snippet omitted because this test is outside the bounded test-source snippet budget; run the command or inspect this test only if earlier listed evidence is insufficient."
        normalized_tests.append(item)
    payload = dict(payload)
    payload["impacted_tests"] = normalized_tests
    payload["test_source_snippet_limit"] = max_tests_with_snippets
    payload["test_source_snippets_attached"] = snippets_attached
    payload["test_source_snippets_omitted"] = max(0, len(tests) - snippets_attached)
    return payload


def _dependency_import_evidence(raw_dir: Path, source_ref: str) -> dict[str, str]:
    try:
        _source_node, source_context = _sqlite_file_context_from_raw(raw_dir, source_ref)
        source_file_id = int(source_context.get("file_id") or 0)
    except Exception:
        return {}
    if not source_file_id:
        return {}
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return {}
    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT tf.project_key, tf.rel_path, tp.path AS project_path, d.import_specifier
                FROM dependencies d
                JOIN files tf ON tf.file_id = d.target_file_id
                LEFT JOIN projects tp ON tp.project_key = tf.project_key
                WHERE d.source_file_id = ?
                ORDER BY tf.project_key, tf.rel_path;
                """,
                (source_file_id,),
            ).fetchall()
    except Exception:
        return {}
    evidence: dict[str, str] = {}
    for row in rows:
        repo_rel = _repo_relative_from_sqlite_row(str(row["project_path"] or ""), str(row["rel_path"] or ""))
        target_ref = f"{row['project_key']}::{repo_rel}"
        import_specifier = str(row["import_specifier"] or "").strip()
        if target_ref and import_specifier:
            evidence[target_ref] = import_specifier
    return evidence


def _attach_upstream_dependency_snippets(raw_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    target_ref = str(payload.get("target_ref") or payload.get("target") or "")
    target_file = str(payload.get("target_file") or target_ref.split("::", 1)[-1]).replace("\\", "/").strip("/")
    project = str(payload.get("target_project") or _project_from_ref(target_ref) or "MAIN")
    if not target_ref:
        return payload
    content = _source_snapshot_content_for_ref(raw_dir, target_ref)
    import_map = _dependency_import_evidence(raw_dir, target_ref)
    upstream_files = payload.get("upstream_dependency_files") if isinstance(payload.get("upstream_dependency_files"), list) else []
    evidence_rows: list[dict[str, Any]] = []
    for path in upstream_files[:8]:
        file_path, candidate_ref = _agent_file_ref(str(path), project)
        import_specifier = import_map.get(candidate_ref, "")
        needles = []
        if import_specifier:
            normalized_spec = import_specifier.replace("\\", "/").strip()
            no_ext = re.sub(r"\.(tsx|ts|jsx|js|css|scss|sass|json)$", "", normalized_spec)
            basename = Path(no_ext).name
            needles.extend([normalized_spec, no_ext, basename])
            if not normalized_spec.startswith((".", "@", "/")):
                needles.append(f"@/{no_ext.lstrip('/')}")
        snippets = _line_evidence_snippets(
            content,
            needles,
            evidence_label=f"{target_file} imports {import_specifier}",
            status="included_dependency_evidence_line",
            max_snippets=1,
            context_lines=1,
        )
        if snippets:
            evidence_rows.append(
                {
                    "file": file_path,
                    "target_ref": candidate_ref,
                    "import_specifier": import_specifier,
                    "source_snippets": snippets,
                    "snippet_status": "included_dependency_evidence_line",
                }
            )
    payload = dict(payload)
    payload["upstream_dependency_evidence"] = evidence_rows
    payload["upstream_dependency_evidence_omitted"] = max(0, len(upstream_files[:8]) - len(evidence_rows))
    return payload


def _source_grounding_status(
    raw_dir: Path,
    context: dict[str, Any],
    target_abs: Path,
    *,
    preferred_symbols: set[str] | None = None,
) -> dict[str, Any]:
    db_path = raw_dir / "codemaps.db"
    file_id = int((context or {}).get("file_id") or 0)
    project_key = str((context or {}).get("atlas_node") or "").split("::", 1)[0]
    rel_path = str((context or {}).get("atlas_relative_path") or "").replace("\\", "/").strip("/")
    repo_rel_path = str((context or {}).get("repo_relative_path") or rel_path).replace("\\", "/").strip("/")
    agent_target_ref = f"{project_key}::{repo_rel_path}" if project_key and repo_rel_path else repo_rel_path
    if not db_path.exists() or not file_id or not project_key or not rel_path:
        return {
            "source_snapshot_status": "missing",
            "source_snapshot_hash": "",
            "drift_check_status": "not_available",
            "target_spans": [],
            "target_span_count": 0,
        }

    snapshot_status = "missing"
    snapshot_hash = ""
    snapshot_mtime = 0.0
    snapshot_content = ""
    target_spans: list[dict[str, Any]] = []
    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            conn.row_factory = sqlite3.Row
            snapshot = conn.execute(
                """
                SELECT content, content_hash, status, source_mtime
                FROM source_snapshots
                WHERE project_key = ? AND rel_path = ?
                LIMIT 1;
                """,
                (project_key, rel_path),
            ).fetchone()
            if snapshot:
                snapshot_content = str(snapshot["content"] or "")
                snapshot_status = str(snapshot["status"] or "unknown")
                snapshot_hash = str(snapshot["content_hash"] or "")
                snapshot_mtime = float(snapshot["source_mtime"] or 0.0)
            symbol_names = sorted(str(item) for item in (preferred_symbols or set()) if str(item).strip())
            if symbol_names:
                placeholders = ",".join("?" for _ in symbol_names)
                rows = conn.execute(
                    f"""
                    SELECT name, type, line, char, end_line, source_lines
                    FROM symbols
                    WHERE file_id = ? AND name IN ({placeholders})
                    ORDER BY line, name
                    LIMIT 12;
                    """,
                    (file_id, *symbol_names),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT name, type, line, char, end_line, source_lines
                    FROM symbols
                    WHERE file_id = ?
                    ORDER BY line, name
                    LIMIT 12;
                    """,
                    (file_id,),
                ).fetchall()
            for row in rows:
                line = int(row["line"] or 0)
                end_line = int(row["end_line"] or line or 0)
                target_spans.append(
                    {
                        "symbol": row["name"],
                        "type": row["type"],
                        "target_ref": agent_target_ref,
                        "start_line": line,
                        "end_line": end_line,
                        "char": int(row["char"] or 0),
                        "source_lines": row["source_lines"] or (f"L{line}-L{end_line}" if line else ""),
                        "line_status": "available" if line else "not_available",
                    }
                )
    except Exception as exc:
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="mcp.server",
                category="storage_fallback",
                operation="source_grounding_status",
                subject=f"{project_key}::{rel_path}",
                reason="SQLite source snapshot or symbol span lookup failed while grounding an agent target.",
                fallback="path_status_without_source_grounding",
                claim_impact="agent_target_drift_status_unavailable",
                exception=exc,
            )
        except Exception:
            pass
        return {
            "source_snapshot_status": "error",
            "source_snapshot_hash": "",
            "source_snapshot_mtime": 0.0,
            "drift_check_status": "not_available",
            "target_spans": [],
            "target_span_count": 0,
        }

    drift_status = "not_available"
    current_hash = ""
    try:
        if target_abs.exists() and target_abs.is_file() and target_abs.stat().st_size <= 2_000_000:
            current_bytes = target_abs.read_bytes()
            current_content = target_abs.read_text(encoding="utf-8", errors="replace")
            if snapshot_hash:
                current_hash_candidates = {
                    hashlib.sha256(current_bytes).hexdigest(),
                    hashlib.sha256(current_content.encode("utf-8")).hexdigest(),
                    hashlib.sha1(current_bytes).hexdigest(),
                    hashlib.sha1(current_content.encode("utf-8")).hexdigest(),
                    hashlib.md5(current_bytes).hexdigest(),
                    hashlib.md5(current_content.encode("utf-8")).hexdigest(),
                }
                if snapshot_hash in current_hash_candidates:
                    current_hash = snapshot_hash
                    drift_status = "match"
                elif len(snapshot_hash) in {32, 40, 64}:
                    current_hash = sorted(current_hash_candidates, key=lambda item: (len(item) != len(snapshot_hash), item))[0]
                    drift_status = "mismatch"
                else:
                    drift_status = "not_comparable_unknown_hash_algorithm"
            else:
                drift_status = "no_snapshot_hash"
        elif target_abs.exists() and target_abs.is_file():
            drift_status = "not_checked_too_large"
        else:
            drift_status = "target_missing"
    except Exception as exc:
        drift_status = "error"
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="mcp.server",
                category="caught_error",
                operation="source_grounding_live_hash",
                subject=f"{project_key}::{rel_path}",
                reason="Live target hash could not be compared with SQLite source snapshot hash.",
                fallback="drift_status_error",
                claim_impact="agent_target_drift_status_unavailable",
                exception=exc,
            )
        except Exception:
            pass

    target_source_snippets = (
        _bounded_source_snippets(snapshot_content, target_spans)
        if snapshot_status == "ok" and drift_status == "match"
        else []
    )

    return {
        "source_snapshot_status": snapshot_status,
        "source_snapshot_hash": snapshot_hash,
        "source_snapshot_mtime": snapshot_mtime,
        "live_file_hash": current_hash,
        "drift_check_status": drift_status,
        "target_spans": target_spans,
        "target_span_count": len(target_spans),
        "target_source_snippets": target_source_snippets,
    }


def _narrow_status_to_line_range(
    status: dict[str, Any],
    raw_dir: Path,
    *,
    line_start: int = 0,
    line_end: int = 0,
) -> dict[str, Any]:
    """Replace broad symbol snippets with a source-grounded explicit line range."""
    start = _safe_positive_int(line_start)
    end = _safe_positive_int(line_end)
    if not isinstance(status, dict) or start <= 0:
        return status
    target_ref = str(status.get("target_ref") or "").strip()
    if not target_ref:
        return status
    content = _source_snapshot_content_for_ref(raw_dir, target_ref)
    total_lines = len(content.splitlines()) if content else 0
    if total_lines <= 0:
        narrowed = dict(status)
        narrowed["requested_line_range"] = {
            "line_start": start,
            "line_end": end or start,
            "line_status": "not_available",
            "reason": "source_snapshot_content_missing",
        }
        narrowed["target_spans"] = []
        narrowed["target_span_count"] = 0
        narrowed["target_source_snippets"] = []
        return narrowed
    end = end if end >= start else start
    bounded_start = max(1, min(start, total_lines))
    bounded_end = max(bounded_start, min(end, total_lines))
    line_status = "available" if bounded_start == start and bounded_end == end else "clamped_to_file_bounds"
    span = {
        "symbol": "requested_line_range",
        "type": "line_range",
        "target_ref": target_ref,
        "start_line": bounded_start,
        "end_line": bounded_end,
        "source_lines": f"L{bounded_start}-L{bounded_end}",
        "line_status": line_status,
    }
    narrowed = dict(status)
    narrowed["requested_line_range"] = {
        "line_start": start,
        "line_end": end,
        "bounded_line_start": bounded_start,
        "bounded_line_end": bounded_end,
        "line_status": line_status,
    }
    if str(status.get("source_snapshot_status") or "") == "ok" and str(status.get("drift_check_status") or "") == "match":
        narrowed["target_spans"] = [span]
        narrowed["target_span_count"] = 1
        narrowed["target_source_snippets"] = _bounded_source_snippets(
            content,
            [span],
            max_snippets=1,
            max_lines_per_snippet=80,
            max_total_chars=6000,
        )
    else:
        narrowed["target_spans"] = [span]
        narrowed["target_span_count"] = 1
        narrowed["target_source_snippets"] = []
    return narrowed


def _target_path_status(
    raw_dir: Path,
    target_file: str,
    target_root: str = "",
    *,
    preferred_symbols: set[str] | None = None,
) -> dict[str, Any]:
    db_exists = (raw_dir / "codemaps.db").exists()
    resolved_node, context = _resolve_target_node_from_raw(raw_dir, target_file, allow_atlas_fallback=not db_exists)
    project_from_node, rel_from_node = resolved_node.split("::", 1) if "::" in resolved_node else ("", resolved_node)
    target_rel = str(context.get("repo_relative_path") or rel_from_node).replace("\\", "/").strip("/")
    target_ref = _target_ref_from_context(resolved_node, context)
    analysis_root = Path(_analysis_root_display(target_root)).resolve()
    target_abs = (analysis_root / target_rel).resolve() if target_rel else analysis_root
    inside_root = analysis_root in [target_abs, *target_abs.parents]
    exists = inside_root and target_abs.exists() and target_abs.is_file()
    project = _project_from_ref(target_ref)
    indexed = bool(_sqlite_file_context_from_raw(raw_dir, target_ref) or _sqlite_file_context_from_raw(raw_dir, f"{project}::{target_rel}" if project else target_rel))
    if not indexed and not db_exists:
        try:
            atlas = _atlas(raw_dir=raw_dir)
            project_data = atlas.get(project, {}) if isinstance(atlas, dict) else {}
            files = project_data.get("files") if isinstance(project_data, dict) else {}
            if isinstance(files, dict):
                for rel_path, file_info in files.items():
                    workspace_rel = str((file_info or {}).get("workspace_rel") or rel_path).replace("\\", "/").strip("/") if isinstance(file_info, dict) else str(rel_path).replace("\\", "/").strip("/")
                    atlas_rel = str(rel_path).replace("\\", "/").strip("/")
                    if target_rel in {workspace_rel, atlas_rel} or resolved_node == f"{project}::{atlas_rel}":
                        indexed = True
                        break
        except Exception as exc:
            try:
                from tools.core.honesty_telemetry import record_honesty_event

                record_honesty_event(
                    component="mcp.server",
                    category="storage_fallback",
                    operation="target_path_status_indexed_fallback",
                    subject=str(target_file or ""),
                    reason="SQLite indexed check did not resolve target; Atlas payload fallback failed.",
                    fallback="indexed_false",
                    claim_impact="target_indexed_status_may_be_conservative",
                    exception=exc,
                )
            except Exception:
                pass
    source_grounding = _source_grounding_status(
        raw_dir,
        context,
        target_abs,
        preferred_symbols=preferred_symbols,
    )
    return {
        "resolved_node": resolved_node,
        "target_ref": target_ref,
        "target_file": target_rel,
        "target_project": project,
        "target_abs": str(target_abs),
        "exists": exists,
        "inside_root": inside_root,
        "indexed": indexed,
        **source_grounding,
    }


def _public_target_path_status(status: dict[str, Any]) -> dict[str, Any]:
    """Return only agent-usable path status fields, without debug or absolute host paths."""
    if not isinstance(status, dict):
        return {}
    return {
        "target_ref": status.get("target_ref") or "",
        "target_file": status.get("target_file") or "",
        "target_project": status.get("target_project") or "",
        "exists": bool(status.get("exists")),
        "inside_root": bool(status.get("inside_root")),
        "indexed": bool(status.get("indexed")),
        "source_snapshot_status": status.get("source_snapshot_status") or "",
        "source_snapshot_hash": status.get("source_snapshot_hash") or "",
        "drift_check_status": status.get("drift_check_status") or "",
        "requested_line_range": status.get("requested_line_range") if isinstance(status.get("requested_line_range"), dict) else {},
        "target_span_count": int(status.get("target_span_count") or 0),
        "target_spans": status.get("target_spans") if isinstance(status.get("target_spans"), list) else [],
        "target_source_snippets": status.get("target_source_snippets") if isinstance(status.get("target_source_snippets"), list) else [],
    }


def _compact_merge_path_status(status: dict[str, Any]) -> dict[str, Any]:
    """Return merge queue path status without source snippets or host-only fields."""
    public = _public_target_path_status(status)
    return {
        "target_ref": public.get("target_ref") or "",
        "target_file": public.get("target_file") or "",
        "target_project": public.get("target_project") or "",
        "exists": bool(public.get("exists")),
        "inside_root": bool(public.get("inside_root")),
        "indexed": bool(public.get("indexed")),
        "source_snapshot_status": public.get("source_snapshot_status") or "",
        "drift_check_status": public.get("drift_check_status") or "",
        "target_span_count": int(public.get("target_span_count") or 0),
    }


def _bounded_source_grounding_for_agent(status: dict[str, Any], *, max_spans: int = 1) -> dict[str, Any]:
    """Return compact source grounding for agent operation packets."""
    public = _public_target_path_status(status)
    spans = public.get("target_spans") if isinstance(public.get("target_spans"), list) else []
    snippets = public.get("target_source_snippets") if isinstance(public.get("target_source_snippets"), list) else []
    snapshot_hash = str(public.get("source_snapshot_hash") or "")
    return {
        "target_ref": public.get("target_ref") or "",
        "target_file": public.get("target_file") or "",
        "target_project": public.get("target_project") or "",
        "target_exists": bool(public.get("exists")),
        "target_indexed": bool(public.get("indexed")),
        "target_grounding_status": "grounded" if public.get("exists") and public.get("indexed") else "missing_or_unindexed",
        "source_snapshot_status": public.get("source_snapshot_status") or "missing",
        "source_snapshot_hash_prefix": snapshot_hash[:12],
        "drift_check_status": public.get("drift_check_status") or "not_available",
        "target_span_count": int(public.get("target_span_count") or len(spans)),
        "target_spans_shown": min(len(spans), max_spans),
        "target_spans_omitted": max(0, len(spans) - max_spans),
        "target_spans": spans[:max_spans],
        "target_source_snippets_shown": min(len(snippets), max_spans),
        "target_source_snippets_omitted": max(0, len(snippets) - max_spans),
        "target_source_snippets": snippets[:max_spans],
    }


def _partition_directives_by_source_freshness(
    raw_dir: Path,
    directives: list[dict[str, Any]],
    *,
    target_root: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actionable: list[dict[str, Any]] = []
    refresh_required: list[dict[str, Any]] = []
    for directive in directives:
        target_files = directive.get("target_files") if isinstance(directive.get("target_files"), list) else []
        target_refs = directive.get("target_refs") if isinstance(directive.get("target_refs"), list) else []
        target_ref = str(target_refs[0] or "") if target_refs else ""
        if not target_ref and target_files:
            normalized_file = str(target_files[0]).replace("\\", "/")
            target_ref = f"MAIN::{normalized_file}"
        if not target_ref:
            actionable.append(directive)
            continue
        source_grounding = _bounded_source_grounding_for_agent(
            _target_path_status(raw_dir, target_ref, target_root=target_root),
            max_spans=1,
        )
        if (
            source_grounding.get("source_snapshot_status") == "ok"
            and source_grounding.get("drift_check_status") == "match"
        ):
            actionable.append(directive)
            continue
        target_file = str(source_grounding.get("target_file") or "").replace("\\", "/")
        refresh_command_argv = ["python", "sage.py", "watch", "--once"]
        if target_root:
            refresh_command_argv.extend(["--target-root", str(target_root)])
        if target_file:
            refresh_command_argv.extend(["--path", target_file])
        refresh_required.append(
            {
                **directive,
                "queue_lane": "refresh_required",
                "mutation_allowed": False,
                "source_grounding": source_grounding,
                "required_action": (
                    "Run the exact-file surgical refresh command before treating "
                    "this directive as an operation candidate."
                ),
                "refresh_command_argv": refresh_command_argv,
            }
        )
    return actionable, refresh_required


def _source_grounding_yaml_lines(status: dict[str, Any], *, max_spans: int = 5) -> list[str]:
    if not isinstance(status, dict) or not status:
        return [
            "source_grounding:",
            "  source_snapshot_status: \"unknown\"",
            "  drift_check_status: \"not_available\"",
            "  target_span_count: 0",
            "  target_spans: []",
        ]
    snapshot_hash = str(status.get("source_snapshot_hash") or "")
    spans = status.get("target_spans") if isinstance(status.get("target_spans"), list) else []
    snippets = status.get("target_source_snippets") if isinstance(status.get("target_source_snippets"), list) else []
    lines = [
        "source_grounding:",
        f"  source_snapshot_status: {json.dumps(status.get('source_snapshot_status') or 'missing', ensure_ascii=False)}",
        f"  source_snapshot_hash_prefix: {json.dumps(snapshot_hash[:12], ensure_ascii=False)}",
        f"  drift_check_status: {json.dumps(status.get('drift_check_status') or 'not_available', ensure_ascii=False)}",
        f"  target_span_count: {int(status.get('target_span_count') or len(spans))}",
        f"  target_spans_shown: {min(len(spans), max_spans)}",
        f"  target_spans_omitted: {max(0, len(spans) - max_spans)}",
        "  target_spans:",
    ]
    if spans:
        for span in spans[:max_spans]:
            if not isinstance(span, dict):
                continue
            lines.append("    - symbol: " + json.dumps(span.get("symbol") or "", ensure_ascii=False))
            lines.append("      type: " + json.dumps(span.get("type") or "", ensure_ascii=False))
            lines.append(f"      start_line: {int(span.get('start_line') or 0)}")
            lines.append(f"      end_line: {int(span.get('end_line') or span.get('start_line') or 0)}")
            lines.append("      source_lines: " + json.dumps(span.get("source_lines") or "", ensure_ascii=False))
            lines.append("      line_status: " + json.dumps(span.get("line_status") or "not_available", ensure_ascii=False))
    else:
        lines.append("    []")
    lines.append(f"  target_source_snippets_shown: {min(len(snippets), max_spans)}")
    lines.append(f"  target_source_snippets_omitted: {max(0, len(snippets) - max_spans)}")
    lines.append("  target_source_snippet_usage: \"bounded source excerpts for grounding/orientation; inspect the target file or requested line range before editing outside shown lines\"")
    lines.extend(render_target_source_snippets(snippets, indent="  ", item_indent="    ", max_items=max_spans))
    evidence_snippets = (
        status.get("evidence_source_snippets")
        if isinstance(status.get("evidence_source_snippets"), list)
        else []
    )
    lines.append(f"  evidence_source_snippets_shown: {len(evidence_snippets)}")
    lines.append("  evidence_source_snippets:")
    if evidence_snippets:
        for snippet in evidence_snippets[:max_spans]:
            if not isinstance(snippet, dict):
                continue
            lines.append("    - evidence: " + json.dumps(snippet.get("evidence") or "", ensure_ascii=False))
            lines.append("      source_lines: " + json.dumps(snippet.get("source_lines") or "", ensure_ascii=False))
            lines.append(f"      matched_line: {int(snippet.get('matched_line') or 0)}")
            lines.append("      snippet_status: " + json.dumps(snippet.get("snippet_status") or "not_available", ensure_ascii=False))
            code = snippet.get("code")
            if code:
                lines.append("      code: |-")
                for code_line in str(code).splitlines():
                    lines.append("        " + code_line)
    else:
        lines.append("    []")
    return lines


def _agent_file_ref(path_or_ref: str, default_project: str = "") -> tuple[str, str]:
    text = str(path_or_ref or "").replace("\\", "/").strip()
    if "::" in text:
        project, rel_path = text.split("::", 1)
        project = project.strip() or default_project
        rel_path = rel_path.strip("/")
    else:
        project = default_project
        rel_path = text.strip("/")
    return rel_path, f"{project}::{rel_path}" if project and rel_path else rel_path


def _bounded_patch_change_snippets(patch_content: str, *, max_hunks: int = 3, max_lines_per_hunk: int = 12) -> list[dict[str, Any]]:
    if not str(patch_content or "").strip():
        return []
    snippets: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in str(patch_content).splitlines():
        if raw_line.startswith("@@"):
            if current:
                snippets.append(current)
                if len(snippets) >= max_hunks:
                    return snippets
            current = {
                "hunk_header": raw_line,
                "snippet_status": "included_patch_hunk",
                "changed_lines": [],
                "omitted_changed_lines": 0,
            }
            continue
        if current is None:
            continue
        if raw_line.startswith(("---", "+++", "\\")):
            continue
        if not raw_line.startswith(("+", "-")):
            continue
        changed_lines = current.setdefault("changed_lines", [])
        if len(changed_lines) < max_lines_per_hunk:
            changed_lines.append(raw_line)
        else:
            current["omitted_changed_lines"] = int(current.get("omitted_changed_lines") or 0) + 1
    if current and len(snippets) < max_hunks:
        snippets.append(current)
    return snippets


def _bounded_full_replacement_diff_snippets(
    current_content: str,
    proposed_content: str,
    *,
    max_hunks: int = 3,
    max_lines_per_hunk: int = 12,
) -> list[dict[str, Any]]:
    if current_content == proposed_content:
        return []
    diff_lines = list(
        difflib.unified_diff(
            current_content.splitlines(),
            proposed_content.splitlines(),
            fromfile="current",
            tofile="proposed",
            lineterm="",
            n=2,
        )
    )
    snippets: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in diff_lines:
        if raw_line.startswith("@@"):
            if current:
                snippets.append(current)
                if len(snippets) >= max_hunks:
                    return snippets
            current = {
                "hunk_header": raw_line,
                "snippet_status": "included_full_replacement_diff_hunk",
                "changed_lines": [],
                "omitted_changed_lines": 0,
            }
            continue
        if current is None or raw_line.startswith(("---", "+++")):
            continue
        if not raw_line.startswith(("+", "-")):
            continue
        changed_lines = current.setdefault("changed_lines", [])
        if len(changed_lines) < max_lines_per_hunk:
            changed_lines.append(raw_line)
        else:
            current["omitted_changed_lines"] = int(current.get("omitted_changed_lines") or 0) + 1
    if current and len(snippets) < max_hunks:
        snippets.append(current)
    return snippets


def _is_unified_diff_like(value: str) -> bool:
    text = str(value or "")
    return bool(re.search(r"(?m)^@@\s+-\d+", text) or re.search(r"(?m)^diff\s+--git\s+", text))


def _check_exact_patch_applicability(patch_content: str, workspace_root: Path) -> dict[str, Any]:
    """Ask Git's non-mutating patch engine whether the exact payload applies."""
    if not _is_unified_diff_like(patch_content):
        return {
            "status": "NOT_APPLICABLE",
            "oracle": "git_apply_check",
            "mutation_performed": False,
            "reason": "payload_is_not_a_unified_diff",
        }

    patch_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="sage_patch_", suffix=".diff", delete=False) as handle:
            handle.write(str(patch_content).encode("utf-8"))
            patch_path = Path(handle.name)
        safe_env = isolated_python_subprocess_env(
            os.environ,
            code_maps_dir=BASE_DIR,
            vendor_paths=VENDOR_PATHS,
        )
        result, duration = run_observed_subprocess(
            ["git", "apply", "--check", "--whitespace=nowarn", str(patch_path)],
            cwd=Path(workspace_root).resolve(),
            label="patch_applicability",
            timeout=patch_applicability_timeout_seconds(),
            env=safe_env,
            log=None,
        )
        if int(result.returncode) == 124:
            return {
                "status": "UNKNOWN",
                "oracle": "git_apply_check",
                "command": ["git", "apply", "--check", "--whitespace=nowarn", "<temporary-patch>"],
                "returncode": int(result.returncode),
                "duration_seconds": duration,
                "mutation_performed": False,
                "timed_out": True,
                "reason": "patch_applicability_oracle_timeout",
                "stdout_excerpt": str(result.stdout or "")[:1200],
                "stderr_excerpt": str(result.stderr or "")[:1200],
            }
        return {
            "status": "PASS" if result.returncode == 0 else "FAIL",
            "oracle": "git_apply_check",
            "command": ["git", "apply", "--check", "--whitespace=nowarn", "<temporary-patch>"],
            "returncode": int(result.returncode),
            "duration_seconds": duration,
            "mutation_performed": False,
            "stdout_excerpt": str(result.stdout or "")[:1200],
            "stderr_excerpt": str(result.stderr or "")[:1200],
        }
    except Exception as exc:
        return {
            "status": "UNKNOWN",
            "oracle": "git_apply_check",
            "mutation_performed": False,
            "reason": "patch_applicability_oracle_unavailable",
            "error": str(exc),
        }
    finally:
        if patch_path is not None:
            try:
                patch_path.unlink(missing_ok=True)
            except OSError:
                pass


def _narrow_source_grounding_to_inspection_target(status: dict[str, Any], payload: dict[str, Any], target: str) -> dict[str, Any]:
    """Keep source snippets bounded to the inspected symbol when the target is unambiguous."""
    if not isinstance(status, dict) or not status:
        return status
    requested_symbol = str(target or "").split("::", 1)[-1].strip()
    payload_spans = payload.get("target_spans") if isinstance(payload.get("target_spans"), list) else []
    if not payload_spans:
        payload_spans = (
            payload.get("atlas_symbols", [])[:AGENT_INSPECTION_MAX_TARGET_SPANS]
            if isinstance(payload.get("atlas_symbols"), list)
            else []
        )
    wanted_symbols = {
        str(span.get("symbol") or "").strip()
        for span in payload_spans
        if isinstance(span, dict) and str(span.get("symbol") or "").strip()
    }
    if requested_symbol:
        wanted_symbols.add(requested_symbol)
    wanted_lines = {
        str(span.get("source_lines") or "").strip()
        for span in payload_spans
        if isinstance(span, dict) and str(span.get("source_lines") or "").strip()
    }
    if not wanted_symbols and not wanted_lines:
        return status

    narrowed = dict(status)
    spans = status.get("target_spans") if isinstance(status.get("target_spans"), list) else []
    snippets = status.get("target_source_snippets") if isinstance(status.get("target_source_snippets"), list) else []

    def _matches(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        symbol = str(row.get("symbol") or "").strip()
        source_lines = str(row.get("source_lines") or "").strip()
        return bool((symbol and symbol in wanted_symbols) or (source_lines and source_lines in wanted_lines))

    narrowed_spans = [row for row in spans if _matches(row)]
    narrowed_snippets = [row for row in snippets if _matches(row)]
    if narrowed_spans:
        narrowed["target_spans"] = narrowed_spans
        narrowed["target_span_count"] = len(narrowed_spans)
    narrowed["target_source_snippets"] = narrowed_snippets
    return narrowed


def _sqlite_symbol_inspection_payload(raw_dir: Path, target_symbol: str, target_root: str = "") -> dict[str, Any] | None:
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return None
    scoped_project = ""
    symbol_query = str(target_symbol or "").strip()
    if "::" in symbol_query:
        scoped_project, symbol_query = symbol_query.split("::", 1)
    symbol_query = symbol_query.strip()
    matches = _find_symbol_matches(symbol_query, project=scoped_project or None, raw_dir=raw_dir)
    if scoped_project:
        matches = [row for row in matches if str(row.get("project") or "").lower() == scoped_project.lower()]

    contexts: list[dict[str, Any]] = []
    context_keys: set[tuple[str, str]] = set()
    symbol_rows: list[dict[str, Any]] = []
    for row in matches[:12]:
        project = str(row.get("project") or "")
        atlas_rel = str(row.get("file") or "").replace("\\", "/").strip("/")
        repo_rel = str(row.get("repo_relative_path") or atlas_rel).replace("\\", "/").strip("/")
        if project and repo_rel and (project, repo_rel) not in context_keys:
            contexts.append(
                {
                    "project": project,
                    "project_key": project,
                    "file": atlas_rel,
                    "workspace_rel": repo_rel,
                    "repo_relative_path": repo_rel,
                    "atlas_node": row.get("atlas_node") or (f"{project}::{atlas_rel}" if atlas_rel else ""),
                    "source": "sqlite_symbols",
                }
            )
            context_keys.add((project, repo_rel))
        symbol_rows.append(
            {
                "project": project,
                "symbol": row.get("name"),
                "file": atlas_rel,
                "workspace_rel": repo_rel,
                "target_ref": f"{project}::{repo_rel}" if project and repo_rel else repo_rel,
                "type": row.get("type"),
                "line": int(row.get("line") or 0),
                "char": int(row.get("char") or 0),
                "end_line": int(row.get("end_line") or row.get("line") or 0),
                "source_lines": row.get("source_lines") or "",
                "dependencies_sample": [],
                "dependency_count": int(row.get("dependencies") or 0),
                "source": "sqlite_symbols",
            }
        )

    return {
        "meta": {
            "kind": "target_inspection",
            "version": "v1",
            "generator": "tools.mcp.server",
            "artifact_root": str(raw_dir),
            "source": "sqlite_symbols",
        },
        "analysis_root": _analysis_root_display(target_root),
        "target": {"kind": "symbol", "value": target_symbol},
        "target_file_context": contexts[:10],
        "summary": {
            "atlas_files": len(contexts),
            "atlas_symbols": len(symbol_rows),
            "ui_runtime_matches": 0,
            "ui_risk_tiers": {},
            "dead_code_matches": 0,
            "audit_violations": 0,
            "merge_decisions": 0,
            "blast_radius_matches": 0,
        },
        "atlas_symbols": symbol_rows[:12],
        "ui_runtime_matches": [],
        "dead_code_matches": [],
        "audit_violations": [],
        "merge_decisions": [],
        "blast_radius_matches": [],
    }


def _sqlite_file_inspection_payload(raw_dir: Path, file_path: str, target_root: str = "") -> dict[str, Any] | None:
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return None
    sqlite_context = _sqlite_file_context_from_raw(raw_dir, file_path)
    if sqlite_context is None:
        return None
    resolved_node, context = sqlite_context
    project, atlas_rel = resolved_node.split("::", 1) if "::" in resolved_node else ("", resolved_node)
    repo_rel = str(context.get("repo_relative_path") or atlas_rel).replace("\\", "/").strip("/")
    symbol_rows: list[dict[str, Any]] = []
    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT symbols.name, symbols.type, symbols.line, symbols.char,
                       symbols.end_line, symbols.source_lines,
                       (
                           SELECT COUNT(*)
                           FROM dependencies
                           WHERE dependencies.source_file_id = files.file_id
                       ) AS dependency_count
                FROM files
                LEFT JOIN symbols ON symbols.file_id = files.file_id
                WHERE files.project_key = ? AND files.rel_path = ?
                ORDER BY symbols.name
                LIMIT 200;
                """,
                (project, atlas_rel),
            ).fetchall()
        for row in rows:
            if not row["name"]:
                continue
            symbol_rows.append(
                {
                    "project": project,
                    "symbol": row["name"],
                    "file": atlas_rel,
                    "workspace_rel": repo_rel,
                    "target_ref": f"{project}::{repo_rel}" if project and repo_rel else repo_rel,
                    "type": row["type"],
                    "line": int(row["line"] or 0),
                    "char": int(row["char"] or 0),
                    "end_line": int(row["end_line"] or row["line"] or 0),
                    "source_lines": row["source_lines"] or "",
                    "dependencies_sample": [],
                    "dependency_count": int(row["dependency_count"] or 0),
                    "source": "sqlite_symbols",
                }
            )
    except Exception as exc:
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="mcp.server",
                category="storage_fallback",
                operation="sqlite_file_inspection_symbols",
                subject=str(file_path or ""),
                reason="SQLite file symbol lookup failed while building file inspection.",
                fallback="file_context_without_symbols",
                claim_impact="file_inspection_symbol_details_may_be_incomplete",
                exception=exc,
            )
        except Exception:
            pass

    audit_violations, audit_total, audit_query_ok = _audit_violation_work_items_from_sqlite(
        raw_dir,
        page=1,
        page_size=200,
        project=project,
        file_path=atlas_rel,
    )
    file_context = {
        "project": project,
        "project_key": project,
        "file": atlas_rel,
        "workspace_rel": repo_rel,
        "repo_relative_path": repo_rel,
        "atlas_node": resolved_node,
        "source": "sqlite_files",
    }
    dead_code_path = raw_dir / "dead_code.json"
    dead_code_payload = load_raw_artifact_path(dead_code_path, None)
    dead_code_available = isinstance(dead_code_payload, dict) and isinstance(dead_code_payload.get("items"), list)
    dead_code_matches = (
        project_dead_code_matches_for_file(dead_code_payload, [file_context])
        if dead_code_available
        else []
    )
    dead_code_identity = {
        "artifact": "dead_code",
        "content_fingerprint": raw_artifact_content_fingerprint(dead_code_path),
        "generation": (dead_code_payload.get("meta") or {}) if isinstance(dead_code_payload, dict) else {},
    }
    return {
        "meta": {
            "kind": "target_inspection",
            "version": "v1",
            "generator": "tools.mcp.server",
            "artifact_root": str(raw_dir),
            "source": "sqlite_files",
            "violation_source": "sqlite_findings" if audit_query_ok else "unavailable",
            "evidence_status": "COMPLETE" if dead_code_available and audit_query_ok else "PARTIAL",
            "dead_code_projection_status": "AVAILABLE" if dead_code_available else "UNKNOWN",
            "dead_code_artifact_identity": dead_code_identity,
        },
        "analysis_root": _analysis_root_display(target_root),
        "target": {"kind": "file", "value": file_path},
        "target_file_context": [file_context],
        "summary": {
            "atlas_files": 1,
            "atlas_symbols": len(symbol_rows),
            "ui_runtime_matches": 0,
            "ui_risk_tiers": {},
            "dead_code_matches": len(dead_code_matches) if dead_code_available else None,
            "dead_code_matches_status": "AVAILABLE" if dead_code_available else "UNKNOWN",
            "audit_violations": audit_total,
            "merge_decisions": 0,
            "blast_radius_matches": 0,
        },
        "atlas_symbols": symbol_rows[:12],
        "ui_runtime_matches": [],
        "dead_code_matches": dead_code_matches[:12],
        "audit_violations": audit_violations,
        "merge_decisions": [],
        "blast_radius_matches": [],
    }


def _project_from_ref(target_ref: str) -> str:
    return str(target_ref or "").split("::", 1)[0] if "::" in str(target_ref or "") else ""


def _render_impact_brief(payload: dict[str, Any]) -> str:
    direct = payload.get("direct_dependents") if isinstance(payload.get("direct_dependents"), list) else []
    direct_refs = payload.get("direct_dependent_refs") if isinstance(payload.get("direct_dependent_refs"), list) else []
    transitive = payload.get("transitive_dependents") if isinstance(payload.get("transitive_dependents"), list) else []
    transitive_refs = payload.get("transitive_dependent_refs") if isinstance(payload.get("transitive_dependent_refs"), list) else []
    transitive_depths = payload.get("transitive_dependent_depths") if isinstance(payload.get("transitive_dependent_depths"), dict) else {}
    direct_pairs = set(zip(direct, direct_refs + [""] * max(0, len(direct) - len(direct_refs))))
    transitive_pairs = list(zip(transitive, transitive_refs + [""] * max(0, len(transitive) - len(transitive_refs))))
    transitive_sample_pairs = [(path, ref) for path, ref in transitive_pairs if (path, ref) not in direct_pairs]
    target_ref = str(payload.get("target_ref") or payload.get("target") or "")
    target_status = payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}
    target_exists = bool(target_status.get("exists", True))
    target_indexed = bool(target_status.get("indexed", True))
    target_grounded = target_exists and target_indexed
    next_action = "inspect_direct_dependents_before_public_contract_change" if target_grounded else "refresh_target_analysis_before_impact_decision"
    follow_up_when = (
        "Only request deeper scope if direct dependents and listed validation cannot explain the failure or the user asks for broader impact."
        if target_grounded
        else "Do not request deeper impact scope until this target is grounded in disk and Atlas."
    )
    deeper_scope = f"get_impact_radius(target_node={target_ref!r}, depth=3)" if target_grounded else "Refresh SAGE analysis for this exact target repository, then rerun get_impact_radius."
    full_graph = f"get_impact_radius(target_node={target_ref!r}, depth=0)" if target_grounded else "not_available_until_target_grounded"
    inspect_first: list[str] = []
    for candidate in [payload.get("target_file") or payload.get("target") or "", *direct[:3]]:
        normalized = str(candidate or "").strip()
        if normalized and normalized not in inspect_first:
            inspect_first.append(normalized)
    yaml_lines = [
        "mission:",
        "  - Use this impact radius before editing the target file.",
        "task:",
        f"  analysis_root: {json.dumps(payload.get('analysis_root') or '', ensure_ascii=False)}",
        f"  target_project: {json.dumps(payload.get('target_project') or _project_from_ref(target_ref), ensure_ascii=False)}",
        f"  target_file: {json.dumps(payload.get('target_file') or payload.get('target') or '', ensure_ascii=False)}",
        f"  target_ref: {json.dumps(target_ref, ensure_ascii=False)}",
        f"  radius_depth: {int(payload.get('radius_depth') or 0)}",
        "  evidence_basis: \"static dependency graph\"",
        f"  target_exists: {str(target_exists).lower()}",
        f"  target_indexed: {str(target_indexed).lower()}",
        f"  target_grounding_status: {json.dumps('grounded' if target_grounded else 'missing_or_unindexed', ensure_ascii=False)}",
        *_source_grounding_yaml_lines(target_status, max_spans=1),
        f"  blast_radius_size: {int(payload.get('blast_radius_size') or 0)}",
        f"  returned_scope_size: {int(payload.get('returned_scope_size') or payload.get('blast_radius_size') or 0)}",
        f"  direct_dependents_count: {int(payload.get('direct_dependents_count') or 0)}",
        f"  direct_dependents_shown: {min(len(direct), 12)}",
        f"  direct_dependents_omitted: {int(payload.get('direct_dependents_omitted') or max(0, len(direct) - 12))}",
        f"  transitive_dependents_shown: {min(len(transitive_sample_pairs), 20)}",
        f"  transitive_dependents_omitted: {int(payload.get('transitive_dependents_omitted') or max(0, len(transitive_sample_pairs) - 20))}",
        f"  next_depth_hint: {json.dumps(payload.get('next_depth_hint') or '', ensure_ascii=False)}",
        "path_contract:",
        "  open_files_with: \"analysis_root + target_file or listed file\"",
        "  target_ref_usage: \"SAGE/MCP reference only; not a filesystem path\"",
        "  target_ref_format: \"<project>::<repo_relative_path>; MAIN is the primary analyzed project scope\"",
        "directive:",
        "  next_action: " + json.dumps(next_action, ensure_ascii=False),
        "  inspect_first:",
        *["    - " + json.dumps(path, ensure_ascii=False) for path in inspect_first],
        "follow_up:",
        f"  when_to_use: {json.dumps(follow_up_when, ensure_ascii=False)}",
        f"  deeper_scope: {json.dumps(deeper_scope, ensure_ascii=False)}",
        f"  full_graph: {json.dumps(full_graph, ensure_ascii=False)}",
        "direct_dependents:",
    ]
    if payload.get("dependency_graph_source"):
        yaml_lines.insert(10, f"  dependency_graph_source: {json.dumps(payload.get('dependency_graph_source'), ensure_ascii=False)}")
    if direct:
        for index, item in enumerate(direct[:12]):
            yaml_lines.append(f"  - file: {json.dumps(item, ensure_ascii=False)}")
            yaml_lines.append("    depth: 1")
            if index < len(direct_refs):
                yaml_lines.append(f"    target_ref: {json.dumps(direct_refs[index], ensure_ascii=False)}")
    else:
        yaml_lines.append("  []")
    yaml_lines.append("transitive_dependents_sample:")
    if transitive_sample_pairs:
        for item, ref in transitive_sample_pairs[:20]:
            yaml_lines.append(f"  - file: {json.dumps(item, ensure_ascii=False)}")
            if item in transitive_depths:
                yaml_lines.append(f"    depth: {int(transitive_depths.get(item) or 0)}")
            if ref:
                yaml_lines.append(f"    target_ref: {json.dumps(ref, ensure_ascii=False)}")
    else:
        yaml_lines.append("  []")
    limits = payload.get("evidence_limits") if isinstance(payload.get("evidence_limits"), list) else []
    if limits:
        yaml_lines.append("evidence_limits:")
        yaml_lines.extend("  - " + json.dumps(str(item), ensure_ascii=False) for item in limits[:5])
    yaml_lines.extend(
        [
            "do:",
            "  - If target_grounding_status is not grounded, refresh analysis for the exact repository before trusting impact radius.",
            "  - Inspect direct dependents before changing public behavior or exported contracts.",
            "  - Keep the patch smallest when blast_radius_size is nonzero.",
            "  - Treat transitive_dependents_sample as a depth-limited bounded sample; request a deeper graph only when direct evidence requires it.",
            "do_not:",
            "  - Do not refactor transitive dependents unless a direct validation failure proves it is required.",
            "  - Do not expand work to omitted dependents from this brief alone.",
        ]
    )
    return "\n".join(["# Impact Radius Brief", "", "```yaml", *_with_context_budget(yaml_lines), "```", ""])


def _normalize_merge_action_filter(value: str) -> str:
    text = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "import-now": "Import Now",
        "safe-to-import": "Import Now",
        "review": "Import With Review",
        "import-with-review": "Import With Review",
        "assisted-import": "Import With Review",
        "do-not-import": "Do Not Import Yet",
        "do-not-import-yet": "Do Not Import Yet",
        "blocked": "Do Not Import Yet",
    }
    return aliases.get(text, str(value or "").strip())


def _bounded_merge_evidence(values: Any, *, limit: int = 8) -> tuple[list[Any], int]:
    rows = values if isinstance(values, list) else []
    bounded = rows[: max(0, int(limit))]
    return bounded, max(0, len(rows) - len(bounded))


def _render_merge_review_queue_brief(payload: dict[str, Any]) -> str:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    target_proof = payload.get("target_proof") if isinstance(payload.get("target_proof"), dict) else {}
    cockpit_proof = target_proof.get("cockpit") if isinstance(target_proof.get("cockpit"), dict) else {}
    yaml_lines = [
        "mission:",
        "  - Review merge candidates without applying file copies or imports automatically.",
        "  - Escalate to the human before any merge/import/move/delete mutation.",
        "task:",
        f"  analysis_root: {json.dumps(payload.get('analysis_root') or '', ensure_ascii=False)}",
        f"  total_candidates: {int(payload.get('total_candidates') or 0)}",
        f"  returned: {len(items)}",
        f"  action_filter: {json.dumps(payload.get('action_filter') or '', ensure_ascii=False)}",
        f"  status: {json.dumps(payload.get('status') or '', ensure_ascii=False)}",
        "target_proof:",
        f"  verdict: {json.dumps(target_proof.get('verdict') or '', ensure_ascii=False)}",
        f"  analysis_snapshot_id: {json.dumps(target_proof.get('analysis_snapshot_id') or '', ensure_ascii=False)}",
        f"  root_binding: {json.dumps(target_proof.get('root_binding') or '', ensure_ascii=False)}",
        f"  cockpit_snapshot_binding: {json.dumps(cockpit_proof.get('snapshot_binding') or '', ensure_ascii=False)}",
        f"  cockpit_freshness: {json.dumps(cockpit_proof.get('freshness') or '', ensure_ascii=False)}",
        f"  required_action: {json.dumps(payload.get('required_action') or '', ensure_ascii=False)}",
        "policy_boundary:",
        "  human_approval_required: true",
        "  safe_default: review_only",
        "  mutation_allowed_by_this_packet: false",
        "path_contract:",
        "  open_files_with: \"analysis_root + source_file or proposed_target_path\"",
        "  source_file_role: \"read-only variation/source evidence\"",
        "  proposed_target_path_role: \"MAIN target candidate; mutate only after human approval\"",
        "  source_ref_usage: \"SAGE/MCP follow-up reference only; not a filesystem path\"",
        "  evidence_dependency_lists_are_not_open_file_targets: true",
        "items:",
    ]
    if items:
        for item in items:
            yaml_lines.extend(
                [
                    "  - candidate: " + json.dumps(item.get("candidate") or "", ensure_ascii=False),
                    "    action: " + json.dumps(item.get("action") or "", ensure_ascii=False),
                    "    confidence: " + json.dumps(item.get("confidence") or "", ensure_ascii=False),
                    "    source_project: " + json.dumps(item.get("source_project") or "", ensure_ascii=False),
                    "    source_file: " + json.dumps(item.get("source_file") or "", ensure_ascii=False),
                    "    source_file_role: \"read_only_evidence\"",
                    "    source_ref: " + json.dumps(item.get("source_ref") or "", ensure_ascii=False),
                    "    proposed_target_path: " + json.dumps(item.get("target_path") or "", ensure_ascii=False),
                    "    proposed_target_role: \"main_candidate_requires_human_approval\"",
                    "    source_file_status:",
                    "      exists: " + ("true" if (item.get("source_file_status") or {}).get("exists") else "false"),
                    "      indexed: " + ("true" if (item.get("source_file_status") or {}).get("indexed") else "false"),
                    "      inside_root: " + ("true" if (item.get("source_file_status") or {}).get("inside_root") else "false"),
                    "    proposed_target_status:",
                    "      exists: " + ("true" if (item.get("proposed_target_status") or {}).get("exists") else "false"),
                    "      indexed: " + ("true" if (item.get("proposed_target_status") or {}).get("indexed") else "false"),
                    "      inside_root: " + ("true" if (item.get("proposed_target_status") or {}).get("inside_root") else "false"),
                    "    route: " + json.dumps(item.get("route") or "", ensure_ascii=False),
                    "    closure_size: " + str(int(item.get("closure_size") or 0)),
                    "    reasons:",
                ]
            )
            reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
            yaml_lines.extend([f"      - {json.dumps(reason, ensure_ascii=False)}" for reason in reasons] or ["      []"])
            yaml_lines.append("    required_actions:")
            required_actions = item.get("required_actions") if isinstance(item.get("required_actions"), list) else []
            yaml_lines.extend([f"      - {json.dumps(action, ensure_ascii=False)}" for action in required_actions] or ["      []"])
            yaml_lines.append("    evidence:")
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            for key in ("external_deps", "unresolved_internal_deps", "target_conflicts", "missing_i18n_keys"):
                values = evidence.get(key) if isinstance(evidence.get(key), list) else []
                yaml_lines.append(f"      {key}:")
                yaml_lines.extend([f"        - {json.dumps(value, ensure_ascii=False)}" for value in values[:8]] or ["        []"])
            yaml_lines.append("    inspect_first:")
            inspect_first = item.get("inspect_first") if isinstance(item.get("inspect_first"), list) else [
                item.get("source_file"),
                item.get("target_path"),
            ]
            yaml_lines.extend([f"      - {json.dumps(path, ensure_ascii=False)}" for path in inspect_first if path] or ["      []"])
    else:
        yaml_lines.append("  []")
    merge_validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    yaml_lines.extend(
        [
            "validation:",
            "  mode: " + json.dumps(merge_validation.get("mode") or "", ensure_ascii=False),
            "  pre_approval_tools:",
            *["    - " + json.dumps(row, ensure_ascii=False) for row in merge_validation.get("pre_approval_tools", [])],
            "  post_approval_tools:",
            *["    - " + json.dumps(row, ensure_ascii=False) for row in merge_validation.get("post_approval_tools", [])],
            "  completion_rule: " + json.dumps(merge_validation.get("completion_rule") or "", ensure_ascii=False),
        ]
    )
    yaml_lines.extend(
        [
            "do:",
            "  - Inspect source_file as read-only evidence and proposed_target_path as the MAIN target candidate before making any recommendation.",
            "  - Use MCP follow-up calls inspect_file(target=source_ref), get_impact_radius(target_node=source_ref), and get_test_impact(target_file=source_ref) before recommending an approved merge patch.",
            "  - Treat Import With Review as advisory until a human approves the exact scope.",
            "  - After human approval, use the listed MCP validation follow-up and target-repository commands for the bounded patch.",
            "do_not:",
            "  - Do not edit source_file; it is variation/source evidence, not the default target-repository edit surface.",
            "  - Do not copy dependency packages automatically from this packet.",
            "  - Do not treat Import Now as permission to mutate without human approval.",
            "  - Do not widen merge scope beyond the listed source_file, proposed_target_path, and dependency package evidence.",
            "  - Do not open evidence.external_deps as filesystem paths unless another field also lists them under inspect_first.",
        ]
    )
    return "\n".join(["# Merge Review Queue", "", "```yaml", *_with_context_budget(yaml_lines), "```", ""])


def _nearest_package_validation_commands(payload: dict[str, Any]) -> list[str]:
    analysis_root = str(payload.get("analysis_root") or "").strip()
    target_file = str(payload.get("target_file") or payload.get("target") or "").replace("\\", "/").strip()
    if not analysis_root or not target_file:
        return []
    try:
        root = Path(analysis_root).resolve()
        target_path = (root / target_file).resolve()
        target_path.relative_to(root)
    except Exception:
        return []
    cursor = target_path.parent if target_path.suffix else target_path
    normalized_target = target_file.lower()
    target_path = Path(normalized_target)
    target_suffix = target_path.suffix.lower()
    path_parts = {part.lower() for part in target_path.parts}
    type_contract_names = {"type", "types", "schema", "schemas", "contract", "contracts", "interface", "interfaces"}
    typescript_contract_target = (
        normalized_target.endswith(".d.ts")
        or (
            target_suffix in {".ts", ".tsx", ".mts", ".cts"}
            and (
                bool(path_parts & type_contract_names)
                or any(token in target_path.stem.lower() for token in ("type", "schema", "contract", "interface"))
            )
        )
    )
    script_priority = (
        ("typecheck", "build", "test", "lint")
        if typescript_contract_target
        else ("test", "typecheck", "lint", "build")
    )
    command_limit = 3 if typescript_contract_target else 2
    while True:
        package_path = cursor / "package.json"
        if package_path.exists():
            try:
                package_data = json.loads(package_path.read_text(encoding="utf-8"))
            except Exception:
                package_data = {}
            scripts = package_data.get("scripts") if isinstance(package_data, dict) else {}
            if isinstance(scripts, dict):
                try:
                    package_rel = package_path.parent.relative_to(root).as_posix()
                except Exception:
                    package_rel = package_path.parent.as_posix()
                prefix = "." if package_rel == "." else package_rel
                commands = [
                    target_package_script_command(package_data, prefix, script)
                    for script in script_priority
                    if script in scripts
                ]
                return commands[:command_limit]
        if cursor == root or cursor.parent == cursor:
            return []
        cursor = cursor.parent


def _repo_relative_from_sqlite_row(project_path: str, rel_path: str) -> str:
    project_path = str(project_path or "").replace("\\", "/").strip("/")
    if project_path in {".", "./"}:
        project_path = ""
    if project_path.startswith("./"):
        project_path = project_path[2:].strip("/")
    rel_path = str(rel_path or "").replace("\\", "/").strip("/")
    if rel_path.startswith("./"):
        rel_path = rel_path[2:].strip("/")
    if project_path and rel_path and rel_path != project_path and not rel_path.startswith(f"{project_path}/"):
        return f"{project_path}/{rel_path}".strip("/")
    return rel_path


def _sqlite_impact_radius_from_raw(raw_dir: Path, target_node: str, target_root: str = "", depth: int = 2) -> dict[str, Any] | None:
    started = time.perf_counter()
    context_result = _sqlite_file_context_from_raw(raw_dir, target_node)
    if context_result is None:
        return None
    resolved_node, context = context_result
    file_id = int(context.get("file_id") or 0)
    if not file_id:
        return None
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return None
    radius_depth = max(0, int(depth or 0))
    sql_depth = 128 if radius_depth == 0 else radius_depth
    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            conn.row_factory = sqlite3.Row
            direct_rows = conn.execute(
                """
                SELECT f.file_id, f.project_key, f.rel_path, p.path
                FROM dependencies d
                JOIN files f ON f.file_id = d.source_file_id
                LEFT JOIN projects p ON p.project_key = f.project_key
                WHERE d.target_file_id = ?
                ORDER BY f.project_key, f.rel_path;
                """,
                (file_id,),
            ).fetchall()
            transitive_rows = conn.execute(
                """
                WITH RECURSIVE reach(file_id, depth, path) AS (
                    SELECT d.source_file_id, 1, ',' || d.source_file_id || ','
                    FROM dependencies d
                    WHERE d.target_file_id = ?
                    UNION
                    SELECT d.source_file_id, reach.depth + 1, reach.path || d.source_file_id || ','
                    FROM dependencies d
                    JOIN reach ON d.target_file_id = reach.file_id
                    WHERE reach.depth < ?
                      AND instr(reach.path, ',' || d.source_file_id || ',') = 0
                )
                SELECT DISTINCT f.file_id, f.project_key, f.rel_path, p.path, min(reach.depth) AS depth
                FROM reach
                JOIN files f ON f.file_id = reach.file_id
                LEFT JOIN projects p ON p.project_key = f.project_key
                GROUP BY f.file_id, f.project_key, f.rel_path, p.path
                ORDER BY depth, f.project_key, f.rel_path
                LIMIT 200;
                """,
                (file_id, sql_depth),
            ).fetchall()
            transitive_count = int(
                conn.execute(
                    """
                    WITH RECURSIVE reach(file_id, depth, path) AS (
                        SELECT d.source_file_id, 1, ',' || d.source_file_id || ','
                        FROM dependencies d
                        WHERE d.target_file_id = ?
                        UNION
                        SELECT d.source_file_id, reach.depth + 1, reach.path || d.source_file_id || ','
                        FROM dependencies d
                        JOIN reach ON d.target_file_id = reach.file_id
                        WHERE reach.depth < ?
                          AND instr(reach.path, ',' || d.source_file_id || ',') = 0
                    )
                    SELECT COUNT(DISTINCT file_id) FROM reach;
                    """,
                    (file_id, 128),
                ).fetchone()[0]
            )
    except Exception:
        return None

    def path_for(row: sqlite3.Row) -> str:
        return _repo_relative_from_sqlite_row(str(row["path"] or ""), str(row["rel_path"] or ""))

    def ref_for(row: sqlite3.Row) -> str:
        return f"{row['project_key']}::{path_for(row)}"

    direct_paths = [path_for(row) for row in direct_rows]
    transitive_paths = [path_for(row) for row in transitive_rows]
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    target_file = str(context.get("repo_relative_path") or "").replace("\\", "/").strip("/")
    target_ref = _target_ref_from_context(resolved_node, context)
    return {
        "target": resolved_node,
        "target_ref": target_ref,
        "analysis_root": _analysis_root_display(target_root),
        "target_project": _project_from_ref(target_ref),
        "target_file": target_file,
        "target_path_status": _target_path_status(raw_dir, target_file, target_root=target_root),
        "radius_depth": radius_depth,
        "dependency_graph_source": "sqlite_dependencies",
        "evidence_limits": [],
        "duration_ms": duration_ms,
        "slow_warning": "impact_radius_resolution_exceeded_2s" if duration_ms > 2000 else "",
        "status": "ok",
        "blast_radius_size": transitive_count,
        "returned_scope_size": len(transitive_paths),
        "direct_dependents_count": len(direct_paths),
        "direct_dependents_omitted": 0,
        "transitive_dependents_omitted": max(0, transitive_count - len(transitive_paths)),
        "next_depth_hint": "Use depth=3 first when the returned scope is insufficient; use depth=0 full graph only as a last resort." if radius_depth > 0 and transitive_count > len(transitive_paths) else "",
        "direct_dependents": direct_paths,
        "direct_dependent_refs": [ref_for(row) for row in direct_rows],
        "transitive_dependents": transitive_paths,
        "transitive_dependent_refs": [ref_for(row) for row in transitive_rows],
        "transitive_dependent_depths": {path_for(row): int(row["depth"] or 0) for row in transitive_rows},
    }


def _sqlite_upstream_trace_from_raw(
    raw_dir: Path,
    target_node: str,
    target_root: str = "",
    signals_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    context_result = _sqlite_file_context_from_raw(raw_dir, target_node)
    if context_result is None:
        return None
    resolved_node, context = context_result
    file_id = int(context.get("file_id") or 0)
    if not file_id:
        return None
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return None

    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            conn.row_factory = sqlite3.Row
            upstream_rows = conn.execute(
                """
                SELECT f.file_id, f.project_key, f.rel_path, p.path
                FROM dependencies d
                JOIN files f ON f.file_id = d.target_file_id
                LEFT JOIN projects p ON p.project_key = f.project_key
                WHERE d.source_file_id = ?
                ORDER BY f.project_key, f.rel_path
                LIMIT 100;
                """,
                (file_id,),
            ).fetchall()
            dependent_rows = conn.execute(
                """
                SELECT f.file_id, f.project_key, f.rel_path, p.path
                FROM dependencies d
                JOIN files f ON f.file_id = d.source_file_id
                LEFT JOIN projects p ON p.project_key = f.project_key
                WHERE d.target_file_id = ?
                ORDER BY f.project_key, f.rel_path
                LIMIT 100;
                """,
                (file_id,),
            ).fetchall()
    except Exception as exc:
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="mcp.server",
                category="storage_fallback",
                operation="sqlite_upstream_trace_lookup",
                subject=target_node,
                reason="SQLite dependency lookup failed while building an upstream trace.",
                fallback="atlas_dependency_graph",
                claim_impact="upstream_trace_requires_atlas_fallback",
                exception=exc,
            )
        except Exception:
            pass
        return None

    def path_for(row: sqlite3.Row) -> str:
        return _repo_relative_from_sqlite_row(str(row["path"] or ""), str(row["rel_path"] or ""))

    def ref_for(row: sqlite3.Row) -> str:
        return f"{row['project_key']}::{path_for(row)}"

    def context_for(row: sqlite3.Row) -> dict[str, Any]:
        repo_rel = path_for(row)
        atlas_rel = str(row["rel_path"] or "").replace("\\", "/").strip("/")
        return {
            "repo_relative_path": repo_rel,
            "atlas_node": f"{row['project_key']}::{atlas_rel}",
            "target_ref": ref_for(row),
            "project_key": str(row["project_key"] or ""),
            "source": "sqlite_dependencies",
        }

    target_ref = _target_ref_from_context(resolved_node, context)
    target_file = str(context.get("repo_relative_path") or "").replace("\\", "/").strip("/")
    upstream_refs = [ref_for(row) for row in upstream_rows]
    dependent_refs = [ref_for(row) for row in dependent_rows]
    candidate_changed_sources: list[dict[str, Any]] = []
    if isinstance(signals_data, dict):
        target_keys = {resolved_node, target_ref, target_file}
        for signal in signals_data.get("active_signals", []) or []:
            if not isinstance(signal, dict):
                continue
            signal_node = str(signal.get("node_key") or "")
            signal_path = str(signal.get("relative_path") or "")
            direct = {str(item) for item in signal.get("direct_dependents", []) or []}
            transitive = {str(item) for item in signal.get("transitive_dependents", []) or []}
            if target_keys.intersection({signal_node, signal_path}) or target_keys.intersection(direct) or target_keys.intersection(transitive):
                relation = "self" if target_keys.intersection({signal_node, signal_path}) else ("direct_dependent" if target_keys.intersection(direct) else "transitive_dependent")
                candidate_changed_sources.append(
                    {
                        "source_node": signal_node,
                        "source_path": signal_path,
                        "relation": relation,
                        "reason": f"`{target_ref}` is in the {relation} radius of active signal `{signal_node}`.",
                    }
                )

    return {
        "target": resolved_node,
        "target_ref": target_ref,
        "analysis_root": _analysis_root_display(target_root),
        "target_project": _project_from_ref(target_ref),
        "target_file": target_file,
        "target_path_status": _target_path_status(raw_dir, target_file, target_root=target_root),
        "target_file_context": {
            "repo_relative_path": target_file,
            "atlas_node": resolved_node,
            "target_ref": target_ref,
            "source": "sqlite_files",
        },
        "dependency_graph_source": "sqlite_dependencies",
        "evidence_limits": [],
        "candidate_changed_sources": candidate_changed_sources[:25],
        "upstream_dependencies": upstream_refs,
        "upstream_dependency_files": [path_for(row) for row in upstream_rows],
        "upstream_dependency_context": [context_for(row) for row in upstream_rows],
        "direct_dependents": dependent_refs,
        "direct_dependent_files": [path_for(row) for row in dependent_rows],
        "direct_dependent_context": [context_for(row) for row in dependent_rows],
        "reasoning": [
            "Upstream dependencies are files the target imports or depends on.",
            "Candidate changed sources are active ContextOS files whose radius contains the target.",
            "Use *_files fields for editor navigation and *_context / target_ref fields for SAGE graph identity.",
        ],
    }


def _impact_radius_from_raw(raw_dir: Path, target_node: str, target_root: str = "", depth: int = 2) -> dict[str, Any] | None:
    started = time.perf_counter()
    sqlite_payload = _sqlite_impact_radius_from_raw(raw_dir, target_node, target_root=target_root, depth=depth)
    if sqlite_payload is not None:
        return sqlite_payload
    if not (raw_dir / "atlas.json").exists():
        return None
    resolved_node, context = _resolve_target_node_from_raw(raw_dir, target_node)
    atlas = _atlas(raw_dir=raw_dir)
    repo_rel_by_node: dict[str, str] = {}
    for project, project_data in atlas.items():
        if not isinstance(project_data, dict):
            continue
        files = project_data.get("files") or {}
        if not isinstance(files, dict):
            continue
        for rel_path, file_info in files.items():
            atlas_rel = str(rel_path).replace("\\", "/").strip("/")
            workspace_rel = atlas_rel
            if isinstance(file_info, dict):
                workspace_rel = str(file_info.get("workspace_rel") or atlas_rel).replace("\\", "/").strip("/")
            if atlas_rel:
                repo_rel_by_node[f"{project}::{atlas_rel}"] = workspace_rel

    def repo_rel_for_node(node: str) -> str:
        if node in repo_rel_by_node:
            return repo_rel_by_node[node]
        _project, rel = node.split("::", 1) if "::" in node else ("", node)
        return rel

    def target_ref_for_node(node: str) -> str:
        project, _rel = node.split("::", 1) if "::" in node else ("", node)
        repo_rel = repo_rel_for_node(node)
        return f"{project}::{repo_rel}" if project else repo_rel

    target_ref = target_ref_for_node(resolved_node)
    target_file = context.get("repo_relative_path") or repo_rel_for_node(resolved_node)
    nodes, _edges, reverse = _dependency_graph_from_raw(raw_dir)
    radius_depth = max(0, int(depth or 0))
    if resolved_node not in nodes and resolved_node not in reverse:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        graph_source = _dependency_graph_source(raw_dir)
        return {
            "target": resolved_node,
            "target_ref": target_ref,
            "analysis_root": _analysis_root_display(target_root),
            "target_project": _project_from_ref(target_ref),
            "target_file": target_file,
            "target_path_status": _target_path_status(raw_dir, target_file, target_root=target_root),
            "radius_depth": radius_depth,
            "dependency_graph_source": graph_source,
            "evidence_limits": [
                "Atlas import fallback was used; cycle classification may require circular_deps.json."
            ] if graph_source == "atlas_imports_fallback" else [],
            "duration_ms": duration_ms,
            "status": "target_not_found_in_dependency_graph",
            "agent_action": "inspect_or_refresh_target_before_editing",
            "blast_radius_size": 0,
            "direct_dependents_count": 0,
            "direct_dependents_omitted": 0,
            "transitive_dependents_omitted": 0,
            "next_depth_hint": "",
            "direct_dependents": [],
            "direct_dependent_refs": [],
            "transitive_dependents": [],
            "transitive_dependent_refs": [],
        }
    direct_nodes = sorted(reverse.get(resolved_node, []))
    transitive_nodes = _reachable_dependents_limited(resolved_node, reverse, radius_depth)
    precomputed_counts = _precomputed_blast_counts(raw_dir, resolved_node)
    total_transitive_count = int(precomputed_counts.get("transitive") or len(transitive_nodes))
    total_direct_count = int(precomputed_counts.get("direct") or len(direct_nodes))
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    graph_source = _dependency_graph_source(raw_dir)
    return {
        "target": resolved_node,
        "target_ref": target_ref,
        "analysis_root": _analysis_root_display(target_root),
        "target_project": _project_from_ref(target_ref),
        "target_file": target_file,
        "target_path_status": _target_path_status(raw_dir, target_file, target_root=target_root),
        "radius_depth": radius_depth,
        "dependency_graph_source": graph_source,
        "evidence_limits": [
            "Atlas import fallback was used; cycle classification may require circular_deps.json."
        ] if graph_source == "atlas_imports_fallback" else [],
        "duration_ms": duration_ms,
        "slow_warning": "impact_radius_resolution_exceeded_2s" if duration_ms > 2000 else "",
        "status": "ok",
        "blast_radius_size": total_transitive_count,
        "returned_scope_size": len(transitive_nodes),
        "direct_dependents_count": total_direct_count,
        "direct_dependents_omitted": max(0, total_direct_count - len(direct_nodes)),
        "transitive_dependents_omitted": max(0, total_transitive_count - len(transitive_nodes)),
        "next_depth_hint": "Use depth=3 first when the returned scope is insufficient; use depth=0 full graph only as a last resort." if radius_depth > 0 and total_transitive_count > len(transitive_nodes) else "",
        "direct_dependents": [repo_rel_for_node(node) for node in direct_nodes],
        "direct_dependent_refs": [target_ref_for_node(node) for node in direct_nodes],
        "transitive_dependents": [repo_rel_for_node(node) for node in transitive_nodes],
        "transitive_dependent_refs": [target_ref_for_node(node) for node in transitive_nodes],
    }


def _render_test_impact_brief(payload: dict[str, Any]) -> str:
    visible_test_limit = 8
    tests = payload.get("impacted_tests") if isinstance(payload.get("impacted_tests"), list) else []
    has_error = bool(payload.get("error"))
    target_exists = bool(payload.get("target_exists", True))
    target_indexed = bool(payload.get("target_indexed", True))
    target_grounded = target_exists and target_indexed
    next_action = (
        "resolve_test_impact_error_before_finalizing"
        if has_error
        else ("refresh_target_analysis_before_test_decision" if not target_grounded else None)
    )
    if next_action is None:
        next_action = (
            "run_listed_tests_before_finalizing"
            if tests
            else "run_nearest_feature_or_package_validation"
        )
    run_commands = []
    for row in tests:
        command = str(row.get("run_command") or "").strip() if isinstance(row, dict) else ""
        if command and command not in run_commands:
            run_commands.append(command)
    if not target_grounded:
        run_commands = ["Refresh SAGE analysis for this exact target repository, then rerun get_test_impact."]
    elif not run_commands:
        run_commands.extend(_nearest_package_validation_commands(payload))
    test_impact_policy = _test_impact_brief_policy()
    yaml_lines = [
        "mission:",
        "  - Run or inspect these tests before finalizing a patch to the target file.",
        "task:",
        f"  analysis_root: {json.dumps(payload.get('analysis_root') or '', ensure_ascii=False)}",
        f"  target_project: {json.dumps(payload.get('target_project') or _project_from_ref(str(payload.get('target_ref') or payload.get('target') or '')), ensure_ascii=False)}",
        f"  target_file: {json.dumps(payload.get('target_file') or payload.get('target') or '', ensure_ascii=False)}",
        f"  target_ref: {json.dumps(payload.get('target_ref') or payload.get('target') or '', ensure_ascii=False)}",
        f"  impacted_test_count: {len(tests)}",
        f"  test_source_snippet_limit: {int(payload.get('test_source_snippet_limit') or 0)}",
        f"  test_source_snippets_attached: {int(payload.get('test_source_snippets_attached') or 0)}",
        f"  test_source_snippets_omitted: {int(payload.get('test_source_snippets_omitted') or 0)}",
        f"  target_exists: {str(target_exists).lower()}",
        f"  target_indexed: {str(target_indexed).lower()}",
        f"  target_grounding_status: {json.dumps(payload.get('target_grounding_status') or ('grounded' if target_grounded else 'missing_or_unindexed'), ensure_ascii=False)}",
        *_source_grounding_yaml_lines(payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}, max_spans=1),
        "path_contract:",
        "  open_files_with: \"analysis_root + target_file or impacted_tests.file\"",
        "  target_ref_usage: \"SAGE/MCP reference only; not a filesystem path\"",
        "  target_ref_format: \"<project>::<repo_relative_path>; MAIN is the primary analyzed project scope\"",
        "directive:",
        "  next_action: " + json.dumps(next_action, ensure_ascii=False),
        "  inspect_first:",
        "    - " + json.dumps(payload.get("target_file") or payload.get("target") or "", ensure_ascii=False),
        "  confidence_note: " + json.dumps("An empty impacted_tests list is not proof that no tests matter.", ensure_ascii=False),
        "validation:",
        "  commands:",
    ]
    yaml_lines.extend(
        [f"    - {json.dumps(command, ensure_ascii=False)}" for command in run_commands[:visible_test_limit]]
        or ["    []"]
    )
    yaml_lines.append("  command_contract_summary:")
    yaml_lines.append("    projection: " + json.dumps(test_impact_policy.get("command_contract_projection") or "", ensure_ascii=False))
    yaml_lines.append("    agent_rule: " + json.dumps(test_impact_policy.get("agent_rule") or "", ensure_ascii=False))
    yaml_lines.append("    groups:")
    yaml_lines.extend(
        [
            "      - execution_scope: " + json.dumps(contract.get("execution_scope") or "", ensure_ascii=False)
            + "\n        proof_boundary: " + json.dumps(contract.get("proof_boundary") or "", ensure_ascii=False)
            + "\n        agent_note: " + json.dumps(contract.get("agent_note") or "", ensure_ascii=False)
            + f"\n        command_count: {int(contract.get('command_count') or 0)}"
            + "\n        example_commands: " + json.dumps(contract.get("example_commands") or [], ensure_ascii=False)
            for contract in command_contract_summary_for_agent(
                run_commands,
                max_groups=int(test_impact_policy.get("max_contract_groups") or 4),
                examples_per_group=int(test_impact_policy.get("example_commands_per_group") or 1),
            )
        ]
        or ["      []"]
    )
    if has_error:
        yaml_lines.extend(
            [
                "error:",
                "  message: " + json.dumps(payload.get("error") or "", ensure_ascii=False),
            ]
        )
    yaml_lines.extend(
        [
            "impacted_tests:",
        ]
    )
    if tests:
        for row in tests[:visible_test_limit]:
            yaml_lines.append("  - file: " + json.dumps(row.get("repo_relative_path") or row.get("file") or "", ensure_ascii=False))
            yaml_lines.append("    confidence: " + json.dumps(row.get("confidence") or "", ensure_ascii=False))
            yaml_lines.append("    reason: " + json.dumps(row.get("type") or row.get("reason") or "", ensure_ascii=False))
            yaml_lines.append("    run: " + json.dumps(row.get("run_command") or "", ensure_ascii=False))
            snippets = row.get("source_snippets") if isinstance(row.get("source_snippets"), list) else []
            snippet_status = str(row.get("source_snippet_status") or ("included" if snippets else "not_available")).strip()
            snippet_note = str(row.get("source_snippet_note") or "").strip()
            yaml_lines.append("    source_snippet_status: " + json.dumps(snippet_status, ensure_ascii=False))
            if snippet_note:
                yaml_lines.append("    source_snippet_note: " + json.dumps(snippet_note, ensure_ascii=False))
            yaml_lines.append(f"    source_snippets_shown: {min(len(snippets), 2)}")
            yaml_lines.append("    source_snippets:")
            if snippets:
                for snippet in snippets[:2]:
                    if not isinstance(snippet, dict):
                        continue
                    yaml_lines.append("      - evidence: " + json.dumps(snippet.get("evidence") or "", ensure_ascii=False))
                    yaml_lines.append("        source_lines: " + json.dumps(snippet.get("source_lines") or "", ensure_ascii=False))
                    yaml_lines.append(f"        matched_line: {int(snippet.get('matched_line') or 0)}")
                    yaml_lines.append("        snippet_status: " + json.dumps(snippet.get("snippet_status") or "not_available", ensure_ascii=False))
                    code = snippet.get("code")
                    if code:
                        yaml_lines.append("        code: |-")
                        for code_line in str(code).splitlines():
                            yaml_lines.append("          " + code_line)
            else:
                yaml_lines.append("      []")
    else:
        yaml_lines.append("  []")
    yaml_lines.extend(
        [
            "do:",
            "  - Prefer the highest-confidence listed tests and inspect source_snippets before running broad test searches.",
            "  - If no tests are listed, run the nearest package or feature-level validation command.",
            "  - If target_grounding_status is not grounded, refresh analysis for the exact repository before trusting test impact.",
            "do_not:",
            "  - Do not treat an empty test-impact list as proof that no tests matter.",
            "  - Do not grep the whole test tree before using listed source_snippets and run commands.",
        ]
    )
    return "\n".join(
        [
            "# Test Impact Brief",
            "",
            "```yaml",
            *_with_context_budget(yaml_lines, budget_tokens=BOUNDED_AGENT_PACKET_TOKENS),
            "```",
            "",
        ]
    )


def _normalize_test_impact_payload_for_agent(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    tests = payload.get("impacted_tests") if isinstance(payload.get("impacted_tests"), list) else []
    normalized_tests: list[dict[str, Any]] = []
    for row in tests:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        repo_path = str(item.get("repo_relative_path") or item.get("file") or "").replace("\\", "/").strip("/")
        old_file = str(item.get("file") or "").replace("\\", "/").strip("/")
        if repo_path:
            item["file"] = repo_path
            item["repo_relative_path"] = repo_path
            command = str(item.get("run_command") or "").strip()
            if not command or (old_file and old_file != repo_path and old_file in command):
                item["run_command"] = command_for_test(repo_path)
        normalized_tests.append(item)
    normalized["impacted_tests"] = normalized_tests
    return normalized


def _is_likely_test_path(path: str) -> bool:
    return is_test_path(path)


def _base_without_test_suffix(path: str) -> str:
    return extract_logical_base_name(path).lower()


def _direct_colocated_test_candidates(analysis_root: Path, target_file_rel: str) -> list[dict[str, Any]]:
    """Find bounded live sibling tests that directly import the target, including untracked files."""
    root = analysis_root.resolve()
    target_abs = (root / target_file_rel).resolve()
    try:
        target_abs.relative_to(root)
    except ValueError:
        return []
    if not target_abs.exists() or not target_abs.is_file():
        return []

    target_stem = target_abs.stem
    escaped_stem = re.escape(target_stem)
    import_patterns = [
        re.compile(
            rf"""(?:from\s+|import\s*\(|require\s*\()\s*["']\./{escaped_stem}(?:\.[A-Za-z0-9]+)?["']"""
        ),
        re.compile(rf"""from\s+\.{escaped_stem}\s+import\s+"""),
    ]
    candidates: list[dict[str, Any]] = []
    try:
        siblings = sorted(target_abs.parent.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return []
    inspected_candidates = 0
    for candidate in siblings:
        if not candidate.is_file():
            continue
        repo_rel = candidate.relative_to(root).as_posix()
        if not _is_likely_test_path(repo_rel) or _base_without_test_suffix(repo_rel) != target_stem.lower():
            continue
        inspected_candidates += 1
        if inspected_candidates > 200:
            break
        try:
            if candidate.stat().st_size > 2_000_000:
                continue
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(pattern.search(content) for pattern in import_patterns):
            continue
        candidates.append(
            {
                "file": repo_rel,
                "repo_relative_path": repo_rel,
                "type": "Direct Co-located Static Import",
                "confidence": confidence_value("direct_static_import"),
                "run_command": command_for_test(repo_rel),
                "source": "live_bounded_sibling_scan",
            }
        )
    return candidates


def _merge_live_direct_test_candidates(
    result: dict[str, Any],
    analysis_root: Path,
    target_file_rel: str,
) -> dict[str, Any]:
    """Merge stronger live direct-import evidence into any test-impact projection."""

    existing = result.get("impacted_tests") if isinstance(result.get("impacted_tests"), list) else []
    tests = {
        str(row.get("repo_relative_path") or row.get("file") or ""): dict(row)
        for row in existing
        if isinstance(row, dict) and str(row.get("repo_relative_path") or row.get("file") or "")
    }
    for row in _direct_colocated_test_candidates(analysis_root, target_file_rel):
        tests[str(row["repo_relative_path"])] = row
    result["impacted_tests"] = sorted(
        tests.values(),
        key=lambda row: (-float(row.get("confidence") or 0), str(row.get("file") or "")),
    )
    return result


def _test_impact_from_raw(raw_dir: Path, target_file: str, target_root: str = "") -> dict[str, Any] | None:
    if not load_atlas_data(raw_dir):
        return None
    resolved_node, context = _resolve_target_node_from_raw(raw_dir, target_file)
    target_status = _target_path_status(raw_dir, target_file, target_root=target_root)
    target_file_rel = str(context.get("repo_relative_path") or target_file).replace("\\", "/")
    target_base = _base_without_test_suffix(target_file_rel)
    tests: dict[str, dict[str, Any]] = {}

    deps_payload = _load_json(raw_dir / "circular_deps.json")
    nodes, _edges, reverse = _dependency_graph_from_raw(raw_dir) if isinstance(deps_payload, dict) and deps_payload else ({}, [], {})
    if reverse:
        for dep_node in reverse.get(resolved_node, []):
            dep_rel = _repo_relative_from_node(raw_dir, dep_node)
            if _is_likely_test_path(dep_rel):
                tests[dep_rel] = {
                    "file": dep_rel,
                    "repo_relative_path": dep_rel,
                    "type": "Direct Static Import",
                    "confidence": 1.0,
                    "run_command": command_for_test(dep_rel),
                }

    atlas = _atlas(raw_dir=raw_dir)
    for project_key, project_data in atlas.items():
        files = project_data.get("files") or {}
        if not isinstance(files, dict):
            continue
        for rel_path, file_info in files.items():
            repo_rel = str((file_info or {}).get("workspace_rel") or rel_path).replace("\\", "/") if isinstance(file_info, dict) else str(rel_path)
            if not _is_likely_test_path(repo_rel):
                continue
            if _base_without_test_suffix(repo_rel) == target_base:
                tests.setdefault(
                    repo_rel,
                    {
                        "file": repo_rel,
                        "repo_relative_path": repo_rel,
                        "type": "Semantic Convention Match",
                        "confidence": 0.8,
                        "run_command": command_for_test(repo_rel),
                    },
                )

    result = {
        "target": resolved_node,
        "target_ref": _target_ref_from_node(raw_dir, resolved_node),
        "analysis_root": _analysis_root_display(target_root),
        "target_project": _project_from_ref(_target_ref_from_node(raw_dir, resolved_node)),
        "target_file": target_file_rel,
        "target_file_context": context,
        "target_path_status": target_status,
        "target_exists": bool(target_status.get("exists")),
        "target_indexed": bool(target_status.get("indexed")),
        "target_grounding_status": "grounded" if target_status.get("exists") and target_status.get("indexed") else "missing_or_unindexed",
        "impacted_tests": sorted(tests.values(), key=lambda row: (-float(row.get("confidence") or 0), str(row.get("file") or ""))),
    }
    return _merge_live_direct_test_candidates(
        result,
        Path(_analysis_root_display(target_root)),
        target_file_rel,
    )


def _normalize_confidence_payload_for_agent(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    matrix = dict(payload.get("confidence_matrix")) if isinstance(payload.get("confidence_matrix"), dict) else {}
    target_grounded = payload.get("target_exists") is True and payload.get("target_indexed") is True
    input_evidence = payload.get("input_evidence") if isinstance(payload.get("input_evidence"), dict) else {}
    dependency_evidence = input_evidence.get("circular_deps") if isinstance(input_evidence.get("circular_deps"), dict) else {}
    dependency_ready = str(dependency_evidence.get("status") or "UNKNOWN").upper() in {"PASS", "AVAILABLE"}
    merge_safety = str(matrix.get("merge_safety") or "UNKNOWN").upper()
    drift = matrix.get("architecture_drift_certainty")
    dead_code = matrix.get("dead_code_confidence")
    drift_value = float(drift) if isinstance(drift, (int, float)) else None
    dead_code_value = float(dead_code) if isinstance(dead_code, (int, float)) else None

    if not target_grounded or not dependency_ready or merge_safety.startswith("UNKNOWN"):
        aggregate_risk = "UNKNOWN"
    elif merge_safety in {"CRITICAL", "LOW"} or (drift_value is not None and drift_value >= 0.80) or (
        dead_code_value is not None and dead_code_value <= 0.50
    ):
        aggregate_risk = "HIGH"
    elif merge_safety == "MEDIUM" or (drift_value is not None and drift_value >= 0.40) or (
        dead_code_value is not None and dead_code_value <= 0.80
    ):
        aggregate_risk = "MEDIUM"
    else:
        aggregate_risk = "LOW"

    if not target_grounded:
        recommended_action = _confidence_recommended_action("target_not_grounded")
    elif not dependency_ready:
        recommended_action = _confidence_recommended_action("dependency_evidence_unavailable")
    elif aggregate_risk in {"HIGH", "MEDIUM"}:
        recommended_action = _confidence_recommended_action("elevated_risk")
    else:
        recommended_action = _confidence_recommended_action("bounded_patch")

    matrix["aggregate_risk"] = aggregate_risk
    normalized["target_exists"] = payload.get("target_exists") is True
    normalized["target_indexed"] = payload.get("target_indexed") is True
    if not target_grounded and not str(payload.get("target_grounding_status") or "").strip():
        normalized["target_grounding_status"] = "missing_or_unindexed"
    normalized["confidence_matrix"] = matrix
    normalized["recommended_action"] = recommended_action
    return normalized


def _render_confidence_brief(payload: dict[str, Any]) -> str:
    payload = _normalize_confidence_payload_for_agent(payload)
    matrix = payload.get("confidence_matrix") if isinstance(payload.get("confidence_matrix"), dict) else {}
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    target_exists = payload.get("target_exists") is True
    target_indexed = payload.get("target_indexed") is True
    target_grounded = target_exists and target_indexed
    input_evidence = payload.get("input_evidence") if isinstance(payload.get("input_evidence"), dict) else {}
    dependency_evidence = input_evidence.get("circular_deps") if isinstance(input_evidence.get("circular_deps"), dict) else {}
    dependency_status = str(dependency_evidence.get("status") or "UNKNOWN").upper()
    aggregate_risk = str(matrix.get("aggregate_risk") or "UNKNOWN").upper()
    next_action = str(payload.get("recommended_action") or _confidence_recommended_action(""))
    yaml_lines = [
        "mission:",
        "  - Use this risk estimate to decide how cautious the patch should be.",
        "task:",
        f"  analysis_root: {json.dumps(payload.get('analysis_root') or '', ensure_ascii=False)}",
        f"  target_project: {json.dumps(payload.get('target_project') or _project_from_ref(str(payload.get('target_ref') or payload.get('target') or '')), ensure_ascii=False)}",
        f"  target_file: {json.dumps(payload.get('target_file') or payload.get('target') or '', ensure_ascii=False)}",
        f"  target_ref: {json.dumps(payload.get('target_ref') or payload.get('target') or '', ensure_ascii=False)}",
        f"  target_exists: {str(target_exists).lower()}",
        f"  target_indexed: {str(target_indexed).lower()}",
        f"  target_grounding_status: {json.dumps(payload.get('target_grounding_status') or ('grounded' if target_grounded else 'missing_or_unindexed'), ensure_ascii=False)}",
        *_source_grounding_yaml_lines(payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}, max_spans=1),
        "path_contract:",
        "  open_files_with: " + json.dumps(
            "analysis_root + target_file" if target_grounded else "not_available_target_missing_or_unindexed",
            ensure_ascii=False,
        ),
        "  target_ref_usage: \"SAGE/MCP reference only; not a filesystem path\"",
        "  target_ref_format: \"<project>::<repo_relative_path>; MAIN is the primary analyzed project scope\"",
        "directive:",
        "  next_action: " + json.dumps(next_action, ensure_ascii=False),
        "  inspect_first:",
        *(
            ["    - " + json.dumps(payload.get("target_file") or payload.get("target") or "", ensure_ascii=False)]
            if target_grounded
            else ["    []"]
        ),
        "confidence_matrix:",
        f"  merge_safety: {json.dumps('UNKNOWN_TARGET_NOT_GROUNDED' if not target_grounded else (matrix.get('merge_safety') or ''), ensure_ascii=False)}",
        f"  architecture_drift_certainty: {json.dumps(None if not target_grounded else matrix.get('architecture_drift_certainty'), ensure_ascii=False)}",
        f"  dead_code_confidence: {json.dumps(None if not target_grounded else matrix.get('dead_code_confidence'), ensure_ascii=False)}",
        f"  aggregate_risk: {json.dumps(aggregate_risk, ensure_ascii=False)}",
        "  decision_boundary: " + json.dumps(payload.get("decision_boundary") or "Confidence is a risk estimate, not a standalone merge or deploy approval.", ensure_ascii=False),
        "input_evidence:",
        "  circular_deps:",
        f"    status: {json.dumps(dependency_status, ensure_ascii=False)}",
        f"    source: {json.dumps(dependency_evidence.get('source') or 'missing', ensure_ascii=False)}",
        f"    shape_status: {json.dumps(dependency_evidence.get('shape_status') or 'unknown', ensure_ascii=False)}",
        "reasons:",
    ]
    yaml_lines.extend([f"  - {json.dumps(item, ensure_ascii=False)}" for item in reasons[:12]] or ["  []"])
    yaml_lines.extend(
        [
            "do:",
            "  - If target_grounding_status is not grounded, refresh analysis for the exact repository before trusting this score.",
            "  - If circular_deps input evidence is not PASS or AVAILABLE, refresh dependency evidence before trusting merge safety.",
            "  - Treat merge_safety as the dependency-blast dimension only; SAFE cannot override architecture, dead-code, or missing-evidence risk.",
            "  - If aggregate_risk is MEDIUM or HIGH, inspect architecture evidence, impact radius, and tests before editing.",
            "do_not:",
            "  - Do not use this score as a standalone approval to merge.",
        ]
    )
    return "\n".join(["# Confidence Brief", "", "```yaml", *_with_context_budget(yaml_lines), "```", ""])


def _confidence_from_raw(raw_dir: Path, target_file: str, target_root: str = "") -> dict[str, Any] | None:
    if not load_atlas_data(raw_dir):
        return None
    resolved_node, context = _resolve_target_node_from_raw(raw_dir, target_file)
    target_status = _target_path_status(raw_dir, target_file, target_root=target_root)
    target_rel = str(context.get("repo_relative_path") or target_file).replace("\\", "/")
    content = ""
    if target_root:
        target = _valid_external_target(target_root)
        if target is not None:
            candidate = (target / target_rel).resolve()
            try:
                candidate.relative_to(target.resolve())
                if candidate.exists() and candidate.is_file():
                    content = candidate.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                content = ""

    deps_payload = _load_json(raw_dir / "circular_deps.json")
    deps_meta = artifact_state_meta(raw_dir, "circular_deps")
    deps_shape_valid = isinstance(deps_payload, dict) and isinstance(deps_payload.get("edges"), list) and isinstance(deps_payload.get("cycles"), list)
    deps_ready = bool(deps_meta.get("exists")) and deps_shape_valid
    nodes, _edges, reverse = _dependency_graph_from_raw(raw_dir) if deps_ready else ({}, [], {})
    dependent_count = len(reverse.get(resolved_node, [])) if deps_ready else 0
    loc_count = len(content.splitlines()) if content else 0
    reasons: list[str] = []
    merge_safety = "SAFE" if deps_ready else "UNKNOWN"
    if not deps_ready:
        reasons.append("Dependency evidence is unavailable or invalid; merge safety cannot be classified.")
    if dependent_count >= 10:
        merge_safety = "CRITICAL"
        reasons.append(f"High direct dependent count: {dependent_count}")
    elif dependent_count >= 5:
        merge_safety = "LOW"
        reasons.append(f"Elevated direct dependent count: {dependent_count}")
    elif dependent_count >= 1:
        merge_safety = "MEDIUM"
        reasons.append(f"Direct dependent count: {dependent_count}")
    if loc_count > 400:
        merge_safety = "MEDIUM" if merge_safety == "SAFE" else merge_safety
        reasons.append(f"Large file: {loc_count} LOC")
    relative_import_count = content.count("../") if content else 0
    drift = min(0.95, round(0.10 + relative_import_count * 0.15, 2)) if relative_import_count else 0.10
    if relative_import_count:
        reasons.append(f"Contains {relative_import_count} parent-directory relative imports")
    target_grounded = bool(target_status.get("exists")) and bool(target_status.get("indexed"))
    if not target_grounded:
        merge_safety = "UNKNOWN_TARGET_NOT_GROUNDED"
        reasons.append("Target file is missing from disk or not present in the current Atlas index.")
    return {
        "target": resolved_node,
        "target_ref": _target_ref_from_node(raw_dir, resolved_node),
        "analysis_root": _analysis_root_display(target_root),
        "target_project": _project_from_ref(_target_ref_from_node(raw_dir, resolved_node)),
        "target_file": target_rel,
        "target_path_status": target_status,
        "target_exists": bool(target_status.get("exists")),
        "target_indexed": bool(target_status.get("indexed")),
        "target_grounding_status": "grounded" if target_status.get("exists") and target_status.get("indexed") else "missing_or_unindexed",
        "confidence_matrix": {
            "dead_code_confidence": None,
            "merge_safety": merge_safety,
            "architecture_drift_certainty": drift,
            "dynamic_magic_hazard": None,
        },
        "metrics": {
            "loc": loc_count,
            "blast_radius_dependents": dependent_count,
        },
        "input_evidence": {
            "circular_deps": {
                "status": "AVAILABLE" if deps_ready else "UNKNOWN",
                "source": str(deps_meta.get("source") or "missing"),
                "shape_status": "valid" if deps_shape_valid else "invalid",
                "payload_bytes": int(deps_meta.get("payload_bytes") or 0),
                "updated_at": str(deps_meta.get("updated_at") or ""),
            }
        },
        "reasons": reasons,
        "verdict": "Target-root scoped static confidence estimate; run full confidence engine for complete workspace verdict.",
        "decision_boundary": "Confidence is a risk estimate, not a standalone merge or deploy approval.",
    }


def _render_upstream_trace_brief(payload: dict[str, Any]) -> str:
    upstream_dependency_limit = 4
    changed_source_limit = 2
    direct_dependent_limit = 4
    evidence_snippet_limit = 2
    traces = payload.get("traces") or payload.get("upstream_traces") or payload.get("candidates") or []
    traces = traces if isinstance(traces, list) else []
    upstream_files = payload.get("upstream_dependency_files") if isinstance(payload.get("upstream_dependency_files"), list) else []
    if isinstance(payload.get("direct_dependent_files"), list):
        direct_dependent_files = payload.get("direct_dependent_files") or []
    elif isinstance(payload.get("direct_dependents"), list):
        direct_dependent_files = payload.get("direct_dependents") or []
    else:
        direct_dependent_files = []
    direct_dependent_path_set = {
        str(path or "").replace("\\", "/").strip("/")
        for path in direct_dependent_files
        if str(path or "").strip()
    }
    changed_sources = payload.get("candidate_changed_sources") if isinstance(payload.get("candidate_changed_sources"), list) else []
    target = str(payload.get("target") or "")
    target_status = payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}
    target_exists = bool(target_status.get("exists", True))
    target_indexed = bool(target_status.get("indexed", True))
    target_grounded = target_exists and target_indexed
    has_upstream_evidence = bool(traces or upstream_files or changed_sources)
    upstream_shown = (
        min(len(traces), upstream_dependency_limit)
        if traces
        else min(len(upstream_files), upstream_dependency_limit) + min(len(changed_sources), changed_source_limit)
    )
    upstream_total = len(traces) + len(upstream_files) + len(changed_sources)
    direct_shown = min(len(direct_dependent_files), direct_dependent_limit)
    target_ref = str(payload.get("target_ref") or target)
    target_project = str(payload.get("target_project") or _project_from_ref(str(payload.get("target_ref") or target)))
    yaml_lines = [
        "mission:",
        "  - Use this trace to look upstream before fixing a symptom locally.",
        "task:",
        f"  analysis_root: {json.dumps(payload.get('analysis_root') or '', ensure_ascii=False)}",
        f"  target_project: {json.dumps(target_project, ensure_ascii=False)}",
        f"  target_file: {json.dumps(payload.get('target_file') or target, ensure_ascii=False)}",
        f"  target_ref: {json.dumps(target_ref, ensure_ascii=False)}",
        "  evidence_basis: \"static dependency graph plus active change signals when available\"",
        f"  target_exists: {str(target_exists).lower()}",
        f"  target_indexed: {str(target_indexed).lower()}",
        f"  target_grounding_status: {json.dumps('grounded' if target_grounded else 'missing_or_unindexed', ensure_ascii=False)}",
        *_source_grounding_yaml_lines(target_status, max_spans=1),
        f"  upstream_candidate_count: {len(traces) + len(upstream_files) + len(changed_sources)}",
        f"  upstream_candidates_shown: {upstream_shown}",
        f"  upstream_candidates_omitted: {max(0, upstream_total - upstream_shown)}",
        f"  upstream_dependency_count: {len(upstream_files)}",
        f"  direct_dependent_count: {len(direct_dependent_files)}",
        f"  direct_dependents_shown: {direct_shown}",
        f"  candidate_changed_source_count: {len(changed_sources)}",
        "path_contract:",
        "  open_files_with: \"analysis_root + target_file or upstream candidate file\"",
        "  target_ref_usage: \"SAGE/MCP reference only; not a filesystem path\"",
        "  target_ref_format: \"<project>::<repo_relative_path>; MAIN is the primary analyzed project scope\"",
        "follow_up:",
        "  when_to_use: \"Only request broader impact if upstream candidates do not explain the symptom or public behavior may change.\"",
        f"  downstream_impact: {json.dumps(f'get_impact_radius(target_node={target_ref!r}, depth=2)', ensure_ascii=False)}",
        f"  broader_downstream_impact: {json.dumps(f'get_impact_radius(target_node={target_ref!r}, depth=3)', ensure_ascii=False)}",
        "directive:",
        "  next_action: " + json.dumps("refresh_target_analysis_before_upstream_decision" if not target_grounded else ("inspect_upstream_candidates_before_local_patch" if has_upstream_evidence else "patch_target_only_after_local_evidence"), ensure_ascii=False),
        "  inspect_first:",
        "    - " + json.dumps(payload.get("target_file") or target, ensure_ascii=False),
        "upstream_candidates:",
    ]
    if payload.get("dependency_graph_source"):
        yaml_lines.insert(8, f"  dependency_graph_source: {json.dumps(payload.get('dependency_graph_source'), ensure_ascii=False)}")
    if traces:
        for item in traces[:upstream_dependency_limit]:
            file_path, item_ref = _agent_file_ref(str(item), target_project)
            yaml_lines.append("  - file: " + json.dumps(file_path, ensure_ascii=False))
            yaml_lines.append("    target_ref: " + json.dumps(item_ref, ensure_ascii=False))
            yaml_lines.append("    relation: \"upstream_candidate\"")
            if file_path in direct_dependent_path_set:
                yaml_lines.append("    bidirectional_dependency: true")
                yaml_lines.append("    agent_note: \"This candidate is also a direct dependent; inspect both import directions before editing.\"")
    elif upstream_files or changed_sources:
        for path in upstream_files[:upstream_dependency_limit]:
            file_path, item_ref = _agent_file_ref(str(path), target_project)
            yaml_lines.append("  - file: " + json.dumps(file_path, ensure_ascii=False))
            yaml_lines.append("    target_ref: " + json.dumps(item_ref, ensure_ascii=False))
            yaml_lines.append("    relation: \"upstream_dependency\"")
            yaml_lines.append("    reason: \"Target imports or depends on this file; inspect before patching a downstream symptom.\"")
            if file_path in direct_dependent_path_set:
                yaml_lines.append("    bidirectional_dependency: true")
                yaml_lines.append("    agent_note: \"This dependency is also a direct dependent; inspect both import directions before editing.\"")
        for row in changed_sources[:changed_source_limit]:
            if isinstance(row, dict):
                file_path, item_ref = _agent_file_ref(str(row.get("source_path") or row.get("source_node") or ""), target_project)
                yaml_lines.append("  - file: " + json.dumps(file_path, ensure_ascii=False))
                yaml_lines.append("    target_ref: " + json.dumps(item_ref, ensure_ascii=False))
                yaml_lines.append("    relation: " + json.dumps(row.get("relation") or "candidate_changed_source", ensure_ascii=False))
                yaml_lines.append("    reason: " + json.dumps(row.get("reason") or "Active ContextOS source may explain the target symptom.", ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    evidence_rows = payload.get("upstream_dependency_evidence") if isinstance(payload.get("upstream_dependency_evidence"), list) else []
    yaml_lines.append("dependency_evidence_snippets:")
    if evidence_rows:
        for row in evidence_rows[:evidence_snippet_limit]:
            if not isinstance(row, dict):
                continue
            yaml_lines.append("  - file: " + json.dumps(row.get("file") or "", ensure_ascii=False))
            yaml_lines.append("    target_ref: " + json.dumps(row.get("target_ref") or "", ensure_ascii=False))
            yaml_lines.append("    import_specifier: " + json.dumps(row.get("import_specifier") or "", ensure_ascii=False))
            yaml_lines.append("    snippet_status: " + json.dumps(row.get("snippet_status") or "not_available", ensure_ascii=False))
            snippets = row.get("source_snippets") if isinstance(row.get("source_snippets"), list) else []
            yaml_lines.append(f"    source_snippets_shown: {min(len(snippets), 1)}")
            yaml_lines.append("    source_snippets:")
            if snippets:
                for snippet in snippets[:1]:
                    if not isinstance(snippet, dict):
                        continue
                    yaml_lines.append("      - evidence: " + json.dumps(snippet.get("evidence") or "", ensure_ascii=False))
                    yaml_lines.append("        source_lines: " + json.dumps(snippet.get("source_lines") or "", ensure_ascii=False))
                    yaml_lines.append(f"        matched_line: {int(snippet.get('matched_line') or 0)}")
                    yaml_lines.append("        snippet_status: " + json.dumps(snippet.get("snippet_status") or "not_available", ensure_ascii=False))
                    code = snippet.get("code")
                    if code:
                        yaml_lines.append("        code: |-")
                        for code_line in str(code).splitlines():
                            yaml_lines.append("          " + code_line)
            else:
                yaml_lines.append("      []")
    else:
        yaml_lines.append("  []")
    yaml_lines.append(f"dependency_evidence_snippets_omitted: {int(payload.get('upstream_dependency_evidence_omitted') or 0)}")
    yaml_lines.append("direct_dependents_sample:")
    if direct_dependent_files:
        for path in direct_dependent_files[:direct_dependent_limit]:
            file_path, item_ref = _agent_file_ref(str(path), target_project)
            yaml_lines.append("  - file: " + json.dumps(file_path, ensure_ascii=False))
            yaml_lines.append("    target_ref: " + json.dumps(item_ref, ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    limits = payload.get("evidence_limits") if isinstance(payload.get("evidence_limits"), list) else []
    if limits:
        yaml_lines.append("evidence_limits:")
        yaml_lines.extend("  - " + json.dumps(str(item), ensure_ascii=False) for item in limits[:5])
    yaml_lines.extend(
        [
            "do:",
            "  - If target_grounding_status is not grounded, refresh analysis for the exact repository before trusting upstream trace.",
            "  - Inspect dependency_evidence_snippets first; they show the target-file import/call line that links to each upstream candidate when available.",
            "  - Inspect upstream candidates before patching the target if the same failure can originate there.",
            "  - Use direct_dependents_sample to understand likely downstream blast before changing public behavior.",
            "do_not:",
            "  - Do not edit upstream files unless the code confirms causality.",
            "  - Do not grep broadly for upstream causality before using dependency_evidence_snippets and listed candidates.",
            "  - Do not expand scope only because more upstream or dependent files exist.",
        ]
    )
    return "\n".join(
        [
            "# Upstream Cause Brief",
            "",
            "```yaml",
            *_with_context_budget(yaml_lines, budget_tokens=BOUNDED_AGENT_PACKET_TOKENS),
            "```",
            "",
        ]
    )


def _render_patch_validation_brief(payload: dict[str, Any], target_file: str, target_root: str = "") -> str:
    violations = payload.get("violations") if isinstance(payload.get("violations"), list) else []
    existing_violations = payload.get("existing_violations") if isinstance(payload.get("existing_violations"), list) else []
    resolved_violations = payload.get("resolved_violations") if isinstance(payload.get("resolved_violations"), list) else []
    audit_baseline = payload.get("current_audit_baseline") if isinstance(payload.get("current_audit_baseline"), dict) else {}
    status = str(payload.get("status") or "").upper()
    no_op_patch = bool(payload.get("no_op_patch"))
    invalid_patch = bool(payload.get("invalid_patch"))
    full_replacement_patch = bool(payload.get("full_replacement_patch"))
    target_exists = payload.get("target_exists") is True
    target_indexed = payload.get("target_indexed") is True
    target_grounded = target_exists and target_indexed
    applicability = payload.get("patch_applicability") if isinstance(payload.get("patch_applicability"), dict) else {}
    target_native = payload.get("target_native_policy_validation") if isinstance(payload.get("target_native_policy_validation"), dict) else {}
    safe_to_apply = (status == "PASS" and not violations) if payload.get("safe_to_apply") is None else bool(payload.get("safe_to_apply"))
    if not target_grounded:
        safe_to_apply = False
        next_action = "refresh_target_analysis_or_request_create_file_approval"
    elif invalid_patch:
        safe_to_apply = False
        next_action = "provide_valid_patch_before_validation"
    elif no_op_patch:
        safe_to_apply = False
        next_action = "provide_non_empty_patch_before_validation"
    elif full_replacement_patch:
        safe_to_apply = False
        next_action = "request_human_approval_or_provide_bounded_unified_diff"
    elif str(applicability.get("status") or "").upper() == "FAIL":
        safe_to_apply = False
        next_action = "repair_exact_patch_payload_before_apply"
    elif str(applicability.get("status") or "").upper() == "UNKNOWN":
        safe_to_apply = False
        next_action = "retry_bounded_applicability_or_request_review"
    elif str(target_native.get("status") or "").upper() != "PASS":
        safe_to_apply = False
        next_action = "run_target_native_policy_and_validation_oracles_before_apply"
    else:
        next_action = "apply_patch_only_if_requested_and_in_scope" if safe_to_apply else "revise_patch_before_apply"
    overall_status = status
    if overall_status in {"PASS", "OK"} and not safe_to_apply:
        overall_status = "INCOMPLETE_EVIDENCE"
    next_action_contract = _patch_validation_next_action_contract(next_action)
    target_ref = str(payload.get("target_ref") or (target_file if "::" in str(target_file or "") else ""))
    target_file_display = str(payload.get("target_file") or (str(target_file or "").split("::", 1)[1] if "::" in str(target_file or "") else str(target_file or "")))
    yaml_lines = [
        "mission:",
        "  - Use this result before applying the proposed patch.",
        "task:",
        f"  analysis_root: {json.dumps(payload.get('analysis_root') or _analysis_root_display(target_root), ensure_ascii=False)}",
        f"  target_project: {json.dumps(payload.get('target_project') or _project_from_ref(target_ref), ensure_ascii=False)}",
        f"  target_file: {json.dumps(target_file_display, ensure_ascii=False)}",
        f"  target_ref: {json.dumps(target_ref, ensure_ascii=False)}",
        f"  status: {json.dumps(overall_status, ensure_ascii=False)}",
        f"  governance_status: {json.dumps(payload.get('governance_status') or ('PASS' if payload.get('governance_validation_passed') else status), ensure_ascii=False)}",
        f"  governance_validation_passed: {str(bool(payload.get('governance_validation_passed'))).lower()}",
        f"  safe_to_apply: {str(safe_to_apply).lower()}",
        f"  application_readiness: {json.dumps(payload.get('application_readiness') or 'INCOMPLETE_EVIDENCE', ensure_ascii=False)}",
        f"  no_op_patch: {str(no_op_patch).lower()}",
        f"  invalid_patch: {str(invalid_patch).lower()}",
        f"  full_replacement_patch: {str(full_replacement_patch).lower()}",
        f"  human_approval_required: {str(bool(payload.get('human_approval_required'))).lower()}",
        f"  violation_count: {len(violations)}",
        f"  unchanged_sage_governance_violation_count: {len(existing_violations)}",
        f"  resolved_sage_governance_violation_count: {len(resolved_violations)}",
        f"  patch_validator_baseline_violation_count: {len(existing_violations) + len(resolved_violations)}",
        f"  current_audit_baseline_status: {json.dumps(audit_baseline.get('status') or 'unavailable', ensure_ascii=False)}",
        f"  current_audit_finding_count: {json.dumps(audit_baseline.get('finding_count'), ensure_ascii=False)}",
        f"  analysis_snapshot_id: {json.dumps(payload.get('analysis_snapshot_id') or '', ensure_ascii=False)}",
        f"  target_exists: {str(target_exists).lower()}",
        f"  target_indexed: {str(target_indexed).lower()}",
        "patch_applicability:",
        "  status: " + json.dumps(applicability.get("status") or "NOT_RUN", ensure_ascii=False),
        "  oracle: " + json.dumps(applicability.get("oracle") or "", ensure_ascii=False),
        f"  mutation_performed: {str(bool(applicability.get('mutation_performed'))).lower()}",
        "  stderr_excerpt: " + json.dumps(applicability.get("stderr_excerpt") or "", ensure_ascii=False),
        "target_native_policy_validation:",
        "  status: " + json.dumps(target_native.get("status") or "NOT_RUN", ensure_ascii=False),
        "  claim_boundary: " + json.dumps(target_native.get("claim_boundary") or "", ensure_ascii=False),
        *_source_grounding_yaml_lines(payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}, max_spans=1),
        "proof_boundary:",
        "  validates: \"patch format, target grounding, path safety, configured SAGE governance checks and exact Git applicability when patch_applicability is PASS\"",
        "  patch_validator_baseline_semantics: \"unchanged + resolved findings in this patch validator's configured rule scope\"",
        "  current_audit_baseline_semantics: \"current SQLite Audit findings for the grounded target file and project; not patch-delta findings or target-native enforcement truth\"",
        "  does_not_validate: \"business correctness, target-native policy unless explicitly PASS, runtime behavior, or full test success\"",
        "  semantic_quality_status: \"not_proven_by_this_tool\"",
        "path_contract:",
        "  open_files_with: " + json.dumps(
            "analysis_root + target_file" if target_grounded else "not_available_target_missing_or_unindexed",
            ensure_ascii=False,
        ),
        "  target_ref_usage: \"SAGE/MCP reference only; not a filesystem path\"",
        "  target_ref_format: \"<project>::<repo_relative_path>; MAIN is the primary analyzed project scope\"",
        "directive:",
        "  next_action: " + json.dumps(next_action_contract["id"], ensure_ascii=False),
        "  instruction: " + json.dumps(next_action_contract["instruction"], ensure_ascii=False),
        "  agent_rule: " + json.dumps(next_action_contract["agent_rule"], ensure_ascii=False),
        "  inspect_first:",
        *(["    - " + json.dumps(target_file_display, ensure_ascii=False)] if target_grounded else ["    []"]),
        "proposed_change_snippets:",
    ]
    change_snippets = payload.get("proposed_change_snippets") if isinstance(payload.get("proposed_change_snippets"), list) else []
    if change_snippets:
        for snippet in change_snippets[:3]:
            if not isinstance(snippet, dict):
                continue
            yaml_lines.append("  - hunk_header: " + json.dumps(snippet.get("hunk_header") or "", ensure_ascii=False))
            yaml_lines.append("    snippet_status: " + json.dumps(snippet.get("snippet_status") or "not_available", ensure_ascii=False))
            yaml_lines.append(f"    omitted_changed_lines: {int(snippet.get('omitted_changed_lines') or 0)}")
            yaml_lines.append("    changed_lines:")
            changed_lines = snippet.get("changed_lines") if isinstance(snippet.get("changed_lines"), list) else []
            if changed_lines:
                yaml_lines.extend("      - " + json.dumps(str(line), ensure_ascii=False) for line in changed_lines[:12])
            else:
                yaml_lines.append("      []")
    else:
        yaml_lines.append("  []")
    yaml_lines.extend([
        "violations:",
    ])
    if violations:
        for row in violations[:20]:
            if isinstance(row, dict):
                yaml_lines.append("  - rule: " + json.dumps(row.get("rule") or "", ensure_ascii=False))
                yaml_lines.append("    detail: " + json.dumps(row.get("detail") or "", ensure_ascii=False))
                yaml_lines.append("    recommended_action: " + json.dumps(row.get("recommended_action") or "", ensure_ascii=False))
            else:
                yaml_lines.append("  - " + json.dumps(str(row), ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    yaml_lines.append("existing_violations_context:")
    if existing_violations:
        for row in existing_violations[:10]:
            if isinstance(row, dict):
                yaml_lines.append("  - rule: " + json.dumps(row.get("rule") or "", ensure_ascii=False))
                yaml_lines.append("    detail: " + json.dumps(row.get("detail") or "", ensure_ascii=False))
                yaml_lines.append("    baseline_status: " + json.dumps(row.get("baseline_status") or "pre_existing_non_blocking", ensure_ascii=False))
            else:
                yaml_lines.append("  - " + json.dumps(str(row), ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    yaml_lines.extend(
        [
            "do:",
            "  - Provide a valid unified diff or full replacement content if invalid_patch is true.",
            "  - Provide a non-empty patch before validation if no_op_patch is true.",
            "  - Apply the patch only when exact applicability and required target-native policy/test validation are PASS and the task scope is still correct.",
            "  - If violations exist, make the smallest change that satisfies the listed rule and rerun validation.",
            "  - Treat existing_violations_context as visible technical debt, not as proof that the proposed patch created it.",
            "do_not:",
            "  - Do not infer apply authority from governance PASS; safe_to_apply remains false while target-native validation is unproven.",
            "  - Do not treat PASS as permission for unrelated refactors.",
            "  - Do not bypass path-safety or doctrine violations with allowlists unless a human explicitly approves.",
        ]
    )
    return "\n".join(["# Patch Validation Brief", "", "```yaml", *_with_context_budget(yaml_lines), "```", ""])


def _render_supporting_context_brief(title: str, payload: dict[str, Any]) -> str:
    rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    surface = str(payload.get("surface") or title)
    resolved_title = _supporting_context_title(surface, title)
    do_lines = _supporting_context_actions(surface)
    if surface == "blast_radius" and not rows:
        do_lines = _supporting_context_actions(surface, empty_result=True)
    open_files_with = (
        "No direct edit target; first request a concrete target_file from a primary agent tool."
        if surface == "health_metrics"
        else "analysis_root + listed file/path fields"
    )
    mission_line = (
        "Use this bounded triage context before selecting a concrete edit target."
        if surface == "health_metrics"
        else "Use this bounded context only after a concrete edit target exists."
    )
    yaml_lines = [
        "mission:",
        f"  - {mission_line}",
        "task:",
        f"  analysis_root: {json.dumps(payload.get('analysis_root') or _analysis_root_display(), ensure_ascii=False)}",
        f"  surface: {json.dumps(surface, ensure_ascii=False)}",
        f"  filter: {json.dumps(payload.get('filter') or '', ensure_ascii=False)}",
        f"  returned: {len(rows)}",
        f"  status: {json.dumps(payload.get('status') or 'ok', ensure_ascii=False)}",
        "path_contract:",
        f"  open_files_with: {json.dumps(open_files_with, ensure_ascii=False)}",
        "  target_ref_usage: \"SAGE/MCP reference only; not a filesystem path\"",
        "  target_ref_format: \"<project>::<repo_relative_path>; MAIN is the primary analyzed project scope\"",
        "  refs_are_not_paths: true",
    ]
    if surface == "dead_code":
        has_candidates = bool(rows)
        yaml_lines.extend(
            [
                "dead_code_triage:",
                "  actionability: "
                + json.dumps("candidate_review" if has_candidates else "no_action", ensure_ascii=False),
                "  file_imported: "
                + json.dumps("reported_per_candidate" if has_candidates else "not_applicable_without_candidate", ensure_ascii=False),
                "  symbol_seen_globally: "
                + json.dumps("reported_per_candidate" if has_candidates else "not_applicable_without_candidate", ensure_ascii=False),
                "  local_symbol_usage: "
                + json.dumps("reported_per_candidate" if has_candidates else "not_applicable_without_candidate", ensure_ascii=False),
                "  absence_claim: "
                + json.dumps(
                    "candidate_rows_available_for_review"
                    if has_candidates
                    else "no_candidate_in_filtered_sage_artifact_not_repository_clean",
                    ensure_ascii=False,
                ),
            ]
        )
    if surface == "state_flow":
        state_flow_policy = _state_flow_brief_policy()
        yaml_lines.extend(
            [
                "state_flow_policy:",
                "  max_items_semantics: " + json.dumps(state_flow_policy["max_items_semantics"], ensure_ascii=False),
                "  agent_rule: " + json.dumps(state_flow_policy["agent_rule"], ensure_ascii=False),
            ]
        )
    yaml_lines.append("items:")
    if rows:
        for row in rows[:20]:
            if isinstance(row, dict):
                yaml_lines.append("  - " + json.dumps(row, ensure_ascii=False))
            else:
                yaml_lines.append("  - " + json.dumps(str(row), ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    yaml_lines.extend(
        [
            "do:",
            *[f"  - {line}" for line in do_lines],
            "  - Treat this as context, not as permission to broaden the patch.",
            "do_not:",
            "  - Do not edit from this summary alone.",
            "  - Do not treat graph references, sample keys, or evidence lists as filesystem paths unless the packet provides target_file or chain_targets.",
            "  - Do not assume missing items means the repository has no risk; it may mean the relevant artifact has not been generated.",
        ]
    )
    return "\n".join([f"# {resolved_title}", "", "```yaml", *_with_context_budget(yaml_lines), "```", ""])


def _render_module_integrity_brief(payload: dict[str, Any]) -> str:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    yaml_lines = [
        "mission:",
        "  - Use this bounded module integrity brief after choosing a concrete edit target.",
        "task:",
        "  analysis_root: " + json.dumps(payload.get("analysis_root") or _analysis_root_display(), ensure_ascii=False),
        "  filter: " + json.dumps(payload.get("filter") or "", ensure_ascii=False),
        "  status: " + json.dumps(payload.get("status") or "", ensure_ascii=False),
        f"  returned: {len(items)}",
        "path_contract:",
        "  open_files_with: \"analysis_root + target_file or inspect_first item\"",
        "  target_ref_usage: \"SAGE/MCP reference only; not a filesystem path\"",
        "  target_ref_format: \"<project>::<repo_relative_path>; MAIN is the primary analyzed project scope\"",
        "  refs_are_not_paths: true",
        "items:",
    ]
    if items:
        for row in items[:20]:
            yaml_lines.append("  - file: " + json.dumps(row.get("file") or "", ensure_ascii=False))
            yaml_lines.append("    target_file: " + json.dumps(row.get("target_file") or row.get("file") or "", ensure_ascii=False))
            yaml_lines.append("    target_ref: " + json.dumps(row.get("target_ref") or "", ensure_ascii=False))
            yaml_lines.append("    target_status: " + json.dumps(row.get("target_status") or {}, ensure_ascii=False))
            yaml_lines.append("    rule: " + json.dumps(row.get("rule") or "", ensure_ascii=False))
            yaml_lines.append("    label: " + json.dumps(row.get("label") or "", ensure_ascii=False))
            yaml_lines.append("    priority: " + json.dumps(row.get("priority") or "", ensure_ascii=False))
            yaml_lines.append("    evidence: " + json.dumps(row.get("evidence") or "", ensure_ascii=False))
            yaml_lines.append("    why_it_matters: " + json.dumps(row.get("why_it_matters") or "", ensure_ascii=False))
            yaml_lines.append("    fix_strategy: " + json.dumps(row.get("fix_strategy") or "", ensure_ascii=False))
            yaml_lines.append("    inspect_first: " + json.dumps(row.get("inspect_first") or [], ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    yaml_lines.extend(
        [
            "do:",
            "  - Inspect the listed target file before editing.",
            "  - Apply one rule-specific minimal patch at a time.",
            "  - Treat this as context, not as permission to broaden the patch.",
            "do_not:",
            "  - Do not treat this module summary as permission for broad cleanup.",
            "  - Do not add suppressions or allowlists without explicit human approval.",
        ]
    )
    return "\n".join(["# Module Integrity Brief", "", "```yaml", *_with_context_budget(yaml_lines), "```", ""])


def _artifact_or_missing(raw_dir: Path, filename: str, tool_name: str, target: str, target_root: str) -> tuple[Any | None, str | None]:
    path = raw_dir / filename
    data = _load_json(path)
    if data is None:
        return None, _missing_target_artifact_brief(tool_name, target, target_root, [filename])
    return data, None


def _stable_audit_work_item_id(
    project_key: str,
    file_path: str,
    rule_id: str,
    detail: str,
) -> str:
    """Build an agent-facing identity from semantic evidence, not SQLite row order."""
    normalized_path = repair_text(str(file_path or "")).replace("\\", "/").strip()
    normalized_path = "" if not normalized_path else posixpath.normpath(normalized_path).strip("/")
    identity = json.dumps(
        {
            "project": repair_text(str(project_key or "UNKNOWN")).strip(),
            "target_file": normalized_path,
            "rule": repair_text(str(rule_id or "architecture_violation")).strip(),
            "evidence": repair_text(str(detail or "")).strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"audit_debt_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"


def _audit_violation_work_items(
    audit: dict[str, Any],
    *,
    rule: str = "",
    project: str = "",
    severity: str = "",
) -> list[dict[str, Any]]:
    doctrine = _doctrine()
    rule_profiles = (build_rule_taxonomy().get("profiles", {}) or {})
    labels = doctrine.get("violation_labels", {}) if isinstance(doctrine, dict) else {}
    remediation_policy = (
        doctrine.get("audit_remediation_policy", {}).get("waves", {})
        if isinstance(doctrine.get("audit_remediation_policy"), dict)
        else {}
    )
    target_rule = str(rule or "").strip().lower()
    raw_project_filter = str(project or "").strip()
    target_project = "" if raw_project_filter in {"*", "all", "ALL"} else raw_project_filter.lower()
    target_severity = str(severity or "").strip().lower()
    items: list[dict[str, Any]] = []
    for violation in audit.get("violations", []) or []:
        if not isinstance(violation, dict):
            continue
        file_path = str(
            violation.get("repo_relative_path")
            or violation.get("workspace_rel")
            or violation.get("file")
            or violation.get("path")
            or ""
        ).replace("\\", "/")
        project_key = str(violation.get("project_key") or violation.get("project") or "").strip()
        target_ref = str(violation.get("target_ref") or "").strip()
        if "::" in file_path and not project_key:
            project_key, file_path = file_path.split("::", 1)
        rule_id = str(violation.get("rule") or "architecture_violation")
        mode = str(violation.get("mode") or violation.get("severity") or violation.get("level") or "").lower()
        if target_rule and target_rule not in rule_id.lower():
            continue
        if target_project and target_project not in project_key.lower():
            continue
        if target_severity and target_severity not in mode:
            continue
        policy = remediation_policy.get(rule_id, {}) if isinstance(remediation_policy, dict) else {}
        if not policy and isinstance(remediation_policy, dict):
            policy = remediation_policy.get("default", {})
        profile = rule_profiles.get(rule_id, {}) if isinstance(rule_profiles, dict) else {}
        guidance = _rule_guidance(rule_id, doctrine)
        profile_mode = str(profile.get("mode") or profile.get("default_mode") or "").lower()
        governance_mode = str(guidance.get("governance_mode") or "").lower()
        effective_mode = mode or profile_mode or governance_mode
        label = labels.get(rule_id, rule_id) if isinstance(labels, dict) else rule_id
        detail = str(violation.get("detail") or violation.get("message") or "").strip()
        priority = policy.get("priority")
        if not priority:
            priority = "high" if effective_mode in {"enforced", "heal", "critical"} else "normal"
        explanation = {
            "mode": effective_mode or "unknown",
            "rationale": profile.get("rationale") or "SAGE matched this rule against the target file.",
        }
        item = {
            "id": _stable_audit_work_item_id(project_key, file_path, rule_id, detail),
            "target_project": project_key or "UNKNOWN",
            "target_file": file_path,
            "target_ref": target_ref or (f"{project_key}::{file_path}" if project_key else file_path),
            "rule": rule_id,
            "label": label,
            "mode": effective_mode or "unknown",
            "governance_mode": governance_mode or "unknown",
            "why_it_matters": profile.get("rationale") or "SAGE matched this rule against the target file.",
            "fix_strategy": policy.get("action")
            or "Make the smallest code change that satisfies the rule rationale and preserves public behavior.",
            "evidence": detail[:900],
            "recommended_action": policy.get("action")
            or "Open the target file, confirm the evidence, and make the smallest patch that satisfies the listed rule rationale.",
            "inspect_first": [file_path] if file_path else [],
            "priority": priority,
            **target_directive_actionability_projection(
                "actionable_proposal",
                evidence_source="audit_violation",
            ),
            **target_directive_approval_projection(explanation),
            "validation_tools": target_repo_validation_tools(),
        }
        items.append(item)
    priority_order = {"critical": 0, "high": 1, "normal": 2, "medium": 2, "low": 3}
    return sorted(items, key=lambda row: (priority_order.get(str(row.get("priority")).lower(), 4), row.get("target_file") or ""))


def _audit_violation_work_items_from_sqlite(
    raw_dir: Path,
    *,
    page: int,
    page_size: int,
    rule: str = "",
    project: str = "",
    severity: str = "",
    file_path: str = "",
) -> tuple[list[dict[str, Any]], int, bool]:
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return [], 0, False
    raw_project_filter = str(project or "").strip()
    target_project = "" if raw_project_filter in {"*", "all", "ALL"} else raw_project_filter
    where = ["fi.engine_name = ?"]
    params: list[Any] = ["audit_report"]
    if target_project:
        where.append("LOWER(p.project_key) = LOWER(?)")
        params.append(target_project)
    target_rule = str(rule or "").strip()
    if target_rule:
        where.append("LOWER(fi.code) LIKE LOWER(?)")
        params.append(f"%{target_rule}%")
    target_severity = str(severity or "").strip()
    if target_severity:
        where.append("LOWER(fi.severity) LIKE LOWER(?)")
        params.append(f"%{target_severity}%")
    target_file = str(file_path or "").replace("\\", "/").strip("/")
    if target_file:
        where.append("LOWER(f.rel_path) = LOWER(?)")
        params.append(target_file)
    where_clause = " AND ".join(where)
    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'findings';"
            ).fetchone()
            if not table:
                return [], 0, False
            total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM findings fi
                    JOIN files f ON f.file_id = fi.file_id
                    JOIN projects p ON p.project_key = f.project_key
                    WHERE {where_clause};
                    """,
                    params,
                ).fetchone()[0]
            )
            if total <= 0:
                return [], 0, True
            rows = conn.execute(
                f"""
                SELECT fi.severity, fi.code, fi.message, p.project_key, f.rel_path
                FROM findings fi
                JOIN files f ON f.file_id = fi.file_id
                JOIN projects p ON p.project_key = f.project_key
                WHERE {where_clause}
                ORDER BY
                    CASE LOWER(fi.severity)
                        WHEN 'critical' THEN 0
                        WHEN 'enforced' THEN 0
                        WHEN 'heal' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'warning' THEN 2
                        WHEN 'advisory' THEN 3
                        ELSE 4
                    END,
                    f.rel_path,
                    fi.code,
                    fi.message
                LIMIT ? OFFSET ?;
                """,
                [*params, max(1, int(page_size or 10)), max(0, (max(1, int(page or 1)) - 1) * max(1, int(page_size or 10)))],
            ).fetchall()
    except Exception:
        return [], 0, False

    doctrine = _doctrine()
    items: list[dict[str, Any]] = []
    for row in rows:
        violation: dict[str, Any] = {}
        try:
            parsed = json.loads(row["message"])
            if isinstance(parsed, dict):
                violation = parsed
        except Exception:
            violation = {}
        rule_id = str(row["code"] or violation.get("rule") or "architecture_violation")
        guidance = _rule_guidance(rule_id, doctrine)
        project_key = str(row["project_key"] or violation.get("project_key") or violation.get("project") or "")
        file_path = str(row["rel_path"] or violation.get("atlas_rel_path") or violation.get("file") or "").replace("\\", "/")
        detail = str(violation.get("detail") or violation.get("message") or row["message"] or "").strip()
        audit_severity = str(row["severity"] or violation.get("severity") or "").lower()
        violation_mode = str(violation.get("mode") or "").lower()
        governance_mode = str(guidance.get("governance_mode") or "").lower()
        effective_mode = violation_mode or governance_mode or audit_severity
        approval_modes = {effective_mode, governance_mode, audit_severity}
        approval_required = bool(approval_modes & {"enforced", "heal", "critical"})
        priority = guidance.get("priority") or ("high" if approval_required else "normal")
        explanation = {
            "mode": effective_mode or "unknown",
            "rationale": guidance.get("why_it_matters") or "SAGE matched this rule against the target file.",
        }
        items.append(
            {
                "id": _stable_audit_work_item_id(project_key, file_path, rule_id, detail),
                "target_project": project_key or "UNKNOWN",
                "target_file": file_path,
                "target_ref": f"{project_key}::{file_path}" if project_key and file_path else file_path,
                "rule": rule_id,
                "label": guidance.get("label") or rule_id,
                "mode": effective_mode or "unknown",
                "audit_severity": audit_severity or "unknown",
                "governance_mode": governance_mode or "unknown",
                "why_it_matters": guidance.get("why_it_matters"),
                "fix_strategy": guidance.get("fix_strategy"),
                "evidence": detail[:900],
                "recommended_action": guidance.get("recommended_action"),
                "inspect_first": [file_path] if file_path else [],
                "priority": priority,
                **target_directive_actionability_projection(
                    "actionable_proposal",
                    evidence_source="audit_violation",
                ),
                **target_directive_approval_projection(
                    explanation,
                    approval_required=approval_required,
                    approval_basis=f"Audit severity is '{audit_severity or 'unknown'}'.",
                ),
                "validation_tools": target_repo_validation_tools(),
            }
        )
    return items, total, True


def _module_integrity_items_from_sqlite(
    raw_dir: Path,
    module_path: str,
    *,
    max_items: int,
    target_root: str = "",
) -> tuple[list[dict[str, Any]], int, bool]:
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return [], 0, False
    module_filter = str(module_path or "").replace("\\", "/").strip("/").lower()
    default_main_scope = not target_root and not _is_variation_workspace_text(module_path) and "::" not in str(module_path or "")
    where = ["fi.engine_name = ?"]
    params: list[Any] = ["audit_report"]
    if default_main_scope:
        where.append("p.project_key = ?")
        params.append("MAIN")
    if module_filter:
        where.append(
            """
            (
                LOWER(f.rel_path) LIKE ?
                OR LOWER(CASE WHEN p.path = '' THEN f.rel_path ELSE p.path || '/' || f.rel_path END) LIKE ?
                OR LOWER(p.path) = ?
            )
            """
        )
        like = f"%{module_filter}%"
        params.extend([like, like, module_filter])
    where_clause = " AND ".join(where)
    try:
        with closing(sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            conn.row_factory = sqlite3.Row
            total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM findings fi
                    JOIN files f ON f.file_id = fi.file_id
                    JOIN projects p ON p.project_key = f.project_key
                    WHERE {where_clause};
                    """,
                    params,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT fi.finding_id, fi.severity, fi.code, fi.message, p.project_key, p.path, f.rel_path
                FROM findings fi
                JOIN files f ON f.file_id = fi.file_id
                JOIN projects p ON p.project_key = f.project_key
                WHERE {where_clause}
                ORDER BY
                    CASE LOWER(fi.severity)
                        WHEN 'critical' THEN 0
                        WHEN 'enforced' THEN 0
                        WHEN 'heal' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'warning' THEN 2
                        WHEN 'advisory' THEN 3
                        ELSE 4
                    END,
                    p.project_key,
                    f.rel_path,
                    fi.finding_id
                LIMIT ?;
                """,
                [*params, max(1, int(max_items or 20))],
            ).fetchall()
    except Exception:
        return [], 0, False

    doctrine = _doctrine()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        project_key = str(row["project_key"] or "")
        rel_path = str(row["rel_path"] or "").replace("\\", "/").strip("/")
        repo_rel = _repo_relative_from_sqlite_row(str(row["path"] or ""), rel_path)
        violation: dict[str, Any] = {}
        try:
            parsed = json.loads(row["message"])
            if isinstance(parsed, dict):
                violation = parsed
        except Exception:
            violation = {}
        rule_id = str(row["code"] or violation.get("rule") or "architecture_violation")
        guidance = _rule_guidance(rule_id, doctrine)
        detail = str(violation.get("detail") or violation.get("message") or row["message"] or "").strip()
        target_context = _target_context_from_project_file(raw_dir, project_key, repo_rel, target_root=target_root)
        filtered.append(
            {
                "file": repo_rel,
                "target_file": target_context.get("target_file") or repo_rel,
                "target_ref": target_context.get("target_ref") or f"{project_key}::{repo_rel}",
                "target_status": target_context.get("target_status") or {},
                "rule": rule_id,
                "label": guidance.get("label"),
                "why_it_matters": guidance.get("why_it_matters"),
                "fix_strategy": guidance.get("fix_strategy"),
                "inspect_first": [target_context.get("target_file") or repo_rel],
                "remediation_action": guidance.get("recommended_action"),
                "priority": guidance.get("priority"),
                "evidence": detail[:900],
            }
        )
    return filtered, total, True


def _rule_guidance(rule_id: str, doctrine: dict[str, Any] | None = None) -> dict[str, Any]:
    doctrine = doctrine if isinstance(doctrine, dict) else _doctrine()
    rule_profiles = (build_rule_taxonomy().get("profiles", {}) or {})
    profile = rule_profiles.get(rule_id, {}) if isinstance(rule_profiles, dict) else {}
    remediation_policy = (
        doctrine.get("audit_remediation_policy", {}).get("waves", {})
        if isinstance(doctrine.get("audit_remediation_policy"), dict)
        else {}
    )
    policy = remediation_policy.get(rule_id, {}) if isinstance(remediation_policy, dict) else {}
    if not policy and isinstance(remediation_policy, dict):
        policy = remediation_policy.get("default", {})
    labels = doctrine.get("violation_labels", {}) if isinstance(doctrine, dict) else {}
    label = labels.get(rule_id, profile.get("label") or rule_id) if isinstance(labels, dict) else profile.get("label") or rule_id
    action = policy.get("action") or "Make the smallest code change that satisfies the rule rationale and preserves public behavior."
    return {
        "label": label,
        "governance_mode": str(profile.get("mode") or profile.get("default_mode") or "").lower(),
        "why_it_matters": profile.get("rationale") or "SAGE matched this rule against the target file.",
        "fix_strategy": action,
        "recommended_action": action,
        "priority": policy.get("priority") or "normal",
    }


def _calibrate_confidence_with_sqlite_impact(
    raw_dir: Path,
    target_file: str,
    target_root: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    calibrated = dict(result)
    metrics = dict(calibrated.get("metrics") if isinstance(calibrated.get("metrics"), dict) else {})
    matrix = dict(calibrated.get("confidence_matrix") if isinstance(calibrated.get("confidence_matrix"), dict) else {})
    reasons = calibrated.get("reasons") if isinstance(calibrated.get("reasons"), list) else []
    if not reasons:
        risk_reasons = metrics.get("risk_mitigation_reasons")
        reasons = risk_reasons if isinstance(risk_reasons, list) else []
    impact = _sqlite_impact_radius_from_raw(raw_dir, target_file, target_root=target_root, depth=2)
    if impact is not None:
        direct_count = int(impact.get("direct_dependents_count") or 0)
        blast_radius_size = int(impact.get("blast_radius_size") or direct_count)
        metrics["blast_radius_dependents"] = direct_count
        metrics["blast_radius_size"] = blast_radius_size
        metrics["confidence_dependency_source"] = "sqlite_dependencies"
        confidence_policy = require_doctrine_mapping("confidence_policy")
        critical_threshold = int(confidence_policy.get("critical_dep_threshold", 10) or 10)
        low_threshold = int(confidence_policy.get("low_dep_threshold", 5) or 5)
        merge_safety = str(matrix.get("merge_safety") or "SAFE").upper()
        calibrated_safety = merge_safety
        if direct_count >= critical_threshold:
            calibrated_safety = "CRITICAL"
        elif direct_count >= low_threshold and calibrated_safety != "CRITICAL":
            calibrated_safety = "LOW"
        elif direct_count >= 1 and calibrated_safety not in {"CRITICAL", "LOW"}:
            calibrated_safety = "MEDIUM"
        if calibrated_safety != merge_safety:
            reasons = [
                item
                for item in reasons
                if str(item) != "File adheres completely to standard static and architectural safety constraints."
            ]
            reasons.append(
                f"SQLite impact radius reports {direct_count} direct dependents and {blast_radius_size} total impacted files."
            )
        matrix["merge_safety"] = calibrated_safety
    calibrated["metrics"] = metrics
    calibrated["confidence_matrix"] = matrix
    calibrated["reasons"] = reasons
    return calibrated


def _directive_matches_dependency_target(directive: dict[str, Any], target_ref: str) -> bool:
    normalized_ref = str(target_ref or "").strip().replace("\\", "/")
    if not normalized_ref:
        return False
    _project, separator, relative_path = normalized_ref.partition("::")
    if not separator:
        relative_path = normalized_ref
    declared_refs = {
        str(item).strip().replace("\\", "/")
        for item in directive.get("target_refs", []) or []
        if str(item).strip()
    }
    if declared_refs:
        return normalized_ref in declared_refs
    file_contexts = [
        context
        for context in directive.get("file_context", []) or []
        if isinstance(context, dict)
    ]
    if file_contexts:
        return any(
            str(context.get("atlas_node") or "").strip().replace("\\", "/") == normalized_ref
            for context in file_contexts
        )
    target_files = {
        str(item).strip().replace("\\", "/").lstrip("/")
        for item in directive.get("target_files", []) or []
        if str(item).strip()
    }
    return relative_path.strip().lstrip("/") in target_files


def _enrich_surgical_packet_with_sqlite_impact(
    raw_dir: Path,
    packet: dict[str, Any],
    *,
    target_root: str = "",
) -> dict[str, Any]:
    if not isinstance(packet, dict):
        return packet
    focus_rows = packet.get("l1_focus")
    if not isinstance(focus_rows, list):
        return packet

    from tools.core.contextos_signal_limits import contextos_signal_limit

    direct_limit = contextos_signal_limit("direct_dependents")
    transitive_limit = contextos_signal_limit("transitive_dependents")
    atlas_commit = _load_json(raw_dir / "atlas_commit.json") or {}
    snapshot_id = str(atlas_commit.get("snapshot_id") or "")
    projections: list[dict[str, Any]] = []

    for row in focus_rows:
        if not isinstance(row, dict):
            continue
        target_ref = str(row.get("target_ref") or row.get("node_key") or "").strip()
        if not target_ref:
            row["dependency_projection_status"] = "target_not_declared"
            continue
        impact = _sqlite_impact_radius_from_raw(
            raw_dir,
            target_ref,
            target_root=target_root,
            depth=2,
        )
        if not isinstance(impact, dict):
            row["dependency_projection_status"] = "sqlite_projection_unavailable"
            row["dependency_graph_source"] = "unavailable"
            row["dependency_snapshot_id"] = snapshot_id
            projections.append(
                {
                    "target_ref": target_ref,
                    "status": "sqlite_projection_unavailable",
                    "dependency_graph_source": "unavailable",
                    "dependency_snapshot_id": snapshot_id,
                }
            )
            continue

        direct = list(impact.get("direct_dependents") or [])
        transitive = list(impact.get("transitive_dependents") or [])
        direct_count = int(impact.get("direct_dependents_count") or len(direct))
        transitive_count = int(impact.get("blast_radius_size") or len(transitive))
        row["direct_dependents_count"] = direct_count
        row["direct_dependents"] = direct[:direct_limit]
        row["direct_dependents_omitted"] = max(0, direct_count - len(row["direct_dependents"]))
        row["transitive_dependents_count"] = transitive_count
        row["transitive_dependents"] = transitive[:transitive_limit]
        row["transitive_dependents_omitted"] = max(
            0,
            transitive_count - len(row["transitive_dependents"]),
        )
        row["dependency_projection_status"] = "sqlite_current_snapshot"
        row["dependency_graph_source"] = str(
            impact.get("dependency_graph_source") or "sqlite_dependencies"
        )
        row["dependency_snapshot_id"] = snapshot_id
        projection = {
            "target_ref": target_ref,
            "status": row["dependency_projection_status"],
            "dependency_graph_source": row["dependency_graph_source"],
            "dependency_snapshot_id": snapshot_id,
            "direct_dependents_count": direct_count,
            "direct_dependents": list(row["direct_dependents"]),
            "direct_dependents_omitted": row["direct_dependents_omitted"],
            "transitive_dependents_count": transitive_count,
            "transitive_dependents": list(row["transitive_dependents"]),
            "transitive_dependents_omitted": row["transitive_dependents_omitted"],
        }
        projections.append(projection)

        for directive in packet.get("agent_action_directives") or []:
            if not isinstance(directive, dict) or not _directive_matches_dependency_target(directive, target_ref):
                continue
            boundary = directive.get("evidence_boundary")
            if not isinstance(boundary, dict):
                continue
            boundary.update(
                {
                    "dependency_projection_id": "sqlite_current_snapshot",
                    "dependency_graph_source": row["dependency_graph_source"],
                    "dependency_snapshot_id": snapshot_id,
                    "direct_dependents_omitted": row["direct_dependents_omitted"],
                    "transitive_dependents_omitted": row["transitive_dependents_omitted"],
                }
            )

        for trace in packet.get("upstream_traces") or []:
            if not isinstance(trace, dict):
                continue
            trace_ref = str(trace.get("target_ref") or trace.get("target") or "").strip()
            if trace_ref != target_ref:
                continue
            trace["direct_dependents"] = list(impact.get("direct_dependent_refs") or [])
            trace["direct_dependent_files"] = list(row["direct_dependents"])
            trace["direct_dependents_count"] = direct_count
            trace["direct_dependents_omitted"] = row["direct_dependents_omitted"]
            trace["dependency_graph_source"] = row["dependency_graph_source"]
            trace["dependency_snapshot_id"] = snapshot_id
    packet["dependency_projections"] = projections
    return packet


def _render_violation_work_queue_brief(payload: dict[str, Any]) -> str:
    """Fit complete work items, never cut safety instructions or source snippets."""
    maximum = int(_work_queue_brief_policy()["max_visible_items"])
    for visible_limit in range(maximum, 0, -1):
        body = _render_violation_work_queue_page(payload, visible_limit=visible_limit)
        if context_budget_profile(body, budget_tokens=BOUNDED_AGENT_PACKET_TOKENS)["status"] == "pass":
            return body
    return "\n".join([
        "# Technical Debt Work Queue", "", "```yaml",
        'status: "context_budget_exceeded"',
        "safe_to_apply: false",
        "mutation_authority: not_granted",
        "items: []",
        'reason: "A complete work item and its safety context do not fit the bounded brief."',
        'next_action: "Request get_violation_work_queue with format=json or narrower project/rule filters before editing."',
        'claim_boundary: "No visible brief item does not mean a clean repository. Full evidence is retained in JSON."',
        "```", "",
    ])


def _render_violation_work_queue_page(payload: dict[str, Any], *, visible_limit: int) -> str:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    trust = payload.get("artifact_trust") if isinstance(payload.get("artifact_trust"), dict) else {}
    trust_scope = trust.get("scope") if isinstance(trust.get("scope"), dict) else {}
    trust_failures = trust.get("failures") if isinstance(trust.get("failures"), list) else []
    trust_warnings = trust.get("warnings") if isinstance(trust.get("warnings"), list) else []
    trust_status = str(trust.get("status") or "UNKNOWN").upper()
    trust_action = "safe_to_use" if trust_status == "PASS" else "refresh_sage_evidence_before_editing"
    brief_policy = _work_queue_brief_policy()
    grouped_items = _group_violation_work_items_for_agent(items) if trust_status == "PASS" else []
    max_visible_items = int(brief_policy.get("max_visible_items") or 1)
    visible_items = grouped_items[:min(max_visible_items, visible_limit)]
    raw_page_items = len(items)
    grouped_work_items = len(grouped_items)
    omitted_work_items = max(0, grouped_work_items - len(visible_items))
    raw_page_items_grouped = max(0, raw_page_items - grouped_work_items)
    total_violations = int(payload.get("total_violations") or 0)
    queue_status = str(payload.get("status") or ("clean" if total_violations == 0 else "debt_present"))
    coverage = (
        payload.get("coverage")
        if isinstance(payload.get("coverage"), dict)
        else {
            "status": "partial",
            "sage_audit": "evaluated",
            "target_native": "not_evaluated",
            "combined_verdict": "not_available",
            "clean_scope": "sage_audit_only",
        }
    )
    if queue_status == "clean" and coverage.get("target_native") != "evaluated":
        queue_status = "clean_within_sage_audit"
    clean_queue = trust_status == "PASS" and total_violations == 0 and not visible_items
    actionability_projection = (
        payload.get("authority_projection")
        if isinstance(payload.get("authority_projection"), dict)
        else target_directive_actionability_projection(
            "actionable_proposal" if visible_items else "no_action",
            evidence_source="visible_audit_work_items" if visible_items else "no_visible_audit_work_item",
        )
    )
    validation_policy = target_repo_validation_policy()
    validation_mode = (
        validation_policy.get("inactive_mode") if clean_queue else validation_policy.get("mode")
    )
    validation_tools = (
        validation_policy.get("inactive_tools", []) if clean_queue else target_repo_validation_tools()
    )
    validation_completion_rule = (
        validation_policy.get("inactive_completion_rule")
        if clean_queue
        else validation_policy.get("completion_rule")
    )
    if visible_items:
        authority_rule_mode = "item_scoped_below"
        authority_approval_required = any(
            bool(item.get("human_approval_required")) for item in visible_items
        )
        authority_decision_source = "item_scoped_below"
        authority_reason = "Each visible work item carries its own governance mode and approval decision."
    elif clean_queue:
        authority_rule_mode = "not_applicable"
        authority_approval_required = False
        authority_decision_source = "no_actionable_item"
        authority_reason = (
            "The bounded queue exposes no actionable work item; no mutation is authorized or proposed."
        )
    else:
        authority_rule_mode = "not_applicable"
        authority_approval_required = False
        authority_decision_source = "no_visible_item_in_projection"
        authority_reason = (
            "This bounded projection exposes no visible work item; it does not authorize a mutation."
        )
    mission_lines = [
        "Use this queue to pay down existing repository technical debt before starting new feature work.",
        "Pick one item, inspect the target file, and make the smallest safe patch.",
    ]
    if clean_queue:
        mission_lines = [
            "No SAGE Audit work item is available for the selected project and filters.",
            "Target-native enforcement was not evaluated; do not read this bounded result as repository-wide clean.",
            "Do not invent a fix target from this SAGE Audit-only queue.",
        ]
    if trust_status != "PASS":
        mission_lines = [
            "Do not edit from this queue until SAGE evidence is refreshed.",
            "Use this response only to see why the current technical-debt queue is not safe to act on.",
        ]
    yaml_lines = [
        "mission:",
        *["  - " + json.dumps(line, ensure_ascii=False) for line in mission_lines],
    ]
    if trust_status != "PASS":
        yaml_lines.extend(
            [
                "evidence_gate:",
                "  status: " + json.dumps(trust_status.lower(), ensure_ascii=False),
                "  action: " + json.dumps(trust_action, ensure_ascii=False),
                "  scope: "
                + json.dumps(
                    f"{int(trust_scope.get('audited_project_count') or 0)}/{int(trust_scope.get('atlas_project_count') or 0)} projects audited",
                    ensure_ascii=False,
                ),
            ]
        )
        if trust_failures or trust_warnings:
            yaml_lines.append("  blockers:")
        else:
            yaml_lines.append("  blockers: []")
        if trust_failures:
            for row in trust_failures[:5]:
                yaml_lines.append("    - " + json.dumps(row.get("name") or "", ensure_ascii=False))
        if trust_warnings:
            for row in trust_warnings[:5]:
                yaml_lines.append("    - " + json.dumps(row.get("name") or "", ensure_ascii=False))
    yaml_lines.extend([
        "task:",
        "  status: " + json.dumps(queue_status, ensure_ascii=False),
        "  analysis_root: " + json.dumps(payload.get("analysis_root") or "", ensure_ascii=False),
        f"  total_violations: {total_violations}",
        f"  returned_work_items: {len(visible_items)}",
        f"  {brief_policy.get('omission_field')}: {omitted_work_items}",
        f"  raw_page_items: {raw_page_items}",
        f"  grouped_work_items: {grouped_work_items}",
        f"  raw_page_items_grouped: {raw_page_items_grouped}",
        "  grouping_policy: \"Rows with the same target_ref and rule are grouped into one patch-sized work item; evidence_items preserves the grouped evidence.\"",
        "  projection_policy: " + json.dumps(brief_policy.get("agent_rule") or "", ensure_ascii=False),
        f"  page: {int(payload.get('page') or 1)}",
        f"  page_size: {int(payload.get('page_size') or len(visible_items) or 1)}",
        "coverage:",
        "  status: " + json.dumps(coverage.get("status") or "partial", ensure_ascii=False),
        "  sage_audit: " + json.dumps(coverage.get("sage_audit") or "evaluated", ensure_ascii=False),
        "  target_native: " + json.dumps(coverage.get("target_native") or "not_evaluated", ensure_ascii=False),
        "  combined_verdict: " + json.dumps(coverage.get("combined_verdict") or "not_available", ensure_ascii=False),
        "  clean_scope: " + json.dumps(coverage.get("clean_scope") or "sage_audit_only", ensure_ascii=False),
        "authority_projection:",
        "  actionability: " + json.dumps(actionability_projection.get("actionability") or "no_action", ensure_ascii=False),
        "  mutation_proposed: " + ("true" if actionability_projection.get("mutation_proposed") else "false"),
        "  mutation_authority: " + json.dumps(actionability_projection.get("mutation_authority") or "not_granted_by_this_directive", ensure_ascii=False),
        "  approval_is_not_mutation_authority: true",
        "  approval_requirement_scope: " + json.dumps(actionability_projection.get("approval_requirement_scope") or "not_applicable_without_separate_actionable_mutation", ensure_ascii=False),
        "  rule_mode: " + json.dumps(authority_rule_mode, ensure_ascii=False),
        "  human_approval_required: " + ("true" if authority_approval_required else "false"),
        "  approval_decision_source: " + json.dumps(authority_decision_source, ensure_ascii=False),
        "  approval_reason: " + json.dumps(authority_reason, ensure_ascii=False),
        "path_contract:",
        "  open_files_with: \"analysis_root + target_file or inspect_first item\"",
        "  target_ref_usage: \"SAGE/MCP reference only; not a filesystem path\"",
        "  target_ref_format: \"<project>::<repo_relative_path>; MAIN is the primary analyzed project scope\"",
        "items:",
    ])
    if visible_items:
        for item_index, item in enumerate(visible_items):
            yaml_lines.append("  - id: " + json.dumps(item.get("id") or "", ensure_ascii=False))
            yaml_lines.append("    target_project: " + json.dumps(item.get("target_project") or "", ensure_ascii=False))
            yaml_lines.append("    target_file: " + json.dumps(item.get("target_file") or "", ensure_ascii=False))
            yaml_lines.append("    target_ref: " + json.dumps(item.get("target_ref") or "", ensure_ascii=False))
            yaml_lines.append("    rule: " + json.dumps(item.get("rule") or "", ensure_ascii=False))
            yaml_lines.append("    label: " + json.dumps(item.get("label") or "", ensure_ascii=False))
            if item.get("governance_mode"):
                yaml_lines.append("    governance_mode: " + json.dumps(item.get("governance_mode") or "", ensure_ascii=False))
            yaml_lines.append("    priority: " + json.dumps(item.get("priority") or "", ensure_ascii=False))
            yaml_lines.append("    rule_mode: " + json.dumps(item.get("rule_mode") or item.get("mode") or "unknown", ensure_ascii=False))
            yaml_lines.append("    human_approval_required: " + ("true" if item.get("human_approval_required") else "false"))
            yaml_lines.append("    approval_decision_source: " + json.dumps(item.get("approval_decision_source") or "unknown", ensure_ascii=False))
            yaml_lines.append("    approval_reason: " + json.dumps(item.get("approval_reason") or "", ensure_ascii=False))
            yaml_lines.append("    why_it_matters: " + json.dumps(item.get("why_it_matters") or "", ensure_ascii=False))
            yaml_lines.append("    fix_strategy: " + json.dumps(item.get("fix_strategy") or "", ensure_ascii=False))
            yaml_lines.append("    edit_focus: \"Use evidence_source_snippets as the edit target; target_source_snippets are orientation context.\"")
            yaml_lines.append("    inspect_first: " + json.dumps(item.get("inspect_first") or [], ensure_ascii=False))
            source_grounding = item.get("source_grounding") if isinstance(item.get("source_grounding"), dict) else {}
            if source_grounding:
                if item_index == 0:
                    for grounding_line in _source_grounding_yaml_lines(source_grounding, max_spans=1):
                        yaml_lines.append("    " + grounding_line)
                else:
                    yaml_lines.extend(
                        [
                            "    source_grounding:",
                            "      source_snapshot_status: "
                            + json.dumps(source_grounding.get("source_snapshot_status") or "unknown", ensure_ascii=False),
                            "      drift_check_status: "
                            + json.dumps(source_grounding.get("drift_check_status") or "unknown", ensure_ascii=False),
                            "      detail_status: \"omitted_progressive_disclosure\"",
                            "      next_action: \"Select this item and request a filtered queue or inspect_file before editing.\"",
                        ]
                    )
            evidence_items = item.get("evidence_items")
            if isinstance(evidence_items, list) and evidence_items:
                yaml_lines.append("    evidence_items: " + json.dumps(evidence_items[:5], ensure_ascii=False))
            else:
                yaml_lines.append("    evidence: " + json.dumps(item.get("evidence") or "", ensure_ascii=False))
            if int(item.get("grouped_count") or 1) > 1:
                yaml_lines.append(f"    grouped_count: {int(item.get('grouped_count') or 1)}")
            yaml_lines.append("    recommended_action: " + json.dumps(item.get("recommended_action") or "", ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    yaml_lines.extend(
        [
            "do:",
            *(
                [
                    "  - Treat this queue only as having no visible SAGE Audit item for the selected project and filters.",
                    "  - Run target-native enforcement before making any broader clean or compliance claim.",
                    "  - Ask the user for a concrete target, run a broader query, or use supporting context tools before editing.",
                    "  - Do not patch repository code from an empty work queue.",
                ]
                if clean_queue
                else
                [
                    "  - Start with the first item unless the user chooses another.",
                    "  - Verify the evidence in the target repository before editing.",
                    "  - Run the listed validation commands after each patch.",
                ]
                if trust_status == "PASS"
                else [
                    "  - Refresh SAGE evidence for the analyzed repository before requesting a code patch from this queue.",
                    "  - Re-run this tool after the evidence status is pass.",
                ]
            ),
            "do_not:",
            "  - Do not batch unrelated items into one broad refactor.",
            "  - Do not add suppressions or allowlists without explicit human approval.",
            *(
                [
                    "  - Do not claim repository-wide cleanliness or target-native compliance from this SAGE Audit-only queue.",
                    "  - Do not claim an architecture fix was applied from this empty queue.",
                ]
                if clean_queue
                else []
                if trust_status == "PASS"
                else ["  - Do not edit target repository code from stale or incomplete queue evidence."]
            ),
            "validation:",
            "  mode: " + json.dumps(validation_mode or "", ensure_ascii=False),
            "  tools:",
            *[
                "    - " + json.dumps(row, ensure_ascii=False)
                for row in validation_tools
            ],
            *( ["    []"] if not validation_tools else [] ),
            "  completion_rule: " + json.dumps(validation_completion_rule or "", ensure_ascii=False),
        ]
    )
    return "\n".join(
        [
            "# Technical Debt Work Queue",
            "",
            "```yaml",
            *_with_context_budget(yaml_lines, budget_tokens=BOUNDED_AGENT_PACKET_TOKENS),
            "```",
            "",
        ]
    )


def _group_violation_work_items_for_agent(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated audit rows into patch-sized work items for target-repo agents."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        target_ref = str(item.get("target_ref") or item.get("target_file") or "")
        rule_id = str(item.get("rule") or "")
        key = (target_ref, rule_id)
        existing = grouped.get(key)
        evidence = str(item.get("evidence") or "").strip()
        if existing is None:
            clone = dict(item)
            clone["evidence_items"] = [evidence] if evidence else []
            clone["grouped_count"] = 1
            grouped[key] = clone
            continue
        existing["grouped_count"] = int(existing.get("grouped_count") or 1) + 1
        evidence_items = existing.setdefault("evidence_items", [])
        if evidence and evidence not in evidence_items:
            evidence_items.append(evidence)
        inspect_first = existing.setdefault("inspect_first", [])
        if not isinstance(inspect_first, list):
            inspect_first = []
            existing["inspect_first"] = inspect_first
        for path in item.get("inspect_first") or []:
            if path and path not in inspect_first:
                inspect_first.append(path)
    return list(grouped.values())


def _resolve_work_item_paths_for_agent(raw_dir: Path, items: list[dict[str, Any]], target_root: str = "") -> list[dict[str, Any]]:
    """Resolve only the bounded page of work items to repo-relative paths."""
    resolved_items: list[dict[str, Any]] = []
    for item in items:
        clone = dict(item)
        project_key = str(clone.get("target_project") or "")
        file_path = str(clone.get("target_file") or "").replace("\\", "/")
        if file_path:
            try:
                resolved_node, context = _resolve_target_node_from_raw(
                    raw_dir,
                    f"{project_key}::{file_path}" if project_key and project_key != "UNKNOWN" else file_path,
                )
                resolved_file = str(context.get("repo_relative_path") or resolved_node.split("::", 1)[-1] or file_path).replace("\\", "/")
                resolved_project = project_key if project_key and project_key != "UNKNOWN" else _project_from_ref(resolved_node)
                clone["target_project"] = resolved_project or project_key
                clone["target_file"] = resolved_file
                clone["target_ref"] = f"{resolved_project}::{resolved_file}" if resolved_project else resolved_file
                target_status = _target_path_status(raw_dir, clone["target_ref"], target_root=target_root)
                public_status = _public_target_path_status(target_status)
                evidence_for_snippets = [
                    str(value or "").strip()
                    for value in clone.get("evidence_items") or [clone.get("evidence") or ""]
                    if str(value or "").strip()
                ]
                if (
                    public_status.get("source_snapshot_status") == "ok"
                    and public_status.get("drift_check_status") == "match"
                    and evidence_for_snippets
                ):
                    snapshot_content = _source_snapshot_content_for_ref(raw_dir, clone["target_ref"])
                    public_status["evidence_source_snippets"] = _evidence_source_snippets(
                        snapshot_content,
                        evidence_for_snippets,
                    )
                clone["source_grounding"] = public_status
                inspect_first = [resolved_file]
                evidence = str(clone.get("evidence") or "")
                if " imports " in evidence:
                    imported = import_specifier_from_audit_detail(evidence)
                    if imported.startswith("."):
                        candidate = strip_current_directory_prefix(
                            posixpath.normpath(
                                posixpath.join(posixpath.dirname(resolved_file), imported.replace("\\", "/"))
                            )
                        )
                        if candidate and not candidate.startswith("../"):
                            try:
                                _resolved_import_node, import_context = _resolve_target_node_from_raw(
                                    raw_dir,
                                    f"{resolved_project}::{candidate}" if resolved_project else candidate,
                                )
                                import_file = str(import_context.get("repo_relative_path") or candidate).replace("\\", "/")
                                import_status = _target_path_status(raw_dir, _resolved_import_node, target_root=target_root)
                                if (import_file and import_file not in inspect_first
                                        and import_status.get("exists") is True
                                        and import_status.get("inside_root") is True
                                        and import_status.get("indexed") is True):
                                    inspect_first.append(import_file)
                            except Exception:
                                pass
                clone["inspect_first"] = inspect_first
            except Exception:
                pass
        resolved_items.append(clone)
    return resolved_items


@mcp.resource("architecture://summary")
def get_architecture_summary() -> str:
    path = RAW_DIR / "ai_context.json"
    return _read_json_artifact(path, "Architecture context not found. Run the pipeline first.")


@mcp.resource("architecture://dashboard")
def get_cmo_dashboard() -> str:
    path = REPORTS_DIR / "cmo_dashboard.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "CMO dashboard not found. Run the full pipeline first."


@mcp.resource("architecture://oracle")
def get_architecture_oracle_resource() -> str:
    path = REPORTS_DIR / "architecture_oracle.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Architecture Oracle proposal not found. Run the Architecture Oracle step after Atlas generation."


@mcp.tool()
def search_symbols(query: str, project: str = "MAIN", target_root: str = "", format: str = "brief") -> str:
    """Search symbols and files for target-repository work.

    Defaults to MAIN so coding agents do not treat variations as edit targets.
    Use project="*" / "all" only for merge, variation, or cross-project review.
    """
    requested_format = str(format or "brief").strip().lower()
    analysis_root = _analysis_root_display(target_root)
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("search_symbols", target_root)
    matches = _find_symbol_matches(query, project=project or None, raw_dir=raw_dir)
    if not matches:
        if requested_format in {"json", "machine"}:
            return f"No symbols found matching '{query}'."
        return _render_symbol_search_brief(query, [], analysis_root=analysis_root)
    if requested_format in {"json", "machine"}:
        return json.dumps(matches[:20], indent=2, ensure_ascii=False)
    return _render_symbol_search_brief(query, matches[:10], analysis_root=analysis_root)


@mcp.tool()
def get_architecture_oracle(project: str = "") -> str:
    """Return the Post-Atlas Architecture Oracle doctrine proposal."""
    payload = _load_json(RAW_DIR / "architecture_oracle.json") or {}
    if not payload:
        return "Architecture Oracle proposal not found. Run `python sage.py run --step architectureoracle` after Atlas generation."
    if project:
        projects = [
            item for item in payload.get("projects", [])
            if str(item.get("project", "")).lower() == project.lower()
        ]
        return json.dumps(
            {"summary": payload.get("summary", {}), "projects": projects, "policy": payload.get("policy", {})},
            indent=2,
            ensure_ascii=False,
        )
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def get_symbol_dna(name: str, project: str = "") -> str:
    """Return a closure bundle for a symbol, generating it on demand when needed."""
    target_project = project or _host_key(_atlas())
    try:
        closure_path = _resolve_raw_artifact(_safe_symbol_artifact_name(name))
    except ValueError as exc:
        return f"Invalid symbol name: {exc}"
    if closure_path.exists():
        return closure_path.read_text(encoding="utf-8")

    walker_path = TOOLS_DIR / "engines" / "closure_walker.py"
    try:
        safe_env = isolated_python_subprocess_env(
            os.environ,
            code_maps_dir=BASE_DIR,
            vendor_paths=VENDOR_PATHS,
        )
        from tools.core.artifact_store import get_adaptive_timeout
        result, _duration = run_observed_subprocess(
            [sys.executable, str(walker_path), name, target_project],
            cwd=BASE_DIR,
            label="mcp_symbol_closure_generation",
            timeout=get_adaptive_timeout(180),
            env=safe_env,
            log=None,
        )
        if result.returncode != 0:
            output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
            return f"Closure generation failed: {output or result.returncode}"
    except Exception as exc:
        return f"Closure generation failed: {exc}"

    if closure_path.exists():
        return closure_path.read_text(encoding="utf-8")
    return f"Closure bundle was not generated for '{name}'."


@mcp.tool()
def check_module_integrity(module_path: str, target_root: str = "", format: str = "brief", max_items: int = 20) -> str:
    """Filter audit violations for a path or module fragment and provide reasoning trace."""
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("check_module_integrity", target_root)
    sqlite_items, sqlite_total, sqlite_ok = _module_integrity_items_from_sqlite(
        raw_dir,
        module_path,
        max_items=max_items,
        target_root=target_root,
    )
    if sqlite_ok:
        result = {
            "status": "healthy" if not sqlite_total else "impure",
            "violation_count": sqlite_total,
            "reasoning_trace": sqlite_items,
            "source": "sqlite_findings",
        }
        if str(format or "brief").lower() in {"json", "machine"}:
            return json.dumps(result, indent=2, ensure_ascii=False)
        return _render_module_integrity_brief(
            {"surface": "module_integrity", "analysis_root": _analysis_root_display(target_root), "filter": module_path, "status": result["status"], "items": sqlite_items},
        )
    audit, missing = _artifact_or_missing(raw_dir, "audit_report.json", "check_module_integrity", module_path, target_root)
    if missing:
        return missing
    doctrine = _doctrine()

    filtered = []
    for violation in (audit.get("violations") or []):
        file_path = str(violation.get("file", "")).lower()
        if not _default_agent_scope_allows(violation, target_root=target_root, explicit_filter=module_path):
            continue
        if module_path.lower() in file_path:
            rule_id = violation.get("rule", "")
            guidance = _rule_guidance(str(rule_id), doctrine)
            violation["label"] = guidance["label"]
            violation["why_it_matters"] = guidance["why_it_matters"]
            violation["fix_strategy"] = guidance["fix_strategy"]
            violation["remediation_action"] = guidance["recommended_action"]
            violation["priority"] = guidance["priority"]
            scoped_file = str(violation.get("file") or violation.get("path") or "")
            project_key = str(violation.get("project") or "")
            violation["inspect_first"] = [f"{project_key}::{scoped_file}" if project_key and scoped_file else scoped_file]
            filtered.append(violation)

    bounded = filtered[: max(1, int(max_items or 20))]
    result = {
        "status": "healthy" if not filtered else "impure",
        "violation_count": len(filtered),
        "reasoning_trace": bounded,
    }
    if str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(result, indent=2, ensure_ascii=False)
    compact = [
        (
            lambda target_context: {
                "file": row.get("file"),
                "target_file": target_context.get("target_file") or row.get("file"),
                "target_ref": target_context.get("target_ref") or ((row.get("inspect_first") or [""])[0] if row.get("inspect_first") else ""),
                "target_status": target_context.get("target_status") or {},
                "rule": row.get("rule"),
                "label": row.get("label"),
                "evidence": row.get("evidence") or row.get("detail") or "",
                "why_it_matters": row.get("why_it_matters"),
                "fix_strategy": row.get("fix_strategy"),
                "inspect_first": [target_context.get("target_file") or row.get("file")],
                "remediation_action": row.get("remediation_action"),
                "priority": row.get("priority"),
            }
        )(_target_context_from_project_file(raw_dir, str(row.get("project") or ""), row.get("file"), target_root=target_root))
        for row in bounded
        if isinstance(row, dict)
    ]
    return _render_module_integrity_brief(
        {"surface": "module_integrity", "analysis_root": _analysis_root_display(target_root), "filter": module_path, "status": result["status"], "items": compact},
    )


@mcp.tool()
def validate_actor_proposal(
    proposal: dict[str, Any],
    target_root: str = "",
    request_id: str = "",
    request_actor_id: str = "",
    request_purpose: str = "",
    repository_snapshot: str = "",
    declared_scope: dict[str, Any] | None = None,
) -> str:
    """Validate a structured actor proposal against its canonical request envelope without authorizing execution."""

    from tools.core.actor_proposal_contract import validate_actor_proposal_payload

    gateway_context = _ACTOR_GATEWAY_DISPATCH_CONTEXT.get()
    if not isinstance(gateway_context, dict) or gateway_context.get("tool") != "validate_actor_proposal":
        return json.dumps(
            {
                "status": "BLOCKED",
                "blocking": True,
                "failure_code": "actor_gateway_required",
                "claim_boundary": (
                    "Proposal conformance requires canonical fields injected by dispatch_actor_request. "
                    "A direct tool call cannot establish request identity, freshness or scope conformance."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    contract = load_json_file(BASE_DIR / "config" / "actor_interaction_contract.json", {})
    proposal_contract = contract.get("proposal_contract") if isinstance(contract.get("proposal_contract"), dict) else {}
    result = validate_actor_proposal_payload(
        proposal if isinstance(proposal, dict) else {},
        proposal_contract=proposal_contract,
        request_id=request_id,
        request_actor_id=request_actor_id,
        request_purpose=request_purpose,
        repository_snapshot=repository_snapshot,
        declared_scope=declared_scope if isinstance(declared_scope, dict) else {},
    )
    result["analysis_root"] = _analysis_root_display(target_root)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
async def dispatch_actor_request(request: dict[str, Any], tool_name: str, tool_arguments: dict[str, Any] | None = None) -> str:
    """Dispatch one structured, explicitly scoped actor request to an eligible visible MCP tool."""

    from tools.core.actor_interaction_runtime import ActorInteractionError, actor_interaction_failure, validate_mcp_actor_request
    from tools.core.governance_trace import current_trace_id

    arguments = tool_arguments if isinstance(tool_arguments, dict) else {}
    profile = str(getattr(mcp, "active_tool_profile", "unknown"))
    visible = set(getattr(mcp, "_visible_tool_names", frozenset()))
    try:
        validated = validate_mcp_actor_request(
            BASE_DIR,
            request,
            tool_name=tool_name,
            tool_arguments=arguments,
            profile=profile,
            visible_tools=visible,
        )
    except ActorInteractionError as exc:
        return json.dumps(actor_interaction_failure(exc, request_id=str((request or {}).get("request_id") or "")), indent=2, ensure_ascii=False)

    effective_arguments = validated["tool_arguments"]
    gateway_token = _ACTOR_GATEWAY_DISPATCH_CONTEXT.set(
        {
            "tool": tool_name,
            "request_fingerprint": validated["request_fingerprint"],
        }
    )
    try:
        result = await mcp._tool_manager.call_tool(tool_name, effective_arguments, convert_result=False)
    except Exception as exc:
        result = {
            "status": "dispatch_failed",
            "error_type": type(exc).__name__,
            "claim_boundary": "The eligible tool did not return authoritative evidence; no result or success may be inferred.",
        }
    finally:
        _ACTOR_GATEWAY_DISPATCH_CONTEXT.reset(gateway_token)
    raw_status = _actor_result_status(result)
    outcome = validated["result_status_map"].get(raw_status, validated["unknown_result_state"])
    completed_states = ["REQUESTED"] if outcome in {"INVALID_CONTEXT", "INCOMPLETE_EVIDENCE"} else validated["required_states"]
    payload = {
        "status": outcome,
        "request_id": validated["request"]["request_id"],
        "request_fingerprint": validated["request_fingerprint"],
        "contract_version": validated["contract_version"],
        "adapter_type": "mcp",
        "profile": validated["profile"],
        "operation": validated["operation_profile"],
        "tool": validated["tool"],
        "interaction_state": outcome,
        "completed_states": completed_states,
        "trace_id": current_trace_id() or "not_available",
        "tool_result": result,
        "claim_boundary": "The gateway proves bounded request translation and dispatch. The called tool result retains its own evidence and authority boundary.",
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def get_violation_work_queue(
    page: int = 1,
    page_size: int = 10,
    rule: str = "",
    project: str = "MAIN",
    severity: str = "",
    target_root: str = "",
    format: str = "brief",
) -> str:
    """Return a bounded technical-debt queue from audit violations for coding agents.

    Defaults to MAIN so variation/companion repositories do not become edit targets
    unless the caller explicitly chooses a project or project="*" for all projects.
    """
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("get_violation_work_queue", started, result, status=status, fail_closed_reason=fail_closed_reason)

    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _done(_invalid_external_target_brief("get_violation_work_queue", target_root), status="fail_closed", fail_closed_reason="invalid_external_target")
    trust_summary = _audit_queue_trust_projection(
        _ensure_agent_artifact_chain_current(raw_dir, target_root=target_root)
    )
    if _artifact_trust_blocks_actor_context(trust_summary):
        trust_summary["auto_refresh"] = _audit_queue_recovery_plan(
            target_root=target_root,
            project=project,
        )
        invalid_context = _invalid_actor_context_payload("get_violation_work_queue", trust_summary)
        rendered = json.dumps(invalid_context, indent=2, ensure_ascii=False)
        if str(format or "brief").strip().lower() not in {"json", "machine"}:
            rendered = _render_invalid_actor_context_brief(invalid_context)
        return _done(rendered, status="fail_closed", fail_closed_reason="invalid_or_stale_artifact_trust")
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 10), 50))
    page_items, total_violations, sqlite_ok = _audit_violation_work_items_from_sqlite(
        raw_dir,
        page=page,
        page_size=page_size,
        rule=rule,
        project=project,
        severity=severity,
    )
    queue_source = "sqlite_findings" if sqlite_ok else "audit_report_payload"
    if not sqlite_ok:
        audit, missing = _artifact_or_missing(raw_dir, "audit_report.json", "get_violation_work_queue", "audit_report", target_root)
        if missing:
            return _done(missing, status="fail_closed", fail_closed_reason="missing_audit_report")
        items = _audit_violation_work_items(
            audit if isinstance(audit, dict) else {},
            rule=rule,
            project=project,
            severity=severity,
        )
        total_violations = len(items)
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]
    page_items = _resolve_work_item_paths_for_agent(raw_dir, page_items, target_root=target_root)
    queue_actionability = target_directive_actionability_projection(
        "actionable_proposal" if page_items else "no_action",
        evidence_source="visible_audit_work_items" if page_items else "no_visible_audit_work_item",
    )
    coverage = {
        "status": "partial",
        "sage_audit": "evaluated",
        "target_native": "not_evaluated",
        "combined_verdict": "not_available",
        "clean_scope": "sage_audit_only",
        "meaning": (
            "This queue contains SAGE Audit findings only. It does not establish compliance "
            "with target-native lint, compiler, test, policy, security or runtime authorities."
        ),
    }
    payload = {
        "status": "clean_within_sage_audit" if not total_violations else "debt_present",
        "analysis_root": _analysis_root_display(target_root),
        "analysis_snapshot_id": _analysis_snapshot_id(raw_dir),
        "total_violations": total_violations,
        "page": page,
        "page_size": page_size,
        "queue_source": queue_source,
        "coverage": coverage,
        "filters": {
            "rule": rule,
            "project": project,
            "severity": severity,
            "target_root": target_root,
        },
        "artifact_trust": {
            "status": trust_summary.get("status"),
            "scope": trust_summary.get("scope"),
            "freshness": trust_summary.get("freshness"),
            "failures": trust_summary.get("failures", [])[:10],
            "warnings": trust_summary.get("warnings", [])[:10],
        },
        "authority_projection": queue_actionability,
        "items": page_items,
    }
    if str(format or "brief").strip().lower() in {"json", "machine"}:
        return _done(json.dumps(payload, indent=2, ensure_ascii=False))
    return _done(_render_violation_work_queue_brief(payload))


@mcp.tool()
def simulate_change_impact(target_node: str, target_root: str = "", format: str = "brief") -> str:
    """
    Simulate the impact of changing or removing a specific file or symbol.
    Target format: PROJECT::path/to/file.ts (e.g. MAIN::src/main.tsx)
    Returns the blast radius size, direct dependents, and transitive dependents.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("simulate_change_impact", target_root)
    payload = _impact_radius_from_raw(raw_dir, target_node, target_root=target_root)
    if payload is None:
        return _missing_target_artifact_brief("simulate_change_impact", target_node, target_root, ["atlas.json"])
    if str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return _render_impact_brief(payload)


@mcp.tool()
def trigger_autonomous_fix() -> str:
    """Autonomous fix execution is intentionally disabled on the MCP surface."""
    return (
        "Autonomous fix execution is disabled via MCP. "
        "Run the generated script manually from a trusted local shell if you intend to apply it."
    )


@mcp.tool()
def refresh_workspace() -> str:
    """Refresh discovery, governance, and compiled runtime config via the unified CLI."""
    return _run_cli("refresh")


@mcp.tool()
def validate_workspace(include_react: bool = True) -> str:
    """Run lifecycle/entrypoint validators via the unified CLI."""
    args = ["validate"]
    if include_react:
        args.append("--react")
    return _run_cli(*args)


@mcp.tool()
def doctor_workspace(include_validate: bool = False) -> str:
    """Check workspace health and optionally run validators."""
    args = ["doctor"]
    if include_validate:
        args.append("--include-validate")
    return _run_cli(*args)


@mcp.tool()
def get_nexora_brief(refresh: bool = False, human_report: bool = False) -> str:
    """Return the compact AI/human surface brief; refresh it first when requested."""
    if refresh or not (RAW_DIR / "nexora_brief.json").exists():
        run_nexora_brief()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "nexora_brief.md", "Nexora brief report not found.")
    return _read_json_artifact(RAW_DIR / "nexora_brief.json", "Nexora brief artifact not found.")


@mcp.tool()
def get_nexora_agent_contract(refresh: bool = False, human_report: bool = False) -> str:
    """Return the Nexora SAGE AI worker / Human-in-the-Loop operating contract."""
    if refresh or not (RAW_DIR / "nexora_agent_contract.json").exists():
        run_nexora_brief()
        run_agent_contract()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "nexora_agent_contract.md", "Nexora agent contract report not found.")
    return _read_json_artifact(RAW_DIR / "nexora_agent_contract.json", "Nexora agent contract artifact not found.")


def _render_operator_agent_surface_brief(surface: dict[str, Any]) -> str:
    directives = surface.get("directives") if isinstance(surface.get("directives"), list) else []
    path_contract = surface.get("path_contract") if isinstance(surface.get("path_contract"), dict) else {}
    has_actionable_proposal = any(
        isinstance(directive, dict)
        and directive.get("actionability") == "actionable_proposal"
        and bool(directive.get("mutation_proposed"))
        for directive in directives
    )
    mission_lines = (
        [
            "Use this broad target-repository handoff only when a focused work queue or inspection packet is not enough.",
            "Pick one actionable proposal, inspect the listed target files, and make the smallest safe patch under separate actor authority.",
        ]
        if has_actionable_proposal
        else [
            "Use this broad target-repository handoff for bounded orientation only.",
            "No visible directive proposes mutation; obtain an actionable finding or explicit user intent before editing.",
        ]
    )
    lines = [
        "# Operator Packet Brief",
        "",
        "```yaml",
        "mission:",
        *["  - " + line for line in mission_lines],
        "task:",
        f"  surface: {json.dumps(surface.get('surface') or 'target_repository_coding_agent', ensure_ascii=False)}",
        f"  analysis_root: {json.dumps(surface.get('analysis_root') or _analysis_root_display(), ensure_ascii=False)}",
        f"  directive_count: {int(surface.get('directive_count') or len(directives))}",
        f"  returned_directives: {int(surface.get('returned_directives') or len(directives))}",
        f"  contains_platform_status: {str(bool(surface.get('contains_platform_status'))).lower()}",
        f"  contains_debug_artifacts: {str(bool(surface.get('contains_debug_artifacts'))).lower()}",
        "path_contract:",
        f"  open_files_with: {json.dumps(path_contract.get('open_files_with') or 'analysis_root + directives[].target_files or related_files', ensure_ascii=False)}",
        f"  target_refs_usage: {json.dumps(path_contract.get('target_refs_usage') or 'SAGE/MCP follow-up references only; not filesystem paths', ensure_ascii=False)}",
        f"  target_ref_format: {json.dumps(path_contract.get('target_ref_format') or '<project>::<repo_relative_path>; MAIN is the primary analyzed project scope', ensure_ascii=False)}",
        "directives:",
    ]
    if directives:
        for directive in directives[:3]:
            if not isinstance(directive, dict):
                continue
            explanation = directive.get("rule_explanation") if isinstance(directive.get("rule_explanation"), dict) else {}
            mutation_proposed = (
                directive.get("actionability") == "actionable_proposal"
                and bool(directive.get("mutation_proposed"))
            )
            display_action = (
                directive.get("action")
                if mutation_proposed
                else "Inspect the bounded evidence only; this directive does not propose repository mutation."
            )
            lines.extend(
                [
                    f"  - id: {json.dumps(directive.get('id') or '', ensure_ascii=False)}",
                    f"    intent: {json.dumps(directive.get('intent') or '', ensure_ascii=False)}",
                    f"    actionability: {json.dumps(directive.get('actionability') or 'orientation_only', ensure_ascii=False)}",
                    f"    mutation_proposed: {str(bool(mutation_proposed)).lower()}",
                    f"    mutation_authority: {json.dumps(directive.get('mutation_authority') or 'not_granted_by_this_directive', ensure_ascii=False)}",
                    "    approval_is_not_mutation_authority: true",
                    f"    target_files: {json.dumps(directive.get('target_files') or [], ensure_ascii=False)}",
                    f"    related_files: {json.dumps(directive.get('related_files') or [], ensure_ascii=False)}",
                    f"    rule: {json.dumps(directive.get('rule') or '', ensure_ascii=False)}",
                    f"    rule_label: {json.dumps(explanation.get('label') or str(directive.get('rule') or '').replace('_', ' ').title(), ensure_ascii=False)}",
                    f"    evidence: {json.dumps(directive.get('evidence') or '', ensure_ascii=False)}",
                    "    one_shot_patch_ready: false",
                    "    next_tool: \"get_violation_work_queue or inspect_file\"",
                    f"    action: {json.dumps(display_action or '', ensure_ascii=False)}",
                    f"    validation_tools: {json.dumps(directive.get('validation_tools') or target_repo_validation_tools(), ensure_ascii=False)}",
                    f"    rule_mode: {json.dumps(directive.get('rule_mode') or explanation.get('mode') or 'unknown', ensure_ascii=False)}",
                    f"    human_approval_required: {str(bool(directive.get('human_approval_required'))).lower()}",
                    f"    approval_decision_source: {json.dumps(directive.get('approval_decision_source') or 'unknown', ensure_ascii=False)}",
                    f"    approval_reason: {json.dumps(directive.get('approval_reason') or '', ensure_ascii=False)}",
                    f"    confidence: {json.dumps(directive.get('confidence') or '', ensure_ascii=False)}",
                ]
            )
    else:
        lines.append("  []")
    machine_projection = json.dumps("get_operator_packet(format='json')", ensure_ascii=False)
    lines.extend(
        [
            "follow_up:",
            f"  machine_projection: {machine_projection}",
            '  hint: "The machine projection preserves the same target-repository boundary in structured JSON."',
            "do:",
            "  - Prefer get_violation_work_queue or inspect_file when a concrete target exists.",
            "  - Use this broad packet to choose a first target, then switch to target inspection and impact/test tools.",
            "  - Run the listed validation commands before finalizing a patch.",
            "do_not:",
            "  - Do not treat this broad packet as permission for multi-file cleanup.",
            "  - Do not treat target_ref values as filesystem paths.",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _operator_privileged_projection_allowed() -> bool:
    try:
        identity = _resolve_mcp_execution_identity()
    except ValueError:
        return False
    return identity.get("system_scope") == "SAGE_ON_SAGE"


def _operator_projection_access_denied(*, requested_format: str) -> str:
    payload = {
        "status": "BLOCKED",
        "reason": "operator_projection_requires_sage_on_sage_profile",
        "agent_action": "Use the default target-repository projection, or reconnect through an explicitly authorized SAGE operator profile.",
        "claim_boundary": "No SAGE platform, debug, human-report, or refresh projection was returned.",
    }
    if requested_format in {"json", "machine"}:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return "\n".join(
        [
            "# Operator Projection Blocked",
            "",
            "- status: `BLOCKED`",
            "- reason: `operator_projection_requires_sage_on_sage_profile`",
            "- action: Use the default target-repository projection, or reconnect through an explicitly authorized SAGE operator profile.",
            "- boundary: No SAGE platform, debug, human-report, or refresh projection was returned.",
        ]
    )


@mcp.tool()
def get_operator_packet(refresh: bool = False, human_report: bool = False, projection: str = "agent", target_root: str = "", format: str = "brief") -> str:
    """Return the operator packet; default projection is target-repository agent context."""
    requested_format = str(format or "brief").strip().lower()
    requested_projection = str(projection or "agent").strip().lower()
    privileged_projection = requested_projection in {"full", "operator", "platform", "debug"}
    if (refresh or human_report or privileged_projection) and not _operator_privileged_projection_allowed():
        return _operator_projection_access_denied(requested_format=requested_format)
    if target_root:
        try:
            raw_dir = _raw_dir_for_target(target_root)
            reports_dir = _reports_dir_for_target(target_root)
        except ValueError as exc:
            return _invalid_external_target_brief("get_operator_packet", target_root)
        if refresh:
            return (
                "External target operator packet refresh is not implicit. "
                "Run run_external_target_analysis(target_root=..., full=true) first."
            )
        if human_report:
            return _read_text_artifact(reports_dir / "nexora_operator_packet.md", "External target operator packet report not found.")
        packet = _load_json(raw_dir / "nexora_operator_packet.json")
        if not isinstance(packet, dict):
            return _missing_target_artifact_brief("get_operator_packet", "nexora_operator_packet", target_root, ["nexora_operator_packet.json"])
        if requested_projection in {"full", "operator", "platform", "debug"}:
            return json.dumps(packet, indent=2, ensure_ascii=False)
        agent_surface = packet.get("target_repository_agent_surface")
        if not isinstance(agent_surface, dict):
            return "Target repository agent surface not found in external target operator packet. Refresh external target analysis first."
        compact_surface = dict(agent_surface)
        compact_surface["analysis_root"] = _analysis_root_display(target_root)
        directives = agent_surface.get("directives")
        if isinstance(directives, list):
            max_directives = 3
            compact_surface["directives"] = directives[:max_directives]
            compact_surface["directive_count"] = len(directives)
            compact_surface["returned_directives"] = min(len(directives), max_directives)
        if requested_format in {"json", "machine"}:
            return json.dumps(compact_surface, indent=2, ensure_ascii=False)
        return _render_operator_agent_surface_brief(compact_surface)

    if refresh or not (RAW_DIR / "nexora_operator_packet.json").exists():
        run_nexora_brief()
        run_agent_contract()
        run_operator_packet()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "nexora_operator_packet.md", "Nexora operator packet report not found.")
    packet = _load_json(RAW_DIR / "nexora_operator_packet.json")
    if not isinstance(packet, dict):
        return "Nexora operator packet artifact not found."
    if requested_projection not in {"full", "operator", "platform", "debug"}:
        agent_surface = packet.get("target_repository_agent_surface")
        expected_root = str(ANALYZED_REPOSITORY_ROOT)
        if not isinstance(agent_surface, dict) or str(agent_surface.get("analysis_root") or "") != expected_root:
            run_operator_packet()
            packet = _load_json(RAW_DIR / "nexora_operator_packet.json")
            if not isinstance(packet, dict):
                return "Nexora operator packet artifact not found after refresh."
    if requested_projection in {"full", "operator", "platform", "debug"}:
        return json.dumps(packet, indent=2, ensure_ascii=False)
    agent_surface = packet.get("target_repository_agent_surface")
    if not isinstance(agent_surface, dict):
        return "Target repository agent surface not found in operator packet. Refresh the operator packet first."
    compact_surface = dict(agent_surface)
    directives = agent_surface.get("directives")
    if isinstance(directives, list):
        max_directives = 3
        compact_surface["directives"] = directives[:max_directives]
        compact_surface["directive_count"] = len(directives)
        compact_surface["returned_directives"] = min(len(directives), max_directives)
    if requested_format in {"json", "machine"}:
        return json.dumps(compact_surface, indent=2, ensure_ascii=False)
    return _render_operator_agent_surface_brief(compact_surface)


@mcp.tool()
def get_human_approval_gates(target_root: str = "") -> str:
    """Return current Human-in-the-Loop approval gates for risky AI actions."""
    target = _valid_external_target(target_root) if target_root else None
    if target_root and target is None:
        return _invalid_external_target_brief("get_human_approval_gates", target_root)
    execution_identity = resolve_execution_identity(
        target_root=target_root,
        actor_profile=_active_mcp_actor_profile(),
        reality_profile=str(os.environ.get(SAGE_REALITY_TARGET_PROFILE_ENV) or ""),
        public_distribution=(BASE_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file(),
        installation_root=BASE_DIR,
        default_repository_root=ANALYZED_REPOSITORY_ROOT,
    )
    try:
        gates = build_approval_gates(
            raw_dir=_raw_dir_for_target(target_root),
            system_scope=execution_identity["system_scope"],
            subject_root=execution_identity["subject_root"],
        )
    except ValueError as exc:
        return json.dumps(
            {
                "status": "BLOCKED",
                "reason": "approval_gate_scope_unresolved",
                "error": str(exc),
            },
            indent=2,
            ensure_ascii=False,
        )
    return json.dumps(gates, indent=2, ensure_ascii=False)


@mcp.tool()
def get_hitl_approval_ledger(human_report: bool = False) -> str:
    """Return the Human-in-the-Loop approval decision ledger."""
    if not (RAW_DIR / "hitl_approval_ledger.json").exists():
        init_ledger()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "hitl_approval_ledger.md", "HITL approval ledger report not found.")
    return _read_json_artifact(RAW_DIR / "hitl_approval_ledger.json", "HITL approval ledger artifact not found.")


@mcp.tool()
def verify_hitl_approval_ledger() -> str:
    """Verify HITL approval ledger HMAC signatures and hash-chain continuity."""
    return json.dumps(verify_ledger(), indent=2, ensure_ascii=False)


@mcp.tool()
def record_hitl_decision(
    gate: str,
    decision: str,
    scope: str,
    actor: str = "human",
    rationale: str = "",
    evidence: str = "",
    expires_at: str = "",
    request_id: str = "",
) -> str:
    """Record a human approval decision. Evidence may be a semicolon-separated artifact list."""
    evidence_items = [item.strip() for item in str(evidence or "").split(";") if item.strip()]
    entry = record_decision(
        gate=gate,
        decision=decision,
        scope=scope,
        actor=actor,
        rationale=rationale,
        evidence=evidence_items,
        expires_at=expires_at,
        request_id=request_id,
    )
    if request_id:
        try:
            update_request_status(
                request_id=request_id,
                status="closed",
                reason=f"Resolved by ledger decision: {decision}",
                linked_ledger_entry=str(entry.get("id") or ""),
            )
        except Exception:
            pass
    run_operator_packet()
    return json.dumps(entry, indent=2, ensure_ascii=False)


@mcp.tool()
def get_hitl_decision_requests(human_report: bool = False) -> str:
    """Return AI-created decision requests awaiting or documenting human review."""
    if not (RAW_DIR / "hitl_decision_requests.json").exists():
        init_requests()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "hitl_decision_requests.md", "HITL decision requests report not found.")
    return _read_json_artifact(RAW_DIR / "hitl_decision_requests.json", "HITL decision requests artifact not found.")


@mcp.tool()
def create_hitl_decision_request(
    gate: str,
    scope: str,
    proposed_action: str,
    risk: str,
    rationale: str = "",
    evidence: str = "",
    confidence: str = "medium",
    requested_by: str = "ai_agent",
) -> str:
    """Create a structured human decision request before risky AI actions."""
    evidence_items = [item.strip() for item in str(evidence or "").split(";") if item.strip()]
    request = create_request(
        gate=gate,
        scope=scope,
        proposed_action=proposed_action,
        risk=risk,
        rationale=rationale,
        evidence=evidence_items,
        confidence=confidence,
        requested_by=requested_by,
    )
    run_operator_packet()
    return json.dumps(request, indent=2, ensure_ascii=False)


@mcp.tool()
def update_hitl_decision_request(
    request_id: str,
    status: str,
    reason: str = "",
    linked_ledger_entry: str = "",
) -> str:
    """Update a human approval request; mutates HITL state and should follow explicit approval intent."""
    request = update_request_status(
        request_id=request_id,
        status=status,
        reason=reason,
        linked_ledger_entry=linked_ledger_entry,
    )
    run_operator_packet()
    return json.dumps(request, indent=2, ensure_ascii=False)


@mcp.tool()
def validate_hitl_governance() -> str:
    """Validate HITL contract shape, approval gates, operator packet, and approval ledger."""
    validation = run_hitl_governance_validation()
    return json.dumps(validation, indent=2, ensure_ascii=False)


@mcp.tool()
def validate_hitl_lifecycle_smoke() -> str:
    """Run isolated HITL lifecycle smoke validation."""
    validation = run_hitl_lifecycle_smoke()
    return json.dumps(validation, indent=2, ensure_ascii=False)


@mcp.tool()
def get_agent_response_template() -> str:
    """Return the canonical Nexora agent answer template."""
    return json.dumps(response_template(), indent=2, ensure_ascii=False)


@mcp.tool()
def validate_agent_response(response_json: str) -> str:
    """Validate an AI agent response JSON string against the Nexora response contract."""
    try:
        response = json.loads(response_json)
    except Exception as exc:
        response = {"_parse_error": str(exc)}
    validation = validate_response(response if isinstance(response, dict) else {})
    return json.dumps(validation, indent=2, ensure_ascii=False)


@mcp.tool()
def validate_agent_response_template() -> str:
    """Write and validate the canonical Nexora agent response template."""
    validation = run_template_smoke()
    return json.dumps(validation, indent=2, ensure_ascii=False)


@mcp.tool()
def get_agent_response_ledger(human_report: bool = False) -> str:
    """Return the validated AI response ledger."""
    if not (RAW_DIR / "nexora_agent_response_ledger.json").exists():
        init_response_ledger()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "nexora_agent_response_ledger.md", "Nexora agent response ledger report not found.")
    return _read_json_artifact(RAW_DIR / "nexora_agent_response_ledger.json", "Nexora agent response ledger artifact not found.")


@mcp.tool()
def record_agent_response(response_json: str, task_id: str = "", actor: str = "ai_agent") -> str:
    """Validate and record an AI response JSON string for later human audit."""
    try:
        response = json.loads(response_json)
    except Exception as exc:
        response = {"_parse_error": str(exc)}
    entry = record_response(response if isinstance(response, dict) else {}, task_id=task_id, actor=actor)
    run_operator_packet()
    return json.dumps(entry, indent=2, ensure_ascii=False)


@mcp.tool()
def get_agent_handoff(refresh: bool = False, human_report: bool = False) -> str:
    """Return the pasteable AI agent handoff prompt for MCP-less or external AI use."""
    if refresh or not (RAW_DIR / "nexora_agent_handoff.json").exists():
        run_agent_handoff()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "nexora_agent_handoff.md", "Nexora agent handoff report not found.")
    return _read_json_artifact(RAW_DIR / "nexora_agent_handoff.json", "Nexora agent handoff artifact not found.")


@mcp.tool()
def get_watchdog_session(human_report: bool = False, target_root: str = "", profile_id: str = "") -> str:
    """Return the latest persisted watchdog pulse/session."""
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("get_watchdog_session", started, result, status=status, fail_closed_reason=fail_closed_reason)

    try:
        raw_dir, reports_dir, resolved_target_root = _watchdog_session_roots(target_root, profile_id)
    except ValueError as exc:
        return _done(
            "\n".join(
                [
                    "# Watchdog Session Request Rejected",
                    "",
                    "```yaml",
                    "status: invalid_watchdog_target_scope",
                    "reason: " + json.dumps(str(exc), ensure_ascii=False),
                    "target_root: " + json.dumps(target_root, ensure_ascii=False),
                    "profile_id: " + json.dumps(profile_id, ensure_ascii=False),
                    "do_not:",
                    "  - Do not fall back to the primary workspace watchdog session.",
                    "```",
                    "",
                ]
            ),
            status="fail_closed",
            fail_closed_reason="invalid_watchdog_target_scope",
        )

    if human_report:
        return _done(_read_text_artifact(reports_dir / "watchdog_session.md", "Watchdog session report not found. Run `python sage.py watch --once` first."))
    if not (raw_dir / "watchdog_session.json").exists():
        return _done("\n".join(
            [
                "# Watchdog Session Missing",
                "",
                "```yaml",
                "status: missing_watchdog_session",
                "meaning: \"No live watchdog pulse has been persisted for this workspace.\"",
                "next_step: \"Run run_watchdog_once(path?) or python sage.py watch --once --path <file-or-directory> when live-change context is required.\"",
                "next_action: \"Run run_watchdog_once(path?) or python sage.py watch --once --path <file-or-directory> when live-change context is required.\"",
                "do_not:",
                "  - Do not infer that the repository has no active risk from a missing watchdog session.",
                "  - Do not fall back to stale active-signal or unrelated target artifacts.",
                "```",
                "",
            ]
        ), status="fail_closed", fail_closed_reason="missing_watchdog_session")
    payload = _load_json(raw_dir / "watchdog_session.json") or {}
    if not isinstance(payload, dict):
        payload = {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    target_descriptor = payload.get("target_descriptor") if isinstance(payload.get("target_descriptor"), dict) else {}
    debt_field = str(target_descriptor.get("proof_debt_field") or "target_repository_deep_proof_debt")
    debt = payload.get(debt_field) if isinstance(payload.get(debt_field), dict) else {}
    embedded_pulse_ledger = payload.get("watchdog_pulse_ledger") if isinstance(payload.get("watchdog_pulse_ledger"), dict) else {}
    external_pulse_ledger = _load_json(raw_dir / "watchdog_pulse_ledger.json") or {}
    if not isinstance(external_pulse_ledger, dict):
        external_pulse_ledger = {}
    session_path = raw_dir / "watchdog_session.json"
    ledger_path = raw_dir / "watchdog_pulse_ledger.json"
    session_mtime = _raw_artifact_source_mtime("watchdog_session", session_path)
    ledger_mtime = _raw_artifact_source_mtime("watchdog_pulse_ledger", ledger_path)
    external_ledger_is_newer = bool(external_pulse_ledger) and ledger_mtime > session_mtime
    external_ledger_summary = (
        external_pulse_ledger.get("summary") if isinstance(external_pulse_ledger.get("summary"), dict) else {}
    )
    pulse_ledger = external_pulse_ledger if external_ledger_is_newer else embedded_pulse_ledger
    pulse_ledger_summary = pulse_ledger.get("summary") if isinstance(pulse_ledger.get("summary"), dict) else {}
    pulse_ledger_meta = pulse_ledger.get("meta") if isinstance(pulse_ledger.get("meta"), dict) else {}
    current_violation_count = (
        pulse_ledger_summary.get("current_violation_count")
        if "current_violation_count" in pulse_ledger_summary
        else pulse_ledger.get("current_violation_count")
    )
    if current_violation_count in (None, "") and external_ledger_summary:
        current_violation_count = external_ledger_summary.get("current_violation_count")
    session_currentness = "stale_against_pulse_ledger" if external_ledger_is_newer else "latest_session_artifact"
    violations_sample_status = "historical_session_not_current" if external_ledger_is_newer else "current_session"
    path_contract = payload.get("path_contract") if isinstance(payload.get("path_contract"), dict) else {}
    input_origin = str(payload.get("input_origin") or summary.get("input_origin") or "legacy_changed_files")
    input_files = payload.get("input_files") if isinstance(payload.get("input_files"), list) else []
    changed_files = payload.get("changed_files") if isinstance(payload.get("changed_files"), list) else []
    sample_files = payload.get("sample_files") if isinstance(payload.get("sample_files"), list) else []
    if not input_files:
        input_files = sample_files or changed_files
    violations = payload.get("violations") if isinstance(payload.get("violations"), list) else []
    analysis_root = Path(path_contract.get("analysis_root") or str(ANALYZED_REPOSITORY_ROOT))

    proof_boundary = payload.get("proof_boundary") if isinstance(payload.get("proof_boundary"), dict) else {}
    recommended_deep_proof = str(proof_boundary.get("recommended_deep_proof") or "python sage.py run --profile release-deep")
    recommended_deep_proof = recommended_deep_proof.replace("nexora.py", "sage.py")
    producer = payload.get("producer") if isinstance(payload.get("producer"), dict) else {}
    required_identity = {
        "system_scope": target_descriptor.get("system_scope"),
        "acquisition_mode": target_descriptor.get("acquisition_mode"),
        "profile_id": target_descriptor.get("profile_id") or profile_id,
        "subject_root": target_descriptor.get("subject_root"),
        "artifact_strategy": target_descriptor.get("artifact_strategy"),
        "producer_kind": producer.get("kind"),
        "producer_command": producer.get("command"),
    }
    missing_identity_fields = [
        field for field, value in required_identity.items() if value in (None, "", [])
    ]
    identity_status = "complete" if not missing_identity_fields else "incomplete"
    session_status = (
        "watchdog_session_available"
        if identity_status == "complete"
        else "watchdog_session_identity_incomplete"
    )
    pulse_ledger_status = (
        pulse_ledger_summary.get("status")
        or pulse_ledger.get("status")
        or ("not_created" if not pulse_ledger else "incomplete")
    )
    yaml_lines = [
        "status: " + session_status,
        "meaning: \"Latest persisted watchdog pulse is save-time context, not repo-wide release proof.\"",
        "execution_identity:",
        "  status: " + json.dumps(identity_status, ensure_ascii=False),
        "  missing_fields: " + json.dumps(missing_identity_fields, ensure_ascii=False),
        "  producer_kind: " + json.dumps(producer.get("kind") or "", ensure_ascii=False),
        "  producer_command: " + json.dumps(producer.get("command") or [], ensure_ascii=False),
        "session_currentness:",
        "  status: " + json.dumps(session_currentness, ensure_ascii=False),
        "  session_generated_at: " + json.dumps((payload.get("meta") or {}).get("generated_at") or "", ensure_ascii=False),
        "  pulse_ledger_generated_at: " + json.dumps(pulse_ledger_meta.get("generated_at") or "", ensure_ascii=False),
        "  pulse_ledger_source: " + json.dumps("external_watchdog_pulse_ledger_json" if external_ledger_is_newer else "embedded_watchdog_session", ensure_ascii=False),
        f"  pulse_ledger_newer_than_session: {str(external_ledger_is_newer).lower()}",
        "  agent_rule: " + json.dumps(
            "If session_currentness is stale_against_pulse_ledger, treat violations_sample as historical and run run_watchdog_once(path?) before acting on live-save context."
            if external_ledger_is_newer
            else "This is the latest watchdog_session artifact; still treat it as save-time context, not release proof.",
            ensure_ascii=False,
        ),
        "summary:",
        "  input_origin: " + json.dumps(input_origin, ensure_ascii=False),
        f"  input_files: {int(summary.get('input_files') or len(input_files))}",
        f"  changed_files: {int(summary.get('changed_files') or len(changed_files))}",
        f"  sample_files: {int(summary.get('sample_files') or len(sample_files))}",
        "  integrity: " + json.dumps(summary.get("integrity") or "", ensure_ascii=False),
        f"  violation_count: {int(summary.get('violation_count') or len(violations))}",
        "  claim_boundary: " + json.dumps(summary.get("claim_boundary") or "surgical_change_context", ensure_ascii=False),
        "target_descriptor:",
        "  system_scope: " + json.dumps(target_descriptor.get("system_scope") or "unknown", ensure_ascii=False),
        "  acquisition_mode: " + json.dumps(target_descriptor.get("acquisition_mode") or "unknown", ensure_ascii=False),
        "  profile_id: " + json.dumps(target_descriptor.get("profile_id") or profile_id or "", ensure_ascii=False),
        "  artifact_strategy: " + json.dumps(target_descriptor.get("artifact_strategy") or "unknown", ensure_ascii=False),
        "  subject_root: " + json.dumps(target_descriptor.get("subject_root") or "", ensure_ascii=False),
        "  requested_target_root: " + json.dumps(resolved_target_root or "<primary_workspace>", ensure_ascii=False),
        "path_contract:",
        "  analysis_root: " + json.dumps(path_contract.get("analysis_root") or str(ANALYZED_REPOSITORY_ROOT), ensure_ascii=False),
        "  open_files_with: " + json.dumps(path_contract.get("open_files_with") or "analysis_root + input_files or violations[].repo_relative_path", ensure_ascii=False),
        "  target_ref_usage: " + json.dumps(path_contract.get("target_ref_usage") or "Use target_ref for SAGE/MCP follow-up references, not as a filesystem path.", ensure_ascii=False),
        "proof_boundary:",
        "  not_release_proof: true",
        "  deep_proof_debt_status: " + json.dumps(debt.get("status") or "unknown", ensure_ascii=False),
        "  deep_proof_debt_field: " + json.dumps(debt_field, ensure_ascii=False),
        "  recommended_deep_proof: " + json.dumps(recommended_deep_proof, ensure_ascii=False),
        "watchdog_pulse_ledger:",
        "  status: " + json.dumps(pulse_ledger_status, ensure_ascii=False),
        f"  unresolved_unread_count: {int(pulse_ledger_summary.get('unresolved_unread_count') or pulse_ledger.get('unresolved_unread_count') or 0)}",
        "  current_violation_count: " + json.dumps(current_violation_count if current_violation_count not in (None, "") else "unknown", ensure_ascii=False),
        "  pulse_id: " + json.dumps((pulse_ledger.get("latest_pulse") or {}).get("pulse_id") or pulse_ledger.get("pulse_id") or "", ensure_ascii=False),
        "  agent_rule: " + json.dumps(pulse_ledger.get("agent_rule") or "Read unresolved watchdog pulse findings before continuing live edits.", ensure_ascii=False),
        "input_files_sample:",
    ]
    if input_files:
        for item in input_files[:8]:
            normalized = _canonical_openable_path(item, analysis_root)
            yaml_lines.append("  - reported_path: " + json.dumps(normalized["reported_path"], ensure_ascii=False))
            yaml_lines.append("    openable_path: " + json.dumps(normalized["openable_path"], ensure_ascii=False))
            yaml_lines.append("    path_status: " + json.dumps(normalized["path_status"], ensure_ascii=False))
    else:
        yaml_lines.append("  []")
    yaml_lines.append("violations_sample:")
    yaml_lines.append("  status: " + json.dumps(violations_sample_status, ensure_ascii=False))
    yaml_lines.append("  items:")
    if violations:
        for row in violations[:5]:
            if isinstance(row, dict):
                yaml_lines.append("    - file: " + json.dumps(row.get("repo_relative_path") or row.get("file") or "", ensure_ascii=False))
                yaml_lines.append("      rule: " + json.dumps(row.get("rule") or row.get("code") or "", ensure_ascii=False))
                yaml_lines.append("      severity: " + json.dumps(row.get("severity") or row.get("level") or "", ensure_ascii=False))
    else:
        yaml_lines.append("    []")
    yaml_lines.extend(
        [
            "next_step: \"If session_currentness is stale_against_pulse_ledger, run run_watchdog_once(path?) before using this as current context; otherwise use input_files_sample or violations_sample within the declared input_origin and run deeper proof before repo-wide claims.\"",
            "do:",
            "  - Treat smoke_sample input as synthetic validation scope, never as a captured filesystem change.",
            "  - Treat filesystem_event input as bounded live-change context only.",
            "  - Check watchdog_pulse_ledger.unresolved_unread_count before starting another edit.",
            "  - Use inspect_file, get_impact_radius, and get_test_impact before editing a listed file.",
            "  - Refresh broader proof before claiming repository-wide health.",
            "do_not:",
            "  - Do not treat historical violations_sample items as current when session_currentness is stale_against_pulse_ledger.",
            "  - Do not infer repository-wide safety from this watchdog session.",
            "  - Do not fall back to stale active-signal or unrelated target artifacts.",
        ]
    )
    rendered = "\n".join(
        [
            "# Watchdog Session",
            "",
            "```yaml",
            *_with_context_budget(yaml_lines, budget_tokens=DEFAULT_WATCHDOG_SESSION_PACKET_BUDGET_TOKENS),
            "```",
            "",
        ]
    )
    if missing_identity_fields:
        return _done(
            rendered,
            status="fail_closed",
            fail_closed_reason="watchdog_session_identity_incomplete",
        )
    return _done(rendered)


@mcp.tool()
def run_watchdog_once(path: str = "", target_root: str = "", profile_id: str = "") -> str:
    """Run one exact-file or directory-sample watchdog pulse and return its session artifact."""
    try:
        raw_dir, _reports_dir, resolved_target_root = _watchdog_session_roots(target_root, profile_id)
    except ValueError as exc:
        return json.dumps(
            {
                "status": "fail_closed",
                "reason": "invalid_watchdog_target_scope",
                "detail": str(exc),
                "target_root": target_root,
                "profile_id": profile_id,
                "agent_rule": "Do not fall back to the primary workspace watchdog session.",
            },
            indent=2,
            ensure_ascii=False,
        )
    args = ["watch", "--once"]
    if resolved_target_root:
        args.extend(["--target-root", resolved_target_root])
    if path:
        args.extend(["--path", path])
    previous_session = _load_json(raw_dir / "watchdog_session.json")
    previous_identity = (
        hashlib.sha256(json.dumps(previous_session, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if previous_session
        else ""
    )
    env_overrides = None
    if profile_id == SAGE_SELF_REALITY_PROFILE:
        env_overrides = {
            SAGE_ACTOR_PROFILE_ENV: SAGE_OPERATOR_ACTOR_PROFILE,
            SAGE_REALITY_TARGET_PROFILE_ENV: SAGE_SELF_REALITY_PROFILE,
        }
    output = _run_cli(
        *args,
        env_overrides=env_overrides,
    )
    if not _script_succeeded(output):
        return json.dumps(
            {
                "status": "FAILED",
                "terminal_status": "FAILED",
                "reason": "watchdog_command_failed",
                "command_output": output,
                "current_watchdog_session": None,
                "historical_session": {
                    "available": bool(previous_session),
                    "status": "historical_not_current_for_failed_run",
                    "generated_at": ((previous_session.get("meta") or {}).get("generated_at") if previous_session else None),
                },
                "required_action": "Resolve the reported watchdog command failure, then rerun run_watchdog_once before using watchdog evidence as current context.",
                "agent_rule": "A failed watchdog command cannot inherit or return the previous persisted session as its current result.",
            },
            indent=2,
            ensure_ascii=False,
        )
    session = _load_json(raw_dir / "watchdog_session.json")
    current_identity = (
        hashlib.sha256(json.dumps(session, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if session
        else ""
    )
    if not session or current_identity == previous_identity:
        return json.dumps(
            {
                "status": "INCOMPLETE_EVIDENCE",
                "terminal_status": "FAILED",
                "reason": "watchdog_session_not_refreshed",
                "command_output": output,
                "current_watchdog_session": None,
                "historical_session": {
                    "available": bool(previous_session),
                    "status": "historical_not_current_for_requested_run",
                    "generated_at": ((previous_session.get("meta") or {}).get("generated_at") if previous_session else None),
                },
                "required_action": "Inspect watchdog persistence telemetry and rerun; do not use the unchanged historical session as current evidence.",
            },
            indent=2,
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "status": "PASS",
            "terminal_status": "PASS",
            "command_output": output,
            "watchdog_session": session,
        },
        indent=2,
        ensure_ascii=False,
    )


def _add_agent_inspection_path_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose top-level agent navigation fields without changing inspection internals."""
    if not isinstance(payload, dict):
        return payload
    contexts = payload.get("target_file_context") if isinstance(payload.get("target_file_context"), list) else []
    target_files: list[str] = []
    target_refs: list[str] = []
    target_projects: list[str] = []
    for row in contexts:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project_key") or row.get("project") or "").strip()
        rel = str(row.get("repo_relative_path") or row.get("workspace_rel") or row.get("file") or "").replace("\\", "/").strip("/")
        if rel and rel not in target_files:
            target_files.append(rel)
        if project and project not in target_projects:
            target_projects.append(project)
        if project and rel:
            ref = f"{project}::{rel}"
            if ref not in target_refs:
                target_refs.append(ref)
    if target_files:
        payload.setdefault("target_file", target_files[0])
        payload.setdefault("target_files", target_files)
    if target_refs:
        payload.setdefault("target_ref", target_refs[0])
        payload.setdefault("target_refs", target_refs)
    if target_projects:
        payload.setdefault("target_project", target_projects[0])
        payload.setdefault("target_projects", target_projects)
    return payload


def _inspection_context_files_openable(payload: dict[str, Any], target_root: str = "") -> bool:
    contexts = payload.get("target_file_context") if isinstance(payload.get("target_file_context"), list) else []
    if not contexts:
        return False
    analysis_root = Path(_analysis_root_display(target_root)).resolve()
    for row in contexts:
        if not isinstance(row, dict):
            continue
        rel = str(row.get("repo_relative_path") or row.get("workspace_rel") or row.get("file") or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        target_abs = (analysis_root / rel).resolve()
        if analysis_root in [target_abs, *target_abs.parents] and target_abs.exists() and target_abs.is_file():
            return True
    return False


def _inspection_symbol_has_unique_exact_match(payload: dict[str, Any]) -> bool:
    target = payload.get("target")
    target_value = str(target.get("value") or "") if isinstance(target, dict) else str(target or "")
    symbol_query = target_value.split("::", 1)[-1].strip().casefold()
    if not symbol_query:
        return False

    exact_matches: set[tuple[str, str, str, int, int]] = set()
    rows = payload.get("atlas_symbols") if isinstance(payload.get("atlas_symbols"), list) else []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("symbol") or "").strip().casefold() != symbol_query:
            continue
        exact_matches.add(
            (
                str(row.get("project") or ""),
                str(row.get("target_ref") or row.get("workspace_rel") or row.get("file") or ""),
                str(row.get("symbol") or ""),
                int(row.get("line") or row.get("start_line") or 0),
                int(row.get("end_line") or row.get("line") or 0),
            )
        )
    if len(exact_matches) != 1:
        return False
    status = payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}
    snippets = status.get("target_source_snippets") if isinstance(status.get("target_source_snippets"), list) else []
    return any(
        isinstance(row, dict)
        and str(row.get("symbol") or "").strip().casefold() == symbol_query
        and str(row.get("snippet_status") or "") == "included"
        for row in snippets
    )


def _set_inspection_edit_authority(payload: dict[str, Any], *, grounded: bool, kind: str) -> None:
    """Separate source grounding from one-shot edit readiness."""
    status = payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}
    snippets = status.get("target_source_snippets") if isinstance(status.get("target_source_snippets"), list) else []
    complete_snippets = bool(snippets) and all(
        isinstance(snippet, dict) and str(snippet.get("snippet_status") or "") == "included"
        for snippet in snippets
    )
    requested_range = status.get("requested_line_range") if isinstance(status.get("requested_line_range"), dict) else {}
    range_complete = bool(requested_range) and str(requested_range.get("line_status") or "") == "available"
    target_kind = str(kind or "").strip().lower()
    exact_symbol_match = target_kind != "symbol" or _inspection_symbol_has_unique_exact_match(payload)
    one_shot_ready = bool(
        grounded
        and complete_snippets
        and exact_symbol_match
        and (target_kind == "symbol" or (target_kind == "file" and range_complete))
    )

    payload["source_grounded_for_inspection"] = bool(grounded)
    payload["one_shot_edit_ready"] = one_shot_ready
    payload["safe_to_edit_from_inspection"] = one_shot_ready
    payload["inspection_authority"] = "bounded_edit_context" if one_shot_ready else "orientation_only"
    if not grounded:
        payload["edit_readiness_reason"] = "source_not_grounded"
    elif not snippets:
        payload["edit_readiness_reason"] = "no_source_snippet"
    elif not complete_snippets:
        payload["edit_readiness_reason"] = "source_context_partial_or_omitted"
    elif target_kind == "symbol" and not exact_symbol_match:
        payload["edit_readiness_reason"] = "symbol_query_requires_unique_exact_match"
    elif target_kind == "file" and not range_complete:
        payload["edit_readiness_reason"] = "file_inspection_requires_explicit_complete_line_range"
    else:
        payload["edit_readiness_reason"] = "complete_bounded_source_context"


def _add_inspection_grounding_status(payload: dict[str, Any], raw_dir: Path, target: str, kind: str, target_root: str = "") -> dict[str, Any]:
    """Make inspection machine output fail-closed when the requested target is not source-grounded."""
    if not isinstance(payload, dict):
        return payload
    contexts = payload.get("target_file_context") if isinstance(payload.get("target_file_context"), list) else []
    if str(kind or "") == "file":
        payload_spans = payload.get("target_spans") if isinstance(payload.get("target_spans"), list) else []
        if not payload_spans:
            payload_spans = (
                payload.get("atlas_symbols", [])[:AGENT_INSPECTION_MAX_TARGET_SPANS]
                if isinstance(payload.get("atlas_symbols"), list)
                else []
            )
        preferred_symbols = {
            str(span.get("symbol") or "").strip()
            for span in payload_spans
            if isinstance(span, dict) and str(span.get("symbol") or "").strip()
        }
        status = _target_path_status(
            raw_dir,
            target,
            target_root=target_root,
            preferred_symbols=preferred_symbols or None,
        )
        payload["target_path_status"] = _narrow_source_grounding_to_inspection_target(
            _public_target_path_status(status),
            payload,
            target,
        )
        grounded = bool(contexts) and bool(status.get("exists")) and bool(status.get("indexed"))
        missing_reason = (
            "requested_target_not_source_grounded"
            if not status.get("exists") or not status.get("indexed")
            else "requested_target_has_no_inspection_context"
        )
    else:
        if len(contexts) == 1 and isinstance(contexts[0], dict):
            row = contexts[0]
            rel = str(row.get("repo_relative_path") or row.get("workspace_rel") or row.get("file") or "").replace("\\", "/").strip("/")
            project_key = str(row.get("project_key") or row.get("project") or _project_from_ref(str(row.get("atlas_node") or "")) or "").strip()
            if rel:
                target_ref = f"{project_key}::{rel}" if project_key else rel
                preferred_symbols = {
                    str(symbol_row.get("symbol") or "").strip()
                    for symbol_row in payload.get("atlas_symbols", [])
                    if isinstance(symbol_row, dict) and str(symbol_row.get("symbol") or "").strip()
                }
                status = _target_path_status(
                    raw_dir,
                    target_ref,
                    target_root=target_root,
                    preferred_symbols=preferred_symbols,
                )
                payload["target_path_status"] = _narrow_source_grounding_to_inspection_target(
                    _public_target_path_status(status),
                    payload,
                    target,
                )
        grounded = bool(contexts) and _inspection_context_files_openable(payload, target_root=target_root)
        missing_reason = "requested_target_not_found_in_current_artifacts"
    _set_inspection_edit_authority(payload, grounded=grounded, kind=kind)
    if not grounded:
        payload["agent_action"] = "refresh_or_correct_target_before_editing"
        payload["grounding_failure"] = missing_reason
    elif not payload.get("one_shot_edit_ready"):
        payload["agent_action"] = "inspect_required_source_range_before_editing"
    return payload


@mcp.tool()
def inspect_file(
    file_path: str,
    format: str = "brief",
    target_root: str = "",
    line_start: int = 0,
    line_end: int = 0,
) -> str:
    """Inspect one file from existing artifacts and return targeted evidence."""
    try:
        raw_dir = _raw_dir_for_target(target_root)
        reports_dir = _reports_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("inspect_file", target_root)
    payload = _sqlite_file_inspection_payload(raw_dir, file_path, target_root=target_root)
    if payload is None:
        payload = write_inspection("file", file_path, raw_dir=raw_dir, reports_dir=reports_dir)
    payload["analysis_root"] = _analysis_root_display(target_root)
    _add_agent_inspection_path_fields(payload)
    _add_inspection_grounding_status(payload, raw_dir, file_path, "file", target_root=target_root)
    if _safe_positive_int(line_start):
        status = payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}
        payload["target_path_status"] = _narrow_status_to_line_range(
            status,
            raw_dir,
            line_start=line_start,
            line_end=line_end,
        )
        _set_inspection_edit_authority(
            payload,
            grounded=bool(payload.get("source_grounded_for_inspection")),
            kind="file",
        )
        if payload.get("one_shot_edit_ready"):
            payload.pop("agent_action", None)
    requested_format = str(format or "brief").strip().lower()
    if requested_format in {"brief", "md", "markdown"}:
        return render_agent_inspection_brief(payload)
    if requested_format in {"brief_debug", "debug"}:
        return render_agent_inspection_brief(payload, debug=True)
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def inspect_folder(folder_path: str, format: str = "brief", target_root: str = "") -> str:
    """Inspect one folder from existing artifacts and return targeted evidence."""
    try:
        raw_dir = _raw_dir_for_target(target_root)
        reports_dir = _reports_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("inspect_folder", target_root)
    payload = write_inspection("folder", folder_path, raw_dir=raw_dir, reports_dir=reports_dir)
    payload["analysis_root"] = _analysis_root_display(target_root)
    _add_agent_inspection_path_fields(payload)
    _add_inspection_grounding_status(payload, raw_dir, folder_path, "folder", target_root=target_root)
    requested_format = str(format or "brief").strip().lower()
    if requested_format in {"brief", "md", "markdown"}:
        return render_agent_inspection_brief(payload)
    if requested_format in {"brief_debug", "debug"}:
        return render_agent_inspection_brief(payload, debug=True)
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def inspect_symbol(symbol: str, format: str = "brief", target_root: str = "", project: str = "MAIN") -> str:
    """Inspect one symbol/component/function from existing artifacts and return targeted evidence.

    Defaults to MAIN so target-repository coding agents do not treat variations
    as edit targets. Use project="*" / "all" or a scoped PROJECT::Symbol value
    for merge/variation review.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
        reports_dir = _reports_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("inspect_symbol", target_root)
    requested_project = str(project or "").strip()
    target_symbol = str(symbol or "").strip()
    if "::" not in target_symbol and requested_project and requested_project.lower() not in {"*", "all", "any"}:
        target_symbol = f"{requested_project}::{target_symbol}"
    payload = _sqlite_symbol_inspection_payload(raw_dir, target_symbol, target_root=target_root)
    if payload is None:
        payload = write_inspection("symbol", target_symbol, raw_dir=raw_dir, reports_dir=reports_dir)
    payload["requested_project_scope"] = requested_project or "all"
    payload["analysis_root"] = _analysis_root_display(target_root)
    _add_agent_inspection_path_fields(payload)
    _add_inspection_grounding_status(payload, raw_dir, target_symbol, "symbol", target_root=target_root)
    requested_format = str(format or "brief").strip().lower()
    if requested_format in {"brief", "md", "markdown"}:
        return render_agent_inspection_brief(payload)
    if requested_format in {"brief_debug", "debug"}:
        return render_agent_inspection_brief(payload, debug=True)
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def get_release_proof(human_report: bool = False) -> str:
    """Return SAGE self-release proof evidence for auditors/developers, not target-repository release approval."""
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "release_proof_bundle.md", "Release proof report not found.")
    return _read_json_artifact(RAW_DIR / "release_proof_bundle.json", "Release proof bundle artifact not found.")


@mcp.tool()
def get_react_universal_readiness(human_report: bool = False) -> str:
    """Return React ecosystem universal readiness evidence."""
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "react_universal_readiness.md", "React universal readiness report not found.")
    return _read_json_artifact(RAW_DIR / "react_universal_readiness.json", "React universal readiness artifact not found.")


@mcp.tool()
def get_report_freshness(human_report: bool = False) -> str:
    """Return SAGE report freshness evidence for readiness/debug review, not target-repository code context."""
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "report_freshness_index.md", "Report freshness report not found.")
    return _read_json_artifact(RAW_DIR / "report_freshness_index.json", "Report freshness artifact not found.")


@mcp.tool()
def get_sage_learning_system(
    human_report: bool = False,
    changed_files: str = "",
    failure_families: str = "",
) -> str:
    """Return SAGE developer/auditor learning-loop state: lessons, debt registry, audit progress and validator commands."""
    started = time.perf_counter()
    changed = [item.strip() for item in changed_files.replace("\n", ",").split(",") if item.strip()]
    families = [item.strip() for item in failure_families.replace("\n", ",").split(",") if item.strip()]
    payload = _build_sage_learning_system_payload(changed, families)
    projection = payload.get("semantic_diff_impacted_lessons", {})
    scope = projection.get("scope", {}) if isinstance(projection, dict) else {}
    bootstrap_status = (
        "PASS"
        if payload.get("summary", {}).get("development_loop_contract_status") == "ok"
        and payload.get("summary", {}).get("active_work_package_status") == "ok"
        and scope.get("status") == "ready"
        else "FAIL"
    )
    payload["bootstrap_receipt"] = record_work_package_operation_safely(
        operation_id="sage_learning_system_bootstrap",
        result_status=bootstrap_status,
        started=started,
    )
    if human_report:
        return _record_mcp_call_result(
            "get_sage_learning_system",
            started,
            _render_sage_learning_system_report(payload),
            status=bootstrap_status.lower(),
        )
    return _record_mcp_call_result(
        "get_sage_learning_system",
        started,
        json.dumps(payload, indent=2, ensure_ascii=False),
        status=bootstrap_status.lower(),
    )


@mcp.tool()
def get_surface_inventory(human_report: bool = False, regenerate: bool = False) -> str:
    """Return the Nexora CLI/MCP/artifact capability inventory."""
    if regenerate:
        run_surface_inventory()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "nexora_surface_inventory.md", "Surface inventory report not found.")
    return _read_json_artifact(RAW_DIR / "nexora_surface_inventory.json", "Surface inventory artifact not found.")


@mcp.tool()
def get_capability_registry(human_report: bool = False, regenerate: bool = False) -> str:
    """Return the machine-readable capability/plugin registry and claim boundaries."""
    if regenerate or not (RAW_DIR / "capability_registry.json").exists():
        run_capability_registry_report()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "capability_registry.md", "Capability registry report not found.")
    return _read_json_artifact(RAW_DIR / "capability_registry.json", "Capability registry artifact not found.")


@mcp.tool()
def get_capability_contract(capability_id: str = "", artifact: str = "") -> str:
    """
    Return the operational capability contract for an AI agent.
    Use capability_id to inspect one capability, or artifact to find which capability owns/trusts an artifact.
    """
    registry = load_capability_registry()
    if capability_id:
        capability = get_capability(registry, capability_id)
        if not capability:
            return json.dumps(
                {
                    "status": "not_found",
                    "capability_id": capability_id,
                    "available_capabilities": build_agent_capability_map(registry).get("summary", {}),
                },
                indent=2,
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "status": "found",
                "source": "config/capability_registry.json",
                "capability": capability,
                "agent_guidance": {
                    "artifacts_to_trust": capability.get("artifacts", []),
                    "validators_to_run": capability.get("validators", []),
                    "claim_boundary": capability.get("claim_boundary"),
                    "language_scope": capability.get("language_scope", []),
                    "framework_scope": capability.get("framework_scope", []),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    if artifact:
        matches = capabilities_for_artifact(registry, artifact)
        return json.dumps(
            {
                "status": "found" if matches else "not_found",
                "artifact": artifact,
                "source": "config/capability_registry.json",
                "capabilities": matches,
                "agent_guidance": [
                    {
                        "capability_id": item.get("id"),
                        "validators_to_run": item.get("validators", []),
                        "claim_boundary": item.get("claim_boundary"),
                    }
                    for item in matches
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    return json.dumps(build_agent_capability_map(registry), indent=2, ensure_ascii=False)


@mcp.tool()
def get_capability_activation_plan(refresh: bool = False, human_report: bool = False) -> str:
    """Return the planning-only capability activation projection for the current project DNA."""
    if refresh or not (RAW_DIR / "capability_activation_plan.json").exists():
        run_capability_activation_plan()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "capability_activation_plan.md", "Capability activation plan report not found.")
    return _read_json_artifact(RAW_DIR / "capability_activation_plan.json", "Capability activation plan artifact not found.")


@mcp.tool()
def get_pipeline_execution_contract(refresh: bool = False, human_report: bool = False) -> str:
    """Return pipeline scheduling semantics: DAG-safe, sequential-required, full-run-only and SQLite writer classes."""
    if refresh or not (RAW_DIR / "pipeline_execution_contract_validation.json").exists():
        validate_pipeline_execution_contract()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "pipeline_execution_contract_validation.md", "Pipeline execution contract report not found.")
    return _read_json_artifact(RAW_DIR / "pipeline_execution_contract_validation.json", "Pipeline execution contract artifact not found.")


@mcp.tool()
def get_pipeline_step_invocation(step: str = "", refresh: bool = False) -> str:
    """Return the canonical command and artifact contract for one pipeline step."""
    if refresh or not (RAW_DIR / "pipeline_step_registry.json").exists():
        _run_cli("run", "--list-steps")
    registry = _load_json(RAW_DIR / "pipeline_step_registry.json") or {}
    steps = registry.get("steps", []) if isinstance(registry, dict) else []
    steps = [item for item in steps if isinstance(item, dict)]

    if not str(step or "").strip():
        return json.dumps(
            {
                "status": "list_steps",
                "tool": "get_pipeline_step_invocation",
                "available_steps": [
                    {
                        "name": item.get("name"),
                        "slug": item.get("slug"),
                        "category": item.get("category"),
                        "heavy": item.get("heavy"),
                        "full_only": item.get("full_only"),
                    }
                    for item in steps
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

    query = re.sub(r"[^a-z0-9]+", "", str(step or "").lower())
    matches = [
        item for item in steps
        if query in {
            re.sub(r"[^a-z0-9]+", "", str(item.get("name") or "").lower()),
            re.sub(r"[^a-z0-9]+", "", str(item.get("slug") or "").lower()),
        }
    ]
    if not matches:
        partial = [
            item for item in steps
            if query and query in re.sub(r"[^a-z0-9]+", "", str(item.get("name") or "").lower())
        ][:10]
        return json.dumps(
            {
                "status": "not_found",
                "query": step,
                "suggestions": [{"name": item.get("name"), "slug": item.get("slug")} for item in partial],
            },
            indent=2,
            ensure_ascii=False,
        )

    selected = matches[0]
    contract = selected.get("invocation_contract") if isinstance(selected.get("invocation_contract"), dict) else {}
    execution = selected.get("execution_contract") if isinstance(selected.get("execution_contract"), dict) else {}
    return json.dumps(
        {
            "status": "found",
            "step": {
                "name": selected.get("name"),
                "slug": selected.get("slug"),
                "category": selected.get("category"),
                "depends_on": selected.get("depends_on", []),
                "heavy": selected.get("heavy"),
                "full_only": selected.get("full_only"),
            },
            "invocation": contract,
            "execution": {
                "scheduler_class": execution.get("scheduler_class"),
                "parallel_safe_after_dependencies": execution.get("parallel_safe_after_dependencies"),
                "sqlite_writer": execution.get("sqlite_writer"),
                "reasons": execution.get("reasons", []),
            },
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
def get_engine_signal_contracts(refresh: bool = False, human_report: bool = False) -> str:
    """Return engine signal/evidence semantics: signals, calibrated evidence, verdicts, action plans and context packets."""
    if refresh or not (RAW_DIR / "engine_signal_contract_validation.json").exists():
        validate_engine_signal_contracts()
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "engine_signal_contract_validation.md", "Engine signal contract report not found.")
    return _read_json_artifact(RAW_DIR / "engine_signal_contract_validation.json", "Engine signal contract artifact not found.")


@mcp.tool()
def get_artifact_provenance(human_report: bool = False) -> str:
    """Return SAGE artifact provenance evidence for auditors/debug review, not target-repository code context."""
    if human_report:
        return _read_text_artifact(REPORTS_DIR / "artifact_provenance_index.md", "Artifact provenance report not found.")
    return _read_json_artifact(RAW_DIR / "artifact_provenance_index.json", "Artifact provenance artifact not found.")


@mcp.tool()
def external_target_preflight(target_root: str) -> str:
    """Validate an external target folder before asking Nexora to analyze it."""
    target = _valid_external_target(target_root)
    if target is None:
        return _invalid_external_target_brief("external_target_preflight", target_root)
    return _run_python_script(TOOLS_DIR / "external_target_preflight.py", str(target))


@mcp.tool()
def run_external_target_analysis(
    target_root: str,
    full: bool = True,
    refresh: bool = False,
    skip_preflight: bool = False,
    include_brief: bool = True,
    format: str = "brief",
    profile_id: str = "",
) -> str:
    """Run Nexora against an external target folder with isolated output.

    The default full=True uses the existing AI Context dependency closure so
    Quality Gate, ContextOS and live-surface packet inputs are materialized in
    one isolated generation. Use full=False only as a quick quality-gate
    refresh when no surgical brief is required. Use refresh=True when
    stale/drifted evidence must bypass cache reuse without escalating to
    release-deep claim semantics.
    """
    target = _valid_external_target(target_root)
    if target is None:
        return _invalid_external_target_brief("run_external_target_analysis", target_root)
    resolved_profile = str(profile_id or "").strip()
    if resolved_profile not in {"", SAGE_SELF_REALITY_PROFILE}:
        return json.dumps(
            {
                "status": "FAIL",
                "reason": "unsupported_reality_profile",
                "profile_id": resolved_profile,
            },
            indent=2,
            ensure_ascii=False,
        )
    try:
        execution_identity = _resolve_mcp_execution_identity(
            target_root=str(target),
            reality_profile=resolved_profile,
        )
    except ValueError as exc:
        return json.dumps(
            {
                "status": "FAIL",
                "reason": "execution_identity_rejected",
                "detail": str(exc),
                "target_root": str(target),
                "profile_id": resolved_profile,
            },
            indent=2,
            ensure_ascii=False,
        )
    env_overrides = None
    if resolved_profile == SAGE_SELF_REALITY_PROFILE:
        env_overrides = {
            SAGE_ACTOR_PROFILE_ENV: SAGE_OPERATOR_ACTOR_PROFILE,
            SAGE_REALITY_TARGET_PROFILE_ENV: SAGE_SELF_REALITY_PROFILE,
        }
    args = ["run", "--target-root", str(target)]
    if full:
        args.append("--full")
        if include_brief:
            args.extend(["--step", "AI Context Generator", "--ai-context"])
    else:
        args.extend(["--step", "qualitygates"])
    if skip_preflight:
        args.append("--skip-target-preflight")
    if refresh:
        args.append("--refresh")
    output = _run_cli(*args, env_overrides=env_overrides)
    analysis_succeeded = _script_succeeded(output)
    context_closure_output = ""
    context_closure_succeeded = True
    context_closure_mode = "not_requested"
    surgical_packet = None
    surgical_packet_succeeded = not include_brief
    if include_brief:
        surgical_packet = get_surgical_operation_packet(
            max_signals=8,
            format=format,
            target_root=str(target),
        )
        surgical_packet_succeeded = is_successful_surgical_packet({"body": surgical_packet})
    if full and include_brief and analysis_succeeded and surgical_packet_succeeded:
        context_closure_mode = "reused_current_primary_full_run"
        context_closure_output = (
            "Primary full run already produced a current surgical packet; "
            "no additional Quality Gates recovery was executed."
        )
    elif full and include_brief and analysis_succeeded:
        context_closure_mode = "agent_context_recovery"
        closure_args = list(args)
        if "--refresh" not in closure_args:
            closure_args.append("--refresh")
        context_closure_output = _run_cli(*closure_args, env_overrides=env_overrides)
        context_closure_succeeded = _script_succeeded(context_closure_output)
        if context_closure_succeeded:
            surgical_packet = get_surgical_operation_packet(
                max_signals=8,
                format=format,
                target_root=str(target),
            )
            surgical_packet_succeeded = is_successful_surgical_packet(
                {"body": surgical_packet}
            )
    index_output = _run_python_script(TOOLS_DIR / "generate_external_target_index.py")
    index_succeeded = _script_succeeded(index_output)
    operation_succeeded = (
        analysis_succeeded
        and context_closure_succeeded
        and index_succeeded
        and surgical_packet_succeeded
    )
    return json.dumps(
        {
            "status": "PASS" if operation_succeeded else "FAIL",
            "target_root": str(target),
            "execution_identity": execution_identity,
            "profile_id": resolved_profile,
            "analysis_succeeded": analysis_succeeded,
            "context_closure_requested": bool(full and include_brief),
            "context_closure_succeeded": context_closure_succeeded,
            "context_closure_mode": context_closure_mode,
            "external_target_index_succeeded": index_succeeded,
            "surgical_packet_succeeded": surgical_packet_succeeded,
            "command_output": output,
            "context_closure_output": context_closure_output,
            "external_target_index_output": index_output,
            "index_report": _read_text_artifact(REPORTS_DIR / "external_target_runs.md", ""),
            "surgical_packet": surgical_packet,
            "required_action": (
                ""
                if operation_succeeded
                else "Resolve the failed analysis, context closure, index or surgical-packet stage before using target-repository claims."
            ),
            "claim_boundary": (
                "PASS proves command completion and isolated output indexing; each downstream claim still requires its own current evidence."
                if operation_succeeded
                else "Returned command output is diagnostic evidence only; the target repository is not successfully analyzed."
            ),
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
def get_surgical_context(symbol: str, project: str = "", target_root: str = "", format: str = "brief") -> str:
    """Return a concise mission briefing for a symbol using atlas metadata."""
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_surgical_context", target_root)
    if not (raw_dir / "atlas.json").exists():
        return _missing_target_artifact_brief("get_surgical_context", symbol, target_root, ["atlas.json"])
    requested_format = str(format or "brief").lower()
    project_filter = project or "MAIN"
    matches = _find_symbol_matches(symbol, project=project_filter, raw_dir=raw_dir)
    if matches:
        exact_matches = [row for row in matches if str(row.get("name") or "") == str(symbol or "")]
        candidates = exact_matches or matches
        if len(candidates) > 1:
            search_policy = _symbol_search_policy()
            ambiguity_preview_items = int(search_policy["ambiguity_preview_items"])
            search_truncated = any(bool(row.get("search_truncated")) for row in candidates)
            candidate_rows = [
                {
                    "symbol": row.get("name") or symbol,
                    "project": row.get("project") or row.get("project_key") or project_filter,
                    "target_file": row.get("repo_relative_path") or row.get("file"),
                    "target_ref": (
                        f"{row.get('project') or row.get('project_key') or project_filter}::{row.get('repo_relative_path') or row.get('file')}"
                        if (row.get("project") or row.get("project_key") or project_filter)
                        and (row.get("repo_relative_path") or row.get("file"))
                        else row.get("repo_relative_path") or row.get("file")
                    ),
                    "target_status": "indexed_symbol_candidate",
                    "type": row.get("type"),
                    "line": row.get("line"),
                    "source": "sqlite_symbols",
                }
                for row in candidates[:ambiguity_preview_items]
            ]
            ambiguity_payload = {
                "status": "ambiguous_symbol",
                "symbol": symbol,
                "project": project_filter,
                "candidate_count": len(candidates),
                "candidate_count_semantics": "lower_bound" if search_truncated else "complete_match_set",
                "search_truncated": search_truncated,
                "candidates": candidate_rows,
                "next_action": "Inspect the intended candidate file before requesting edit context; symbol names alone are not unique identities.",
            }
            if requested_format in {"json", "machine"}:
                return json.dumps(ambiguity_payload, indent=2, ensure_ascii=False)
            return _render_supporting_context_brief(
                "Surgical Context Brief",
                {
                    "analysis_root": _analysis_root_display(target_root),
                    "surface": "surgical_context",
                    "filter": symbol,
                    "status": "ambiguous_symbol",
                    "items": candidate_rows,
                    "directive": ambiguity_payload["next_action"],
                },
            )
        symbol_info = candidates[0]
        if requested_format in {"json", "machine"}:
            return json.dumps({"briefing": symbol_info, "source": "sqlite_symbols"}, indent=2, ensure_ascii=False)
        return _render_supporting_context_brief(
            "Surgical Context Brief",
            {
                "analysis_root": _analysis_root_display(target_root),
                "surface": "surgical_context",
                "filter": symbol,
                "status": "matched_by_search",
                "source": "sqlite_symbols",
                "items": [
                    (
                        lambda target_context: {
                            "symbol": symbol_info.get("name") or symbol,
                            "target_file": target_context.get("target_file") or symbol_info.get("repo_relative_path") or symbol_info.get("file"),
                            "target_ref": target_context.get("target_ref") or symbol_info.get("target_ref") or "",
                            "target_status": target_context.get("target_status") or {},
                            "type": symbol_info.get("type"),
                            "dependencies": symbol_info.get("dependencies"),
                        }
                    )(
                        _target_context_from_project_file(
                            raw_dir,
                            str(symbol_info.get("project") or symbol_info.get("project_key") or project_filter),
                            symbol_info.get("repo_relative_path") or symbol_info.get("file"),
                            target_root=target_root,
                        )
                    )
                ],
            },
        )
    if requested_format in {"json", "machine"}:
        return json.dumps({"status": "not_found", "symbol": symbol, "project": project_filter}, indent=2, ensure_ascii=False)
    return _render_supporting_context_brief(
        "Surgical Context Brief",
        {"surface": "surgical_context", "filter": symbol, "status": "not_found", "items": []},
    )


@mcp.tool()
def get_dead_code(path: str = "", target_root: str = "", format: str = "brief", max_items: int = 50) -> str:
    """
    Return bounded dead-code candidates for target-repository cleanup.
    Use after a concrete path/symbol exists; default brief is agent-facing and target_root-isolated.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_dead_code", target_root)
    data, missing = _artifact_or_missing(raw_dir, "dead_code.json", "get_dead_code", path, target_root)
    if missing:
        return missing
    items = data.get("items", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    needle = path.lower().strip()
    filtered = []
    for item in items:
        if needle and isinstance(item, dict):
            project = str(item.get("project") or "").strip()
            source_file = item.get("file") or item.get("path")
            target_context = _target_context_from_project_file(raw_dir, project, source_file, target_root=target_root)
            searchable = " ".join(
                [
                    str(source_file or ""),
                    str(target_context.get("target_file") or ""),
                    str(target_context.get("target_ref") or ""),
                    str(item),
                ]
            ).lower()
            if needle not in searchable:
                continue
        elif needle and needle not in str(item).lower():
            continue
        filtered.append(item)
    filtered = [
        item
        for item in filtered
        if _default_agent_scope_allows(item, target_root=target_root, explicit_filter=path)
    ]
    bounded = filtered[: max(1, int(max_items or 50))]
    if str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(bounded, indent=2, ensure_ascii=False)
    compact = []
    for item in bounded:
        if isinstance(item, dict):
            project = str(item.get("project") or "").strip()
            source_file = item.get("file") or item.get("path")
            target_context = _target_context_from_project_file(raw_dir, project, source_file, target_root=target_root)
            compact.append(
                {
                    "file": source_file,
                    "target_file": target_context.get("target_file") or source_file,
                    "target_ref": target_context.get("target_ref") or item.get("scoped_file") or "",
                    "target_status": target_context.get("target_status") or {},
                    "symbol": item.get("symbol") or item.get("name"),
                    "kind": item.get("kind") or item.get("type"),
                    "confidence": item.get("confidence") or item.get("certainty"),
                    "reason": item.get("reason") or item.get("rationale") or item.get("status"),
                    "actionability": dict(item.get("actionability")) if isinstance(item.get("actionability"), dict) else {},
                    "evidence": item.get("evidence") if isinstance(item.get("evidence"), dict) else {},
                }
            )
        else:
            compact.append(str(item))
    return _render_supporting_context_brief(
        "Dead Code Context Brief",
        {
            "analysis_root": _analysis_root_display(target_root),
            "surface": "dead_code",
            "filter": path,
            "status": "ok" if compact else "no_actionable_items",
            "items": compact,
        },
    )


@mcp.tool()
def find_clones(symbol: str, target_root: str = "", format: str = "brief", max_items: int = 10) -> str:
    """
    Return bounded clone/duplication evidence for one target symbol.
    Use for refactor review only; default brief preserves public-contract cautions and target_root isolation.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("find_clones", target_root)
    data, missing = _artifact_or_missing(raw_dir, "clone_detector.json", "find_clones", symbol, target_root)
    if missing:
        return missing
    matches = [
        cluster
        for cluster in (data.get("clusters") or [])
        if any(symbol.lower() in str(instance.get("id", "")).lower() for instance in cluster.get("instances", []))
    ]
    matches = [
        cluster
        for cluster in matches
        if _default_agent_scope_allows(cluster, target_root=target_root, explicit_filter=symbol)
    ]
    bounded = matches[: max(1, int(max_items or 10))]
    if str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(bounded, indent=2, ensure_ascii=False)

    def _clone_instance_target(instance: dict[str, Any]) -> dict[str, Any]:
        raw_file = str(instance.get("file") or "").strip()
        raw_id = str(instance.get("id") or "").strip()
        scoped = raw_file if "::" in raw_file else raw_id
        if "::" in scoped:
            parts = scoped.split("::")
            target_ref = "::".join(parts[:2]) if len(parts) >= 2 else scoped
            targets = _scoped_key_targets(raw_dir, [target_ref], target_root=target_root)
            target = targets[0] if targets else {}
            return {
                "symbol": instance.get("symbol") or instance.get("name") or (parts[2] if len(parts) > 2 else ""),
                "target_file": target.get("target_file") or (parts[1] if len(parts) > 1 else raw_file),
                "target_ref": target.get("target_ref") or target_ref,
                "target_status": {
                    "exists": bool(target.get("exists")),
                    "indexed": bool(target.get("indexed")),
                },
            }
        status = _target_path_status(raw_dir, raw_file, target_root=target_root) if raw_file else {}
        return {
            "symbol": instance.get("symbol") or instance.get("name") or "",
            "target_file": status.get("target_file") or raw_file,
            "target_ref": status.get("target_ref") or raw_file,
            "target_status": {
                "exists": bool(status.get("exists")),
                "indexed": bool(status.get("indexed")),
            },
        }

    compact = []
    for cluster in bounded:
        if not isinstance(cluster, dict):
            compact.append(str(cluster))
            continue
        compact.append(
            {
                "cluster_id": cluster.get("id") or cluster.get("cluster_id"),
                "instance_count": len(cluster.get("instances") or []),
                "instance_targets": [
                    _clone_instance_target(instance)
                    for instance in (cluster.get("instances") or [])[:5]
                    if isinstance(instance, dict)
                ],
                "instance_targets_omitted": max(0, len(cluster.get("instances") or []) - 5),
            }
        )
    return _render_supporting_context_brief(
        "Clone Context Brief",
        {
            "analysis_root": _analysis_root_display(target_root),
            "surface": "clone_detector",
            "filter": symbol,
            "status": "ok",
            "items": compact,
        },
    )


@mcp.tool()
def get_health_metrics(path: str = "", target_root: str = "", format: str = "brief") -> str:
    """Read health and risk summaries for a project, module, or universal category (@root, @shared, @platform)."""
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_health_metrics", target_root)
    health, missing = _artifact_or_missing(raw_dir, "health_score.json", "get_health_metrics", path, target_root)
    if missing:
        return missing
    risk, missing = _artifact_or_missing(raw_dir, "module_risk_matrix.json", "get_health_metrics", path, target_root)
    if missing:
        return missing

    if isinstance(health, dict):
        modules = health.get("modules") or {}
        project_details = health.get("project_details") or {}
        overall_health = health.get("overall", {})
    else:
        modules = {}
        project_details = {}
        overall_health = {}
    module_health = modules.get(path) if isinstance(modules, dict) else {}
    resolution_status = "module_match" if module_health else ""
    if not module_health and isinstance(project_details, dict) and path in project_details:
        module_health = project_details.get(path) or {}
        resolution_status = "project_match"
    if not module_health:
        module_health = overall_health
        resolution_status = "overall_fallback" if path else "overall"

    risk_rows = risk.get("rows", []) if isinstance(risk, dict) else []
    module_risk = next(
        (item for item in risk_rows if isinstance(item, dict) and path.lower() in str(item.get("module", "")).lower()),
        {},
    )
    payload = {
        "path": path,
        "health": module_health,
        "risk": module_risk,
        "resolution_status": resolution_status,
        "resolution_note": "Requested path was not found; health is the overall fallback, not module-specific." if resolution_status == "overall_fallback" else "",
    }
    if str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return _render_supporting_context_brief(
        "Target Repository Health Metrics Brief",
        {
            "surface": "health_metrics",
            "analysis_root": _analysis_root_display(target_root),
            "filter": path,
            "status": "fallback" if resolution_status == "overall_fallback" else "ok",
            "items": [
                {
                    "path": path or "<overall>",
                    "health": module_health,
                    "risk": module_risk,
                    "resolution_status": resolution_status,
                    "resolution_note": "Requested path was not found; health is the overall fallback, not module-specific." if resolution_status == "overall_fallback" else "",
                }
            ],
        },
    )


@mcp.tool()
def get_circular_dependencies(module: str = "", target_root: str = "", format: str = "brief", max_items: int = 20) -> str:
    """
    Return bounded circular-dependency evidence for a module or target repository.
    Use as supporting context; default brief lists actionable chain targets without applying changes.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_circular_dependencies", target_root)
    data, missing = _artifact_or_missing(raw_dir, "circular_deps.json", "get_circular_dependencies", module, target_root)
    if missing:
        return missing
    cycles = data.get("cycles") or [] if isinstance(data, dict) else []
    filtered = [cycle for cycle in cycles if not module or module.lower() in str(cycle).lower()]
    filtered = [
        cycle
        for cycle in filtered
        if _default_agent_scope_allows(cycle, target_root=target_root, explicit_filter=module)
    ]
    bounded = filtered[: max(1, int(max_items or 20))]
    if str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(bounded, indent=2, ensure_ascii=False)
    compact_cycles = []
    max_chain_targets = 4
    for cycle in bounded:
        if not isinstance(cycle, dict):
            compact_cycles.append(cycle)
            continue
        chain = [str(node) for node in (cycle.get("chain") or []) if str(node or "").strip()]
        chain_targets = _scoped_key_targets(raw_dir, chain[:max_chain_targets], target_root=target_root)
        compact_cycles.append(
            {
                "chain_sample": chain[:max_chain_targets],
                "chain_targets": chain_targets,
                "chain_targets_omitted": max(0, len(chain) - len(chain_targets)),
                "length": cycle.get("length") or len(chain),
            }
        )
    return _render_supporting_context_brief(
        "Circular Dependency Brief",
        {
            "analysis_root": _analysis_root_display(target_root),
            "surface": "circular_dependencies",
            "filter": module,
            "status": "ok" if compact_cycles else "no_actionable_items",
            "items": compact_cycles,
        },
    )


@mcp.tool()
def get_blast_radius(symbol: str, target_root: str = "", format: str = "brief", max_items: int = 10) -> str:
    """
    Return existing blast-radius artifact evidence for one symbol, canonical target ref, or repository-relative file.
    Use as supporting context; call get_impact_radius when a concrete depth-limited dependent list is needed.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_blast_radius", target_root)
    data, missing = _artifact_or_missing(raw_dir, "blast_radius.json", "get_blast_radius", symbol, target_root)
    if missing:
        return missing
    impact = data.get(symbol) or data.get(symbol.lower()) if isinstance(data, dict) else None
    resolved_node, resolved_context = _resolve_target_node_from_raw(raw_dir, symbol)
    resolved_target_ref = _target_ref_from_context(resolved_node, resolved_context)
    exact_keys = {
        str(value or "").replace("\\", "/").strip("/").lower()
        for value in (symbol, resolved_node, resolved_target_ref)
        if str(value or "").strip()
    }
    query_mode = "legacy_mapping"
    if impact:
        items = impact if isinstance(impact, list) else [impact]
    else:
        rows = data.get("blast_radius") or [] if isinstance(data, dict) else []
        exact_items = [
            item
            for item in rows
            if (
                str(item.get("file") or "").replace("\\", "/").strip("/").lower()
                in exact_keys
                if isinstance(item, dict)
                else False
            )
        ]
        if exact_items:
            items = exact_items
            query_mode = "canonical_target"
        else:
            items = [
                item
                for item in rows
                if symbol.lower() in str(item.get("file", item) if isinstance(item, dict) else item).lower()
            ]
            query_mode = "symbol_or_substring"
    items = [
        item
        for item in items
        if _default_agent_scope_allows(item, target_root=target_root, explicit_filter=symbol)
    ]
    bounded = items[: max(1, int(max_items or 10))]
    result_status = "ok" if bounded else "no_actionable_items"
    if str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(
            {
                "status": result_status,
                "analysis_root": _analysis_root_display(target_root),
                "filter": symbol,
                "query_mode": query_mode,
                "resolved_target": resolved_node,
                "resolved_target_ref": resolved_target_ref,
                "items": bounded,
                "required_follow_up": (
                    f"get_impact_radius(target_node={resolved_target_ref!r}, depth=2)"
                    if bounded and resolved_target_ref
                    else "Inspect or refresh a concrete target before requesting impact radius."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    compact_items: list[dict[str, Any]] = []
    for item in bounded:
        if not isinstance(item, dict):
            compact_items.append({"value": str(item)})
            continue
        ref = str(item.get("file") or item.get("target_ref") or item.get("key") or "")
        targets = _scoped_key_targets(raw_dir, [ref], target_root=target_root) if ref else []
        target_context = targets[0] if targets else {"target_file": ref, "target_ref": ref, "exists": False, "indexed": False}
        imported_contracts = item.get("imported_contracts") if isinstance(item.get("imported_contracts"), list) else []
        member_dependencies = item.get("member_dependencies") if isinstance(item.get("member_dependencies"), list) else []
        compact_items.append(
            {
                "target_file": target_context.get("target_file") or "",
                "target_ref": target_context.get("target_ref") or ref,
                "target_status": {
                    "exists": bool(target_context.get("exists")),
                    "indexed": bool(target_context.get("indexed")),
                },
                "direct_dependents": item.get("direct_dependents"),
                "transitive_dependents": item.get("transitive_dependents"),
                "total_impact_score": item.get("total_impact_score"),
                "imported_contracts_sample": imported_contracts[:8],
                "imported_contracts_omitted": max(0, len(imported_contracts) - 8),
                "member_dependencies_sample": member_dependencies[:8],
                "member_dependencies_omitted": max(0, len(member_dependencies) - 8),
            }
        )
    return _render_supporting_context_brief(
        "Blast Radius Brief",
        {
            "analysis_root": _analysis_root_display(target_root),
            "surface": "blast_radius",
            "filter": symbol,
            "status": result_status,
            "query_mode": query_mode,
            "resolved_target": resolved_node,
            "resolved_target_ref": resolved_target_ref,
            "items": compact_items,
        },
    )


def _dict_sample_keys(value: Any, *, project: str = "", limit: int = 20) -> list[str]:
    if not isinstance(value, dict):
        return []
    keys = [str(key) for key in value.keys()]
    if project:
        keys = [key for key in keys if key.startswith(f"{project}::")]
    return keys[: max(1, int(limit or 20))]


def _scoped_key_targets(raw_dir: Path, keys: list[str], target_root: str = "") -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for key in keys:
        text = str(key or "").strip()
        if not text:
            continue
        target_file = _target_file_from_ref(raw_dir, text)
        target_ref = _target_ref_from_node(raw_dir, text) if "::" in text else ""
        status = _target_path_status(raw_dir, target_file, target_root=target_root) if target_file else {}
        targets.append(
            {
                "target_file": target_file,
                "target_ref": target_ref or status.get("target_ref") or text,
                "exists": bool(status.get("exists")) if status else False,
                "indexed": bool(status.get("indexed")) if status else False,
            }
        )
    return targets


def _target_context_from_project_file(raw_dir: Path, project: str, file_path: Any, target_root: str = "") -> dict[str, Any]:
    rel = str(file_path or "").replace("\\", "/").strip("/")
    if not project or not rel:
        return {"target_file": rel, "target_ref": "", "target_status": {}}
    node = f"{project}::{rel}"
    target_file = _target_file_from_ref(raw_dir, node)
    target_ref = _target_ref_from_node(raw_dir, node)
    status = _target_path_status(raw_dir, target_file, target_root=target_root) if target_file else {}
    return {
        "target_file": target_file,
        "target_ref": target_ref,
        "target_status": {
            "exists": bool(status.get("exists")),
            "inside_root": bool(status.get("inside_root")),
            "indexed": bool(status.get("indexed")),
        },
    }


def _is_variation_workspace_text(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").lower()
    return any(marker in text for marker in ("/variations/", "variations/", "::variations/", "/companions/", "companions/", "::companions/"))


def _default_agent_scope_allows(value: Any, *, target_root: str = "", explicit_filter: str = "") -> bool:
    """Keep default target-repo coding surfaces on MAIN unless variation scope is explicit."""
    explicit = str(explicit_filter or "").strip()
    if _is_variation_workspace_text(explicit) or ("::" in explicit and not explicit.upper().startswith("MAIN::")):
        return True
    if isinstance(value, dict):
        for key in ("project", "source_project", "target_project", "source"):
            project = str(value.get(key) or "").strip()
            if project and project.upper() != "MAIN":
                return False
    text = str(value or "")
    scoped_projects = {match.group(1).upper() for match in re.finditer(r"\b([A-Z][A-Z0-9_]{2,})::", text)}
    if scoped_projects and scoped_projects != {"MAIN"}:
        return False
    return not _is_variation_workspace_text(text)


@mcp.tool()
def get_state_flow(project: str = "MAIN", max_items: int = 20, full: bool = False, target_root: str = "", format: str = "brief") -> str:
    """
    Return bounded state/query flow context for a project, MAIN by default.
    Use full=True or format=json only for explicit machine/debug review, not default coding context.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_state_flow", target_root)
    path = raw_dir / "state_flow.json"
    data = _load_json(path)
    if data is None:
        return _missing_target_artifact_brief("get_state_flow", project, target_root, ["state_flow.json"])
    if full or str(format or "brief").lower() in {"json", "machine"}:
        return _read_json_artifact(path, "State flow data not found.")
    data = data or {}
    project_filter = "" if str(project or "").strip() in {"*", "all", "ALL"} else str(project or "").strip()
    section_limit = max(1, int(max_items or 20))
    brief_policy = _state_flow_brief_policy()
    sample_keys_limit = int(brief_policy["sample_keys_limit"])
    sample_targets_limit = int(brief_policy["sample_targets_limit"])
    summary = {
        "mode": "bounded_summary",
        "project_filter": project_filter or None,
        "full_data_hint": "Call get_state_flow(full=True) only when a machine-readable full state graph is explicitly needed.",
        "sections": {},
    }
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                all_sample_keys = _dict_sample_keys(value, project=project_filter, limit=len(value) or 1)
                sample_keys = all_sample_keys[:sample_keys_limit]
                all_sample_targets = _scoped_key_targets(raw_dir, sample_keys, target_root=target_root)
                sample_targets = all_sample_targets[:sample_targets_limit]
                summary["sections"][key] = {
                    "count": len(value),
                    "filtered_count": len(all_sample_keys),
                    "sample_keys": sample_keys,
                    "sample_keys_returned": len(sample_keys),
                    "sample_keys_omitted": max(0, len(all_sample_keys) - len(sample_keys)),
                    "sample_targets": sample_targets,
                    "sample_targets_returned": len(sample_targets),
                    "sample_targets_omitted": max(0, len(all_sample_targets) - len(sample_targets)),
                }
            elif isinstance(value, list):
                rows = value
                if project_filter:
                    rows = [row for row in rows if project_filter.lower() in str(row).lower()]
                summary["sections"][key] = {
                    "count": len(value),
                    "filtered_count": len(rows),
                    "sample": rows[:section_limit],
                    "sample_returned": min(len(rows), section_limit),
                    "sample_omitted": max(0, len(rows) - min(len(rows), section_limit)),
                }
            else:
                summary["sections"][key] = value
    items = []
    for key, value in (summary.get("sections") or {}).items():
        items.append({"section": key, "summary": value})
    return _render_supporting_context_brief(
        "State Flow Brief",
        {
            "analysis_root": _analysis_root_display(target_root),
            "surface": "state_flow",
            "filter": project_filter,
            "status": "ok",
            "items": items[:section_limit],
        },
    )


@mcp.tool()
def get_hexagonal_bindings(target_root: str = "", format: str = "brief", max_items: int = 20) -> str:
    """
    Return bounded port/adapter binding evidence for target-repository architecture review.
    Use as supporting context; default brief is read-only and target_root-isolated.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_hexagonal_bindings", target_root)
    path = raw_dir / "hexagonal_bindings.json"
    if not path.exists():
        return _missing_target_artifact_brief("get_hexagonal_bindings", "", target_root, ["hexagonal_bindings.json"])
    data = _load_json(path) or {}
    if str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _target_from_ref(ref: Any) -> dict[str, Any]:
        targets = _scoped_key_targets(raw_dir, [str(ref or "")], target_root=target_root)
        target = targets[0] if targets else {}
        return {
            "target_file": target.get("target_file") or "",
            "target_ref": target.get("target_ref") or str(ref or ""),
            "target_status": {
                "exists": bool(target.get("exists")),
                "indexed": bool(target.get("indexed")),
            },
        }

    def _compact_bound(row: Any) -> dict[str, Any]:
        row = row if isinstance(row, dict) else {}
        adapters = row.get("adapters") if isinstance(row.get("adapters"), list) else []
        return {
            "section": "bound",
            "port": row.get("port") or "",
            "port_target": _target_from_ref(row.get("port_file")),
            "adapter_targets": [
                {
                    "name": adapter.get("name") or "",
                    **_target_from_ref(adapter.get("file")),
                }
                for adapter in adapters[:5]
                if isinstance(adapter, dict)
            ],
            "adapter_targets_omitted": max(0, len(adapters) - 5),
        }

    def _compact_unbound(row: Any) -> dict[str, Any]:
        row = row if isinstance(row, dict) else {}
        project = str(row.get("project") or "")
        file_ref = str(row.get("file") or "")
        return {
            "section": "unbound_port",
            "port": row.get("port") or "",
            "port_target": _target_from_ref(file_ref if "::" in file_ref else f"{project}::{file_ref}"),
        }

    items = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "bound" and isinstance(value, list):
                for row in value[:5]:
                    items.append(_compact_bound(row))
                    if len(items) >= max(1, int(max_items or 20)):
                        break
            elif key == "unbound_ports" and isinstance(value, list):
                for row in value[:5]:
                    items.append(_compact_unbound(row))
                    if len(items) >= max(1, int(max_items or 20)):
                        break
            else:
                items.append({"section": key, "summary": value if not isinstance(value, list) else value[:5]})
            if len(items) >= max(1, int(max_items or 20)):
                break
    return _render_supporting_context_brief(
        "Hexagonal Binding Brief",
        {
            "analysis_root": _analysis_root_display(target_root),
            "surface": "hexagonal_bindings",
            "filter": "",
            "status": "ok",
            "items": items,
        },
    )


@mcp.tool()
def get_merge_waves() -> str:
    """
    Return raw merge-wave planning evidence for SAGE debug/provenance review.
    Do not use as the default target-repository merge action surface; use get_merge_review_queue instead.
    """
    from tools.core.fractal_io import load_fractal_map_data

    data = load_fractal_map_data()
    if data is None:
        return "Fractal map not found."
    return json.dumps(data.get("merge_waves", {}), indent=2, ensure_ascii=False)


@mcp.tool()
def get_merge_review_queue(action: str = "Import With Review", max_items: int = 5, target_root: str = "", format: str = "brief") -> str:
    """
    Return a small, human-approval-gated merge review queue for target-repository agents.
    This is advisory evidence only; it must not apply merge/import mutations.
    """
    requested_format = str(format or "brief").strip().lower()
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_merge_review_queue", target_root)
    trust_summary = _ensure_agent_artifact_chain_current(raw_dir, target_root=target_root)
    if _artifact_trust_blocks_actor_context(trust_summary):
        payload = _invalid_actor_context_payload("get_merge_review_queue", trust_summary)
        payload["status"] = "INCOMPLETE_EVIDENCE"
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)
        return rendered if requested_format in {"json", "machine"} else _render_invalid_actor_context_brief(payload)

    from tools.core.target_repository_proof import build_target_repository_proof

    try:
        proof = build_target_repository_proof(
            target_root=Path(_analysis_root_display(target_root)),
            raw_dir=raw_dir,
            mode="merge",
        )
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        payload = {
            "status": "INCOMPLETE_EVIDENCE",
            "surface": "merge_review_queue",
            "analysis_root": _analysis_root_display(target_root),
            "total_candidates": 0,
            "action_filter": _normalize_merge_action_filter(action),
            "items": [],
            "policy_boundary": {
                "human_approval_required": True,
                "safe_default": "review_only",
                "mutation_allowed_by_this_packet": False,
            },
            "required_action": "Repair or regenerate the target proof contract and target analysis before reviewing merge candidates.",
            "claim_boundary": "The target proof could not be constructed; no merge candidate or approval inference is available.",
        }
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)
        return rendered if requested_format in {"json", "machine"} else _render_merge_review_queue_brief(payload)
    proof_summary = proof.get("summary") if isinstance(proof.get("summary"), dict) else {}
    proof_verdict = str(proof_summary.get("verdict") or "UNKNOWN")
    cockpit_evidence = next(
        (
            row for row in (proof.get("evidence") or [])
            if isinstance(row, dict) and row.get("artifact_id") == "merge_decision_cockpit"
        ),
        {},
    )
    blocking_evidence = [
        {
            "artifact_id": row.get("artifact_id"),
            "availability": row.get("availability"),
            "freshness": row.get("freshness"),
            "snapshot_binding": row.get("snapshot_binding"),
            "source_verdict": row.get("source_verdict"),
        }
        for row in (proof.get("evidence") or [])
        if isinstance(row, dict)
        and row.get("required") is True
        and (
            row.get("availability") != "PRESENT"
            or row.get("freshness") not in {"CURRENT", "NOT_APPLICABLE"}
            or row.get("snapshot_binding") != "BOUND"
            or row.get("source_verdict") == "BLOCKED"
        )
    ]
    proof_projection = {
        "verdict": proof_verdict,
        "analysis_snapshot_id": (proof.get("subject") or {}).get("analysis_snapshot_id"),
        "root_binding": (proof.get("subject") or {}).get("root_binding"),
        "cockpit": {
            "availability": cockpit_evidence.get("availability"),
            "freshness": cockpit_evidence.get("freshness"),
            "snapshot_binding": cockpit_evidence.get("snapshot_binding"),
            "bound_snapshot_id": cockpit_evidence.get("bound_snapshot_id"),
            "content_sha256": cockpit_evidence.get("content_sha256"),
        },
        "unknowns": (proof.get("unknowns") or [])[:20],
        "blocking_evidence": blocking_evidence,
        "human_decisions": proof.get("human_decisions") or [],
        "claim_boundary": proof.get("claim_boundary"),
    }
    if proof_verdict in {"BLOCKED", "UNKNOWN"}:
        source_failures = [
            str(row.get("artifact_id") or "")
            for row in blocking_evidence
            if row.get("source_verdict") == "BLOCKED"
        ]
        required_action = (
            "Resolve the blocking target evidence before reviewing merge candidates: "
            + ", ".join(source_failures)
            if source_failures
            else "Refresh the target analysis and producer lineage before reviewing merge candidates."
        )
        payload = {
            "status": "INCOMPLETE_EVIDENCE",
            "surface": "merge_review_queue",
            "analysis_root": _analysis_root_display(target_root),
            "total_candidates": 0,
            "action_filter": _normalize_merge_action_filter(action),
            "items": [],
            "target_proof": proof_projection,
            "policy_boundary": {
                "human_approval_required": True,
                "safe_default": "review_only",
                "mutation_allowed_by_this_packet": False,
            },
            "required_action": required_action,
            "claim_boundary": "No merge candidate is served from missing, stale, blocked or target-unbound evidence.",
        }
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)
        return rendered if requested_format in {"json", "machine"} else _render_merge_review_queue_brief(payload)

    path = raw_dir / "merge_decision_cockpit.json"
    if not path.exists():
        payload = {
            "status": "INCOMPLETE_EVIDENCE",
            "surface": "merge_review_queue",
            "analysis_root": _analysis_root_display(target_root),
            "total_candidates": 0,
            "action_filter": _normalize_merge_action_filter(action),
            "items": [],
            "target_proof": proof_projection,
            "policy_boundary": {
                "human_approval_required": True,
                "safe_default": "review_only",
                "mutation_allowed_by_this_packet": False,
            },
            "required_action": "Regenerate the target merge analysis; the cockpit artifact changed after proof evaluation.",
            "claim_boundary": "The proof-to-read race invalidated the decision-support context; no merge candidate is served.",
        }
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)
        return rendered if requested_format in {"json", "machine"} else _render_merge_review_queue_brief(payload)
    data = _load_json(path) or {}
    from tools.core.analysis_snapshot_lineage import payload_sha256

    expected_cockpit_sha256 = str(cockpit_evidence.get("content_sha256") or "")
    if not isinstance(data, dict) or not expected_cockpit_sha256 or payload_sha256(data) != expected_cockpit_sha256:
        payload = {
            "status": "INCOMPLETE_EVIDENCE",
            "surface": "merge_review_queue",
            "analysis_root": _analysis_root_display(target_root),
            "total_candidates": 0,
            "action_filter": _normalize_merge_action_filter(action),
            "items": [],
            "target_proof": proof_projection,
            "policy_boundary": {
                "human_approval_required": True,
                "safe_default": "review_only",
                "mutation_allowed_by_this_packet": False,
            },
            "required_action": "Regenerate the target merge analysis; the cockpit content changed after proof evaluation.",
            "claim_boundary": "The proof-to-read content identity changed; no merge candidate is served.",
        }
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)
        return rendered if requested_format in {"json", "machine"} else _render_merge_review_queue_brief(payload)
    all_rows = data.get("decisions") if isinstance(data, dict) else []
    all_rows = all_rows if isinstance(all_rows, list) else []
    rows = list(all_rows)
    action_filter = _normalize_merge_action_filter(action)
    if action_filter:
        rows = [row for row in rows if isinstance(row, dict) and str(row.get("action") or "") == action_filter]
    limit = max(1, min(int(max_items or 5), 20))
    items: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        source_ref = str(evidence.get("source_contract_file") or "")
        source_file = _target_file_from_ref(raw_dir, source_ref) if source_ref else ""
        target_path = posixpath.normpath(str(row.get("target_path") or "").replace("\\", "/"))
        confidence = row.get("confidence") if isinstance(row.get("confidence"), dict) else {}
        route = row.get("route") if isinstance(row.get("route"), dict) else {}
        harness_plan = evidence.get("harness_plan") if isinstance(evidence.get("harness_plan"), dict) else {}
        i18n_keys = harness_plan.get("i18n_keys") if isinstance(harness_plan.get("i18n_keys"), dict) else {}
        external_deps, external_deps_omitted = _bounded_merge_evidence(evidence.get("external_deps"))
        unresolved_internal_deps, unresolved_internal_deps_omitted = _bounded_merge_evidence(evidence.get("unresolved_internal_deps"))
        target_conflicts, target_conflicts_omitted = _bounded_merge_evidence(evidence.get("target_conflicts"))
        missing_i18n_keys, missing_i18n_keys_omitted = _bounded_merge_evidence(i18n_keys.get("missing"))
        items.append(
            {
                "candidate": row.get("candidate"),
                "action": row.get("action"),
                "confidence": f"{confidence.get('tier', 'unknown')}:{confidence.get('score', '')}",
                "source_project": row.get("source"),
                "source_file": source_file,
                "source_ref": _target_ref_from_context(*_resolve_target_node_from_raw(raw_dir, source_ref)) if source_ref else "",
                "source_file_status": _compact_merge_path_status(
                    _target_path_status(raw_dir, source_ref, target_root=target_root)
                ) if source_file else {},
                "target_path": target_path,
                "proposed_target_status": _compact_merge_path_status(
                    _target_path_status(raw_dir, target_path, target_root=target_root)
                ) if target_path else {},
                "route": route.get("smoke_path") or "",
                "closure_size": row.get("closure_size"),
                "reasons": row.get("reasons") if isinstance(row.get("reasons"), list) else [],
                "required_actions": row.get("required_actions") if isinstance(row.get("required_actions"), list) else [],
                "evidence": {
                    "external_deps": external_deps,
                    "external_deps_omitted": external_deps_omitted,
                    "unresolved_internal_deps": unresolved_internal_deps,
                    "unresolved_internal_deps_omitted": unresolved_internal_deps_omitted,
                    "target_conflicts": target_conflicts,
                    "target_conflicts_omitted": target_conflicts_omitted,
                    "missing_i18n_keys": missing_i18n_keys,
                    "missing_i18n_keys_omitted": missing_i18n_keys_omitted,
                },
                "inspect_first": [
                    path for path in [source_file, target_path] if path
                ],
            }
        )
    merge_validation = merge_review_validation_policy()
    total_candidates = len(all_rows)
    payload = {
        "status": "APPROVAL_REQUIRED" if total_candidates else "ACCEPTED",
        "surface": "merge_review_queue",
        "analysis_root": _analysis_root_display(target_root),
        "total_candidates": total_candidates,
        "action_filter": action_filter,
        "items": items,
        "target_proof": proof_projection,
        "policy_boundary": {
            "human_approval_required": True,
            "safe_default": "review_only",
            "mutation_allowed_by_this_packet": False,
        },
        "validation": merge_validation,
        "do": [
            "Inspect source_file as read-only evidence and proposed_target_path as the MAIN target candidate before making any recommendation.",
            "Use MCP follow-up calls inspect_file(target=source_ref), get_impact_radius(target_node=source_ref), and get_test_impact(target_file=source_ref) before recommending an approved merge patch.",
            "Treat Import With Review as advisory until a human approves the exact scope.",
            "After human approval, use the listed MCP validation follow-up and target-repository commands for the bounded patch.",
        ],
        "do_not": [
            "Do not edit source_file; it is variation/source evidence, not the default target-repository edit surface.",
            "Do not copy dependency packages automatically from this packet.",
            "Do not treat Import Now as permission to mutate without human approval.",
            "Do not widen merge scope beyond the listed source_file, proposed_target_path, and dependency package evidence.",
            "Do not open evidence.external_deps as filesystem paths unless another field also lists them under inspect_first.",
        ],
    }
    if requested_format in {"json", "machine"}:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return _render_merge_review_queue_brief(payload)


@mcp.tool()
def get_ui_architecture(component: str = "", max_items: int = 30, full: bool = False, target_root: str = "", format: str = "brief") -> str:
    """
    Return bounded UI architecture evidence for one component or project surface.
    Use as supporting context; use full=True or format=json only for explicit machine/debug review.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_ui_architecture", target_root)
    path = raw_dir / "ui_architecture_map.json"
    data = _load_json(path)
    if data is None:
        path = raw_dir / "ui_mapper.json"
        data = _load_json(path)
    if data is None:
        return _missing_target_artifact_brief("get_ui_architecture", component, target_root, ["ui_architecture_map.json", "ui_mapper.json"])
    data = data or {}
    if full or str(format or "brief").lower() in {"json", "machine"}:
        return json.dumps(data, indent=2, ensure_ascii=False)
    rows = []
    needle = component.lower().strip()
    for project, payload in (data or {}).items():
        if not isinstance(payload, dict):
            continue
        for section, values in payload.items():
            if not isinstance(values, list):
                continue
            for row in values:
                if not isinstance(row, dict):
                    continue
                if needle and needle not in str(row).lower():
                    continue
                if not _default_agent_scope_allows({"project": project, **row}, target_root=target_root, explicit_filter=component):
                    continue
                source_file = row.get("file") or row.get("scoped_file")
                target_context = _target_context_from_project_file(raw_dir, str(project), source_file, target_root=target_root)
                rows.append(
                    {
                        "project": project,
                        "section": section,
                        "file": source_file,
                        **target_context,
                        "name": row.get("name") or row.get("component") or row.get("symbol"),
                        "risk_tier": row.get("risk_tier"),
                        "risk_reasons": (row.get("risk_reasons") or [])[:8] if isinstance(row.get("risk_reasons"), list) else [],
                    }
                )
                if len(rows) >= max(1, int(max_items or 30)):
                    break
            if len(rows) >= max(1, int(max_items or 30)):
                break
        if len(rows) >= max(1, int(max_items or 30)):
            break
    return _render_supporting_context_brief(
        "UI Architecture Brief",
        {
            "analysis_root": _analysis_root_display(target_root),
            "surface": "ui_architecture",
            "filter": component,
            "status": "ok",
            "items": rows,
        },
    )


def _render_target_write_lease_brief(payload: dict[str, Any]) -> str:
    lease = payload.get("lease") if isinstance(payload.get("lease"), dict) else {}
    lines = [
        "---",
        "context_type: SAGE_TARGET_WRITE_LEASE",
        "---",
        "# Target Write Lease",
        f"- status: {json.dumps(payload.get('status') or 'unknown')}",
        f"- analysis_root: {json.dumps(payload.get('analysis_root') or '')}",
        f"- target_ref: {json.dumps(payload.get('target_ref') or '')}",
        f"- target_file: {json.dumps(payload.get('target_file') or '')}",
        f"- actor_id: {json.dumps(payload.get('actor_id') or '')}",
    ]
    if lease:
        lines.extend(
            [
                "## Active Lease",
                f"- holder: {json.dumps(lease.get('actor_id') or '')}",
                f"- remaining_seconds: {int(lease.get('remaining_seconds') or 0)}",
                f"- source_snapshot_hash: {json.dumps(lease.get('source_snapshot_hash') or '')}",
            ]
        )
    if payload.get("retry_after_seconds"):
        lines.append(f"- retry_after_seconds: {int(payload.get('retry_after_seconds') or 0)}")
    if payload.get("agent_instruction"):
        lines.extend(["## Agent Action", f"- {payload['agent_instruction']}"])
    lines.extend(
        [
            "## Contract Boundary",
            "- This coordinates cooperating agents that share this target analysis database.",
            "- It is not a distributed filesystem lock across disconnected SAGE installations.",
        ]
    )
    return "\n".join(lines)


def _write_lease_target_status(raw_dir: Path, target_file: str, target_root: str) -> tuple[dict[str, Any], dict[str, Any]]:
    target_status = _target_path_status(raw_dir, target_file, target_root=target_root)
    required = {
        "target_exists": bool(target_status.get("exists")),
        "target_indexed": bool(target_status.get("indexed")),
        "source_snapshot_status_ok": str(target_status.get("source_snapshot_status") or "").lower() == "ok",
        "drift_check_status_match": str(target_status.get("drift_check_status") or "").lower() == "match",
    }
    return target_status, required


@mcp.tool()
def manage_target_write_lease(
    action: str,
    target_file: str,
    actor_id: str,
    target_root: str = "",
    ttl_seconds: int = 0,
    format: str = "brief",
) -> str:
    """Acquire or release a short target-file lease around a cooperating agent's external patch attempt."""
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("manage_target_write_lease", started, result, status=status, fail_closed_reason=fail_closed_reason)

    from tools.core.target_write_lease import target_write_lease_actions

    normalized_action = str(action or "").strip().lower()
    allowed_actions = target_write_lease_actions()
    if normalized_action not in allowed_actions:
        payload = {
            "status": "invalid_action",
            "analysis_root": _analysis_root_display(target_root),
            "target_file": str(target_file or ""),
            "actor_id": str(actor_id or ""),
            "agent_instruction": f"Use one policy-declared action: {', '.join(sorted(allowed_actions)) or 'none available'}.",
        }
        rendered = json.dumps(payload, indent=2, ensure_ascii=False) if str(format).lower() in {"json", "machine"} else _render_target_write_lease_brief(payload)
        return _done(rendered, status="fail_closed", fail_closed_reason="invalid_target_write_lease_action")

    if normalized_action == "release":
        return _release_target_write_lease_for_mcp(target_file, actor_id, target_root=target_root, format=format)

    target = _valid_external_target(target_root) if target_root else None
    if target_root and target is None:
        return _done(_invalid_external_target_brief("manage_target_write_lease", target_root), status="fail_closed", fail_closed_reason="invalid_external_target")
    try:
        raw_dir = _raw_dir_for_target(target_root)
        target_status, grounding = _write_lease_target_status(raw_dir, target_file, target_root)
        payload: dict[str, Any] = {
            "analysis_root": _analysis_root_display(target_root),
            "target_ref": target_status.get("target_ref") or "",
            "target_file": target_status.get("target_file") or str(target_file or ""),
            "actor_id": str(actor_id or "").strip(),
            "target_path_status": _public_target_path_status(target_status),
            "required_source_grounding": grounding,
        }
        if not all(grounding.values()):
            payload.update(
                {
                    "status": "not_grounded",
                    "agent_instruction": "Do not edit. Refresh analysis for this target, then reacquire a lease only when indexed source snapshot grounding and drift status are current.",
                }
            )
            rendered = json.dumps(payload, indent=2, ensure_ascii=False) if str(format).lower() in {"json", "machine"} else _render_target_write_lease_brief(payload)
            return _done(rendered, status="fail_closed", fail_closed_reason="target_not_grounded")

        from tools.core.target_write_lease import acquire_target_write_lease as acquire_lease

        lease_result = acquire_lease(
            raw_dir / "codemaps.db",
            analysis_root=payload["analysis_root"],
            target_file=payload["target_file"],
            actor_id=payload["actor_id"],
            source_snapshot_hash=str(target_status.get("source_snapshot_hash") or ""),
            ttl_seconds=ttl_seconds or None,
        )
        payload.update(lease_result)
        if payload.get("status") in {"acquired", "renewed"}:
            payload["agent_instruction"] = "Validate the bounded patch with this same actor_id, apply it externally only after PASS and any required approval, then release this lease immediately."
        rendered = json.dumps(payload, indent=2, ensure_ascii=False) if str(format).lower() in {"json", "machine"} else _render_target_write_lease_brief(payload)
        result_status = "ok" if payload.get("status") in {"acquired", "renewed"} else "fail_closed"
        return _done(rendered, status=result_status, fail_closed_reason="target_write_lease_busy" if result_status != "ok" else "")
    except Exception as exc:
        payload = {
            "status": "error",
            "analysis_root": _analysis_root_display(target_root),
            "target_file": str(target_file or ""),
            "actor_id": str(actor_id or ""),
            "agent_instruction": "Do not edit while write-lease coordination is unavailable; resolve the lease error or obtain explicit human coordination.",
            "error": str(exc),
        }
        rendered = json.dumps(payload, indent=2, ensure_ascii=False) if str(format).lower() in {"json", "machine"} else _render_target_write_lease_brief(payload)
        return _done(rendered, status="error", fail_closed_reason="target_write_lease_error")


def _release_target_write_lease_for_mcp(target_file: str, actor_id: str, target_root: str = "", format: str = "brief") -> str:
    """Release helper for the single public target-write-lease MCP tool."""
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("manage_target_write_lease", started, result, status=status, fail_closed_reason=fail_closed_reason)

    target = _valid_external_target(target_root) if target_root else None
    if target_root and target is None:
        return _done(_invalid_external_target_brief("manage_target_write_lease", target_root), status="fail_closed", fail_closed_reason="invalid_external_target")
    try:
        raw_dir = _raw_dir_for_target(target_root)
        target_status = _target_path_status(raw_dir, target_file, target_root=target_root)
        from tools.core.target_write_lease import release_target_write_lease as release_lease

        payload = release_lease(
            raw_dir / "codemaps.db",
            analysis_root=_analysis_root_display(target_root),
            target_file=target_status.get("target_file") or str(target_file or ""),
            actor_id=actor_id,
        )
        payload.update(
            {
                "analysis_root": _analysis_root_display(target_root),
                "target_ref": target_status.get("target_ref") or "",
                "target_file": target_status.get("target_file") or str(target_file or ""),
                "actor_id": str(actor_id or "").strip(),
                "agent_instruction": "The next mutation must reacquire a lease and refresh source grounding; do not reuse a pre-write packet after this release.",
            }
        )
        rendered = json.dumps(payload, indent=2, ensure_ascii=False) if str(format).lower() in {"json", "machine"} else _render_target_write_lease_brief(payload)
        result_status = "ok" if payload.get("status") in {"released", "not_found"} else "fail_closed"
        return _done(rendered, status=result_status, fail_closed_reason="target_write_lease_not_owner" if result_status != "ok" else "")
    except Exception as exc:
        payload = {
            "status": "error",
            "analysis_root": _analysis_root_display(target_root),
            "target_file": str(target_file or ""),
            "actor_id": str(actor_id or ""),
            "error": str(exc),
        }
        rendered = json.dumps(payload, indent=2, ensure_ascii=False) if str(format).lower() in {"json", "machine"} else _render_target_write_lease_brief(payload)
        return _done(rendered, status="error", fail_closed_reason="target_write_lease_error")


@mcp.tool()
def validate_patch(target_file: str, patch_content: str, target_root: str = "", format: str = "brief", actor_id: str = "") -> str:
    """
    Validate a proposed code block or git patch/diff against the sealed Architecture Doctrine layer rules in-memory.
    Checks layer purity, forbidden imports, alias hygiene, and LOC complexity limits before physical disk write.
    """
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("validate_patch", started, result, status=status, fail_closed_reason=fail_closed_reason)

    try:
        from tools.engines.mcp_governance_engine import validate_proposed_patch
        target = _valid_external_target(target_root) if target_root else None
        if target_root and target is None:
            return _done(_invalid_external_target_brief("validate_patch", target_root), status="fail_closed", fail_closed_reason="invalid_external_target")
        workspace_root = Path(target if target is not None else ANALYZED_REPOSITORY_ROOT).resolve()
        result = validate_proposed_patch(
            target_file,
            patch_content,
            workspace_root=workspace_root,
        )
        raw_dir = _raw_dir_for_target(target_root)
        target_status = _target_path_status(raw_dir, target_file, target_root=target_root)
        target_ref = target_file if "::" in str(target_file or "") else str(target_status.get("target_ref") or "")
        if isinstance(result, dict):
            analysis_snapshot_id = _analysis_snapshot_id(raw_dir)
            target_project = _project_from_ref(target_ref)
            target_file_value = str(target_status.get("target_file") or str(target_file).split("::", 1)[-1])
            audit_baseline, audit_baseline_count, audit_query_ok = _audit_violation_work_items_from_sqlite(
                raw_dir,
                page=1,
                page_size=100,
                project=target_project,
                file_path=target_file_value,
            )
            result.setdefault("analysis_root", _analysis_root_display(target_root))
            result.setdefault("target_project", target_project)
            result.setdefault("target_file", target_file_value)
            result.setdefault("target_ref", target_ref)
            result["analysis_snapshot_id"] = analysis_snapshot_id
            result["existing_violation_count_semantics"] = (
                "SAGE governance findings that remain unchanged after the proposed patch"
            )
            result["resolved_violation_count_semantics"] = (
                "SAGE governance findings present in the current source and removed by the proposed patch"
            )
            result["patch_validator_baseline_violation_count"] = (
                int(result.get("existing_violation_count") or 0)
                + int(result.get("resolved_violation_count") or 0)
            )
            result["current_audit_baseline"] = {
                "status": "available" if audit_query_ok else "unavailable",
                "analysis_snapshot_id": analysis_snapshot_id,
                "finding_count": audit_baseline_count if audit_query_ok else None,
                "findings": audit_baseline[:25] if audit_query_ok else [],
                "semantics": (
                    "Current SQLite Audit findings for the grounded target file and project. "
                    "This is broader current-state context, not the patch validator delta or "
                    "target-repository-native enforcement truth."
                ),
            }
            validated_no_op = bool(result.get("no_op_patch"))
            result["target_path_status"] = target_status
            result["target_exists"] = bool(target_status.get("exists"))
            result["target_indexed"] = bool(target_status.get("indexed"))
            result["target_inside_root"] = bool(target_status.get("inside_root"))
            result["proposed_change_snippets"] = _bounded_patch_change_snippets(patch_content)
            lease_actor = str(actor_id or "").strip()
            if lease_actor and not validated_no_op:
                try:
                    from tools.core.target_write_lease import inspect_target_write_lease

                    lease_state = inspect_target_write_lease(
                        raw_dir / "codemaps.db",
                        analysis_root=_analysis_root_display(target_root),
                        target_file=target_status.get("target_file") or str(target_file or ""),
                    )
                    active_lease = lease_state.get("lease") if isinstance(lease_state.get("lease"), dict) else {}
                    lease_matches_actor = str(active_lease.get("actor_id") or "") == lease_actor
                    lease_matches_snapshot = str(active_lease.get("source_snapshot_hash") or "") == str(target_status.get("source_snapshot_hash") or "")
                    result["target_write_lease"] = {
                        **lease_state,
                        "requested_actor_id": lease_actor,
                        "matches_actor": lease_matches_actor,
                        "matches_source_snapshot": lease_matches_snapshot,
                    }
                    if not lease_matches_actor or not lease_matches_snapshot:
                        lease_action = _mcp_recommended_tool_action(
                            "manage_target_write_lease",
                            {
                                "action": "acquire",
                                "target_file": target_status.get("target_file") or str(target_file or ""),
                                "actor_id": lease_actor,
                                "target_root": target_root,
                            },
                            required_profile="target_repository_followup",
                        )
                        violations = result.get("violations") if isinstance(result.get("violations"), list) else []
                        violations.append(
                            {
                                "rule": "target_write_lease_not_current",
                                "detail": "The supplied actor_id does not hold a current write lease grounded to this source snapshot.",
                                "recommended_action": (
                                    "Acquire the exact grounded target lease through the "
                                    f"{lease_action['required_profile']} MCP profile, return to "
                                    f"{lease_action['return_profile'] or lease_action['current_profile']}, "
                                    "then validate again with the same actor_id."
                                ),
                                "recommended_action_contract": lease_action,
                            }
                        )
                        result["violations"] = violations
                        result["status"] = "FAIL"
                        result["safe_to_apply"] = False
                except Exception as exc:
                    result["target_write_lease"] = {
                        "status": "unknown",
                        "requested_actor_id": lease_actor,
                        "error": str(exc),
                    }
                    violations = result.get("violations") if isinstance(result.get("violations"), list) else []
                    violations.append(
                        {
                            "rule": "target_write_lease_unavailable",
                            "detail": "Write-lease state could not be checked for the supplied actor_id.",
                            "recommended_action": "Do not apply a coordinated multi-agent patch until write-lease state is available.",
                        }
                    )
                    result["violations"] = violations
                    result["status"] = "FAIL"
                    result["safe_to_apply"] = False
            elif validated_no_op:
                result["target_write_lease"] = {
                    "status": "not_required_no_op",
                    "requested_actor_id": lease_actor,
                    "agent_instruction": "No write lease is required because the proposed content makes no target mutation.",
                }
            else:
                result["target_write_lease"] = {
                    "status": "not_requested",
                    "agent_instruction": "For cooperating multi-agent writes, acquire_target_write_lease and pass the same actor_id to validate_patch before applying externally.",
                }
            try:
                from tools.core.seal_impact_guard import mutating_agent_surface_block

                execution_identity = resolve_execution_identity(
                    target_root=target_root,
                    actor_profile=_active_mcp_actor_profile(),
                    reality_profile=str(os.environ.get(SAGE_REALITY_TARGET_PROFILE_ENV) or ""),
                    public_distribution=(BASE_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file(),
                    installation_root=BASE_DIR,
                    default_repository_root=ANALYZED_REPOSITORY_ROOT,
                )
                seal_block = mutating_agent_surface_block(
                    system_scope=execution_identity["system_scope"],
                    raw_dir=raw_dir,
                    subject_root=execution_identity["subject_root"],
                )
            except Exception as exc:
                seal_block = {
                    "blocked": True,
                    "failure_family": "validator_internal_error",
                    "seal_impact_status": "unknown",
                    "highest_impact_level": "unknown",
                    "message": "Seal impact guard failed internally; mutating patch validation fails closed without claiming that a human seal was impacted.",
                    "report": "output/reports/seal_impact_validation.md",
                    "exception": str(exc),
                }
            if validated_no_op:
                result["non_actionable_seal_observation"] = seal_block
                result["seal_impact_guard"] = {
                    "status": "not_required_no_op",
                    "blocked": False,
                    "message": "Seal revalidation is not required because the proposed content makes no target mutation.",
                }
            else:
                result["seal_impact_guard"] = seal_block
            if seal_block.get("blocked") and not validated_no_op:
                violations = result.get("violations") if isinstance(result.get("violations"), list) else []
                if seal_block.get("failure_family") == "validator_internal_error":
                    violations.append(
                        {
                            "rule": "validator_internal_error",
                            "detail": seal_block.get("message"),
                            "recommended_action": "Do not apply the patch. Preserve the error, report the SAGE defect, and retry only after the validator is repaired.",
                            "report": seal_block.get("report"),
                        }
                    )
                else:
                    violations.append(
                        {
                            "rule": "active_human_seal_impacted",
                            "detail": seal_block.get("message")
                            or "An active human seal is impacted; SAGE will not mark mutating agent work safe to apply.",
                            "recommended_action": "Refresh the affected proof pack and record a new Progressive HITL human seal before applying patches through SAGE.",
                            "report": seal_block.get("report"),
                        }
                    )
                result["violations"] = violations
                result["status"] = "FAIL"
                result["safe_to_apply"] = False
                result["human_approval_required"] = True
            full_replacement_patch = (
                not result["proposed_change_snippets"]
                and str(patch_content or "").strip()
                and bool(target_status.get("exists"))
                and not _is_unified_diff_like(str(patch_content or ""))
            )
            result["full_replacement_patch"] = bool(full_replacement_patch)
            governance_passed = (
                str(result.get("status") or "").upper() == "PASS"
                and not (result.get("violations") if isinstance(result.get("violations"), list) else [])
            )
            result["governance_validation_passed"] = governance_passed
            result["governance_status"] = "PASS" if governance_passed else str(result.get("status") or "UNKNOWN").upper()
            result["target_native_policy_validation"] = {
                "status": "NOT_RUN",
                "claim_boundary": (
                    "SAGE does not currently ingest or execute the target repository's effective "
                    "lint/compiler/policy oracle in validate_patch."
                ),
                "required_action": (
                    "Run the repository-native policy, lint, compiler and focused test commands "
                    "before applying this candidate."
                ),
            }
            if (
                _is_unified_diff_like(str(patch_content or ""))
                and bool(target_status.get("exists"))
                and bool(target_status.get("indexed"))
                and bool(analysis_snapshot_id)
            ):
                result["patch_applicability"] = _check_exact_patch_applicability(
                    str(patch_content or ""),
                    workspace_root,
                )
            else:
                result["patch_applicability"] = {
                    "status": "NOT_RUN",
                    "oracle": "git_apply_check",
                    "mutation_performed": False,
                    "reason": (
                        "not_unified_diff"
                        if not _is_unified_diff_like(str(patch_content or ""))
                        else "target_or_snapshot_not_grounded"
                    ),
                }
            if full_replacement_patch and not validated_no_op:
                result["human_approval_required"] = True
                try:
                    target_abs = Path(str(target_status.get("target_abs") or ""))
                    if target_abs.exists() and target_abs.is_file() and target_abs.stat().st_size <= 2_000_000:
                        current_content = target_abs.read_text(encoding="utf-8", errors="replace")
                        result["proposed_change_snippets"] = _bounded_full_replacement_diff_snippets(
                            current_content,
                            str(patch_content or ""),
                        )
                except Exception as exc:
                    try:
                        from tools.core.honesty_telemetry import record_honesty_event

                        record_honesty_event(
                            component="mcp.server",
                            category="caught_error",
                            operation="patch_validation_full_replacement_diff_preview",
                            subject=str(target_file or ""),
                            reason="Could not build bounded proposed-change preview for a full replacement patch.",
                            fallback="omit_proposed_change_snippets",
                            claim_impact="agent_patch_preview_degraded",
                            exception=exc,
                        )
                    except Exception:
                        pass
            if not str(patch_content or "").strip():
                result["status"] = "NO_OP"
                result["safe_to_apply"] = False
                result["no_op_patch"] = True
            elif not target_status.get("exists") or not target_status.get("indexed"):
                violations = result.get("violations") if isinstance(result.get("violations"), list) else []
                violations.append(
                    {
                        "rule": "mcp_target_not_grounded",
                        "detail": "Target file is missing from disk or not present in the current Atlas index; this tool cannot treat the patch as safe to apply.",
                        "recommended_action": "Refresh SAGE analysis for the target repository or use an explicit create-file workflow with human approval before applying this patch.",
                    }
                )
                result["violations"] = violations
                result["status"] = "FAIL"
                result["safe_to_apply"] = False
                result["target_grounding_status"] = "missing_or_unindexed"
            elif not analysis_snapshot_id:
                violations = result.get("violations") if isinstance(result.get("violations"), list) else []
                violations.append(
                    {
                        "rule": "analysis_snapshot_identity_missing",
                        "detail": "The target is grounded, but its current Atlas snapshot identity is unavailable.",
                        "recommended_action": "Refresh target analysis and repeat patch validation before treating the result as safe to apply.",
                    }
                )
                result["violations"] = violations
                result["status"] = "INVALID_CONTEXT"
                result["safe_to_apply"] = False
            elif validated_no_op:
                result["status"] = "NO_OP"
                result["safe_to_apply"] = False
                result["human_approval_required"] = False
                result["application_readiness"] = "NO_CHANGE"
                result["non_actionable_patch_facts"] = {
                    "full_replacement_transport_detected": bool(full_replacement_patch),
                    "write_lease_required": False,
                    "seal_revalidation_required": False,
                }
            elif full_replacement_patch:
                violations = result.get("violations") if isinstance(result.get("violations"), list) else []
                violations.append(
                    {
                        "rule": "mcp_full_replacement_requires_human_approval",
                        "detail": "Patch content was interpreted as a full-file replacement, not a bounded unified diff. SAGE can preview the change but will not mark it safe for automatic apply.",
                        "recommended_action": "Use a bounded unified diff for automated patch validation, or request explicit human approval for full-file replacement.",
                    }
                )
                result["violations"] = violations
                result["status"] = "REVIEW_REQUIRED"
                result["safe_to_apply"] = False
                result["human_approval_required"] = True
                result["application_readiness"] = "HUMAN_APPROVAL_REQUIRED"
            elif str((result.get("patch_applicability") or {}).get("status") or "").upper() == "FAIL":
                violations = result.get("violations") if isinstance(result.get("violations"), list) else []
                violations.append(
                    {
                        "rule": "exact_patch_not_applicable",
                        "detail": "The configured Git patch engine rejected the exact payload in non-mutating check mode.",
                        "recommended_action": "Regenerate the patch from the current source bytes and repeat exact applicability validation.",
                        "evidence": result.get("patch_applicability"),
                    }
                )
                result["violations"] = violations
                result["status"] = "FAIL"
                result["safe_to_apply"] = False
                result["application_readiness"] = "NOT_APPLICABLE"
            elif str((result.get("patch_applicability") or {}).get("status") or "").upper() != "PASS":
                result["safe_to_apply"] = False
                result["application_readiness"] = "INCOMPLETE_APPLICABILITY_EVIDENCE"
            elif str(result.get("status") or "").upper() != "PASS" or result.get("violations"):
                result["safe_to_apply"] = False
            else:
                result["safe_to_apply"] = False
                result["application_readiness"] = "APPLICABLE_REQUIRES_TARGET_NATIVE_VALIDATION"
            if (
                str(result.get("status") or "").upper() in {"PASS", "OK"}
                and result.get("safe_to_apply") is not True
            ):
                result["status"] = "INCOMPLETE_EVIDENCE"
            result["overall_status"] = str(result.get("status") or "UNKNOWN").upper()
        requested_format = str(format or "brief").strip().lower()
        if requested_format in {"json", "machine"}:
            response = json.dumps(result, indent=2, ensure_ascii=False)
        else:
            response = _render_patch_validation_brief(result, target_file, target_root=target_root)
        status = "ok"
        fail_closed_reason = ""
        if isinstance(result, dict) and str(result.get("status") or "").upper() not in {"PASS", "OK"}:
            status = "fail_closed"
            fail_closed_reason = str(result.get("status") or "validation_not_passed")
        return _done(response, status=status, fail_closed_reason=fail_closed_reason)
    except Exception as exc:
        requested_format = str(format or "brief").strip().lower()
        payload = {
            "status": "FAIL",
            "analysis_root": _analysis_root_display(target_root),
            "target_project": _project_from_ref(target_file if "::" in str(target_file or "") else ""),
            "target_file": str(target_file or "").split("::", 1)[-1],
            "target_ref": target_file if "::" in str(target_file or "") else "",
            "violations": [{
                "rule": "mcp_governance_exception",
                "detail": f"MCP Governance verification crashed: {str(exc)}",
                "recommended_action": "Check MCP server logs or architecture_doctrine.json configuration status."
            }],
            "metrics": {"loc": 0, "symbols_analyzed": 0}
        }
        response = json.dumps(payload, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_patch_validation_brief(payload, target_file, target_root=target_root)
        return _done(response, status="error", fail_closed_reason="mcp_governance_exception")


@mcp.tool()
def get_impact_radius(target_node: str, target_root: str = "", format: str = "brief", depth: int = 2) -> str:
    """
    Standardized impact radius mapping for a specific file or symbol.
    Target format: PROJECT::path/to/file.ts (e.g. MAIN::src/main.tsx)
    Returns dependency counts plus a depth-limited direct/transitive sample for agent-facing review.
    depth=2 is the default agent scope. Use depth=0 for full reachable graph materialization.
    """
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("get_impact_radius", started, result, status=status, fail_closed_reason=fail_closed_reason)

    requested_format = str(format or "brief").strip().lower()
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _done(_invalid_external_target_brief("get_impact_radius", target_root), status="fail_closed", fail_closed_reason="invalid_external_target")
    if target_root:
        payload = _impact_radius_from_raw(raw_dir, target_node, target_root=target_root, depth=depth)
        if payload is None:
            return _done(_missing_target_artifact_brief(
                "get_impact_radius",
                target_node,
                target_root,
                ["atlas.json", "circular_deps.json"],
            ), status="fail_closed", fail_closed_reason="missing_target_artifact")
        response = json.dumps(payload, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_impact_brief(payload)
        return _done(response)
    try:
        payload = _impact_radius_from_raw(raw_dir, target_node, target_root=target_root, depth=depth)
        if payload is not None:
            response = json.dumps(payload, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_impact_brief(payload)
            return _done(response)
        engine_path = TOOLS_DIR / "engines" / "blast_radius_engine.py"
        result = _run_python_script(engine_path, "--simulate", target_node)
        return _done(result)
    except Exception as exc:
        return _done(f"Impact analysis failed: {exc}", status="error", fail_closed_reason="impact_analysis_exception")


@mcp.tool()
def get_test_impact(target_file: str, target_root: str = "", format: str = "brief") -> str:
    """
    Returns polyglot unit and integration test candidates affected by changes to the target file.
    Target format: PROJECT::path/to/file.tsx or just path/to/file.py
    Analyzes dual vectors: Static Dependency Graph and Semantic Convention Match, and outputs runnable commands.
    """
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("get_test_impact", started, result, status=status, fail_closed_reason=fail_closed_reason)

    requested_format = str(format or "brief").strip().lower()
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _done(_invalid_external_target_brief("get_test_impact", target_root), status="fail_closed", fail_closed_reason="invalid_external_target")
    if target_root:
        result = _test_impact_from_raw(raw_dir, target_file, target_root=target_root)
        if result is None:
            return _done(_missing_target_artifact_brief("get_test_impact", target_file, target_root, ["atlas.json"]), status="fail_closed", fail_closed_reason="missing_target_artifact")
        result = _normalize_test_impact_payload_for_agent(result)
        result = _attach_test_source_snippets(raw_dir, result)
        response = json.dumps(result, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_test_impact_brief(result)
        return _done(response)
    try:
        from tools.engines.test_impact_matcher import find_impacted_tests
        result = find_impacted_tests(target_file)
        resolved_node, context = _resolve_target_node_from_raw(raw_dir, target_file)
        target_ref = _target_ref_from_context(resolved_node, context)
        target_file_rel = context.get("repo_relative_path") or _repo_relative_from_node(raw_dir, resolved_node)
        target_status = _target_path_status(raw_dir, target_file, target_root=target_root)
        if isinstance(result, dict):
            result["analysis_root"] = _analysis_root_display(target_root)
            result["target_ref"] = target_ref
            result["target_project"] = _project_from_ref(target_ref)
            result["target_file"] = target_file_rel
            result["target_path_status"] = target_status
            result["target_exists"] = bool(target_status.get("exists"))
            result["target_indexed"] = bool(target_status.get("indexed"))
            result["target_grounding_status"] = "grounded" if target_status.get("exists") and target_status.get("indexed") else "missing_or_unindexed"
            result = _merge_live_direct_test_candidates(
                result,
                Path(_analysis_root_display(target_root)),
                str(target_file_rel),
            )
            if not (target_status.get("exists") and target_status.get("indexed")):
                matrix = result.get("confidence_matrix") if isinstance(result.get("confidence_matrix"), dict) else {}
                matrix = dict(matrix)
                matrix["merge_safety"] = "UNKNOWN_TARGET_NOT_GROUNDED"
                matrix["architecture_drift_certainty"] = None
                matrix["dead_code_confidence"] = None
                result["confidence_matrix"] = matrix
                reasons = result.get("reasons") if isinstance(result.get("reasons"), list) else []
                if "Target file is missing from disk or not present in the current Atlas index." not in reasons:
                    reasons.append("Target file is missing from disk or not present in the current Atlas index.")
                result["reasons"] = reasons
                result["verdict"] = "Confidence unavailable until the target is grounded in disk and Atlas."
            result.setdefault("decision_boundary", "Confidence is a risk estimate, not a standalone merge or deploy approval.")
            result = _normalize_test_impact_payload_for_agent(result)
            result = _attach_test_source_snippets(raw_dir, result)
        response = json.dumps(result, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_test_impact_brief(result)
        return _done(response)
    except Exception as exc:
        requested_format = str(format or "brief").strip().lower()
        payload = {
            "target": target_file,
            "target_ref": target_file if "::" in str(target_file or "") else "",
            "target_project": _project_from_ref(target_file if "::" in str(target_file or "") else ""),
            "analysis_root": _analysis_root_display(target_root),
            "impacted_tests": [],
            "error": f"Test impact matching failed: {str(exc)}"
        }
        response = json.dumps(payload, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_test_impact_brief(payload)
        return _done(response, status="error", fail_closed_reason="test_impact_exception")


@mcp.tool()
def get_confidence_score(target_file: str, target_root: str = "", format: str = "brief") -> str:
    """
    Returns the composite confidence and risk assessment matrix for a target file.
    Evaluates dead code certainty (reflectivity checking), merge safety level, and architecture drift probability.
    """
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("get_confidence_score", started, result, status=status, fail_closed_reason=fail_closed_reason)

    requested_format = str(format or "brief").strip().lower()
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _done(_invalid_external_target_brief("get_confidence_score", target_root), status="fail_closed", fail_closed_reason="invalid_external_target")
    if target_root:
        result = _confidence_from_raw(raw_dir, target_file, target_root=target_root)
        if result is None:
            return _done(_missing_target_artifact_brief("get_confidence_score", target_file, target_root, ["atlas.json"]), status="fail_closed", fail_closed_reason="missing_target_artifact")
        if isinstance(result, dict):
            result = _calibrate_confidence_with_sqlite_impact(raw_dir, target_file, target_root, result)
            result = _normalize_confidence_payload_for_agent(result)
        response = json.dumps(result, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_confidence_brief(result)
        return _done(response)
    try:
        from tools.engines.confidence_engine import evaluate_file_confidence
        result = evaluate_file_confidence(target_file)
        resolved_node, context = _resolve_target_node_from_raw(raw_dir, target_file)
        target_ref = _target_ref_from_context(resolved_node, context)
        target_file_rel = context.get("repo_relative_path") or _repo_relative_from_node(raw_dir, resolved_node)
        target_status = _target_path_status(raw_dir, target_file, target_root=target_root)
        if isinstance(result, dict):
            result["analysis_root"] = _analysis_root_display(target_root)
            result["target_ref"] = target_ref
            result["target_project"] = _project_from_ref(target_ref)
            result["target_file"] = target_file_rel
            result["target_path_status"] = target_status
            result["target_exists"] = bool(target_status.get("exists"))
            result["target_indexed"] = bool(target_status.get("indexed"))
            result["target_grounding_status"] = "grounded" if target_status.get("exists") and target_status.get("indexed") else "missing_or_unindexed"
            if not (target_status.get("exists") and target_status.get("indexed")):
                matrix = result.get("confidence_matrix") if isinstance(result.get("confidence_matrix"), dict) else {}
                matrix = dict(matrix)
                matrix["merge_safety"] = "UNKNOWN_TARGET_NOT_GROUNDED"
                result["confidence_matrix"] = matrix
                reasons = result.get("reasons") if isinstance(result.get("reasons"), list) else []
                if "Target file is missing from disk or not present in the current Atlas index." not in reasons:
                    reasons.append("Target file is missing from disk or not present in the current Atlas index.")
                result["reasons"] = reasons
                result["verdict"] = "Confidence unavailable until the target is grounded in disk and Atlas."
            result = _calibrate_confidence_with_sqlite_impact(raw_dir, target_file, target_root, result)
            result = _normalize_confidence_payload_for_agent(result)
        response = json.dumps(result, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_confidence_brief(result)
        return _done(response)
    except Exception as exc:
        payload = {
            "target": target_file,
            "target_ref": target_file if "::" in str(target_file or "") else "",
            "target_project": _project_from_ref(target_file if "::" in str(target_file or "") else ""),
            "analysis_root": _analysis_root_display(target_root),
            "target_file": str(target_file or "").split("::", 1)[-1],
            "target_exists": False,
            "target_indexed": False,
            "target_grounding_status": "unknown_due_to_confidence_exception",
            "confidence_matrix": {},
            "reasons": [],
            "error": f"Confidence evaluation failed: {str(exc)}"
        }
        payload = _normalize_confidence_payload_for_agent(payload)
        response = json.dumps(payload, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_confidence_brief(payload)
        return _done(response, status="error", fail_closed_reason="confidence_exception")


@mcp.tool()
def get_telemetry_hotpaths() -> str:
    """
    Returns the aggregated runtime hot-paths and zero-execution coverage gaps inside the workspace.
    Correlates local HTTP route calls, test suite traces, and UI renders with static atlas nodes.
    """
    started = time.perf_counter()
    try:
        from tools.engines.local_telemetry_engine import get_hybrid_telemetry_analysis
        result = get_hybrid_telemetry_analysis()
        return _record_mcp_call_result("get_telemetry_hotpaths", started, json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as exc:
        return _record_mcp_call_result("get_telemetry_hotpaths", started, json.dumps({
            "error": f"Telemetry retrieval failed: {str(exc)}"
        }, indent=2, ensure_ascii=False), status="error", fail_closed_reason="telemetry_retrieval_failed")


@mcp.tool()
def get_mcp_call_telemetry(tool_name: str = "", format: str = "brief") -> str:
    """Return bounded local MCP call latency and payload guidance; not repository proof."""
    requested_format = str(format or "brief").strip().lower()
    tool_filter = str(tool_name or "").strip()
    if requested_format in {"json", "machine"}:
        return json.dumps(load_mcp_call_telemetry(tool_filter), indent=2, ensure_ascii=False)
    return render_mcp_call_telemetry_brief(tool_filter)


@mcp.tool()
def record_dev_trace(trace_type: str, identifier: str, execution_ms: int = 0) -> str:
    """
    Records a local dev trace execution (route hit, component render, or unit test call).
    Increments execution hot-path frequency in the local dev telemetry loop.
    """
    try:
        from tools.engines.local_telemetry_engine import record_trace
        result = record_trace(trace_type, identifier, execution_ms)
        return json.dumps({"status": "SUCCESS", "recorded": result}, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "ERROR",
            "error": f"Failed to record trace: {str(exc)}"
        }, indent=2, ensure_ascii=False)


def _resolve_absolute_path(relative_path: str, project_key: str = "MAIN") -> Path:
    from tools.core.projects_registry import resolve_runtime_projects
    # Remove path traversal tokens and normalize
    cleaned_rel = relative_path.replace("\\", "/").lstrip("/")

    # 1. Resolve relative to the analyzed repository root, then SAGE's own root.
    for base in (TARGET_ROOT.resolve(), BASE_DIR.resolve()):
        cand = (base / cleaned_rel).resolve()
        try:
            cand.relative_to(base)
            if cand.exists():
                return cand
        except ValueError:
            pass

    # 2. Resolve relative to project_key directory
    base_resolved = BASE_DIR.resolve()
    try:
        projects = resolve_runtime_projects(BASE_DIR)
        if project_key in projects:
            proj_resolved = projects[project_key].resolve()
            cand_proj = (proj_resolved / cleaned_rel).resolve()
            try:
                cand_proj.relative_to(proj_resolved)
                if cand_proj.exists():
                    return cand_proj
            except ValueError:
                pass
    except Exception:
        pass

    # Fallback to a safe nonexistent path inside BASE_DIR
    return base_resolved / "nonexistent_file"


def _resolve_absolute_path_for_target(target_root: Path):
    def _resolver(relative_path: str, project_key: str = "MAIN") -> Path:
        cleaned_rel = str(relative_path or "").replace("\\", "/").lstrip("/")
        if "::" in cleaned_rel:
            _project, cleaned_rel = cleaned_rel.split("::", 1)
        base_resolved = target_root.resolve()
        candidate = (base_resolved / cleaned_rel).resolve()
        try:
            candidate.relative_to(base_resolved)
            return candidate
        except ValueError:
            return base_resolved / "nonexistent_file"

    return _resolver


@mcp.tool()
def get_active_signals(
    format: str = "markdown",
    include_bodies: bool = False,
    max_files: int = 8,
    max_chars_per_file: int = 6000,
    scope: str = "summary",
    target_root: str = "",
) -> str:
    """
    Returns the real-time surgical ContextOS active signals.
    Provides L1 focus files, L2 dependents, rule violations, and file bodies
    with strict masking of restricted/locked content.
    """
    try:
        raw_dir = _raw_dir_for_target(target_root)
    except ValueError as exc:
        return _invalid_external_target_brief("get_active_signals", target_root)
    signals_path = raw_dir / "signals.json"
    if not signals_path.exists():
        if target_root:
            return _missing_target_artifact_brief("get_active_signals", "active_signals", target_root, ["signals.json"])
        from tools.core.contextos_mcp import render_active_signals

        return render_active_signals(
            {"analysis_root": _analysis_root_display(target_root), "active_signals": [], "summary": {}},
            output_format=format,
            include_bodies=include_bodies,
            max_files=max_files,
            max_chars_per_file=max_chars_per_file,
            scope=scope,
            resolve_absolute_path=_resolve_absolute_path,
        )
        
    try:
        data = _load_json(signals_path) or {}
    except Exception as e:
        return f"Failed to load ContextOS signals: {e}"

    from tools.core.contextos_mcp import render_active_signals

    target = _valid_external_target(target_root) if target_root else None
    resolver = _resolve_absolute_path_for_target(target) if target is not None else _resolve_absolute_path
    if isinstance(data, dict):
        data.setdefault("analysis_root", _analysis_root_display(target_root))
    return render_active_signals(
        data,
        output_format=format,
        include_bodies=include_bodies,
        max_files=max_files,
        max_chars_per_file=max_chars_per_file,
        scope=scope,
        resolve_absolute_path=resolver,
    )


@mcp.tool()
def trace_upstream_cause(target_node: str, target_root: str = "", format: str = "brief") -> str:
    """
    Reverse blast-radius trace for debugging.
    Answers: if this target is failing, which active ContextOS file or upstream dependency may have caused it?
    """
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("trace_upstream_cause", started, result, status=status, fail_closed_reason=fail_closed_reason)

    requested_format = str(format or "brief").strip().lower()
    try:
        from tools.core.contextos_mcp import build_upstream_trace

        raw_dir = _raw_dir_for_target(target_root)
        if target_root and not (raw_dir / "codemaps.db").exists() and not load_atlas_data(raw_dir):
            return _done(_missing_target_artifact_brief("trace_upstream_cause", target_node, target_root, ["codemaps.db", "atlas.json"]), status="fail_closed", fail_closed_reason="missing_target_artifact")
        signals = _load_json(raw_dir / "signals.json") or {}
        result = _sqlite_upstream_trace_from_raw(raw_dir, target_node, target_root=target_root, signals_data=signals)
        circular_deps = _dependency_graph_payload_from_raw(raw_dir)
        if result is None:
            resolved_node, context = _resolve_target_node_from_raw(raw_dir, target_node)
            trace_target = resolved_node or target_node
            result = build_upstream_trace(trace_target, circular_deps, signals)
        else:
            resolved_node, context = _resolve_target_node_from_raw(raw_dir, target_node)
        if isinstance(result, dict):
            target_ref = _target_ref_from_context(resolved_node, context)
            target_file = context.get("repo_relative_path") or _repo_relative_from_node(raw_dir, resolved_node)
            meta = circular_deps.get("meta") if isinstance(circular_deps.get("meta"), dict) else {}
            result.setdefault("target", resolved_node)
            result.setdefault("target_ref", target_ref)
            result.setdefault("analysis_root", _analysis_root_display(target_root))
            result.setdefault("target_project", _project_from_ref(target_ref))
            result.setdefault("target_file", target_file)
            result.setdefault("target_path_status", _target_path_status(raw_dir, target_file, target_root=target_root))
            result.setdefault("dependency_graph_source", meta.get("dependency_graph_source") or "unknown")
            result.setdefault("evidence_limits", meta.get("limits") if isinstance(meta.get("limits"), list) else [])
            result = _attach_upstream_dependency_snippets(raw_dir, result)
        response = json.dumps(result, indent=2, ensure_ascii=False) if requested_format in {"json", "machine"} else _render_upstream_trace_brief(result)
        return _done(response)
    except Exception as exc:
        return _done(json.dumps({"target": target_node, "error": f"Upstream trace failed: {exc}"}, indent=2, ensure_ascii=False), status="error", fail_closed_reason="upstream_trace_exception")


@mcp.tool()
def get_surgical_operation_packet(max_signals: int = 8, format: str = "brief", target_root: str = "") -> str:
    """
    Return the one-call ContextOS operation packet for a coding agent working on the analyzed repository:
    L1 focus, L2 halo, breadcrumbs, snapshot-bound upstream traces, and next steps.
    format=brief returns a target-repo Markdown/YAML brief; format=json returns the canonical machine contract.
    format=brief_debug includes internal SAGE graph references for troubleshooting.
    target_root reads the isolated external-target output produced by run_external_target_analysis.
    """
    started = time.perf_counter()

    def _done(result: str, *, status: str = "ok", fail_closed_reason: str = "") -> str:
        return _record_mcp_call_result("get_surgical_operation_packet", started, result, status=status, fail_closed_reason=fail_closed_reason)

    try:
        from tools.core.contextos_mcp import build_surgical_operation_packet, render_surgical_operation_brief
        from tools.core.surgical_packet_inputs import evaluate_surgical_packet_inputs

        raw_dir = _raw_dir_for_target(target_root)
        trust_summary = _ensure_agent_artifact_chain_current(raw_dir, target_root=target_root)
        if _artifact_trust_blocks_actor_context(trust_summary):
            trust_summary = dict(trust_summary)
            trust_summary["auto_refresh"] = _surgical_packet_recovery_plan(
                {
                    "status": "BLOCKED",
                    "blocked_inputs": ["artifact_trust_chain"],
                },
                target_root=target_root,
            )
            invalid_context = _invalid_actor_context_payload("get_surgical_operation_packet", trust_summary)
            return _done(
                json.dumps(invalid_context, indent=2, ensure_ascii=False),
                status="fail_closed",
                fail_closed_reason="invalid_or_stale_artifact_trust",
            )
        input_payloads = {
            "signals": _load_json(raw_dir / "signals.json"),
            "circular_deps": _load_json(raw_dir / "circular_deps.json"),
            "live_surface_priority_pack": _load_json(raw_dir / "live_surface_priority_pack.json"),
        }
        atlas_commit = _load_json(raw_dir / "atlas_commit.json") or {}
        input_evidence, usable_inputs = evaluate_surgical_packet_inputs(
            raw_dir=raw_dir,
            expected_snapshot_id=str(atlas_commit.get("snapshot_id") or ""),
            payloads=input_payloads,
        )
        if input_evidence["status"] == "BLOCKED":
            trust_summary = dict(trust_summary)
            trust_summary["auto_refresh"] = _surgical_packet_recovery_plan(
                input_evidence,
                target_root=target_root,
            )
            invalid_context = _invalid_actor_context_payload("get_surgical_operation_packet", trust_summary)
            invalid_context["input_evidence"] = input_evidence
            return _done(
                json.dumps(invalid_context, indent=2, ensure_ascii=False),
                status="fail_closed",
                fail_closed_reason="required_packet_input_unbound",
            )
        signals = usable_inputs["signals"]
        circular_deps = usable_inputs.get("circular_deps", {})
        audit_report = _load_json(raw_dir / "audit_report.json") or {}
        quality_gate = _load_json(raw_dir / "quality_gate.json") or {}
        priority_pack = usable_inputs.get("live_surface_priority_pack", {})
        result = build_surgical_operation_packet(
            signals,
            circular_deps_data=circular_deps,
            audit_report=audit_report,
            quality_gate=quality_gate,
            priority_pack=priority_pack,
            max_signals=max_signals,
            raw_dir=raw_dir,
        )
        if isinstance(result, dict):
            result = _enrich_surgical_packet_with_sqlite_impact(
                raw_dir,
                result,
                target_root=target_root,
            )
            result["status"] = "ACCEPTED" if input_evidence["status"] == "PASS" else "INCOMPLETE_EVIDENCE"
            result["input_evidence"] = input_evidence
            result["analysis_root"] = _analysis_root_display(target_root)
            directives = result.get("agent_action_directives") if isinstance(result.get("agent_action_directives"), list) else []
            actionable_directives, refresh_required_directives = _partition_directives_by_source_freshness(
                raw_dir,
                [row for row in directives if isinstance(row, dict)],
                target_root=target_root,
            )
            result["agent_action_directives"] = actionable_directives
            result["refresh_required_directives"] = refresh_required_directives
            if refresh_required_directives:
                result["status"] = "INCOMPLETE_EVIDENCE"
                result["required_action"] = "Refresh target analysis before using stale directives for mutation."
            directives = actionable_directives or refresh_required_directives
            first_directive = directives[0] if directives and isinstance(directives[0], dict) else {}
            target_files = first_directive.get("target_files") if isinstance(first_directive.get("target_files"), list) else []
            target_refs = first_directive.get("target_refs") if isinstance(first_directive.get("target_refs"), list) else []
            target_ref = str(target_refs[0] or "") if target_refs else ""
            if not target_ref and target_files:
                target_file = str(target_files[0]).replace("\\", "/")
                target_ref = f"MAIN::{target_file}"
            if target_ref:
                source_grounding = _bounded_source_grounding_for_agent(
                    _target_path_status(raw_dir, target_ref, target_root=target_root),
                    max_spans=1,
                )
                evidence = str(first_directive.get("evidence") or "").strip()
                if (
                    source_grounding.get("source_snapshot_status") == "ok"
                    and source_grounding.get("drift_check_status") == "match"
                    and evidence
                ):
                    source_grounding["evidence_source_snippets"] = _evidence_source_snippets(
                        _source_snapshot_content_for_ref(raw_dir, target_ref),
                        [evidence],
                    )
                result["source_grounding"] = source_grounding
            from tools.core.governance_trace import current_trace_id, record_agent_handoff_trace

            handoff_trace_id = current_trace_id()
            if handoff_trace_id:
                try:
                    handoff_trace = record_agent_handoff_trace(result, trace_id=handoff_trace_id)
                    result["operation_trace"] = {
                        "status": "recorded",
                        "trace_id": handoff_trace["trace_id"],
                        "event_type": "agent_handoff",
                        "agent_rule": "Use this opaque local reference only when reporting an ambiguous handoff or validator outcome to SAGE. It is not a filesystem path, source evidence, or a correctness claim.",
                    }
                except Exception as exc:
                    from tools.core.honesty_telemetry import record_honesty_event

                    record_honesty_event(
                        component="mcp.server",
                        category="caught_error",
                        operation="record_agent_handoff_trace",
                        subject="get_surgical_operation_packet",
                        severity="warning",
                        reason="Local agent-handoff trace could not be persisted.",
                        fallback="return_surgical_packet_with_explicit_trace_not_available",
                        claim_impact="failure_attribution_trace_incomplete",
                        exception=exc,
                    )
                    result["operation_trace"] = {
                        "status": "not_available",
                        "reason": "agent_handoff_trace_persistence_failed",
                        "agent_rule": "Do not infer an operation trace when local persistence fails; the packet remains source-grounded but handoff attribution is incomplete.",
                    }
            else:
                result["operation_trace"] = {
                    "status": "not_available",
                    "reason": "packet_generated_outside_mcp_call_context",
                    "agent_rule": "Do not infer an operation trace when this packet was generated outside an MCP call context.",
                }
        requested_format = str(format or "json").lower()
        if requested_format in {"brief", "markdown", "md", "brief_debug", "debug"}:
            return _done(render_surgical_operation_brief(result, debug=requested_format in {"brief_debug", "debug"}))
        return _done(json.dumps(result, indent=2, ensure_ascii=False))
    except ValueError:
        return _done(_invalid_external_target_brief("get_surgical_operation_packet", target_root), status="fail_closed", fail_closed_reason="invalid_external_target")
    except Exception as exc:
        return _done(json.dumps({"error": f"Surgical operation packet failed: {exc}"}, indent=2, ensure_ascii=False), status="error", fail_closed_reason="surgical_operation_packet_exception")


import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class AuthAndRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_keys: list[str] | None = None, requests_per_minute: int = 60):
        super().__init__(app)
        self.api_keys = api_keys or ["safe_default_key_for_dev"]
        self.requests_per_minute = requests_per_minute
        # client_ip -> (tokens, last_update_time)
        self.buckets = defaultdict(lambda: (float(requests_per_minute), time.time()))

    async def dispatch(self, request, call_next):
        # 1. API Key Auth Check
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse({"detail": "Missing or invalid authorization header"}, status_code=401)
        token = auth_header.split(" ", 1)[1].strip()
        if token not in self.api_keys:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        # 2. Token-Bucket Rate Limiter
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        tokens, last_update = self.buckets[client_ip]

        # Replenish
        elapsed = now - last_update
        replenish_rate = self.requests_per_minute / 60.0  # tokens per second
        tokens = min(float(self.requests_per_minute), tokens + elapsed * replenish_rate)

        if tokens < 1.0:
            return JSONResponse({"detail": "Too many requests. Rate limit exceeded."}, status_code=429)

        self.buckets[client_ip] = (tokens - 1.0, now)
        return await call_next(request)


# Apply one fail-closed visibility contract to both stdio and HTTP transports.
_ACTIVE_MCP_PROFILE = resolve_mcp_tool_profile(BASE_DIR)
_MCP_PROFILE_PROJECTION = project_mcp_tool_names(BASE_DIR, _ACTIVE_MCP_PROFILE)
mcp.set_visible_tools(_ACTIVE_MCP_PROFILE, set(_MCP_PROFILE_PROJECTION["visible_tools"]))

# Configure HTTP/SSE app with SaaS Auth & Rate Limiting Middleware
try:
    app = mcp.streamable_http_app()
    configured_keys = os.environ.get("SAGE_API_KEYS", "safe_default_key_for_dev").split(",")
    app.add_middleware(AuthAndRateLimitMiddleware, api_keys=configured_keys, requests_per_minute=60)
except Exception:
    pass


if __name__ == "__main__":
    mcp.run()

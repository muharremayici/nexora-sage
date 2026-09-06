from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.mcp_tool_profiles import validate_mcp_tool_profiles
from tools.core.reality_scope import validate_reality_scope_projections, validate_reality_target_profiles


AGENT_SURFACE_CONTRACT_PATH = CODE_MAPS_DIR / "config" / "agent_surface_contract.json"

def _agent_surface_contract() -> dict[str, Any]:
    return load_json_file(AGENT_SURFACE_CONTRACT_PATH, {})


def _contract_set(key: str) -> set[str]:
    contract = _agent_surface_contract()
    values = contract.get(key, []) if isinstance(contract, dict) else []
    return {str(value) for value in values if str(value).strip()} if isinstance(values, list) else set()


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in value if str(item).strip()} if isinstance(value, list) else set()


ALLOWED_TOOL_ROLES = _contract_set("allowed_tool_roles")
REQUIRED_AGENT_TOOLS = _contract_set("required_agent_tools")

def _mentions_tool(text: str, tool: str) -> bool:
    return f"`{tool}" in text


def _is_target_skill_visible_tool(tool: str, role_rows: dict[str, Any]) -> bool:
    row = role_rows.get(tool, {})
    if not isinstance(row, dict):
        return False
    role = str(row.get("role") or "")
    audience = str(row.get("audience") or "")
    if role in {"primary_agent", "supporting_context"} and "target_repo_agent" in audience:
        return True
    if role == "human_hitl" and "target_repo_agent" in audience:
        return True
    if role == "mutating" and audience == "target_repo_agent":
        return True
    if role == "heavy_validation" and audience == "target_repo_agent":
        return True
    return False


def _patch_validation_next_actions() -> dict[str, Any]:
    contract = _agent_surface_contract()
    values = contract.get("patch_validation_next_actions") if isinstance(contract, dict) else {}
    return values if isinstance(values, dict) else {}


def _patch_validation_emitted_next_actions(server_text: str) -> set[str]:
    tree = ast.parse(server_text)
    renderer = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_render_patch_validation_brief"
        ),
        None,
    )
    if renderer is None:
        return set()
    emitted: set[str] = set()
    for node in ast.walk(renderer):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "next_action" for target in node.targets):
            continue
        emitted.update(
            str(value.value)
            for value in ast.walk(node.value)
            if isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and value.value.strip()
        )
    return emitted


def _confidence_emitted_action_keys(server_text: str) -> set[str]:
    tree = ast.parse(server_text)
    normalizer = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_normalize_confidence_payload_for_agent"
        ),
        None,
    )
    if normalizer is None:
        return set()
    return {
        str(node.args[0].value).strip()
        for node in ast.walk(normalizer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_confidence_recommended_action"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and str(node.args[0].value).strip()
    }


def _work_queue_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    values = contract.get("work_queue_brief_policy") if isinstance(contract, dict) else {}
    return values if isinstance(values, dict) else {}


def _test_impact_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    values = contract.get("test_impact_brief_policy") if isinstance(contract, dict) else {}
    return values if isinstance(values, dict) else {}


def _confidence_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    values = contract.get("confidence_brief_policy") if isinstance(contract, dict) else {}
    return values if isinstance(values, dict) else {}


def _state_flow_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    values = contract.get("state_flow_brief_policy") if isinstance(contract, dict) else {}
    return values if isinstance(values, dict) else {}


def _supporting_context_brief_policy() -> dict[str, Any]:
    contract = _agent_surface_contract()
    values = contract.get("supporting_context_brief_policy") if isinstance(contract, dict) else {}
    return values if isinstance(values, dict) else {}


def _skill_declares_supporting_context_profile(skill_text: str) -> bool:
    normalized = " ".join(skill_text.split())
    return (
        "Tools classified as `supporting_context` are not part of" in normalized
        and "`target_repository_default`" in normalized
        and "switch to `target_repository_followup`" in normalized
        and "do not retry the same" in normalized
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        base = _decorator_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _mcp_tool_metadata() -> dict[str, dict[str, Any]]:
    server_path = CODE_MAPS_DIR / "tools" / "mcp" / "server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8"))
    metadata: dict[str, dict[str, Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = {_decorator_name(decorator) for decorator in node.decorator_list}
        if "mcp.tool" in decorators:
            metadata[node.name] = {
                "docstring": ast.get_docstring(node) or "",
                "parameters": {
                    argument.arg
                    for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                },
            }
    return metadata


def _agent_surface_taxonomy_report() -> dict[str, Any]:
    path = CODE_MAPS_DIR / "config" / "agent_surface_taxonomy.json"
    if not path.exists():
        return {"ok": False, "path": "config/agent_surface_taxonomy.json", "issues": ["missing_taxonomy_config"]}
    payload = json.loads(path.read_text(encoding="utf-8"))
    validation_contract = payload.get("validation_contract") if isinstance(payload.get("validation_contract"), dict) else {}
    required_actors = _string_set(validation_contract.get("required_actor_surface_ids"))
    required_system_scopes = _string_set(validation_contract.get("required_system_scope_ids"))
    required_targets = _string_set(validation_contract.get("required_target_scope_ids"))
    required_rules = _string_set(validation_contract.get("required_surface_rule_ids"))
    required_transfer_families = _string_set(validation_contract.get("required_transfer_family_ids"))
    required_transfer_fields = _string_set(validation_contract.get("required_transfer_fields"))
    allowed_transfer_directions = _string_set(validation_contract.get("allowed_transfer_directions"))
    allowed_transfer_priorities = _string_set(validation_contract.get("allowed_transfer_priorities"))
    required_forbidden_default_roles = _string_set(
        validation_contract.get("target_repository_agent_required_forbidden_default_roles")
    )
    actor_rows = payload.get("actor_surfaces") if isinstance(payload.get("actor_surfaces"), list) else []
    system_scope_rows = payload.get("system_scopes") if isinstance(payload.get("system_scopes"), list) else []
    target_rows = payload.get("target_scopes") if isinstance(payload.get("target_scopes"), list) else []
    rule_rows = payload.get("surface_rules") if isinstance(payload.get("surface_rules"), list) else []
    transfer_rows = (
        payload.get("bidirectional_capability_transfer_matrix")
        if isinstance(payload.get("bidirectional_capability_transfer_matrix"), list)
        else []
    )
    canonical_cef_flow = _string_set(payload.get("canonical_cef_flow"))
    actors = {str(row.get("id")): row for row in actor_rows if isinstance(row, dict)}
    system_scopes = {str(row.get("id")): row for row in system_scope_rows if isinstance(row, dict)}
    targets = {str(row.get("id")): row for row in target_rows if isinstance(row, dict)}
    rules = {str(row.get("id")): row for row in rule_rows if isinstance(row, dict)}
    transfers = {str(row.get("id")): row for row in transfer_rows if isinstance(row, dict)}
    issues: list[Any] = []
    if (
        not required_actors
        or not required_system_scopes
        or not required_targets
        or not required_rules
        or not required_forbidden_default_roles
        or not required_transfer_families
        or not required_transfer_fields
        or not allowed_transfer_directions
        or not allowed_transfer_priorities
        or not canonical_cef_flow
    ):
        issues.append({"validation_contract_missing_or_empty": True})
    missing_actors = sorted(required_actors - set(actors))
    missing_system_scopes = sorted(required_system_scopes - set(system_scopes))
    missing_targets = sorted(required_targets - set(targets))
    missing_rules = sorted(required_rules - set(rules))
    if missing_actors:
        issues.append({"missing_actors": missing_actors})
    if missing_system_scopes:
        issues.append({"missing_system_scopes": missing_system_scopes})
    if missing_targets:
        issues.append({"missing_targets": missing_targets})
    if missing_rules:
        issues.append({"missing_rules": missing_rules})
    missing_transfer_families = sorted(required_transfer_families - set(transfers))
    undeclared_transfer_families = sorted(set(transfers) - required_transfer_families)
    if missing_transfer_families:
        issues.append({"missing_transfer_families": missing_transfer_families})
    if undeclared_transfer_families:
        issues.append({"undeclared_transfer_families": undeclared_transfer_families})
    transfer_contract_issues: list[dict[str, Any]] = []
    for transfer_id, row in transfers.items():
        missing_fields = sorted(
            field for field in required_transfer_fields
            if field not in row or row.get(field) in (None, "", [])
        )
        cef_mapping = row.get("cef_role_mapping") if isinstance(row.get("cef_role_mapping"), dict) else {}
        realizes = _string_set(cef_mapping.get("realizes"))
        contributes_to = _string_set(cef_mapping.get("contributes_to"))
        cef_stages = realizes | contributes_to
        unknown_cef_stages = sorted(cef_stages - canonical_cef_flow)
        direction = str(row.get("transfer_direction") or "")
        priority = str(row.get("priority") or "")
        if (
            missing_fields
            or not realizes
            or unknown_cef_stages
            or direction not in allowed_transfer_directions
            or priority not in allowed_transfer_priorities
        ):
            transfer_contract_issues.append(
                {
                    "id": transfer_id,
                    "missing_fields": missing_fields,
                    "unknown_cef_stages": unknown_cef_stages,
                    "realizes": sorted(realizes),
                    "contributes_to": sorted(contributes_to),
                    "transfer_direction": direction,
                    "priority": priority,
                }
            )
    if transfer_contract_issues:
        issues.append({"transfer_contract_issues": transfer_contract_issues})

    scope_validation = validate_reality_scope_projections(required_system_scopes)
    if scope_validation.get("issues"):
        issues.append({"canonical_projection_issues": scope_validation.get("issues")})
    target_agent = actors.get("target_repository_agent", {})
    forbidden_default_roles = set(target_agent.get("forbidden_default_roles") or [])
    if not required_forbidden_default_roles.issubset(forbidden_default_roles):
        issues.append({"target_repository_agent_forbidden_default_roles": sorted(forbidden_default_roles)})
    if target_agent.get("default_surface") is not True:
        issues.append({"target_repository_agent_default_surface": target_agent.get("default_surface")})
    corpus_usage = str((targets.get("corpus_validation_pool") or {}).get("agent_usage") or "").lower()
    if "must not be injected" not in corpus_usage and "not ordinary coding context" not in corpus_usage:
        issues.append({"corpus_validation_pool_agent_usage": corpus_usage})
    maintainer_rule = str((rules.get("sage_on_sage_is_explicit") or {}).get("rule") or "").lower()
    if "explicit" not in maintainer_rule:
        issues.append({"sage_on_sage_rule": maintainer_rule})
    return {
        "ok": not issues,
        "path": "config/agent_surface_taxonomy.json",
        "actor_surfaces": sorted(actors),
        "system_scopes": sorted(system_scopes),
        "target_scopes": sorted(targets),
        "surface_rules": sorted(rules),
        "transfer_families": sorted(transfers),
        "bidirectional_capability_transfer_matrix": transfer_rows,
        "canonical_cef_flow": payload.get("canonical_cef_flow"),
        "capability_scope_counts": scope_validation.get("capability_scope_counts", {}),
        "projection_summaries": scope_validation.get("projection_summaries", {}),
        "issues": issues,
    }


def build_validation() -> dict[str, Any]:
    tool_metadata = _mcp_tool_metadata()
    tools = set(tool_metadata)
    tool_docstrings = {name: str(row["docstring"]) for name, row in tool_metadata.items()}
    tool_parameters = {name: set(row["parameters"]) for name, row in tool_metadata.items()}
    missing = sorted(REQUIRED_AGENT_TOOLS - tools)
    reality_target_profiles = validate_reality_target_profiles(tools)
    server_path = CODE_MAPS_DIR / "tools" / "mcp" / "server.py"
    server_text = server_path.read_text(encoding="utf-8", errors="replace")
    skill_path = CODE_MAPS_DIR / "SKILL.md"
    skill_text = skill_path.read_text(encoding="utf-8", errors="replace") if skill_path.exists() else ""
    developer_skill_path = CODE_MAPS_DIR / "docs" / "SAGE_DEVELOPER_SKILL.md"
    developer_skill_text = developer_skill_path.read_text(encoding="utf-8", errors="replace") if developer_skill_path.exists() else ""
    first_call_section = ""
    if "## First Call Order" in skill_text and "## MCP Tool Roles" in skill_text:
        first_call_section = skill_text.split("## First Call Order", 1)[1].split("## MCP Tool Roles", 1)[0]
    tool_roles_path = CODE_MAPS_DIR / "config" / "mcp_tool_roles.json"
    tool_roles = json.loads(tool_roles_path.read_text(encoding="utf-8")) if tool_roles_path.exists() else {}
    role_rows = tool_roles.get("tools", {}) if isinstance(tool_roles, dict) else {}
    role_names = {
        name: row.get("role")
        for name, row in role_rows.items()
        if isinstance(row, dict)
    }
    missing_role_rows = sorted(tools - set(role_names))
    stale_role_rows = sorted(set(role_names) - tools)
    invalid_role_rows = sorted(name for name, role in role_names.items() if role not in ALLOWED_TOOL_ROLES)
    missing_docstrings = sorted(name for name, docstring in tool_docstrings.items() if not docstring.strip())
    short_docstrings = sorted(name for name, docstring in tool_docstrings.items() if 0 < len(docstring.strip()) < 40)
    incomplete_role_metadata = sorted(
        name
        for name, row in role_rows.items()
        if isinstance(row, dict)
        and (
            not str(row.get("audience") or "").strip()
            or not str(row.get("default_use") or "").strip()
            or not isinstance(row.get("mutates"), bool)
            or not isinstance(row.get("heavy"), bool)
        )
    )
    mutating_primary = sorted(
        name
        for name, row in role_rows.items()
        if isinstance(row, dict)
        and row.get("role") == "primary_agent"
        and row.get("mutates") is True
        and row.get("safe_generated_artifact_write") is not True
        and row.get("mutates_repository") is not False
    )
    primary_tools = sorted(name for name, role in role_names.items() if role == "primary_agent")
    role_counts: dict[str, int] = {}
    for role in role_names.values():
        role_counts[str(role)] = role_counts.get(str(role), 0) + 1
    agent_surface_contract = _agent_surface_contract()
    target_validation_policy = (
        agent_surface_contract.get("target_repository_validation_policy", {})
        if isinstance(agent_surface_contract, dict)
        else {}
    )
    target_validation_tools = (
        target_validation_policy.get("validation_tools", [])
        if isinstance(target_validation_policy, dict)
        else []
    )
    target_validation_tool_names = {
        str(row.get("tool") or "") for row in target_validation_tools if isinstance(row, dict)
    }
    target_validation_profile_bindings = {
        str(row.get("tool") or ""): str(row.get("profile_id") or "")
        for row in target_validation_tools
        if isinstance(row, dict) and row.get("tool")
    }
    readiness_contract = agent_surface_contract.get("ai_agent_readiness", {}) if isinstance(agent_surface_contract, dict) else {}
    tool_context_profiles = (
        readiness_contract.get("tool_context_profiles", {})
        if isinstance(readiness_contract, dict)
        else {}
    )
    default_tool_context_profile = str(readiness_contract.get("default_tool_context_profile") or "")
    profile_validation = validate_mcp_tool_profiles(CODE_MAPS_DIR, set(tools))
    profile_projections = profile_validation.get("profiles", {})
    patch_next_actions = _patch_validation_next_actions()
    required_patch_next_actions = _patch_validation_emitted_next_actions(server_text)
    patch_next_action_missing = sorted(required_patch_next_actions - set(patch_next_actions))
    patch_next_action_unemitted = sorted(set(patch_next_actions) - required_patch_next_actions)
    patch_next_action_incomplete = sorted(
        action_id
        for action_id in required_patch_next_actions & set(patch_next_actions)
        if not isinstance(patch_next_actions.get(action_id), dict)
        or not str(patch_next_actions[action_id].get("instruction") or "").strip()
        or not str(patch_next_actions[action_id].get("agent_rule") or "").strip()
    )
    work_queue_policy = _work_queue_brief_policy()
    work_queue_policy_complete = (
        int(work_queue_policy.get("max_visible_items") or 0) > 0
        and str(work_queue_policy.get("omission_field") or "").strip()
        and str(work_queue_policy.get("agent_rule") or "").strip()
    )
    test_impact_policy = _test_impact_brief_policy()
    test_impact_policy_complete = (
        str(test_impact_policy.get("command_contract_projection") or "").strip()
        and int(test_impact_policy.get("max_contract_groups") or 0) > 0
        and int(test_impact_policy.get("example_commands_per_group") or 0) > 0
        and str(test_impact_policy.get("agent_rule") or "").strip()
    )
    state_flow_policy = _state_flow_brief_policy()
    state_flow_policy_complete = (
        int(state_flow_policy.get("sample_keys_limit") or 0) > 0
        and int(state_flow_policy.get("sample_targets_limit") or 0) > 0
        and str(state_flow_policy.get("max_items_semantics") or "").strip()
        and str(state_flow_policy.get("agent_rule") or "").strip()
    )
    supporting_context_policy = _supporting_context_brief_policy()
    supporting_context_titles = (
        supporting_context_policy.get("surface_titles")
        if isinstance(supporting_context_policy.get("surface_titles"), dict)
        else {}
    )
    supporting_context_actions = (
        supporting_context_policy.get("surface_actions")
        if isinstance(supporting_context_policy.get("surface_actions"), dict)
        else {}
    )
    supporting_context_empty_actions = (
        supporting_context_policy.get("empty_result_actions")
        if isinstance(supporting_context_policy.get("empty_result_actions"), dict)
        else {}
    )
    required_supporting_context_surfaces = {
        "blast_radius",
        "circular_dependencies",
        "clone_detector",
        "dead_code",
        "health_metrics",
        "hexagonal_bindings",
        "state_flow",
        "surgical_context",
        "ui_architecture",
    }
    supporting_context_policy_complete = (
        str(supporting_context_policy.get("default_title") or "").strip()
        and isinstance(supporting_context_policy.get("default_actions"), list)
        and all(str(item).strip() for item in supporting_context_policy.get("default_actions", []))
        and required_supporting_context_surfaces.issubset(set(supporting_context_titles))
        and (required_supporting_context_surfaces - {"surgical_context"}).issubset(set(supporting_context_actions))
        and "blast_radius" in supporting_context_empty_actions
    )
    confidence_policy = _confidence_brief_policy()
    confidence_actions = (
        confidence_policy.get("recommended_actions")
        if isinstance(confidence_policy.get("recommended_actions"), dict)
        else {}
    )
    confidence_emitted_action_keys = _confidence_emitted_action_keys(server_text)
    confidence_configured_action_keys = set(confidence_actions)
    confidence_runtime_action_literals = sorted(
        str(action)
        for action in confidence_actions.values()
        if str(action).strip() and str(action) in server_text
    )
    confidence_action_keys_missing_from_contract = sorted(confidence_emitted_action_keys - confidence_configured_action_keys)
    confidence_action_keys_unemitted_by_server = sorted(confidence_configured_action_keys - confidence_emitted_action_keys)
    confidence_policy_complete = (
        bool(confidence_emitted_action_keys)
        and not confidence_action_keys_missing_from_contract
        and not confidence_action_keys_unemitted_by_server
        and not confidence_runtime_action_literals
        and all(str(confidence_actions.get(key) or "").strip() for key in confidence_emitted_action_keys)
        and str(confidence_policy.get("missing_policy_action") or "").strip()
        and str(confidence_policy.get("agent_rule") or "").strip()
    )
    default_tool_profile = (
        tool_context_profiles.get(default_tool_context_profile, {})
        if isinstance(tool_context_profiles, dict)
        else {}
    )
    default_profile_roles = {
        str(role)
        for role in (default_tool_profile.get("roles", []) if isinstance(default_tool_profile, dict) else [])
        if str(role).strip()
    }
    default_profile_exclude_heavy = bool(default_tool_profile.get("exclude_heavy")) if isinstance(default_tool_profile, dict) else True
    try:
        default_profile_max_tools = int(default_tool_profile.get("max_tools") or 0)
    except (TypeError, ValueError):
        default_profile_max_tools = 0
    recommended_first_calls = (
        readiness_contract.get("recommended_first_calls", [])
        if isinstance(readiness_contract, dict)
        else []
    )
    recommended_first_calls = [
        str(tool)
        for tool in recommended_first_calls
        if str(tool).strip()
    ] if isinstance(recommended_first_calls, list) else []
    required_operation_tools = [
        str(tool)
        for tool in readiness_contract.get("required_operation_tools", [])
        if str(tool).strip()
    ] if isinstance(readiness_contract.get("required_operation_tools", []), list) else []
    operating_contract = (
        readiness_contract.get("agent_operating_contract", {})
        if isinstance(readiness_contract, dict)
        else {}
    )
    minimal_context_sequence = [
        str(tool)
        for tool in operating_contract.get("minimal_context_sequence", [])
        if str(tool).strip()
    ] if isinstance(operating_contract.get("minimal_context_sequence", []), list) else []
    human_approval_sequence = [
        str(tool)
        for tool in operating_contract.get("human_approval_sequence", [])
        if str(tool).strip()
    ] if isinstance(operating_contract.get("human_approval_sequence", []), list) else []
    target_skill_required_tools = sorted(
        set(recommended_first_calls)
        | set(required_operation_tools)
        | set(minimal_context_sequence)
        | set(human_approval_sequence)
    )
    target_skill_visible_tools = sorted(
        tool for tool in target_skill_required_tools if _is_target_skill_visible_tool(tool, role_rows)
    )
    missing_target_tools_from_skill = sorted(
        tool for tool in target_skill_visible_tools if not _mentions_tool(skill_text, tool)
    )
    forbidden_root_skill_tools = sorted(
        name
        for name, row in role_rows.items()
        if isinstance(row, dict)
        and _mentions_tool(skill_text, name)
        and not _is_target_skill_visible_tool(name, role_rows)
    )
    forbidden_first_call_roles = {"debug_provenance", "heavy_validation"}
    forbidden_first_calls = sorted(
        tool
        for tool in recommended_first_calls
        if role_names.get(tool) in forbidden_first_call_roles
    )
    missing_first_call_tools = sorted(
        tool
        for tool in recommended_first_calls
        if tool not in tools
    )
    default_target_context_tools = sorted(
        name
        for name, row in role_rows.items()
        if isinstance(row, dict)
        and row.get("role") in default_profile_roles
        and (not default_profile_exclude_heavy or row.get("heavy") is not True)
    )
    first_calls_outside_default_context = sorted(
        tool
        for tool in recommended_first_calls
        if tool in role_rows and tool not in default_target_context_tools
    )
    surface_taxonomy_report = _agent_surface_taxonomy_report()
    hitl_runbook_text = (CODE_MAPS_DIR / "docs" / "AI_AGENT_HITL_RUNBOOK.md").read_text(encoding="utf-8")
    normalized_hitl_runbook_text = " ".join(hitl_runbook_text.split())
    semantic_checks = [
        {
            "name": "search_symbols_returns_repo_relative_and_atlas_identity",
            "passed": '"repo_relative_path"' in server_text and '"atlas_node"' in server_text and "_file_context_from_atlas" in server_text,
            "details": "MCP symbol search must expose both target-repo edit path and SAGE graph identity.",
        },
        {
            "name": "search_symbols_brief_returns_scoped_inspection_targets",
            "passed": "_render_symbol_search_brief" in server_text
            and "target_ref" in server_text
            and "inspect_file(file_path=target_ref" in server_text
            and "Do not edit from search results alone." in server_text,
            "details": "Search results should be target selectors, not edit instructions; default brief must point agents to inspect_file with a scoped target_ref.",
        },
        {
            "name": "surgical_operation_packet_supports_llm_brief_projection",
            "passed": 'format: str = "brief"' in server_text and "format=json returns the canonical machine contract" in server_text and "render_surgical_operation_brief" in server_text and "brief_debug" in server_text,
            "details": "The surgical operation packet should stay one MCP tool, default to the target-repo brief, and preserve canonical JSON plus debug projections.",
        },
        {
            "name": "agent_validation_commands_expose_scope_contracts",
            "passed": "command_contract_summary_for_agent" in server_text
            and "target_repo_validation_policy" in server_text
            and "merge_review_validation_policy" in server_text
            and "python -B .\\\\tools\\\\validate_merge_intelligence_regression.py" not in server_text,
            "details": "Target-repository commands must expose execution/proof scope, while merge review uses MCP/target-repository validation policy without leaking SAGE self validators.",
        },
        {
            "name": "operator_packet_defaults_to_target_agent_projection",
            "passed": 'def get_operator_packet(refresh: bool = False, human_report: bool = False, projection: str = "agent", target_root: str = "", format: str = "brief")' in server_text
            and 'packet.get("target_repository_agent_surface")' in server_text
            and 'requested_projection in {"full", "operator", "platform", "debug"}' in server_text
            and "get_operator_packet(refresh?, human_report?, projection?, target_root?, format?)" in skill_text
            and "_render_operator_agent_surface_brief" in server_text
            and "one_shot_patch_ready: false" in server_text
            and "next_tool: " in server_text
            and "`target_root` reads isolated external-target output" in skill_text,
            "details": "Operator packet MCP access should default to the target-repository coding-agent Markdown/YAML surface and require explicit projection/format for machine or full platform status.",
        },
        {
            "name": "surgical_operation_packet_reads_external_target_outputs",
            "passed": "target_root: str = \"\"" in server_text and "_raw_dir_for_target" in server_text and "live_surface_priority_pack.json" in server_text,
            "details": "The surgical operation packet must be able to read isolated external-target outputs instead of only the SAGE workspace output.",
        },
        {
            "name": "primary_agent_analysis_tools_support_target_root_and_briefs",
            "passed": "def get_impact_radius(target_node: str, target_root: str = \"\", format: str = \"brief\", depth: int = 2)" in server_text
            and "depth-limited bounded sample" in server_text
            and "def get_test_impact(target_file: str, target_root: str = \"\", format: str = \"brief\")" in server_text
            and "def get_confidence_score(target_file: str, target_root: str = \"\", format: str = \"brief\")" in server_text
            and "def trace_upstream_cause(target_node: str, target_root: str = \"\", format: str = \"brief\")" in server_text
            and "_missing_target_artifact_brief" in server_text,
            "details": "Primary target-repo analysis tools must support isolated target_root artifacts, default to brief projection, and fail closed when required target artifacts are absent.",
        },
        {
            "name": "patch_and_signal_tools_support_target_root_isolation",
            "passed": "def validate_patch(" in server_text
            and "target_root: str = \"\"" in server_text
            and "actor_id: str = \"\"" in server_text
            and "def get_active_signals(" in server_text
            and "target_root: str = \"\"" in server_text
            and "_resolve_absolute_path_for_target" in server_text
            and "_render_patch_validation_brief" in server_text,
            "details": "Patch validation and active signals must be target-root aware so external target agents do not read or validate against SAGE source workspace state.",
        },
        {
            "name": "invalid_external_target_errors_are_agent_briefs",
            "passed": "_invalid_external_target_brief" in server_text
            and "return str(exc)" not in server_text
            and "return f\"Invalid external target root:" not in server_text,
            "details": "Invalid external target roots must return a structured fail-closed Markdown/YAML agent brief, not plain text or an implicit fallback to the current SAGE workspace.",
        },
        {
            "name": "external_target_analysis_can_return_surgical_brief",
            "passed": "include_brief: bool = True" in server_text and "\"surgical_packet\": surgical_packet" in server_text,
            "details": "External target analysis should optionally return the target-repository surgical brief in the same MCP response.",
        },
        {
            "name": "large_graph_tools_default_to_bounded_agent_summaries",
            "passed": "def get_state_flow(project: str = \"MAIN\", max_items: int = 20, full: bool = False, target_root: str = \"\", format: str = \"brief\")" in server_text
            and "def get_ui_architecture(component: str = \"\", max_items: int = 30, full: bool = False, target_root: str = \"\", format: str = \"brief\")" in server_text
            and "bounded_summary" in server_text
            and "full_data_hint" in server_text,
            "details": "MCP graph tools must default to MAIN-scoped bounded agent summaries and require explicit full=True for full artifacts.",
        },
        {
            "name": "supporting_context_tools_support_target_root_briefs",
            "passed": "def get_dead_code(path: str = \"\", target_root: str = \"\", format: str = \"brief\", max_items: int = 50)" in server_text
            and "def get_health_metrics(path: str = \"\", target_root: str = \"\", format: str = \"brief\")" in server_text
            and "def get_circular_dependencies(module: str = \"\", target_root: str = \"\", format: str = \"brief\", max_items: int = 20)" in server_text
            and "def get_blast_radius(symbol: str, target_root: str = \"\", format: str = \"brief\", max_items: int = 10)" in server_text
            and "def get_hexagonal_bindings(target_root: str = \"\", format: str = \"brief\", max_items: int = 20)" in server_text
            and "def check_module_integrity(module_path: str, target_root: str = \"\", format: str = \"brief\", max_items: int = 20)" in server_text
            and "def get_violation_work_queue(" in server_text
            and "_render_violation_work_queue_brief" in server_text
            and "_ensure_agent_artifact_chain_current" in server_text
            and "evidence_gate:" in server_text
            and "returned_work_items:" in server_text
            and "_work_queue_brief_policy" in server_text
            and "omitted_work_items" in json.dumps(work_queue_policy)
            and work_queue_policy_complete
            and "_test_impact_brief_policy" in server_text
            and "command_contract_summary_for_agent" in server_text
            and test_impact_policy_complete
            and "_state_flow_brief_policy" in server_text
            and "sample_keys_omitted" in server_text
            and "sample_targets_omitted" in server_text
            and "max_items_semantics" in server_text
            and state_flow_policy_complete
            and "refresh_sage_evidence_before_editing" in server_text
            and "why_it_matters" in server_text
            and "fix_strategy" in server_text
            and "inspect_first" in server_text
            and "_confidence_brief_policy" in server_text
            and "_confidence_recommended_action" in server_text
            and confidence_policy_complete
            and "inspect_upstream_candidates_before_local_patch" in server_text
            and "safe_to_apply" in server_text
            and "_patch_validation_next_action_contract" in server_text
            and bool(required_patch_next_actions)
            and not patch_next_action_missing
            and not patch_next_action_unemitted
            and not patch_next_action_incomplete
            and "run_listed_tests_before_finalizing" in server_text
            and "run_nearest_feature_or_package_validation" in server_text
            and "def simulate_change_impact(target_node: str, target_root: str = \"\", format: str = \"brief\")" in server_text
            and "def get_surgical_context(symbol: str, project: str = \"\", target_root: str = \"\", format: str = \"brief\")" in server_text
            and "def find_clones(symbol: str, target_root: str = \"\", format: str = \"brief\", max_items: int = 10)" in server_text
            and "_render_supporting_context_brief" in server_text
            and "_artifact_or_missing" in server_text,
            "details": {
                "summary": "Supporting-context tools must read isolated target artifacts, default to Markdown/YAML briefs, and fail closed when target artifacts are missing.",
                "patch_validation_emitted_next_actions": sorted(required_patch_next_actions),
                "patch_validation_next_action_missing": patch_next_action_missing,
                "patch_validation_next_action_unemitted": patch_next_action_unemitted,
                "patch_validation_next_action_incomplete": patch_next_action_incomplete,
                "work_queue_policy_complete": work_queue_policy_complete,
                "test_impact_policy_complete": test_impact_policy_complete,
                "confidence_policy_complete": confidence_policy_complete,
                "confidence_configured_action_keys": sorted(confidence_configured_action_keys),
                "confidence_emitted_action_keys": sorted(confidence_emitted_action_keys),
                "confidence_action_keys_missing_from_contract": confidence_action_keys_missing_from_contract,
                "confidence_action_keys_unemitted_by_server": confidence_action_keys_unemitted_by_server,
                "confidence_runtime_action_literals": confidence_runtime_action_literals,
                "state_flow_policy_complete": state_flow_policy_complete,
            },
        },
        {
            "name": "supporting_context_titles_and_actions_are_contract_driven",
            "passed": supporting_context_policy_complete
            and "_supporting_context_title" in server_text
            and "_supporting_context_actions" in server_text
            and "surface_actions = {" not in server_text,
            "details": {
                "path": "config/agent_surface_contract.json",
                "required_surfaces": sorted(required_supporting_context_surfaces),
                "configured_titles": sorted(supporting_context_titles),
                "configured_actions": sorted(supporting_context_actions),
                "configured_empty_actions": sorted(supporting_context_empty_actions),
                "policy_complete": supporting_context_policy_complete,
            },
        },
        {
            "name": "agent_surface_documents_english_directives_with_exact_evidence",
            "passed": "Default directive language should be English"
            in (CODE_MAPS_DIR / "tools" / "generate_agent_surface_quality_review.py").read_text(encoding="utf-8")
            and "Keep default target-repository brief instructions in English" in skill_text
            and "Preserve exact" in skill_text,
            "details": "Default agent briefs should use English for instructions while preserving exact target-repository paths, symbols, and evidence snippets.",
        },
        {
            "name": "merge_review_queue_is_human_approval_gated_agent_brief",
            "passed": "def get_merge_review_queue(action: str = \"Import With Review\", max_items: int = 5, target_root: str = \"\", format: str = \"brief\")" in server_text
            and "_render_merge_review_queue_brief" in server_text
            and "human_approval_required: true" in server_text
            and "mutation_allowed_by_this_packet: false" in server_text
            and "Do not copy dependency packages automatically from this packet." in server_text
            and "`get_merge_review_queue" in skill_text
            and "advisory only" in skill_text,
            "details": "Merge review must be a bounded target-agent brief and must never imply automatic merge/import permission.",
        },
        {
            "name": "mcp_tools_have_role_registry",
            "passed": tool_roles_path.exists()
            and not missing_role_rows
            and not stale_role_rows
            and not invalid_role_rows
            and not mutating_primary,
            "details": {
                "path": "config/mcp_tool_roles.json",
                "missing_role_rows": missing_role_rows,
                "stale_role_rows": stale_role_rows,
                "invalid_role_rows": invalid_role_rows,
                "mutating_primary": mutating_primary,
            },
        },
        {
            "name": "mcp_tools_have_agent_selectable_metadata",
            "passed": not missing_docstrings
            and not short_docstrings
            and not incomplete_role_metadata,
            "details": {
                "docstring_contract": "Every @mcp.tool needs a concise MCP-native description; role registry rows need audience/default_use/mutates/heavy.",
                "missing_docstrings": missing_docstrings,
                "short_docstrings": short_docstrings,
                "incomplete_role_metadata": incomplete_role_metadata,
            },
        },
        {
            "name": "agent_surface_taxonomy_separates_target_maintainer_and_corpus_contexts",
            "passed": surface_taxonomy_report.get("ok") is True,
            "details": surface_taxonomy_report,
        },
        {
            "name": "sage_self_target_profile_is_explicit_and_tool_complete",
            "passed": not reality_target_profiles.get("issues")
            and "refresh" in tool_parameters.get("run_external_target_analysis", set()),
            "details": {
                **reality_target_profiles,
                "run_external_target_analysis_parameters": sorted(
                    tool_parameters.get("run_external_target_analysis", set())
                ),
            },
        },
        {
            "name": "skill_documents_mcp_role_policy",
            "passed": "primary_agent" in skill_text
            and "debug_provenance" in skill_text
            and "mutating" in skill_text
            and "heavy_validation" in skill_text,
            "details": "SKILL.md must tell agents that MCP tools are role-classified.",
        },
        {
            "name": "skill_documents_supporting_context_profile_transition",
            "passed": _skill_declares_supporting_context_profile(skill_text),
            "details": "SKILL.md must state that supporting-context tools require target_repository_followup and that profile rejection does not prove capability absence.",
        },
        {
            "name": "skill_first_call_order_excludes_debug_and_heavy_defaults",
            "passed": first_call_section
            and "Optional target-repository support" in first_call_section
            and "Do not preload" in first_call_section
            and "`get_agent_contract`" not in first_call_section.split("Optional target-repository support", 1)[0]
            and "`get_nexora_agent_contract`" not in first_call_section.split("Optional target-repository support", 1)[0]
            and "`get_nexora_brief`" not in first_call_section.split("Optional target-repository support", 1)[0]
            and "`get_operator_packet`" not in first_call_section.split("Optional target-repository support", 1)[0]
            and "`get_release_proof`" not in first_call_section.split("Optional target-repository support", 1)[0]
            and "`get_surface_inventory`" not in first_call_section.split("Optional target-repository support", 1)[0]
            and "`get_capability_registry`" not in first_call_section.split("Optional target-repository support", 1)[0]
            and "`validate_workspace`" not in first_call_section.split("Optional target-repository support", 1)[0],
            "details": "Default first-call guidance must keep debug/provenance and heavy-validation tools out of ordinary target-repository coding context.",
        },
        {
            "name": "target_repository_skill_hides_sage_internal_tools",
            "passed": not forbidden_root_skill_tools and not missing_target_tools_from_skill,
            "details": {
                "path": "SKILL.md",
                "target_skill_required_tools": target_skill_visible_tools,
                "missing_target_tools_from_skill": missing_target_tools_from_skill,
                "forbidden_root_skill_tools": forbidden_root_skill_tools,
            },
        },
        {
            "name": "agent_surface_contract_first_calls_are_target_repo_safe",
            "passed": bool(recommended_first_calls)
            and not forbidden_first_calls
            and not missing_first_call_tools
            and "get_surgical_operation_packet" in recommended_first_calls
            and "validate_patch" in recommended_first_calls,
            "details": {
                "path": "config/agent_surface_contract.json",
                "recommended_first_calls": recommended_first_calls,
                "forbidden_first_call_roles": sorted(forbidden_first_call_roles),
                "forbidden_first_calls": forbidden_first_calls,
                "missing_first_call_tools": missing_first_call_tools,
            },
        },
        {
            "name": "agent_surface_contract_scopes_default_tool_window",
            "passed": bool(default_tool_context_profile)
            and default_tool_context_profile in tool_context_profiles
            and bool(default_profile_roles)
            and default_profile_max_tools > 0
            and len(default_target_context_tools) <= default_profile_max_tools
            and not first_calls_outside_default_context,
            "details": {
                "path": "config/agent_surface_contract.json",
                "profile": default_tool_context_profile,
                "roles": sorted(default_profile_roles),
                "exclude_heavy": default_profile_exclude_heavy,
                "max_tools": default_profile_max_tools,
                "default_tool_count": len(default_target_context_tools),
                "default_tools": default_target_context_tools,
                "first_calls_outside_default_context": first_calls_outside_default_context,
            },
        },
        {
            "name": "mcp_runtime_profiles_are_contract_derived_and_fail_closed",
            "passed": profile_validation.get("passed") is True
            and set(profile_projections[default_tool_context_profile]["visible_tools"]) == set(default_target_context_tools),
            "details": profile_validation,
        },
        {
            "name": "target_repository_validation_policy_uses_target_agent_tools",
            "passed": {"get_test_impact", "validate_patch", "get_violation_work_queue"}.issubset(target_validation_tool_names)
            and all(role_names.get(name) == "primary_agent" for name in target_validation_tool_names)
            and all(
                profile_id in profile_projections
                and tool_name in set(profile_projections[profile_id]["visible_tools"])
                for tool_name, profile_id in target_validation_profile_bindings.items()
            )
            and "SAGE self-development validators" in str(target_validation_policy.get("agent_rule") or ""),
            "details": {
                "path": "config/agent_surface_contract.json",
                "tools": sorted(target_validation_tool_names),
                "profile_bindings": target_validation_profile_bindings,
                "mode": target_validation_policy.get("mode"),
            },
        },
        {
            "name": "release_proof_surface_is_sage_self_provenance_not_target_repo_approval",
            "passed": role_names.get("get_release_proof") == "debug_provenance"
            and str((role_rows.get("get_release_proof") or {}).get("audience") or "") == "sage_developer_or_auditor"
            and "not target-repository release approval" in str((role_rows.get("get_release_proof") or {}).get("default_use") or "")
            and "SAGE self-release proof" in developer_skill_text
            and "not a target-repository release approval" in developer_skill_text
            and "SAGE self-release proof evidence" in server_text
            and "not target-repository release approval" in server_text,
            "details": "get_release_proof must remain a SAGE self/auditor provenance surface, not a primary target-repository coding or release-approval packet.",
        },
        {
            "name": "sage_internal_contract_tools_have_nexora_identity",
            "passed": "def get_agent_contract(" not in server_text
            and "get_agent_contract" not in role_names
            and "get_nexora_agent_contract" in tools
            and role_names.get("get_nexora_agent_contract") == "debug_provenance",
            "details": "SAGE operating-contract MCP tools must carry Nexora identity so they are not confused with target-repository coding tools.",
        },
        {
            "name": "response_and_handoff_tools_are_not_repository_evidence",
            "passed": role_names.get("get_agent_response_template") == "human_hitl"
            and role_names.get("validate_agent_response") == "human_hitl"
            and role_names.get("validate_agent_response_template") == "heavy_validation"
            and role_names.get("get_agent_response_ledger") == "human_hitl"
            and role_names.get("record_agent_response") == "mutating"
            and role_names.get("get_agent_handoff") == "debug_provenance"
            and "Response-contract tools shape or audit the agent answer; they do not provide" in skill_text
            and "target-repository evidence" in skill_text
            and "separate authority surface" in skill_text
            and "not part of the public target-repository" in skill_text
            and "These response-contract tools shape and audit the agent answer."
            in normalized_hitl_runbook_text
            and "not a richer substitute for the surgical target-repository context"
            in normalized_hitl_runbook_text,
            "details": "Agent response and MCP-less handoff tools are governance/transfer surfaces; they must not be treated as target-repository evidence or primary coding context.",
        }
    ]
    status = "PASS" if not missing and all(check["passed"] for check in semantic_checks) else "FAIL"
    return {
        "meta": {
            "kind": "mcp_agent_surface_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_mcp_agent_surface",
        },
        "summary": {
            "required_tools": len(REQUIRED_AGENT_TOOLS),
            "present_required_tools": len(REQUIRED_AGENT_TOOLS) - len(missing),
            "total_mcp_tools": len(tools),
            "missing_required_tools": missing,
            "missing_target_tools_from_skill": missing_target_tools_from_skill,
            "forbidden_root_skill_tools": forbidden_root_skill_tools,
            "status": status,
            "role_counts": role_counts,
            "primary_agent_tools": primary_tools,
        },
        "required_tools": sorted(REQUIRED_AGENT_TOOLS),
        "target_skill_required_tools": target_skill_visible_tools,
        "all_tools": sorted(tools),
        "tool_role_registry": {
            "path": "config/mcp_tool_roles.json",
            "roles": tool_roles.get("role_policy", {}) if isinstance(tool_roles, dict) else {},
            "missing_role_rows": missing_role_rows,
            "stale_role_rows": stale_role_rows,
            "invalid_role_rows": invalid_role_rows,
            "mutating_primary": mutating_primary,
        },
        "tool_context_profiles": profile_projections,
        "agent_surface_taxonomy": surface_taxonomy_report,
        "reality_target_profiles": reality_target_profiles,
        "skill_contract": {
            "path": "SKILL.md",
            "target_skill_required_tools": target_skill_visible_tools,
            "missing_target_tools_from_skill": missing_target_tools_from_skill,
            "forbidden_root_skill_tools": forbidden_root_skill_tools,
        },
        "semantic_checks": semantic_checks,
    }


def render_report(validation: dict[str, Any]) -> str:
    summary = validation.get("summary", {})
    lines = [
        "# MCP Agent Surface Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- registered_surface_tools: `{summary.get('required_tools')}`",
        f"- present_registered_surface_tools: `{summary.get('present_required_tools')}`",
        f"- total_mcp_tools: `{summary.get('total_mcp_tools')}`",
        f"- role_counts: `{summary.get('role_counts')}`",
        f"- missing_required_tools: `{summary.get('missing_required_tools')}`",
        f"- missing_target_tools_from_skill: `{summary.get('missing_target_tools_from_skill')}`",
        f"- forbidden_root_skill_tools: `{summary.get('forbidden_root_skill_tools')}`",
        "",
        "## Registered MCP Surface Tools",
        "",
    ]
    for tool in validation.get("required_tools", []):
        lines.append(f"- `{tool}`")
    lines.extend(["", "## Target Repository Skill Tools", ""])
    for tool in validation.get("target_skill_required_tools", []):
        lines.append(f"- `{tool}`")
    lines.extend(["", "## Primary Agent Tools", ""])
    for tool in summary.get("primary_agent_tools", []):
        lines.append(f"- `{tool}`")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    validation = build_validation()
    save_json_atomic(RAW_DIR / "mcp_agent_surface_validation.json", validation)
    save_text_atomic(REPORTS_DIR / "mcp_agent_surface_validation.md", render_report(validation))
    return validation


def main() -> int:
    validation = run()
    print(json.dumps(validation.get("summary", {}), ensure_ascii=False))
    return 0 if validation.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

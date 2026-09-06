from __future__ import annotations

import re
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR
from tools.core.json_io import load_json_file


COMMAND_CONTRACT_PATH = CONFIG_DIR / "cli_command_contract.json"
AGENT_SURFACE_CONTRACT_PATH = CONFIG_DIR / "agent_surface_contract.json"


def _normalize_step_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _extract_step_query(command: str) -> str:
    match = re.search(r"--step\s+(\"[^\"]+\"|'[^']+'|[^\s]+)", command)
    if not match:
        return ""
    return match.group(1).strip().strip("\"'")


def _step_invocation_contract(step_query: str) -> dict[str, Any] | None:
    registry = load_json_file(RAW_DIR / "pipeline_step_registry.json", {})
    steps = registry.get("steps", []) if isinstance(registry, dict) else []
    query = _normalize_step_slug(step_query)
    for step in steps:
        if not isinstance(step, dict):
            continue
        names = {
            _normalize_step_slug(str(step.get("name") or "")),
            _normalize_step_slug(str(step.get("slug") or "")),
        }
        if query in names:
            invocation = step.get("invocation_contract") if isinstance(step.get("invocation_contract"), dict) else {}
            explicit = invocation.get("explicit_step_closure") if isinstance(invocation.get("explicit_step_closure"), dict) else {}
            return {
                "step": step.get("name"),
                "slug": step.get("slug"),
                "execution_scope": f"explicit_step_{explicit.get('closure_tier') or 'unknown'}",
                "dependency_closure_count": explicit.get("dependency_closure_count"),
                "proof_boundary": explicit.get("freshness_claim") or "single_step_evidence_not_global_freshness",
                "agent_note": explicit.get("operator_warning") or "This step command runs its declared dependency closure.",
                "recommended_alternative": explicit.get("recommended_alternative") or "",
            }
    return None


def _agent_command_classifiers() -> list[dict[str, Any]]:
    contract = load_json_file(COMMAND_CONTRACT_PATH, {})
    rows = contract.get("agent_command_classifiers", []) if isinstance(contract, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _classifier_contract(text: str) -> dict[str, Any] | None:
    lower = str(text or "").lower()
    for row in _agent_command_classifiers():
        pattern = str(row.get("pattern") or "").strip()
        if not pattern:
            continue
        try:
            matched = re.search(pattern, lower) is not None
        except re.error:
            matched = False
        if not matched:
            continue
        return {
            "execution_scope": row.get("execution_scope") or "unknown",
            "proof_boundary": row.get("proof_boundary") or "command_scope_not_classified",
            "agent_note": row.get("agent_note") or "Run only if this command is relevant to the edited target files.",
            "recommended_alternative": row.get("recommended_alternative") or "",
        }
    return None


def command_contract_for_agent(command: str) -> dict[str, Any]:
    text = str(command or "").strip()
    lower = text.lower()
    base: dict[str, Any] = {
        "command": text,
        "execution_scope": "unknown",
        "proof_boundary": "command_scope_not_classified",
        "agent_note": "Run only if this command is relevant to the edited target files.",
        "recommended_alternative": "",
    }
    if not text:
        return base
    if " run " in f" {lower} " and "--step" in lower:
        step_query = _extract_step_query(text)
        contract = _step_invocation_contract(step_query)
        if contract:
            return {**base, **contract}
        return {
            **base,
            "execution_scope": "explicit_step_unknown",
            "proof_boundary": "single_step_evidence_not_global_freshness",
            "agent_note": "This appears to run one pipeline step, but the step was not found in the current registry.",
        }
    classifier = _classifier_contract(text)
    if classifier:
        return {**base, **classifier}
    return base


def command_contracts_for_agent(commands: list[Any], *, limit: int = 6) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for command in commands:
        text = str(command or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        contracts.append(command_contract_for_agent(text))
        if len(contracts) >= max(1, int(limit or 6)):
            break
    return contracts


def target_repo_validation_policy() -> dict[str, Any]:
    contract = load_json_file(AGENT_SURFACE_CONTRACT_PATH, {})
    policy = contract.get("target_repository_validation_policy", {}) if isinstance(contract, dict) else {}
    tools = policy.get("validation_tools", []) if isinstance(policy, dict) else []
    normalized_tools = [dict(row) for row in tools if isinstance(row, dict) and row.get("tool") and row.get("call")]
    return {
        "mode": str(policy.get("mode") or "missing_target_repository_validation_policy"),
        "validation_tools": normalized_tools,
        "completion_rule": str(policy.get("completion_rule") or ""),
        "agent_rule": str(policy.get("agent_rule") or ""),
        "inactive_mode": str(policy.get("inactive_mode") or "missing_inactive_validation_policy"),
        "inactive_tools": [
            dict(row)
            for row in policy.get("inactive_tools", [])
            if isinstance(row, dict)
        ],
        "inactive_completion_rule": str(policy.get("inactive_completion_rule") or ""),
    }


def target_repo_validation_tools() -> list[dict[str, Any]]:
    return target_repo_validation_policy()["validation_tools"]


def merge_review_validation_policy() -> dict[str, Any]:
    contract = load_json_file(AGENT_SURFACE_CONTRACT_PATH, {})
    policy = contract.get("merge_review_validation_policy", {}) if isinstance(contract, dict) else {}
    return {
        "mode": str(policy.get("mode") or "missing_merge_review_validation_policy"),
        "pre_approval_tools": [dict(row) for row in policy.get("pre_approval_tools", []) if isinstance(row, dict)],
        "post_approval_tools": [dict(row) for row in policy.get("post_approval_tools", []) if isinstance(row, dict)],
        "completion_rule": str(policy.get("completion_rule") or ""),
    }


def target_package_script_command(
    package_data: dict[str, Any],
    package_prefix: str,
    script: str,
) -> str:
    manager_spec = str(package_data.get("packageManager") or "").strip().lower()
    manager = manager_spec.split("@", 1)[0] if manager_spec else "npm"
    prefix = str(package_prefix or ".")
    if manager == "pnpm":
        return f'pnpm --dir "{prefix}" run {script}'
    if manager == "yarn":
        return f'yarn --cwd "{prefix}" run {script}'
    if manager == "bun":
        return f'bun --cwd "{prefix}" run {script}'
    return f'npm --prefix "{prefix}" run {script}'


def command_contract_summary_for_agent(
    commands: list[Any],
    *,
    max_groups: int = 4,
    examples_per_group: int = 1,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    seen_commands: set[str] = set()
    for command in commands:
        text = str(command or "").strip()
        if not text or text in seen_commands:
            continue
        seen_commands.add(text)
        contract = command_contract_for_agent(text)
        key = (
            str(contract.get("execution_scope") or ""),
            str(contract.get("proof_boundary") or ""),
            str(contract.get("agent_note") or ""),
            str(contract.get("recommended_alternative") or ""),
        )
        group = groups.setdefault(
            key,
            {
                "execution_scope": key[0],
                "proof_boundary": key[1],
                "agent_note": key[2],
                "recommended_alternative": key[3],
                "command_count": 0,
                "example_commands": [],
            },
        )
        group["command_count"] = int(group.get("command_count") or 0) + 1
        examples = group.get("example_commands") if isinstance(group.get("example_commands"), list) else []
        if len(examples) < max(1, int(examples_per_group or 1)):
            examples.append(text)
        group["example_commands"] = examples
    return list(groups.values())[: max(1, int(max_groups or 4))]

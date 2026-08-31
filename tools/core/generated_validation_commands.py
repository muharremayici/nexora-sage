from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR, DYNAMIC_CONFIG
from tools.core.json_io import load_json_file


CONTRACT_PATH = CONFIG_DIR / "generated_post_validation_commands_contract.json"


def generated_post_validation_contract(command_id: str) -> dict[str, Any]:
    contract = load_json_file(CONTRACT_PATH, {})
    rows = contract.get("commands", []) if isinstance(contract, dict) else []
    for row in rows:
        if isinstance(row, dict) and row.get("id") == command_id:
            command = str(row.get("command") or "").strip()
            if command:
                return dict(row)
    raise ValueError(f"unknown generated post-validation command: {command_id}")


def generated_post_validation_command(command_id: str) -> str:
    return str(generated_post_validation_contract(command_id)["command"])


def generated_mutation_post_validation() -> dict[str, Any]:
    """Resolve post-mutation validation for the active SAGE or target-repository reality."""
    target_override = DYNAMIC_CONFIG.get("_target_root_override")
    if isinstance(target_override, dict) and target_override.get("enabled"):
        from tools.core.agent_command_contracts import target_repo_validation_policy

        policy = target_repo_validation_policy()
        return {
            "mode": policy["mode"],
            "validation_tools": policy["validation_tools"],
            "completion_rule": policy["completion_rule"],
            "proof_boundary": "Validates the bounded target-repository change; SAGE self-development tests are not target proof.",
        }

    contract = generated_post_validation_contract("engine_contract_smoke")
    return {
        "mode": "command",
        "command": str(contract["command"]),
        "execution_scope": str(contract.get("execution_scope") or ""),
        "proof_boundary": str(contract.get("proof_boundary") or ""),
        "full_release_step_id": str(contract.get("full_release_step_id") or ""),
    }

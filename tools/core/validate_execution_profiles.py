from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached


CONTRACT_PATH = CONFIG_DIR / "cli_command_contract.json"
CODE_MAPS_DIR = CONFIG_DIR.parent


def validate_execution_contract() -> dict[str, Any]:
    contract = load_json_object_strict_cached(CONTRACT_PATH, label="CLI command contract")
    payload = contract.get("validation_execution")
    if not isinstance(payload, dict):
        raise ValueError("CLI validation execution contract is missing")
    return payload


def _profiles_by_id(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiles = contract.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("CLI validation execution profiles must be a list")
    result = {
        str(profile.get("id") or ""): profile
        for profile in profiles
        if isinstance(profile, dict) and str(profile.get("id") or "")
    }
    if not result:
        raise ValueError("CLI validation execution profiles are empty")
    return result


def resolve_validate_execution_profile(args: Any) -> dict[str, Any]:
    contract = validate_execution_contract()
    profiles = _profiles_by_id(contract)
    selected = [
        profile_id
        for profile_id in profiles
        if bool(getattr(args, f"validate_profile_{profile_id}", False))
    ]
    if len(selected) > 1:
        raise ValueError(f"Only one validation profile may be selected: {', '.join(sorted(selected))}")
    profile_id = selected[0] if selected else str(contract.get("default_profile_id") or "")
    profile = profiles.get(profile_id)
    if not isinstance(profile, dict):
        raise ValueError(f"Unknown default validation profile: {profile_id}")
    return profile


def validation_profile_options() -> list[dict[str, str]]:
    contract = validate_execution_contract()
    return [
        {
            "id": str(profile.get("id") or ""),
            "public_flag": str(profile.get("public_flag") or ""),
            "help": str(profile.get("operator_guidance") or ""),
        }
        for profile in _profiles_by_id(contract).values()
        if str(profile.get("public_flag") or "")
    ]


def validator_commands_for_profile(profile: dict[str, Any]) -> list[list[str]]:
    return validator_commands_for_set_id(str(profile.get("validator_set_id") or ""))


def validator_commands_for_set_id(set_id: str) -> list[list[str]]:
    contract = validate_execution_contract()
    validator_sets = contract.get("validator_sets")
    if not isinstance(validator_sets, dict):
        raise ValueError("CLI validation validator sets are missing")
    paths = validator_sets.get(set_id)
    if not isinstance(paths, list) or not paths:
        raise ValueError(f"CLI validation validator set is missing or empty: {set_id}")
    return [
        ["python", str(CODE_MAPS_DIR / Path(str(path)))]
        for path in paths
        if str(path).strip()
    ]


def doctor_validator_profile(system_scope: str) -> dict[str, Any]:
    contract = validate_execution_contract()
    doctor = contract.get("doctor")
    if not isinstance(doctor, dict):
        raise ValueError("CLI doctor execution contract is missing")
    profiles = doctor.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("CLI doctor execution profiles are missing")
    profile = profiles.get(str(system_scope or ""))
    if not isinstance(profile, dict):
        raise ValueError(f"CLI doctor execution profile is missing: {system_scope}")
    set_id = str(profile.get("validator_set_id") or "")
    if not set_id:
        raise ValueError(f"CLI doctor validator set is not declared: {system_scope}")
    return profile


def refresh_command_specs() -> list[dict[str, str]]:
    contract = validate_execution_contract()
    specs = contract.get("refresh_commands")
    if not isinstance(specs, list):
        raise ValueError("CLI validation refresh commands must be a list")
    return [
        {str(key): str(value) for key, value in spec.items()}
        for spec in specs
        if isinstance(spec, dict)
    ]


def optional_validator_options() -> list[dict[str, Any]]:
    contract = validate_execution_contract()
    options = contract.get("optional_validators")
    if not isinstance(options, list):
        raise ValueError("CLI optional validators must be a list")
    return [option for option in options if isinstance(option, dict)]


def remediation_options() -> dict[str, str]:
    contract = validate_execution_contract()
    payload = contract.get("remediation")
    if not isinstance(payload, dict):
        raise ValueError("CLI validation remediation contract is missing")
    return {str(key): str(value) for key, value in payload.items()}

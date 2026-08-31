from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR


RELEASE_PROOF_STEPS_CONTRACT_PATH = CONFIG_DIR / "release_proof_steps_contract.json"
RELEASE_PROOF_SCOPE_CONTRACT_PATH = CONFIG_DIR / "release_proof_scope_contract.json"
SAGE_RELEASE_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "sage-release-proof"


def load_release_proof_steps_contract(path: Path | None = None) -> dict[str, Any]:
    target = Path(path or RELEASE_PROOF_STEPS_CONTRACT_PATH)
    if not target.exists():
        raise FileNotFoundError(f"Release proof steps contract not found: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Release proof steps contract is not valid JSON: {target}:{exc.lineno}:{exc.colno}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Release proof steps contract root must be an object: {target}")
    return payload


def load_release_proof_scope_contract(path: Path | None = None) -> dict[str, Any]:
    target = Path(path or RELEASE_PROOF_SCOPE_CONTRACT_PATH)
    if not target.exists():
        raise FileNotFoundError(f"Release proof scope contract not found: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Release proof scope contract is not valid JSON: {target}:{exc.lineno}:{exc.colno}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Release proof scope contract root must be an object: {target}")
    return payload


def release_proof_step_scope_map(path: Path | None = None) -> dict[str, dict[str, Any]]:
    contract = load_release_proof_scope_contract(path)
    resolved: dict[str, dict[str, Any]] = {}
    domains = contract.get("domains", []) if isinstance(contract, dict) else []
    for domain in domains if isinstance(domains, list) else []:
        if not isinstance(domain, dict):
            continue
        domain_id = str(domain.get("id") or "").strip()
        cadence = str(domain.get("execution_cadence") or "").strip()
        evidence_role = str(domain.get("evidence_role") or "").strip()
        for raw_step_id in domain.get("step_ids", []) if isinstance(domain.get("step_ids"), list) else []:
            step_id = str(raw_step_id).strip()
            if not step_id:
                continue
            if step_id in resolved:
                raise ValueError(f"Release proof step has duplicate scope classification: {step_id}")
            resolved[step_id] = {
                "proof_domain": domain_id,
                "execution_cadence": cadence,
                "evidence_role": evidence_role,
            }

    readiness = contract.get("step_readiness_authority", {})
    default_authority = readiness.get("default", {}) if isinstance(readiness, dict) else {}
    overrides = readiness.get("overrides", {}) if isinstance(readiness, dict) else {}
    if (
        not isinstance(default_authority, dict)
        or not isinstance(default_authority.get("blocks_machine_release"), bool)
        or not isinstance(default_authority.get("blocks_public_release"), bool)
        or not str(default_authority.get("reason") or "").strip()
        or not isinstance(overrides, dict)
    ):
        raise ValueError("Release proof readiness authority defaults are incomplete")
    unknown_overrides = sorted(set(str(value) for value in overrides) - set(resolved))
    if unknown_overrides:
        raise ValueError(
            f"Release proof readiness authority overrides unknown steps: {unknown_overrides}"
        )
    for step_id, override in overrides.items():
        if (
            not isinstance(override, dict)
            or not isinstance(override.get("blocks_machine_release"), bool)
            or not isinstance(override.get("blocks_public_release"), bool)
            or not str(override.get("reason") or "").strip()
        ):
            raise ValueError(
                f"Release proof readiness authority override is incomplete: {step_id}"
            )
    for step_id, scope in resolved.items():
        authority = overrides.get(step_id, default_authority)
        scope["blocks_machine_release"] = authority["blocks_machine_release"]
        scope["blocks_public_release"] = authority["blocks_public_release"]
        scope["readiness_authority_reason"] = str(authority["reason"]).strip()
    return resolved


def _resolve_symbolic_path(value: str) -> str:
    if value == "${python}":
        return sys.executable
    if value.startswith("${code_maps}/"):
        return str(CODE_MAPS_DIR / value.removeprefix("${code_maps}/"))
    if value.startswith("${raw}/"):
        return str(RAW_DIR / value.removeprefix("${raw}/"))
    if value.startswith("${release_runtime}/"):
        return str(SAGE_RELEASE_RUNTIME_ROOT / value.removeprefix("${release_runtime}/"))
    return value


def _resolve_raw_artifact(value: Any) -> Path | None:
    if not value:
        return None
    resolved = _resolve_symbolic_path(str(value))
    return Path(resolved)


def load_release_proof_runtime_defaults(path: Path | None = None) -> dict[str, Any]:
    contract = load_release_proof_steps_contract(path)
    defaults = contract.get("runtime_defaults", {}) if isinstance(contract, dict) else {}
    return defaults if isinstance(defaults, dict) else {}


def load_release_proof_key_artifacts(path: Path | None = None) -> list[Path]:
    contract = load_release_proof_steps_contract(path)
    rows = contract.get("artifact_snapshot_paths", []) if isinstance(contract, dict) else []
    return [Path(_resolve_symbolic_path(str(item))) for item in rows if str(item).strip()]


def load_release_proof_provenance_embed_targets(path: Path | None = None) -> list[Path]:
    contract = load_release_proof_steps_contract(path)
    rows = contract.get("provenance_embed_targets", []) if isinstance(contract, dict) else []
    return [Path(_resolve_symbolic_path(str(item))) for item in rows if str(item).strip()]


def load_release_proof_steps(path: Path | None = None) -> list[dict[str, Any]]:
    contract = load_release_proof_steps_contract(path)
    rows = contract.get("steps", []) if isinstance(contract, dict) else []
    steps: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        step = dict(row)
        command = step.get("command", [])
        if isinstance(command, list):
            step["command"] = [_resolve_symbolic_path(str(item)) for item in command]
        else:
            step["command"] = []
        raw_artifact = _resolve_raw_artifact(step.get("raw_artifact"))
        if raw_artifact is not None:
            step["raw_artifact"] = raw_artifact
        runtime_directories = step.get("runtime_directories", [])
        step["runtime_directories"] = (
            [
                Path(_resolve_symbolic_path(str(item)))
                for item in runtime_directories
                if str(item).strip()
            ]
            if isinstance(runtime_directories, list)
            else []
        )
        depends_on = step.get("depends_on", [])
        step["depends_on"] = [str(item) for item in depends_on] if isinstance(depends_on, list) else []
        step["required"] = bool(step.get("required", True))
        steps.append(step)
    return steps


def load_release_proof_step_ids(path: Path | None = None) -> set[str]:
    return {str(step.get("id") or "") for step in load_release_proof_steps(path) if step.get("id")}


def load_release_proof_artifact_requiredness(path: Path | None = None) -> dict[str, bool]:
    requiredness: dict[str, bool] = {}
    for step in load_release_proof_steps(path):
        artifact = step.get("raw_artifact")
        if not isinstance(artifact, Path):
            continue
        name = artifact.name
        required = bool(step.get("required"))
        if name in requiredness and requiredness[name] != required:
            raise ValueError(f"Conflicting release-proof requiredness for artifact: {name}")
        requiredness[name] = required
    return requiredness


def load_release_proof_command_names(path: Path | None = None) -> set[str]:
    names: set[str] = set()
    for step in load_release_proof_steps(path):
        command = step.get("command", [])
        if not isinstance(command, list):
            continue
        for part in command:
            value = str(part)
            if not value:
                continue
            names.add(value)
            names.add(Path(value).name)
    return names

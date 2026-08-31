from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT_PATH = ROOT / "config" / "runtime_config_compiler_contract.json"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def compiler_identity_contract(contract_path: Path = DEFAULT_CONTRACT_PATH) -> dict:
    contract = _load_json(contract_path)
    identity = contract.get("identity") if isinstance(contract, dict) else None
    validation = contract.get("validation") if isinstance(contract, dict) else None
    if contract.get("_meta", {}).get("kind") != "nexora.runtime_config_compiler_contract":
        raise ValueError("Runtime config compiler contract kind is missing or invalid.")
    if not isinstance(identity, dict) or not isinstance(validation, dict):
        raise ValueError("Runtime config compiler identity/validation contract is missing.")
    required = {str(item) for item in validation.get("required_identity_fields", []) if str(item)}
    if not required or not required.issubset(identity):
        raise ValueError("Runtime config compiler identity contract is incomplete.")
    if identity.get("algorithm") != "sha256_json_canonical_v1":
        raise ValueError("Unsupported runtime config fingerprint algorithm.")
    required_provenance = {
        str(item) for item in validation.get("required_provenance_fields", []) if str(item)
    }
    provenance_fields = identity.get("provenance_fields")
    if (
        not required_provenance
        or not isinstance(provenance_fields, dict)
        or not required_provenance.issubset(provenance_fields)
        or any(not str(provenance_fields.get(item) or "") for item in required_provenance)
        or len({str(provenance_fields[item]) for item in required_provenance}) != len(required_provenance)
    ):
        raise ValueError("Runtime config provenance field ownership is incomplete or ambiguous.")
    return contract


def _canonical_json_fingerprint(payload: object) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _baseline_identity_payload(runtime_config: dict, contract: dict) -> dict:
    keys = [str(item) for item in contract["identity"].get("baseline_owned_keys", []) if str(item)]
    if not keys:
        raise ValueError("Runtime config compiler baseline ownership is empty.")
    return {key: deepcopy(runtime_config[key]) for key in keys if key in runtime_config}


def runtime_config_content_identity(
    discovery: dict,
    overrides: dict,
    runtime_config: dict,
    *,
    contract_path: Path = DEFAULT_CONTRACT_PATH,
) -> dict[str, str]:
    contract = compiler_identity_contract(contract_path)
    fields = contract["identity"].get("provenance_fields", {})
    source_field = str(fields.get("source_fingerprint") or "")
    baseline_field = str(fields.get("baseline_fingerprint") or "")
    if not source_field or not baseline_field:
        raise ValueError("Runtime config provenance field ownership is incomplete.")
    return {
        source_field: _canonical_json_fingerprint({"discovery": discovery, "overrides": overrides}),
        baseline_field: _canonical_json_fingerprint(_baseline_identity_payload(runtime_config, contract)),
    }


def runtime_config_needs_compile(
    discovery_path: Path,
    overrides_path: Path,
    config_path: Path,
    *,
    contract_path: Path = DEFAULT_CONTRACT_PATH,
) -> bool:
    contract = compiler_identity_contract(contract_path)
    declared_inputs = [str(item) for item in contract["identity"].get("source_inputs", [])]
    actual_inputs = [discovery_path.name, overrides_path.name]
    if declared_inputs != actual_inputs:
        raise ValueError(
            f"Runtime config source inputs do not match compiler contract: "
            f"declared={declared_inputs}, actual={actual_inputs}"
        )
    if not config_path.exists():
        return discovery_path.exists() or overrides_path.exists()
    discovery = _load_json(discovery_path)
    overrides = _load_json(overrides_path)
    runtime_config = _load_json(config_path)
    expected = runtime_config_content_identity(
        discovery,
        overrides,
        runtime_config,
        contract_path=contract_path,
    )
    provenance = runtime_config.get("_provenance")
    if not isinstance(provenance, dict):
        return True
    return any(provenance.get(field) != fingerprint for field, fingerprint in expected.items())

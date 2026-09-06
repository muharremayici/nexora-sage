from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.artifact_validator import validate_against_schema
from tools.core.artifact_registry import artifact_metadata, artifact_path_for_storage_root
from tools.core.atlas_integrity import validate_atlas_commit
from tools.core.config import CONFIG_DIR, RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_file, load_json_object_strict, load_raw_artifact_path


CONTRACT_PATH = CONFIG_DIR / "analysis_snapshot_lineage_contract.json"


def payload_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contract() -> dict[str, Any]:
    return load_json_object_strict(CONTRACT_PATH, label="analysis snapshot lineage contract")


def receipt_path(raw_dir: Path, artifact_id: str) -> Path:
    artifact_contract = _contract().get("artifacts", {}).get(artifact_id)
    if not isinstance(artifact_contract, dict):
        raise ValueError(f"analysis snapshot lineage artifact is not registered: {artifact_id}")
    receipt_artifact_id = str(artifact_contract.get("receipt_artifact_id") or "")
    registry = artifact_metadata()
    if receipt_artifact_id not in registry:
        raise ValueError(f"analysis snapshot lineage receipt is not in artifact registry: {receipt_artifact_id}")
    return artifact_path_for_storage_root(raw_dir, receipt_artifact_id)


def load_atlas_commit(raw_dir: Path) -> dict[str, Any]:
    path = raw_dir / "atlas_commit.json"
    if raw_dir.resolve() == RAW_DIR.resolve():
        from tools.core.artifact_store import STORE

        payload = STORE.load_raw("atlas_commit", {})
    elif raw_dir.name == ".raw":
        payload = load_raw_artifact_path(path, {})
    else:
        payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def _load_atlas_commit(raw_dir: Path) -> dict[str, Any]:
    """Backward-compatible private alias for older callers and tests."""

    return load_atlas_commit(raw_dir)


def load_lineage_receipt(raw_dir: Path, artifact_id: str) -> dict[str, Any]:
    path = receipt_path(raw_dir, artifact_id)
    if raw_dir.resolve() == RAW_DIR.resolve():
        from tools.core.artifact_store import STORE

        receipt_artifact_id = str(_contract()["artifacts"][artifact_id]["receipt_artifact_id"])
        payload = STORE.load_raw(receipt_artifact_id, {})
    elif raw_dir.name == ".raw":
        payload = load_raw_artifact_path(path, {})
    else:
        payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def validate_lineage_receipt(receipt: Any) -> list[str]:
    contract = _contract()
    schema_path = Path(__file__).resolve().parents[2] / str(contract["receipt"]["schema"])
    return validate_against_schema(schema_path, "analysis_snapshot_lineage", receipt)


def receipt_digest_binding(
    *,
    raw_dir: Path,
    artifact_id: str,
    artifact_sha256: str,
    expected_snapshot_id: str,
) -> tuple[str, str | None, list[str]]:
    receipt = load_lineage_receipt(raw_dir, artifact_id)
    errors = validate_lineage_receipt(receipt) if receipt else ["missing_lineage_receipt"]
    observed_snapshot = receipt.get("atlas_snapshot_id") if isinstance(receipt, dict) else None
    if errors:
        return "UNAVAILABLE", observed_snapshot if isinstance(observed_snapshot, str) else None, errors
    if receipt.get("status") != _contract()["receipt"]["complete_status"]:
        return "UNAVAILABLE", observed_snapshot if isinstance(observed_snapshot, str) else None, list(receipt.get("errors") or ["lineage_receipt_blocked"])
    if receipt.get("artifact_id") != artifact_id:
        return "MISMATCH", observed_snapshot if isinstance(observed_snapshot, str) else None, ["artifact_id_mismatch"]
    if not artifact_sha256:
        return "UNAVAILABLE", observed_snapshot if isinstance(observed_snapshot, str) else None, ["artifact_digest_unavailable"]
    if receipt.get("artifact_sha256") != artifact_sha256:
        return "MISMATCH", observed_snapshot if isinstance(observed_snapshot, str) else None, ["artifact_content_hash_mismatch"]
    if observed_snapshot != expected_snapshot_id:
        return "MISMATCH", observed_snapshot if isinstance(observed_snapshot, str) else None, ["atlas_snapshot_id_mismatch"]
    return "BOUND", str(observed_snapshot), []


def receipt_binding(
    *,
    raw_dir: Path,
    artifact_id: str,
    artifact_payload: Any,
    expected_snapshot_id: str,
) -> tuple[str, str | None, list[str]]:
    return receipt_digest_binding(
        raw_dir=raw_dir,
        artifact_id=artifact_id,
        artifact_sha256=payload_sha256(artifact_payload),
        expected_snapshot_id=expected_snapshot_id,
    )


def evaluate_snapshot_bound_inputs(
    *,
    contract_path: Path,
    raw_dir: Path,
    expected_snapshot_id: str,
    payloads: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = load_json_object_strict(contract_path, label=f"snapshot input contract {contract_path.name}")
    rows: list[dict[str, Any]] = []
    usable: dict[str, Any] = {}
    blocked: list[str] = []
    omitted: list[str] = []
    forbidden = [
        str(artifact_id)
        for artifact_id in contract.get("authority", {}).get("forbidden_artifacts", [])
        if payloads.get(str(artifact_id)) is not None
    ]

    for artifact_id, input_contract in contract.get("inputs", {}).items():
        if not isinstance(input_contract, dict):
            continue
        artifact_id = str(artifact_id)
        required = bool(input_contract.get("required"))
        payload = payloads.get(artifact_id)
        binding = "UNAVAILABLE"
        observed_snapshot = None
        errors = ["artifact_missing"]
        if payload is not None and expected_snapshot_id:
            try:
                binding, observed_snapshot, errors = receipt_binding(
                    raw_dir=raw_dir,
                    artifact_id=artifact_id,
                    artifact_payload=payload,
                    expected_snapshot_id=expected_snapshot_id,
                )
            except ValueError as exc:
                errors = [f"lineage_contract_unavailable:{exc}"]
        if binding == "BOUND":
            usable[artifact_id] = payload
        elif required:
            blocked.append(artifact_id)
        else:
            omitted.append(artifact_id)
        rows.append(
            {
                "artifact_id": artifact_id,
                "required": required,
                "decision_use": list(input_contract.get("decision_use") or []),
                "snapshot_binding": binding,
                "bound_snapshot_id": observed_snapshot,
                "errors": sorted(set(str(error) for error in errors)),
                "disposition": "USED" if binding == "BOUND" else ("BLOCKED" if required else "OMITTED"),
            }
        )

    status = "BLOCKED" if blocked or forbidden else ("PARTIAL_CONTEXT" if omitted else "PASS")
    return (
        {
            "status": status,
            "expected_snapshot_id": expected_snapshot_id or None,
            "blocked_inputs": blocked,
            "omitted_inputs": omitted,
            "forbidden_inputs": forbidden,
            "inputs": rows,
            "claim_boundary": str(contract.get("authority", {}).get("rule") or ""),
        },
        usable,
    )


def write_lineage_receipt(
    *,
    artifact_id: str,
    producer: str,
    artifact_payload: Any,
    atlas: dict[str, Any],
    atlas_commit: dict[str, Any],
    dependency_payloads: dict[str, Any] | None = None,
    raw_dir: Path = RAW_DIR,
) -> dict[str, Any]:
    contract = _contract()
    artifact_contract = contract.get("artifacts", {}).get(artifact_id)
    if not isinstance(artifact_contract, dict):
        raise ValueError(f"analysis snapshot lineage artifact is not registered: {artifact_id}")
    expected_producer = str(artifact_contract.get("producer") or "")
    if producer != expected_producer:
        raise ValueError(f"analysis snapshot lineage producer mismatch for {artifact_id}: {producer!r} != {expected_producer!r}")

    errors: list[str] = []
    atlas_checks = validate_atlas_commit(atlas, atlas_commit) if isinstance(atlas, dict) and isinstance(atlas_commit, dict) else []
    atlas_valid = bool(atlas_checks) and all(bool(row.get("passed")) for row in atlas_checks)
    snapshot_id = str(atlas_commit.get("snapshot_id") or "") if atlas_valid else ""
    if not atlas_valid:
        errors.append("atlas_commit_invalid_or_unavailable")

    dependency_payloads = dependency_payloads or {}
    dependency_rows: list[dict[str, Any]] = []
    for dependency_id in artifact_contract.get("lineage_dependencies", []):
        dependency_id = str(dependency_id)
        dependency_payload = dependency_payloads.get(dependency_id)
        dependency_hash = payload_sha256(dependency_payload)
        binding, _, binding_errors = receipt_binding(
            raw_dir=raw_dir,
            artifact_id=dependency_id,
            artifact_payload=dependency_payload,
            expected_snapshot_id=snapshot_id,
        ) if snapshot_id and dependency_id in dependency_payloads else ("UNAVAILABLE", None, ["dependency_payload_missing"])
        dependency_rows.append(
            {
                "artifact_id": dependency_id,
                "artifact_sha256": dependency_hash,
                "snapshot_binding": binding,
            }
        )
        errors.extend(f"dependency:{dependency_id}:{error}" for error in binding_errors)

    complete_status = str(contract["receipt"]["complete_status"])
    blocked_status = str(contract["receipt"]["blocked_status"])
    receipt = {
        "meta": {
            "kind": "analysis_snapshot_lineage",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "artifact_id": artifact_id,
        "producer": producer,
        "status": complete_status if not errors else blocked_status,
        "atlas_snapshot_id": snapshot_id or None,
        "artifact_sha256": payload_sha256(artifact_payload),
        "atlas_sha256": str(atlas_commit.get("atlas_sha256") or "") or None,
        "dependencies": dependency_rows,
        "errors": sorted(set(errors)),
    }
    schema_errors = validate_lineage_receipt(receipt)
    if schema_errors:
        raise ValueError("analysis snapshot lineage receipt failed schema validation: " + "; ".join(schema_errors))
    save_json_atomic(receipt_path(raw_dir, artifact_id), receipt)
    return receipt


def write_current_atlas_lineage(
    *,
    artifact_id: str,
    producer: str,
    artifact_payload: Any,
    atlas: dict[str, Any],
    dependency_payloads: dict[str, Any] | None = None,
    raw_dir: Path = RAW_DIR,
) -> dict[str, Any]:
    atlas_commit = load_atlas_commit(raw_dir)
    return write_lineage_receipt(
        artifact_id=artifact_id,
        producer=producer,
        artifact_payload=artifact_payload,
        atlas=atlas,
        atlas_commit=atlas_commit if isinstance(atlas_commit, dict) else {},
        dependency_payloads=dependency_payloads,
        raw_dir=raw_dir,
    )

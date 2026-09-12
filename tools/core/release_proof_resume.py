"""Exact-identity interruption resume for the existing release-proof DAG."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_file
from tools.core.release_proof_steps import load_release_proof_scope_contract


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def valid_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _sha256_path_set(paths: list[Path]) -> str:
    if any(not path.is_file() for path in paths):
        return ""
    digest = hashlib.sha256()
    try:
        for path in sorted(paths, key=lambda item: item.as_posix()):
            digest.update(path.relative_to(CODE_MAPS_DIR).as_posix().encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    except (OSError, ValueError):
        return ""
    return digest.hexdigest()


def _reuse_policy() -> dict[str, Any]:
    contract = load_release_proof_scope_contract()
    policy = contract.get("evidence_reuse")
    if not isinstance(policy, dict):
        raise ValueError("Release-proof evidence reuse policy is missing")
    return policy


def _resolve_contract_path(value: Any) -> Path:
    text = str(value or "")
    if not text.startswith("${code_maps}/"):
        raise ValueError("Release-proof checkpoint path must stay under ${code_maps}")
    root = CODE_MAPS_DIR.resolve()
    candidate = (root / text.removeprefix("${code_maps}/")).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Release-proof checkpoint path escapes the SAGE workspace")
    return candidate


def checkpoint_path() -> Path:
    checkpoint = _reuse_policy().get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise ValueError("Release-proof checkpoint policy is missing")
    return _resolve_contract_path(checkpoint.get("path"))


def lock_path() -> Path:
    checkpoint = _reuse_policy().get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise ValueError("Release-proof checkpoint policy is missing")
    return _resolve_contract_path(checkpoint.get("lock_path"))


def environment_sha256() -> str:
    return canonical_sha256(
        {
            "executable": sys.executable,
            "python_version": sys.version,
            "platform": sys.platform,
        }
    )


def policy_sha256() -> str:
    return _sha256_path_set(
        [
            CODE_MAPS_DIR / "config" / "release_proof_scope_contract.json",
            CODE_MAPS_DIR / "config" / "release_proof_steps_contract.json",
            CODE_MAPS_DIR / "config" / "pipeline_execution_policy.json",
        ]
    )


def build_reuse_context() -> dict[str, Any]:
    policy = _reuse_policy()
    fields = policy.get("identity_fields")
    checkpoint = policy.get("checkpoint")
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) != len(set(map(str, fields)))
        or not isinstance(checkpoint, dict)
    ):
        raise ValueError("Release-proof resume policy is incomplete")
    return {
        "identity_fields": [str(field) for field in fields],
        "fresh_domains": [str(item) for item in policy.get("always_fresh_domains", [])],
        "execution_mode": str(policy.get("execution_mode") or ""),
        "automatic": checkpoint.get("automatic") is True,
        "policy_sha256": policy_sha256(),
        "environment_sha256": environment_sha256(),
    }


def output_artifact_sha256(raw_artifact: Any) -> str:
    if not isinstance(raw_artifact, Path):
        return ""
    try:
        managed_raw = raw_artifact.resolve().parent == RAW_DIR.resolve()
    except OSError:
        managed_raw = False
    if managed_raw:
        try:
            from tools.core.artifact_store import STORE

            metadata = STORE.raw_metadata(raw_artifact.stem)
        except Exception as exc:
            try:
                from tools.core.honesty_telemetry import record_honesty_event

                record_honesty_event(
                    component="release_proof_resume",
                    category="caught_error",
                    operation="read_output_artifact_metadata",
                    subject=raw_artifact.stem,
                    severity="warning",
                    reason="Managed output identity metadata could not be read.",
                    fallback="identity_incomplete_force_step_execution",
                    claim_impact="resume_disabled_for_step_release_evidence_unchanged",
                    exception=exc,
                )
            except Exception:
                print("[release-proof] WARNING resume artifact metadata telemetry unavailable")
            return ""
        payload_sha = str(metadata.get("payload_sha") or "") if isinstance(metadata, dict) else ""
        if valid_sha256(payload_sha):
            try:
                return canonical_sha256(
                    {
                        "truth_source": metadata.get("truth_source"),
                        "payload_sha": payload_sha,
                        "payload_bytes": int(metadata.get("payload_bytes") or 0),
                        "storage_mode": str(metadata.get("storage_mode") or ""),
                        "generation_id": str(metadata.get("generation_id") or ""),
                        "part_count": int(metadata.get("part_count") or 0),
                    }
                )
            except (TypeError, ValueError):
                return ""
    file_sha = _sha256_file(raw_artifact)
    if not valid_sha256(file_sha):
        return ""
    try:
        size_bytes = raw_artifact.stat().st_size
    except OSError:
        return ""
    return canonical_sha256(
        {
            "truth_source": "artifact_file",
            "sha256": file_sha,
            "size_bytes": size_bytes,
        }
    )


def run_identity(
    *,
    source_fingerprint: str,
    proof_scope: str,
    selected_step_ids: list[str],
    ordered_steps: list[dict[str, Any]],
    reuse_context_value: dict[str, Any] | None = None,
) -> dict[str, str]:
    context = reuse_context_value or build_reuse_context()
    return {
        "source_sha256": source_fingerprint if valid_sha256(source_fingerprint) else "",
        "policy_sha256": str(context.get("policy_sha256") or ""),
        "environment_sha256": str(context.get("environment_sha256") or ""),
        "selection_sha256": canonical_sha256(
            {
                "proof_scope": proof_scope,
                "selected_step_ids": selected_step_ids,
                "ordered_step_ids": [str(step.get("id") or "") for step in ordered_steps],
            }
        ),
    }


def run_identity_complete(identity: Any) -> bool:
    return (
        isinstance(identity, dict)
        and set(identity) == {
            "source_sha256",
            "policy_sha256",
            "environment_sha256",
            "selection_sha256",
        }
        and all(valid_sha256(value) for value in identity.values())
    )


def step_reuse_identity(
    step: dict[str, Any],
    *,
    scope_contract: dict[str, Any],
    proof_scope: str,
    selected_step_ids: list[str],
    source_fingerprint: str,
    dependency_results: dict[str, dict[str, Any]],
    reuse_context_value: dict[str, Any] | None = None,
) -> dict[str, str]:
    context = reuse_context_value or build_reuse_context()
    fields = context.get("identity_fields")
    if not isinstance(fields, list) or len(fields) != len(set(map(str, fields))):
        raise ValueError("Release-proof reuse identity fields are invalid")
    dependencies: dict[str, str] = {}
    dependency_identity_complete = True
    for dependency in [str(item) for item in step.get("depends_on", []) or []]:
        evidence = str(dependency_results.get(dependency, {}).get("evidence_sha256") or "")
        dependencies[dependency] = evidence
        dependency_identity_complete = dependency_identity_complete and valid_sha256(evidence)
    source_valid = valid_sha256(source_fingerprint)
    input_sha256 = canonical_sha256(dependencies) if dependency_identity_complete else ""
    identity = {
        "source_dependency_sha256": (
            canonical_sha256(
                {
                    "source_sha256": source_fingerprint,
                    "dependency_evidence": dependencies,
                }
            )
            if source_valid and dependency_identity_complete
            else ""
        ),
        "validator_sha256": (
            canonical_sha256(
                {
                    "source_sha256": source_fingerprint,
                    "command": step.get("command", []),
                }
            )
            if source_valid
            else ""
        ),
        "policy_sha256": str(context.get("policy_sha256") or ""),
        "environment_sha256": str(context.get("environment_sha256") or ""),
        "input_artifacts_sha256": input_sha256,
        "scope_sha256": canonical_sha256(
            {
                "proof_scope": proof_scope,
                "selected_step_ids": selected_step_ids,
                "step_scope": scope_contract,
            }
        ),
        "step_contract_sha256": canonical_sha256(step),
        "output_artifact_sha256": output_artifact_sha256(step.get("raw_artifact")),
    }
    return {str(field): str(identity.get(str(field)) or "") for field in fields}


def reuse_identity_complete(
    identity: Any,
    reuse_context_value: dict[str, Any] | None = None,
) -> bool:
    context = reuse_context_value or build_reuse_context()
    fields = context.get("identity_fields")
    required = {str(field) for field in fields} if isinstance(fields, list) else set()
    return (
        bool(required)
        and isinstance(identity, dict)
        and set(identity) == required
        and all(valid_sha256(value) for value in identity.values())
    )


def attach_step_evidence(
    result: dict[str, Any],
    *,
    identity: dict[str, str],
    proof_domain: str,
    evidence_from_dependency: bool,
    reuse_context_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = reuse_context_value or build_reuse_context()
    fresh_domains = {str(item) for item in context.get("fresh_domains", [])}
    clean = (
        result.get("passed") is True
        and result.get("returncode") == 0
        and result.get("timed_out") is False
        and result.get("attention") is False
        and result.get("execution_status") == "COMPLETED"
    )
    result["reuse_identity"] = identity
    shared_receipt = result.get("shared_producer_receipt")
    shared_rows = shared_receipt.get("artifacts", {}) if isinstance(shared_receipt, dict) else {}
    shared_artifact_evidence = {
        str(consumer_id): {
            "artifact_id": row.get("artifact_id"),
            "artifact_sha256": row.get("artifact_sha256"),
            "primary_fingerprint": row.get("primary_fingerprint"),
            "snapshot_binding": row.get("snapshot_binding"),
            "observed_snapshot_id": row.get("observed_snapshot_id"),
            "valid": row.get("valid"),
        }
        for consumer_id, row in shared_rows.items()
        if isinstance(row, dict)
    }
    result["evidence_sha256"] = canonical_sha256(
        {
            "id": result.get("id"),
            "passed": result.get("passed"),
            "returncode": result.get("returncode"),
            "timed_out": result.get("timed_out"),
            "attention": result.get("attention"),
            "execution_status": result.get("execution_status"),
            "reuse_identity": identity,
            "shared_artifact_evidence": shared_artifact_evidence,
        }
    )
    result["checkpoint_reuse_eligible"] = bool(
        clean
        and reuse_identity_complete(identity, context)
        and proof_domain not in fresh_domains
        and not evidence_from_dependency
    )
    return result


def resume_decision(
    *,
    step_id: str,
    proof_domain: str,
    evidence_from_dependency: bool,
    current_identity: dict[str, str],
    previous_result: Any,
    reuse_context_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = reuse_context_value or build_reuse_context()
    reasons: list[str] = []
    if context.get("execution_mode") != "interruption_resume_exact_identity":
        reasons.append("resume_execution_mode_invalid")
    if context.get("automatic") is not True:
        reasons.append("automatic_resume_not_authorized")
    if proof_domain in {str(item) for item in context.get("fresh_domains", [])}:
        reasons.append("fresh_candidate_evidence_required")
    if evidence_from_dependency:
        reasons.append("same_invocation_shared_evidence_required")
    if not reuse_identity_complete(current_identity, context):
        reasons.append("current_identity_incomplete")
    if not isinstance(previous_result, dict) or previous_result.get("id") != step_id:
        reasons.append("prior_checkpoint_result_missing_or_wrong_step")
    else:
        if previous_result.get("checkpoint_reuse_eligible") is not True:
            reasons.append("prior_checkpoint_result_not_reuse_eligible")
        if previous_result.get("passed") is not True:
            reasons.append("prior_checkpoint_result_not_passed")
        if previous_result.get("returncode") != 0 or previous_result.get("timed_out") is not False:
            reasons.append("prior_checkpoint_result_not_clean")
        if previous_result.get("attention") is not False:
            reasons.append("prior_checkpoint_attention_requires_execution")
        if previous_result.get("execution_status") not in {"COMPLETED", "RESUMED_FROM_CHECKPOINT"}:
            reasons.append("prior_checkpoint_execution_status_invalid")
        if not valid_sha256(previous_result.get("evidence_sha256")):
            reasons.append("prior_checkpoint_evidence_identity_missing")
        prior_identity = previous_result.get("reuse_identity")
        if not reuse_identity_complete(prior_identity, context):
            reasons.append("prior_checkpoint_identity_incomplete")
        elif reuse_identity_complete(current_identity, context) and prior_identity != current_identity:
            reasons.append("resume_identity_changed")
    return {
        "reuse": not reasons,
        "reasons": sorted(set(reasons)) or ["exact_interrupted_run_identity_match"],
    }

def load_checkpoint() -> dict[str, Any]:
    payload = load_json_file(checkpoint_path(), {}, bypass_proxy=True)
    return payload if isinstance(payload, dict) else {}


def checkpoint_resume_results(
    payload: Any,
    *,
    expected_run_identity: dict[str, str],
    restart: bool,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if restart:
        return {}, ["operator_requested_fresh_restart"]
    policy = _reuse_policy()
    checkpoint = policy.get("checkpoint")
    resumable_states = (
        {str(item) for item in checkpoint.get("resumable_states", [])}
        if isinstance(checkpoint, dict)
        else set()
    )
    reasons: list[str] = []
    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    if (
        not isinstance(meta, dict)
        or meta.get("kind") != "nexora.release_proof_resume_checkpoint"
        or meta.get("version") != "v1"
        or meta.get("authority") != "interruption_resume_only_no_release_authority"
    ):
        reasons.append("checkpoint_missing_or_wrong_contract")
    elif payload.get("state") not in resumable_states:
        reasons.append("checkpoint_state_not_resumable")
    elif not run_identity_complete(expected_run_identity):
        reasons.append("current_run_identity_incomplete")
    elif payload.get("run_identity") != expected_run_identity:
        reasons.append("checkpoint_run_identity_changed")
    steps = payload.get("steps") if isinstance(payload, dict) else None
    if not isinstance(steps, dict):
        reasons.append("checkpoint_steps_missing")
    if reasons:
        return {}, sorted(set(reasons))
    return (
        {str(key): value for key, value in steps.items() if isinstance(value, dict)},
        ["resumable_interrupted_checkpoint_loaded"],
    )


def new_checkpoint(
    *,
    invocation_id: str,
    proof_scope: str,
    selected_step_ids: list[str],
    run_identity_value: dict[str, str],
    carried_results: dict[str, dict[str, Any]],
    resume_reasons: list[str],
) -> dict[str, Any]:
    now = _utc_now()
    return {
        "meta": {
            "kind": "nexora.release_proof_resume_checkpoint",
            "version": "v1",
            "authority": "interruption_resume_only_no_release_authority",
        },
        "state": "in_progress",
        "invocation_id": invocation_id,
        "proof_scope": proof_scope,
        "selected_step_ids": selected_step_ids,
        "run_identity": run_identity_value,
        "resume_reasons": resume_reasons,
        "started_at": now,
        "updated_at": now,
        "steps": dict(carried_results),
    }


def _checkpoint_result(result: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id", "label", "proof_domain", "execution_cadence", "evidence_role",
        "blocks_machine_release", "blocks_public_release", "readiness_authority_reason",
        "required", "execution_status", "depends_on", "returncode", "passed", "timed_out",
        "raw_artifact", "raw_artifact_sha256", "artifact_payload_sha256", "attention",
        "attention_reasons", "reuse_identity", "evidence_sha256", "checkpoint_reuse_eligible",
    }
    return {key: result.get(key) for key in allowed if key in result}


def save_checkpoint(payload: dict[str, Any]) -> None:
    payload["updated_at"] = _utc_now()
    save_json_atomic(checkpoint_path(), payload, bypass_proxy=True)


def record_checkpoint_result(payload: dict[str, Any], result: dict[str, Any]) -> None:
    steps = payload.setdefault("steps", {})
    if not isinstance(steps, dict):
        raise ValueError("Release-proof checkpoint steps must be an object")
    steps[str(result.get("id") or "")] = _checkpoint_result(result)
    payload["state"] = "in_progress"
    save_checkpoint(payload)


def mark_checkpoint_state(payload: dict[str, Any], state: str) -> None:
    allowed = {"in_progress", "proof_steps_complete", "completed"}
    if state not in allowed:
        raise ValueError(f"Invalid release-proof checkpoint state: {state}")
    payload["state"] = state
    save_checkpoint(payload)
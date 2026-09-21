"""Global-exact interruption resume and reviewed completed-proof reuse."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_file
from tools.core.release_proof_steps import load_release_proof_scope_contract
from tools.core.release_validation_dependencies import (
    build_release_validation_dependency_context,
)


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


def _normalized_distribution_name(value: Any) -> str:
    return re.sub(r"[-_.]+", "-", str(value or "").strip().lower())


def _installed_python_distribution_receipt(
    required_names: set[str],
) -> dict[str, Any]:
    rows: dict[str, str] = {}
    reasons: list[str] = []
    try:
        for distribution in importlib.metadata.distributions():
            name = _normalized_distribution_name(distribution.metadata.get("Name"))
            version = str(distribution.version or "").strip()
            if not name or not version:
                reasons.append("installed_python_distribution_metadata_incomplete")
                continue
            previous = rows.get(name)
            if previous is not None and previous != version:
                reasons.append(f"duplicate_python_distribution_version:{name}")
            rows[name] = version
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        return {
            "complete": False,
            "reasons": [f"installed_python_distribution_inventory_failed:{type(exc).__name__}"],
            "packages": [],
        }
    missing = sorted(name for name in required_names if not rows.get(name))
    reasons.extend(f"required_python_distribution_missing:{name}" for name in missing)
    return {
        "complete": bool(rows) and not reasons,
        "reasons": sorted(set(reasons)),
        "packages": [
            {"name": name, "version": version}
            for name, version in sorted(rows.items())
        ],
    }


def _node_runtime_receipt() -> dict[str, Any]:
    executable = shutil.which("node")
    if not executable:
        return {
            "complete": False,
            "path": None,
            "version": None,
            "reason": "declared_node_runtime_missing",
        }
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "complete": False,
            "path": str(Path(executable).resolve()),
            "version": None,
            "reason": f"node_runtime_observation_failed:{type(exc).__name__}",
        }
    version = (result.stdout or result.stderr or "").strip().splitlines()
    version_text = version[0] if version else ""
    return {
        "complete": result.returncode == 0 and bool(version_text),
        "path": str(Path(executable).resolve()),
        "version": version_text or None,
        "returncode": result.returncode,
        "reason": (
            "declared_node_runtime_observed"
            if result.returncode == 0 and version_text
            else "declared_node_runtime_version_unavailable"
        ),
    }


@lru_cache(maxsize=1)
def environment_receipt() -> dict[str, Any]:
    """Build one bounded runtime/dependency identity for the whole proof invocation."""
    reasons: list[str] = []
    executable = Path(sys.executable)
    executable_sha256 = _sha256_file(executable)
    if not valid_sha256(executable_sha256):
        reasons.append("python_executable_identity_unavailable")
    prefix = Path(sys.prefix)
    base_prefix = Path(getattr(sys, "base_prefix", sys.prefix))
    venv_active = prefix.resolve() != base_prefix.resolve()
    venv_config = prefix / "pyvenv.cfg"
    venv_config_sha256 = _sha256_file(venv_config) if venv_config.is_file() else None
    if venv_active and not valid_sha256(venv_config_sha256):
        reasons.append("active_venv_configuration_identity_unavailable")

    try:
        from tools.core.installation_preflight import (
            load_installation_preflight_contract,
            node_ast_cache_state,
        )

        installation_contract = load_installation_preflight_contract()
    except (ImportError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        installation_contract = {}
        reasons.append(f"installation_preflight_contract_unavailable:{type(exc).__name__}")
    python_features = (
        installation_contract.get("python_features", {})
        if isinstance(installation_contract, dict)
        else {}
    )
    required_python_names = {
        _normalized_distribution_name(row.get("package"))
        for row in python_features.values()
        if isinstance(row, dict) and row.get("required_by_default_profile") is True
    }
    required_python_names.discard("")
    python_distributions = _installed_python_distribution_receipt(required_python_names)
    if python_distributions.get("complete") is not True:
        reasons.extend(str(item) for item in python_distributions.get("reasons", []))

    node_contract = (
        installation_contract.get("node", {})
        if isinstance(installation_contract, dict)
        else {}
    )
    node_declared = bool(
        isinstance(node_contract, dict)
        and node_contract.get("package_manifest")
        and node_contract.get("bundled_typescript_marker")
    )
    node_runtime = _node_runtime_receipt() if node_declared else {
        "complete": False,
        "reason": "node_runtime_authority_not_declared",
    }
    if node_runtime.get("complete") is not True:
        reasons.append(str(node_runtime.get("reason") or "node_runtime_identity_incomplete"))
    try:
        node_dependency = (
            node_ast_cache_state(node_contract, root=CODE_MAPS_DIR)
            if node_declared
            else {}
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        node_dependency = {}
        reasons.append(f"node_dependency_identity_failed:{type(exc).__name__}")
    if node_dependency.get("status") != "VERIFIED":
        reasons.append("node_dependency_identity_unverified")

    receipt = {
        "meta": {
            "kind": "nexora.release_proof_environment_receipt",
            "version": "v1",
            "authority": "exact_environment_reuse_invalidation_only_no_release_authority",
        },
        "python_runtime": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "full_version": sys.version,
            "cache_tag": getattr(sys.implementation, "cache_tag", None),
            "executable": str(executable.resolve()),
            "executable_sha256": executable_sha256 or None,
            "prefix": str(prefix.resolve()),
            "base_prefix": str(base_prefix.resolve()),
            "venv_active": venv_active,
            "venv_config_sha256": venv_config_sha256,
        },
        "operating_system": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "architecture": list(platform.architecture()),
        },
        "python_distributions": python_distributions,
        "node_runtime": node_runtime,
        "node_dependency": {
            "status": node_dependency.get("status"),
            "content_identity": node_dependency.get("content_identity"),
            "expected_typescript_version": node_dependency.get(
                "expected_typescript_version"
            ),
            "locked_typescript_version": node_dependency.get(
                "locked_typescript_version"
            ),
            "installed_typescript_version": node_dependency.get(
                "installed_typescript_version"
            ),
            "errors": list(node_dependency.get("errors") or []),
        },
        "complete": not reasons,
        "reasons": sorted(set(reasons)) or ["bounded_environment_identity_complete"],
        "claim_boundary": (
            "Binds the current Python executable, interpreter, OS/architecture, active "
            "environment package name/version inventory, declared Node runtime version and "
            "the central TypeScript manifest/lock/installed-version receipt. It invalidates "
            "exact-environment reuse; it does not prove arbitrary installed-package file "
            "integrity or portability to another machine."
        ),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def environment_sha256() -> str:
    receipt = environment_receipt()
    digest = str(receipt.get("receipt_sha256") or "")
    payload = dict(receipt)
    payload.pop("receipt_sha256", None)
    return (
        digest
        if receipt.get("complete") is True
        and valid_sha256(digest)
        and canonical_sha256(payload) == digest
        else ""
    )


def policy_sha256() -> str:
    return _sha256_path_set(
        [
            CODE_MAPS_DIR / "config" / "release_proof_scope_contract.json",
            CODE_MAPS_DIR / "config" / "release_proof_steps_contract.json",
            CODE_MAPS_DIR / "config" / "release_evidence_cadence_contract.json",
            CODE_MAPS_DIR / "config" / "pipeline_execution_policy.json",
        ]
    )


def build_reuse_context() -> dict[str, Any]:
    policy = _reuse_policy()
    dependency_context = build_release_validation_dependency_context()
    fields = policy.get("identity_fields")
    checkpoint = policy.get("checkpoint")
    completed_run_reuse = policy.get("completed_run_reuse")
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) != len(set(map(str, fields)))
        or not isinstance(checkpoint, dict)
        or not isinstance(completed_run_reuse, dict)
    ):
        raise ValueError("Release-proof resume policy is incomplete")
    return {
        "identity_fields": [str(field) for field in fields],
        "fresh_domains": [str(item) for item in policy.get("always_fresh_domains", [])],
        "execution_mode": str(policy.get("execution_mode") or ""),
        "automatic": checkpoint.get("automatic") is True,
        "completed_run_reuse": dict(completed_run_reuse),
        "policy_sha256": policy_sha256(),
        "environment_sha256": environment_sha256(),
        "environment_receipt": environment_receipt(),
        "semantic_dependency_context": dependency_context,
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


def completed_step_reuse_identity(
    step: dict[str, Any],
    *,
    scope_contract: dict[str, Any],
    proof_scope: str,
    selected_step_ids: list[str],
    source_fingerprint: str,
    dependency_results: dict[str, dict[str, Any]],
    checkpoint_identity: dict[str, str],
    reuse_context_value: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build completed-proof identity without weakening interruption identity."""
    context = reuse_context_value or build_reuse_context()
    fields = context.get("identity_fields")
    required_fields = [str(field) for field in fields] if isinstance(fields, list) else []
    if not required_fields or len(required_fields) != len(set(required_fields)):
        raise ValueError("Release-proof completed reuse identity fields are invalid")
    if not valid_sha256(source_fingerprint) or not reuse_identity_complete(
        checkpoint_identity,
        context,
    ):
        return {field: "" for field in required_fields}
    dependency_context = context.get("semantic_dependency_context")
    if not isinstance(dependency_context, dict) or dependency_context.get("complete") is not True:
        return {field: "" for field in required_fields}
    step_id = str(step.get("id") or "")
    dispositions = dependency_context.get("step_dispositions")
    disposition = dispositions.get(step_id) if isinstance(dispositions, dict) else None
    if disposition not in {
        "semantic_closure_reviewed",
        "same_invocation_shared_producer",
        "always_fresh",
        "global_source_fingerprint_until_mapped",
    }:
        return {field: "" for field in required_fields}
    semantic_source_sha256 = source_fingerprint
    completed_policy_sha256 = str(context.get("policy_sha256") or "")
    if disposition == "semantic_closure_reviewed":
        reviewed = dependency_context.get("reviewed_step_identities")
        projection = reviewed.get(step_id) if isinstance(reviewed, dict) else None
        if not isinstance(projection, dict) or projection.get("reuse_eligible") is not True:
            return {field: "" for field in required_fields}
        semantic_source_sha256 = str(projection.get("source_dependency_sha256") or "")
        completed_policy_sha256 = canonical_sha256(
            {
                "release_policy_sha256": context.get("policy_sha256"),
                "semantic_dependency_contract_sha256": dependency_context.get(
                    "semantic_dependency_contract_sha256"
                ),
                "semantic_reuse_engine_sha256": dependency_context.get(
                    "semantic_reuse_engine_sha256"
                ),
            }
        )
    dependencies: dict[str, str] = {}
    dependency_identity_complete = True
    for dependency in [str(item) for item in step.get("depends_on", []) or []]:
        evidence = str(
            dependency_results.get(dependency, {}).get("completed_evidence_sha256") or ""
        )
        dependencies[dependency] = evidence
        dependency_identity_complete = dependency_identity_complete and valid_sha256(evidence)
    source_valid = valid_sha256(semantic_source_sha256)
    input_sha256 = canonical_sha256(dependencies) if dependency_identity_complete else ""
    identity = {
        "source_dependency_sha256": (
            canonical_sha256(
                {
                    "source_sha256": semantic_source_sha256,
                    "dependency_completed_evidence": dependencies,
                }
            )
            if source_valid and dependency_identity_complete
            else ""
        ),
        "validator_sha256": (
            canonical_sha256(
                {
                    "source_sha256": semantic_source_sha256,
                    "command": step.get("command", []),
                }
            )
            if source_valid
            else ""
        ),
        "policy_sha256": completed_policy_sha256,
        "environment_sha256": str(context.get("environment_sha256") or ""),
        "input_artifacts_sha256": input_sha256,
        "scope_sha256": str(checkpoint_identity.get("scope_sha256") or ""),
        "step_contract_sha256": str(checkpoint_identity.get("step_contract_sha256") or ""),
        "output_artifact_sha256": str(checkpoint_identity.get("output_artifact_sha256") or ""),
    }
    return {field: str(identity.get(field) or "") for field in required_fields}


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


_CLEAN_EVIDENCE_EXECUTION_STATUSES = frozenset(
    {
        "COMPLETED",
        "RESUMED_FROM_CHECKPOINT",
        "REUSED_FROM_COMPLETED_PROOF",
    }
)


def _shared_artifact_evidence(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    shared_receipt = result.get("shared_producer_receipt")
    shared_rows = shared_receipt.get("artifacts", {}) if isinstance(shared_receipt, dict) else {}
    return {
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


def step_evidence_sha256(
    result: dict[str, Any],
    *,
    identity: dict[str, str] | None = None,
) -> str:
    """Hash reusable semantics while normalizing clean carry-forward status."""
    execution_status = result.get("execution_status")
    clean = (
        result.get("passed") is True
        and result.get("returncode") == 0
        and result.get("timed_out") is False
        and result.get("attention") is False
        and execution_status in _CLEAN_EVIDENCE_EXECUTION_STATUSES
    )
    return canonical_sha256(
        {
            "id": result.get("id"),
            "passed": result.get("passed"),
            "returncode": result.get("returncode"),
            "timed_out": result.get("timed_out"),
            "attention": result.get("attention"),
            "execution_status": "CLEAN_COMPLETED_EVIDENCE" if clean else execution_status,
            "reuse_identity": identity if identity is not None else result.get("reuse_identity"),
            "checkpoint_reuse_eligible": result.get("checkpoint_reuse_eligible"),
            "completed_reuse_eligible": result.get("completed_reuse_eligible"),
            "shared_artifact_evidence": _shared_artifact_evidence(result),
        }
    )


def completed_step_evidence_sha256(
    result: dict[str, Any],
    *,
    identity: dict[str, str] | None = None,
) -> str:
    """Hash completed-proof evidence independently from checkpoint identity."""
    execution_status = result.get("execution_status")
    clean = (
        result.get("passed") is True
        and result.get("returncode") == 0
        and result.get("timed_out") is False
        and result.get("attention") is False
        and execution_status in _CLEAN_EVIDENCE_EXECUTION_STATUSES
    )
    return canonical_sha256(
        {
            "id": result.get("id"),
            "passed": result.get("passed"),
            "returncode": result.get("returncode"),
            "timed_out": result.get("timed_out"),
            "attention": result.get("attention"),
            "execution_status": "CLEAN_COMPLETED_EVIDENCE" if clean else execution_status,
            "completed_reuse_identity": (
                identity if identity is not None else result.get("completed_reuse_identity")
            ),
            "completed_reuse_eligible": result.get("completed_reuse_eligible"),
            "shared_artifact_evidence": _shared_artifact_evidence(result),
        }
    )


def attach_step_evidence(
    result: dict[str, Any],
    *,
    identity: dict[str, str],
    completed_identity: dict[str, str],
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
    result["completed_reuse_identity"] = completed_identity
    result["checkpoint_reuse_eligible"] = bool(
        clean
        and reuse_identity_complete(identity, context)
        and proof_domain not in fresh_domains
        and not evidence_from_dependency
    )
    result["completed_reuse_eligible"] = bool(
        clean
        and reuse_identity_complete(completed_identity, context)
        and proof_domain not in fresh_domains
        and not evidence_from_dependency
    )
    result["evidence_sha256"] = step_evidence_sha256(result, identity=identity)
    result["completed_evidence_sha256"] = completed_step_evidence_sha256(
        result,
        identity=completed_identity,
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
        if previous_result.get("execution_status") not in {
            "COMPLETED",
            "RESUMED_FROM_CHECKPOINT",
            "REUSED_FROM_COMPLETED_PROOF",
        }:
            reasons.append("prior_checkpoint_execution_status_invalid")
        prior_evidence_sha256 = previous_result.get("evidence_sha256")
        if not valid_sha256(prior_evidence_sha256):
            reasons.append("prior_checkpoint_evidence_identity_missing")
        elif prior_evidence_sha256 != step_evidence_sha256(previous_result):
            reasons.append("prior_checkpoint_evidence_digest_mismatch")
        prior_identity = previous_result.get("reuse_identity")
        if not reuse_identity_complete(prior_identity, context):
            reasons.append("prior_checkpoint_identity_incomplete")
        elif reuse_identity_complete(current_identity, context) and prior_identity != current_identity:
            reasons.append("resume_identity_changed")
    return {
        "reuse": not reasons,
        "reasons": sorted(set(reasons)) or ["exact_interrupted_run_identity_match"],
    }


def completed_proof_reuse_decision(
    *,
    step_id: str,
    proof_scope: str,
    release_phase: str,
    aggregate_strategy: str,
    proof_domain: str,
    evidence_from_dependency: bool,
    current_completed_identity: dict[str, str],
    previous_result: Any,
    reuse_context_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = reuse_context_value or build_reuse_context()
    policy = context.get("completed_run_reuse")
    reasons: list[str] = []
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        reasons.append("completed_run_reuse_not_enabled")
        policy = {}
    if policy.get("authority") != "execution_skip_only_no_release_or_publication_authority":
        reasons.append("completed_run_reuse_authority_invalid")
    if proof_scope != policy.get("proof_scope"):
        reasons.append("completed_run_reuse_scope_invalid")
    if release_phase != policy.get("release_phase"):
        reasons.append("completed_run_reuse_phase_invalid")
    if aggregate_strategy != policy.get("aggregate_strategy"):
        reasons.append("completed_run_reuse_strategy_invalid")
    if proof_domain in {str(item) for item in context.get("fresh_domains", [])}:
        reasons.append("fresh_candidate_evidence_required")
    if evidence_from_dependency:
        reasons.append("same_invocation_shared_evidence_required")
    if not reuse_identity_complete(current_completed_identity, context):
        reasons.append("current_completed_identity_incomplete")
    if not isinstance(previous_result, dict) or previous_result.get("id") != step_id:
        reasons.append("prior_completed_result_missing_or_wrong_step")
    else:
        if previous_result.get("completed_reuse_eligible") is not True:
            reasons.append("prior_completed_result_not_reuse_eligible")
        if previous_result.get("passed") is not True:
            reasons.append("prior_completed_result_not_passed")
        if previous_result.get("returncode") != 0 or previous_result.get("timed_out") is not False:
            reasons.append("prior_completed_result_not_clean")
        if previous_result.get("attention") is not False:
            reasons.append("prior_completed_attention_requires_execution")
        allowed_statuses = {
            str(item) for item in policy.get("allowed_prior_execution_statuses", [])
        }
        if previous_result.get("execution_status") not in allowed_statuses:
            reasons.append("prior_completed_execution_status_invalid")
        prior_evidence_sha256 = previous_result.get("completed_evidence_sha256")
        if not valid_sha256(prior_evidence_sha256):
            reasons.append("prior_completed_semantic_evidence_identity_missing")
        elif prior_evidence_sha256 != completed_step_evidence_sha256(previous_result):
            reasons.append("prior_completed_semantic_evidence_digest_mismatch")
        prior_identity = previous_result.get("completed_reuse_identity")
        if not reuse_identity_complete(prior_identity, context):
            reasons.append("prior_completed_semantic_identity_incomplete")
        elif (
            reuse_identity_complete(current_completed_identity, context)
            and prior_identity != current_completed_identity
        ):
            reasons.append("completed_reuse_identity_changed")
    return {
        "reuse": not reasons,
        "reasons": sorted(set(reasons)) or ["exact_completed_content_bound_receipt_match"],
        "authority": "execution_skip_only_no_release_or_publication_authority",
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
        "completed_reuse_identity", "completed_evidence_sha256", "completed_reuse_eligible",
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

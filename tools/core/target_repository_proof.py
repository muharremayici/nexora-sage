from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.artifact_registry import artifact_metadata
from tools.core.analysis_snapshot_lineage import receipt_binding
from tools.core.atlas_integrity import validate_atlas_commit
from tools.core.config import CONFIG_DIR, RAW_DIR
from tools.core.json_io import load_json_object_strict, load_raw_artifact_path_strict


CONTRACT_PATH = CONFIG_DIR / "target_repository_proof_contract.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _pointer(payload: Any, pointer: str | None) -> Any:
    if not pointer:
        return None
    current = payload
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _payload_sha(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _repository_reference(root: Path, explicit_snapshot: str | None) -> dict[str, Any]:
    if explicit_snapshot:
        return {
            "repository_reference_kind": "explicit",
            "repository_reference_id": explicit_snapshot,
            "working_tree_status": "unknown",
        }
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        return {
            "repository_reference_kind": "git_commit",
            "repository_reference_id": head or None,
            "working_tree_status": "dirty" if status.strip() else "clean",
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "repository_reference_kind": "unavailable",
            "repository_reference_id": None,
            "working_tree_status": "unknown",
        }


def _artifact_timestamp(raw_dir: Path, artifact_id: str, path: Path) -> datetime | None:
    if raw_dir.resolve() == RAW_DIR.resolve():
        try:
            from tools.core.artifact_store import STORE

            metadata = STORE.raw_metadata(artifact_id)
            updated_at = _parse_time(str(metadata.get("updated_at") or ""))
            if updated_at:
                return updated_at
            source_mtime = float(metadata.get("source_mtime") or 0.0)
            if source_mtime > 0:
                return datetime.fromtimestamp(source_mtime, timezone.utc)
        except Exception:
            pass
    if path.exists():
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return None


def _source_verdict(payload: Any, evidence_contract: dict[str, Any]) -> str:
    pointer = evidence_contract.get("verdict_pointer")
    if not pointer:
        return "PRESENT"
    value = _pointer(payload, str(pointer))
    if value in evidence_contract.get("pass_values", []):
        return "PASS"
    if value in evidence_contract.get("block_values", []):
        return "BLOCKED"
    return "UNKNOWN"


def _atlas_project_roots(atlas: Any) -> list[Path]:
    if not isinstance(atlas, dict):
        return []
    roots: list[Path] = []
    for project_id, project_data in atlas.items():
        if project_id == "symbols" or not isinstance(project_data, dict):
            continue
        project = project_data.get("project")
        root = project.get("root") if isinstance(project, dict) else None
        if isinstance(root, str) and root.strip():
            roots.append(Path(root).resolve())
    return roots


def _root_binding(target_root: Path, atlas: Any) -> str:
    project_roots = _atlas_project_roots(atlas)
    if not project_roots:
        return "UNAVAILABLE"
    resolved_target = target_root.resolve()
    return "BOUND" if all(root == resolved_target or resolved_target in root.parents for root in project_roots) else "MISMATCH"


def _atlas_identity(atlas: Any, commit: Any) -> tuple[str | None, bool]:
    if not isinstance(atlas, dict) or not isinstance(commit, dict):
        return None, False
    checks = validate_atlas_commit(atlas, commit)
    return str(commit.get("snapshot_id") or "") or None, bool(checks) and all(row["passed"] for row in checks)


def build_target_repository_proof(
    *,
    target_root: Path,
    raw_dir: Path,
    mode: str,
    repository_reference: str | None = None,
    evidence_not_before: str | None = None,
) -> dict[str, Any]:
    contract = load_json_object_strict(CONTRACT_PATH, label="target repository proof contract")
    mode_contract = contract.get("modes", {}).get(mode)
    if not isinstance(mode_contract, dict):
        raise ValueError(f"unknown target proof mode: {mode}")

    registry = artifact_metadata()
    evidence_contracts = contract.get("evidence_contracts", {})
    required_ids = [str(value) for value in mode_contract.get("required_evidence", [])]
    optional_ids = [str(value) for value in mode_contract.get("optional_evidence", [])]
    if not required_ids:
        raise ValueError(f"target proof mode {mode} has no required evidence")

    caller_freshness_basis = _parse_time(evidence_not_before)
    evidence_rows: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    unknowns: list[str] = []
    human_decisions: list[str] = []

    artifact_paths: dict[str, Path] = {}
    for artifact_id in required_ids + optional_ids:
        if artifact_id not in registry or artifact_id not in evidence_contracts:
            raise ValueError(f"target proof artifact is not centrally registered: {artifact_id}")
        artifact_path = raw_dir / Path(registry[artifact_id]["path"]).name
        artifact_paths[artifact_id] = artifact_path
        required = artifact_id in required_ids
        availability = "MISSING"
        freshness = "UNKNOWN"
        content_sha = None
        source_verdict = "UNKNOWN"
        try:
            payload = load_raw_artifact_path_strict(artifact_path)
            if payload is None:
                raise FileNotFoundError(artifact_path)
            availability = "PRESENT"
            payloads[artifact_id] = payload
            content_sha = _payload_sha(payload)
            source_verdict = _source_verdict(payload, evidence_contracts[artifact_id])
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, json.JSONDecodeError):
            availability = "INVALID"

        evidence_rows.append(
            {
                "artifact_id": artifact_id,
                "required": required,
                "path": str(artifact_path),
                "availability": availability,
                "freshness": freshness,
                "snapshot_binding": "UNAVAILABLE",
                "bound_snapshot_id": None,
                "content_sha256": content_sha,
                "source_verdict": source_verdict,
                "human_decision": bool(evidence_contracts[artifact_id].get("human_decision")),
            }
        )

    atlas = payloads.get("atlas")
    atlas_commit = payloads.get("atlas_commit")
    analysis_snapshot_id, atlas_identity_valid = _atlas_identity(atlas, atlas_commit)
    root_binding = _root_binding(target_root, atlas)
    reference = _repository_reference(target_root.resolve(), repository_reference)
    subject = {
        "scope": "target_repository",
        "root": str(target_root.resolve()),
        "analysis_snapshot_kind": "atlas_commit" if analysis_snapshot_id else "unavailable",
        "analysis_snapshot_id": analysis_snapshot_id,
        "root_binding": root_binding,
        **reference,
    }
    if not atlas_identity_valid:
        unknowns.append("analysis_snapshot:invalid_or_unavailable")
    if root_binding != "BOUND":
        unknowns.append(f"target_root:{root_binding.lower()}")
    commit_generated_at = _parse_time(
        str((atlas_commit.get("meta") or {}).get("generated_at") or "")
        if isinstance(atlas_commit, dict)
        else None
    )
    freshness_basis = commit_generated_at
    if caller_freshness_basis and (freshness_basis is None or caller_freshness_basis > freshness_basis):
        freshness_basis = caller_freshness_basis

    for row in evidence_rows:
        artifact_id = row["artifact_id"]
        evidence_contract = evidence_contracts[artifact_id]
        payload = payloads.get(artifact_id)
        binding_mode = evidence_contract.get("snapshot_binding")
        if binding_mode in {"atlas_commit_payload", "atlas_commit_identity"}:
            row["snapshot_binding"] = "BOUND" if atlas_identity_valid else "MISMATCH"
            row["bound_snapshot_id"] = analysis_snapshot_id
            row["freshness"] = "NOT_APPLICABLE"
        elif binding_mode == "producer_lineage_receipt" and payload is not None and analysis_snapshot_id:
            binding, observed_snapshot, binding_errors = receipt_binding(
                raw_dir=raw_dir,
                artifact_id=artifact_id,
                artifact_payload=payload,
                expected_snapshot_id=analysis_snapshot_id,
            )
            row["bound_snapshot_id"] = observed_snapshot
            row["snapshot_binding"] = binding
            unknowns.extend(f"{artifact_id}:lineage_{error}" for error in binding_errors)
            artifact_time = _artifact_timestamp(raw_dir, artifact_id, artifact_paths[artifact_id])
            if freshness_basis is None or artifact_time is None:
                row["freshness"] = "UNKNOWN"
            else:
                row["freshness"] = "CURRENT" if artifact_time >= freshness_basis else "STALE"
        elif payload is not None and analysis_snapshot_id:
            observed_snapshot = _pointer(payload, str(evidence_contract.get("snapshot_pointer") or ""))
            row["bound_snapshot_id"] = observed_snapshot if isinstance(observed_snapshot, str) else None
            row["snapshot_binding"] = (
                "BOUND" if observed_snapshot == analysis_snapshot_id
                else "MISMATCH" if observed_snapshot
                else "UNAVAILABLE"
            )
            artifact_time = _artifact_timestamp(raw_dir, artifact_id, artifact_paths[artifact_id])
            if freshness_basis is None or artifact_time is None:
                row["freshness"] = "UNKNOWN"
            else:
                row["freshness"] = "CURRENT" if artifact_time >= freshness_basis else "STALE"

        if row["availability"] != "PRESENT":
            unknowns.append(f"{artifact_id}:{row['availability'].lower()}")
        else:
            if row["freshness"] not in {"CURRENT", "NOT_APPLICABLE"}:
                unknowns.append(f"{artifact_id}:freshness_{row['freshness'].lower()}")
            if row["snapshot_binding"] != "BOUND":
                unknowns.append(f"{artifact_id}:snapshot_{row['snapshot_binding'].lower()}")
        if bool(evidence_contract.get("human_decision")) and row["availability"] == "PRESENT":
            human_decisions.append(f"review_{artifact_id}")

    if subject["working_tree_status"] == "dirty":
        human_decisions.append("review_dirty_target_snapshot")

    required_rows = [row for row in evidence_rows if row["required"]]
    optional_rows = [row for row in evidence_rows if not row["required"]]
    required_ready = [
        row for row in required_rows
        if row["availability"] == "PRESENT"
        and row["freshness"] in {"CURRENT", "NOT_APPLICABLE"}
        and row["snapshot_binding"] == "BOUND"
        and row["source_verdict"] != "BLOCKED"
    ]
    blockers = [
        row for row in required_rows
        if row["availability"] != "PRESENT"
        or row["freshness"] not in {"CURRENT", "NOT_APPLICABLE"}
        or row["snapshot_binding"] != "BOUND"
        or row["source_verdict"] == "BLOCKED"
    ]
    if not atlas_identity_valid or root_binding != "BOUND" or blockers:
        verdict = "BLOCKED"
    elif human_decisions:
        verdict = "REVIEW_REQUIRED"
    elif any(row["source_verdict"] == "UNKNOWN" for row in required_rows):
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"

    statistics = []
    evidence_by_id = {row["artifact_id"]: row for row in evidence_rows}
    for definition in contract.get("statistics", {}).get("definitions", []):
        artifact_id = str(definition.get("artifact_id") or "")
        evidence_row = evidence_by_id.get(artifact_id, {})
        if (
            evidence_row.get("availability") != "PRESENT"
            or evidence_row.get("freshness") not in {"CURRENT", "NOT_APPLICABLE"}
            or evidence_row.get("snapshot_binding") != "BOUND"
        ):
            continue
        value = _pointer(payloads.get(artifact_id), str(definition.get("pointer") or ""))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            statistics.append(
                {
                    "id": str(definition["id"]),
                    "artifact_id": artifact_id,
                    "value": value,
                    "decision_use": str(definition["decision_use"]),
                    "scope": "target_analysis_snapshot",
                    "denominator": None,
                    "claim_boundary": "absolute_count_not_rate_or_quality_score",
                }
            )

    contract_identity = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
    return {
        "meta": {
            "kind": "target_repository_proof_bundle",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_target_repository_proof_bundle",
            "contract_identity": contract_identity,
        },
        "subject": subject,
        "claim_boundary": str(contract["authority"]["claim_boundary"]),
        "summary": {
            "mode": mode,
            "verdict": verdict,
            "required_evidence": len(required_rows),
            "required_ready": len(required_ready),
            "optional_evidence": len(optional_rows),
            "optional_ready": sum(
                1 for row in optional_rows
                if row["availability"] == "PRESENT"
                and row["freshness"] in {"CURRENT", "NOT_APPLICABLE"}
                and row["snapshot_binding"] == "BOUND"
            ),
        },
        "evidence": evidence_rows,
        "statistics": statistics,
        "unknowns": sorted(set(unknowns)),
        "human_decisions": sorted(set(human_decisions)),
    }

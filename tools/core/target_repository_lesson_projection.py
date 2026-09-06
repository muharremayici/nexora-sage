from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools.core.artifact_registry import artifact_path_for_storage_root
from tools.core.artifact_validator import validate_payload
from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict, load_raw_artifact_path_strict
from tools.hitl_approval_ledger import verify_ledger


CONTRACT_PATH = CONFIG_DIR / "target_repository_lesson_projection_contract.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(authority: str, identity: str) -> str:
    return f"{authority}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"


def _recurrence_lessons(payload: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    policy = contract["recurrence_admission"]
    minimum_observations = int(policy["minimum_observations"])
    minimum_pulses = int(policy["minimum_distinct_pulses"])
    lessons: list[dict[str, Any]] = []
    for row in payload.get("entries", []) or []:
        if not isinstance(row, dict):
            continue
        first_pulse = str(row.get("first_seen_pulse_id") or "")
        last_pulse = str(row.get("last_seen_pulse_id") or "")
        distinct_pulses = {value for value in (first_pulse, last_pulse) if value}
        try:
            seen_count = int(row.get("seen_count") or 0)
        except (TypeError, ValueError):
            continue
        if seen_count < minimum_observations or len(distinct_pulses) < minimum_pulses:
            continue
        rule = str(row.get("rule") or "").strip()
        target_path = str(row.get("repo_relative_path") or "").replace("\\", "/").strip()
        identity_values = {
            field: (target_path if field == "repo_relative_path" else str(row.get(field) or "").strip())
            for field in policy["identity_fields"]
        }
        if not rule or not all(identity_values.values()):
            continue
        identity = "|".join(f"{field}:{identity_values[field]}" for field in policy["identity_fields"])
        status = str(row.get("status") or "")
        unresolved = status == str((payload.get("policy") or {}).get("unread_status") or "unresolved_unread")
        lessons.append(
            {
                "lesson_id": _stable_id("watchdog_recurrence", identity),
                "source_authority": "watchdog_recurrence",
                "source_identity": identity,
                "authority_status": str(policy["authority_status"]),
                "attention": "ACTIVE" if unresolved else "DORMANT",
                "validity": str(policy["validity"]),
                "enforcement": str(policy["enforcement"]),
                "rule": f"Within this target, changes affecting the recorded scope should re-check recurring finding family '{rule}'.",
                "target_paths": [target_path] if target_path else [],
                "evidence": sorted({f"seen_count:{seen_count}", f"first_pulse:{first_pulse}", f"last_pulse:{last_pulse}", str(row.get("detail") or rule)}),
                "human_actor": None,
            }
        )
    return lessons


def _human_lessons(
    payload: dict[str, Any],
    contract: dict[str, Any],
    target_id: str,
    integrity_validator: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    binding = contract["target_binding"]
    if not isinstance(payload.get("entries"), list):
        return [], "human_approval_ledger:invalid_structure"
    integrity = integrity_validator(payload)
    if str(integrity.get("status") or "") != str(binding["require_signed_chain_status"]):
        return [], "human_approval_ledger:integrity_not_authoritative"
    expected_scope = str(binding["human_scope_template"]).format(target_id=target_id)
    accepted = {str(value) for value in binding["accepted_human_decisions"]}
    admission = contract["human_admission"]
    lessons: list[dict[str, Any]] = []
    for row in payload.get("entries", []) or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("gate") or "") != str(binding["human_gate"]):
            continue
        if str(row.get("scope") or "") != expected_scope or str(row.get("decision") or "") not in accepted:
            continue
        rationale = str(row.get("rationale") or "").strip()
        evidence = sorted({str(value) for value in row.get("evidence", []) if str(value).strip()})
        if (binding["require_human_rationale"] and not rationale) or (binding["require_human_evidence"] and not evidence):
            continue
        identity = str(row.get("id") or "").strip()
        if not identity:
            continue
        lessons.append(
            {
                "lesson_id": _stable_id("human_target_decision", identity),
                "source_authority": "human_target_decision",
                "source_identity": identity,
                "authority_status": str(admission["authority_status"]),
                "attention": "ACTIVE",
                "validity": str(admission["validity"]),
                "enforcement": str(admission["enforcement"]),
                "rule": rationale,
                "target_paths": [],
                "evidence": evidence,
                "human_actor": str(row.get("actor") or "human"),
            }
        )
    return lessons, None


def build_target_repository_lesson_projection(
    *,
    target_id: str,
    target_root: Path,
    raw_dir: Path,
    integrity_validator: Callable[[dict[str, Any]], dict[str, Any]] = verify_ledger,
) -> dict[str, Any]:
    contract = load_json_object_strict(CONTRACT_PATH, label="target repository lesson projection contract")
    binding = contract["target_binding"]
    if not re.fullmatch(str(binding["target_id_pattern"]), target_id.strip()):
        raise ValueError("target_id does not satisfy the canonical target identity pattern")
    proof_artifact_id = str(binding["proof_artifact_id"])
    proof_path = artifact_path_for_storage_root(raw_dir, proof_artifact_id)
    binding_available = "MISSING"
    binding_unknown = "target_binding:missing"
    try:
        proof = load_raw_artifact_path_strict(proof_path)
        if proof is None:
            raise FileNotFoundError(proof_path)
        if not isinstance(proof, dict) or validate_payload(proof_artifact_id, proof):
            raise ValueError("target proof artifact is schema-invalid")
        subject = proof.get("subject") if isinstance(proof.get("subject"), dict) else {}
        proof_root = Path(str(subject.get("root") or "")).resolve()
        if (
            str(subject.get("scope") or "") != str(binding["required_proof_scope"])
            or str(subject.get("root_binding") or "") != str(binding["required_root_binding"])
            or proof_root != target_root.resolve()
        ):
            raise ValueError("target proof subject does not bind the requested target root")
        binding_available = "PRESENT"
        binding_unknown = ""
    except FileNotFoundError:
        pass
    except (ValueError, TypeError, OSError):
        binding_available = "INVALID"
        binding_unknown = "target_binding:invalid"
    source_specs = [
        ("watchdog_recurrence", str(contract["recurrence_admission"]["source_artifact_id"])),
        ("human_target_decision", str(contract["human_admission"]["source_artifact_id"])),
    ]
    source_rows: list[dict[str, Any]] = [{"source_authority": "target_binding", "artifact_id": proof_artifact_id, "availability": binding_available, "eligible_rows": 0}]
    lessons: list[dict[str, Any]] = []
    unknowns: list[str] = [binding_unknown] if binding_unknown else []
    for authority, artifact_id in source_specs:
        path = artifact_path_for_storage_root(raw_dir, artifact_id)
        availability = "MISSING"
        projected: list[dict[str, Any]] = []
        try:
            payload = load_raw_artifact_path_strict(path)
            if payload is None:
                raise FileNotFoundError(path)
            if not isinstance(payload, dict):
                raise ValueError("artifact root is not an object")
            schema_errors = validate_payload(artifact_id, payload)
            if schema_errors:
                raise ValueError(f"artifact schema validation failed: {schema_errors[:3]}")
            availability = "PRESENT"
            if authority == "watchdog_recurrence":
                projected = _recurrence_lessons(payload, contract)
            else:
                projected, integrity_unknown = _human_lessons(payload, contract, target_id, integrity_validator)
                if integrity_unknown:
                    unknowns.append(integrity_unknown)
                    availability = "INVALID"
        except FileNotFoundError:
            unknowns.append(f"{authority}:missing")
        except (ValueError, TypeError):
            availability = "INVALID"
            unknowns.append(f"{authority}:invalid")
        seen: set[str] = set()
        unique = []
        for lesson in projected:
            identity = str(lesson["source_identity"])
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(lesson)
        if binding_available == "PRESENT":
            lessons.extend(unique)
        else:
            unique = []
        source_rows.append({"source_authority": authority, "artifact_id": artifact_id, "availability": availability, "eligible_rows": len(unique)})
    lessons.sort(key=lambda row: (0 if row["authority_status"] == "HUMAN_CONFIRMED" else 1, str(row["lesson_id"])))
    present = sum(1 for row in source_rows if row["availability"] == "PRESENT")
    status = "PARTIAL" if present != len(source_rows) else "EMPTY" if not lessons else "COMPLETE"
    return {
        "meta": {
            "kind": "target_repository_lesson_projection",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_target_repository_lesson_projection",
            "contract_identity": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
        },
        "subject": {"system_scope": "SAGE_ON_REPOSITORY", "target_id": target_id, "target_root": str(target_root.resolve())},
        "claim_boundary": str(contract["authority"]["claim_boundary"]),
        "summary": {
            "status": status,
            "declared_sources": len(source_rows),
            "present_sources": present,
            "lesson_count": len(lessons),
            "recurrence_candidates": sum(1 for row in lessons if row["authority_status"] == "CANDIDATE_RECURRENCE"),
            "human_confirmed": sum(1 for row in lessons if row["authority_status"] == "HUMAN_CONFIRMED"),
        },
        "sources": source_rows,
        "lessons": lessons,
        "unknowns": sorted(set(unknowns)),
    }

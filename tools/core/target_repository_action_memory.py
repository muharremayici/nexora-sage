from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools.core.artifact_registry import artifact_path_for_storage_root
from tools.core.artifact_validator import validate_payload
from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict, load_raw_artifact_path_strict


CONTRACT_PATH = CONFIG_DIR / "target_repository_action_memory_contract.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(source_id: str, identity: Any) -> str:
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{source_id}_{hashlib.sha256(encoded).hexdigest()[:16]}"


def _declared_freshness(payload: dict[str, Any]) -> tuple[str, str | None]:
    generated_at = str((payload.get("meta") or {}).get("generated_at") or "").strip()
    if not generated_at:
        return "UNKNOWN", None
    try:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return "UNKNOWN", generated_at
    return ("DECLARED", generated_at) if parsed.tzinfo else ("UNKNOWN", generated_at)


def _audit_actions(payload: dict[str, Any], source: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    generated_freshness, generated_at = _declared_freshness(payload)
    for row in payload.get("violations", []) or []:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project_key") or row.get("project") or "UNKNOWN")
        target_file = str(row.get("repo_relative_path") or row.get("workspace_rel") or row.get("file") or row.get("path") or "").replace("\\", "/")
        rule = str(row.get("rule") or row.get("code") or "architecture_violation")
        evidence = str(row.get("detail") or row.get("message") or "")
        identity = row.get("id") or {"project": project, "target_file": target_file, "rule": rule, "evidence": evidence}
        source_identity = str(row.get("id") or _stable_id("audit_row", identity))
        mode = str(row.get("mode") or row.get("severity") or row.get("level") or "").lower()
        priority = str(row.get("priority") or ("HIGH" if mode in {"critical", "enforced", "heal", "high"} else "NORMAL")).upper()
        actions.append(
            {
                "action_id": _stable_id(str(source["id"]), source_identity),
                "source_authority": source["id"],
                "source_identity": source_identity,
                "lifecycle": str(source["lifecycle"]),
                "priority": priority,
                "target_ref": str(row.get("target_ref") or (f"{project}::{target_file}" if target_file else project)),
                "target_file": target_file,
                "recommended_action": str(row.get("recommended_action") or "Inspect the finding evidence and make the smallest target-repository patch that satisfies the rule."),
                "evidence": evidence,
                "human_approval_required": bool(source["human_approval_required"]),
                "source_generated_at": generated_at,
                "freshness": generated_freshness,
            }
        )
    return actions


def _watchdog_actions(payload: dict[str, Any], source: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    generated_freshness, generated_at = _declared_freshness(payload)
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
    unresolved_status = str(policy.get("unread_status") or "unresolved_unread")
    for row in payload.get("entries", []) or []:
        if not isinstance(row, dict) or str(row.get("status") or "") != unresolved_status:
            continue
        source_identity = str(row.get("violation_hash") or _stable_id("watchdog_row", row))
        target_file = str(row.get("repo_relative_path") or "").replace("\\", "/")
        actions.append(
            {
                "action_id": _stable_id(str(source["id"]), source_identity),
                "source_authority": source["id"],
                "source_identity": source_identity,
                "lifecycle": "UNRESOLVED",
                "priority": "HIGH",
                "target_ref": str(row.get("target_ref") or target_file),
                "target_file": target_file,
                "recommended_action": "Inspect the current watchdog finding, make the smallest bounded correction, and confirm absence in a later pulse.",
                "evidence": str(row.get("detail") or row.get("rule") or ""),
                "human_approval_required": bool(source["human_approval_required"]),
                "source_generated_at": generated_at,
                "freshness": generated_freshness,
            }
        )
    debt = payload.get("proof_debt_state") if isinstance(payload.get("proof_debt_state"), dict) else {}
    if str(debt.get("status") or "") == "due":
        reasons = [str(value) for value in debt.get("due_reasons", []) if str(value).strip()]
        source_identity = _stable_id("watchdog_deep_proof", {"status": "due", "reasons": sorted(reasons)})
        actions.append(
            {
                "action_id": _stable_id(str(source["id"]), source_identity),
                "source_authority": source["id"],
                "source_identity": source_identity,
                "lifecycle": "DUE",
                "priority": "HIGH",
                "target_ref": "target_repository",
                "target_file": "",
                "recommended_action": "Run the centrally configured broad proof refresh sequence before making a repository-wide confidence claim.",
                "evidence": ", ".join(reasons) or "proof_debt_due_without_declared_reason",
                "human_approval_required": bool(source["human_approval_required"]),
                "source_generated_at": generated_at,
                "freshness": generated_freshness,
            }
        )
    return actions


def _merge_actions(payload: dict[str, Any], source: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    generated_freshness, generated_at = _declared_freshness(payload)
    lifecycle_map = source.get("action_lifecycle") if isinstance(source.get("action_lifecycle"), dict) else {}
    for row in payload.get("decisions", []) or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "")
        candidate = str(row.get("candidate") or "")
        source_project = str(row.get("source") or "")
        target_file = str(row.get("target_path") or "").replace("\\", "/")
        source_identity = str(row.get("id") or _stable_id("merge_row", {"candidate": candidate, "source": source_project, "target_path": target_file, "action": action}))
        required_actions = [str(value) for value in row.get("required_actions", []) if str(value).strip()]
        reasons = [str(value) for value in row.get("reasons", []) if str(value).strip()]
        actions.append(
            {
                "action_id": _stable_id(str(source["id"]), source_identity),
                "source_authority": source["id"],
                "source_identity": source_identity,
                "lifecycle": str(lifecycle_map.get(action) or "UNKNOWN"),
                "priority": "HIGH" if action in {"Import With Review", "Do Not Import Yet"} else "NORMAL",
                "target_ref": f"{source_project}::{candidate}" if source_project and candidate else candidate,
                "target_file": target_file,
                "recommended_action": "; ".join(required_actions) or f"Review the merge candidate under the source decision: {action or 'UNKNOWN'}.",
                "evidence": "; ".join(reasons),
                "human_approval_required": bool(source["human_approval_required"]),
                "source_generated_at": generated_at,
                "freshness": generated_freshness,
            }
        )
    return actions


_PROJECTORS: dict[str, Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]] = {
    "audit_violations": _audit_actions,
    "watchdog_proof_debt": _watchdog_actions,
    "merge_review": _merge_actions,
}


def build_target_repository_action_memory(*, target_root: Path, raw_dir: Path) -> dict[str, Any]:
    contract = load_json_object_strict(CONTRACT_PATH, label="target repository action memory contract")
    sources = contract.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("target repository action memory contract must declare sources")
    source_rows: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    unknowns: list[str] = []
    for source in sources:
        if not isinstance(source, dict) or str(source.get("id") or "") not in _PROJECTORS:
            raise ValueError("target repository action memory contract contains an unsupported source")
        source_id = str(source["id"])
        artifact_id = str(source["artifact_id"])
        path = artifact_path_for_storage_root(raw_dir, artifact_id)
        availability = "MISSING"
        freshness = "UNKNOWN"
        generated_at = None
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
            freshness, generated_at = _declared_freshness(payload)
            projected = _PROJECTORS[source_id](payload, source)
        except FileNotFoundError:
            unknowns.append(f"{source_id}:missing")
        except (ValueError, TypeError, json.JSONDecodeError):
            availability = "INVALID"
            unknowns.append(f"{source_id}:invalid")
        if availability == "PRESENT" and freshness == "UNKNOWN":
            unknowns.append(f"{source_id}:freshness_unknown")
        seen: set[str] = set()
        unique = []
        for action in projected:
            identity = str(action["source_identity"])
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(action)
        actions.extend(unique)
        source_rows.append(
            {
                "source_id": source_id,
                "artifact_id": artifact_id,
                "availability": availability,
                "freshness": freshness,
                "generated_at": generated_at,
                "action_count": len(unique),
            }
        )
    priority_rank = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3, "UNKNOWN": 4}
    actions.sort(key=lambda row: (priority_rank.get(str(row["priority"]).upper(), 5), str(row["source_authority"]), str(row["action_id"])))
    present_sources = sum(1 for row in source_rows if row["availability"] == "PRESENT")
    status = "PARTIAL" if present_sources != len(source_rows) else "EMPTY" if not actions else "COMPLETE"
    return {
        "meta": {
            "kind": "target_repository_action_memory",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_target_repository_action_memory",
            "contract_identity": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
        },
        "subject": {"scope": "target_repository", "target_root": str(target_root.resolve())},
        "claim_boundary": str(contract["authority"]["claim_boundary"]),
        "summary": {
            "status": status,
            "declared_sources": len(source_rows),
            "present_sources": present_sources,
            "action_count": len(actions),
            "human_review_actions": sum(1 for row in actions if row["human_approval_required"]),
        },
        "sources": source_rows,
        "actions": actions,
        "unknowns": sorted(set(unknowns)),
    }

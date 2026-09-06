from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR
from tools.core.json_io import load_json_object_strict


CONTRACT_PATH = CONFIG_DIR / "artifact_freshness_contract.json"


def _timestamp_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0.0


def load_artifact_freshness_contract() -> dict[str, Any]:
    return load_json_object_strict(CONTRACT_PATH, label="Artifact freshness contract")


def artifact_state_meta(raw_dir: Path, artifact: str) -> dict[str, Any]:
    db_path = raw_dir / "codemaps.db"
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT source_mtime, updated_at, payload_sha, "
                    "COALESCE(payload_bytes, length(payload)) AS payload_bytes "
                    "FROM state_payloads WHERE name = ?;",
                    (artifact,),
                ).fetchone()
            if row:
                updated_at = row["updated_at"] or ""
                content_mtime = float(row["source_mtime"] or 0.0)
                return {
                    "exists": True,
                    "source": "sqlite_state_payloads",
                    "mtime": content_mtime,
                    "content_mtime": content_mtime,
                    "validation_mtime": _timestamp_epoch(updated_at),
                    "updated_at": updated_at,
                    "payload_sha": str(row["payload_sha"] or ""),
                    "payload_bytes": int(row["payload_bytes"] or 0),
                }
        except Exception as exc:
            return {"exists": False, "source": "sqlite_state_payloads", "mtime": 0.0, "error": str(exc)}
    json_path = raw_dir / f"{artifact}.json"
    if json_path.exists():
        shadow_mtime = float(json_path.stat().st_mtime)
        return {
            "exists": True,
            "source": "json_shadow",
            "mtime": shadow_mtime,
            "content_mtime": shadow_mtime,
            "validation_mtime": shadow_mtime,
            "updated_at": "",
            "payload_sha": "",
            "payload_bytes": int(json_path.stat().st_size),
        }
    return {"exists": False, "source": "missing", "mtime": 0.0, "updated_at": "", "payload_sha": "", "payload_bytes": 0}


def _state_payload_meta(raw_dir: Path, artifact: str) -> dict[str, Any]:
    """Backward-compatible private alias for older contract consumers."""
    return artifact_state_meta(raw_dir, artifact)


def _evaluate_chain(
    raw_dir: Path,
    chain: dict[str, Any],
    *,
    system_scope: str = "SAGE_ON_SAGE",
) -> dict[str, Any]:
    scope_overrides = chain.get("scope_overrides")
    scope_overrides = scope_overrides if isinstance(scope_overrides, dict) else {}
    scope_override = scope_overrides.get(system_scope)
    scope_override = scope_override if isinstance(scope_override, dict) else {}
    effective_chain = {**chain, **scope_override}
    ordered_artifacts = [str(item) for item in effective_chain.get("ordered_artifacts", []) or [] if str(item).strip()]
    required_artifacts = [str(item) for item in effective_chain.get("required_artifacts", []) or [] if str(item).strip()]
    artifact_names = list(dict.fromkeys([*ordered_artifacts, *required_artifacts]))
    artifact_rows = []
    missing = []
    json_shadow_only = []
    for artifact in artifact_names:
        meta = artifact_state_meta(raw_dir, artifact)
        row = {"artifact": artifact, **meta}
        artifact_rows.append(row)
        if not meta.get("exists"):
            missing.append(artifact)
        elif meta.get("source") == "json_shadow":
            json_shadow_only.append(artifact)

    stale_edges = []
    ordered_rows = [row for row in artifact_rows if row.get("artifact") in set(ordered_artifacts)]
    existing_rows = [row for row in ordered_rows if row.get("exists")]
    for earlier, later in zip(existing_rows, existing_rows[1:]):
        producer_validation_mtime = float(earlier.get("validation_mtime") or earlier.get("mtime") or 0.0)
        consumer_validation_mtime = float(later.get("validation_mtime") or later.get("mtime") or 0.0)
        if consumer_validation_mtime + 0.001 < producer_validation_mtime:
            stale_edges.append(
                {
                    "producer": earlier.get("artifact"),
                    "consumer": later.get("artifact"),
                    "freshness_basis": "validation_mtime",
                    "producer_validation_mtime": producer_validation_mtime,
                    "consumer_validation_mtime": consumer_validation_mtime,
                    "producer_content_mtime": earlier.get("content_mtime", earlier.get("mtime")),
                    "consumer_content_mtime": later.get("content_mtime", later.get("mtime")),
                }
            )

    status = "PASS"
    if missing or stale_edges:
        status = "FAIL"
    elif json_shadow_only:
        status = "WARN"
    return {
        "id": chain.get("id"),
        "system_scope": system_scope,
        "scope_override_applied": bool(scope_override),
        "status": status,
        "description": effective_chain.get("description"),
        "consumer_surfaces": effective_chain.get("consumer_surfaces", []),
        "stale_behavior": effective_chain.get("stale_behavior"),
        "agent_message": effective_chain.get("agent_message"),
        "refresh_plan": effective_chain.get("refresh_plan", []),
        "ordered_artifacts": ordered_artifacts,
        "required_artifacts": required_artifacts,
        "artifacts": artifact_rows,
        "missing_artifacts": missing,
        "json_shadow_only_artifacts": json_shadow_only,
        "stale_edges": stale_edges,
    }


def evaluate_artifact_freshness_contract(
    raw_dir: Path | None = None,
    *,
    system_scope: str = "SAGE_ON_SAGE",
) -> dict[str, Any]:
    raw_dir = Path(raw_dir or RAW_DIR)
    contract = load_artifact_freshness_contract()
    chains = [row for row in contract.get("chains", []) if isinstance(row, dict)] if isinstance(contract, dict) else []
    evaluations = [
        _evaluate_chain(raw_dir, chain, system_scope=system_scope)
        for chain in chains
    ]
    failures = [row for row in evaluations if row.get("status") == "FAIL"]
    warnings = [row for row in evaluations if row.get("status") == "WARN"]
    return {
        "meta": {
            "kind": "artifact_freshness_contract_evaluation",
            "version": "v2",
            "contract": "config/artifact_freshness_contract.json",
            "system_scope": system_scope,
        },
        "summary": {
            "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
            "chains": len(evaluations),
            "failed_chains": len(failures),
            "warning_chains": len(warnings),
        },
        "default_policy": contract.get("default_policy", {}) if isinstance(contract, dict) else {},
        "chains": evaluations,
    }


def chain_evaluation(evaluation: dict[str, Any], chain_id: str) -> dict[str, Any]:
    for row in evaluation.get("chains", []) if isinstance(evaluation, dict) else []:
        if isinstance(row, dict) and row.get("id") == chain_id:
            return row
    return {}


def evaluate_named_artifact_chain(chain_id: str, raw_dir: Path | None = None) -> dict[str, Any]:
    """Evaluate one centrally declared artifact chain without scanning unrelated chains."""
    raw_dir = Path(raw_dir or RAW_DIR)
    contract = load_artifact_freshness_contract()
    for chain in contract.get("chains", []) if isinstance(contract, dict) else []:
        if isinstance(chain, dict) and str(chain.get("id") or "") == chain_id:
            return _evaluate_chain(raw_dir, chain)
    return {
        "id": chain_id,
        "status": "FAIL",
        "missing_contract": True,
        "missing_artifacts": [],
        "json_shadow_only_artifacts": [],
        "stale_edges": [],
        "artifacts": [],
    }

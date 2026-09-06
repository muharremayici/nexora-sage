from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_object_strict


def _pipeline_semantic_contract(row: dict[str, Any]) -> dict[str, Any]:
    contract = dict(row)
    contract.pop("index", None)
    invocation = dict(contract.get("invocation_contract") or {})
    closure = dict(invocation.get("explicit_step_closure") or {})
    closure.pop("dependency_closure_count", None)
    closure.pop("dependency_closure_sample", None)
    invocation["explicit_step_closure"] = closure
    contract["invocation_contract"] = invocation
    return contract


def contract_fingerprint(scope: str, row: dict[str, Any]) -> str:
    contract = _pipeline_semantic_contract(row) if scope == "pipeline_steps" else row
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def contract_rows_for_scope(
    scope: str,
    *,
    pipeline_registry_path: Path,
    release_steps_path: Path,
) -> list[dict[str, str]] | None:
    if scope == "pipeline_steps":
        if not pipeline_registry_path.is_file():
            return None
        payload = load_json_object_strict(pipeline_registry_path, label="pipeline step registry")
        rows = payload.get("steps", [])
        id_field = "slug"
    elif scope == "release_proof_steps":
        payload = load_json_object_strict(release_steps_path, label="release proof steps contract")
        rows = payload.get("steps", [])
        id_field = "id"
    else:
        return []
    return [
        {
            "id": str(row[id_field]),
            "fingerprint": contract_fingerprint(scope, row),
        }
        for row in rows
        if isinstance(row, dict) and row.get(id_field)
    ]

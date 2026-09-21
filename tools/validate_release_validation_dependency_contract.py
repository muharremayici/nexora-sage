"""Validate release-proof semantic dependency and fail-closed reuse ownership."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.release_validation_dependencies import (
    CONTRACT_PATH,
    SCHEMA_PATH,
    disposition_by_step,
    load_release_validation_dependency_contract,
    resolve_step_dependency_identity,
    validate_dependency_contract,
)


REPORT_PATH = RAW_DIR / "release_validation_dependency_contract_validation.json"


def _check(check_id: str, ok: bool, details: Any) -> dict[str, Any]:
    return {"id": check_id, "ok": bool(ok), "details": details}


def run_validation(*, root: Path = ROOT) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        contract = load_release_validation_dependency_contract(
            root / "config" / "release_validation_dependency_contract.json"
        )
        ensure_against_schema(
            root
            / "config"
            / "schemas"
            / "release_validation_dependency_contract.schema.json",
            "release_validation_dependency_contract",
            contract,
        )
        checks.append(_check("contract_schema_valid", True, str(SCHEMA_PATH.relative_to(ROOT))))
    except Exception as exc:
        contract = {}
        checks.append(_check("contract_schema_valid", False, f"{type(exc).__name__}: {exc}"))

    if contract:
        errors = validate_dependency_contract(contract, root=root)
        checks.append(_check("contract_semantics_valid", not errors, errors or "semantic contract ok"))
        dispositions = disposition_by_step(contract)
        proof_contract = json.loads(
            (root / "config" / "release_proof_steps_contract.json").read_text(
                encoding="utf-8"
            )
        )
        expected_step_count = len(proof_contract.get("steps", []))
        counts: dict[str, int] = {}
        for disposition in dispositions.values():
            counts[disposition] = counts.get(disposition, 0) + 1
        checks.append(
            _check(
                "all_release_steps_have_one_explicit_disposition",
                len(dispositions) == expected_step_count and "duplicate" not in counts,
                {
                    "step_count": len(dispositions),
                    "expected_step_count": expected_step_count,
                    "counts": counts,
                },
            )
        )
        checks.append(
            _check(
                "engine_suite_stays_global_until_exact_shards_exist",
                dispositions.get("engine_contract_tests")
                == "global_source_fingerprint_until_mapped",
                dispositions.get("engine_contract_tests"),
            )
        )
        try:
            reviewed = resolve_step_dependency_identity(
                "source_layer_classification_policy_validation",
                root=root,
                contract=contract,
            )
            checks.append(
                _check(
                    "reviewed_leaf_semantic_closure_is_complete",
                    reviewed.get("reuse_eligible") is True
                    and reviewed.get("identity_mode") == "semantic_closure_content_identity"
                    and int((reviewed.get("closure") or {}).get("file_count") or 0) > 0,
                    {
                        "reuse_eligible": reviewed.get("reuse_eligible"),
                        "identity_mode": reviewed.get("identity_mode"),
                        "file_count": (reviewed.get("closure") or {}).get("file_count"),
                        "reasons": reviewed.get("reasons"),
                    },
                )
            )
        except Exception as exc:
            checks.append(
                _check(
                    "reviewed_leaf_semantic_closure_is_complete",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            )

    failed = [row["id"] for row in checks if not row["ok"]]
    return {
        "meta": {
            "kind": "release_validation_dependency_contract_validation",
            "version": "v1",
            "source": str(CONTRACT_PATH.relative_to(ROOT)),
            "authority": "validation_only_no_release_or_publication_authority",
        },
        "status": "PASS" if not failed else "FAIL",
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": failed,
        },
        "checks": checks,
    }


def main() -> int:
    payload = run_validation()
    save_json_atomic(REPORT_PATH, payload, indent=2)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

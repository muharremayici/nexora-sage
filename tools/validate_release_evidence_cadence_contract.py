"""Validate the central release evidence cadence and decision contract."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ensure_against_schema
from tools.core.json_io import load_json_object_strict
from tools.core.release_evidence_cadence import (
    select_release_evidence,
    validate_release_evidence_cadence_contract,
)


def _check(check_id: str, ok: bool, details: Any) -> dict[str, Any]:
    return {"id": check_id, "ok": bool(ok), "details": details}


def _acceptance_checks(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for case in contract.get("acceptance_cases", []):
        case_id = str(case.get("id") or "unnamed")
        try:
            selection = select_release_evidence(case["context"], contract=contract)
            actual = {
                evidence_id: selection["decisions"][evidence_id]["disposition"]
                for evidence_id in case["expected"]
            }
            expected = dict(case["expected"])
            checks.append(
                _check(
                    f"acceptance_case:{case_id}",
                    actual == expected,
                    {"expected": expected, "actual": actual},
                )
            )
        except Exception as exc:
            checks.append(_check(f"acceptance_case:{case_id}", False, str(exc)))
    return checks


def _work_item_check(contract: Mapping[str, Any], root: Path) -> dict[str, Any]:
    registry = load_json_object_strict(
        root / "config" / "sage_work_item_registry.json",
        label="SAGE work-item registry",
    )
    rows = {
        str(row.get("id")): row
        for row in registry.get("work_items", [])
        if isinstance(row, Mapping) and str(row.get("id") or "").strip()
    }
    target_release = str(contract["governance"]["target_release"])
    required = set(contract["validation"]["required_work_item_ids"])
    declared = set(contract["governance"]["work_item_ids"])
    missing = sorted(declared - set(rows))
    wrong_release = sorted(
        item_id
        for item_id in declared & set(rows)
        if str(rows[item_id].get("target_release") or "") != target_release
    )
    return _check(
        "owned_by_existing_target_release_work",
        not missing and not wrong_release and required.issubset(declared),
        {
            "target_release": target_release,
            "declared_work_items": sorted(declared),
            "required_work_items": sorted(required),
            "missing_work_items": missing,
            "wrong_target_release": wrong_release,
        },
    )


def _proof_operating_model_check(root: Path) -> dict[str, Any]:
    proof_scope = load_json_object_strict(
        root / "config" / "release_proof_scope_contract.json",
        label="Release proof scope contract",
    )
    operating_model = proof_scope.get("proof_operating_model", {})
    development = (
        operating_model.get("development", {})
        if isinstance(operating_model, Mapping)
        else {}
    )
    frozen = (
        operating_model.get("frozen_candidate", {})
        if isinstance(operating_model, Mapping)
        else {}
    )
    details = {
        "development_execution": development.get("execution"),
        "development_full_bundle_by_default": development.get(
            "full_bundle_by_default"
        ),
        "frozen_candidate_execution": frozen.get("execution"),
    }
    return _check(
        "cadence_preserves_existing_release_proof_owner",
        details["development_execution"] == "selected_dependency_closure"
        and details["development_full_bundle_by_default"] is False
        and details["frozen_candidate_execution"]
        == "evaluate_all_receipts_execute_invalidated_and_always_fresh",
        details,
    )


def _release_proof_command_metadata_check(root: Path) -> dict[str, Any]:
    cli_contract = load_json_object_strict(
        root / "config" / "cli_command_contract.json",
        label="CLI command contract",
    )
    pipeline_policy = load_json_object_strict(
        root / "config" / "pipeline_execution_policy.json",
        label="Pipeline execution policy",
    )
    cli_rows = [
        row
        for row in cli_contract.get("commands", [])
        if isinstance(row, Mapping) and row.get("id") == "release_proof_bundle"
    ]
    cli_surface = str(cli_rows[0].get("surface") or "") if len(cli_rows) == 1 else ""
    validator_rows = (
        (pipeline_policy.get("validator_preconditions") or {}).get("validators") or {}
        if isinstance(pipeline_policy, Mapping)
        else {}
    )
    pipeline_row = (
        validator_rows.get("run_release_proof_bundle", {})
        if isinstance(validator_rows, Mapping)
        else {}
    )
    pipeline_command = str(pipeline_row.get("command") or "")
    expected_cli = (
        "python tools/run_release_proof_bundle.py --release-phase frozen_candidate "
        "--trigger governed_source_changed"
    )
    expected_pipeline = (
        "python -B tools\\run_release_proof_bundle.py --release-phase frozen_candidate "
        "--trigger governed_source_changed"
    )
    details = {
        "cli_surface": cli_surface,
        "pipeline_command": pipeline_command,
        "required_phase": "frozen_candidate",
        "semantic_trigger": "governed_source_changed",
    }
    return _check(
        "release_proof_command_metadata_is_fail_closed",
        cli_surface == expected_cli
        and pipeline_command == expected_pipeline
        and "version_identity_changed" not in cli_surface
        and "version_identity_changed" not in pipeline_command,
        details,
    )


def run_validation(root: Path = ROOT) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        contract = load_json_object_strict(
            root / "config" / "release_evidence_cadence_contract.json",
            label="Release evidence cadence contract",
        )
        ensure_against_schema(
            root
            / "config"
            / "schemas"
            / "release_evidence_cadence_contract.schema.json",
            "release_evidence_cadence_contract",
            contract,
        )
        checks.append(_check("contract_schema_valid", True, "schema ok"))
    except Exception as exc:
        contract = {}
        checks.append(_check("contract_schema_valid", False, str(exc)))

    if contract:
        try:
            validate_release_evidence_cadence_contract(contract, root=root)
            checks.append(_check("contract_semantics_valid", True, "semantic contract ok"))
        except Exception as exc:
            checks.append(_check("contract_semantics_valid", False, str(exc)))

        try:
            checks.append(_work_item_check(contract, root))
        except Exception as exc:
            checks.append(_check("owned_by_existing_target_release_work", False, str(exc)))
        try:
            checks.append(_proof_operating_model_check(root))
        except Exception as exc:
            checks.append(
                _check("cadence_preserves_existing_release_proof_owner", False, str(exc))
            )
        try:
            checks.append(_release_proof_command_metadata_check(root))
        except Exception as exc:
            checks.append(
                _check("release_proof_command_metadata_is_fail_closed", False, str(exc))
            )
        checks.extend(_acceptance_checks(contract))

    failed = [row["id"] for row in checks if not row["ok"]]
    return {
        "meta": {
            "kind": "release_evidence_cadence_contract_validation",
            "version": "1.0.0",
            "source": "config/release_evidence_cadence_contract.json",
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
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

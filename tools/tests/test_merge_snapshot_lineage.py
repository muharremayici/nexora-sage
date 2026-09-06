from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.core.analysis_snapshot_lineage import (
    evaluate_snapshot_bound_inputs,
    payload_sha256,
    receipt_path,
)
from tools.core.config import CONFIG_DIR


def _write_receipt(raw_dir: Path, artifact_id: str, payload: object, snapshot_id: str) -> None:
    path = receipt_path(raw_dir, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "meta": {
                    "kind": "analysis_snapshot_lineage",
                    "version": "v1",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
                "artifact_id": artifact_id,
                "producer": "test.fixture",
                "status": "COMPLETE",
                "atlas_snapshot_id": snapshot_id,
                "artifact_sha256": payload_sha256(payload),
                "atlas_sha256": "a" * 64,
                "dependencies": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )


def test_merge_input_contract_omits_unbound_optional_evidence(tmp_path: Path) -> None:
    snapshot_id = "snapshot-a"
    packages = {"packages": []}
    smoke = {"runs": []}
    _write_receipt(tmp_path, "merge_dependency_packages", packages, snapshot_id)
    _write_receipt(tmp_path, "ui_smoke_execution", smoke, snapshot_id)

    evidence, usable = evaluate_snapshot_bound_inputs(
        contract_path=CONFIG_DIR / "merge_simulation_input_contract.json",
        raw_dir=tmp_path,
        expected_snapshot_id=snapshot_id,
        payloads={
            "merge_dependency_packages": packages,
            "ui_smoke_execution": smoke,
            "ts_diagnostics": {"projects": {}},
        },
    )

    assert evidence["status"] == "PARTIAL_CONTEXT"
    assert evidence["omitted_inputs"] == ["ts_diagnostics"]
    assert set(usable) == {"merge_dependency_packages", "ui_smoke_execution"}


def test_merge_input_contract_blocks_missing_required_evidence(tmp_path: Path) -> None:
    snapshot_id = "snapshot-a"
    packages = {"packages": []}
    _write_receipt(tmp_path, "merge_dependency_packages", packages, snapshot_id)

    evidence, usable = evaluate_snapshot_bound_inputs(
        contract_path=CONFIG_DIR / "merge_simulation_input_contract.json",
        raw_dir=tmp_path,
        expected_snapshot_id=snapshot_id,
        payloads={"merge_dependency_packages": packages},
    )

    assert evidence["status"] == "BLOCKED"
    assert evidence["blocked_inputs"] == ["ui_smoke_execution"]
    assert usable == {"merge_dependency_packages": packages}

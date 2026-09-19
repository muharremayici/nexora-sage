from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.core.artifact_validator import validate_payload
from tools.core.artifact_registry import artifact_path_for_storage_root
from tools.core.analysis_snapshot_lineage import write_lineage_receipt
from tools.core.analysis_scope_authority import (
    bind_atlas_materialization,
    build_preflight_scope_authority,
)
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.json_io import load_json_file
from tools.core.target_repository_proof import build_target_repository_proof


def _raw_dir(target_root: Path) -> Path:
    return target_root / ".raw"


def _path(target_root: Path, artifact_id: str) -> Path:
    return artifact_path_for_storage_root(_raw_dir(target_root), artifact_id)


def _write(target_root: Path, artifact_id: str, payload: object) -> None:
    path = _path(target_root, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read(raw_dir: Path, artifact_id: str) -> dict:
    return load_json_file(_path(raw_dir, artifact_id), {})


def _baseline(
    target_root: Path,
    *,
    projects: list[str] | str | None = None,
) -> str:
    raw_dir = _raw_dir(target_root)
    basis = datetime.now(timezone.utc) - timedelta(seconds=2)
    atlas = {"MAIN": {"project": {"root": str(target_root)}, "files": {}}}
    commit = build_atlas_commit(atlas, generated_at=basis.isoformat())
    snapshot_id = commit["snapshot_id"]
    _write(target_root, "atlas", atlas)
    _write(target_root, "atlas_commit", commit)
    scope_authority = build_preflight_scope_authority(
        topology={
            "ontology_contract": "canonical_repository_topology_v1",
            "selection_mode": "evidence_backed_auto",
            "project_candidates": {"MAIN": str(target_root)},
            "project_candidate_relationship_roles": {"MAIN": "host"},
            "project_candidate_role_authority": {
                "MAIN": {"relationship_role": "host", "relationship_resolved": True}
            },
            "selected_projects": {"MAIN": str(target_root)},
            "excluded_projects": {},
            "excluded_project_reasons": {},
        },
        projects=projects,
        repository_file_count=0,
        repository_inventory_truncated=False,
        repository_language_counts={},
        effective_file_count=0,
        effective_inventory_truncated=False,
        effective_language_counts={},
        effective_project_file_counts={"MAIN": 0},
        polyglot_capabilities={"languages": {}},
        effective_project_inventory_evidence={"MAIN": {}},
    )
    scope_artifact = {
        "meta": {
            "kind": "analysis_scope_authority",
            "version": "v1",
            "stage": "POST_ATLAS",
            "authority": "shared_repository_analysis_scope",
        },
        "scope_authority": bind_atlas_materialization(scope_authority, atlas),
    }
    _write(target_root, "analysis_scope_authority", scope_artifact)
    write_lineage_receipt(
        artifact_id="analysis_scope_authority",
        producer="tools.orchestrators.orchestrator",
        artifact_payload=scope_artifact,
        atlas=atlas,
        atlas_commit=commit,
        raw_dir=raw_dir,
    )
    genome = {"Example": []}
    audit = {"summary": {"total": 2}}
    quality = {"passed": True}
    _write(target_root, "genome", genome)
    _write(target_root, "audit_report", audit)
    _write(target_root, "quality_gate", quality)
    write_lineage_receipt(artifact_id="genome", producer="tools.engines.nuclear_processor", artifact_payload=genome, atlas=atlas, atlas_commit=commit, raw_dir=raw_dir)
    write_lineage_receipt(
        artifact_id="audit_report",
        producer="tools.engines.audit",
        artifact_payload=audit,
        atlas=atlas,
        atlas_commit=commit,
        dependency_payloads={"analysis_scope_authority": scope_artifact},
        raw_dir=raw_dir,
    )
    write_lineage_receipt(
        artifact_id="quality_gate",
        producer="tools.engines.quality_gate",
        artifact_payload=quality,
        atlas=atlas,
        atlas_commit=commit,
        dependency_payloads={
            "analysis_scope_authority": scope_artifact,
            "genome": genome,
            "audit_report": audit,
        },
        raw_dir=raw_dir,
    )
    return basis.isoformat()


def _write_merge_lineage(target_root: Path, cockpit: dict) -> None:
    raw_dir = _raw_dir(target_root)
    atlas = _read(target_root, "atlas")
    commit = _read(target_root, "atlas_commit")
    payloads = {
        "genome": _read(target_root, "genome"),
        "surgical_discovery": [],
        "fractal_map": {"projects": {}},
        "framework_routes": {"routes": []},
        "host_merge_intelligence": {"studios": {}},
        "ui_runtime_contracts": {"merge_candidates": []},
        "ui_smoke_specs": {"specs": []},
        "ui_smoke_execution": {"runs": []},
        "merge_dependency_packages": {"packages": []},
        "merge_simulation": {"simulations": []},
        "merge_decision_cockpit": cockpit,
    }
    producers = {
        "surgical_discovery": "tools.engines.nuclear_processor",
        "fractal_map": "tools.engines.fractal_mapper",
        "framework_routes": "tools.engines.framework_route_analyzer",
        "host_merge_intelligence": "tools.engines.host_merge_intelligence",
        "ui_runtime_contracts": "tools.engines.ui_runtime_contract_analyzer",
        "ui_smoke_specs": "tools.engines.ui_smoke_spec_generator",
        "ui_smoke_execution": "tools.engines.ui_smoke_execution_report",
        "merge_dependency_packages": "tools.engines.merge_dependency_packager",
        "merge_simulation": "tools.engines.merge_simulation_engine",
        "merge_decision_cockpit": "tools.engines.merge_decision_cockpit",
    }
    dependencies = {
        "surgical_discovery": [],
        "fractal_map": ["genome", "surgical_discovery"],
        "framework_routes": [],
        "host_merge_intelligence": ["fractal_map"],
        "ui_runtime_contracts": ["framework_routes", "host_merge_intelligence"],
        "ui_smoke_specs": ["ui_runtime_contracts"],
        "ui_smoke_execution": ["ui_smoke_specs"],
        "merge_dependency_packages": ["ui_runtime_contracts"],
        "merge_simulation": ["merge_dependency_packages", "ui_smoke_execution"],
        "merge_decision_cockpit": [
            "merge_simulation",
            "ui_runtime_contracts",
            "merge_dependency_packages",
            "ui_smoke_specs",
        ],
    }
    for artifact_id in producers:
        _write(target_root, artifact_id, payloads[artifact_id])
        write_lineage_receipt(
            artifact_id=artifact_id,
            producer=producers[artifact_id],
            artifact_payload=payloads[artifact_id],
            atlas=atlas,
            atlas_commit=commit,
            dependency_payloads={dep: payloads[dep] for dep in dependencies[artifact_id]},
            raw_dir=raw_dir,
        )


def test_baseline_bundle_is_scoped_and_schema_valid(tmp_path: Path) -> None:
    basis = _baseline(tmp_path)
    payload = build_target_repository_proof(
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        mode="baseline",
        repository_reference="snapshot-a",
        evidence_not_before=basis,
    )
    assert payload["summary"]["verdict"] == "PASS"
    assert payload["subject"]["scope"] == "target_repository"
    assert payload["subject"]["analysis_snapshot_kind"] == "atlas_commit"
    assert payload["subject"]["root_binding"] == "BOUND"
    assert payload["statistics"] == [
        {
            "id": "audit_finding_count",
            "artifact_id": "audit_report",
            "value": 2,
            "decision_use": "target_remediation_triage",
            "scope": "target_analysis_snapshot",
            "denominator": None,
            "claim_boundary": "absolute_count_not_rate_or_quality_score",
        }
    ]
    assert validate_payload("target_repository_proof_bundle", payload) == []


def test_missing_or_failed_required_evidence_blocks(tmp_path: Path) -> None:
    basis = _baseline(tmp_path)
    _path(tmp_path, "quality_gate").unlink()
    missing = build_target_repository_proof(
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        mode="baseline",
        repository_reference="snapshot-a",
        evidence_not_before=basis,
    )
    assert missing["summary"]["verdict"] == "BLOCKED"
    assert "quality_gate:missing" in missing["unknowns"]

    atlas = _read(tmp_path, "atlas")
    commit = _read(tmp_path, "atlas_commit")
    genome = _read(tmp_path, "genome")
    audit = _read(tmp_path, "audit_report")
    quality = {"passed": False}
    _write(tmp_path, "quality_gate", quality)
    write_lineage_receipt(artifact_id="quality_gate", producer="tools.engines.quality_gate", artifact_payload=quality, atlas=atlas, atlas_commit=commit, dependency_payloads={"genome": genome, "audit_report": audit}, raw_dir=_raw_dir(tmp_path))
    failed = build_target_repository_proof(
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        mode="baseline",
        repository_reference="snapshot-a",
        evidence_not_before=basis,
    )
    assert failed["summary"]["verdict"] == "BLOCKED"


def test_atlas_commit_supplies_freshness_and_snapshot_authority(tmp_path: Path) -> None:
    _baseline(tmp_path)
    payload = build_target_repository_proof(target_root=tmp_path, raw_dir=_raw_dir(tmp_path), mode="baseline")
    assert payload["summary"]["verdict"] == "PASS"
    assert payload["subject"]["repository_reference_kind"] == "unavailable"


def test_caller_snapshot_cannot_bind_unrelated_target_or_evidence(tmp_path: Path) -> None:
    _baseline(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    wrong_root = build_target_repository_proof(
        target_root=unrelated,
        raw_dir=_raw_dir(tmp_path),
        mode="baseline",
        repository_reference="plausible-commit",
    )
    assert wrong_root["summary"]["verdict"] == "BLOCKED"
    assert wrong_root["subject"]["root_binding"] == "MISMATCH"

    _write(tmp_path, "quality_gate", {"passed": True, "tampered_after_production": True})
    wrong_evidence = build_target_repository_proof(
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        mode="baseline",
        repository_reference="plausible-commit",
    )
    assert wrong_evidence["summary"]["verdict"] == "BLOCKED"
    assert "quality_gate:snapshot_mismatch" in wrong_evidence["unknowns"]


def test_missing_or_tampered_atlas_commit_blocks(tmp_path: Path) -> None:
    _baseline(tmp_path)
    _path(tmp_path, "atlas_commit").unlink()
    missing = build_target_repository_proof(target_root=tmp_path, raw_dir=_raw_dir(tmp_path), mode="baseline")
    assert missing["summary"]["verdict"] == "BLOCKED"

    _baseline(tmp_path)
    commit = _read(tmp_path, "atlas_commit")
    commit["atlas_sha256"] = "0" * 64
    _write(tmp_path, "atlas_commit", commit)
    tampered = build_target_repository_proof(target_root=tmp_path, raw_dir=_raw_dir(tmp_path), mode="baseline")
    assert tampered["summary"]["verdict"] == "BLOCKED"
    assert "analysis_snapshot:invalid_or_unavailable" in tampered["unknowns"]


def test_merge_bundle_requires_human_decision(tmp_path: Path) -> None:
    basis = _baseline(tmp_path)
    _write_merge_lineage(tmp_path, {"meta": {"kind": "merge_decision_cockpit"}, "summary": {"candidates": 4}})
    payload = build_target_repository_proof(
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        mode="merge",
        repository_reference="snapshot-a",
        evidence_not_before=basis,
    )
    assert payload["summary"]["verdict"] == "REVIEW_REQUIRED"
    assert payload["human_decisions"] == ["review_merge_decision_cockpit"]
    assert payload["statistics"][-1]["value"] == 4


def test_target_proof_binds_explicit_project_scope_to_scope_authority(
    tmp_path: Path,
) -> None:
    _baseline(tmp_path, projects=["MAIN"])

    payload = build_target_repository_proof(
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        mode="baseline",
        projects="main",
    )

    assert payload["summary"]["verdict"] == "PASS"
    assert payload["subject"]["requested_projects"] == ["MAIN"]
    assert payload["subject"]["effective_projects"] == ["MAIN"]
    assert payload["subject"]["project_scope_binding"] == "BOUND"
    assert payload["subject"]["scope_authority_id"]


def test_target_proof_blocks_requested_project_scope_mismatch(
    tmp_path: Path,
) -> None:
    _baseline(tmp_path, projects=["MAIN"])

    payload = build_target_repository_proof(
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        mode="baseline",
        projects="OTHER",
    )

    assert payload["summary"]["verdict"] == "BLOCKED"
    assert payload["subject"]["project_scope_binding"] == "MISMATCH"
    assert "requested_project_scope:mismatch" in payload["unknowns"]


def test_target_proof_rejects_non_bounded_scope_status_for_explicit_projects(
    tmp_path: Path,
) -> None:
    _baseline(tmp_path, projects=["MAIN"])
    scope = _read(tmp_path, "analysis_scope_authority")
    scope["scope_authority"]["evidence_status"] = "COMPLETE_REPOSITORY"
    _write(tmp_path, "analysis_scope_authority", scope)

    payload = build_target_repository_proof(
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        mode="baseline",
        projects="MAIN",
    )

    assert payload["summary"]["verdict"] == "BLOCKED"
    assert payload["subject"]["project_scope_binding"] == "MISMATCH"
    assert payload["subject"]["scope_evidence_status"] == "COMPLETE_REPOSITORY"

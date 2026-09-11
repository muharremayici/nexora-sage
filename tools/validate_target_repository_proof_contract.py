from __future__ import annotations

import ast
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_registry import ARTIFACT_SCHEMAS, artifact_metadata, artifact_path_for_storage_root
from tools.core.analysis_snapshot_lineage import write_lineage_receipt
from tools.core.analysis_scope_authority import (
    bind_atlas_materialization,
    build_preflight_scope_authority,
)
from tools.core.artifact_validator import validate_payload
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.release_proof_steps import load_release_proof_step_ids
from tools.core.target_repository_proof import CONTRACT_PATH, build_target_repository_proof


RAW_OUTPUT = RAW_DIR / "target_repository_proof_contract_validation.json"
REPORT_OUTPUT = REPORTS_DIR / "target_repository_proof_contract_validation.md"


def _check(name: str, passed: bool, details: object) -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def _fixture_path(raw_dir: Path, artifact_id: str) -> Path:
    return artifact_path_for_storage_root(raw_dir, artifact_id)


def _write_fixture(raw_dir: Path, artifact_id: str, payload: object) -> None:
    path = _fixture_path(raw_dir, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_merge_fixture_lineage(raw_dir: Path, atlas: dict, commit: dict, genome: dict, cockpit: dict) -> None:
    payloads = {
        "genome": genome,
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
        "merge_decision_cockpit": ["merge_simulation", "ui_runtime_contracts", "merge_dependency_packages", "ui_smoke_specs"],
    }
    for artifact_id in producers:
        _write_fixture(raw_dir, artifact_id, payloads[artifact_id])
        write_lineage_receipt(
            artifact_id=artifact_id,
            producer=producers[artifact_id],
            artifact_payload=payloads[artifact_id],
            atlas=atlas,
            atlas_commit=commit,
            dependency_payloads={dependency: payloads[dependency] for dependency in dependencies[artifact_id]},
            raw_dir=raw_dir,
        )


def _producer_lineage_declarations(lineage_contract: dict[str, object]) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for artifact_id, row in lineage_contract.get("artifacts", {}).items():
        if not isinstance(row, dict):
            results[str(artifact_id)] = False
            continue
        producer = str(row.get("producer") or "")
        source_path = ROOT / (producer.replace(".", "/") + ".py")
        if not source_path.is_file():
            results[str(artifact_id)] = False
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        matched = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if function_name != "write_current_atlas_lineage":
                continue
            keywords = {
                keyword.arg: keyword.value.value
                for keyword in node.keywords
                if keyword.arg and isinstance(keyword.value, ast.Constant)
            }
            if keywords.get("artifact_id") == artifact_id and keywords.get("producer") == producer:
                matched = True
                break
        results[str(artifact_id)] = matched
    return results


def _lineage_storage_access_contract() -> dict[str, bool]:
    source_path = ROOT / "tools" / "core" / "analysis_snapshot_lineage.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    return {
        "registry_owns_receipt_paths": any(
            isinstance(node.func, ast.Name) and node.func.id == "artifact_path_for_storage_root"
            for node in calls
        ),
        "atlas_commit_reads_sqlite_first": any(
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "load_raw"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "atlas_commit"
            for node in calls
        ),
        "receipt_writes_use_managed_raw_proxy": any(
            isinstance(node.func, ast.Name)
            and node.func.id == "save_json_atomic"
            and node.args
            and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "receipt_path"
            for node in calls
        ),
    }


def _fixture_results() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="sage_target_proof_") as temp_name:
        target_root = Path(temp_name)
        raw_dir = target_root / ".raw"
        basis = datetime.now(timezone.utc) - timedelta(seconds=2)
        atlas = {"MAIN": {"project": {"root": str(target_root)}, "files": {}}}
        commit = build_atlas_commit(atlas, generated_at=basis.isoformat())
        _write_fixture(raw_dir, "atlas", atlas)
        _write_fixture(raw_dir, "atlas_commit", commit)
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
            projects=None,
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
        _write_fixture(raw_dir, "analysis_scope_authority", scope_artifact)
        write_lineage_receipt(
            artifact_id="analysis_scope_authority",
            producer="tools.orchestrators.orchestrator",
            artifact_payload=scope_artifact,
            atlas=atlas,
            atlas_commit=commit,
            raw_dir=raw_dir,
        )
        genome = {"Example": []}
        audit = {"summary": {"total": 3}}
        quality = {"passed": True}
        _write_fixture(raw_dir, "genome", genome)
        _write_fixture(raw_dir, "audit_report", audit)
        _write_fixture(raw_dir, "quality_gate", quality)
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
        clean = build_target_repository_proof(
            target_root=target_root,
            raw_dir=raw_dir,
            mode="baseline",
            repository_reference="fixture-snapshot",
            evidence_not_before=basis.isoformat(),
        )
        schema_errors = validate_payload("target_repository_proof_bundle", clean)

        _fixture_path(raw_dir, "quality_gate").unlink()
        missing = build_target_repository_proof(
            target_root=target_root,
            raw_dir=raw_dir,
            mode="baseline",
            repository_reference="fixture-snapshot",
            evidence_not_before=basis.isoformat(),
        )
        quality = {"passed": False}
        _write_fixture(raw_dir, "quality_gate", quality)
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
        blocked = build_target_repository_proof(
            target_root=target_root,
            raw_dir=raw_dir,
            mode="baseline",
            repository_reference="fixture-snapshot",
            evidence_not_before=basis.isoformat(),
        )
        quality = {"passed": True}
        _write_fixture(raw_dir, "quality_gate", quality)
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
        _write_merge_fixture_lineage(
            raw_dir,
            atlas,
            commit,
            genome,
            {"meta": {"kind": "merge_decision_cockpit"}, "summary": {"candidates": 1}},
        )
        review = build_target_repository_proof(
            target_root=target_root,
            raw_dir=raw_dir,
            mode="merge",
            repository_reference="fixture-snapshot",
            evidence_not_before=basis.isoformat(),
        )
        unrelated = target_root / "unrelated"
        unrelated.mkdir()
        wrong_root = build_target_repository_proof(
            target_root=unrelated,
            raw_dir=raw_dir,
            mode="baseline",
            repository_reference="plausible-but-nonauthoritative",
        )
        _write_fixture(raw_dir, "quality_gate", {"passed": True, "tampered_after_production": True})
        unbound = build_target_repository_proof(
            target_root=target_root,
            raw_dir=raw_dir,
            mode="baseline",
            repository_reference="plausible-but-nonauthoritative",
        )
        _write_fixture(raw_dir, "quality_gate", quality)
        tampered_commit = dict(commit)
        tampered_commit["atlas_sha256"] = "0" * 64
        _write_fixture(raw_dir, "atlas_commit", tampered_commit)
        tampered = build_target_repository_proof(
            target_root=target_root,
            raw_dir=raw_dir,
            mode="baseline",
        )
        return {
            "clean_verdict": clean["summary"]["verdict"],
            "missing_verdict": missing["summary"]["verdict"],
            "blocked_verdict": blocked["summary"]["verdict"],
            "review_verdict": review["summary"]["verdict"],
            "wrong_root_verdict": wrong_root["summary"]["verdict"],
            "unbound_verdict": unbound["summary"]["verdict"],
            "tampered_verdict": tampered["summary"]["verdict"],
            "schema_errors": schema_errors,
        }


def build_validation() -> dict[str, object]:
    contract = load_json_object_strict(CONTRACT_PATH, label="target repository proof contract")
    governance_registry = load_json_object_strict(ROOT / "config" / "governance_registry.json", label="governance registry")
    registry = artifact_metadata()
    authority = contract.get("authority", {})
    modes = contract.get("modes", {})
    evidence_contracts = contract.get("evidence_contracts", {})
    snapshot_binding_contract = contract.get("snapshot_binding", {})
    allowed_snapshot_bindings = {
        str(value)
        for value in snapshot_binding_contract.get("allowed_modes", [])
        if str(value)
    }
    lineage_contract_path = ROOT / str(snapshot_binding_contract.get("producer_lineage_contract") or "")
    lineage_contract = load_json_object_strict(lineage_contract_path, label="analysis snapshot lineage contract") if lineage_contract_path.is_file() else {}
    lineage_artifact_ids = set(lineage_contract.get("artifacts", {}))
    lineage_receipt_artifact_ids = {
        str(row.get("receipt_artifact_id") or "")
        for row in lineage_contract.get("artifacts", {}).values()
        if isinstance(row, dict) and str(row.get("receipt_artifact_id") or "")
    }
    receipt_bound_ids = {
        artifact_id
        for artifact_id, row in evidence_contracts.items()
        if isinstance(row, dict) and row.get("snapshot_binding") == "producer_lineage_receipt"
    }
    transitional_pointer_ids = {
        str(value)
        for value in snapshot_binding_contract.get("transitional_payload_pointer_artifacts", [])
        if str(value)
    }
    actual_pointer_ids = {
        artifact_id
        for artifact_id, row in evidence_contracts.items()
        if isinstance(row, dict) and row.get("snapshot_binding") == "payload_pointer"
    }
    producer_lineage_declarations = _producer_lineage_declarations(lineage_contract)
    lineage_storage_access = _lineage_storage_access_contract()
    all_mode_ids = {
        str(artifact_id)
        for mode in modes.values()
        if isinstance(mode, dict)
        for key in ("required_evidence", "optional_evidence")
        for artifact_id in mode.get(key, [])
    }
    forbidden = set(authority.get("forbidden_authority_sources", []))
    fixture = _fixture_results()
    governance_surface_ids = {
        str(row.get("id"))
        for row in governance_registry.get("governance_surfaces", [])
        if isinstance(row, dict)
    }
    release_step_ids = load_release_proof_step_ids()
    checks = [
        _check("contract_kind", contract.get("meta", {}).get("kind") == "target_repository_proof_contract", contract.get("meta")),
        _check("three_operation_modes", set(modes) == {"baseline", "change", "merge"}, sorted(modes)),
        _check("evidence_is_centrally_registered", all_mode_ids <= set(registry), sorted(all_mode_ids - set(registry))),
        _check("evidence_contracts_are_complete", all_mode_ids == set(evidence_contracts), {"mode_ids": sorted(all_mode_ids), "contract_ids": sorted(evidence_contracts)}),
        _check(
            "atlas_commit_is_required_in_every_mode",
            all("atlas_commit" in mode.get("required_evidence", []) for mode in modes.values() if isinstance(mode, dict)),
            {mode_id: mode.get("required_evidence", []) for mode_id, mode in modes.items() if isinstance(mode, dict)},
        ),
        _check(
            "every_evidence_contract_declares_snapshot_binding",
            bool(allowed_snapshot_bindings)
            and all(row.get("snapshot_binding") in allowed_snapshot_bindings for row in evidence_contracts.values() if isinstance(row, dict)),
            evidence_contracts,
        ),
        _check(
            "producer_lineage_binding_has_canonical_contract",
            snapshot_binding_contract.get("producer_lineage_contract") == "config/analysis_snapshot_lineage_contract.json"
            and lineage_contract_path.is_file(),
            snapshot_binding_contract,
        ),
        _check("producer_lineage_artifacts_are_owned", receipt_bound_ids <= lineage_artifact_ids, sorted(receipt_bound_ids - lineage_artifact_ids)),
        _check(
            "lineage_receipts_are_sqlite_first_registered_artifacts",
            lineage_contract.get("receipt", {}).get("storage") == "artifact_registry_sqlite_first_with_json_shadow"
            and lineage_receipt_artifact_ids <= set(registry)
            and all(registry[artifact_id]["schema"].endswith("analysis_snapshot_lineage.schema.json") for artifact_id in lineage_receipt_artifact_ids),
            {"receipt_artifact_ids": sorted(lineage_receipt_artifact_ids), "unregistered": sorted(lineage_receipt_artifact_ids - set(registry))},
        ),
        _check("lineage_storage_access_uses_canonical_authority", all(lineage_storage_access.values()), lineage_storage_access),
        _check("registered_producers_emit_lineage_receipts", bool(producer_lineage_declarations) and all(producer_lineage_declarations.values()), producer_lineage_declarations),
        _check("payload_pointer_transition_is_explicit", actual_pointer_ids == transitional_pointer_ids, {"actual": sorted(actual_pointer_ids), "declared": sorted(transitional_pointer_ids)}),
        _check(
            "caller_reference_is_explicitly_nonauthoritative",
            "caller-provided repository reference is descriptive only" in str(authority.get("subject_identity_rule") or ""),
            authority.get("subject_identity_rule"),
        ),
        _check("sage_release_authority_is_excluded", {"release_proof_bundle", "release_readiness", "system_health_check"} <= forbidden and not (all_mode_ids & forbidden), sorted(forbidden)),
        _check("bundle_has_dedicated_schema", "target_repository_proof_bundle" in ARTIFACT_SCHEMAS and ARTIFACT_SCHEMAS["target_repository_proof_bundle"].name == "target_repository_proof_bundle.schema.json", str(ARTIFACT_SCHEMAS.get("target_repository_proof_bundle", ""))),
        _check("governance_surface_is_registered", "target_repository_proof_contract" in governance_surface_ids, sorted(governance_surface_ids)),
        _check("release_proof_step_is_registered", "target_repository_proof_contract" in release_step_ids, sorted(release_step_ids)),
        _check("clean_fixture_passes", fixture["clean_verdict"] == "PASS", fixture),
        _check("missing_required_evidence_blocks", fixture["missing_verdict"] == "BLOCKED", fixture),
        _check("failed_source_gate_blocks", fixture["blocked_verdict"] == "BLOCKED", fixture),
        _check("merge_evidence_requires_human_review", fixture["review_verdict"] == "REVIEW_REQUIRED", fixture),
        _check("caller_reference_cannot_bind_unrelated_root", fixture["wrong_root_verdict"] == "BLOCKED", fixture),
        _check("unbound_required_evidence_blocks", fixture["unbound_verdict"] == "BLOCKED", fixture),
        _check("tampered_atlas_commit_blocks", fixture["tampered_verdict"] == "BLOCKED", fixture),
        _check("generated_bundle_matches_schema", not fixture["schema_errors"], fixture["schema_errors"]),
        _check(
            "statistics_are_bounded_and_actionable",
            all(
                isinstance(row, dict)
                and row.get("artifact_id") in all_mode_ids
                and bool(row.get("pointer"))
                and bool(row.get("decision_use"))
                for row in contract.get("statistics", {}).get("definitions", [])
            ),
            contract.get("statistics"),
        ),
    ]
    failures = [row for row in checks if not row["passed"]]
    return {
        "meta": {"kind": "target_repository_proof_contract_validation", "version": "v1", "generated_at": datetime.now(timezone.utc).isoformat()},
        "summary": {"status": "FAIL" if failures else "PASS", "checks": len(checks), "passed": len(checks) - len(failures), "failed": len(failures)},
        "checks": checks,
    }


def _render(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = ["# Target Repository Proof Contract Validation", "", f"- status: `{summary['status']}`", f"- checks: `{summary['checks']}`", f"- failed: `{summary['failed']}`", "", "| Check | Result |", "|---|---|"]
    for row in payload["checks"]:
        lines.append(f"| `{row['name']}` | {'PASS' if row['passed'] else 'FAIL'} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT, payload)
    save_text_atomic(REPORT_OUTPUT, _render(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

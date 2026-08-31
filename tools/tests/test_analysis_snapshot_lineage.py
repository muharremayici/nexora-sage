from __future__ import annotations

from pathlib import Path

from tools.core import analysis_snapshot_lineage, artifact_registry, artifact_store
from tools.core.analysis_snapshot_lineage import receipt_binding, write_lineage_receipt
from tools.core.atlas_integrity import build_atlas_commit
from tools.engines import test_impact_matcher
from tools.engines.quality_gate import (
    _audit_mode_breakdown,
    _extract_project_audit_violations,
    _extract_total_audit_violations,
)


def _atlas(root: Path) -> tuple[dict, dict]:
    atlas = {"MAIN": {"project": {"root": str(root)}, "files": {}}}
    return atlas, build_atlas_commit(atlas)


def test_receipt_binds_exact_producer_content(tmp_path: Path) -> None:
    atlas, commit = _atlas(tmp_path)
    payload = {"Symbol": []}
    receipt = write_lineage_receipt(
        artifact_id="genome",
        producer="tools.engines.nuclear_processor",
        artifact_payload=payload,
        atlas=atlas,
        atlas_commit=commit,
        raw_dir=tmp_path,
    )
    assert receipt["status"] == "COMPLETE"
    binding, snapshot_id, errors = receipt_binding(
        raw_dir=tmp_path,
        artifact_id="genome",
        artifact_payload=payload,
        expected_snapshot_id=commit["snapshot_id"],
    )
    assert (binding, snapshot_id, errors) == ("BOUND", commit["snapshot_id"], [])


def test_receipt_rejects_tampered_content_and_wrong_producer(tmp_path: Path) -> None:
    atlas, commit = _atlas(tmp_path)
    payload = {"Symbol": []}
    write_lineage_receipt(artifact_id="genome", producer="tools.engines.nuclear_processor", artifact_payload=payload, atlas=atlas, atlas_commit=commit, raw_dir=tmp_path)
    binding, _, errors = receipt_binding(raw_dir=tmp_path, artifact_id="genome", artifact_payload={"Changed": []}, expected_snapshot_id=commit["snapshot_id"])
    assert binding == "MISMATCH"
    assert errors == ["artifact_content_hash_mismatch"]

    try:
        write_lineage_receipt(artifact_id="genome", producer="wrong.owner", artifact_payload=payload, atlas=atlas, atlas_commit=commit, raw_dir=tmp_path)
    except ValueError as exc:
        assert "producer mismatch" in str(exc)
    else:
        raise AssertionError("wrong producer must fail")


def test_dependency_mismatch_blocks_downstream_receipt(tmp_path: Path) -> None:
    atlas, commit = _atlas(tmp_path)
    genome = {"Symbol": []}
    audit = {"summary": {"total": 0}}
    write_lineage_receipt(artifact_id="genome", producer="tools.engines.nuclear_processor", artifact_payload=genome, atlas=atlas, atlas_commit=commit, raw_dir=tmp_path)
    write_lineage_receipt(artifact_id="audit_report", producer="tools.engines.audit", artifact_payload=audit, atlas=atlas, atlas_commit=commit, raw_dir=tmp_path)
    receipt = write_lineage_receipt(
        artifact_id="quality_gate",
        producer="tools.engines.quality_gate",
        artifact_payload={"passed": True},
        atlas=atlas,
        atlas_commit=commit,
        dependency_payloads={"genome": {"tampered": []}, "audit_report": audit},
        raw_dir=tmp_path,
    )
    assert receipt["status"] == "BLOCKED"
    assert "dependency:genome:artifact_content_hash_mismatch" in receipt["errors"]


def test_invalid_atlas_commit_writes_blocked_receipt(tmp_path: Path) -> None:
    atlas, commit = _atlas(tmp_path)
    commit["atlas_sha256"] = "0" * 64
    receipt = write_lineage_receipt(artifact_id="audit_report", producer="tools.engines.audit", artifact_payload={"summary": {}}, atlas=atlas, atlas_commit=commit, raw_dir=tmp_path)
    assert receipt["status"] == "BLOCKED"
    assert receipt["atlas_snapshot_id"] is None
    assert receipt["errors"] == ["atlas_commit_invalid_or_unavailable"]


def test_test_impact_uses_the_supplied_snapshot_inputs(monkeypatch) -> None:
    atlas = {
        "MAIN": {
            "files": {
                "src/value.ts": {"workspace_rel": "src/value.ts"},
                "src/value.test.ts": {"workspace_rel": "src/value.test.ts"},
            }
        }
    }
    circular_deps = {
        "nodes": {"MAIN::src/value.ts": {}, "MAIN::src/value.test.ts": {}},
        "edges": [{"source": "MAIN::src/value.test.ts", "target": "MAIN::src/value.ts"}],
    }
    monkeypatch.setattr(
        test_impact_matcher,
        "load_atlas_data",
        lambda: (_ for _ in ()).throw(AssertionError("injected Atlas must not be reloaded")),
    )

    result = test_impact_matcher.find_impacted_tests(
        "MAIN::src/value.ts",
        atlas=atlas,
        circular_deps=circular_deps,
    )

    assert result["impacted_tests"][0]["file"] == "src/value.test.ts"
    assert result["impacted_tests"][0]["type"] == "Dual Vector Match"


def test_quality_gate_audit_metrics_share_one_payload() -> None:
    payload = {
        "summary": {
            "total": 2,
            "by_project": {"MAIN": 2},
            "rule_taxonomy": {
                "profiles": {"bounded_rule": {"mode": "enforced"}},
                "summary": {"rules": 1},
            },
        },
        "violations": [
            {"rule": "bounded_rule", "project": "MAIN"},
            {"rule": "bounded_rule", "project": "MAIN"},
        ],
    }

    assert _extract_total_audit_violations(payload) == 2
    assert _extract_project_audit_violations(payload) == {"MAIN": 2}
    assert _audit_mode_breakdown(payload)["totals"]["enforced"] == 2


def test_registered_nested_path_is_preserved_under_alternate_storage_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        artifact_registry,
        "load_artifact_registry",
        lambda: {
            "path_contract": {"storage_roots": {"managed_runtime_artifact": "output/.raw"}},
            "artifacts": [
                {
                    "id": "nested_proof",
                    "path": "output/.raw/proof/nested.json",
                    "storage_class": "managed_runtime_artifact",
                }
            ],
        },
    )

    assert artifact_registry.artifact_path_for_storage_root(tmp_path, "nested_proof") == tmp_path / "proof" / "nested.json"


def test_current_atlas_commit_read_uses_sqlite_primary_store(tmp_path: Path, monkeypatch) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    expected = {"snapshot_id": "sqlite-primary"}

    class FakeStore:
        def load_raw(self, artifact_id: str, default: object) -> object:
            assert artifact_id == "atlas_commit"
            return expected

    monkeypatch.setattr(analysis_snapshot_lineage, "RAW_DIR", raw_dir)
    monkeypatch.setattr(artifact_store, "STORE", FakeStore())

    assert analysis_snapshot_lineage._load_atlas_commit(raw_dir) == expected

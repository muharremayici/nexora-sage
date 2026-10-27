"""Actual static Atlas/lineage/Oracle path; never execute subject code or scripts."""
from copy import deepcopy

import pytest

from tools.core import analysis_snapshot_lineage as lineage
from tools.core import artifact_store, atlas_io, config, projects_registry, runtime_project_scope
from tools.core.analysis_scope_authority import runtime_scope_authority
from tools.core.atlas_integrity import validate_atlas_commit
from tools.engines import architecture_oracle as oracle, generate_atlas as generator
from tools.orchestrators import orchestrator
from tools.tests.test_atlas_materialization_profile import _sqlite_store
from tools.tests.test_repository_observation import observed_subject


@pytest.fixture
def oracle_subject(observed_subject, tmp_path, monkeypatch):
    root, compiled, _supplied_metadata = observed_subject
    raw = tmp_path / ".raw"
    raw.mkdir()
    runtime = dict(compiled, use_sqlite=True)
    store = _sqlite_store(raw)
    for module in (artifact_store, atlas_io, config, lineage, oracle, generator):
        monkeypatch.setattr(module, "RAW_DIR", raw)
    for module in (artifact_store, config, projects_registry, generator, orchestrator):
        monkeypatch.setattr(module, "DYNAMIC_CONFIG", runtime)
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(artifact_store, "ROOT", root)
    monkeypatch.setattr(config, "PROJECT_FILTER", [])
    monkeypatch.setattr(runtime_project_scope, "_RUNTIME_PROJECT_FILTER", None)
    monkeypatch.setattr(projects_registry, "PROJECTS", {k: [v] for k, v in runtime["variations"].items()})
    monkeypatch.setattr(generator, "ROOT", root)
    monkeypatch.setattr(generator, "CONFIG_FILE", tmp_path / "CONFIG_PATH.json")
    monkeypatch.setattr(generator, "ensure_output_dir", lambda: None)
    monkeypatch.setattr(generator, "GLOBAL_ATLAS_CACHE", generator.SizeBoundedDict(max_size=10))
    monkeypatch.setattr(orchestrator, "ROOT", root)
    monkeypatch.setattr(oracle, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(oracle, "CONFIG_FILE", tmp_path / "CONFIG_PATH.json")
    monkeypatch.setattr(oracle, "DISCOVERY_FILE", tmp_path / "DISCOVERY_PATH.json")
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")

    def generate(*, projects=None, nonempty_library=False, no_library_source=False, write_scope_receipt=True):
        if nonempty_library:
            (root / "packages/widget/value.ts").write_text("export const value = 1;", encoding="utf-8")
        if no_library_source:
            (root / "packages/widget/value.ts").unlink()
        # Real project resolver, ownership walk, native Node AST, SQLite writer and commit.
        atlas, _, _ = generator.generate_atlas()
        assert set(atlas) - {"symbols"} == {"MAIN", "WIDGET"}
        assert set(atlas["MAIN"]["files"]) == {"page.tsx"}
        assert set(atlas["WIDGET"]["files"]) == (set() if no_library_source else {"value.ts"})
        for project in ("MAIN", "WIDGET"):
            for item in atlas[project]["files"].values():
                assert item["parser_evidence"]["status"] == "observed"
        commit = lineage.load_atlas_commit(raw)
        assert commit["state"] == "complete"
        assert all(row["passed"] for row in validate_atlas_commit(atlas, commit))
        primary = atlas_io.load_atlas_data_with_identity()
        assert primary.primary_verified and primary.source == "sqlite_primary"
        assert primary.data == atlas
        runtime_project_scope.set_runtime_project_filter(projects)
        fresh = orchestrator._runtime_project_observation({"project_observation": "refresh_static_facts"}, "oracle-current")
        scope = runtime_scope_authority(dynamic_config=runtime, projects=projects, atlas=atlas,
            project_observation=fresh, observation_run_id="oracle-current")
        artifact = orchestrator._scope_authority_artifact(scope, stage="POST_ATLAS")
        config.save_json_atomic(raw / "analysis_scope_authority.json", artifact)
        if write_scope_receipt:
            receipt = lineage.write_current_atlas_lineage(artifact_id="analysis_scope_authority",
                producer="tools.orchestrators.orchestrator", artifact_payload=artifact, atlas=atlas, raw_dir=raw)
            assert receipt["status"] == "COMPLETE"
            assert receipt["atlas_snapshot_id"] == commit["snapshot_id"]
        return atlas, commit, artifact

    return root, raw, store, generate


def _run_and_check_hitl(raw, store):
    # No explicit Atlas argument and no mocked binding/reader/classifier.
    result = oracle.run_architecture_oracle()
    assert store.load_raw("architecture_oracle", {}) == result
    assert result["repository_blueprint"]["policy_activation_allowed"] is False
    policy = store.load_raw("effective_architecture_policy", {})
    assert policy["projects"]
    assert all(not row["policy_activation_allowed"] for row in policy["projects"].values())
    assert result["policy"]["seal_requires_human_approval"] is True
    return result


@pytest.mark.parametrize("nonempty_library", [False, True], ids=["zero-byte-library", "observed-library"])
def test_actual_oracle_consumes_committed_normal_observation(oracle_subject, nonempty_library, record_testsuite_property):
    _root, raw, store, generate = oracle_subject
    _atlas, commit, artifact = generate(nonempty_library=nonempty_library)
    result = _run_and_check_hitl(raw, store)
    composition = result["repository_blueprint"]
    binding = composition["evidence"]["snapshot_binding"]
    assert binding == {"status": "BOUND", "atlas_snapshot_id": commit["snapshot_id"],
                       "atlas_sha256": commit["atlas_sha256"], "errors": []}
    assert result["scope_authority"]["project_observation_identity"] == artifact["scope_authority"]["project_observation_identity"]
    assert {row["project"]: row["project_system_kind"]["kind"] for row in result["projects"]} == {"MAIN": "application", "WIDGET": "library"}
    assert all(row["project_language_capability"]["status"] == "ENGINE_AVAILABLE" for row in result["projects"])
    # Composition observes inventory/kinds, not a strong architecture classification.
    assert composition["classification_status"] == "OBSERVED"
    assert composition["proposal_identity_status"] == "BOUND"
    assert composition["insufficient_source_projects"] == []
    assert all(row["seal_ready"] is False and row["seal_proposal"]["status"] == "ADVISORY_ONLY"
               for row in result["projects"])
    for key, value in {"snapshot_id": commit["snapshot_id"], "atlas_sha256": commit["atlas_sha256"],
                       "composition_status": composition["classification_status"], "native_ast": True}.items():
        record_testsuite_property(f"{'nonempty' if nonempty_library else 'zero_byte'}_{key}", value)


def test_actual_oracle_keeps_absent_source_distinct_from_zero_bytes(oracle_subject):
    _root, raw, store, generate = oracle_subject
    generate(no_library_source=True)
    result = _run_and_check_hitl(raw, store)
    composition = result["repository_blueprint"]
    assert composition["evidence"]["snapshot_binding"]["status"] == "BOUND"
    assert composition["classification_status"] == "PARTIAL_EVIDENCE"
    assert composition["insufficient_source_projects"] == ["WIDGET"]
    widget = next(row for row in result["projects"] if row["project"] == "WIDGET")
    assert widget["recommended_profile"] is None
    assert widget["confidence"] == 0
    assert widget["proposal_identity_status"] == "UNAVAILABLE"


@pytest.mark.parametrize("fault,expected_error", [
    ("missing_receipt", "missing_lineage_receipt"),
    ("tampered_scope", "artifact_content_hash_mismatch"),
    ("changed_snapshot", "atlas_snapshot_id_mismatch"),
    ("missing_commit", "complete_atlas_commit_unavailable"),
])
def test_actual_oracle_withholds_unbound_scope(oracle_subject, fault, expected_error):
    root, raw, store, generate = oracle_subject
    _atlas, before, artifact = generate(write_scope_receipt=fault != "missing_receipt")
    if fault == "tampered_scope":
        changed = deepcopy(artifact)
        changed["scope_authority"]["project_observation_identity"] = "changed-after-receipt"
        config.save_json_atomic(raw / "analysis_scope_authority.json", changed)
    elif fault == "changed_snapshot":
        (root / "page.tsx").write_text("export function Page(){return <div/>;}", encoding="utf-8")
        generator.generate_atlas()
        assert lineage.load_atlas_commit(raw)["snapshot_id"] != before["snapshot_id"]
    elif fault == "missing_commit":
        store.save_raw("atlas_commit", {})
    result = _run_and_check_hitl(raw, store)
    composition = result["repository_blueprint"]
    assert composition["classification_status"] == "SNAPSHOT_UNBOUND"
    assert expected_error in composition["evidence"]["snapshot_binding"]["errors"]
    assert composition["proposal_identity_status"] == "UNAVAILABLE"
    assert all(row["proposal_identity_status"] == "UNAVAILABLE" for row in result["projects"])


def test_actual_oracle_does_not_promote_a_bounded_projection(oracle_subject):
    _root, raw, store, generate = oracle_subject
    generate(projects=["WIDGET"])
    result = _run_and_check_hitl(raw, store)
    assert [row["project"] for row in result["projects"]] == ["WIDGET"]
    composition = result["repository_blueprint"]
    assert composition["classification_status"] == "NOT_APPLICABLE"
    assert composition["evidence"]["scope_evidence_status"] == "BOUNDED_PROJECT_SELECTION"
    assert composition["evidence"]["full_repository_claim_eligible"] is False
    assert composition["evidence"]["snapshot_binding"]["status"] == "BOUND"
    assert composition["proposal_identity_status"] == "UNAVAILABLE"

"""Real external receipt/config/Atlas/Oracle path; no subject code execution."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools import external_target_preflight as preflight
from tools.core import analysis_snapshot_lineage as lineage
from tools.core import artifact_store, atlas_io, config, projects_registry, runtime_project_scope
from tools.core import repository_observation as observation
from tools.core.analysis_scope_authority import runtime_scope_authority
from tools.core.atlas_integrity import validate_atlas_commit
from tools.engines import architecture_oracle as oracle, generate_atlas as generator
from tools.orchestrators import orchestrator
from tools.tests.test_atlas_materialization_profile import _sqlite_store
from tools.tests.test_repository_observation import observed_subject


@pytest.fixture
def external_subject(observed_subject, tmp_path, monkeypatch):
    root, compiled, _supplied_metadata = observed_subject
    monkeypatch.setattr(preflight, "EXTERNAL_TARGETS_DIR", tmp_path / "external_targets")
    monkeypatch.setenv("CODEMAPS_EXTERNAL_RUN_ID", "same-tiny-subject")
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")

    def prepare(*, projects=None, nonempty_library=False):
        if nonempty_library:
            (root / "packages/widget/value.ts").write_text("export const value = 1;", encoding="utf-8")
        payload = preflight.build_preflight(root, projects=",".join(projects or []))
        raw = Path(payload["target"]["output_dir"]) / ".raw"
        raw.mkdir(parents=True)
        store = _sqlite_store(raw)
        for module in (artifact_store, atlas_io, config, lineage, oracle, generator):
            monkeypatch.setattr(module, "RAW_DIR", raw)
        monkeypatch.setattr(artifact_store, "STORE", store)
        monkeypatch.setattr(artifact_store, "ROOT", root)
        monkeypatch.setenv("CODEMAPS_TARGET_ROOT", str(root))
        monkeypatch.setenv("CODEMAPS_TARGET_PROJECTS", ",".join(projects or []))
        # The real writer persists the immutable run, SQLite latest and report.
        persisted = preflight.persist_preflight(payload)
        transport = preflight.preflight_receipt_transport(persisted)
        monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", transport["path"])
        monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", transport["sha256"])
        assert config.load_external_target_preflight_receipt(root, require_freshness=True) == persisted
        runtime = config._apply_target_root_override(dict(compiled, use_sqlite=True))
        assert runtime["_target_root_override"]["observation_source"] == "external_target_preflight_receipt"
        assert runtime["_target_root_override"]["preflight_run_id"] == transport["run_id"]
        for module in (artifact_store, config, projects_registry, generator, orchestrator):
            monkeypatch.setattr(module, "DYNAMIC_CONFIG", runtime)
        monkeypatch.setattr(config, "PROJECT_FILTER", projects or [])
        monkeypatch.setattr(runtime_project_scope, "_RUNTIME_PROJECT_FILTER", projects)
        monkeypatch.setattr(projects_registry, "PROJECTS", {k: [v] for k, v in runtime["variations"].items()})
        monkeypatch.setattr(generator, "ROOT", root)
        monkeypatch.setattr(generator, "CONFIG_FILE", tmp_path / "CONFIG_PATH.json")
        monkeypatch.setattr(generator, "ensure_output_dir", lambda: None)
        monkeypatch.setattr(generator, "GLOBAL_ATLAS_CACHE", generator.SizeBoundedDict(max_size=10))
        monkeypatch.setattr(orchestrator, "ROOT", root)
        monkeypatch.setattr(oracle, "REPORTS_DIR", raw.parent / "reports")
        monkeypatch.setattr(oracle, "CONFIG_FILE", tmp_path / "CONFIG_PATH.json")
        monkeypatch.setattr(oracle, "DISCOVERY_FILE", tmp_path / "DISCOVERY_PATH.json")
        return root, raw, store, persisted, runtime, compiled

    return prepare


def _run_oracle(raw, store, atlas, authority):
    artifact = orchestrator._scope_authority_artifact(authority, stage="POST_ATLAS")
    config.save_json_atomic(raw / "analysis_scope_authority.json", artifact)
    receipt = lineage.write_current_atlas_lineage(artifact_id="analysis_scope_authority",
        producer="tools.orchestrators.orchestrator", artifact_payload=artifact, atlas=atlas, raw_dir=raw)
    assert receipt["status"] == "COMPLETE"
    result = oracle.run_architecture_oracle()
    assert result == store.load_raw("architecture_oracle", {})
    assert result["repository_blueprint"]["policy_activation_allowed"] is False
    assert all(not row["policy_activation_allowed"] for row in store.load_raw("effective_architecture_policy", {})["projects"].values())
    return result


@pytest.mark.parametrize("projects,nonempty", [(None, False), (None, True), (["WIDGET"], False)])
def test_admitted_external_runtime_and_oracle_match_normal_semantic_facts(external_subject, monkeypatch, projects, nonempty, record_testsuite_property):
    root, raw, store, persisted, runtime, normal_config = external_subject(projects=projects, nonempty_library=nonempty)
    atlas, _, _ = generator.generate_atlas()
    commit = lineage.load_atlas_commit(raw)
    assert all(row["passed"] for row in validate_atlas_commit(atlas, commit))
    primary = atlas_io.load_atlas_data_with_identity()
    assert primary.primary_verified and primary.source == "sqlite_primary" and primary.data == atlas
    for row in atlas.values():
        if isinstance(row, dict):
            assert all(item["parser_evidence"]["status"] == "observed" for item in row.get("files", {}).values())
    selected = {key: value for key, value in normal_config["variations"].items() if key in projects} if projects else None
    fresh = observation.observe_current_repository(root, normal_config["_repository_topology"], projects=selected, run_id="normal-comparison")
    normal_scope = runtime_scope_authority(dynamic_config=normal_config, projects=projects, atlas=atlas,
        project_observation=fresh, observation_run_id="normal-comparison")
    # External path must use its admitted receipt, never re-observe normal facts.
    monkeypatch.setattr(observation, "observe_current_repository", lambda *a, **k: pytest.fail("external observation recollected"))
    assert orchestrator._runtime_project_observation({"project_observation": "refresh_static_facts"}, "external") is None
    scope = runtime_scope_authority(dynamic_config=runtime, projects=projects, atlas=atlas, raw_dir=raw)
    for key in ("scope_authority_id", "evidence_status", "effective_runtime_projects", "project_system_kinds",
                "project_system_kind_identity", "project_language_capabilities", "project_language_capability_identity"):
        assert scope[key] == normal_scope[key], key
    assert scope["project_system_kinds"] == persisted["summary"]["analysis_scope"]["scope_authority"]["project_system_kinds"]
    external = _run_oracle(raw, store, atlas, scope)
    normal = _run_oracle(raw, store, atlas, normal_scope)
    assert external["projects"] == normal["projects"]
    assert external["repository_blueprint"] == normal["repository_blueprint"]
    binding = external["repository_blueprint"]["evidence"]["snapshot_binding"]
    assert binding["status"] == "BOUND" and binding["atlas_snapshot_id"] == commit["snapshot_id"]
    assert external["summary"]["project_count"] == (1 if projects else 2)
    assert external["repository_blueprint"]["classification_status"] == ("NOT_APPLICABLE" if projects else "OBSERVED")
    assert external["scope_authority"]["full_repository_claim_eligible"] is (not bool(projects))
    record_testsuite_property(f"external_{'bounded' if projects else 'nonempty' if nonempty else 'zero_byte'}_snapshot", commit["snapshot_id"])


@pytest.mark.parametrize("fault,message", [
    ("hash", "content identity mismatch"), ("missing_hash", "hash is missing"),
    ("wrong_target", "target identity mismatch"), ("projects", "project-filter identity mismatch"),
    ("stale_config", "target observation identity is stale"),
])
def test_actual_external_admission_rejects_mismatched_receipts(external_subject, monkeypatch, fault, message):
    root, _raw, _store, _persisted, _runtime, _normal = external_subject()
    target = root
    if fault == "hash":
        monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", "0" * 64)
    elif fault == "missing_hash":
        monkeypatch.delenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256")
    elif fault == "wrong_target":
        target = root.parent
    elif fault == "projects":
        monkeypatch.setenv("CODEMAPS_TARGET_PROJECTS", "WIDGET")
    elif fault == "stale_config":
        path = root / "package.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["dependencies"]["react"] = "20"
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match=message):
        config.load_external_target_preflight_receipt(target, require_freshness=True)


def test_runtime_does_not_exchange_admitted_receipt_for_later_projection(external_subject):
    root, raw, _store, admitted, runtime, _normal = external_subject()
    path = root / "package.json"
    path.write_text(json.dumps({"workspaces": ["packages/*"], "exports": {".": "./page.tsx"}}), encoding="utf-8")
    later = preflight.persist_preflight(preflight.build_preflight(root))
    assert admitted["meta"]["run_id"] != later["meta"]["run_id"]
    before = admitted["summary"]["analysis_scope"]["scope_authority"]
    after = later["summary"]["analysis_scope"]["scope_authority"]
    assert before["scope_authority_id"] == after["scope_authority_id"]
    assert before["project_system_kind_identity"] != after["project_system_kind_identity"]
    scope = runtime_scope_authority(dynamic_config=runtime, projects=None, raw_dir=raw)
    # A later projection must not silently replace the explicitly admitted run.
    assert scope["project_system_kinds"] == before["project_system_kinds"]
    assert scope["project_system_kind_identity"] == before["project_system_kind_identity"]


@pytest.mark.parametrize("fault", ["scope", "run_id", "hash", "bypass"])
def test_incomplete_admitted_transport_or_bypass_cannot_borrow_latest(external_subject, fault):
    _root, raw, _store, _admitted, runtime, _normal = external_subject()
    changed = deepcopy(runtime)
    override = changed["_target_root_override"]
    if fault == "scope":
        override["analysis_scope"].pop("scope_authority")
    elif fault == "run_id":
        override["preflight_run_id"] = None
    elif fault == "hash":
        override["preflight_receipt_sha256"] = "not-a-content-identity"
    else:
        override["observation_source"] = "runtime_recomputed_fallback"
    result = runtime_scope_authority(dynamic_config=changed, projects=None, raw_dir=raw)
    assert result["evidence_status"] == "INCOMPLETE_EVIDENCE"
    assert result["full_repository_claim_eligible"] is False
    # No Atlas was supplied: unavailable counts remain unknown, not measured zero.
    assert result["claim_eligible_source_file_count"] is None
    assert result["project_language_capabilities"] == {}

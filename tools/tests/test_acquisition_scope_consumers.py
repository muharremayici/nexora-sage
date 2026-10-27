from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tools.core.analysis_scope_authority import (
    BOUNDED_PROJECT_SELECTION, COMPLETE_REPOSITORY, INCOMPLETE_EVIDENCE,
    bind_consumer_projects, load_scope_authority_for_consumer,
    reconcile_consumer_scope_authorities, reconcile_quality_scope_authority,
    runtime_scope_authority,
)
from tools.engines.quality_gate import _quality_scope_gate


@pytest.fixture
def acquisition_scope_example(tmp_path, monkeypatch):
    """Same five-file shape as root repair; supplied Atlas metadata, not a new AST run."""
    from tools import config_compiler
    from tools.core import artifact_store, config
    from tools.engines import quality_gate
    from tools.external_target_preflight import build_preflight
    from tools.orchestrators import discovery
    from tools.tests.test_atlas_materialization_profile import _sqlite_store

    root, raw = tmp_path / "target", tmp_path / ".raw"
    root.mkdir()
    raw.mkdir()
    project_files = {
        "MAIN": (".", ["a.ts", "src/index.ts", "tests/root.test.ts"]),
        "WIDGET": ("packages/widget", ["child.ts"]),
        "LEGACY": ("Variations/Legacy", ["old.ts"]),
    }
    atlas = {}
    for project, (relative, files) in project_files.items():
        project_root = root / relative
        project_root.mkdir(parents=True, exist_ok=True)
        manifest = {"workspaces": ["packages/*"]} if project == "MAIN" else {"name": project.lower()}
        (project_root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        for name in files:
            path = project_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("export const value = 1;", encoding="utf-8")
        atlas[project] = {"root_path": str(project_root), "files": {name: {} for name in files}}
    monkeypatch.setattr(discovery, "ROOT_DIR", root)
    monkeypatch.setattr(discovery, "DYNAMIC_CONFIG", {})
    monkeypatch.delenv("CODEMAPS_TOPOLOGY_MODE", raising=False)
    topology = discovery.resolve_discovery_topology()
    declared = {"workspace_root": str(root), "variations": topology["selected_projects"],
                "_repository_topology": topology, "project_roles": topology["selected_project_roles"]}
    for key, value in (("DISCOVERY_PATH", declared), ("OVERRIDES_PATH", {}), ("CONFIG_PATH", {})):
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        monkeypatch.setattr(config_compiler, key, path)
    compiled = config_compiler.compile_runtime_config()
    assert compiled["variations"] == {key: value[0] for key, value in project_files.items()}
    store = _sqlite_store(raw)
    # Real SQLite reads/writes, with the active managed path configured as in a run.
    monkeypatch.setattr(config, "RAW_DIR", raw)
    monkeypatch.setattr(quality_gate, "RAW_DIR", raw)
    monkeypatch.setattr(artifact_store, "STORE", store)
    store._save_payload_to_state_table("atlas", atlas)
    assert store.load_raw("atlas", {}) == atlas

    def external_config(projects):
        preflight = build_preflight(root, projects=projects)
        # Synthetic post-admission transport, not proof of actual receipt admission.
        # Actual immutable bytes/hash/currentness are tested by the external
        # observation/Oracle integration suite; invalid-filter cases here reach
        # only the consumer's fail-closed contract, never a real admission grant.
        preflight["meta"]["run_id"] = "post-admission-consumer-fixture"
        store._save_payload_to_state_table("external_target_preflight", preflight)
        # Admission/frozen-receipt identity is covered by the root repair suite.
        # Here exercise transport after admission, then real post-Atlas SQLite reads.
        monkeypatch.setenv("CODEMAPS_TARGET_ROOT", str(root))
        monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", "f" * 64)
        monkeypatch.setattr(config, "_load_external_target_preflight_receipt", lambda *a: preflight)
        return config._apply_target_root_override({}), preflight

    return compiled, atlas, raw, store, external_config


@pytest.mark.parametrize("projects,expected,count", [
    (None, {"MAIN", "WIDGET", "LEGACY"}, 5),
    ("MAIN", {"MAIN"}, 3), ("WIDGET", {"WIDGET"}, 1),
    ("LEGACY", {"LEGACY"}, 1), ("MISSING", set(), 0),
    ("WIDGET,MISSING", {"WIDGET"}, 1),
])
def test_acquisition_scope_survives_post_atlas_sqlite_and_quality_consumers(
    acquisition_scope_example, monkeypatch, projects, expected, count,
):
    from tools.core import runtime_project_scope
    from tools.core.analysis_scope_authority import topology_projection

    compiled, atlas, raw, store, external_config = acquisition_scope_example
    external, preflight = external_config(projects)
    assert topology_projection(compiled)["main_root_decision"] == topology_projection(external)["main_root_decision"]
    assert topology_projection(compiled)["topology_authority_id"] == topology_projection(external)["topology_authority_id"]
    monkeypatch.setattr(runtime_project_scope, "_RUNTIME_PROJECT_FILTER", projects.split(",") if projects else None)
    projected, runtime = runtime_project_scope.project_runtime_atlas(atlas)
    assert set(projected) == expected
    assert set(runtime["preserved_only_projects"]) == set(atlas) - expected
    original = deepcopy(atlas)
    observed = []
    for config in (compiled, external):
        authority = runtime_scope_authority(dynamic_config=config, projects=projects, atlas=atlas, raw_dir=raw)
        status = INCOMPLETE_EVIDENCE if "MISSING" in (projects or "") else (
            BOUNDED_PROJECT_SELECTION if projects else COMPLETE_REPOSITORY)
        assert set(authority["effective_runtime_projects"]) == expected
        assert authority["evidence_status"] == status
        assert authority["indexed_source_file_count"] == count
        assert authority["claim_eligible_source_file_count"] == (0 if status == INCOMPLETE_EVIDENCE else count)
        assert authority["full_repository_claim_eligible"] is (projects is None)
        artifact = {"stage": "POST_ATLAS", "scope_authority": authority}
        store._save_payload_to_state_table("analysis_scope_authority", artifact)
        # A contradictory shadow must not replace the current SQLite authority.
        (raw / "analysis_scope_authority.json").write_text("{}", encoding="utf-8")
        loaded_artifact, loaded = load_scope_authority_for_consumer(raw)
        assert loaded_artifact == artifact
        consumers = {layer: bind_consumer_projects(loaded, layer=layer, observed_projects=projected)
                     for layer in ("audit", "architecture_oracle")}
        reconciled = reconcile_consumer_scope_authorities(loaded, consumers)
        assert reconciled["consumer_scope_consistency"] == "CONSISTENT"
        quality, usable, shared = _quality_scope_gate({"audit_scope": {"scope_authority": consumers["audit"]}})
        assert shared == artifact
        assert usable is (status != INCOMPLETE_EVIDENCE)
        action, actionable = reconcile_quality_scope_authority(artifact, {
            "scope_gate_status": "PASS" if usable else INCOMPLETE_EVIDENCE,
            "analysis_scope_authority": quality,
        })
        assert actionable is usable
        assert action["scope_authority_id"] == authority["scope_authority_id"]
        observed.append(authority)
    for key in ("scope_authority_id", "topology_authority_id", "effective_runtime_projects",
                "unavailable_requested_projects", "project_relationship_identity"):
        assert observed[0][key] == observed[1][key], key
    # Default topology has no static inventory classifier; Preflight has inspected
    # the manifests/inventory. Both honestly say unknown, but not for the same
    # evidence reason, so their evidence-bearing system-kind IDs must not be equated.
    for project in expected:
        assert observed[0]["project_system_kinds"][project]["kind"] == "unknown"
        assert observed[1]["project_system_kinds"][project]["kind"] == "unknown"
        assert observed[0]["project_system_kinds"][project]["authority"] == "unresolved_without_evidence_bearing_system_kind_classifier"
        assert observed[1]["project_system_kinds"][project]["authority"] == "insufficient_static_system_kind_evidence"
    assert observed[0]["effective_supported_source_file_count"] is None
    assert observed[1]["effective_supported_source_file_count"] == count
    assert observed[0]["project_language_capabilities"] == {}
    assert observed[1]["project_language_capabilities"] == preflight["summary"]["analysis_scope"]["scope_authority"]["project_language_capabilities"]
    assert atlas == original  # Projection and consumer checks never rewrite canonical Atlas.


@pytest.mark.parametrize("failure,reason", [
    ("missing_preflight", "external_target_preflight_scope_authority_missing"),
    ("wrong_topology", "preflight_discovery_topology_authority_identity_mismatch"),
    ("wrong_filter", "preflight_discovery_scope_authority_identity_mismatch"),
    ("missing_project", "effective_projects_missing_from_atlas"),
    ("missing_file", "atlas_indexed_fewer_files_than_effective_supported_source_inventory"),
])
def test_acquisition_scope_evidence_gaps_cannot_authorize_actions(acquisition_scope_example, failure, reason):
    _compiled, atlas, raw, store, external_config = acquisition_scope_example
    external, preflight = external_config("MAIN")
    source = deepcopy(atlas)
    if failure == "missing_preflight":
        preflight = {}
        external["_target_root_override"]["analysis_scope"].pop("scope_authority")
    elif failure == "wrong_topology":
        preflight["summary"]["analysis_scope"]["scope_authority"]["topology_authority_id"] = "sha256:" + "a" * 64
    elif failure == "missing_project":
        source.pop("MAIN")
    elif failure == "missing_file":
        source["MAIN"]["files"].pop("tests/root.test.ts")
    store._save_payload_to_state_table("external_target_preflight", preflight)
    authority = runtime_scope_authority(dynamic_config=external,
        projects="WIDGET" if failure == "wrong_filter" else "MAIN", atlas=source, raw_dir=raw)
    assert authority["evidence_status"] == INCOMPLETE_EVIDENCE
    assert reason in authority["incomplete_reasons"]
    assert authority["claim_eligible_source_file_count"] == 0
    _action, actionable = reconcile_quality_scope_authority({"scope_authority": authority}, {
        "scope_gate_status": "PASS", "analysis_scope_authority": authority})
    assert actionable is False  # Even an upstream PASS cannot bless incomplete scope.


@pytest.mark.parametrize("failure", ["extra_audit_project", "missing_oracle_project", "stale_quality", "missing_authority"])
def test_acquisition_scope_downstream_drift_cannot_authorize_actions(acquisition_scope_example, failure):
    _compiled, atlas, raw, store, external_config = acquisition_scope_example
    external, _preflight = external_config("WIDGET")
    authority = runtime_scope_authority(dynamic_config=external, projects="WIDGET", atlas=atlas, raw_dir=raw)
    store._save_payload_to_state_table("analysis_scope_authority", {} if failure == "missing_authority" else {"scope_authority": authority})
    _artifact, loaded = load_scope_authority_for_consumer(raw)
    audit_scope = bind_consumer_projects(loaded, layer="audit",
        observed_projects=["WIDGET", "MAIN"] if failure == "extra_audit_project" else ["WIDGET"])
    oracle_scope = bind_consumer_projects(loaded, layer="architecture_oracle",
        observed_projects=[] if failure == "missing_oracle_project" else ["WIDGET"])
    combined = reconcile_consumer_scope_authorities(loaded, {"audit": audit_scope, "architecture_oracle": oracle_scope})
    if failure == "stale_quality":
        combined = dict(combined, scope_authority_id="sha256:" + "b" * 64)
    _action, actionable = reconcile_quality_scope_authority({"scope_authority": loaded}, {
        "scope_gate_status": "PASS", "analysis_scope_authority": combined})
    assert actionable is False
    assert atlas["MAIN"]["files"] == {"a.ts": {}, "src/index.ts": {}, "tests/root.test.ts": {}}

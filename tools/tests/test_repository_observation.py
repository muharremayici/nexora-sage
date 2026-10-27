"""Focused static facts/admission tests; no target scripts, components or tests execute."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools import config_compiler, external_target_preflight as preflight
from tools.core import repository_observation as observation
from tools.core.analysis_scope_authority import runtime_scope_authority
from tools.orchestrators import discovery, orchestrator


@pytest.fixture
def observed_subject(tmp_path, monkeypatch):
    root = tmp_path / "subject"
    root.mkdir()
    for name, payload in {
        "package.json": {"workspaces": ["packages/*"], "dependencies": {"react": "19", "vite": "6"}, "scripts": {"dev": "never execute this"}},
        "packages/widget/package.json": {"exports": {".": "./value.ts"}},
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    (root / "page.tsx").write_text("export function Page(){return null;}", encoding="utf-8")
    (root / "packages/widget/value.ts").write_text("", encoding="utf-8")
    monkeypatch.setattr(discovery, "ROOT_DIR", root)
    monkeypatch.setattr(discovery, "DYNAMIC_CONFIG", {})
    monkeypatch.delenv("CODEMAPS_TOPOLOGY_MODE", raising=False)
    captured = {}
    monkeypatch.setattr(discovery, "save_json_atomic", lambda path, payload: captured.update(proposal=payload))
    monkeypatch.setattr(discovery, "detect_languages", lambda *a, **k: pytest.fail("redundant language walk"))
    proposal = discovery.run_discovery(output_path=tmp_path / "proposal.json", write_reports=False)
    assert captured["proposal"] == proposal
    for key, payload in (("DISCOVERY_PATH", proposal), ("OVERRIDES_PATH", {}), ("CONFIG_PATH", {})):
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(config_compiler, key, path)
    compiled = config_compiler.compile_runtime_config()
    atlas = {"MAIN": {"files": {"page.tsx": {}}}, "WIDGET": {"files": {"value.ts": {}}}}
    return root, compiled, atlas


@pytest.mark.parametrize("projects,expected,count", [(None, {"MAIN", "WIDGET"}, 2),
    ("MAIN", {"MAIN"}, 1), ("WIDGET", {"WIDGET"}, 1), ("MISSING", set(), 0),
    ("WIDGET,MISSING", {"WIDGET"}, 1)])
def test_real_discovery_preflight_and_run_scope_share_facts(observed_subject, tmp_path, projects, expected, count):
    root, compiled, atlas = observed_subject
    topology = compiled["_repository_topology"]
    fresh = observation.observe_current_repository(root, topology, run_id="run-current")
    external = preflight.build_preflight(root, projects=projects)
    raw = tmp_path / ".raw"
    raw.mkdir()
    (raw / "external_target_preflight.json").write_text(json.dumps(external), encoding="utf-8")
    normal = runtime_scope_authority(dynamic_config=compiled, projects=projects, atlas=atlas,
        project_observation=fresh, observation_run_id="run-current")
    remote = runtime_scope_authority(dynamic_config={"_target_root_override": external["summary"]["analysis_scope"]},
        projects=projects, atlas=atlas, raw_dir=raw)
    for key in ("scope_authority_id", "evidence_status", "effective_runtime_projects", "project_system_kinds",
                "project_system_kind_identity", "project_language_capabilities", "project_language_capability_identity"):
        assert normal[key] == remote[key], key
    assert set(normal["effective_runtime_projects"]) == expected
    assert normal["indexed_source_file_count"] == count
    assert normal["claim_eligible_source_file_count"] == (0 if "MISSING" in (projects or "") else count)
    assert topology["project_candidate_system_kinds"]["MAIN"]["kind"] == "application"
    assert topology["project_candidate_system_kinds"]["WIDGET"]["kind"] == "library"
    assert topology["project_observation"] == fresh.payload()


def test_observation_uses_one_inventory_and_consumer_never_walks(observed_subject, monkeypatch):
    root, compiled, atlas = observed_subject
    calls = []
    original = observation.repository_and_selected_project_inventory
    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(observation, "repository_and_selected_project_inventory", counted)
    fresh = observation.observe_current_repository(root, compiled["_repository_topology"], run_id="one")
    assert calls == [root]
    monkeypatch.setattr(observation, "repository_and_selected_project_inventory", lambda *a, **k: pytest.fail("consumer rewalk"))
    scope = runtime_scope_authority(dynamic_config=compiled, projects=None, atlas=atlas,
        project_observation=fresh, observation_run_id="one")
    assert scope["evidence_status"] == "COMPLETE_REPOSITORY"


@pytest.mark.parametrize("failure,reason", [
    ("old_run", "project_observation_not_current_for_run"),
    ("serialized", "project_observation_not_current_for_run"),
    ("tampered", "project_observation_identity_mismatch"),
    ("wrong_root", "project_observation_target_mismatch"),
    ("remap", "project_observation_topology_mismatch"),
    ("ownership", "project_observation_topology_mismatch"),
    ("policy", "project_observation_policy_mismatch"),
    ("coverage", "project_observation_coverage_mismatch"),
])
def test_wrong_or_stale_observations_cannot_authorize(observed_subject, monkeypatch, failure, reason):
    root, compiled, atlas = observed_subject
    config = deepcopy(compiled)
    topology = config["_repository_topology"]
    coverage = {"MAIN": "."} if failure == "coverage" else None
    fresh = observation.observe_current_repository(root, topology, projects=coverage, run_id="same")
    run_id = "other" if failure == "old_run" else "same"
    if failure == "serialized":
        fresh = fresh.payload()
    elif failure == "tampered":
        payload = fresh.payload()
        payload["project_system_kinds"]["MAIN"]["kind"] = "library"
        fresh = observation.ProjectObservation(json.dumps(payload), "same")
    elif failure == "wrong_root":
        config["workspace_root"] = str(root.parent)
    elif failure == "remap":
        topology["selected_projects"]["ALIAS"] = topology["selected_projects"].pop("WIDGET")
    elif failure == "ownership":
        topology["project_ownership_exclusions"]["MAIN"].append("new-boundary")
    elif failure == "policy":
        inputs = list(observation.current_observation_inputs())
        inputs[0] = dict(inputs[0], file_count_limit=7)
        monkeypatch.setattr(observation, "current_observation_inputs", lambda: tuple(inputs))
    scope = runtime_scope_authority(dynamic_config=config, projects=None, atlas=atlas,
        project_observation=fresh, observation_run_id=run_id)
    assert scope["evidence_status"] == "INCOMPLETE_EVIDENCE"
    assert reason in scope["incomplete_reasons"]
    assert scope["claim_eligible_source_file_count"] == 0
    assert scope["project_language_capabilities"] == {}


def test_persisted_observation_is_not_a_freshness_token(observed_subject):
    _root, compiled, atlas = observed_subject
    scope = runtime_scope_authority(dynamic_config=compiled, projects=None, atlas=atlas)
    assert scope["project_language_capabilities"] == {}
    assert all(row["kind"] == "unknown" for row in scope["project_system_kinds"].values())


def test_truncation_and_recognized_no_engine_remain_explicit(observed_subject, monkeypatch):
    root, compiled, atlas = observed_subject
    (root / "future.rs").write_text("fn future() {}", encoding="utf-8")
    topology = compiled["_repository_topology"]
    fresh = observation.observe_current_repository(root, topology, run_id="languages")
    scope = runtime_scope_authority(dynamic_config=compiled, projects=None, atlas=atlas,
        project_observation=fresh, observation_run_id="languages")
    capability = scope["project_language_capabilities"]["MAIN"]
    assert capability["status"] == "PARTIAL_ENGINE_COVERAGE"
    assert capability["engine_unavailable_language_families"] == ["rust"]
    assert capability["recognition_does_not_authorize_engine_activation"] is True
    inputs = list(observation.current_observation_inputs())
    inputs[0] = dict(inputs[0], file_count_limit=1)
    monkeypatch.setattr(observation, "current_observation_inputs", lambda: tuple(inputs))
    fresh = observation.observe_current_repository(root, topology, run_id="truncated")
    scope = runtime_scope_authority(dynamic_config=compiled, projects=None, atlas=atlas,
        project_observation=fresh, observation_run_id="truncated")
    assert scope["evidence_status"] == "INCOMPLETE_EVIDENCE"
    assert scope["claim_eligible_source_file_count"] == 0
    assert all(row["kind"] == "unknown" for row in scope["project_system_kinds"].values())


@pytest.mark.parametrize("mode", ["daily", "watchdog_save_pulse", "release_bounded", "explicit_step"])
def test_narrow_execution_modes_do_not_acquire_inventory(monkeypatch, mode):
    monkeypatch.setattr(orchestrator, "DYNAMIC_CONFIG", {})
    monkeypatch.setattr(observation, "observe_current_repository", lambda *a, **k: pytest.fail("narrow mode expanded"))
    assert orchestrator._runtime_project_observation(orchestrator.load_pipeline_execution_policy()["execution_modes"][mode], "narrow") is None


def test_full_mode_uses_current_run_but_external_receipt_never_reobserves(observed_subject, monkeypatch):
    root, compiled, _atlas = observed_subject
    monkeypatch.setattr(orchestrator, "ROOT", root)
    monkeypatch.setattr(orchestrator, "DYNAMIC_CONFIG", compiled)
    monkeypatch.setattr(orchestrator, "get_runtime_project_filter", lambda: ["WIDGET"])
    result = orchestrator._runtime_project_observation({"project_observation": "refresh_static_facts"}, "pipeline-run")
    assert result.run_id == "pipeline-run"
    assert result.payload()["captured_projects"] == {"WIDGET": "packages/widget"}
    monkeypatch.setattr(orchestrator, "DYNAMIC_CONFIG", {"_target_root_override": {}})
    monkeypatch.setattr(observation, "observe_current_repository", lambda *a, **k: pytest.fail("external double walk"))
    assert orchestrator._runtime_project_observation({"project_observation": "refresh_static_facts"}, "pipeline-run") is None


def test_fresh_scope_sqlite_and_oracle_keep_evidence_and_hitl_boundary(observed_subject, tmp_path, monkeypatch):
    from tools.core import artifact_store, config
    from tools.core.analysis_scope_authority import load_scope_authority_for_consumer
    from tools.engines.architecture_oracle import build_repository_composition_blueprint
    from tools.tests.test_atlas_materialization_profile import _sqlite_store
    root, compiled, atlas = observed_subject
    fresh = observation.observe_current_repository(root, compiled["_repository_topology"], run_id="oracle")
    scope = runtime_scope_authority(dynamic_config=compiled, projects=None, atlas=atlas,
        project_observation=fresh, observation_run_id="oracle")
    raw = tmp_path / ".raw"
    store = _sqlite_store(raw)
    monkeypatch.setattr(config, "RAW_DIR", raw)
    monkeypatch.setattr(artifact_store, "STORE", store)
    store._save_payload_to_state_table("analysis_scope_authority", {"scope_authority": scope})
    _artifact, loaded = load_scope_authority_for_consumer(raw)
    assert loaded == scope
    rows = [{"project": project, "file_count": 1, "classification_status": "OBSERVED", "recommended_profile": "MODULAR_FLAT"} for project in atlas]
    # Supplied snapshot isolates composition behavior, not actual Oracle lineage admission.
    composition = build_repository_composition_blueprint(rows, loaded,
        {"status": "BOUND", "atlas_snapshot_id": "fixture-only", "atlas_sha256": "fixture-only"})
    assert composition["classification_status"] == "OBSERVED"
    assert composition["system_kind_counts"] == {"application": 1, "library": 1}
    assert composition["policy_activation_allowed"] is False


def test_external_missing_receipt_cannot_be_bypassed_by_normal_observation(observed_subject, tmp_path):
    root, compiled, atlas = observed_subject
    fresh = observation.observe_current_repository(root, compiled["_repository_topology"], run_id="external")
    scope = runtime_scope_authority(dynamic_config={"_target_root_override": compiled["_repository_topology"]},
        projects=None, atlas=atlas, raw_dir=tmp_path / ".raw", project_observation=fresh, observation_run_id="external")
    assert scope["evidence_status"] == "INCOMPLETE_EVIDENCE"
    assert "external_target_preflight_scope_authority_missing" in scope["incomplete_reasons"]


def test_smoke_discovery_does_not_create_rich_observation(observed_subject, tmp_path, monkeypatch):
    root, _compiled, _atlas = observed_subject
    monkeypatch.setattr(discovery, "observe_current_repository", lambda *a, **k: pytest.fail("smoke full walk"))
    monkeypatch.setattr(discovery, "detect_languages", lambda *a, **k: ([], False))
    result = discovery.run_discovery(profile="entrypoint-smoke", output_path=tmp_path / "smoke.json", write_reports=False)
    assert "project_observation" not in result["_repository_topology"]


def test_unavailable_target_is_not_reported_as_truncated_inventory(observed_subject, tmp_path):
    _root, compiled, atlas = observed_subject
    target = tmp_path / "missing"
    config = dict(compiled, workspace_root=str(target))
    fresh = observation.observe_current_repository(target, compiled["_repository_topology"], run_id="invalid")
    assert fresh.payload()["repository_inventory"][1] is False
    scope = runtime_scope_authority(dynamic_config=config, projects=None, atlas=atlas,
        project_observation=fresh, observation_run_id="invalid")
    assert scope["evidence_status"] == "INCOMPLETE_EVIDENCE"
    assert "project_observation_inventory_unavailable" in scope["incomplete_reasons"]


def test_missing_kind_policy_keeps_unknown_without_enabling_a_new_engine(observed_subject, monkeypatch):
    root, compiled, atlas = observed_subject
    inputs = list(observation.current_observation_inputs())
    inputs[2] = {}
    monkeypatch.setattr(observation, "current_observation_inputs", lambda: tuple(inputs))
    fresh = observation.observe_current_repository(root, compiled["_repository_topology"], run_id="unknown")
    scope = runtime_scope_authority(dynamic_config=compiled, projects=None, atlas=atlas,
        project_observation=fresh, observation_run_id="unknown")
    assert all(row["kind"] == "unknown" and row["authority"] == "system_kind_policy_unavailable"
               for row in scope["project_system_kinds"].values())
    assert all(row["recognition_does_not_authorize_engine_activation"] for row in scope["project_language_capabilities"].values())


def test_admitted_external_observation_survives_runtime_and_discovery_without_rewalk(observed_subject, tmp_path, monkeypatch):
    from tools.core import config
    root, _compiled, _atlas = observed_subject
    payload = preflight.build_preflight(root)
    # This starts after admission; immutable receipt hash/currentness has its own controls.
    monkeypatch.setenv("CODEMAPS_TARGET_ROOT", str(root))
    monkeypatch.setattr(config, "_load_external_target_preflight_receipt", lambda *a: payload)
    external = config._apply_target_root_override({})
    monkeypatch.setattr(discovery, "DYNAMIC_CONFIG", external)
    monkeypatch.setattr(discovery, "observe_current_repository", lambda *a, **k: pytest.fail("admitted facts rewalked"))
    proposal = discovery.run_discovery(output_path=tmp_path / "external-proposal.json", write_reports=False)
    assert proposal["_repository_topology"]["project_observation"] == payload["summary"]["analysis_scope"]["project_observation"]

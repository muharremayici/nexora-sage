from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

from tools.core.analysis_scope_authority import (
    BOUNDED_PROJECT_SELECTION,
    COMPLETE_REPOSITORY,
    INCOMPLETE_EVIDENCE,
    bind_atlas_materialization,
    bind_consumer_projects,
    build_preflight_scope_authority,
    load_scope_authority_for_consumer,
    reconcile_quality_scope_authority,
    reconcile_consumer_scope_authorities,
    runtime_scope_authority,
    scope_authority_id,
    scope_receipt_details,
)
from tools.engines.quality_gate import _quality_scope_gate
from tools.engines import audit
from tools.validate_artifact_trust import _expected_analysis_projects
from tools.core.unmanaged_atomic_io import native_filesystem_path


def _topology(*, excluded: bool = False) -> dict:
    candidates = {"MAIN": "src", "WEB": "packages/web"} if excluded else {"MAIN": "src"}
    return {
        "ontology_contract": "canonical_repository_topology_v1",
        "selection_mode": "evidence_backed_auto",
        "project_candidates": candidates,
        "project_candidate_relationship_roles": {
            "MAIN": "host",
            **({"WEB": "unresolved"} if excluded else {}),
        },
        "project_candidate_role_authority": {
            "MAIN": {"relationship_resolved": True},
            **({"WEB": {"relationship_resolved": False}} if excluded else {}),
        },
        "selected_projects": {"MAIN": "src"},
        "excluded_projects": {"WEB": "packages/web"} if excluded else {},
        "excluded_project_reasons": {"WEB": "relationship_unresolved"} if excluded else {},
    }


def _preflight(*, projects=None, excluded=False, repository_truncated=False) -> dict:
    return build_preflight_scope_authority(
        topology=_topology(excluded=excluded),
        projects=projects,
        repository_file_count=20 if excluded else 10,
        repository_inventory_truncated=repository_truncated,
        repository_language_counts={"typescript": 20 if excluded else 10},
        effective_file_count=10,
        effective_inventory_truncated=False,
        effective_language_counts={"typescript": 10},
        effective_project_file_counts={"MAIN": 10},
        polyglot_capabilities={"languages": {"typescript": {}}},
    )


def _atlas(*projects: str, files_per_project: int = 10) -> dict:
    return {
        project: {"files": {f"src/file_{index}.ts": {} for index in range(files_per_project)}}
        for project in projects
    }


def test_complete_small_repository_is_eligible_for_repository_claim() -> None:
    authority = bind_atlas_materialization(_preflight(), _atlas("MAIN"))

    assert authority["evidence_status"] == COMPLETE_REPOSITORY
    assert authority["topology_authority_id"].startswith("sha256:")
    assert authority["full_repository_claim_eligible"] is True
    assert authority["indexed_source_file_count"] == 10
    assert authority["claim_eligible_source_file_count"] == 10
    assert authority["atlas_consistency"] == "CONSISTENT"


def test_project_system_kind_has_a_separate_semantic_identity_and_survives_atlas_binding() -> None:
    topology = _topology()
    topology["project_candidate_system_kinds"] = {
        "MAIN": {
            "kind": "application",
            "authority": "technical_system_kind_static_inventory_v1",
            "confidence": "high",
            "evidence": ["application_framework_and_runtime_script"],
            "candidate_kinds": ["application"],
        }
    }
    authority = build_preflight_scope_authority(
        topology=topology,
        projects=None,
        repository_file_count=10,
        repository_inventory_truncated=False,
        repository_language_counts={"typescript": 10},
        effective_file_count=10,
        effective_inventory_truncated=False,
        effective_language_counts={"typescript": 10},
        effective_project_file_counts={"MAIN": 10},
        polyglot_capabilities={"languages": {"typescript": {}}},
    )
    bound = bind_atlas_materialization(authority, _atlas("MAIN"))

    assert bound["project_system_kinds"]["MAIN"]["kind"] == "application"
    assert bound["project_system_kind_identity"].startswith("sha256:")
    changed = json.loads(json.dumps(topology))
    changed["project_candidate_system_kinds"]["MAIN"]["kind"] = "library"
    changed_authority = build_preflight_scope_authority(
        topology=changed,
        projects=None,
        repository_file_count=10,
        repository_inventory_truncated=False,
        repository_language_counts={"typescript": 10},
        effective_file_count=10,
        effective_inventory_truncated=False,
        effective_language_counts={"typescript": 10},
        effective_project_file_counts={"MAIN": 10},
        polyglot_capabilities={"languages": {"typescript": {}}},
    )
    assert changed_authority["project_system_kind_identity"] != authority["project_system_kind_identity"]


def test_project_relationship_roles_have_a_separate_semantic_identity_and_survive_atlas_binding() -> None:
    topology = _topology()
    topology["discovered_topology"] = "multi_project"
    topology["analysis_projection"] = "multi_project"
    topology["project_candidates"] = {"MAIN": "src", "WEB": "packages/web"}
    topology["selected_projects"] = {"MAIN": "src", "WEB": "packages/web"}
    topology["project_candidate_relationship_roles"] = {"MAIN": "host", "WEB": "companion"}
    topology["project_candidate_role_authority"] = {
        "MAIN": {"relationship_role": "host", "relationship_resolved": True},
        "WEB": {"relationship_role": "companion", "relationship_resolved": True},
    }
    authority = build_preflight_scope_authority(
        topology=topology,
        projects=None,
        repository_file_count=20,
        repository_inventory_truncated=False,
        repository_language_counts={"typescript": 20},
        effective_file_count=20,
        effective_inventory_truncated=False,
        effective_language_counts={"typescript": 20},
        effective_project_file_counts={"MAIN": 10, "WEB": 10},
        polyglot_capabilities={"languages": {"typescript": {}}},
    )
    bound = bind_atlas_materialization(authority, _atlas("MAIN", "WEB"))

    assert bound["discovered_topology"] == "multi_project"
    assert bound["project_relationship_roles"] == {"MAIN": "host", "WEB": "companion"}
    assert bound["project_relationship_identity"].startswith("sha256:")

    changed = json.loads(json.dumps(topology))
    changed["project_candidate_relationship_roles"]["WEB"] = "variant"
    changed["project_candidate_role_authority"]["WEB"]["relationship_role"] = "variant"
    changed_authority = build_preflight_scope_authority(
        topology=changed,
        projects=None,
        repository_file_count=20,
        repository_inventory_truncated=False,
        repository_language_counts={"typescript": 20},
        effective_file_count=20,
        effective_inventory_truncated=False,
        effective_language_counts={"typescript": 20},
        effective_project_file_counts={"MAIN": 10, "WEB": 10},
        polyglot_capabilities={"languages": {"typescript": {}}},
    )
    assert changed_authority["project_relationship_identity"] != authority["project_relationship_identity"]


def test_project_language_capability_distinguishes_recognition_from_engine_availability() -> None:
    authority = build_preflight_scope_authority(
        topology=_topology(),
        projects=None,
        repository_file_count=12,
        repository_inventory_truncated=False,
        repository_language_counts={"typescript": 8, "rust": 4},
        effective_file_count=12,
        effective_inventory_truncated=False,
        effective_language_counts={"typescript": 8, "rust": 4},
        effective_project_file_counts={"MAIN": 12},
        polyglot_capabilities={
            "languages": {
                "typescript": {"claim_level": "deep_specialist"},
            }
        },
        effective_project_inventory_evidence={
            "MAIN": {
                "language_counts": {"typescript": 8, "rust": 4},
                "analysis_language_counts": {"typescript": 8},
            }
        },
    )
    bound = bind_atlas_materialization(authority, _atlas("MAIN", files_per_project=8))
    capability = bound["project_language_capabilities"]["MAIN"]

    assert capability["status"] == "PARTIAL_ENGINE_COVERAGE"
    assert capability["recognized_language_families"] == ["rust", "typescript"]
    assert capability["engine_available_language_families"] == ["typescript"]
    assert capability["engine_unavailable_language_families"] == ["rust"]
    assert capability["language_claim_levels"] == {
        "rust": "not_available",
        "typescript": "deep_specialist",
    }
    assert capability["recognition_does_not_authorize_engine_activation"] is True
    assert bound["project_language_capability_identity"].startswith("sha256:")


def test_default_runtime_fallback_preserves_system_kind_and_relationship_semantics() -> None:
    topology = _topology()
    topology["project_candidate_system_kinds"] = {
        "MAIN": {
            "kind": "application",
            "authority": "technical_system_kind_static_inventory_v1",
            "confidence": "high",
            "evidence": ["application_framework_and_runtime_script"],
            "candidate_kinds": ["application"],
        }
    }
    authority = runtime_scope_authority(
        dynamic_config={"_repository_topology": topology},
        projects=None,
    )

    assert authority["project_system_kinds"]["MAIN"]["kind"] == "application"
    assert authority["project_system_kind_identity"].startswith("sha256:")
    assert authority["project_relationship_roles"] == {"MAIN": "host"}
    assert authority["project_relationship_identity"].startswith("sha256:")


def test_explicit_main_selection_is_bounded_not_incomplete_for_unselected_sibling() -> None:
    authority = bind_atlas_materialization(
        _preflight(projects="MAIN", excluded=True, repository_truncated=True),
        _atlas("MAIN"),
    )

    assert authority["evidence_status"] == BOUNDED_PROJECT_SELECTION
    assert authority["claim_scope"] == "explicit_project_selection"
    assert authority["full_repository_claim_eligible"] is False
    assert authority["incomplete_reasons"] == []


def test_automatic_selection_with_supported_sources_outside_scope_is_incomplete() -> None:
    authority = _preflight(excluded=True)

    assert authority["evidence_status"] == INCOMPLETE_EVIDENCE
    assert "supported_source_outside_effective_scope" in authority["incomplete_reasons"]
    assert "material_excluded_project_candidates" in authority["incomplete_reasons"]
    assert authority["full_repository_claim_eligible"] is False


def test_automatic_truncated_repository_inventory_is_incomplete() -> None:
    authority = _preflight(repository_truncated=True)

    assert authority["evidence_status"] == INCOMPLETE_EVIDENCE
    assert "repository_inventory_truncated" in authority["incomplete_reasons"]


def test_atlas_missing_authorized_project_degrades_scope_without_erasing_upstream_identity() -> None:
    authority = _preflight(projects="MAIN")
    expected_id = authority["scope_authority_id"]

    bound = bind_atlas_materialization(authority, {})

    assert bound["scope_authority_id"] == expected_id
    assert bound["evidence_status"] == INCOMPLETE_EVIDENCE
    assert bound["atlas_consistency"] == INCOMPLETE_EVIDENCE
    assert "effective_projects_missing_from_atlas" in bound["incomplete_reasons"]


def test_atlas_missing_one_canonical_analysis_source_fails_closed() -> None:
    authority = _preflight()

    bound = bind_atlas_materialization(
        authority,
        _atlas("MAIN", files_per_project=9),
    )

    assert bound["evidence_status"] == INCOMPLETE_EVIDENCE
    assert bound["claim_eligible_source_file_count"] == 0
    assert "atlas_indexed_fewer_files_than_effective_supported_source_inventory" in bound["incomplete_reasons"]


def test_consumer_project_mismatch_is_visible_at_terminal_reconciliation() -> None:
    base = bind_atlas_materialization(_preflight(), _atlas("MAIN"))
    audit = bind_consumer_projects(base, layer="audit", observed_projects=["MAIN"])
    oracle = bind_consumer_projects(base, layer="architecture_oracle", observed_projects=[])

    reconciled = reconcile_consumer_scope_authorities(
        base,
        {"audit": audit, "architecture_oracle": oracle},
    )
    receipt = scope_receipt_details(reconciled)

    assert reconciled["evidence_status"] == INCOMPLETE_EVIDENCE
    assert reconciled["consumer_scope_checks"]["audit"]["status"] == "CONSISTENT"
    assert reconciled["consumer_scope_checks"]["architecture_oracle"]["status"] == "PROJECT_SET_MISMATCH"
    assert "architecture_oracle:PROJECT_SET_MISMATCH" in receipt["scope_consumer_checks"]
    assert receipt["scope_full_repository_claim_eligible"] is False


def test_quality_scope_identity_mismatch_blocks_action_surface_and_preserves_prior_checks() -> None:
    base = bind_atlas_materialization(_preflight(), _atlas("MAIN"))
    base = reconcile_consumer_scope_authorities(
        base,
        {"audit": bind_consumer_projects(base, layer="audit", observed_projects=["MAIN"])},
    )
    mismatched = dict(base)
    mismatched["scope_authority_id"] = "sha256:" + "f" * 64

    reconciled, actionable = reconcile_quality_scope_authority(
        base,
        {
            "scope_gate_status": "PASS",
            "analysis_scope_authority": mismatched,
        },
    )

    assert actionable is False
    assert reconciled["evidence_status"] == INCOMPLETE_EVIDENCE
    assert reconciled["consumer_scope_checks"]["audit"]["status"] == "CONSISTENT"
    assert reconciled["consumer_scope_checks"]["quality_gate"]["status"] == "IDENTITY_MISMATCH"


def test_quality_scope_gate_must_pass_even_when_identity_matches() -> None:
    base = bind_atlas_materialization(_preflight(), _atlas("MAIN"))

    reconciled, actionable = reconcile_quality_scope_authority(
        base,
        {
            "scope_gate_status": "INCOMPLETE_EVIDENCE",
            "analysis_scope_authority": base,
        },
    )

    assert actionable is False
    assert "quality_gate_scope_not_accepted" in reconciled["incomplete_reasons"]


def test_runtime_rejects_preflight_from_a_different_topology_identity(
    tmp_path,
) -> None:
    topology = _topology()
    stale = _preflight()
    stale["topology_authority_id"] = "sha256:" + "e" * 64
    preflight_payload = {
        "summary": {"analysis_scope": {"scope_authority": stale}},
    }
    (tmp_path / "external_target_preflight.json").write_text(
        json.dumps(preflight_payload),
        encoding="utf-8",
    )
    with patch(
        "tools.core.json_io.load_raw_artifact_path",
        return_value=preflight_payload,
    ) as loader:
        authority = runtime_scope_authority(
            dynamic_config={"_target_root_override": topology},
            projects=None,
            atlas=_atlas("MAIN"),
            raw_dir=tmp_path,
        )

    loader.assert_called_once_with(tmp_path / "external_target_preflight.json", {})
    assert authority["evidence_status"] == INCOMPLETE_EVIDENCE
    assert "preflight_discovery_topology_authority_identity_mismatch" in authority["incomplete_reasons"]


def test_runtime_reads_external_preflight_from_long_generation_path(tmp_path) -> None:
    topology = _topology()
    preflight_authority = _preflight()
    preflight_payload = {
        "summary": {"analysis_scope": {"scope_authority": preflight_authority}},
    }
    raw_dir = (
        tmp_path
        / ("target_" + ("x" * 100))
        / "generations"
        / ("run_" + ("y" * 100))
        / ".raw"
    )
    target_dir = raw_dir.parents[2]
    try:
        native_raw_dir = Path(native_filesystem_path(raw_dir))
        native_raw_dir.mkdir(parents=True)
        preflight_path = Path(
            native_filesystem_path(raw_dir / "external_target_preflight.json")
        )
        assert len(str(preflight_path)) > 260
        preflight_path.write_text(json.dumps(preflight_payload), encoding="utf-8")

        authority = runtime_scope_authority(
            dynamic_config={"_target_root_override": topology},
            projects=None,
            atlas=_atlas("MAIN"),
            raw_dir=raw_dir,
        )
    finally:
        __import__("shutil").rmtree(
            Path(native_filesystem_path(target_dir)),
            ignore_errors=False,
        )

    assert authority["evidence_status"] == COMPLETE_REPOSITORY
    assert authority["scope_authority_id"] == preflight_authority["scope_authority_id"]
    assert "external_target_preflight_scope_authority_missing" not in authority["incomplete_reasons"]


def test_default_runtime_does_not_inherit_external_preflight(tmp_path) -> None:
    topology = _topology()
    stale = _preflight()
    stale["scope_authority_id"] = "sha256:" + "e" * 64
    (tmp_path / "external_target_preflight.json").write_text("{}", encoding="utf-8")

    with patch("tools.core.json_io.load_raw_artifact_path") as loader:
        authority = runtime_scope_authority(
            dynamic_config={"_repository_topology": topology},
            projects=["MAIN"],
            atlas=_atlas("MAIN"),
            raw_dir=tmp_path,
        )

    loader.assert_not_called()
    assert authority["evidence_status"] == BOUNDED_PROJECT_SELECTION
    assert authority["incomplete_reasons"] == []


def test_operator_packet_withholds_directives_when_quality_scope_identity_differs(
    monkeypatch,
) -> None:
    from tools import generate_nexora_operator_packet as operator

    authority = bind_atlas_materialization(_preflight(), _atlas("MAIN"))
    mismatched = dict(authority)
    mismatched["scope_authority_id"] = "sha256:" + "d" * 64
    payloads = {
        "analysis_scope_authority.json": {"scope_authority": authority},
        "quality_gate.json": {
            "scope_gate_status": "PASS",
            "analysis_scope_authority": mismatched,
        },
        "audit_report.json": {"violations": [{"project": "MAIN", "file": "src/a.ts"}]},
    }
    monkeypatch.setattr(
        operator,
        "_load",
        lambda path: payloads.get(path.name, {}),
    )
    monkeypatch.setattr(operator, "load_capability_registry", lambda: {})
    monkeypatch.setattr(operator, "load_capability_activation_plan", lambda: {})
    monkeypatch.setattr(operator, "relevant_capability_contracts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(operator, "relevant_activation_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(operator, "architecture_governance_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        operator,
        "build_agent_action_directives",
        lambda *_args, **_kwargs: [{"id": "unsafe", "target_files": ["src/a.ts"]}],
    )

    packet = operator.build_operator_packet()

    surface = packet["target_repository_agent_surface"]
    assert surface["status"] == INCOMPLETE_EVIDENCE
    assert surface["directives"] == []
    assert packet["agent_action_directives"] == []
    assert surface["scope_authority"]["scope_authority_id"] == authority["scope_authority_id"]


def test_non_main_validation_subset_uses_shared_authority_without_redefining_repository() -> None:
    topology = _topology()
    topology["project_candidates"]["VARIANT_A"] = "Variations/a"
    topology["project_candidate_relationship_roles"]["VARIANT_A"] = "variant"
    topology["project_candidate_role_authority"]["VARIANT_A"] = {"relationship_resolved": True}
    topology["selected_projects"]["VARIANT_A"] = "Variations/a"
    authority = build_preflight_scope_authority(
        topology=topology,
        projects=None,
        repository_file_count=3,
        repository_inventory_truncated=False,
        repository_language_counts={"typescript": 3},
        effective_file_count=3,
        effective_inventory_truncated=False,
        effective_language_counts={"typescript": 3},
        effective_project_file_counts={"MAIN": 2, "VARIANT_A": 1},
        polyglot_capabilities={"languages": {"typescript": {}}},
    )
    authority = bind_atlas_materialization(
        authority,
        {
            "MAIN": {"files": {"src/a.ts": {}, "src/b.ts": {}}},
            "VARIANT_A": {"files": {"Variations/a/index.ts": {}}},
        },
    )

    validation = bind_consumer_projects(
        authority,
        layer="validation_oracle",
        observed_projects=["VARIANT_A"],
        expected_projects=["VARIANT_A"],
    )

    assert validation["scope_authority_id"] == authority["scope_authority_id"]
    assert validation["evidence_status"] == COMPLETE_REPOSITORY
    assert validation["layer_consistency"] == "CONSISTENT"
    assert validation["validation_oracle_expected_projects"] == ["VARIANT_A"]


def test_scope_identity_changes_only_with_topology_or_requested_projection() -> None:
    topology = _topology(excluded=True)

    assert scope_authority_id(topology, None) == scope_authority_id(dict(topology), None)
    assert scope_authority_id(topology, None) != scope_authority_id(topology, "MAIN")


def test_quality_gate_consumes_shared_scope_instead_of_recomputing_it(monkeypatch) -> None:
    authority = bind_atlas_materialization(_preflight(), _atlas("MAIN"))
    monkeypatch.setattr(
        "tools.engines.quality_gate.load_json_file",
        lambda *_args, **_kwargs: {"scope_authority": authority},
    )

    observed, passed, shared_payload = _quality_scope_gate(
        {"audit_scope": {"scope_authority": authority}}
    )

    assert passed is True
    assert shared_payload == {"scope_authority": authority}
    assert observed["scope_authority_id"] == authority["scope_authority_id"]
    assert observed["consumer_scope_checks"]["audit"]["status"] == "CONSISTENT"


def test_quality_gate_fails_closed_on_audit_scope_identity_mismatch(monkeypatch) -> None:
    authority = bind_atlas_materialization(_preflight(), _atlas("MAIN"))
    mismatched = dict(authority)
    mismatched["scope_authority_id"] = "sha256:" + "f" * 64
    monkeypatch.setattr(
        "tools.engines.quality_gate.load_json_file",
        lambda *_args, **_kwargs: {"scope_authority": authority},
    )

    observed, passed, shared_payload = _quality_scope_gate(
        {"audit_scope": {"scope_authority": mismatched}}
    )

    assert passed is False
    assert shared_payload == {"scope_authority": authority}
    assert observed["evidence_status"] == INCOMPLETE_EVIDENCE
    assert observed["consumer_scope_checks"]["audit"]["status"] == "IDENTITY_MISMATCH"


def test_quality_gate_fails_closed_on_audit_analysis_gap(monkeypatch) -> None:
    authority = bind_atlas_materialization(_preflight(), _atlas("MAIN"))
    monkeypatch.setattr(
        "tools.engines.quality_gate.load_json_file",
        lambda *_args, **_kwargs: {"scope_authority": authority},
    )

    observed, passed, _shared_payload = _quality_scope_gate(
        {
            "audit_scope": {"scope_authority": authority},
            "summary": {"analysis_gap_count": 1},
        }
    )

    assert passed is False
    assert observed["evidence_status"] == INCOMPLETE_EVIDENCE
    assert observed["consumer_scope_checks"]["audit_analysis_gaps"] == {
        "status": INCOMPLETE_EVIDENCE,
        "count": 1,
    }
    assert "audit_analysis_gaps:1" in observed["incomplete_reasons"]


def test_audit_lineage_binds_the_exact_shared_scope_artifact() -> None:
    authority = bind_atlas_materialization(_preflight(), _atlas("MAIN"))
    scope_artifact = {"scope_authority": authority}
    structural_contract = {
        "status": "available",
        "passed": True,
        "atlas_contract_file_ratio": 1.0,
        "member_detail_contract_ratio": 1.0,
        "genome_occurrence_contract_ratio": 1.0,
        "atlas_current_version_ratio": 1.0,
        "genome_current_version_ratio": 1.0,
    }
    with (
        patch.object(audit, "_load_structural_contract_health", return_value=structural_contract),
        patch.object(audit, "save_json_atomic"),
        patch.object(audit, "save_text_atomic"),
        patch.object(audit, "write_current_atlas_lineage") as lineage,
    ):
        audit._write_outputs(
            {},
            [],
            {"total": 0, "by_rule": {}},
            audited_projects=["MAIN"],
            atlas_project_count=1,
            atlas=_atlas("MAIN"),
            scope_authority=authority,
            scope_authority_artifact=scope_artifact,
        )

    assert lineage.call_args.kwargs["dependency_payloads"] == {
        "analysis_scope_authority": scope_artifact
    }


def test_only_orchestrator_produces_runtime_scope_authority() -> None:
    tools_root = Path(__file__).resolve().parents[1]
    callers: list[str] = []
    for path in tools_root.rglob("*.py"):
        if "tests" in path.parts or path.name == "analysis_scope_authority.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.id if isinstance(function, ast.Name) else (
                function.attr if isinstance(function, ast.Attribute) else ""
            )
            if name == "runtime_scope_authority":
                callers.append(path.relative_to(tools_root.parent).as_posix())

    assert callers == ["tools/orchestrators/orchestrator.py"]


def test_orchestrator_main_does_not_shadow_global_scope_writer() -> None:
    orchestrator_path = Path(__file__).resolve().parents[1] / "orchestrators" / "orchestrator.py"
    tree = ast.parse(orchestrator_path.read_text(encoding="utf-8"), filename=str(orchestrator_path))
    main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    local_imports = {
        alias.asname or alias.name
        for node in ast.walk(main)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "save_json_atomic" not in local_imports


def test_quality_lineage_uses_canonical_not_scoped_atlas() -> None:
    quality_path = Path(__file__).resolve().parents[1] / "engines" / "quality_gate.py"
    tree = ast.parse(quality_path.read_text(encoding="utf-8"), filename=str(quality_path))
    quality_lineage_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "write_current_atlas_lineage":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        artifact_id = keywords.get("artifact_id")
        if isinstance(artifact_id, ast.Constant) and artifact_id.value == "quality_gate":
            quality_lineage_calls.append(keywords)

    normal_calls = [
        keywords
        for keywords in quality_lineage_calls
        if isinstance(keywords.get("atlas"), ast.Name)
    ]
    failure_drill_calls = [
        keywords
        for keywords in quality_lineage_calls
        if isinstance(keywords.get("atlas"), ast.Dict)
    ]

    assert len(normal_calls) == 1
    assert len(failure_drill_calls) == 1
    lineage_atlas = normal_calls[0]["atlas"]
    assert isinstance(lineage_atlas, ast.Name)
    assert lineage_atlas.id == "canonical_atlas"


def test_artifact_validator_uses_bounded_effective_projects_not_full_config() -> None:
    projects, source = _expected_analysis_projects(
        {
            "scope": {
                "evidence_status": BOUNDED_PROJECT_SELECTION,
                "effective_runtime_projects": {"MAIN": "src"},
            }
        },
        {"MAIN", "VARIANT_A"},
    )

    assert projects == {"MAIN"}
    assert source == "analysis_scope_authority:BOUNDED_PROJECT_SELECTION"


def test_artifact_validator_falls_back_to_config_when_scope_is_unusable() -> None:
    projects, source = _expected_analysis_projects(
        {
            "scope": {
                "evidence_status": INCOMPLETE_EVIDENCE,
                "effective_runtime_projects": {"MAIN": "src"},
            }
        },
        {"MAIN", "VARIANT_A"},
    )

    assert projects == {"MAIN", "VARIANT_A"}
    assert source == "config_fallback_due_to_unusable_scope_authority"


def test_scope_consumer_uses_sqlite_first_raw_artifact_loader(tmp_path: Path) -> None:
    authority = bind_atlas_materialization(_preflight(), _atlas("MAIN"))
    artifact = {"scope_authority": authority}
    raw_dir = tmp_path / ".raw"

    with patch(
        "tools.core.json_io.load_raw_artifact_path",
        return_value=artifact,
    ) as loader:
        observed_artifact, observed_authority = load_scope_authority_for_consumer(raw_dir)

    loader.assert_called_once_with(raw_dir / "analysis_scope_authority.json", {})
    assert observed_artifact == artifact
    assert observed_authority == authority

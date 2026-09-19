from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tools.core.analysis_snapshot_lineage import write_lineage_receipt
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.audit_finding_generation import finding_scope_sha256
from tools.core.contextos_mcp import build_surgical_operation_packet, render_surgical_operation_brief
from tools.core.contextos_signal_limits import contextos_signal_limit
from tools.core.surgical_packet_inputs import evaluate_surgical_packet_inputs
from tools.engines import clone_detector, live_surface_analyzer
from tools.mcp import server


ROOT = Path(__file__).resolve().parents[2]


def test_surgical_packet_filters_companion_signals_before_focus_projection(tmp_path: Path) -> None:
    packet = build_surgical_operation_packet(
        {
            "active_signals": [
                {
                    "node_key": "MAIN::src/App.tsx",
                    "relative_path": "src/App.tsx",
                    "target_ref": "MAIN::src/App.tsx",
                },
                {
                    "node_key": "SAGE_COMPANION::tools/mcp/server.py",
                    "relative_path": "Embedded-SAGE-Install/tools/mcp/server.py",
                    "target_ref": "SAGE_COMPANION::tools/mcp/server.py",
                },
            ]
        },
        project_scope="MAIN",
        raw_dir=tmp_path,
    )

    assert [row["node_key"] for row in packet["l1_focus"]] == ["MAIN::src/App.tsx"]


def test_hot_file_packet_is_orientation_only_without_actionable_violation(tmp_path: Path) -> None:
    packet = build_surgical_operation_packet(
        {
            "active_signals": [
                {
                    "node_key": "MAIN::src/shared/types/project.ts",
                    "relative_path": "src/shared/types/project.ts",
                    "target_ref": "MAIN::src/shared/types/project.ts",
                    "reasoning_breadcrumbs": ["Changed file belongs to the current dependency halo."],
                }
            ]
        },
        audit_report={"violations": []},
        quality_gate={"checks": []},
        priority_pack={"items": []},
        project_scope="MAIN",
        raw_dir=tmp_path,
    )

    directive = packet["agent_action_directives"][0]
    brief = render_surgical_operation_brief(packet)

    assert directive["actionability"] == "orientation_only"
    assert directive["mutation_proposed"] is False
    assert directive["mutation_authority"] == "not_granted_by_this_directive"
    assert directive["human_approval_required"] is False
    assert directive["approval_requirement_scope"] == "not_applicable_without_separate_actionable_mutation"
    assert "does not propose or authorize a mutation" in brief
    assert "Make the smallest safe change" not in brief
    assert "approval_is_not_mutation_authority: true" in brief


def test_scoped_audit_directive_names_watchdog_source_artifact(tmp_path: Path) -> None:
    packet = build_surgical_operation_packet(
        {"active_signals": []},
        audit_report={
            "violations": [
                {
                    "project": "MAIN",
                    "file": "src/domain.ts",
                    "rule": "bounded_rule",
                    "detail": "current scoped evidence",
                }
            ]
        },
        audit_source_artifact="output/.raw/watchdog_audit_report.json",
        raw_dir=tmp_path,
    )

    assert packet["agent_action_directives"][0]["source_artifacts"] == [
        "output/.raw/watchdog_audit_report.json",
        "config/architecture_doctrine.json",
    ]


def test_public_surgical_packet_prefers_current_scoped_watchdog_audit(tmp_path: Path) -> None:
    raw_dir = tmp_path.resolve()
    signals = {"active_signals": []}
    evidence = {
        "status": "PASS",
        "blocked_inputs": [],
        "omitted_inputs": [],
        "inputs": [],
    }
    changed_files = ["MAIN::src/domain.ts"]
    atlas_commit = {
        "snapshot_id": "snapshot-18",
        "state": "complete",
        "generation_mode": "surgical",
        "generation_transition": {
            "kind": "scoped_delta",
            "parent_snapshot_id": "snapshot-17",
            "changed_files": changed_files,
            "changed_files_sha256": finding_scope_sha256(changed_files),
            "deleted_files": [],
            "deleted_files_sha256": finding_scope_sha256([]),
        },
    }
    watchdog_audit = {
        "meta": {"kind": "watchdog_audit_report"},
        "artifact_identity": {
            "status": "BOUND",
            "atlas_snapshot_id": "snapshot-18",
            "artifact_root": str(raw_dir),
        },
        "audit_scope": {
            "scope_kind": "scoped_change",
            "full_repository_claim": False,
            "scope_status": "complete",
            "requested_files": changed_files,
            "audited_files": changed_files,
            "unresolved_requested_files": [],
        },
        "violations": [{"project": "MAIN", "file": "src/domain.ts", "rule": "bounded_rule"}],
    }
    trust = {
        "status": "FAIL",
        "checks": [
            {"name": "artifact_present:atlas", "passed": True, "severity": "error"},
            {"name": "sqlite_primary:atlas", "passed": True, "severity": "warning"},
            {"name": "artifact_present:audit_report", "passed": True, "severity": "error"},
            {"name": "lineage:audit_report_bound_to_current_snapshot", "passed": False, "severity": "error"},
            {"name": "artifact_present:quality_gate", "passed": True, "severity": "error"},
            {"name": "lineage:quality_gate_bound_to_current_snapshot", "passed": False, "severity": "error"},
        ],
    }
    loaded_names: list[str] = []

    def load_payload(path: Path) -> dict:
        name = Path(path).name
        loaded_names.append(name)
        if name == "atlas_commit.json":
            return atlas_commit
        if name == "watchdog_audit_report.json":
            return watchdog_audit
        return {}

    packet = {"summary": {}, "agent_action_directives": []}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=raw_dir),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value=trust),
        patch.object(server, "_load_json", side_effect=load_payload),
        patch(
            "tools.core.surgical_packet_inputs.evaluate_surgical_packet_inputs",
            return_value=(evidence, {"signals": signals}),
        ),
        patch("tools.core.contextos_mcp.build_surgical_operation_packet", return_value=packet) as builder,
        patch.object(server, "_enrich_surgical_packet_with_sqlite_impact", side_effect=lambda _raw, result, **_kwargs: result),
        patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
    ):
        payload = json.loads(server.get_surgical_operation_packet(format="json"))

    assert payload["status"] == "INCOMPLETE_EVIDENCE"
    assert payload["input_evidence"]["context_inputs"]["audit"]["status"] == "PASS"
    assert payload["input_evidence"]["context_inputs"]["quality_gate"]["status"] == "OMITTED"
    assert "audit_report.json" not in loaded_names
    assert "quality_gate.json" not in loaded_names
    assert builder.call_args.kwargs["audit_report"] == watchdog_audit
    assert builder.call_args.kwargs["quality_gate"] == {}
    assert builder.call_args.kwargs["audit_source_artifact"] == "output/.raw/watchdog_audit_report.json"


def test_surgical_packet_bounds_graph_samples_without_losing_totals(tmp_path: Path) -> None:
    direct = [f"MAIN::src/direct-{index}.ts" for index in range(30)]
    transitive = [f"MAIN::src/transitive-{index}.ts" for index in range(60)]
    edges = [
        {"source": node, "target": "MAIN::src/domain.ts"}
        for node in direct
    ]
    packet = build_surgical_operation_packet(
        {
            "active_signals": [
                {
                    "node_key": "MAIN::src/domain.ts",
                    "relative_path": "src/domain.ts",
                    "direct_dependents": direct,
                    "transitive_dependents": transitive,
                }
            ]
        },
        circular_deps_data={"edges": edges},
        project_scope="MAIN",
        raw_dir=tmp_path,
    )

    limit = contextos_signal_limit("agent_related_files")
    focus = packet["l1_focus"][0]
    trace = packet["upstream_traces"][0]

    assert len(focus["direct_dependents"]) == limit
    assert focus["direct_dependents_count"] == 30
    assert focus["direct_dependents_omitted"] == 30 - limit
    assert len(focus["transitive_dependents"]) == limit
    assert focus["transitive_dependents_count"] == 60
    assert focus["transitive_dependents_omitted"] == 60 - limit
    assert len(trace["direct_dependents"]) == limit
    assert trace["direct_dependents_count"] == 30
    assert trace["direct_dependents_omitted"] == 30 - limit


def test_actionable_audit_violation_remains_a_bounded_mutation_proposal(tmp_path: Path) -> None:
    packet = build_surgical_operation_packet(
        {"active_signals": []},
        audit_report={
            "violations": [
                {
                    "project": "MAIN",
                    "file": "src/domain.ts",
                    "rule": "cross_module_deep_import",
                    "detail": "Import crosses the declared module boundary.",
                }
            ]
        },
        project_scope="MAIN",
        raw_dir=tmp_path,
    )

    directive = packet["agent_action_directives"][0]
    brief = render_surgical_operation_brief(packet)

    assert directive["actionability"] == "actionable_proposal"
    assert directive["mutation_proposed"] is True
    assert directive["mutation_authority"] == "separate_actor_policy_and_approval"
    assert directive["approval_requirement_scope"] == "proposed_mutation"
    assert "smallest evidence-backed repository change" in brief


def test_public_surgical_packet_uses_same_sqlite_dependency_projection_as_impact_radius() -> None:
    signals = {"active_signals": [{"node_key": "MAIN::src/domain.ts"}]}
    evidence = {"status": "PASS", "blocked_inputs": [], "omitted_inputs": []}
    packet = {
        "summary": {},
        "agent_action_directives": [
            {
                "id": "contextos_focus_1",
                "target_files": ["src/domain.ts"],
                "file_context": [
                    {
                        "repo_relative_path": "src/domain.ts",
                        "atlas_node": "MAIN::src/domain.ts",
                    }
                ],
                "evidence_boundary": {
                    "direct_dependents_omitted": 17,
                    "transitive_dependents_omitted": 9,
                },
            },
            {
                "id": "other_project_focus",
                "target_files": ["src/domain.ts"],
                "file_context": [{"atlas_node": "OTHER::src/domain.ts"}],
                "evidence_boundary": {"direct_dependents_omitted": 33},
            },
            {
                "id": "audit_violation_same_target",
                "target_refs": ["MAIN::src/domain.ts"],
                "target_files": ["src/domain.ts"],
            },
        ],
        "upstream_traces": [
            {
                "target_ref": "MAIN::src/domain.ts",
                "direct_dependents": [],
                "direct_dependent_files": [],
            }
        ],
        "l1_focus": [
            {
                "node_key": "MAIN::src/domain.ts",
                "target_ref": "MAIN::src/domain.ts",
                "direct_dependents_count": 0,
                "direct_dependents": [],
                "direct_dependents_omitted": 0,
                "transitive_dependents_count": 0,
                "transitive_dependents": [],
                "transitive_dependents_omitted": 0,
            }
        ],
    }
    impact = {
        "dependency_graph_source": "sqlite_dependencies",
        "direct_dependents_count": 3,
        "direct_dependents": ["src/a.ts", "src/b.ts", "src/c.ts"],
        "direct_dependent_refs": [
            "MAIN::src/a.ts",
            "MAIN::src/b.ts",
            "MAIN::src/c.ts",
        ],
        "blast_radius_size": 5,
        "transitive_dependents": [
            "src/a.ts",
            "src/b.ts",
            "src/c.ts",
            "src/d.ts",
            "src/e.ts",
        ],
    }
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(
            server,
            "_ensure_agent_artifact_chain_current",
            return_value={
                "status": "PASS",
                "checks": [
                    {"name": "artifact_present:atlas", "passed": True, "severity": "error"},
                    {"name": "sqlite_primary:atlas", "passed": True, "severity": "warning"},
                ],
            },
        ),
        patch("tools.core.surgical_packet_inputs.evaluate_surgical_packet_inputs", return_value=(evidence, {"signals": signals})),
        patch("tools.core.contextos_mcp.build_surgical_operation_packet", return_value=packet),
        patch.object(server, "_sqlite_impact_radius_from_raw", return_value=impact) as impact_query,
        patch.object(
            server,
            "_partition_directives_by_source_freshness",
            side_effect=lambda _raw_dir, directives, **_kwargs: (directives, []),
        ),
        patch.object(
            server,
            "_load_json",
            side_effect=lambda path: {"snapshot_id": "snapshot-17"} if Path(path).name == "atlas_commit.json" else {},
        ),
        patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
    ):
        payload = json.loads(server.get_surgical_operation_packet(format="json"))

    focus = payload["l1_focus"][0]
    assert focus["direct_dependents_count"] == 3
    assert focus["direct_dependents"] == ["src/a.ts", "src/b.ts", "src/c.ts"]
    assert focus["direct_dependents_omitted"] == 0
    assert focus["transitive_dependents_count"] == 5
    assert focus["dependency_projection_status"] == "sqlite_current_snapshot"
    assert focus["dependency_graph_source"] == "sqlite_dependencies"
    assert focus["dependency_snapshot_id"] == "snapshot-17"
    assert payload["dependency_projections"][0]["direct_dependents_count"] == 3
    assert payload["upstream_traces"][0]["direct_dependents"] == [
        "MAIN::src/a.ts",
        "MAIN::src/b.ts",
        "MAIN::src/c.ts",
    ]
    assert payload["upstream_traces"][0]["direct_dependent_files"] == [
        "src/a.ts",
        "src/b.ts",
        "src/c.ts",
    ]
    assert payload["upstream_traces"][0]["dependency_snapshot_id"] == "snapshot-17"
    boundary = payload["agent_action_directives"][0]["evidence_boundary"]
    assert boundary["dependency_projection_id"] == "sqlite_current_snapshot"
    assert boundary["dependency_graph_source"] == "sqlite_dependencies"
    assert boundary["dependency_snapshot_id"] == "snapshot-17"
    assert boundary["direct_dependents_omitted"] == 0
    assert boundary["transitive_dependents_omitted"] == 0
    assert payload["agent_action_directives"][1]["evidence_boundary"] == {
        "direct_dependents_omitted": 33
    }
    assert "evidence_boundary" not in payload["agent_action_directives"][2]
    impact_query.assert_called_once_with(
        server.RAW_DIR,
        "MAIN::src/domain.ts",
        target_root="",
        depth=2,
    )


def test_surgical_brief_uses_canonical_dependency_total_for_its_bounded_sample() -> None:
    packet = {
        "agent_action_directives": [
            {
                "intent": "inspect_hot_file_and_blast_radius",
                "target_files": ["src/domain.ts"],
                "target_refs": ["MAIN::src/domain.ts"],
                "rule": "contextos_active_focus",
                "rule_explanation": {"label": "ContextOS Active Focus", "mode": "advisory"},
                "actionability": "orientation_only",
                "mutation_proposed": False,
                "mutation_authority": "not_granted_by_this_directive",
                "human_approval_required": False,
            }
        ],
        "dependency_projections": [
            {
                "target_ref": "MAIN::src/domain.ts",
                "direct_dependents_count": 194,
                "direct_dependents": [f"src/consumer-{index}.ts" for index in range(12)],
                "direct_dependents_omitted": 182,
                "dependency_graph_source": "sqlite_dependencies",
                "dependency_snapshot_id": "snapshot-18",
            }
        ],
    }

    brief = render_surgical_operation_brief(packet)

    assert "direct_dependents_total: 194" in brief
    assert "direct_dependents_shown: 8" in brief
    assert "direct_dependents_omitted: 186" in brief
    assert "dependency_snapshot_id: snapshot-18" in brief


def test_typescript_test_impact_prioritizes_compiler_backed_package_scripts(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "contract.ts").write_text("export interface Contract {}\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "pnpm@10.0.0",
                "scripts": {
                    "test": "vitest run",
                    "lint": "eslint src",
                    "build": "tsc -b && vite build",
                },
            }
        ),
        encoding="utf-8",
    )

    commands = server._nearest_package_validation_commands(
        {"analysis_root": str(tmp_path), "target_file": "src/contract.ts"}
    )

    assert commands == [
        'pnpm --dir "." run build',
        'pnpm --dir "." run test',
        'pnpm --dir "." run lint',
    ]

    component_commands = server._nearest_package_validation_commands(
        {"analysis_root": str(tmp_path), "target_file": "src/Component.tsx"}
    )
    assert component_commands == [
        'pnpm --dir "." run test',
        'pnpm --dir "." run lint',
    ]


def _atlas(tmp_path: Path) -> tuple[dict, dict]:
    atlas = {"MAIN": {"project": {"root": str(tmp_path)}, "files": {}}, "symbols": {}}
    return atlas, build_atlas_commit(atlas)


def _write(tmp_path: Path, artifact_id: str, producer: str, payload: dict, atlas: dict, commit: dict, dependencies=None):
    return write_lineage_receipt(
        artifact_id=artifact_id,
        producer=producer,
        artifact_payload=payload,
        atlas=atlas,
        atlas_commit=commit,
        dependency_payloads=dependencies,
        raw_dir=tmp_path,
    )


def test_required_signal_without_receipt_blocks_packet(tmp_path: Path) -> None:
    _, commit = _atlas(tmp_path)
    evidence, usable = evaluate_surgical_packet_inputs(
        raw_dir=tmp_path,
        expected_snapshot_id=commit["snapshot_id"],
        payloads={"signals": {"active_signals": []}},
    )
    assert evidence["status"] == "BLOCKED"
    assert evidence["blocked_inputs"] == ["signals"]
    assert usable == {}


def test_optional_unbound_inputs_are_reported_and_omitted(tmp_path: Path) -> None:
    atlas, commit = _atlas(tmp_path)
    signals = {"active_signals": []}
    _write(tmp_path, "signals", "tools.engines.quant_engine", signals, atlas, commit)
    evidence, usable = evaluate_surgical_packet_inputs(
        raw_dir=tmp_path,
        expected_snapshot_id=commit["snapshot_id"],
        payloads={"signals": signals},
    )
    assert evidence["status"] == "PARTIAL_CONTEXT"
    assert evidence["omitted_inputs"] == ["circular_deps", "live_surface_priority_pack"]
    assert usable == {"signals": signals}


def test_mixed_snapshot_signal_blocks_packet(tmp_path: Path) -> None:
    atlas, commit = _atlas(tmp_path)
    signals = {"active_signals": []}
    _write(tmp_path, "signals", "tools.engines.quant_engine", signals, atlas, commit)
    evidence, _ = evaluate_surgical_packet_inputs(
        raw_dir=tmp_path,
        expected_snapshot_id="different-snapshot",
        payloads={"signals": signals},
    )
    assert evidence["status"] == "BLOCKED"
    assert evidence["inputs"][0]["snapshot_binding"] == "MISMATCH"


def test_forbidden_cross_reality_artifact_blocks_packet(tmp_path: Path) -> None:
    atlas, commit = _atlas(tmp_path)
    signals = {"active_signals": []}
    _write(tmp_path, "signals", "tools.engines.quant_engine", signals, atlas, commit)
    evidence, usable = evaluate_surgical_packet_inputs(
        raw_dir=tmp_path,
        expected_snapshot_id=commit["snapshot_id"],
        payloads={"signals": signals, "react_edge_case_validation": {"status": "PASS"}},
    )
    assert evidence["status"] == "BLOCKED"
    assert evidence["forbidden_inputs"] == ["react_edge_case_validation"]
    assert usable == {"signals": signals}


def test_complete_input_chain_is_accepted(tmp_path: Path) -> None:
    atlas, commit = _atlas(tmp_path)
    genome = {"Symbol": []}
    dead_code = {"items": []}
    clones = {"clusters": []}
    signals = {"active_signals": []}
    circular = {"cycles": []}
    priority = {"items": []}
    _write(tmp_path, "genome", "tools.engines.nuclear_processor", genome, atlas, commit)
    _write(tmp_path, "dead_code", "tools.engines.dead_code_detector", dead_code, atlas, commit)
    _write(tmp_path, "clone_detector", "tools.engines.clone_detector", clones, atlas, commit, {"genome": genome})
    _write(tmp_path, "signals", "tools.engines.quant_engine", signals, atlas, commit)
    _write(tmp_path, "circular_deps", "tools.engines.circular_dependency_finder", circular, atlas, commit)
    _write(
        tmp_path,
        "live_surface_priority_pack",
        "tools.engines.live_surface_analyzer",
        priority,
        atlas,
        commit,
        {"dead_code": dead_code, "clone_detector": clones},
    )
    evidence, usable = evaluate_surgical_packet_inputs(
        raw_dir=tmp_path,
        expected_snapshot_id=commit["snapshot_id"],
        payloads={"signals": signals, "circular_deps": circular, "live_surface_priority_pack": priority},
    )
    assert evidence["status"] == "PASS", evidence
    assert set(usable) == {"signals", "circular_deps", "live_surface_priority_pack"}


def test_input_contract_is_registry_and_lineage_owned() -> None:
    input_contract = json.loads((ROOT / "config" / "surgical_packet_input_contract.json").read_text(encoding="utf-8"))
    artifact_registry = json.loads((ROOT / "config" / "artifact_registry.json").read_text(encoding="utf-8"))
    lineage_contract = json.loads((ROOT / "config" / "analysis_snapshot_lineage_contract.json").read_text(encoding="utf-8"))

    artifacts = {row["id"]: row for row in artifact_registry["artifacts"]}
    lineage = lineage_contract["artifacts"]
    for artifact_id in input_contract["inputs"]:
        assert artifact_id in artifacts
        assert artifact_id in lineage
        receipt_id = lineage[artifact_id]["receipt_artifact_id"]
        assert receipt_id in artifacts
        assert lineage[artifact_id]["producer"].startswith("tools.engines.")
        assert artifacts[receipt_id]["schema"] == "config/schemas/analysis_snapshot_lineage.schema.json"


def test_clone_detector_writes_lineage_for_exact_output_and_genome_dependency() -> None:
    genome = {
        "Symbol": [
            {
                "project": "FIXTURE",
                "file": "src/a.ts",
                "name": "first",
                "source_lines": "L1-L20",
                "dna": "shared-dna",
                "type": "function",
            },
            {
                "project": "FIXTURE",
                "file": "src/b.ts",
                "name": "second",
                "source_lines": "L1-L20",
                "dna": "shared-dna",
                "type": "function",
            },
        ]
    }
    atlas = {"MAIN": {"files": {}}}
    with (
        patch.object(clone_detector, "load_genome_data", return_value=genome),
        patch.object(clone_detector, "load_atlas_data", return_value=atlas),
        patch.object(clone_detector, "save_json_atomic"),
        patch.object(clone_detector, "save_text_atomic"),
        patch.object(clone_detector, "write_current_atlas_lineage") as lineage,
    ):
        assert clone_detector.run_clone_detector() is True

    assert all("_lines_count" not in block and "_symbol_id" not in block for block in genome["Symbol"])
    assert lineage.call_args.kwargs["artifact_id"] == "clone_detector"
    assert lineage.call_args.kwargs["atlas"] is atlas
    assert lineage.call_args.kwargs["dependency_payloads"] == {"genome": genome}
    assert lineage.call_args.kwargs["artifact_payload"]["clusters"][0]["lines"] == 20


def test_live_surface_writes_lineage_for_exact_priority_dependencies() -> None:
    atlas = {"MAIN": {"files": {}}}
    dead_code = {"items": []}
    clones = {"clusters": []}

    def _load(path: Path, default: dict) -> dict:
        return {"dead_code.json": dead_code, "clone_detector.json": clones}.get(Path(path).name, default)

    with (
        patch.object(live_surface_analyzer, "load_atlas_data", return_value=atlas),
        patch.object(live_surface_analyzer, "load_json_file", side_effect=_load),
        patch.object(live_surface_analyzer, "_build_broken_live_findings", return_value=[]),
        patch.object(live_surface_analyzer, "_build_duplicate_live_findings", return_value=[]),
        patch.object(live_surface_analyzer, "ensure_valid_payload"),
        patch.object(live_surface_analyzer, "save_json_atomic"),
        patch.object(live_surface_analyzer, "_write_markdown"),
        patch.object(live_surface_analyzer, "_write_priority_pack_md"),
        patch.object(live_surface_analyzer, "write_current_atlas_lineage") as lineage,
    ):
        live_surface_analyzer.run_live_surface_analyzer()

    assert lineage.call_args.kwargs["artifact_id"] == "live_surface_priority_pack"
    assert lineage.call_args.kwargs["atlas"] is atlas
    assert lineage.call_args.kwargs["dependency_payloads"] == {
        "dead_code": dead_code,
        "clone_detector": clones,
    }

import json
from pathlib import Path
from unittest.mock import patch

from tools.core.contextos_mcp import render_active_signals
from tools.core.scoped_graph_projection import (
    build_scoped_graph_advisories,
    strongly_connected_components,
)
from tools.engines.quant_engine import (
    build_atlas_reverse_dependency_graph,
    get_transitive_dependents,
)


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]


def test_atlas_projection_uses_global_graph_for_scoped_focus() -> None:
    atlas = {
        "MAIN": {
            "files": {
                "src/a.ts": {},
                "src/b.ts": {},
                "src/c.ts": {},
            },
            "dependencies": {
                "src/a.ts": ["src/b.ts"],
                "src/b.ts": ["src/c.ts"],
                "src/c.ts": [],
            },
        }
    }

    reverse, nodes = build_atlas_reverse_dependency_graph(atlas)

    assert reverse["MAIN::src/c.ts"] == {"MAIN::src/b.ts"}
    assert get_transitive_dependents("MAIN::src/c.ts", reverse) == {
        "MAIN::src/a.ts",
        "MAIN::src/b.ts",
    }
    assert len(nodes) == 3


def test_transitive_dependency_walk_does_not_return_the_origin_in_a_cycle() -> None:
    reverse = {
        "MAIN::a.ts": {"MAIN::b.ts"},
        "MAIN::b.ts": {"MAIN::a.ts"},
    }

    assert get_transitive_dependents("MAIN::a.ts", reverse) == {"MAIN::b.ts"}


def test_scoped_graph_advisory_preserves_claim_boundaries() -> None:
    atlas = {
        "MAIN": {
            "files": {
                "src/a.ts": {
                    "exports": ["unusedExport"],
                    "import_records": [],
                    "symbols": [
                        {
                            "name": "unusedExport",
                            "type": "Function",
                            "exported": True,
                            "line": 3,
                            "end_line": 5,
                            "features": [],
                        }
                    ],
                },
                "src/b.ts": {"exports": [], "import_records": [], "symbols": []},
                "src/c.ts": {
                    "target_ref": "MAIN::src/c.ts",
                    "exports": ["unusedExport"],
                    "import_records": [],
                    "symbols": [
                        {
                            "name": "unusedExport",
                            "type": "Function",
                            "exported": True,
                            "line": 3,
                            "end_line": 5,
                            "features": [],
                        }
                    ],
                },
            },
            "dependencies": {
                "src/a.ts": ["src/b.ts"],
                "src/b.ts": ["src/a.ts"],
                "src/c.ts": [],
            },
        }
    }

    with patch(
        "tools.core.scoped_graph_projection.require_doctrine_mapping",
        return_value={
            "formula_weights": {
                "direct_dependent": 1.0,
                "transitive_dependent": 0.5,
            }
        },
    ):
        payload = build_scoped_graph_advisories(
            atlas,
            ["MAIN::src/a.ts", "MAIN::src/c.ts"],
        )

    rows = {row["node_key"]: row for row in payload["advisories"]}
    row = rows["MAIN::src/a.ts"]
    assert row["cycle_membership"]["status"] == "member"
    assert row["cycle_membership"]["component_size"] == 2
    assert row["cycle_membership"]["witness_chain"] == [
        "MAIN::src/a.ts",
        "MAIN::src/b.ts",
        "MAIN::src/a.ts",
    ]
    assert row["cycle_membership"]["witness_edges"] == [
        {"source": "MAIN::src/a.ts", "target": "MAIN::src/b.ts"},
        {"source": "MAIN::src/b.ts", "target": "MAIN::src/a.ts"},
    ]
    assert row["impact_advisory"]["status"] == "available_dependency_only_lower_bound"
    assert row["impact_advisory"]["boundary_bonus_status"] == "deferred_to_full_blast_radius"
    assert row["dead_code_advisory"]["candidate_count"] == 0
    leaf_row = rows["MAIN::src/c.ts"]
    assert leaf_row["target_ref"] == "MAIN::src/c.ts"
    assert leaf_row["dead_code_advisory"]["candidate_count"] == 1
    assert (
        leaf_row["dead_code_advisory"]["claim_boundary"]
        == "candidate_only_not_dead_code_proof_or_absence_claim"
    )
    assert payload["summary"]["full_repository_claim"] is False
    assert payload["summary"]["canonical_artifacts_overwritten"] is False


def test_scoped_scc_does_not_turn_acyclic_cross_edges_into_cycles() -> None:
    graph = {
        "MAIN::a.ts": {"MAIN::b.ts", "MAIN::c.ts"},
        "MAIN::b.ts": {"MAIN::c.ts"},
        "MAIN::c.ts": set(),
    }

    assert strongly_connected_components(graph) == []


def test_scoped_graph_scc_traversal_is_not_limited_by_python_recursion_depth() -> None:
    graph = {
        f"MAIN::src/node_{index}.ts": {f"MAIN::src/node_{index + 1}.ts"}
        for index in range(1500)
    }
    graph["MAIN::src/node_1500.ts"] = set()
    graph["MAIN::src/self.ts"] = {"MAIN::src/self.ts"}

    assert strongly_connected_components(graph) == [["MAIN::src/self.ts"]]


def test_agent_renderer_presents_inspectable_scc_cycle_witness() -> None:
    signals = {
        "meta": {
            "source_mode": "direct_pipeline_scope",
            "current_change_scope": "bounded",
            "current_turn_claim": "supported",
            "signal_origin": "direct_pipeline_scope",
        },
        "summary": {},
        "active_signals": [
            {
                "node_key": "MAIN::src/a.ts",
                "relative_path": "src/a.ts",
                "target_ref": "MAIN::src/a.ts",
                "signal_kind": "source",
                "impact_score": 2.0,
                "impact_score_status": "scoped_dependency_lower_bound",
                "direct_dependents": [],
                "transitive_dependents": [],
                "focus_halo": [],
                "active_violations": [],
                "circular_cycles": [
                    ["MAIN::src/a.ts", "MAIN::src/b.ts", "MAIN::src/a.ts"]
                ],
                "circular_cycles_status": "scoped_scc_membership",
                "risk_claim_boundary": (
                    "scoped_dependency_scc_witness_and_active_violation_context"
                ),
            }
        ],
    }

    rendered = render_active_signals(
        signals,
        output_format="markdown",
        include_bodies=False,
        max_files=8,
        max_chars_per_file=0,
        scope="full",
        resolve_absolute_path=lambda rel_path, project_key: CODE_MAPS_DIR / rel_path,
    )

    assert "Scoped Strongly Connected Component Cycle Witness" in rendered
    assert "MAIN::src/a.ts -> MAIN::src/b.ts" in rendered
    assert "not a full cycle enumeration" in rendered


def test_agent_renderer_does_not_promote_persisted_broad_signals_to_current_turn() -> None:
    signals = {
        "meta": {
            "source_mode": "watchdog_primary",
            "current_change_scope": "unknown",
            "current_turn_claim": "not_established",
            "signal_origin": "watchdog_session.json",
        },
        "summary": {},
        "active_signals": [
            {
                "node_key": "MAIN::src/previous.ts",
                "relative_path": "src/previous.ts",
                "target_ref": "MAIN::src/previous.ts",
                "signal_kind": "source",
                "direct_dependents": [],
                "transitive_dependents": [],
                "focus_halo": [],
                "active_violations": [],
                "circular_cycles": [],
            }
        ],
    }

    rendered = render_active_signals(
        signals,
        output_format="markdown",
        include_bodies=False,
        max_files=8,
        max_chars_per_file=0,
        scope="full",
        resolve_absolute_path=lambda rel_path, project_key: CODE_MAPS_DIR / rel_path,
    )
    projected = json.loads(
        render_active_signals(
            signals,
            output_format="json",
            include_bodies=False,
            max_files=8,
            max_chars_per_file=0,
            scope="summary",
            resolve_absolute_path=lambda rel_path, project_key: CODE_MAPS_DIR / rel_path,
        )
    )

    assert "current AI-turn change scope is unknown" in rendered
    assert "Persisted Orientation Candidates" in rendered
    assert "files you are currently modifying" not in rendered
    assert projected["current_change_scope"] == "unknown"
    assert projected["current_turn_claim"] == "not_established"


def test_agent_renderer_preserves_direct_scope_current_turn_claim() -> None:
    signals = {
        "meta": {
            "source_mode": "direct_pipeline_scope",
            "current_change_scope": "bounded",
            "current_turn_claim": "supported",
            "signal_origin": "direct_pipeline_scope",
        },
        "summary": {},
        "active_signals": [
            {
                "node_key": "MAIN::src/current.ts",
                "relative_path": "src/current.ts",
                "target_ref": "MAIN::src/current.ts",
                "signal_kind": "source",
                "direct_dependents": [],
                "transitive_dependents": [],
                "focus_halo": [],
                "active_violations": [],
                "circular_cycles": [],
            }
        ],
    }

    rendered = render_active_signals(
        signals,
        output_format="markdown",
        include_bodies=False,
        max_files=8,
        max_chars_per_file=0,
        scope="full",
        resolve_absolute_path=lambda rel_path, project_key: CODE_MAPS_DIR / rel_path,
    )

    assert "bounded focus window for the current change scope" in rendered
    assert "L1 Focus (Bounded Changed Files)" in rendered
    assert "current AI-turn change scope is unknown" not in rendered


def test_empty_persisted_projection_does_not_claim_current_scope() -> None:
    rendered = render_active_signals(
        {
            "meta": {
                "source_mode": "watchdog_primary",
                "current_change_scope": "unknown",
                "current_turn_claim": "not_established",
                "signal_origin": "watchdog_session.json",
            },
            "summary": {},
            "active_signals": [],
        },
        output_format="markdown",
        include_bodies=False,
        max_files=8,
        max_chars_per_file=0,
        scope="summary",
        resolve_absolute_path=lambda rel_path, project_key: CODE_MAPS_DIR / rel_path,
    )

    assert "latest persisted ContextOS projection" in rendered
    assert "current-turn scope is unknown" in rendered
    assert "No current ContextOS signal" not in rendered

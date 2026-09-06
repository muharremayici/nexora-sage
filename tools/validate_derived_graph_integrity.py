from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.atlas_integrity import payload_sha256, validate_atlas_commit
from tools.core.atlas_io import load_atlas_data
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.engines.state_flow_scanner import _build_genome_file_index, build_state_flow_results
from tools.engines.state_data_graph_analyzer import _build_possible_invalidation_edges, _key_tokens


RAW_OUTPUT_PATH = RAW_DIR / "derived_graph_integrity_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "derived_graph_integrity_validation.md"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _project_keys(atlas: dict[str, Any]) -> set[str]:
    return {
        key
        for key, value in atlas.items()
        if key != "symbols" and isinstance(value, dict)
    }


def _atlas_nodes(atlas: dict[str, Any]) -> set[str]:
    nodes: set[str] = set()
    for project, project_data in atlas.items():
        if project == "symbols" or not isinstance(project_data, dict):
            continue
        for rel_path in (project_data.get("files", {}) or {}).keys():
            nodes.add(f"{project}::{rel_path}")
    return nodes


def _reachable(start: str, reverse_adjacency: dict[str, list[str]]) -> set[str]:
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        queue.extend(reverse_adjacency.get(current, []))
    return visited - {start}


def _blast_checks(atlas: dict[str, Any], circular: dict[str, Any], blast: dict[str, Any]) -> list[dict[str, Any]]:
    atlas_nodes = _atlas_nodes(atlas)
    circular_nodes_payload = circular.get("nodes", {}) if isinstance(circular, dict) else {}
    circular_nodes = set(circular_nodes_payload.keys()) if isinstance(circular_nodes_payload, dict) else set(circular_nodes_payload or [])
    edges = circular.get("edges", []) if isinstance(circular, dict) else []
    reverse_adjacency: dict[str, list[str]] = {node: [] for node in circular_nodes}
    malformed_edges: list[Any] = []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
            malformed_edges.append(edge)
            continue
        source = str(edge["source"])
        target = str(edge["target"])
        if source in reverse_adjacency and target in reverse_adjacency:
            reverse_adjacency[target].append(source)

    rows = blast.get("blast_radius", []) if isinstance(blast, dict) else []
    rows = rows if isinstance(rows, list) else []
    row_by_file = {
        str(row.get("file")): row
        for row in rows
        if isinstance(row, dict) and row.get("file")
    }
    count_mismatches: list[dict[str, Any]] = []
    for node in circular_nodes:
        row = row_by_file.get(node)
        if not row:
            continue
        expected_direct = len(reverse_adjacency.get(node, []))
        expected_transitive = len(_reachable(node, reverse_adjacency))
        if int(row.get("direct_dependents", -1)) != expected_direct or int(row.get("transitive_dependents", -1)) != expected_transitive:
            count_mismatches.append(
                {
                    "file": node,
                    "expected_direct": expected_direct,
                    "actual_direct": row.get("direct_dependents"),
                    "expected_transitive": expected_transitive,
                    "actual_transitive": row.get("transitive_dependents"),
                }
            )

    metrics = blast.get("metrics", {}) if isinstance(blast, dict) else {}
    first_row = rows[0] if rows and isinstance(rows[0], dict) else {}
    blast_projects = set((blast.get("by_project", {}) or {}).keys()) if isinstance(blast, dict) else set()
    atlas_projects = _project_keys(atlas)
    return [
        _check("circular_graph_edges_well_formed", not malformed_edges, malformed_edges[:20]),
        _check("circular_graph_nodes_belong_to_atlas", circular_nodes.issubset(atlas_nodes), {"unknown": sorted(circular_nodes - atlas_nodes)[:50]}),
        _check("blast_rows_cover_circular_graph", set(row_by_file) == circular_nodes, {"missing": sorted(circular_nodes - set(row_by_file))[:50], "extra": sorted(set(row_by_file) - circular_nodes)[:50]}),
        _check("blast_dependency_counts_recomputed", not count_mismatches, count_mismatches[:50]),
        _check("blast_metrics_match_top_row", metrics.get("most_critical_node") == first_row.get("file") and float(metrics.get("highest_impact_score", 0) or 0) == float(first_row.get("total_impact_score", 0) or 0), {"metrics": metrics, "top_row": first_row.get("file")}),
        _check("blast_project_projection_known", blast_projects.issubset(atlas_projects), {"unknown": sorted(blast_projects - atlas_projects)}),
    ]


def _state_flow_checks(
    atlas: dict[str, Any],
    state_flow: dict[str, Any],
    genome_index: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    atlas_projects = _project_keys(atlas)
    projected_projects = set((state_flow.get("by_project", {}) or {}).keys()) if isinstance(state_flow, dict) else set()
    proof_projects = set((state_flow.get("state_proof_by_project", {}) or {}).keys()) if isinstance(state_flow, dict) else set()
    run_meta = state_flow.get("run_meta", {}) if isinstance(state_flow, dict) else {}
    execution_scope = run_meta.get("execution_scope", {}) if isinstance(run_meta, dict) else {}
    declared_projects = execution_scope.get("analyzed_projects", []) if isinstance(execution_scope, dict) else []
    producer_scope = {
        str(project).strip()
        for project in declared_projects
        if str(project).strip()
    }
    scoped_atlas = {
        project: payload
        for project, payload in atlas.items()
        if project in producer_scope
    }
    expected = build_state_flow_results(
        scoped_atlas,
        _build_genome_file_index() if genome_index is None else genome_index,
    )
    compared_fields = [
        "zustand_stores",
        "zustand_consumers",
        "tanstack_queries",
        "tanstack_mutations",
        "boundary_signals",
        "transitive_hook_consumers",
        "by_project",
        "state_proof",
        "state_proof_by_project",
    ]
    mismatches = [field for field in compared_fields if state_flow.get(field) != expected.get(field)]
    atlas_nodes = _atlas_nodes(atlas)
    referenced_nodes: set[str] = set()
    for field in ("zustand_stores", "zustand_consumers", "tanstack_queries", "tanstack_mutations", "boundary_signals", "transitive_hook_consumers"):
        payload = state_flow.get(field, {}) if isinstance(state_flow, dict) else {}
        if isinstance(payload, dict):
            referenced_nodes.update(str(key) for key in payload.keys())
    return [
        _check("state_flow_references_atlas_nodes", referenced_nodes.issubset(atlas_nodes), {"unknown": sorted(referenced_nodes - atlas_nodes)[:50]}),
        _check("state_flow_execution_scope_declared", isinstance(declared_projects, list), {"declared_projects": declared_projects}),
        _check("state_flow_project_scope_nonempty", bool(producer_scope), {"producer_scope": sorted(producer_scope)}),
        _check("state_flow_project_scope_known", producer_scope.issubset(atlas_projects), {"unknown": sorted(producer_scope - atlas_projects)}),
        _check("state_flow_project_projection_complete", projected_projects == producer_scope, {"expected": sorted(producer_scope), "actual": sorted(projected_projects)}),
        _check("state_flow_project_proof_complete", proof_projects == producer_scope, {"expected": sorted(producer_scope), "actual": sorted(proof_projects)}),
        _check("state_flow_recomputes_from_current_atlas", not mismatches, {"mismatched_fields": mismatches}),
    ]


def _state_data_graph_checks(
    atlas: dict[str, Any],
    commit: dict[str, Any],
    state_flow: dict[str, Any],
    graph: dict[str, Any],
) -> list[dict[str, Any]]:
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    summary = graph.get("summary", {}) if isinstance(graph, dict) else {}
    meta = graph.get("meta", {}) if isinstance(graph, dict) else {}
    node_by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    atlas_nodes = _atlas_nodes(atlas)
    unknown_files: set[str] = set()
    for node in node_by_id.values():
        for source_file in node.get("files", []) or []:
            source_ref = str(source_file)
            if "::" in source_ref and source_ref not in atlas_nodes:
                unknown_files.add(source_ref)

    malformed_edges: list[dict[str, Any]] = []
    actual_edge_pairs: set[tuple[str, str]] = set()
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            malformed_edges.append({"edge": edge})
            continue
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        evidence = edge.get("evidence", {})
        source_nodes = evidence.get("source_nodes", []) if isinstance(evidence, dict) else []
        relation_basis = evidence.get("relation_basis", []) if isinstance(evidence, dict) else []
        source_files = evidence.get("source_files", []) if isinstance(evidence, dict) else []
        matched_key_pairs = evidence.get("matched_key_pairs", []) if isinstance(evidence, dict) else []
        expected_source_files = sorted(
            {
                str(item)
                for node_id in (source, target)
                for item in (node_by_id.get(node_id, {}).get("files", []) or [])
            }
        )
        if (
            source not in node_by_id
            or target not in node_by_id
            or source_nodes != [source, target]
            or not relation_basis
            or source_files != expected_source_files
            or not matched_key_pairs
        ):
            malformed_edges.append(
                {
                    "from": source,
                    "to": target,
                    "source_nodes": source_nodes,
                    "relation_basis": relation_basis,
                    "source_files": source_files,
                    "expected_source_files": expected_source_files,
                    "matched_key_pairs": matched_key_pairs,
                }
            )
        actual_edge_pairs.add((source, target))

    queries = state_flow.get("tanstack_queries", {}) if isinstance(state_flow, dict) else {}
    mutations = state_flow.get("tanstack_mutations", {}) if isinstance(state_flow, dict) else {}
    boundary_signals = state_flow.get("boundary_signals", {}) if isinstance(state_flow, dict) else {}
    expected_edges = _build_possible_invalidation_edges(queries, mutations, boundary_signals)
    expected_edge_by_pair = {
        (str(edge.get("from")), str(edge.get("to"))): edge
        for edge in expected_edges
        if isinstance(edge, dict)
    }
    expected_edge_pairs: set[tuple[str, str]] = set()
    for mutation, mutation_keys in (mutations or {}).items():
        for query, query_keys in (queries or {}).items():
            related = False
            for mutation_key in mutation_keys or []:
                for query_key in query_keys or []:
                    mutation_text = str(mutation_key).strip().lower()
                    query_text = str(query_key).strip().lower()
                    if (
                        query_text
                        and mutation_text
                        and (
                            query_text in mutation_text
                            or mutation_text in query_text
                            or (_key_tokens(query_text) & _key_tokens(mutation_text))
                        )
                    ):
                        related = True
                        break
                if related:
                    break
            if related:
                expected_edge_pairs.add((f"mutation:{mutation}", f"query:{query}"))
    evidence_mismatches = []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        pair = (str(edge.get("from") or ""), str(edge.get("to") or ""))
        expected_edge = expected_edge_by_pair.get(pair)
        if expected_edge and (
            edge.get("shared_tokens") != expected_edge.get("shared_tokens")
            or edge.get("evidence") != expected_edge.get("evidence")
            or edge.get("has_client_invalidation_action")
            != expected_edge.get("has_client_invalidation_action")
        ):
            evidence_mismatches.append(
                {
                    "from": pair[0],
                    "to": pair[1],
                    "expected": expected_edge,
                    "actual": edge,
                }
            )

    by_kind = defaultdict(int)
    by_project = defaultdict(int)
    for node in node_by_id.values():
        by_kind[str(node.get("kind") or "")] += 1
        by_project[str(node.get("project") or "")] += 1
    summary_matches = (
        summary.get("nodes") == len(nodes)
        and summary.get("edges") == len(edges)
        and summary.get("by_kind") == dict(by_kind)
        and summary.get("by_project") == dict(sorted(by_project.items()))
    )
    policy = load_json_object_strict(
        ROOT / "config" / "state_data_graph_policy.json",
        label="State data graph policy",
    )
    source_identity_matches = (
        meta.get("version") == "v2"
        and meta.get("atlas_snapshot_id") == commit.get("snapshot_id")
        and meta.get("state_flow_sha256") == payload_sha256(state_flow)
        and meta.get("policy_sha256") == payload_sha256(policy)
    )
    return [
        _check("state_data_graph_source_identity_current", source_identity_matches, meta),
        _check("state_data_graph_node_files_belong_to_atlas", not unknown_files, {"unknown": sorted(unknown_files)[:50]}),
        _check("state_data_graph_edges_have_endpoint_provenance", not malformed_edges, malformed_edges[:50]),
        _check(
            "state_data_graph_edges_recompute_from_state_flow",
            actual_edge_pairs == expected_edge_pairs,
            {
                "missing": sorted(expected_edge_pairs - actual_edge_pairs)[:50],
                "extra": sorted(actual_edge_pairs - expected_edge_pairs)[:50],
            },
        ),
        _check(
            "state_data_graph_edge_evidence_recomputes_from_semantic_keys",
            not evidence_mismatches,
            evidence_mismatches[:20],
        ),
        _check(
            "state_data_graph_summary_matches_payload",
            summary_matches,
            {
                "expected_nodes": len(nodes),
                "expected_edges": len(edges),
                "expected_by_kind": dict(by_kind),
                "expected_by_project": dict(sorted(by_project.items())),
            },
        ),
    ]


def run_validation() -> dict[str, Any]:
    atlas = load_atlas_data()
    commit = load_json_file(RAW_DIR / "atlas_commit.json", {})
    circular = load_json_file(RAW_DIR / "circular_deps.json", {})
    blast = load_json_file(RAW_DIR / "blast_radius.json", {})
    state_flow = load_json_file(RAW_DIR / "state_flow.json", {})
    state_data_graph = load_json_file(RAW_DIR / "state_data_graph.json", {})

    checks: list[dict[str, Any]] = []
    checks.extend(
        _check(item["name"], item["passed"], {"expected": item["expected"], "actual": item["actual"]})
        for item in validate_atlas_commit(atlas, commit)
    )
    checks.extend(_blast_checks(atlas, circular, blast))
    checks.extend(_state_flow_checks(atlas, state_flow))
    checks.extend(_state_data_graph_checks(atlas, commit, state_flow, state_data_graph))
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {
            "kind": "derived_graph_integrity_validation",
            "version": "v1",
            "atlas_snapshot_id": commit.get("snapshot_id") if isinstance(commit, dict) else None,
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    lines = [
        "# Derived Graph Integrity Validation",
        "",
        f"- status: `{payload['summary']['status']}`",
        f"- checks: `{payload['summary']['passed_checks']}/{payload['summary']['total_checks']}`",
        f"- atlas_snapshot_id: `{payload['meta']['atlas_snapshot_id']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True).replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | `{details}` |")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
Blast Radius / Impact Analysis Engine
Calculates an impact score for every file based on how many other files depend on it.
Identifies the most critical components in the architecture.
"""

import json
from collections import defaultdict, deque

from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_strict
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import canonical_project_name, project_display_name


def _atlas_dependency_coverage(atlas=None):
    atlas = atlas if atlas is not None else project_runtime_atlas(load_atlas_data())[0]
    projects = {}
    aggregate = {"observed": 0, "degraded": 0, "unavailable": 0}
    warning_count = 0

    for project, pdata in (atlas or {}).items():
        coverage = ((pdata.get("project") or {}).get("sequencer_evidence") or {}).get("coverage") or {}
        project_counts = {"observed": 0, "degraded": 0, "unavailable": 0}
        claim_statuses = []
        for detail in coverage.get("details", []) or []:
            counts = detail.get("status_counts") or {}
            for status in project_counts:
                value = counts.get(status, 0)
                if isinstance(value, int):
                    project_counts[status] += value
                    aggregate[status] += value
            claim_status = detail.get("claim_status")
            if isinstance(claim_status, str) and claim_status:
                claim_statuses.append(claim_status)
        project_warning_count = len(coverage.get("warnings", []) or [])
        warning_count += project_warning_count
        projects[project] = {
            "claim_statuses": sorted(set(claim_statuses)),
            "status_counts": project_counts,
            "warning_count": project_warning_count,
        }

    non_observed = aggregate["degraded"] + aggregate["unavailable"]
    if aggregate["observed"] == 0 and aggregate["unavailable"] > 0:
        status = "unavailable"
    elif non_observed > 0:
        status = "degraded"
    elif aggregate["observed"] > 0:
        status = "observed"
    else:
        status = "not_reported"

    return {
        "status": status,
        "status_counts": aggregate,
        "warning_count": warning_count,
        "projects": projects,
        "claim_boundary": (
            "Blast Radius counts are relative to dependency edges materialized in the current Atlas. "
            "Degraded, unavailable or unreported sequencer coverage forbids interpreting the graph as "
            "a complete repository-wide impact proof."
        ),
    }


def _build_boundary_file_index():
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    index = {}
    if not atlas:
        return index

    for project, pdata in atlas.items():
        for rel, f_data in (pdata.get("files", {}) or {}).items():
            imported_contracts = set()
            member_imported_contracts = set()
            member_dependencies = set()
            member_side_effect_markers = set()
            member_side_effect_imports = set()
            member_side_effect_calls = set()
            ui_dependencies = set()
            architectural_markers = set()
            dynamic_imports = set()
            for sym in f_data.get("symbols", []) or []:
                if not isinstance(sym, dict):
                    continue
                for item in sym.get("dependency_imports", []) or []:
                    if isinstance(item, dict) and item.get("localName") and item.get("source"):
                        imported_contracts.add(f"{item['localName']}->{item['source']}")
                    elif item:
                        imported_contracts.add(str(item))
                for member in sym.get("member_details", []) or []:
                    if not isinstance(member, dict):
                        continue
                    for item in member.get("dependencies", []) or []:
                        if item:
                            member_dependencies.add(str(item))
                    for item in member.get("dependency_imports", []) or member.get("dependencyImports", []) or []:
                        if isinstance(item, dict) and item.get("localName") and item.get("source"):
                            member_imported_contracts.add(f"{item['localName']}->{item['source']}")
                        elif item:
                            member_imported_contracts.add(str(item))
                    for item in member.get("side_effect_markers", []) or member.get("sideEffectMarkers", []) or []:
                        if item:
                            member_side_effect_markers.add(str(item))
                    for item in member.get("side_effect_imports", []) or member.get("sideEffectImports", []) or []:
                        if isinstance(item, dict) and item.get("localName") and item.get("source"):
                            member_side_effect_imports.add(f"{item['localName']}->{item['source']}")
                        elif item:
                            member_side_effect_imports.add(str(item))
                    for item in member.get("side_effect_calls", []) or member.get("sideEffectCalls", []) or []:
                        if isinstance(item, dict):
                            marker = str(item.get("marker") or "").strip()
                            expression = str(item.get("expression") or "").strip()
                            trigger_kind = str(item.get("triggerKind") or "").strip()
                            local_name = str(item.get("localName") or "").strip()
                            source = str(item.get("source") or "").strip()
                            if marker and expression:
                                descriptor = f"{marker}:{expression}"
                                if trigger_kind:
                                    descriptor = f"{descriptor} [{trigger_kind}]"
                                if local_name and source:
                                    descriptor = f"{descriptor} -> {local_name}->{source}"
                                member_side_effect_calls.add(descriptor)
                        elif item:
                            member_side_effect_calls.add(str(item))
                ui_dependencies.update(sym.get("ui_dependencies", []) or [])
                architectural_markers.update(sym.get("architectural_markers", []) or [])
                dynamic_imports.update(sym.get("dynamic_imports", []) or [])

            keys = {
                f"{project}::{rel}",
                "{}::{}".format(project, str(f_data.get("workspace_rel") or "").replace("\\", "/").strip("/")),
            }
            for key in list(keys):
                if key.endswith("::"):
                    keys.discard(key)
            for key in keys:
                index[key] = {
                    "imported_contracts": {str(item) for item in imported_contracts},
                    "member_imported_contracts": {str(item) for item in member_imported_contracts},
                    "member_dependencies": {str(item) for item in member_dependencies},
                    "member_side_effect_markers": {str(item) for item in member_side_effect_markers},
                    "member_side_effect_imports": {str(item) for item in member_side_effect_imports},
                    "member_side_effect_calls": {str(item) for item in member_side_effect_calls},
                    "ui_dependencies": {str(item) for item in ui_dependencies},
                    "architectural_markers": {str(item) for item in architectural_markers},
                    "dynamic_imports": {str(item) for item in dynamic_imports},
                }
    return index


def _split_project_and_file(node: str):
    if "::" not in node:
        return "UNKNOWN", node
    project, rel_path = node.split("::", 1)
    return canonical_project_name(project), rel_path


def _summarize_by_project(sorted_nodes):
    by_project = defaultdict(
        lambda: {
            "file_count": 0,
            "nonzero_files": 0,
            "max_total_impact_score": 0.0,
            "top_file": None,
            "top_score": 0.0,
        }
    )

    for node, score in sorted_nodes:
        project, _ = _split_project_and_file(node)
        bucket = by_project[project]
        bucket["file_count"] += 1
        total_score = float(score.get("total_impact_score", 0) or 0)
        if total_score > 0:
            bucket["nonzero_files"] += 1
        if total_score >= bucket["top_score"]:
            bucket["top_score"] = round(total_score, 2)
            bucket["top_file"] = node
            bucket["max_total_impact_score"] = round(total_score, 2)

    return dict(
        sorted(
            by_project.items(),
            key=lambda item: (-item[1]["max_total_impact_score"], item[0]),
        )
    )


def _get_impact_graph():
    deps_path = RAW_DIR / "circular_deps.json"
    data = load_json_file(deps_path, {})
    nodes = data.get("nodes", {})
    edges = data.get("edges", [])
    if not nodes and not edges:
        return None, None, None
    boundary_index = _build_boundary_file_index()

    rev_adj = {node: [] for node in nodes}
    for edge in edges:
        source = edge["source"]
        target = edge["target"]
        if target in rev_adj and source in rev_adj:
            rev_adj[target].append(source)
    
    return nodes, rev_adj, boundary_index

def get_reachable(start_node, rev_adj, memo=None):
    if memo is not None and start_node in memo:
        return set(memo[start_node])

    visited = set()
    queue = deque([start_node])
    while queue:
        current = queue.popleft()
        if current not in visited:
            visited.add(current)
            queue.extend(rev_adj.get(current, []))

    reachable = visited - {start_node}
    if memo is not None:
        memo[start_node] = set(reachable)
    return reachable

def simulate_impact(target_node):
    """Simulates the impact of changing or removing a specific node."""
    nodes, rev_adj, boundary_index = _get_impact_graph()
    if not nodes or target_node not in nodes:
        logger.error(f"[FAIL] Target node '{target_node}' not found in graph.")
        return None

    reachable = get_reachable(target_node, rev_adj, {})
    direct_dependents = rev_adj.get(target_node, [])
    
    signals = boundary_index.get(target_node) or {}
    
    impact = {
        "target": target_node,
        "blast_radius_size": len(reachable),
        "direct_dependents_count": len(direct_dependents),
        "direct_dependents": sorted(direct_dependents),
        "transitive_dependents": sorted(list(reachable)),
        "at_risk_contracts": sorted(list(signals.get("imported_contracts", []))),
        "at_risk_ui_components": sorted(list(signals.get("ui_dependencies", []))),
    }
    
    # Save simulation result
    sim_path = RAW_DIR / "last_simulation.json"
    save_json_atomic(sim_path, impact)
    
    print(json.dumps(impact, indent=2))
    logger.info(f"[SIM] Blast Radius for {target_node}: {len(reachable)} files affected.")
    return impact

def run_blast_radius():
    from tools.core.doctrine_contract import require_doctrine_mapping
    logger.info("Running Blast Radius (Impact Analysis) Engine...")
    
    tuning = require_doctrine_mapping("impact_analysis_tuning")
    formula_weights = tuning.get("formula_weights", {"direct_dependent": 1.0, "transitive_dependent": 0.5})
    bonuses_config = tuning.get("boundary_bonuses", [])

    nodes, rev_adj, boundary_index = _get_impact_graph()
    if not nodes:
        logger.error("[FAIL] circular_deps.json not found! Cannot calculate blast radius.")
        return False

    impact_scores = {}
    reachable_memo = {}

    for node in nodes:
        reachable = get_reachable(node, rev_adj, reachable_memo)
        signals = boundary_index.get(node) or {
            "imported_contracts": set(),
            "member_imported_contracts": set(),
            "member_dependencies": set(),
            "member_side_effect_markers": set(),
            "member_side_effect_imports": set(),
            "member_side_effect_calls": set(),
            "ui_dependencies": set(),
            "architectural_markers": set(),
            "dynamic_imports": set(),
        }
        
        boundary_bonus = 0.0
        # Doctrine-driven boundary bonuses
        for bonus in bonuses_config:
            source_key = bonus.get("source")
            if source_key in signals and signals[source_key]:
                count = len(signals[source_key])
                calc = count * bonus.get("multiplier", 0.0)
                boundary_bonus += min(calc, bonus.get("limit", 10.0))

        direct_weight = formula_weights.get("direct_dependent", 1.0)
        transitive_weight = formula_weights.get("transitive_dependent", 0.5)
        
        impact_scores[node] = {
            "direct_dependents": len(rev_adj[node]),
            "transitive_dependents": len(reachable),
            "boundary_bonus": round(boundary_bonus, 2),
            "ui_dependencies": sorted(signals["ui_dependencies"]),
            "dynamic_imports": sorted(signals["dynamic_imports"]),
            "architectural_markers": sorted(signals["architectural_markers"]),
            "imported_contracts": sorted(signals["imported_contracts"]),
            "member_imported_contracts": sorted(signals["member_imported_contracts"]),
            "member_dependencies": sorted(signals["member_dependencies"]),
            "member_side_effect_markers": sorted(signals["member_side_effect_markers"]),
            "member_side_effect_imports": sorted(signals["member_side_effect_imports"]),
            "member_side_effect_calls": sorted(signals["member_side_effect_calls"]),
            "total_impact_score": (len(rev_adj[node]) * direct_weight) + (len(reachable) * transitive_weight) + boundary_bonus,
        }

    sorted_nodes = sorted(impact_scores.items(), key=lambda item: item[1]["total_impact_score"], reverse=True)
    by_project = _summarize_by_project(sorted_nodes)

    dependency_coverage = _atlas_dependency_coverage()
    results = {
        "metrics": {
            "most_critical_node": sorted_nodes[0][0] if sorted_nodes else None,
            "highest_impact_score": sorted_nodes[0][1]["total_impact_score"] if sorted_nodes else 0,
        },
        "input_evidence": {"atlas_dependency_coverage": dependency_coverage},
        "by_project": by_project,
        "blast_radius": [{"file": key, **value} for key, value in sorted_nodes],
    }

    raw_path = RAW_DIR / "blast_radius.json"
    save_json_atomic(raw_path, results)

    md_lines = [
        "# Blast Radius (Impact Analysis) Report",
        "",
        "> Identifies the most surgically dangerous files to modify. If you change a core object, the blast radius shows how many other files might break.",
        "",
        "## Evidence Boundary",
        "",
        f"- Atlas dependency coverage: `{dependency_coverage['status']}`",
        f"- Observed files: `{dependency_coverage['status_counts']['observed']}`",
        f"- Degraded files: `{dependency_coverage['status_counts']['degraded']}`",
        f"- Unavailable files: `{dependency_coverage['status_counts']['unavailable']}`",
        f"- Coverage warnings: `{dependency_coverage['warning_count']}`",
        f"- Claim boundary: {dependency_coverage['claim_boundary']}",
        "",
        "## By Project",
        "",
        "| Project | Files | Nonzero Impact Files | Top Score | Top File |",
        "|---|---:|---:|---:|---|",
    ]

    for project, summary in by_project.items():
        project_label = f"{project_display_name(project)} [{project}]"
        top_file = summary.get("top_file") or "-"
        md_lines.append(
            f"| `{project_label}` | {summary['file_count']} | {summary['nonzero_files']} | "
            f"{summary['max_total_impact_score']:.1f} | `{top_file}` |"
        )

    md_lines.extend([
        "",
        "## Top 50 Most Critical Files (Highest Impact)",
        "",
        "| Impact Rank | File | Direct Dependents | Transitive Formations | Boundary Bonus | Total Score |",
        "|---|---|---:|---:|---:|---:|",
    ])

    for rank, (node, score) in enumerate(sorted_nodes[:50], 1):
        md_lines.append(
            f"| #{rank} | **`{node}`** | {score['direct_dependents']} | {score['transitive_dependents']} | {score['boundary_bonus']:.1f} | {score['total_impact_score']:.1f} |"
        )

    md_path = REPORTS_DIR / "blast_radius.md"
    save_text_atomic(md_path, "\n".join(md_lines))

    logger.info(f"[OK] Blast Radius generated. Most critical file: {results['metrics']['most_critical_node']}")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Blast Radius / Impact Analysis Engine")
    parser.add_argument("--simulate", type=str, help="Simulate impact for a specific node (e.g. MAIN::src/utils/api.ts)")
    args = parser.parse_args()

    if args.simulate:
        simulate_impact(args.simulate)
    else:
        run_blast_radius()

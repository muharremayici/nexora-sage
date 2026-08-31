"""
Circular Dependency Finder - detects import cycles (A->B->C->A).
Also generates a Mermaid dependency graph for visual inspection.
"""

import json
import sys
from collections import defaultdict

from tools.core.atlas_io import load_atlas_data
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.config import ROOT, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.logger import logger
from tools.core.path_engine import expand_module_candidates, resolve_internal_import
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.runtime_project_scope import project_runtime_atlas


class CircularDependencyFinder:
    def __init__(self):
        self.projects = resolve_runtime_projects(ROOT)

    def _build_import_graph(self):
        logger.info("[PHASE 3.12] Building graph from Atlas (Memory-First)...")
        graph = defaultdict(set)
        all_files = set()
        filtered_self_edges = []

        canonical_atlas = load_atlas_data()
        if not canonical_atlas:
            logger.error("Atlas payload not found.")
            return graph, all_files, filtered_self_edges, canonical_atlas, {}
        atlas, execution_scope = project_runtime_atlas(canonical_atlas)
        if not atlas:
            logger.error("No Atlas projects matched the active runtime project scope.")
            return graph, all_files, filtered_self_edges, canonical_atlas, execution_scope

        analyzed_projects = set(execution_scope.get("analyzed_projects") or [])
        excluded_cross_project_edges = 0
        for pkey, pdata in atlas.items():
            if pkey == "symbols":
                continue

            files = pdata.get("files", {})
            all_rel_paths = set(files.keys())

            for rel_path, f_data in files.items():
                node = f"{pkey}::{rel_path}"
                all_files.add(node)

                # Using pre-resolved dependencies from the project-level map (Cache-Resilient)
                deps = pdata.get("dependencies", {}).get(rel_path, [])
                for target_rel in deps:
                    if target_rel:
                        # [Debt Fixed] Handle both local and cross-project (::) dependency nodes
                        if "::" in target_rel:
                            target_node = target_rel
                            target_project = target_node.split("::", 1)[0]
                            if target_project not in analyzed_projects:
                                excluded_cross_project_edges += 1
                                continue
                        else:
                            target_node = f"{pkey}::{target_rel}"
                            
                        if target_node == node:
                            filtered_self_edges.append({"source": node, "target": target_node, "reason": "self_dependency"})
                            continue
                        graph[node].add(target_node)

        execution_scope["excluded_cross_project_edges"] = excluded_cross_project_edges
        return graph, all_files, filtered_self_edges, canonical_atlas, execution_scope

    def _find_cycles(self, graph):
        if graph:
            sys.setrecursionlimit(max(sys.getrecursionlimit(), len(graph) + 100))
        cycles = []
        visited = set()
        rec_stack = set()
        path = []
        seen_cycles: dict[str, list[str]] = {}

        def canonicalize_cycle(cycle_nodes):
            if not cycle_nodes:
                return "", []
            if len(cycle_nodes) == 1:
                node = cycle_nodes[0]
                return f"{node}->{node}", [node, node]

            n = len(cycle_nodes)
            forward_rotations = []
            for i in range(n):
                forward_rotations.append(tuple(cycle_nodes[i:] + cycle_nodes[:i]))

            reverse_rotations = []
            rev_nodes = list(reversed(cycle_nodes))
            for i in range(n):
                reverse_rotations.append(tuple(rev_nodes[i:] + rev_nodes[:i]))

            best_forward = min(forward_rotations)
            best_reverse = min(reverse_rotations)
            dedupe_key = "->".join(min(best_forward, best_reverse))
            display_cycle = list(best_forward) + [best_forward[0]]
            return dedupe_key, display_cycle

        def dfs(node):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in sorted(graph.get(node, [])):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor) if neighbor in path else -1
                    if cycle_start >= 0:
                        cycle_nodes = path[cycle_start:]
                        cycle_key, normalized = canonicalize_cycle(cycle_nodes)
                        if cycle_key and cycle_key not in seen_cycles:
                            seen_cycles[cycle_key] = normalized
                            cycles.append(normalized)

            path.pop()
            rec_stack.remove(node)

        for node in sorted(graph.keys()):
            if node not in visited:
                dfs(node)

        return cycles

    def _generate_mermaid(self, graph):
        from tools.core.doctrine_contract import require_doctrine_mapping
        viz_config = require_doctrine_mapping("visualization_policy").get("circular_dep_mermaid")
        bucket_depth = viz_config.get("directory_bucket_depth", 2)
        max_edges = viz_config.get("max_edges_per_node", 10)

        dir_graph = defaultdict(lambda: defaultdict(int))

        for src, targets in graph.items():
            src_parts = src.split("::")[1] if "::" in src else src
            src_dir = "/".join(src_parts.split("/")[:bucket_depth])
            for tgt in targets:
                tgt_parts = tgt.split("::")[1] if "::" in tgt else tgt
                tgt_dir = "/".join(tgt_parts.split("/")[:bucket_depth])
                if src_dir != tgt_dir:
                    dir_graph[src_dir][tgt_dir] += 1

        lines = ["graph LR"]
        node_ids = {}
        counter = 0

        for src_dir in sorted(dir_graph.keys()):
            if src_dir not in node_ids:
                node_ids[src_dir] = f"N{counter}"
                counter += 1
            sorted_targets = sorted(dir_graph[src_dir].items(), key=lambda item: item[1], reverse=True)
            for tgt_dir, weight in sorted_targets[:max_edges]:
                if tgt_dir not in node_ids:
                    node_ids[tgt_dir] = f"N{counter}"
                    counter += 1
                src_id = node_ids[src_dir]
                tgt_id = node_ids[tgt_dir]
                lines.append(f'    {src_id}["{src_dir}"] -->|{weight}| {tgt_id}["{tgt_dir}"]')

        return "\n".join(lines)

    def run(self):
        from tools.core.config import DOCTRINE
        logger.info("Building import graph and scanning for circular dependencies...")

        graph, all_files, filtered_self_edges, atlas, execution_scope = self._build_import_graph()
        cycles = self._find_cycles(graph)
        mermaid = self._generate_mermaid(graph)

        from tools.core.doctrine_contract import require_doctrine_path
        classification = require_doctrine_path("visualization_policy", "cycle_classification", expected_type=dict)

        edges = []
        for src, targets in graph.items():
            for tgt in targets:
                edges.append({"source": src, "target": tgt})

        by_project_edges = defaultdict(int)
        for edge in edges:
            source = str(edge.get("source") or "")
            project_key = source.split("::", 1)[0] if "::" in source else "UNKNOWN"
            by_project_edges[project_key] += 1

        by_project_cycles = defaultdict(list)
        for cycle in cycles:
            if not cycle:
                continue
            project_key = cycle[0].split("::", 1)[0] if "::" in cycle[0] else "UNKNOWN"
            by_project_cycles[project_key].append(cycle)

        project_keys = set(self.projects.keys()) if isinstance(self.projects, dict) else set()
        project_keys.update(by_project_edges.keys())
        project_keys.update(by_project_cycles.keys())
        project_keys = sorted(project_keys)
        by_project_payload = {}
        for project_key in project_keys:
            project_cycles = by_project_cycles.get(project_key, [])
            cycle_samples = []
            for cycle in project_cycles[:20]:
                unique_length = max(0, len(cycle) - 1)
                cycle_kind = "mutual" if unique_length == classification.get("mutual_len", 2) else ("long_cycle" if unique_length >= classification.get("long_cycle_min_len", 3) else "degenerate")
                cycle_samples.append(
                    {
                        "chain": cycle,
                        "length": len(cycle),
                        "unique_length": unique_length,
                        "cycle_kind": cycle_kind,
                    }
                )
            by_project_payload[project_key] = {
                "edge_count": int(by_project_edges.get(project_key, 0)),
                "cycle_count": len(project_cycles),
                "has_cycles": bool(project_cycles),
                "cycle_samples": cycle_samples,
            }

        result = {
            "meta": {
                "kind": "circular_dependencies",
                "version": "v2",
                "execution_scope": execution_scope,
            },
            "total_files": len(all_files),
            "total_edges": len(edges),
            "cycles_found": len(cycles),
            "cycles": [{"chain": cycle, "length": len(cycle)} for cycle in cycles[:100]],
            "nodes": {node: {} for node in all_files},
            "edges": edges,
            "filtered_self_edges": filtered_self_edges,
            "filtered_self_edges_count": len(filtered_self_edges),
            "by_project": by_project_payload,
        }

        json_path = RAW_DIR / "circular_deps.json"
        save_json_atomic(json_path, result)
        write_current_atlas_lineage(
            artifact_id="circular_deps",
            producer="tools.engines.circular_dependency_finder",
            artifact_payload=result,
            atlas=atlas,
        )

        mermaid_path = REPORTS_DIR / "dependency_graph.mermaid"
        save_text_atomic(mermaid_path, mermaid)

        md_lines = [
            "# Circular Dependency & Import Graph Report",
            "",
            f"- **Total files scanned:** {len(all_files)}",
            f"- **Total import edges:** {sum(len(v) for v in graph.values())}",
            f"- **Circular dependencies found:** {len(cycles)}",
            f"- **Filtered self edges:** {len(filtered_self_edges)}",
            "",
        ]

        cycle_project_counts = defaultdict(int)
        for cycle in cycles:
            if not cycle:
                continue
            head = cycle[0]
            project_key = head.split("::", 1)[0] if "::" in head else "UNKNOWN"
            cycle_project_counts[project_key] += 1

        if cycles:
            md_lines.append("## By Project")
            md_lines.append("| Project | Cycle Count |")
            md_lines.append("|---|---:|")
            for project_key, count in sorted(cycle_project_counts.items(), key=lambda item: item[1], reverse=True):
                md_lines.append(f"| `{project_key}` | {count} |")
            md_lines.append("")
            md_lines.append("## [WARN] Detected Cycles")
            md_lines.append("| # | Cycle Chain | Length |")
            md_lines.append("|---:|---|---:|")
            for index, cycle in enumerate(cycles[:50], 1):
                # Keep project namespace (PROJECT::path) for deterministic multi-project traceability.
                chain_str = " -> ".join(cycle)
                md_lines.append(f"| {index} | `{chain_str}` | {len(cycle)} |")
            md_lines.append("")

            md_lines.append("## Cycles By Project")
            for project_key, project_cycles in sorted(by_project_cycles.items(), key=lambda item: len(item[1]), reverse=True):
                md_lines.append("")
                md_lines.append(f"### {project_key} ({len(project_cycles)})")
                md_lines.append("| # | Cycle Chain | Length |")
                md_lines.append("|---:|---|---:|")
                for index, cycle in enumerate(project_cycles[:20], 1):
                    chain_str = " -> ".join(cycle)
                    md_lines.append(f"| {index} | `{chain_str}` | {len(cycle)} |")
        else:
            md_lines.append("## [OK] No Circular Dependencies Detected!")

        if filtered_self_edges:
            md_lines.append("")
            md_lines.append("## Filtered Self Edges")
            md_lines.append("| # | Edge | Reason |")
            md_lines.append("|---:|---|---|")
            for index, edge in enumerate(filtered_self_edges[:50], 1):
                edge_text = f"{edge.get('source', '')} -> {edge.get('target', '')}"
                md_lines.append(f"| {index} | `{edge_text}` | `{edge.get('reason', 'self_dependency')}` |")
            if len(filtered_self_edges) > 50:
                md_lines.append(f"| ... | *and {len(filtered_self_edges) - 50} more* | |")

        md_lines.extend(
            [
                "",
                "## Dependency Graph",
                "See `dependency_graph.mermaid` for the full visual graph.",
                "",
                "```mermaid",
                mermaid,
                "```",
            ]
        )

        md_path = REPORTS_DIR / "circular_deps_report.md"
        save_text_atomic(md_path, "\n".join(md_lines))

        logger.info(f"[OK] Circular dependency analysis: {len(cycles)} cycles found")
        logger.info(f"[OK] Dependency graph saved to {mermaid_path}")
        logger.info(f"[OK] Report saved to {md_path}")


if __name__ == "__main__":
    CircularDependencyFinder().run()

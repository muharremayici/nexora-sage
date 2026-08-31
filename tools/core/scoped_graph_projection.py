from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable

from tools.core.contextos_signal_limits import contextos_signal_limit
from tools.core.doctrine_contract import require_doctrine_mapping


def build_atlas_dependency_graph(
    atlas: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Build forward and reverse file graphs from one current Atlas universe."""
    forward: dict[str, set[str]] = {}
    reverse: dict[str, set[str]] = {}
    nodes: set[str] = set()
    for project_key, project in atlas.items():
        if not isinstance(project, dict):
            continue
        dependencies = project.get("dependencies")
        files = project.get("files")
        if not isinstance(dependencies, dict) or not isinstance(files, dict):
            continue
        for rel_path in files:
            node = f"{project_key}::{str(rel_path).replace(chr(92), '/')}"
            nodes.add(node)
            forward.setdefault(node, set())
        for source_rel, targets in dependencies.items():
            source = f"{project_key}::{str(source_rel).replace(chr(92), '/')}"
            nodes.add(source)
            forward.setdefault(source, set())
            for target_rel in targets if isinstance(targets, list) else []:
                target_text = str(target_rel or "").replace("\\", "/")
                if not target_text:
                    continue
                target = target_text if "::" in target_text else f"{project_key}::{target_text}"
                nodes.add(target)
                forward[source].add(target)
                reverse.setdefault(target, set()).add(source)
    for node in nodes:
        forward.setdefault(node, set())
        reverse.setdefault(node, set())
    return forward, reverse, nodes


def build_atlas_reverse_dependency_graph(
    atlas: dict[str, Any],
) -> tuple[dict[str, set[str]], set[str]]:
    _forward, reverse, nodes = build_atlas_dependency_graph(atlas)
    return reverse, nodes


def get_transitive_dependents(node: str, reverse: dict[str, set[str]]) -> set[str]:
    visited = {node}
    reachable: set[str] = set()
    queue = deque([node])
    while queue:
        current = queue.popleft()
        for dependent in reverse.get(current, set()):
            if dependent in visited:
                continue
            visited.add(dependent)
            reachable.add(dependent)
            queue.append(dependent)
    return reachable


def strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return deterministic SCCs without depending on Python recursion depth."""
    nodes = set(graph)
    for neighbors in graph.values():
        nodes.update(neighbors)
    ordered_nodes = sorted(nodes)

    visited: set[str] = set()
    finish_order: list[str] = []
    for root in ordered_nodes:
        if root in visited:
            continue
        visited.add(root)
        stack: list[tuple[str, Any]] = [(root, iter(sorted(graph.get(root, set()))))]
        while stack:
            node, neighbors = stack[-1]
            try:
                neighbor = next(neighbors)
            except StopIteration:
                stack.pop()
                finish_order.append(node)
                continue
            if neighbor in visited:
                continue
            visited.add(neighbor)
            stack.append((neighbor, iter(sorted(graph.get(neighbor, set())))))

    reverse: dict[str, set[str]] = {node: set() for node in nodes}
    for source, targets in graph.items():
        for target in targets:
            reverse[target].add(source)

    assigned: set[str] = set()
    components: list[list[str]] = []
    for root in reversed(finish_order):
        if root in assigned:
            continue
        assigned.add(root)
        component: list[str] = []
        stack = [(root, False)]
        while stack:
            node, _expanded = stack.pop()
            component.append(node)
            for neighbor in reversed(sorted(reverse.get(node, set()))):
                if neighbor in assigned:
                    continue
                assigned.add(neighbor)
                stack.append((neighbor, False))
        component.sort()
        if len(component) > 1 or root in graph.get(root, set()):
            components.append(component)
    return sorted(components, key=lambda members: (members[0], len(members)))


def _component_cycle_witness(
    graph: dict[str, set[str]],
    component: list[str],
) -> list[str]:
    allowed = set(component)
    if not allowed:
        return []
    for start in sorted(allowed):
        if start in graph.get(start, set()):
            return [start, start]
        for neighbor in sorted(graph.get(start, set()) & allowed):
            queue = deque([neighbor])
            parent: dict[str, str | None] = {neighbor: None}
            while queue:
                current = queue.popleft()
                if current == start:
                    path: list[str] = []
                    cursor: str | None = current
                    while cursor is not None:
                        path.append(cursor)
                        cursor = parent[cursor]
                    path.reverse()
                    return [start, *path]
                for target in sorted(graph.get(current, set()) & allowed):
                    if target in parent:
                        continue
                    parent[target] = current
                    queue.append(target)
    return []


def _atlas_file(atlas: dict[str, Any], node: str) -> dict[str, Any]:
    if "::" not in node:
        return {}
    project_key, rel_path = node.split("::", 1)
    project = atlas.get(project_key)
    files = project.get("files") if isinstance(project, dict) else None
    value = files.get(rel_path) if isinstance(files, dict) else None
    return value if isinstance(value, dict) else {}


def _symbol_consumption_index(atlas: dict[str, Any]) -> set[tuple[str, str, str]]:
    consumed: set[tuple[str, str, str]] = set()
    for project_key, project in atlas.items():
        files = project.get("files") if isinstance(project, dict) else None
        if not isinstance(files, dict):
            continue
        for file_data in files.values():
            if not isinstance(file_data, dict):
                continue
            for record in file_data.get("import_records", []) or []:
                if not isinstance(record, dict):
                    continue
                source = str(record.get("source") or "").replace("\\", "/")
                name = str(record.get("name") or "").strip()
                kind = str(record.get("kind") or "").lower()
                if source and name and kind != "namespace":
                    consumed.add((str(project_key), source, name))
    return consumed


def _dead_code_candidates(
    atlas: dict[str, Any],
    focus_nodes: Iterable[str],
    reverse: dict[str, set[str]],
) -> dict[str, list[dict[str, Any]]]:
    consumed = _symbol_consumption_index(atlas)
    limit = contextos_signal_limit("dead_code_candidates")
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in focus_nodes:
        if "::" not in node:
            continue
        if reverse.get(node):
            continue
        project_key, rel_path = node.split("::", 1)
        file_data = _atlas_file(atlas, node)
        exported_names = {
            str(value if isinstance(value, str) else value.get("name") or "")
            for value in file_data.get("exports", []) or []
            if isinstance(value, (str, dict))
        }
        for symbol in file_data.get("symbols", []) or []:
            if not isinstance(symbol, dict):
                continue
            name = str(symbol.get("name") or "").strip()
            if not name or not (bool(symbol.get("exported")) or name in exported_names):
                continue
            if str(symbol.get("type") or "") not in {
                "Arrow",
                "Class",
                "Component",
                "Function",
                "Hook",
                "Variable",
            }:
                continue
            features = {str(value) for value in symbol.get("features", []) or []}
            runtime_contract = bool(symbol.get("runtime_contract") or symbol.get("runtimeContract"))
            if runtime_contract or "Contract:RuntimeDiscovered" in features:
                continue
            if (project_key, rel_path, name) in consumed:
                continue
            candidates[node].append(
                {
                    "symbol": name,
                    "symbol_type": str(symbol.get("type") or "unknown"),
                    "start_line": symbol.get("line", symbol.get("start")),
                    "end_line": symbol.get("end_line", symbol.get("end")),
                    "status": "candidate_for_full_dead_code_review",
                    "confidence": "low",
                    "reason": "no_static_named_import_observed_in_current_atlas",
                }
            )
            if len(candidates[node]) >= limit:
                break
    return dict(candidates)


def build_scoped_graph_advisories(
    atlas: dict[str, Any],
    focus_nodes: Iterable[str],
) -> dict[str, Any]:
    requested = sorted({str(node) for node in focus_nodes if str(node).strip()})
    forward, reverse, nodes = build_atlas_dependency_graph(atlas)
    components = strongly_connected_components(forward)
    component_by_node = {
        node: component
        for component in components
        for node in component
    }
    witness_by_node = {
        node: witness
        for component in components
        for witness in [_component_cycle_witness(forward, component)]
        for node in component
    }
    dead_candidates = _dead_code_candidates(atlas, requested, reverse)
    weights = require_doctrine_mapping("impact_analysis_tuning").get("formula_weights", {})
    direct_weight = float(weights.get("direct_dependent", 1.0))
    transitive_weight = float(weights.get("transitive_dependent", 0.5))
    cycle_node_cap = contextos_signal_limit("cycle_nodes")
    advisories: list[dict[str, Any]] = []

    for node in requested:
        if node not in nodes:
            advisories.append(
                {
                    "node_key": node,
                    "target_ref": node,
                    "status": "unknown",
                    "reason": "focus_node_not_found_in_current_atlas",
                }
            )
            continue
        direct = sorted(reverse.get(node, set()))
        transitive = sorted(get_transitive_dependents(node, reverse))
        component = component_by_node.get(node, [])
        witness = witness_by_node.get(node, [])
        if component and not witness:
            raise RuntimeError(
                f"Strongly connected component has no inspectable cycle witness: {node}"
            )
        candidates = dead_candidates.get(node, [])
        file_data = _atlas_file(atlas, node)
        target_ref = str(file_data.get("target_ref") or node)
        advisories.append(
            {
                "node_key": node,
                "target_ref": target_ref,
                "status": "available",
                "cycle_membership": {
                    "status": "member" if component else "not_member",
                    "component_size": len(component),
                    "component_nodes": component[:cycle_node_cap],
                    "component_nodes_omitted": max(0, len(component) - cycle_node_cap),
                    "witness_chain": witness,
                    "witness_edges": [
                        {"source": witness[index], "target": witness[index + 1]}
                        for index in range(max(0, len(witness) - 1))
                    ],
                    "claim_boundary": "current_global_atlas_strongly_connected_component",
                },
                "impact_advisory": {
                    "status": "available_dependency_only_lower_bound",
                    "direct_dependents": len(direct),
                    "transitive_dependents": len(transitive),
                    "dependency_score": round(
                        (len(direct) * direct_weight) + (len(transitive) * transitive_weight),
                        3,
                    ),
                    "formula_weights": {
                        "direct_dependent": direct_weight,
                        "transitive_dependent": transitive_weight,
                    },
                    "boundary_bonus_status": "deferred_to_full_blast_radius",
                    "claim_boundary": "dependency_score_is_lower_bound_not_canonical_blast_score",
                },
                "dead_code_advisory": {
                    "status": "candidates_available" if candidates else "no_static_candidate_in_scope",
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                    "claim_boundary": "candidate_only_not_dead_code_proof_or_absence_claim",
                    "required_follow_up": "full_dead_code_engine_before_removal",
                },
            }
        )

    return {
        "meta": {
            "kind": "watchdog_graph_advisories",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "tools.core.scoped_graph_projection",
        },
        "summary": {
            "status": "PASS" if advisories and all(row.get("status") == "available" for row in advisories) else "PARTIAL",
            "requested_nodes": len(requested),
            "available_nodes": sum(row.get("status") == "available" for row in advisories),
            "cycle_members": sum(
                (row.get("cycle_membership") or {}).get("status") == "member"
                for row in advisories
            ),
            "dead_code_candidates": sum(
                int((row.get("dead_code_advisory") or {}).get("candidate_count") or 0)
                for row in advisories
            ),
            "evidence_scope": "changed_nodes_projected_from_current_global_atlas",
            "full_repository_claim": False,
            "canonical_artifacts_overwritten": False,
        },
        "requested_nodes": requested,
        "advisories": advisories,
    }

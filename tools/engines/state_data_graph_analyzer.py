from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.atlas_integrity import payload_sha256
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.logger import logger


POLICY_PATH = CONFIG_DIR / "state_data_graph_policy.json"
_POLICY_CACHE: dict[str, Any] | None = None


def _policy_string_list(section: dict[str, Any], key: str) -> list[str]:
    values = section.get(key, [])
    if not isinstance(values, list):
        return []
    return sorted({str(item).replace("\\", "/").strip().lower() for item in values if str(item or "").strip()})


def _load_state_data_policy(force: bool = False) -> dict[str, Any]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None and not force:
        return _POLICY_CACHE
    configured = load_json_object_strict(POLICY_PATH, label="State data graph policy")
    reference = configured.get("reference_surfaces", {})
    if not isinstance(reference, dict):
        reference = {}
    _POLICY_CACHE = {
        "meta": configured.get("meta", {"kind": "state_data_graph_policy", "version": "default"}),
        "policy_source": str(POLICY_PATH),
        "reference_surfaces": {
            "path_markers": _policy_string_list(reference, "path_markers"),
            "file_suffixes": _policy_string_list(reference, "file_suffixes"),
        },
        "client_invalidation_tokens": _policy_string_list(configured, "client_invalidation_tokens"),
        "key_token_stopwords": set(_policy_string_list(configured, "key_token_stopwords")),
    }
    return _POLICY_CACHE


def _project_from_key_or_paths(key: str, paths: list[str]) -> str:
    if "::" in str(key):
        return str(key).split("::", 1)[0]
    projects = [str(path).split("::", 1)[0] for path in paths if "::" in str(path)]
    return projects[0] if projects else "unknown"


def _split_project_file(value: str) -> tuple[str, str]:
    if "::" in str(value):
        project, rel_path = str(value).split("::", 1)
        return project, rel_path.replace("\\", "/")
    return "unknown", str(value).replace("\\", "/")


def _key_tokens(value: str) -> set[str]:
    normalized = str(value or "").lower()
    for char in "[]{}()'\"`.,:/\\":
        normalized = normalized.replace(char, " ")
    stopwords = _load_state_data_policy().get("key_token_stopwords", set())
    return {token for token in normalized.split() if len(token) >= 3 and token not in stopwords}


def _source_context_for_path(path: str) -> str:
    path_text = str(path or "").replace("\\", "/").lower()
    normalized = f"/{path_text}"
    reference = _load_state_data_policy().get("reference_surfaces", {})
    markers = reference.get("path_markers", []) or []
    suffixes = tuple(reference.get("file_suffixes", []) or ())
    parts = [part for part in normalized.split("/") if part]
    has_marker = any(marker.strip("/") in parts for marker in markers)
    if has_marker or normalized.endswith(suffixes):
        return "reference_or_test_surface"
    return "production_surface"


def _client_action_has_invalidation(actions: list[str]) -> bool:
    text = " ".join(str(action) for action in actions).lower()
    tokens = _load_state_data_policy().get("client_invalidation_tokens", []) or []
    return any(token in text for token in tokens)


def _build_possible_invalidation_edges(
    queries: dict[str, Any],
    mutations: dict[str, Any],
    boundary_signals: dict[str, Any],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for mutation, mutation_keys in (mutations or {}).items():
        actions = list((boundary_signals.get(str(mutation)) or {}).get("client_actions") or [])
        for query, query_keys in (queries or {}).items():
            matched_key_pairs = []
            shared_tokens: set[str] = set()
            relation_basis: set[str] = set()
            for mutation_key in mutation_keys or []:
                for query_key in query_keys or []:
                    mutation_text = str(mutation_key).strip().lower()
                    query_text = str(query_key).strip().lower()
                    pair_tokens = _key_tokens(query_text) & _key_tokens(mutation_text)
                    pair_basis = []
                    if query_text and mutation_text and (
                        query_text in mutation_text or mutation_text in query_text
                    ):
                        pair_basis.append("normalized_name_containment")
                    if pair_tokens:
                        pair_basis.append("shared_key_tokens")
                    if pair_basis:
                        relation_basis.update(pair_basis)
                        shared_tokens.update(pair_tokens)
                        matched_key_pairs.append(
                            {
                                "mutation_key": str(mutation_key),
                                "query_key": str(query_key),
                                "relation_basis": sorted(pair_basis),
                            }
                        )
            if matched_key_pairs:
                edges.append(
                    {
                        "from": f"mutation:{mutation}",
                        "to": f"query:{query}",
                        "kind": "possible_invalidation",
                        "shared_tokens": sorted(shared_tokens),
                        "has_client_invalidation_action": _client_action_has_invalidation(actions),
                        "evidence": {
                            "relation_basis": sorted(relation_basis),
                            "source_nodes": [f"mutation:{mutation}", f"query:{query}"],
                            "source_files": sorted({str(mutation), str(query)}),
                            "matched_key_pairs": matched_key_pairs[:40],
                        },
                    }
                )
    return edges


def _zustand_selector_risk_rows(boundary_signals: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, signal in (boundary_signals or {}).items():
        if not isinstance(signal, dict):
            continue
        no_selector_calls = [str(item) for item in (signal.get("zustand_no_selector_calls") or []) if str(item).strip()]
        broad_selector_calls = [str(item) for item in (signal.get("zustand_broad_selector_calls") or []) if str(item).strip()]
        if not no_selector_calls and not broad_selector_calls:
            continue
        project, rel_path = _split_project_file(str(key))
        source_context = _source_context_for_path(rel_path)
        risks = []
        if no_selector_calls:
            risks.append("zustand_selectorless_store_read")
        if broad_selector_calls:
            risks.append("zustand_broad_selector_returns_store")
        rows.append({
            "project": project,
            "file": rel_path,
            "source_context": source_context,
            "actionability": "reference_only" if source_context == "reference_or_test_surface" else "review",
            "no_selector_calls": sorted(set(no_selector_calls)),
            "broad_selector_calls": sorted(set(broad_selector_calls)),
            "risks": risks,
        })
    return rows


def _router_signals_from_atlas(atlas: dict) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if not isinstance(atlas, dict):
        return signals
    router_features = {"RouterConfig", "RouteLoader", "RouteAction", "RouteLazy", "RouteRedirect", "RouteErrorBoundary"}
    for project, pdata in atlas.items():
        files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
        for rel_path, fdata in files.items():
            features = set(fdata.get("features", []) if isinstance(fdata, dict) else [])
            matched = sorted(router_features & features)
            if matched:
                signals.append({"project": project, "file": rel_path, "features": matched})
    return signals[:300]


def run_state_data_graph_analyzer() -> dict[str, Any]:
    logger.info("Building React state/data relationship graph...")
    state_flow = load_json_file(RAW_DIR / "state_flow.json", {})
    atlas_commit = load_json_file(RAW_DIR / "atlas_commit.json", {})
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    policy = load_json_object_strict(POLICY_PATH, label="State data graph policy")

    stores = state_flow.get("zustand_stores", {}) if isinstance(state_flow, dict) else {}
    queries = state_flow.get("tanstack_queries", {}) if isinstance(state_flow, dict) else {}
    mutations = state_flow.get("tanstack_mutations", {}) if isinstance(state_flow, dict) else {}
    boundary_signals = state_flow.get("boundary_signals", {}) if isinstance(state_flow, dict) else {}

    nodes: list[dict[str, Any]] = []
    edges = _build_possible_invalidation_edges(queries, mutations, boundary_signals)
    project_counts = Counter()

    for store, paths in (stores or {}).items():
        path_list = [str(store)] if "::" in str(store) else list(paths or [])
        project = _project_from_key_or_paths(str(store), path_list)
        project_counts[project] += 1
        nodes.append({"id": f"store:{store}", "kind": "zustand_store", "project": project, "files": path_list[:20]})

    for query, paths in (queries or {}).items():
        path_list = [str(query)] if "::" in str(query) else list(paths or [])
        project = _project_from_key_or_paths(str(query), path_list)
        project_counts[project] += 1
        nodes.append({"id": f"query:{query}", "kind": "tanstack_query", "project": project, "files": path_list[:20]})

    for mutation, mutation_keys in (mutations or {}).items():
        path_list = [str(mutation)] if "::" in str(mutation) else []
        project = _project_from_key_or_paths(str(mutation), path_list)
        project_counts[project] += 1
        nodes.append({"id": f"mutation:{mutation}", "kind": "tanstack_mutation", "project": project, "files": path_list[:20]})

    redux_signals = []
    if isinstance(atlas, dict):
        for project, pdata in atlas.items():
            files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            for rel_path, fdata in files.items():
                features = fdata.get("features", []) if isinstance(fdata, dict) else []
                redux_features = [feat for feat in features if str(feat) in {"ReduxStore", "ReduxSlice", "ReduxAsyncThunk"}]
                if redux_features:
                    redux_signals.append({"project": project, "file": rel_path, "features": sorted(set(redux_features))})

    query_key_groups: dict[str, list[str]] = defaultdict(list)
    for query, query_keys in (queries or {}).items():
        path_list = [str(query)] if "::" in str(query) else []
        for token in sorted({token for key in query_keys or [] for token in _key_tokens(str(key))})[:12]:
            query_key_groups[token].extend(path_list[:8])

    mutation_coverage = []
    for mutation, paths in (mutations or {}).items():
        project, rel_path = _split_project_file(str(mutation))
        source_context = _source_context_for_path(rel_path)
        actions = list((boundary_signals.get(str(mutation)) or {}).get("client_actions") or [])
        related_edges = [edge for edge in edges if edge.get("from") == f"mutation:{mutation}"]
        has_invalidation = _client_action_has_invalidation(actions) or any(edge.get("has_client_invalidation_action") for edge in related_edges)
        risks = []
        if not related_edges:
            risks.append("mutation_without_related_query_key")
        if not has_invalidation:
            risks.append("mutation_without_visible_invalidation_action")
        mutation_coverage.append({
            "project": project,
            "file": rel_path,
            "mutation": str(mutation),
            "client_actions": actions,
            "related_queries": [edge.get("to") for edge in related_edges[:12]],
            "has_visible_invalidation": has_invalidation,
            "source_context": source_context,
            "actionability": "reference_only" if source_context == "reference_or_test_surface" else "review",
            "risks": risks,
        })

    router_signals = _router_signals_from_atlas(atlas)
    router_risks = []
    for row in router_signals:
        features = set(row.get("features") or [])
        source_context = _source_context_for_path(str(row.get("file") or ""))
        actionability = "reference_only" if source_context == "reference_or_test_surface" else "review"
        if "RouteLoader" in features and "RouteErrorBoundary" not in features:
            router_risks.append({**row, "risk": "route_loader_without_visible_error_boundary", "source_context": source_context, "actionability": actionability})
        if "RouteAction" in features and "RouteRedirect" not in features:
            router_risks.append({**row, "risk": "route_action_without_visible_redirect_or_completion_contract", "source_context": source_context, "actionability": actionability})

    zustand_selector_risks = _zustand_selector_risk_rows(boundary_signals)
    by_kind = Counter(node["kind"] for node in nodes)
    actionable_mutation_risks = [item for item in mutation_coverage if item.get("risks") and item.get("actionability") != "reference_only"]
    actionable_router_risks = [item for item in router_risks if item.get("actionability") != "reference_only"]
    actionable_zustand_selector_risks = [item for item in zustand_selector_risks if item.get("actionability") != "reference_only"]
    payload = {
        "meta": {
            "kind": "state_data_graph",
            "version": "v2",
            "atlas_snapshot_id": atlas_commit.get("snapshot_id") if isinstance(atlas_commit, dict) else None,
            "state_flow_sha256": payload_sha256(state_flow),
            "policy_sha256": payload_sha256(policy),
        },
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "by_kind": dict(by_kind),
            "redux_signal_files": len(redux_signals),
            "query_key_groups": len(query_key_groups),
            "mutation_coverage_items": len(mutation_coverage),
            "mutation_risks": len(actionable_mutation_risks),
            "mutation_risks_total": sum(1 for item in mutation_coverage if item.get("risks")),
            "router_signal_files": len(router_signals),
            "router_risks": len(actionable_router_risks),
            "router_risks_total": len(router_risks),
            "zustand_selector_risks": len(actionable_zustand_selector_risks),
            "zustand_selector_risks_total": len(zustand_selector_risks),
            "by_project": dict(sorted(project_counts.items())),
        },
        "nodes": nodes,
        "edges": edges,
        "redux_signals": redux_signals[:200],
        "query_key_graph": {
            "groups": {key: sorted(set(value))[:20] for key, value in sorted(query_key_groups.items())},
        },
        "mutation_coverage": mutation_coverage[:300],
        "router_signals": router_signals,
        "router_risks": router_risks[:200],
        "zustand_selector_risks": zustand_selector_risks[:300],
    }
    save_json_atomic(RAW_DIR / "state_data_graph.json", payload)

    lines = [
        "# State/Data Graph",
        "",
        f"- Nodes: `{len(nodes)}`",
        f"- Edges: `{len(edges)}`",
        f"- Redux signal files: `{len(redux_signals)}`",
        f"- Mutation coverage items: `{len(mutation_coverage)}`",
        f"- Mutation risks: `{payload['summary']['mutation_risks']}` actionable / `{payload['summary']['mutation_risks_total']}` total",
        f"- Router risks: `{payload['summary']['router_risks']}` actionable / `{payload['summary']['router_risks_total']}` total",
        f"- Zustand selector risks: `{payload['summary']['zustand_selector_risks']}` actionable / `{payload['summary']['zustand_selector_risks_total']}` total",
        "",
        "| Kind | Count |",
        "|---|---:|",
    ]
    for kind, count in by_kind.most_common():
        lines.append(f"| `{kind}` | {count} |")
    lines.extend(["", "## Mutation Links", "", "| From | To | Kind |", "|---|---|---|"])
    for edge in edges[:80]:
        lines.append(f"| `{edge['from']}` | `{edge['to']}` | `{edge['kind']}` |")
    lines.extend(["", "## Mutation Coverage", "", "| Project | File | Invalidation | Context | Actionability | Risks |", "|---|---|---:|---|---|---|"])
    for item in mutation_coverage[:80]:
        lines.append(
            f"| `{item['project']}` | `{item['file']}` | `{item['has_visible_invalidation']}` | "
            f"`{item['source_context']}` | `{item['actionability']}` | "
            f"`{', '.join(item['risks']) or '-'}` |"
        )
    lines.extend(["", "## Router Risks", "", "| Project | File | Actionability | Risk | Features |", "|---|---|---|---|---|"])
    for item in router_risks[:80]:
        lines.append(
            f"| `{item['project']}` | `{item['file']}` | `{item.get('actionability', 'review')}` | `{item['risk']}` | `{', '.join(item.get('features') or [])}` |"
        )
    lines.extend(["", "## Zustand Selector Risks", "", "| Project | File | Actionability | Risks | No selector calls | Broad selector calls |", "|---|---|---|---|---|---|"])
    for item in zustand_selector_risks[:80]:
        lines.append(
            f"| `{item['project']}` | `{item['file']}` | `{item.get('actionability', 'review')}` | "
            f"`{', '.join(item.get('risks') or [])}` | "
            f"`{', '.join(item.get('no_selector_calls') or []) or '-'}` | "
            f"`{', '.join(item.get('broad_selector_calls') or []) or '-'}` |"
        )
    save_text_atomic(REPORTS_DIR / "state_data_graph.md", "\n".join(lines) + "\n")
    return payload


if __name__ == "__main__":
    run_state_data_graph_analyzer()

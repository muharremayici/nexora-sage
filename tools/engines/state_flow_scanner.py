"""
State Flow Scanner
Scans source code specifically for Zustand stores and TanStack React Query usages
to map the data flow and state consumption across the architecture.
"""

import json
import time
from collections import defaultdict

from tools.core.atlas_io import resolve_atlas_data
from tools.core.atlas_integrity import ATLAS_COMMIT_KIND, ATLAS_COMMIT_VERSION, payload_sha256, source_fingerprint
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import project_display_name
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.state_flow import TRANSITIVE_HOOK_COVERAGE, summarize_state_flow_features


TRANSITION_FEATURE_EXACT = {
    "Tech:setState",
    "Tech:getState",
    "Tech:transition",
    "Tech:subscribe",
    "Tech:dispatch",
    "Tech:reducer",
}
PROPERTY_FEATURE_EXACT = {
    "Tech:shallow",
    "Tech:memo",
    "Tech:useMemo",
    "Tech:useCallback",
    "Tech:context",
}
STATE_CONTRACT_HINTS = ("State->", "Store->", "Slice->", "Schema->", "Port->", "Type->")
TRANSITION_CONTRACT_VERBS = ("create", "set", "update", "toggle", "patch", "apply", "dispatch", "reduce", "mutate", "sync")
WEAK_PROPERTY_MARKERS = {"Tech:zustand_store_shape"}
STRICT_PROPERTY_DRIVER_MARKERS = {
    "Tech:selector",
    "Contract:SelectorType",
    "Tech:shallow",
    "Tech:memo",
    "Tech:useMemo",
    "Tech:useCallback",
    "Tech:context",
}


def _state_flow_has_signal(state_flow: dict) -> bool:
    return bool(
        state_flow.get("has_zustand_store")
        or state_flow.get("zustand_consumers")
        or state_flow.get("zustand_no_selector_calls")
        or state_flow.get("zustand_broad_selector_calls")
        or state_flow.get("react_external_store_consumers")
        or state_flow.get("query_keys")
        or state_flow.get("query_key_refs")
        or state_flow.get("query_key_dynamic")
        or state_flow.get("mutation_keys")
        or state_flow.get("mutation_key_refs")
        or state_flow.get("mutation_key_dynamic")
        or state_flow.get("client_actions")
        or state_flow.get("technologies")
    )


def _exported_hook_names(file_data: dict) -> set[str]:
    hooks: set[str] = set()
    for symbol in file_data.get("symbols", []) or []:
        if not isinstance(symbol, dict):
            continue
        name = str(symbol.get("name") or "")
        if name.startswith("use") and symbol.get("exported") is True:
            hooks.add(name)
    for export in file_data.get("exports", []) or []:
        name = str(export or "")
        if name.startswith("use"):
            hooks.add(name)
    return hooks


def _bound_hook_import_calls(file_data: dict) -> dict[tuple[str, str], tuple[list[dict], int]]:
    """Index positive lexical top-level call sites once per file; no execution claim."""
    symbols = file_data.get("symbols")
    if not isinstance(symbols, list):
        return {}
    sites: dict[tuple[str, str], list[dict]] = defaultdict(list)
    omitted: dict[tuple[str, str], int] = defaultdict(int)
    for symbol in symbols:
        if not isinstance(symbol, dict) or symbol.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
            continue
        name, start, end = symbol.get("name"), symbol.get("line"), symbol.get("end_line")
        evidence = symbol.get("import_call_evidence")
        if (not isinstance(name, str) or not name or type(start) is not int or type(end) is not int
                or not 0 < start <= end or not isinstance(evidence, dict)
                or evidence.get("status") != "observed"
                or evidence.get("binding_scope") != "single_file_lexical_import"
                or not isinstance(evidence.get("calls"), list)):
            continue
        for call in evidence["calls"]:
            if not isinstance(call, dict):
                continue
            line, end_line = call.get("line"), call.get("end_line")
            source, imported_name = call.get("source"), call.get("importedName")
            if (not isinstance(source, str) or not source
                    or not isinstance(imported_name, str) or not imported_name.startswith("use")
                    or call.get("kind") != "named" or call.get("member") is not None
                    or call.get("optional") is not False
                    or not isinstance(call.get("localName"), str) or not call["localName"]
                    or type(line) is not int or type(end_line) is not int
                    or not start <= line <= end_line <= end):
                continue
            key = (source, imported_name)
            if len(sites[key]) < 8:
                sites[key].append({"symbol": name, "line": line, "end_line": end_line})
            else:
                omitted[key] += 1
    return {key: (value, omitted[key]) for key, value in sites.items()}


def _transition_subject_file_set(
    stores: dict,
    queries: dict,
    mutations: dict,
    boundary_signals: dict,
    stateful_files: list[str] | set[str] | None = None,
) -> list[str]:
    """
    Files with read-only TanStack query surfaces are state/data surfaces, but not
    transition obligations by themselves. Transition proof should be required for
    store/mutation owners and explicit QueryClient/client action surfaces.
    """
    client_action_files = {
        rel
        for rel, signal in (boundary_signals or {}).items()
        if (signal or {}).get("client_actions")
    }
    stateful_scope = set(stateful_files or (set(stores.keys()) | set(queries.keys()) | set(mutations.keys())))
    return sorted(set(stores.keys()) | set(mutations.keys()) | (client_action_files & stateful_scope))


def _extract_transition_markers(features: list[str], imported_contracts: list[str]) -> list[str]:
    markers = set()
    for feature in features:
        text = str(feature or "").strip()
        if not text:
            continue
        if text in TRANSITION_FEATURE_EXACT:
            markers.add(text)
        if (
            text.startswith("QueryKey:")
            or text.startswith("QueryKeyRef:")
            or text.startswith("QueryKeyDynamic:")
            or text.startswith("MutationKey:")
            or text.startswith("MutationKeyRef:")
            or text.startswith("MutationKeyDynamic:")
            or text.startswith("QueryClientAction:")
        ):
            markers.add(text.split(":", 1)[0])
    for contract in imported_contracts:
        symbol = str(contract or "").split("->", 1)[0].strip()
        lower = symbol.lower()
        if any(lower.startswith(verb) for verb in TRANSITION_CONTRACT_VERBS):
            markers.add("ContractTransitionVerb")
            break
    return sorted(markers)


def _extract_property_markers(features: list[str], imported_contracts: list[str]) -> list[str]:
    markers = set()
    for feature in features:
        text = str(feature or "").strip()
        if not text:
            continue
        if text in PROPERTY_FEATURE_EXACT:
            markers.add(text)
            continue
        if text == "ZustandStore":
            markers.add("Tech:zustand_store_shape")
            continue
        lower = text.lower()
        if text.startswith("Tech:") and "selector" in lower:
            markers.add("Tech:selector")
        if text.startswith("Type:") and any(token in text for token in ("State", "Store", "Slice")):
            markers.add("Type:StateShape")
    for contract in imported_contracts:
        lower = contract.lower()
        if any(hint in contract for hint in STATE_CONTRACT_HINTS):
            markers.add("Contract:StateType")
        if "selector" in lower:
            markers.add("Contract:SelectorType")
    return sorted(markers)


def _scope_matches_file_key(changed_scope: set[str] | None, *keys: str) -> bool:
    if changed_scope is None:
        return True
    candidates: set[str] = set()
    for key in keys:
        text = str(key or "").replace("\\", "/").strip("/")
        if not text:
            continue
        candidates.add(text)
        if "::" in text:
            candidates.add(text.split("::", 1)[1])
        else:
            candidates.add(f"src/{text}" if not text.startswith("src/") else text[4:])
    return bool(candidates & changed_scope)


def _build_genome_file_index(atlas: dict, changed_scope: set[str] | None = None,
                             *, genome: dict | None = None):
    genome = load_genome_data() if genome is None else genome
    index = defaultdict(
        lambda: {
            "imported_contracts": set(),
            "ui_dependencies": set(),
            "architectural_markers": set(),
            "dynamic_imports": set(),
        }
    )
    if not isinstance(genome, dict):
        return index

    for occs in genome.values():
        for occ in occs if isinstance(occs, list) else []:
            if not isinstance(occ, dict):
                continue
            project, file_rel = occ.get("project"), occ.get("file")
            project_data = atlas.get(project) if isinstance(project, str) else None
            files = project_data.get("files") if isinstance(project_data, dict) else None
            file_data = files.get(file_rel) if isinstance(files, dict) and isinstance(file_rel, str) else None
            if not isinstance(file_data, dict):
                continue
            workspace_rel = file_data.get("workspace_rel") or file_rel
            qualified_file = f"{project}::{file_rel}"
            qualified_workspace = f"{project}::{workspace_rel}"
            if (not isinstance(workspace_rel, str) or not workspace_rel
                    or occ.get("scoped_file") != qualified_file
                    or occ.get("workspace_rel") != workspace_rel
                    or occ.get("scoped_workspace_rel") != qualified_workspace
                    or not isinstance(file_data.get("hash"), str) or not file_data["hash"]
                    or occ.get("file_hash") != file_data["hash"]
                    or not _scope_matches_file_key(changed_scope, workspace_rel, file_rel,
                                                    qualified_workspace, qualified_file)):
                continue
            bucket = index[qualified_file]
            bucket["imported_contracts"].update(occ.get("imported_contracts") or [])
            bucket["ui_dependencies"].update(occ.get("ui_dependencies") or [])
            bucket["architectural_markers"].update(occ.get("architectural_markers") or [])
            bucket["dynamic_imports"].update(occ.get("dynamic_imports") or [])
    return index


def _changed_scope(changed_files: list[str] | None) -> set[str] | None:
    if changed_files is None:
        return None
    scope = set()
    for item in changed_files or []:
        text = str(item or "").replace("\\", "/").strip()
        if not text:
            continue
        scope.add(text if "::" in text else text.strip("/"))
    return scope


def _atlas_source_commit_binding(atlas: dict, commit: dict) -> dict:
    """Bind the loaded Atlas source inventory to its canonical commit, not live bytes."""
    try:
        observed_source = source_fingerprint(atlas)
    except (TypeError, ValueError):
        observed_source = None
    meta = commit.get("meta") if isinstance(commit, dict) else None
    snapshot_id = commit.get("snapshot_id") if isinstance(commit, dict) else None
    projects = sorted(key for key, value in atlas.items()
                      if key != "symbols" and isinstance(value, dict))
    matched = (
        isinstance(meta, dict) and meta.get("kind") == ATLAS_COMMIT_KIND
        and meta.get("version") == ATLAS_COMMIT_VERSION
        and commit.get("state") == "complete"
        and isinstance(snapshot_id, str) and len(snapshot_id) == 64
        and all(char in "0123456789abcdef" for char in snapshot_id)
        and bool(observed_source) and commit.get("source_fingerprint") == observed_source
        and commit.get("projects") == projects
    )
    return {"status": "source_inventory_match" if matched else "unavailable",
            "snapshot_id": snapshot_id if matched else None,
            "source_fingerprint": observed_source}


def _incremental_cache_matches_transition(previous: dict, commit: dict,
                                          changed_scope: set[str] | None,
                                          analyzed_projects: list[str], binding: dict) -> bool:
    """Reuse old file results only across one declared scoped Atlas transition."""
    if (not isinstance(previous, dict) or not isinstance(commit, dict)
            or not isinstance(changed_scope, set) or not changed_scope
            or previous.get("transitive_hook_coverage") != TRANSITIVE_HOOK_COVERAGE
            or binding.get("status") != "source_inventory_match"
            or commit.get("generation_mode") != "surgical"):
        return False
    prior = previous.get("run_meta")
    transition = commit.get("generation_transition")
    if not isinstance(prior, dict) or not isinstance(transition, dict):
        return False
    prior_scope = prior.get("execution_scope")
    refs = transition.get("changed_files")
    deleted = transition.get("deleted_files")
    parent_snapshot_id = transition.get("parent_snapshot_id")
    if (prior.get("atlas_source_binding") != "source_inventory_match"
            or not isinstance(prior.get("analyzed_source_fingerprint"), str)
            or not prior["analyzed_source_fingerprint"]
            or not isinstance(prior_scope, dict)
            or prior_scope.get("analyzed_projects") != analyzed_projects
            or not isinstance(parent_snapshot_id, str) or len(parent_snapshot_id) != 64
            or any(char not in "0123456789abcdef" for char in parent_snapshot_id)
            or prior.get("atlas_snapshot_id") != parent_snapshot_id
            or transition.get("kind") != "scoped_delta"
            or not isinstance(refs, list) or not refs or not isinstance(deleted, list)
            or any(not isinstance(ref, str) or "::" not in ref for ref in refs)
            or refs != sorted(set(refs))
            or transition.get("changed_files_sha256") != payload_sha256(refs)
            or not set(deleted).issubset(refs)
            or transition.get("deleted_files_sha256") != payload_sha256(deleted)):
        return False
    changed_refs = set(refs)
    return (
        all(ref in changed_scope or ref.partition("::")[2] in changed_scope
            for ref in changed_refs)
        and all(scope in changed_refs or any(ref.partition("::")[2] == scope
                                             for ref in changed_refs)
                for scope in changed_scope)
    )


def _state_flow_provider_changed(atlas: dict, changed_scope: set[str] | None,
                                 previous_results: dict | None = None) -> bool:
    if not changed_scope:
        return False
    # A provider removed by this edit no longer has a current hook/signal.
    # Its previous consumers still need a full recomputation.
    inherited = previous_results.get("transitive_hook_consumers", {}) if isinstance(previous_results, dict) else {}
    if isinstance(inherited, dict):
        for sources in inherited.values():
            for source in sources if isinstance(sources, list) else []:
                provider = source.get("provider") if isinstance(source, dict) else None
                if isinstance(provider, str) and (
                    provider in changed_scope or provider.partition("::")[2] in changed_scope
                ):
                    return True
    for pkey, pdata in atlas.items():
        if pkey == "symbols" or not isinstance(pdata, dict):
            continue
        for rel, f_data in (pdata.get("files", {}) or {}).items():
            qualified_rel = f"{pkey}::{rel}"
            if qualified_rel not in changed_scope and rel not in changed_scope:
                continue
            features = f_data.get("features", []) or []
            state_flow = f_data.get("state_flow") or summarize_state_flow_features(features)
            if (
                state_flow.get("has_zustand_store")
                or state_flow.get("query_keys")
                or state_flow.get("query_key_refs")
                or state_flow.get("query_key_dynamic")
                or state_flow.get("mutation_keys")
                or state_flow.get("mutation_key_refs")
                or state_flow.get("mutation_key_dynamic")
                or state_flow.get("client_actions")
                or _exported_hook_names(f_data)
            ):
                return True
    return False


def _load_previous_incremental_results(changed_scope: set[str] | None) -> dict | None:
    if not changed_scope:
        return None
    previous = load_json_file(RAW_DIR / "state_flow.json", {})
    if not isinstance(previous, dict) or not isinstance(previous.get("boundary_signals"), dict):
        return None
    return previous


def build_state_flow_results(
    atlas: dict,
    genome_index: dict | None = None,
    *,
    changed_files: list[str] | None = None,
    previous_results: dict | None = None,
) -> dict:
    genome_index = genome_index or {}
    stores = defaultdict(set)
    zustand_consumers = defaultdict(set)
    queries = defaultdict(set)
    mutations = defaultdict(set)
    boundary_signals = {}
    transitive_hook_consumers = {}
    hook_providers: dict[str, list[dict]] = defaultdict(list)
    file_aliases: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    changed_scope = _changed_scope(changed_files)
    incremental_mode = (
        changed_scope is not None and isinstance(previous_results, dict)
        and previous_results.get("transitive_hook_coverage") == TRANSITIVE_HOOK_COVERAGE
    )

    if incremental_mode:
        for key, target in (
            ("zustand_stores", stores),
            ("zustand_consumers", zustand_consumers),
            ("tanstack_queries", queries),
            ("tanstack_mutations", mutations),
        ):
            for rel, values in (previous_results.get(key, {}) or {}).items():
                if rel not in changed_scope and str(rel).split("::", 1)[-1] not in changed_scope:
                    target[rel].update(values or [])
        boundary_signals.update(
            {
                rel: signal
                for rel, signal in (previous_results.get("boundary_signals", {}) or {}).items()
                if rel not in changed_scope and str(rel).split("::", 1)[-1] not in changed_scope
            }
        )
        transitive_hook_consumers.update(
            {
                rel: sources
                for rel, sources in (previous_results.get("transitive_hook_consumers", {}) or {}).items()
                if rel not in changed_scope and str(rel).split("::", 1)[-1] not in changed_scope
            }
        )

    for pkey, pdata in atlas.items():
        if pkey == "symbols":
            continue
        for rel, f_data in (pdata.get("files", {}) or {}).items():
            if not isinstance(f_data, dict):
                continue
            file_aliases[pkey][rel].add(rel)
            workspace_rel = f_data.get("workspace_rel")
            if isinstance(workspace_rel, str) and workspace_rel:
                file_aliases[pkey][workspace_rel].add(rel)
            features = f_data.get("features", [])
            state_flow = f_data.get("state_flow") or summarize_state_flow_features(features)
            if not _state_flow_has_signal(state_flow):
                continue
            for hook_name in _exported_hook_names(f_data):
                hook_providers[pkey].append(
                    {
                        "project": pkey,
                        "rel": rel,
                        "qualified_rel": f"{pkey}::{rel}",
                        "hook_name": hook_name,
                        "features": list(features or []),
                        "state_flow": state_flow,
                    }
                )

    for pkey, pdata in atlas.items():
        if pkey == "symbols":
            continue
        files = pdata.get("files", {})
        for rel, f_data in files.items():
            qualified_rel = f"{pkey}::{rel}"
            if incremental_mode and qualified_rel not in changed_scope and rel not in changed_scope:
                continue
            features = list(f_data.get("features", []) or [])
            state_flow = f_data.get("state_flow") or summarize_state_flow_features(features)
            inherited_from = []
            import_rows = f_data.get("import_records", []) or []
            has_named_hook_import = any(
                isinstance(row, dict) and row.get("kind") == "named"
                and str(row.get("name") or row.get("importedName") or "").startswith("use")
                for row in import_rows
            )
            hook_calls = _bound_hook_import_calls(f_data) if has_named_hook_import else {}

            for record in import_rows:
                if not isinstance(record, dict):
                    continue
                if record.get("kind") != "named" or record.get("scope") != "top_level":
                    continue
                imported_name = str(record.get("name") or record.get("importedName") or "").strip()
                source = record.get("source")
                if not imported_name.startswith("use"):
                    continue
                raw_source = record.get("raw_source")
                if not isinstance(raw_source, str) or not raw_source:
                    continue
                call_sites, call_sites_omitted = hook_calls.get(
                    (raw_source, imported_name), ([], 0))
                if not call_sites:
                    continue
                # The Atlas resolver owns module identity. A suffix or basename
                # match can silently merge a different hook provider into this file.
                provider_files = file_aliases[pkey].get(source, set()) if isinstance(source, str) else set()
                if len(provider_files) != 1:
                    continue
                provider_rel = next(iter(provider_files))
                for provider in hook_providers.get(pkey, []):
                    if provider["qualified_rel"] == qualified_rel:
                        continue
                    if provider["rel"] != provider_rel:
                        continue
                    if provider["hook_name"] != imported_name:
                        continue
                    inherited_from.append(
                        {
                            "hook": imported_name,
                            "provider": provider["qualified_rel"],
                            "call_sites": call_sites,
                            "call_sites_omitted": call_sites_omitted,
                            "query_keys": provider["state_flow"].get("query_keys") or [],
                            "query_key_refs": provider["state_flow"].get("query_key_refs") or [],
                            "query_key_dynamic": provider["state_flow"].get("query_key_dynamic") or [],
                            "mutation_keys": provider["state_flow"].get("mutation_keys") or [],
                            "mutation_key_refs": provider["state_flow"].get("mutation_key_refs") or [],
                            "mutation_key_dynamic": provider["state_flow"].get("mutation_key_dynamic") or [],
                            "client_actions": provider["state_flow"].get("client_actions") or [],
                            "zustand_consumers": provider["state_flow"].get("zustand_consumers") or [],
                            "zustand_no_selector_calls": provider["state_flow"].get("zustand_no_selector_calls") or [],
                            "zustand_broad_selector_calls": provider["state_flow"].get("zustand_broad_selector_calls") or [],
                            "react_external_store_consumers": provider["state_flow"].get("react_external_store_consumers") or [],
                            "technologies": provider["state_flow"].get("technologies") or [],
                        }
                    )

            if inherited_from:
                # Keep provider-file hints separate. A called hook does not make
                # every query or mutation in its module belong to this file.
                transitive_hook_consumers[qualified_rel] = inherited_from

            genome_boundary = genome_index.get(qualified_rel)
            file_boundary = genome_boundary or {
                "imported_contracts": set(),
                "ui_dependencies": set(),
                "architectural_markers": set(),
                "dynamic_imports": set(),
            }

            if state_flow.get("has_zustand_store"):
                stores[qualified_rel].add("zustand_store")
            for hook_name in state_flow.get("zustand_consumers", []):
                if hook_name:
                    zustand_consumers[qualified_rel].add(str(hook_name))

            for key in state_flow.get("query_keys", []):
                queries[qualified_rel].add(str(key).strip("[]"))
            for key in state_flow.get("query_key_refs", []):
                queries[qualified_rel].add(f"ref:{str(key).strip('[]')}")
            for key in state_flow.get("query_key_dynamic", []):
                queries[qualified_rel].add(f"dynamic:{str(key).strip('[]')}")
            for key in state_flow.get("mutation_keys", []):
                mutations[qualified_rel].add(str(key).strip("[]"))
            for key in state_flow.get("mutation_key_refs", []):
                mutations[qualified_rel].add(f"ref:{str(key).strip('[]')}")
            for key in state_flow.get("mutation_key_dynamic", []):
                mutations[qualified_rel].add(f"dynamic:{str(key).strip('[]')}")
            if state_flow.get("has_tanstack_mutation") and not mutations.get(qualified_rel):
                mutations[qualified_rel].add("mutation_surface")

            has_state_flow_signal = (
                qualified_rel in stores
                or qualified_rel in zustand_consumers
                or qualified_rel in queries
                or qualified_rel in mutations
                or bool(state_flow.get("client_actions"))
            )

            if has_state_flow_signal:
                imported_contracts = sorted(file_boundary["imported_contracts"])
                transition_markers = _extract_transition_markers(features, imported_contracts)
                property_markers = _extract_property_markers(features, imported_contracts)
                boundary_signals[qualified_rel] = {
                    "genome_boundary_source_binding": (
                        "matched_project_file_hash" if genome_boundary else "unavailable"
                    ),
                    "imported_contracts": imported_contracts,
                    "ui_dependencies": sorted(file_boundary["ui_dependencies"]),
                    "architectural_markers": sorted(file_boundary["architectural_markers"]),
                    "dynamic_imports": sorted(file_boundary["dynamic_imports"]),
                    "client_actions": sorted(state_flow.get("client_actions") or []),
                    "query_key_refs": sorted(state_flow.get("query_key_refs") or []),
                    "query_key_dynamic": sorted(state_flow.get("query_key_dynamic") or []),
                    "mutation_key_refs": sorted(state_flow.get("mutation_key_refs") or []),
                    "mutation_key_dynamic": sorted(state_flow.get("mutation_key_dynamic") or []),
                    "technologies": sorted(state_flow.get("technologies") or []),
                    "react_external_store_consumers": sorted(state_flow.get("react_external_store_consumers") or []),
                    "transition_markers": transition_markers,
                    "property_markers": property_markers,
                }
                if inherited_from:
                    boundary_signals[qualified_rel]["transitive_hook_sources"] = inherited_from

    results = {
        "zustand_stores": {k: sorted(v) for k, v in stores.items()},
        "zustand_consumers": {k: sorted(v) for k, v in zustand_consumers.items()},
        "tanstack_queries": {k: sorted(v) for k, v in queries.items()},
        "tanstack_mutations": {k: sorted(v) for k, v in mutations.items()},
        "boundary_signals": boundary_signals,
        "transitive_hook_consumers": transitive_hook_consumers,
        "transitive_hook_coverage": TRANSITIVE_HOOK_COVERAGE,
    }

    by_project = defaultdict(lambda: {
        "zustand_store_files": 0,
        "query_files": 0,
        "mutation_files": 0,
        "query_count": 0,
        "mutation_count": 0,
    })
    project_keys = sorted([key for key in atlas.keys() if key != "symbols"])
    for project in project_keys:
        _ = by_project[project]
    for rel, vals in stores.items():
        project = rel.split("::", 1)[0] if "::" in rel else "UNKNOWN"
        by_project[project]["zustand_store_files"] += 1
    for rel, vals in queries.items():
        project = rel.split("::", 1)[0] if "::" in rel else "UNKNOWN"
        by_project[project]["query_files"] += 1
        by_project[project]["query_count"] += len(vals)
    for rel, vals in mutations.items():
        project = rel.split("::", 1)[0] if "::" in rel else "UNKNOWN"
        by_project[project]["mutation_files"] += 1
        by_project[project]["mutation_count"] += len(vals)
    results["by_project"] = {project: payload for project, payload in sorted(by_project.items())}

    stateful_files = sorted(set(stores.keys()) | set(zustand_consumers.keys()) | set(queries.keys()) | set(mutations.keys()))
    transition_subject_files = _transition_subject_file_set(stores, queries, mutations, boundary_signals, stateful_files)
    transition_files = 0
    property_files = 0
    for rel in transition_subject_files:
        signal = boundary_signals.get(rel, {})
        if signal.get("transition_markers") or signal.get("client_actions"):
            transition_files += 1
    for rel in stateful_files:
        signal = boundary_signals.get(rel, {})
        if signal.get("property_markers"):
            property_files += 1
    results["state_proof"] = {
        "stateful_files": len(stateful_files),
        "transition_subject_files": len(transition_subject_files),
        "transition_files": transition_files,
        "property_files": property_files,
        "transition_ratio": round((transition_files / len(transition_subject_files)) if transition_subject_files else 1.0, 3),
        "property_ratio": round((property_files / len(stateful_files)) if stateful_files else 1.0, 3),
    }
    strong_property_files = 0
    strict_property_files = 0
    for rel in stateful_files:
        signal = boundary_signals.get(rel, {})
        markers = signal.get("property_markers") or []
        strong_markers = [marker for marker in markers if marker not in WEAK_PROPERTY_MARKERS]
        strict_markers = [marker for marker in markers if marker in STRICT_PROPERTY_DRIVER_MARKERS]
        if strong_markers:
            strong_property_files += 1
            signal["property_markers_strong"] = sorted(strong_markers)
        elif markers:
            signal["property_markers_strong"] = []
        if strict_markers:
            strict_property_files += 1
            signal["property_markers_strict"] = sorted(strict_markers)
        elif markers:
            signal["property_markers_strict"] = []

    results["state_proof"]["property_files_strong"] = strong_property_files
    results["state_proof"]["property_ratio_strong"] = round((strong_property_files / len(stateful_files)) if stateful_files else 1.0, 3)
    results["state_proof"]["property_files_strict"] = strict_property_files
    results["state_proof"]["property_ratio_strict"] = round((strict_property_files / len(stateful_files)) if stateful_files else 1.0, 3)

    state_proof_by_project = {}
    for project in project_keys:
        project_files = [rel for rel in stateful_files if rel.startswith(f"{project}::")]
        project_transition_subject_files = [
            rel for rel in transition_subject_files if rel.startswith(f"{project}::")
        ]
        project_stateful = len(project_files)
        project_transition_subjects = len(project_transition_subject_files)
        project_transition = 0
        project_property = 0
        project_property_strong = 0
        project_property_strict = 0
        for rel in project_transition_subject_files:
            signal = boundary_signals.get(rel, {})
            if signal.get("transition_markers") or signal.get("client_actions"):
                project_transition += 1
        for rel in project_files:
            signal = boundary_signals.get(rel, {})
            if signal.get("property_markers"):
                project_property += 1
            if signal.get("property_markers_strong"):
                project_property_strong += 1
            if signal.get("property_markers_strict"):
                project_property_strict += 1
        state_proof_by_project[project] = {
            "stateful_files": project_stateful,
            "transition_subject_files": project_transition_subjects,
            "transition_files": project_transition,
            "property_files": project_property,
            "property_files_strong": project_property_strong,
            "property_files_strict": project_property_strict,
            "transition_ratio": round((project_transition / project_transition_subjects) if project_transition_subjects else 1.0, 3),
            "property_ratio": round((project_property / project_stateful) if project_stateful else 1.0, 3),
            "property_ratio_strong": round((project_property_strong / project_stateful) if project_stateful else 1.0, 3),
            "property_ratio_strict": round((project_property_strict / project_stateful) if project_stateful else 1.0, 3),
        }
    results["state_proof_by_project"] = state_proof_by_project

    raw_path = RAW_DIR / "state_flow.json"
    return results


def run_state_flow_scanner(changed_files=None, atlas=None):
    logger.info("Running State Flow (Zustand/TanStack) Scanner via Atlas SSOT...")

    profile_start = time.perf_counter()
    atlas, atlas_input_source = resolve_atlas_data(atlas)
    atlas = atlas if isinstance(atlas, dict) else {}
    atlas_commit = load_json_file(RAW_DIR / "atlas_commit.json", {})
    atlas_source_binding = _atlas_source_commit_binding(atlas, atlas_commit)
    atlas, _execution_scope = project_runtime_atlas(atlas)
    atlas_loaded_at = time.perf_counter()
    if not atlas:
        logger.error("Atlas payload not found. Run Atlas engine first.")
        return False

    changed_scope = _changed_scope(changed_files)
    previous_results = _load_previous_incremental_results(changed_scope)
    incremental_cache_bound = _incremental_cache_matches_transition(
        previous_results, atlas_commit, changed_scope,
        _execution_scope["analyzed_projects"], atlas_source_binding)
    if not incremental_cache_bound:
        previous_results = None
    provider_changed = _state_flow_provider_changed(atlas, changed_scope, previous_results)
    provider_checked_at = time.perf_counter()
    if provider_changed:
        previous_results = None
    mode = "incremental_consumer_update" if previous_results is not None else "full"
    genome_index = _build_genome_file_index(
        atlas, changed_scope if previous_results is not None else None)
    genome_indexed_at = time.perf_counter()
    if changed_scope:
        logger.info(
            "[STATEFLOW] mode=%s changed=%s provider_changed=%s",
            mode,
            len(changed_scope),
            provider_changed,
        )
    results = build_state_flow_results(
        atlas,
        genome_index,
        changed_files=changed_files,
        previous_results=previous_results,
    )
    results_built_at = time.perf_counter()
    profile_timings = {
        "atlas_load_seconds": round(atlas_loaded_at - profile_start, 3),
        "provider_check_seconds": round(provider_checked_at - atlas_loaded_at, 3),
        "genome_index_seconds": round(genome_indexed_at - provider_checked_at, 3),
        "build_results_seconds": round(results_built_at - genome_indexed_at, 3),
    }
    results["run_meta"] = {
        "mode": mode,
        "changed_files_count": len(changed_scope or []),
        "provider_changed": bool(provider_changed),
        "atlas_input_source": atlas_input_source,
        "atlas_snapshot_id": atlas_source_binding["snapshot_id"],
        "atlas_source_binding": atlas_source_binding["status"],
        "analyzed_source_fingerprint": source_fingerprint(atlas),
        "genome_boundary_binding": "per_occurrence_project_file_hash_or_unavailable",
        "incremental_cache_binding": (
            "validated_parent_scoped_delta" if previous_results is not None
            else "not_reused"
        ),
        "execution_scope": _execution_scope,
        "profile_timings": profile_timings,
    }
    stores = results.get("zustand_stores", {})
    queries = results.get("tanstack_queries", {})
    mutations = results.get("tanstack_mutations", {})
    boundary_signals = results.get("boundary_signals", {})

    raw_path = RAW_DIR / "state_flow.json"
    save_json_atomic(raw_path, results)
    saved_raw_at = time.perf_counter()

    md_lines = [
        "# State Flow & Data Mapping",
        "",
        "> Identifies Zustand global stores and TanStack Query/Mutation data fetching boundaries.",
        "",
        f"**Run mode:** `{mode}`",
        f"**Changed files considered:** `{len(changed_scope or [])}`",
        f"**Provider changed:** `{bool(provider_changed)}`",
        "",
        f"**Zustand Stores found:** {len(stores)} files",
        f"**Queries found:** {sum(len(v) for v in queries.values())} in {len(queries)} files",
        f"**Mutations found:** {sum(len(v) for v in mutations.values())} in {len(mutations)} files",
        f"**State transition subjects:** {results['state_proof']['transition_subject_files']} files",
        f"**State transition proof ratio:** {results['state_proof']['transition_ratio']}",
        f"**State property proof ratio:** {results['state_proof']['property_ratio']}",
        f"**State property strong proof ratio:** {results['state_proof']['property_ratio_strong']}",
        f"**State property strict proof ratio:** {results['state_proof']['property_ratio_strict']}",
        "",
        "## By Project",
        "",
        "| Project | Zustand files | Query files | Query count | Mutation files | Mutation count |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for project, payload in sorted(results["by_project"].items(), key=lambda pair: (pair[0] != "MAIN", pair[0])):
        md_lines.append(
            f"| {project_display_name(project)} [{project}] | {payload['zustand_store_files']} | {payload['query_files']} | {payload['query_count']} | {payload['mutation_files']} | {payload['mutation_count']} |"
        )

    md_lines.extend([
        "",
        "## State Proof By Project",
        "",
        "| Project | Stateful files | Transition subjects | Transition files | Transition ratio | Property files | Property ratio | Property strong files | Property strong ratio | Property strict files | Property strict ratio |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for project, payload in sorted(results["state_proof_by_project"].items(), key=lambda pair: (pair[0] != "MAIN", pair[0])):
        md_lines.append(
            f"| {project_display_name(project)} [{project}] | {payload['stateful_files']} | {payload['transition_subject_files']} | {payload['transition_files']} | {payload['transition_ratio']} | {payload['property_files']} | {payload['property_ratio']} | {payload['property_files_strong']} | {payload['property_ratio_strong']} | {payload['property_files_strict']} | {payload['property_ratio_strict']} |"
        )
    md_lines.append("")

    def _project_key(qualified_rel: str) -> str:
        return qualified_rel.split("::", 1)[0] if "::" in qualified_rel else "UNKNOWN"

    stores_by_project = defaultdict(list)
    queries_by_project = defaultdict(list)
    mutations_by_project = defaultdict(list)
    for rel in sorted(stores.keys()):
        stores_by_project[_project_key(rel)].append(rel)
    for rel in sorted(queries.keys()):
        queries_by_project[_project_key(rel)].append(rel)
    for rel in sorted(mutations.keys()):
        mutations_by_project[_project_key(rel)].append(rel)

    if stores:
        md_lines.extend(["## Zustand Stores By Project", ""])
        for project in sorted(stores_by_project.keys(), key=lambda key: (key != "MAIN", key)):
            entries = stores_by_project[project]
            md_lines.append(f"### {project_display_name(project)} [{project}] ({len(entries)})")
            for rel in entries[:80]:
                md_lines.append(f"- `{rel}`")
                signals = boundary_signals.get(rel, {})
                if signals.get("architectural_markers"):
                    md_lines.append(f"  - markers: {', '.join(signals['architectural_markers'][:5])}")
                if signals.get("imported_contracts"):
                    md_lines.append(f"  - imported contracts: {', '.join(signals['imported_contracts'][:5])}")
            if len(entries) > 80:
                md_lines.append(f"- ... and {len(entries) - 80} more")
            md_lines.append("")

    if queries:
        md_lines.extend(["## TanStack Queries By Project", ""])
        for project in sorted(queries_by_project.keys(), key=lambda key: (key != "MAIN", key)):
            entries = queries_by_project[project]
            md_lines.append(f"### {project_display_name(project)} [{project}] ({len(entries)})")
            for rel in entries[:80]:
                md_lines.append(f"#### `{rel}`")
                for key in queries[rel]:
                    md_lines.append(f"- `QueryKey: [{key}]`")
                signals = boundary_signals.get(rel, {})
                if signals.get("ui_dependencies"):
                    md_lines.append(f"- UI deps: {', '.join(signals['ui_dependencies'][:5])}")
                if signals.get("dynamic_imports"):
                    md_lines.append(f"- Dynamic imports: {', '.join(signals['dynamic_imports'][:5])}")
                if signals.get("client_actions"):
                    md_lines.append(f"- Query client actions: {', '.join(signals['client_actions'][:5])}")
                md_lines.append("")
            if len(entries) > 80:
                md_lines.append(f"- ... and {len(entries) - 80} more")
                md_lines.append("")

    if mutations:
        md_lines.extend(["## TanStack Mutations By Project", ""])
        for project in sorted(mutations_by_project.keys(), key=lambda key: (key != "MAIN", key)):
            entries = mutations_by_project[project]
            md_lines.append(f"### {project_display_name(project)} [{project}] ({len(entries)})")
            for rel in entries[:80]:
                md_lines.append(f"#### `{rel}`")
                for key in mutations[rel]:
                    md_lines.append(f"- `MutationKey: [{key}]`")
                signals = boundary_signals.get(rel, {})
                if signals.get("architectural_markers"):
                    md_lines.append(f"- Markers: {', '.join(signals['architectural_markers'][:5])}")
                if signals.get("client_actions"):
                    md_lines.append(f"- Query client actions: {', '.join(signals['client_actions'][:5])}")
                md_lines.append("")
            if len(entries) > 80:
                md_lines.append(f"- ... and {len(entries) - 80} more")
                md_lines.append("")

    md_path = REPORTS_DIR / "state_flow.md"
    save_text_atomic(md_path, "\n".join(md_lines))
    saved_report_at = time.perf_counter()
    profile_timings["save_raw_seconds"] = round(saved_raw_at - results_built_at, 3)
    profile_timings["render_and_save_report_seconds"] = round(saved_report_at - saved_raw_at, 3)
    profile_timings["total_inside_engine_seconds"] = round(saved_report_at - profile_start, 3)

    logger.info("[STATEFLOW_PROFILE] %s", json.dumps(profile_timings, ensure_ascii=False))
    logger.info("[OK] State Flow definitions generated.")
    return True


if __name__ == "__main__":
    run_state_flow_scanner()

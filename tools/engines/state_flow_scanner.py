"""
State Flow Scanner
Scans source code specifically for Zustand stores and TanStack React Query usages
to map the data flow and state consumption across the architecture.
"""

import json
import time
from collections import defaultdict

from tools.core.atlas_io import resolve_atlas_data
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import project_display_name
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.state_flow import summarize_state_flow_features


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


def _normalize_module_ref(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    if text.startswith("./"):
        text = text[2:]
    while text.startswith("../"):
        text = text[3:]
    for suffix in (".tsx", ".ts", ".jsx", ".js"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if text.endswith("/index"):
        text = text[: -len("/index")]
    return text.lower()


def _module_ref_matches(source: str, provider_rel: str) -> bool:
    src = _normalize_module_ref(source)
    target = _normalize_module_ref(provider_rel)
    if not src or not target:
        return False
    return src == target or target.endswith(f"/{src}") or src.endswith(f"/{target}")


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
        symbol_type = str(symbol.get("type") or symbol.get("canonicalSymbolType") or "")
        if name.startswith("use") and (symbol.get("exported") or symbol_type.lower() == "hook"):
            hooks.add(name)
    for export in file_data.get("exports", []) or []:
        name = str(export or "")
        if name.startswith("use"):
            hooks.add(name)
    return hooks


def _merge_state_flow(target: dict, source: dict) -> dict:
    merged = dict(target or {})
    # Store ownership is not transitive. A component importing a custom hook from a
    # store/provider module consumes state, but it does not become a store module.
    merged["has_zustand_store"] = bool(merged.get("has_zustand_store"))
    for key in (
        "query_keys",
        "query_key_refs",
        "query_key_dynamic",
        "mutation_keys",
        "mutation_key_refs",
        "mutation_key_dynamic",
        "client_actions",
        "zustand_consumers",
        "zustand_no_selector_calls",
        "zustand_broad_selector_calls",
        "react_external_store_consumers",
        "technologies",
    ):
        merged[key] = sorted(set(merged.get(key) or []) | set(source.get(key) or []))
    return merged


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


def _build_genome_file_index(changed_scope: set[str] | None = None):
    genome = load_genome_data()
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
        for occ in occs:
            workspace_rel = str(occ.get("workspace_rel") or "").replace("\\", "/").strip("/")
            file_rel = str(occ.get("file") or "").replace("\\", "/").strip("/")
            project = str(occ.get("project") or occ.get("project_key") or "").strip()
            qualified_workspace = f"{project}::{workspace_rel}" if project and workspace_rel else ""
            qualified_file = f"{project}::{file_rel}" if project and file_rel else ""
            if not _scope_matches_file_key(changed_scope, workspace_rel, file_rel, qualified_workspace, qualified_file):
                continue
            keys = {key for key in {workspace_rel, file_rel} if key}
            if workspace_rel.startswith("src/"):
                keys.add(workspace_rel[4:])
            if file_rel and not file_rel.startswith("src/"):
                keys.add(f"src/{file_rel}")
            for key in keys:
                bucket = index[key]
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


def _state_flow_provider_changed(atlas: dict, changed_scope: set[str] | None) -> bool:
    if not changed_scope:
        return False
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


def _load_previous_incremental_results(changed_scope: set[str] | None, provider_changed: bool) -> dict | None:
    if not changed_scope or provider_changed:
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
    changed_scope = _changed_scope(changed_files)
    incremental_mode = changed_scope is not None and previous_results is not None

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

            for record in f_data.get("import_records", []) or []:
                if not isinstance(record, dict):
                    continue
                imported_name = str(record.get("name") or record.get("importedName") or "").strip()
                source = str(record.get("source") or record.get("raw_source") or "").strip()
                if not imported_name.startswith("use"):
                    continue
                for provider in hook_providers.get(pkey, []):
                    if provider["qualified_rel"] == qualified_rel:
                        continue
                    if provider["hook_name"] != imported_name:
                        continue
                    if not _module_ref_matches(source, provider["rel"]):
                        continue
                    state_flow = _merge_state_flow(state_flow, provider["state_flow"])
                    features = sorted(set(features) | {f"TransitiveHook:{imported_name}", "TransitiveStateFlow"})
                    inherited_from.append(
                        {
                            "hook": imported_name,
                            "provider": provider["qualified_rel"],
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

            file_boundary = genome_index.get(rel) or genome_index.get(f_data.get("workspace_rel", "")) or {
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
                    transitive_hook_consumers[qualified_rel] = inherited_from

    results = {
        "zustand_stores": {k: sorted(v) for k, v in stores.items()},
        "zustand_consumers": {k: sorted(v) for k, v in zustand_consumers.items()},
        "tanstack_queries": {k: sorted(v) for k, v in queries.items()},
        "tanstack_mutations": {k: sorted(v) for k, v in mutations.items()},
        "boundary_signals": boundary_signals,
        "transitive_hook_consumers": transitive_hook_consumers,
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
    atlas, _execution_scope = project_runtime_atlas(atlas if isinstance(atlas, dict) else {})
    atlas_loaded_at = time.perf_counter()
    if not atlas:
        logger.error("Atlas payload not found. Run Atlas engine first.")
        return False

    changed_scope = _changed_scope(changed_files)
    provider_changed = _state_flow_provider_changed(atlas, changed_scope)
    provider_checked_at = time.perf_counter()
    previous_results = _load_previous_incremental_results(changed_scope, provider_changed)
    mode = "incremental_consumer_update" if previous_results is not None else "full"
    genome_index = _build_genome_file_index(changed_scope if previous_results is not None else None)
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

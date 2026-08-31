import os
import sys
import json
from pathlib import Path
from typing import Dict, List, Any, Set

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import CONFIG_DIR, MAIN_PROJECT_ROOT, ROOT, RAW_DIR, SKIP_DIRS, save_json_atomic
from tools.core.json_io import load_json_file
from tools.core.artifact_freshness_contract import evaluate_named_artifact_chain
from tools.core.artifact_validator import ensure_valid_payload
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.atlas_io import resolve_atlas_data
from tools.core.contextos_signal_limits import contextos_signal_limit
from tools.core.distribution_policy import is_managed_clean_mirror_path
from tools.core.language_registry import is_config_or_manifest_file
from tools.core.logger import logger
from tools.core.source_files import is_analysis_source_file
from tools.core.scoped_graph_projection import (
    build_atlas_reverse_dependency_graph,
    build_scoped_graph_advisories,
    get_transitive_dependents,
)
from tools.core.path_identity import strip_current_directory_prefix
from tools.core.changed_file_scope import (
    change_scope_evidence as _change_scope_evidence,
    get_changed_files_from_watchdog,
    get_git_changed_files,
)
from tools.core.watchdog_runtime_contract import (
    evaluate_watchdog_quant_input_evidence,
    load_watchdog_runtime_contract,
    normalize_watchdog_scope_refs,
    watchdog_artifact_identity,
    watchdog_artifact_path,
)

CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
SIGNAL_KIND_SOURCE = "source"
SIGNAL_KIND_CONFIG = "config"


def _repo_relative(path_str: str) -> str:
    value = str(path_str or "").strip().replace("\\", "/")
    if " -> " in value:
        value = value.split(" -> ", 1)[1]
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return strip_current_directory_prefix(value)


def _candidate_paths(path_str: str) -> list[Path]:
    rel = _repo_relative(path_str)
    candidates = []
    raw_path = Path(rel)
    if raw_path.is_absolute():
        candidates.append(raw_path)
    candidates.extend([ROOT / rel, CODE_MAPS_DIR / rel])
    candidates.extend([MAIN_PROJECT_ROOT / rel, MAIN_PROJECT_ROOT / "src" / rel])
    return candidates


def _existing_file_for(path_str: str) -> Path | None:
    for candidate in _candidate_paths(path_str):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        try:
            return path.relative_to(CODE_MAPS_DIR).as_posix()
        except ValueError:
            return path.as_posix()


def _signal_kind(path: Path) -> str | None:
    lowered_parts = {part.lower() for part in path.parts}
    if any(skip.lower() in lowered_parts for skip in SKIP_DIRS):
        return None
    if is_analysis_source_file(path):
        return SIGNAL_KIND_SOURCE
    name = path.name.lower()
    rel_to_codemaps = ""
    try:
        rel_to_codemaps = path.relative_to(CODE_MAPS_DIR).as_posix()
    except ValueError:
        pass
    if is_config_or_manifest_file(name):
        return SIGNAL_KIND_CONFIG
    if rel_to_codemaps.startswith("config/") and path.suffix.lower() in {".json", ".toml"}:
        return SIGNAL_KIND_CONFIG
    return None


def _filter_signal_files(paths: List[str]) -> tuple[list[dict[str, str]], dict[str, int]]:
    seen: set[str] = set()
    accepted: list[dict[str, str]] = []
    rejected = {"non_file": 0, "unsupported_kind": 0, "managed_projection": 0, "duplicate": 0}
    for raw in paths:
        resolved = _existing_file_for(raw)
        if resolved is None:
            rejected["non_file"] += 1
            continue
        if is_managed_clean_mirror_path(resolved):
            rejected["managed_projection"] += 1
            continue
        kind = _signal_kind(resolved)
        if kind is None:
            rejected["unsupported_kind"] += 1
            continue
        display = _display_path(resolved)
        if display in seen:
            rejected["duplicate"] += 1
            continue
        seen.add(display)
        accepted.append({"path": display, "kind": kind})
    return accepted, rejected


def _atlas_file_entry(atlas: dict[str, Any], target_ref: str) -> tuple[str, str, dict[str, Any]] | None:
    if "::" not in str(target_ref):
        return None
    project_key, requested_rel = str(target_ref).split("::", 1)
    project = atlas.get(project_key)
    files = project.get("files") if isinstance(project, dict) else None
    if not isinstance(files, dict):
        return None
    requested = requested_rel.replace("\\", "/").strip("/")
    direct = files.get(requested)
    if isinstance(direct, dict):
        return project_key, requested, direct
    for atlas_rel, meta in files.items():
        if not isinstance(meta, dict):
            continue
        candidates = {
            str(atlas_rel).replace("\\", "/").strip("/"),
            str(meta.get("workspace_rel") or "").replace("\\", "/").strip("/"),
            str(meta.get("repo_relative_path") or "").replace("\\", "/").strip("/"),
            str(meta.get("target_ref") or "").split("::", 1)[-1].replace("\\", "/").strip("/"),
        }
        if requested in candidates:
            return project_key, str(atlas_rel).replace("\\", "/").strip("/"), meta
    return None


def _filter_scoped_signal_files(
    paths: List[str],
    atlas: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    seen: set[str] = set()
    accepted: list[dict[str, str]] = []
    rejected = {"non_file": 0, "unsupported_kind": 0, "managed_projection": 0, "duplicate": 0}
    for target_ref in normalize_watchdog_scope_refs(paths):
        resolved = _atlas_file_entry(atlas, target_ref)
        if resolved is None:
            rejected["non_file"] += 1
            continue
        project_key, atlas_rel, meta = resolved
        repo_relative = str(meta.get("repo_relative_path") or meta.get("workspace_rel") or atlas_rel).replace("\\", "/")
        project_root = Path(str((atlas.get(project_key) or {}).get("project", {}).get("root") or ROOT))
        source_path = project_root / atlas_rel
        if is_managed_clean_mirror_path(source_path):
            rejected["managed_projection"] += 1
            continue
        kind = _signal_kind(source_path)
        if kind is None:
            rejected["unsupported_kind"] += 1
            continue
        node_key = f"{project_key}::{atlas_rel}"
        if node_key in seen:
            rejected["duplicate"] += 1
            continue
        seen.add(node_key)
        accepted.append(
            {
                "path": repo_relative,
                "kind": kind,
                "node_key": node_key,
                "atlas_rel_path": atlas_rel,
                "project_key": project_key,
            }
        )
    return accepted, rejected

def resolve_node_name(changed_file: str, circular_deps_data: Dict[str, Any]) -> str:
    """Maps a changed file relative path to the exact circular_deps node name (PROJECT::path)."""
    norm = changed_file.lower().replace("\\", "/")
    nodes = circular_deps_data.get("nodes", {})
    stripped_norm = norm[4:] if norm.startswith("src/") else norm
    ordered_nodes = sorted(
        nodes.keys(),
        key=lambda node: 0 if str(node).startswith("MAIN::") else 1,
    )
    
    # 1. Direct exact suffix match
    for node in ordered_nodes:
        if "::" in node:
            proj, rel = node.split("::", 1)
            norm_rel = rel.lower().replace("\\", "/")
            if norm_rel == norm or norm_rel == stripped_norm:
                return node
        else:
            if node.lower().replace("\\", "/") == norm:
                return node

    # 2. MAIN-prioritized suffix match for watchdog-shortened paths.
    for node in ordered_nodes:
        if "::" in node:
            _proj, rel = node.split("::", 1)
            norm_rel = rel.lower().replace("\\", "/")
            if norm_rel.endswith("/" + norm) or norm.endswith("/" + norm_rel) or norm_rel.endswith("/" + stripped_norm):
                return node
                
    # 3. General substring match as fallback
    for node in ordered_nodes:
        if norm in node.lower().replace("\\", "/"):
            return node
            
    return f"MAIN::{changed_file}"

def build_reverse_dependency_graph(circular_deps_data: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Builds a reverse dependency map (target -> set of sources) from graph edges."""
    rev_adj = {}
    edges = circular_deps_data.get("edges", [])
    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if src and tgt:
            if tgt not in rev_adj:
                rev_adj[tgt] = set()
            rev_adj[tgt].add(src)
    return rev_adj


def _node_to_relative_path(node: str) -> str:
    if "::" in str(node):
        return str(node).split("::", 1)[1]
    return str(node)


def _rank_halo_nodes(
    nodes: list[str],
    blast_radius_data: list[dict[str, Any]],
    limit: int = 3,
    *,
    score_status: str = "available",
) -> list[dict[str, Any]]:
    """Return the hottest first-ring neighbors for ContextOS focus expansion."""
    score_by_file = {
        str(item.get("file")): float(item.get("total_impact_score", 0.0) or 0.0)
        for item in blast_radius_data
        if isinstance(item, dict)
    }
    ranked = sorted(
        {str(node) for node in nodes if node},
        key=lambda node: (score_by_file.get(node, 0.0), node),
        reverse=True,
    )
    halo: list[dict[str, Any]] = []
    for node in ranked[: max(0, limit)]:
        halo.append(
            {
                "node_key": node,
                "relative_path": _node_to_relative_path(node),
                "halo_rank": len(halo) + 1,
                "impact_score": score_by_file.get(node) if score_status == "available" else None,
                "impact_score_status": score_status,
                "reason": "direct_dependent_of_active_focus",
            }
        )
    return halo


def _signal_breadcrumb(
    *,
    changed_file: str,
    node_key: str,
    source_mode: str,
    entry_kind: str,
    direct_count: int,
    transitive_count: int,
    violation_count: int,
    cycle_count: int,
    broad_evidence_deferred: bool = False,
) -> list[str]:
    breadcrumbs = [
        f"`{changed_file}` is hot because it was observed by ContextOS source mode `{source_mode}`.",
    ]
    if entry_kind == SIGNAL_KIND_CONFIG:
        breadcrumbs.append("It is a config signal, so governance/runtime behavior may change without source edits.")
    if direct_count:
        breadcrumbs.append(f"It has {direct_count} direct dependent file(s); inspect the first-ring halo before editing further.")
    if transitive_count:
        breadcrumbs.append(f"It reaches {transitive_count} transitive dependent file(s), so regression scope is wider than the edited file.")
    if violation_count:
        breadcrumbs.append(f"It already has {violation_count} active doctrine violation(s) in the audit report.")
    if cycle_count:
        breadcrumbs.append(f"It participates in {cycle_count} circular dependency cycle(s), lowering merge confidence.")
    elif broad_evidence_deferred:
        breadcrumbs.append(
            "Scoped SCC membership and a dependency-only impact lower bound are available; "
            "canonical cycle enumeration and boundary-aware Blast Radius remain deferred."
        )
    if node_key.startswith("MAIN::"):
        breadcrumbs.append("It belongs to the host project focus lane.")
    return breadcrumbs


def _signal_actionability(
    *,
    entry_kind: str,
    impact_score: float,
    direct_count: int,
    transitive_count: int,
    violation_count: int,
    cycle_count: int,
    evidence_status: str = "PASS",
    unavailable_inputs: tuple[str, ...] = (),
    broad_evidence_deferred: bool = False,
) -> dict[str, Any]:
    """Classify a hot signal into an agent-friendly action lane from graph facts."""
    if evidence_status != "PASS":
        missing = ", ".join(unavailable_inputs) or "required upstream evidence"
        return {
            "lane": "evidence_refresh_required",
            "priority": "high",
            "reason": f"Risk cannot be localized because {missing} is unavailable or invalid.",
        }
    if entry_kind == SIGNAL_KIND_CONFIG:
        return {
            "lane": "governance_recheck",
            "priority": "high",
            "reason": "Configuration changes can alter doctrine, discovery, or runtime behavior across the workspace.",
        }
    if cycle_count or violation_count:
        return {
            "lane": "act_now",
            "priority": "high",
            "reason": "The active file intersects an existing doctrine violation or circular dependency cycle.",
        }
    if direct_count >= 5 or transitive_count >= 20 or impact_score >= 10:
        return {
            "lane": "review_before_edit",
            "priority": "medium",
            "reason": (
                "Fresh Atlas shows a wide dependency halo; broad blast-score proof remains deferred."
                if broad_evidence_deferred
                else "The active file has a wide dependency halo; inspect blast radius before widening the change."
            ),
        }
    if direct_count or transitive_count or impact_score > 0:
        return {
            "lane": "localized_review",
            "priority": "low",
            "reason": "The active file has limited known dependents; keep review scoped to the first-ring halo.",
        }
    if broad_evidence_deferred:
        return {
            "lane": "clean_in_current_scope",
            "priority": "low",
            "reason": "No current Atlas dependents or scoped Audit violations were found; broad cycle and blast proof remains deferred.",
        }
    return {
        "lane": "local_only",
        "priority": "low",
        "reason": "No known dependents, cycles, or active violations were found for this signal.",
    }


def _input_shape_status(artifact: str, payload: Any) -> str:
    if not isinstance(payload, dict):
        return "invalid"
    required_lists = {
        "circular_deps": ("edges", "cycles"),
        "blast_radius": ("blast_radius",),
        "audit_report": ("violations",),
    }
    fields = required_lists.get(artifact, ())
    return "valid" if all(isinstance(payload.get(field), list) for field in fields) else "invalid"


def _quant_input_evidence(payloads: dict[str, Any]) -> tuple[str, list[str], dict[str, Any]]:
    chain = evaluate_named_artifact_chain("contextos_quant_input_chain", RAW_DIR)
    dependency_chain = evaluate_named_artifact_chain("contextos_quant_dependency_graph_chain", RAW_DIR)
    audit_chain = evaluate_named_artifact_chain("contextos_quant_audit_chain", RAW_DIR)
    freshness_chains = [dependency_chain, audit_chain]
    stale_artifacts: set[str] = set()
    for freshness_chain in freshness_chains:
        ordered = [str(item) for item in freshness_chain.get("ordered_artifacts", [])]
        for edge in freshness_chain.get("stale_edges", []):
            if not isinstance(edge, dict):
                continue
            consumer = str(edge.get("consumer") or "")
            if consumer in ordered:
                stale_artifacts.update(ordered[ordered.index(consumer) :])
    rows = {
        str(row.get("artifact")): row
        for row in chain.get("artifacts", [])
        if isinstance(row, dict) and row.get("artifact")
    }
    evidence: dict[str, Any] = {}
    unavailable: list[str] = []
    for artifact in chain.get("required_artifacts", []):
        name = str(artifact)
        row = rows.get(name, {})
        shape_status = _input_shape_status(name, payloads.get(name))
        available = bool(row.get("exists")) and shape_status == "valid" and name not in stale_artifacts
        evidence[name] = {
            "status": "available" if available else "unavailable",
            "source": str(row.get("source") or "missing"),
            "shape_status": shape_status,
            "payload_bytes": int(row.get("payload_bytes") or 0),
            "updated_at": str(row.get("updated_at") or ""),
        }
        if not available:
            unavailable.append(name)
    if chain.get("missing_contract"):
        unavailable.append("contextos_quant_input_chain")
    for freshness_chain in freshness_chains:
        if freshness_chain.get("missing_contract") or freshness_chain.get("missing_artifacts"):
            unavailable.append(str(freshness_chain.get("id") or "contextos_quant_freshness_chain"))
    return ("PASS" if not unavailable else "PARTIAL"), sorted(set(unavailable)), evidence


def _trim_sequence(values, limit: int):
    items = list(values or [])
    return items[:limit], max(0, len(items) - limit)


def _trim_cycles(cycles, max_cycles: int, max_cycle_nodes: int):
    trimmed = []
    for cycle in list(cycles or [])[:max_cycles]:
        if isinstance(cycle, list):
            trimmed.append(cycle[:max_cycle_nodes])
        else:
            trimmed.append(cycle)
    return trimmed, max(0, len(cycles or []) - max_cycles)

def run_quant_engine(
    focus_source: str = "watchdog_first",
    *,
    focus_files: list[str] | None = None,
    atlas: dict[str, Any] | None = None,
) -> bool:
    """
    Executes Step 58 of SAGE: Calculates L1 focus, resolves L2 dependencies,
    integrates audit violations/cycles, and commits atomic output signals.
    """
    logger.info("Initializing ContextOS Quant Engine (Surgical Signals Sorter)...")
    active_signal_cap = contextos_signal_limit("active_signals")
    direct_dependents_cap = contextos_signal_limit("direct_dependents")
    transitive_dependents_cap = contextos_signal_limit("transitive_dependents")
    active_violations_cap = contextos_signal_limit("active_violations")
    circular_cycles_cap = contextos_signal_limit("circular_cycles")
    cycle_nodes_cap = contextos_signal_limit("cycle_nodes")
    
    direct_scope = focus_files is not None
    circular_deps_data: dict[str, Any] = {}
    blast_radius_data: list[dict[str, Any]] = []
    blast_raw: dict[str, Any] = {}
    audit_data: dict[str, Any] = {}
    atlas_data: dict[str, Any] = {}
    deferred_inputs: list[str] = []
    scope_validation: dict[str, Any] = {}
    watchdog_changed: list[str] = []
    git_changed: list[str] = []
    change_scope_evidence: dict[str, Any] = {}
    evidence_scope = "broad_artifact_projection"
    scoped_graph_advisories: dict[str, Any] | None = None
    scoped_graph_by_node: dict[str, dict[str, Any]] = {}

    if direct_scope:
        watchdog_contract = load_watchdog_runtime_contract()
        evidence_scope = str(watchdog_contract["evidence_scope"])
        l1_candidates = normalize_watchdog_scope_refs(focus_files)
        source_mode = "direct_pipeline_scope"
        atlas_data, atlas_input_source = resolve_atlas_data(atlas)
        audit_path = watchdog_artifact_path("audit")
        try:
            audit_data = load_json_file(audit_path, {})
        except Exception as exc:
            logger.error("Failed to read current scoped watchdog Audit: %s", exc)
            audit_data = {}
        (
            input_evidence_status,
            unavailable_inputs,
            deferred_inputs,
            input_evidence,
            scope_validation,
        ) = evaluate_watchdog_quant_input_evidence(atlas_data, audit_data, l1_candidates)
        change_scope_evidence = _change_scope_evidence(
            "available",
            "direct_pipeline_scope",
            l1_candidates,
        )
        input_evidence["change_scope"] = change_scope_evidence
        filtered_files, rejected_counts = _filter_scoped_signal_files(l1_candidates, atlas_data)
        rev_adj, graph_nodes = build_atlas_reverse_dependency_graph(atlas_data)
        scoped_graph_advisories = build_scoped_graph_advisories(
            atlas_data,
            [str(item.get("node_key") or "") for item in filtered_files],
        )
        scoped_graph_advisories["artifact_identity"] = watchdog_artifact_identity()
        scoped_graph_by_node = {
            str(row.get("node_key") or ""): row
            for row in scoped_graph_advisories.get("advisories", [])
            if isinstance(row, dict) and row.get("node_key")
        }
        broad_evidence_deferred = True
        logger.info(
            "ContextOS Quant direct scope files=%s accepted=%s audit_scope=%s broad_inputs_deferred=%s",
            len(l1_candidates),
            len(filtered_files),
            scope_validation.get("scope_status"),
            ",".join(deferred_inputs),
        )
    else:
        atlas_input_source = "not_required_for_broad_artifact_projection"
        deps_path = RAW_DIR / "circular_deps.json"
        blast_path = RAW_DIR / "blast_radius.json"
        audit_path = RAW_DIR / "audit_report.json"
        try:
            circular_deps_data = load_json_file(deps_path, {})
        except Exception as exc:
            logger.error("Failed to read circular_deps.json: %s", exc)
        try:
            blast_raw = load_json_file(blast_path, {})
            blast_radius_data = blast_raw.get("blast_radius", []) if isinstance(blast_raw, dict) else []
        except Exception as exc:
            logger.error("Failed to read blast_radius.json: %s", exc)
        try:
            audit_data = load_json_file(audit_path, {})
        except Exception as exc:
            logger.error("Failed to read audit_report.json: %s", exc)

        input_evidence_status, unavailable_inputs, input_evidence = _quant_input_evidence(
            {
                "circular_deps": circular_deps_data,
                "blast_radius": blast_raw if isinstance(blast_raw, dict) else {},
                "audit_report": audit_data,
            }
        )
        watchdog_changed, watchdog_evidence = get_changed_files_from_watchdog()
        focus_source = str(focus_source or "watchdog_first").lower()
        if focus_source == "watchdog":
            l1_candidates = sorted(set(watchdog_changed))
            source_mode = "watchdog_only"
            change_scope_evidence = watchdog_evidence
        elif focus_source == "git":
            git_changed, git_evidence = get_git_changed_files()
            l1_candidates = sorted(set(git_changed))
            source_mode = "git_only"
            change_scope_evidence = git_evidence
        elif focus_source == "merged":
            git_changed, git_evidence = get_git_changed_files()
            l1_candidates = sorted(set(watchdog_changed) | set(git_changed))
            source_mode = "watchdog_git_merged"
            merged_available = watchdog_evidence.get("status") == "available" and git_evidence.get("status") == "available"
            change_scope_evidence = _change_scope_evidence(
                "available" if merged_available else "unavailable",
                "watchdog_and_git_changed_file_evidence",
                l1_candidates,
                scope_status="complete" if merged_available else "partial",
                watchdog=watchdog_evidence,
                git=git_evidence,
            )
        else:
            if watchdog_changed and watchdog_evidence.get("status") == "available":
                l1_candidates = sorted(set(watchdog_changed))
                source_mode = "watchdog_primary"
                change_scope_evidence = watchdog_evidence
            else:
                git_changed, git_evidence = get_git_changed_files()
                l1_candidates = sorted(set(git_changed))
                source_mode = "git_fallback"
                change_scope_evidence = git_evidence
        input_evidence["change_scope"] = change_scope_evidence
        if change_scope_evidence.get("status") != "available":
            unavailable_inputs.append("change_scope")
            input_evidence_status = "PARTIAL"
        filtered_files, rejected_counts = _filter_signal_files(l1_candidates)
        rev_adj = build_reverse_dependency_graph(circular_deps_data)
        graph_nodes = set((circular_deps_data.get("nodes") or {}).keys())
        broad_evidence_deferred = False

    if unavailable_inputs:
        logger.warning(
            "ContextOS Quant evidence is partial; scoped actionability disabled until refreshed: %s",
            ", ".join(unavailable_inputs),
        )
    if not filtered_files and change_scope_evidence.get("status") == "available":
        logger.info("No modified files detected in watchdog or git. ContextOS entering dormant scan mode.")
    elif not filtered_files:
        logger.warning("Changed-file scope is unavailable; ContextOS cannot claim a clean dormant state.")

    active_signals = []
    
    for entry in filtered_files:
        changed_file = entry["path"]
        node_key = entry.get("node_key") or resolve_node_name(changed_file, circular_deps_data)
        
        # 4. Resolve L2 Blast Radius (direct and transitive)
        direct_dependents = sorted(list(rev_adj.get(node_key, set())))
        transitive_dependents = sorted(list(get_transitive_dependents(node_key, rev_adj)))
        
        # Pull blast radius pre-computed score if available
        scoped_graph_row = scoped_graph_by_node.get(node_key, {}) if direct_scope else {}
        scoped_impact = scoped_graph_row.get("impact_advisory") if isinstance(scoped_graph_row, dict) else None
        impact_score = (
            float(scoped_impact.get("dependency_score", 0.0) or 0.0)
            if isinstance(scoped_impact, dict)
            else (None if direct_scope else 0.0)
        )
        if not direct_scope:
            for blast_entry in blast_radius_data:
                if blast_entry.get("file") == node_key:
                    impact_score = blast_entry.get("total_impact_score", 0.0)
                    break
                
        # 5. Filter Active Governance Doctrine Violations
        active_violations = []
        for viol in audit_data.get("violations", []):
            viol_file = str(viol.get("file", "")).replace("\\", "/")
            if direct_scope:
                violation_matches = (
                    str(viol.get("project") or "") == str(entry.get("project_key") or "")
                    and viol_file == str(entry.get("atlas_rel_path") or "")
                )
            else:
                violation_matches = changed_file in viol_file or viol_file in changed_file
            if violation_matches:
                active_violations.append({
                    "rule": viol.get("rule", "unknown"),
                    "severity": viol.get("severity", "enforced"),
                    "message": viol.get("message") or viol.get("detail", ""),
                })
                
        # 6. Extract Active Circular Cycles
        circular_cycles = []
        if direct_scope:
            membership = scoped_graph_row.get("cycle_membership") if isinstance(scoped_graph_row, dict) else None
            if isinstance(membership, dict) and membership.get("status") == "member":
                circular_cycles.append(list(membership.get("witness_chain") or []))
        else:
            for cycle in circular_deps_data.get("cycles", []):
                chain = cycle.get("chain", [])
                if node_key in chain:
                    circular_cycles.append(chain)
                
        focus_halo = _rank_halo_nodes(
            direct_dependents,
            blast_radius_data,
            limit=3,
            score_status="deferred_broad_proof" if broad_evidence_deferred else "available",
        )
        breadcrumbs = _signal_breadcrumb(
            changed_file=changed_file,
            node_key=node_key,
            source_mode=source_mode,
            entry_kind=entry["kind"],
            direct_count=len(direct_dependents),
            transitive_count=len(transitive_dependents),
            violation_count=len(active_violations),
            cycle_count=len(circular_cycles),
            broad_evidence_deferred=broad_evidence_deferred,
        )
        if unavailable_inputs:
            breadcrumbs.append(
                "Risk evidence is partial; refresh " + ", ".join(unavailable_inputs) + " before treating this signal as localized."
            )
        actionability = _signal_actionability(
            entry_kind=entry["kind"],
            impact_score=float(impact_score or 0.0),
            direct_count=len(direct_dependents),
            transitive_count=len(transitive_dependents),
            violation_count=len(active_violations),
            cycle_count=len(circular_cycles),
            evidence_status=input_evidence_status,
            unavailable_inputs=tuple(unavailable_inputs),
            broad_evidence_deferred=broad_evidence_deferred,
        )

        direct_preview, direct_omitted = _trim_sequence(direct_dependents, direct_dependents_cap)
        transitive_preview, transitive_omitted = _trim_sequence(transitive_dependents, transitive_dependents_cap)
        violation_preview, violation_omitted = _trim_sequence(active_violations, active_violations_cap)
        cycle_preview, cycle_omitted = _trim_cycles(circular_cycles, circular_cycles_cap, cycle_nodes_cap)
        signal_project_key = str(
            entry.get("project_key")
            or (node_key.split("::", 1)[0] if "::" in node_key else "MAIN")
        )

        # Synthesize active signal context. Counts stay exact; large lists are capped
        # so ContextOS remains a compact agent context artifact instead of a graph dump.
        active_signals.append({
            "node_key": node_key,
            "relative_path": changed_file,
            "target_ref": f"{signal_project_key}::{changed_file}",
            "signal_kind": entry["kind"],
            "impact_score": impact_score,
            "impact_score_status": (
                "scoped_dependency_lower_bound"
                if direct_scope and isinstance(scoped_impact, dict)
                else ("deferred_broad_proof" if broad_evidence_deferred else "available")
            ),
            "direct_dependents_count": len(direct_dependents),
            "transitive_dependents_count": len(transitive_dependents),
            "direct_dependents": direct_preview,
            "direct_dependents_omitted": direct_omitted,
            "transitive_dependents": transitive_preview,
            "transitive_dependents_omitted": transitive_omitted,
            "focus_halo": focus_halo,
            "reasoning_breadcrumbs": breadcrumbs,
            "signal_actionability": actionability,
            "active_violations": violation_preview,
            "active_violations_omitted": violation_omitted,
            "circular_cycles": cycle_preview,
            "circular_cycles_omitted": cycle_omitted,
            "circular_cycles_status": (
                "scoped_scc_membership"
                if direct_scope and scoped_graph_row.get("status") == "available"
                else ("deferred_broad_proof" if broad_evidence_deferred else "available")
            ),
            "risk_claim_boundary": (
                "scoped_dependency_scc_witness_and_active_violation_context"
                if direct_scope
                else "broad_artifact_risk_projection"
            ),
        })

    active_signals.sort(
        key=lambda item: (
            item.get("signal_kind") != SIGNAL_KIND_SOURCE,
            -float(item.get("impact_score", 0.0) or 0.0),
            -int(item.get("direct_dependents_count", 0) or 0),
            str(item.get("relative_path", "")),
        )
    )
    accepted_files_before_cap = len(active_signals)
    active_signals_omitted = max(0, accepted_files_before_cap - active_signal_cap)
    if active_signals_omitted:
        active_signals = active_signals[:active_signal_cap]

    all_halo_nodes = {
        halo.get("node_key")
        for signal in active_signals
        for halo in signal.get("focus_halo", [])
        if isinstance(halo, dict) and halo.get("node_key")
    }
    total_transitive = sum(int(signal.get("transitive_dependents_count", 0) or 0) for signal in active_signals)
    total_graph_nodes = len(graph_nodes)
    scene_pivot = bool(
        len(active_signals) >= 25
        or (total_graph_nodes and total_transitive / max(total_graph_nodes, 1) >= 0.8)
    )
        
    # Write dynamic persistent ContextOS output
    lineage_atlas = atlas_data
    lineage_atlas_source = atlas_input_source
    if not lineage_atlas:
        lineage_atlas, lineage_atlas_source = resolve_atlas_data(atlas)

    result = {
        "meta": {
            "kind": "contexts_active_signals",
            "version": "v1",
            "generator": "tools.engines.quant_engine",
            "source_mode": source_mode,
            "input_evidence_status": input_evidence_status,
            "evidence_scope": evidence_scope,
            "broad_proof_deferred": broad_evidence_deferred,
            "atlas_input_source": atlas_input_source,
            "lineage_atlas_source": lineage_atlas_source,
            "current_change_scope": "bounded" if direct_scope else "unknown",
            "current_turn_claim": "supported" if direct_scope else "not_established",
            "signal_origin": str(change_scope_evidence.get("source") or source_mode),
        },
        "summary": {
            "candidate_files": len(l1_candidates),
            "watchdog_candidates": len(l1_candidates) if direct_scope else len(watchdog_changed),
            "git_candidates": len(git_changed),
            "accepted_files": len(active_signals),
            "accepted_files_before_cap": accepted_files_before_cap,
            "active_signals_omitted": active_signals_omitted,
            "active_signal_cap": active_signal_cap,
            "rejected_files": rejected_counts,
            "source_files": sum(1 for item in active_signals if item.get("signal_kind") == SIGNAL_KIND_SOURCE),
            "config_files": sum(1 for item in active_signals if item.get("signal_kind") == SIGNAL_KIND_CONFIG),
            "halo_files": len(all_halo_nodes),
            "total_transitive_dependents": total_transitive,
            "is_scene_pivot": scene_pivot,
            "scene_pivot_reason": (
                "focus churn exceeded ContextOS pivot threshold"
                if scene_pivot
                else "focus remains inside the current surgical scene"
            ),
            "input_evidence_status": input_evidence_status,
            "unavailable_inputs": unavailable_inputs,
            "deferred_inputs": deferred_inputs,
        },
        "input_evidence": input_evidence,
        "changed_files_count": len(active_signals),
        "active_signals": active_signals
    }
    
    output_path = RAW_DIR / "signals.json"
    ensure_valid_payload("signals", result)
    save_json_atomic(output_path, result)
    write_current_atlas_lineage(
        artifact_id="signals",
        producer="tools.engines.quant_engine",
        artifact_payload=result,
        atlas=lineage_atlas,
    )
    if direct_scope and scoped_graph_advisories is not None:
        ensure_valid_payload("watchdog_graph_advisories", scoped_graph_advisories)
        save_json_atomic(watchdog_artifact_path("graph_advisories"), scoped_graph_advisories)
        logger.info(
            "Scoped graph advisories committed: nodes=%s cycles=%s dead_code_candidates=%s claim=advisory_only",
            scoped_graph_advisories["summary"]["available_nodes"],
            scoped_graph_advisories["summary"]["cycle_members"],
            scoped_graph_advisories["summary"]["dead_code_candidates"],
        )
    logger.info(
        "Surgically committed %s active Focus signals to %s successfully.",
        len(active_signals),
        output_path,
    )
    return True

if __name__ == "__main__":
    success = run_quant_engine()
    sys.exit(0 if success else 1)

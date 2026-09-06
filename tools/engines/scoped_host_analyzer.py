import json
import re
import sys
import hashlib
import time
from datetime import datetime
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, SRC, save_json_atomic, save_text_atomic
from tools.core.host_policy import classify_host_policy, glob_match, load_host_policy
from tools.core.json_io import load_json_file
from tools.core.fractal_io import load_fractal_map_data
from tools.core.logger import logger
from tools.core.path_engine import to_posix_path
from tools.core.atlas_io import load_atlas_data
from tools.core.genome_io import load_genome_data
from tools.engines.decision_evidence import _is_trusted_candidate, _get_structural_signature


def _scoped_host_limits() -> dict:
    policy = load_json_file(CONFIG_DIR / "pipeline_execution_policy.json", {})
    guidance = ((policy.get("step_profile_guidance") or {}) if isinstance(policy, dict) else {}).get("scopedhostanalyzer", {})
    if not isinstance(guidance, dict):
        guidance = {}
    return {
        "max_scope_files": max(1, int(guidance.get("max_scope_files") or 250)),
        "max_candidate_rows": max(1, int(guidance.get("max_candidate_rows") or 500)),
        "heartbeat_rows": max(1, int(guidance.get("heartbeat_rows") or 25)),
        "too_broad_scope_behavior": guidance.get("too_broad_scope_behavior") or "fail_closed_with_narrow_scope_guidance",
    }

def _tokenize(value: str):
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value or "")
    normalized = re.sub(r"[^A-Za-z0-9]+", " ", normalized).strip().lower()
    return {token for token in normalized.split() if token and len(token) > 2}


def _layer_family(layer: str, rel_path: str):
    layer = layer or ""
    rel_path = rel_path or ""
    if "shared/hooks" in layer or "hooks/" in rel_path:
        return "hooks"
    if layer == "application" or rel_path.startswith("application/"):
        return "application"
    if layer == "entities" or rel_path.startswith("entities/"):
        return "entities"
    if layer.startswith("domain") or rel_path.startswith("domain/"):
        return "domain"
    if layer == "infra" or rel_path.startswith("infra/"):
        return "infra"
    if layer == "features" or rel_path.startswith("features/") or rel_path.startswith("widgets/") or rel_path.startswith("pages/"):
        return "ui"
    if rel_path.startswith("integration/"):
        return "integration"
    return "other"


def _atlas_scope_files(scope_root: Path, atlas: dict) -> List[str]:
    try:
        scope_prefix = scope_root.resolve().relative_to(SRC.parent.resolve()).as_posix().strip("/")
    except (ValueError, OSError):
        return []
    files_payload = ((atlas or {}).get("MAIN", {}) or {}).get("files", {})
    if not isinstance(files_payload, dict):
        return []
    rows: List[str] = []
    for file_key, info in files_payload.items():
        if not isinstance(info, dict):
            info = {}
        full_rel = str(info.get("workspace_rel") or info.get("repo_relative_path") or file_key or "").replace("\\", "/").strip("/")
        if not full_rel.startswith(scope_prefix.rstrip("/") + "/"):
            continue
        rel_path = full_rel[len(scope_prefix.rstrip("/") + "/") :]
        if rel_path and Path(rel_path).suffix in {".ts", ".tsx"}:
            rows.append(rel_path)
    return sorted(set(rows))


def _host_inventory(scope_root: Path, fractal: dict, atlas: dict):
    inventory = []
    scope_files = _atlas_scope_files(scope_root, atlas)
    for rel_path in scope_files:
        if "__tests__" in rel_path or rel_path.endswith(".test.ts") or rel_path.endswith(".test.tsx") or rel_path.endswith(".spec.ts"):
            continue
        stem = Path(rel_path).stem

        try:
            full_rel = (scope_root / rel_path).resolve().relative_to(SRC.parent.resolve()).as_posix()
        except (ValueError, TypeError, OSError):
            full_rel = rel_path

        ast_signature = _get_structural_signature(fractal, "MAIN", full_rel)

        if not ast_signature:
            ast_signature = _tokenize(stem) # Fallback to filename tokens

        inventory.append(
            {
                "rel_path": rel_path,
                "stem": stem,
                "signature": ast_signature,
                "family": _layer_family("", rel_path),
            }
        )
    return inventory


def _semantic_neighbors(row, host_inventory, scope_path_str: str, fractal: dict, limit=3):
    candidate_name = row.get("name", "")
    candidate_family = _layer_family(row.get("target_layer"), _relative_target(row.get("target_path_suggestion", ""), scope_path_str))
    
    # Get donor AST signature
    candidate_sig = _get_structural_signature(fractal, row.get("source_project"), row.get("source_path", ""))
    if not candidate_sig:
        candidate_sig = _tokenize(candidate_name) # Fallback if AST empty

    neighbors = []
    if not candidate_sig:
        return neighbors

    for host in host_inventory:
        host_sig = host["signature"]
        union_len = len(candidate_sig | host_sig) or 1
        intersection = candidate_sig & host_sig
        
        if not intersection:
            continue
            
        jaccard = len(intersection) / union_len
        score = jaccard * 10.0  # normalize structural weight
        
        if candidate_family == host["family"]:
            score += 2.0
        if host["stem"].lower() == candidate_name.lower():
            score += 4.0
        elif candidate_name.lower() in host["stem"].lower() or host["stem"].lower() in candidate_name.lower():
            score += 2.0
            
        if score < 2.0:
            continue
            
        neighbors.append(
            {
                "path": host["rel_path"],
                "stem": host["stem"],
                "family": host["family"],
                "shared_ast": sorted(intersection),
                "jaccard": round(jaccard, 3),
                "score": round(score, 1),
            }
        )

    neighbors.sort(key=lambda item: (-item["score"], item["path"]))
    return neighbors[:limit]


def _relative_target(target_path: str, scope_path_str: str):
    normalized = str(target_path or "").replace("\\", "/")
    if not str(scope_path_str or "").strip("/"):
        return normalized
    
    # scope_path_str might look like "src/[module_root]/03-writing"
    # Or just "src/features/Payment". We make sure we match it regardless of trailing slash.
    base_prefix = scope_path_str.replace("\\", "/").strip("/")
    if not base_prefix.endswith("/"):
        base_prefix += "/"

    if normalized.startswith(base_prefix):
        return normalized[len(base_prefix):]
    # If not starting with scope, then the scope matching itself didn't filter correctly,
    # but we will just return normalized for safety.
    return normalized


def _candidate_occurrence(row, genome_index: dict):
    key = (
        row.get("source_project"),
        to_posix_path(row.get("source_path", "")),
        row.get("name"),
    )
    return genome_index.get(key, {})


def _decide_lane(row, host_policy, exists_in_main, neighbors, occurrence):
    review_reasons = set(row.get("review_reasons", []))
    readiness = row.get("merge_readiness", "manual_review")
    action = row.get("action")
    target_layer = row.get("target_layer")
    confidence = float(row.get("confidence", 0) or 0)
    trusted = _is_trusted_candidate(row)
    member_side_effects = set((occurrence or {}).get("member_side_effect_markers") or [])
    member_side_effect_imports = sorted(list(set((occurrence or {}).get("member_side_effect_imports") or [])))
    member_side_effect_calls = sorted(list(set((occurrence or {}).get("member_side_effect_calls") or [])))
    side_effect_source = member_side_effect_imports[0] if member_side_effect_imports else ""
    side_effect_call = member_side_effect_calls[0] if member_side_effect_calls else ""

    if action == "keep_main":
        return "host_retained", "Main project already wins here; donor intake is not required."

    if host_policy == "host_locked":
        return "blocked_host_locked", "Target path is part of the host boundary and should not be replaced."

    if not trusted:
        target_path = str(row.get("target_path_suggestion") or "").lower()
        if target_layer == "api" or "api/index" in target_path:
            return "manual_mapping", "Untrusted candidate targeting an API Boundary requires extreme caution."
        return "manual_mapping", "Candidate is not yet trusted enough for file-level or compose-preferred intake."

    if host_policy == "manual_only" or readiness == "manual_review":
        if "ui_surface_without_strong_match" in review_reasons:
            return "manual_mapping", "UI surface does not have a strong one-to-one host match."
        return "manual_mapping", "Candidate already requires manual review under current merge authority."

    if host_policy == "compose_preferred":
        return "compose_preferred", "Host path is marked compose-preferred and should be evolved rather than replaced."

    if exists_in_main and action == "evo_upgrade":
        return "compose_preferred", "Existing host behavior should absorb donor improvements instead of full replacement."

    if exists_in_main and target_layer in {"application", "entities", "shared/hooks"}:
        return "compose_preferred", "Existing host file in a core lane suggests compose-first intake."

    if member_side_effects and target_layer in {"application", "entities", "domain", "api"}:
        if side_effect_source:
            if side_effect_call:
                return "compose_preferred", f"Member-level runtime coupling is driven by {side_effect_source} via {side_effect_call}; evolve in place."
            return "compose_preferred", f"Member-level runtime coupling is driven by {side_effect_source}; evolve in place."
        if side_effect_call:
            return "compose_preferred", f"Member-level side-effect callsite `{side_effect_call}` suggests evolve-in-place instead of direct intake."
        return "compose_preferred", "Member-level side-effect signals suggest evolve-in-place instead of direct intake."

    if neighbors and target_layer in {"application", "entities", "shared/hooks", "features"}:
        best_neighbor = neighbors[0]
        if best_neighbor["score"] >= 4:
            return "compose_preferred", (
                f"Semantic host neighbor `{best_neighbor['path']}` suggests evolve-in-place instead of adding a duplicate surface."
            )

    if readiness == "auto_merge" and confidence >= 0.6:
        return "file_level_ready", "New file-level intake is plausible with current host policy."

    return "manual_mapping", "Confidence is not high enough for unattended file-level intake."


def _compose_profile(row, lane, neighbors, scope_path_str: str, occurrence):
    if lane != "compose_preferred":
        return None

    target_layer = row.get("target_layer") or ""
    target_path = _relative_target(row.get("target_path_suggestion", ""), scope_path_str)
    neighbor_path = neighbors[0]["path"] if neighbors else ""
    family = _layer_family(target_layer, target_path)
    neighbor_family = _layer_family("", neighbor_path)
    review_reasons = set(row.get("review_reasons", []))
    member_side_effects = set((occurrence or {}).get("member_side_effect_markers") or [])
    member_side_effect_imports = sorted(list(set((occurrence or {}).get("member_side_effect_imports") or [])))
    member_side_effect_calls = sorted(list(set((occurrence or {}).get("member_side_effect_calls") or [])))

    if target_path.startswith("api/") or neighbor_path.startswith("api/") or target_path.startswith("integration/") or neighbor_path.startswith("integration/"):
        return "boundary_compose_sensitive"
    if member_side_effects or member_side_effect_calls:
        return "boundary_compose_sensitive"
    if family == "application" or neighbor_family == "application":
        return "service_compose_ready"
    if family in {"hooks", "entities", "domain"} or neighbor_family in {"hooks", "entities", "domain"}:
        return "logic_compose_ready"
    if family == "ui" or neighbor_family == "ui" or "ui_surface_without_strong_match" in review_reasons:
        return "ui_compose_hotspot"
    return "logic_compose_ready"


def run_scoped_host_analyzer(scope_path: str):
    started_at = time.perf_counter()
    logger.info(f"Building Scoped host-aware candidate matrix for: {scope_path}...")
    limits = _scoped_host_limits()
    scope_path_normalized = scope_path.replace("\\", "/").strip("/")
    # Handle duplicate "src/" if the user enters "src/[module_root]/..."
    # because the selected project root may already point at the source directory.
    if scope_path_normalized in {"", ".", "src"}:
        scope_path_normalized = ""
    elif scope_path_normalized.startswith("src/"):
        scope_path_normalized = scope_path_normalized[4:]
        
    scope_display = scope_path_normalized or "src"
    scope_filename_safe = scope_display.replace("/", "_").replace("-", "_").upper()
    scope_root = SRC / scope_path_normalized
    if not scope_root.exists():
        logger.error(f"Scope path {scope_root} does not exist.")
        return False

    fractal = load_fractal_map_data()
    atlas = load_atlas_data()
    audit = load_json_file(RAW_DIR / "audit_report.json", {})
    policy = load_host_policy()
    genome = load_genome_data()
    genome_index = {}
    if isinstance(genome, dict):
        for occs in genome.values():
            for occ in occs:
                key = (
                    occ.get("project"),
                    to_posix_path(occ.get("file", "")),
                    occ.get("name"),
                )
                if key not in genome_index:
                    genome_index[key] = occ

    # Dynamic row filtering based on path instead of "target_studio"
    all_decisions = fractal.get("all_decisions", [])
    rows = []
    
    # We match if target_path_suggestion falls under this scope
    # Wait, target_path_suggestion is usually in the host. e.g "src/[module_root]/[studio]/api/index.ts"
    for row in all_decisions:
        tsug = str(row.get("target_path_suggestion") or "").replace("\\", "/")
        tsrc = str(row.get("source_path") or "").replace("\\", "/")
        if (
            not scope_path_normalized
            or tsug.startswith(scope_path_normalized)
            or tsug.startswith(f"src/{scope_path_normalized}")
            or tsrc.startswith(scope_path_normalized)
            or tsrc.startswith(f"src/{scope_path_normalized}")
        ):
            rows.append(row)
            
    rows.sort(key=lambda row: row.get("delta", 0), reverse=True)
    scope_files = _atlas_scope_files(scope_root, atlas)
    if len(scope_files) > limits["max_scope_files"] or len(rows) > limits["max_candidate_rows"]:
        logger.error(
            "Scope %s is too broad for scoped host analysis: files=%s/%s rows=%s/%s behavior=%s",
            scope_display,
            len(scope_files),
            limits["max_scope_files"],
            len(rows),
            limits["max_candidate_rows"],
            limits["too_broad_scope_behavior"],
        )
        blocked_payload = {
            "meta": {
                "kind": "scoped_host_analysis",
                "version": "v1",
                "status": "blocked_too_broad_scope",
            },
            "scope": scope_display,
            "host_file_count": len(scope_files),
            "max_scope_files": limits["max_scope_files"],
            "candidate_count": len(rows),
            "max_candidate_rows": limits["max_candidate_rows"],
            "next_action": "rerun_with_narrower_scope",
            "examples": [
                "python -m tools.engines.scoped_host_analyzer src/contexts",
                "python -m tools.engines.scoped_host_analyzer src/layouts",
                "python -m tools.engines.scoped_host_analyzer src/shared",
            ],
        }
        save_json_atomic(RAW_DIR / f"{scope_filename_safe.lower()}_scoped_host_analysis.json", blocked_payload)
        return False
    host_inventory = _host_inventory(scope_root, fractal, atlas)
    existing_files = set(_atlas_scope_files(scope_root, atlas))

    matrix_rows = []
    lane_counter = Counter()
    lane_examples = defaultdict(list)
    compose_counter = Counter()
    compose_examples = defaultdict(list)

    for index, row in enumerate(rows, start=1):
        if index == 1 or index % limits["heartbeat_rows"] == 0:
            logger.info(
                "[SCOPED_HOST_PROFILE] scope=%s processed_rows=%s/%s host_files=%s elapsed_seconds=%.3f",
                scope_display,
                index,
                len(rows),
                len(host_inventory),
                time.perf_counter() - started_at,
            )
        rel_target = _relative_target(row.get("target_path_suggestion", ""), scope_path_normalized)
        host_policy = classify_host_policy(rel_target, policy)
        exists_in_main = rel_target in existing_files
        neighbors = _semantic_neighbors(row, host_inventory, scope_path_normalized, fractal)
        occurrence = _candidate_occurrence(row, genome_index)
        lane, rationale = _decide_lane(row, host_policy, exists_in_main, neighbors, occurrence)
        compose_profile = _compose_profile(row, lane, neighbors, scope_path_normalized, occurrence)
        enriched = {
            "name": row.get("name"),
            "source": row.get("chosen_source"),
            "action": row.get("action"),
            "target_layer": row.get("target_layer"),
            "delta": row.get("delta"),
            "confidence": row.get("confidence"),
            "risk": row.get("risk"),
            "merge_readiness": row.get("merge_readiness"),
            "host_policy": host_policy,
            "exists_in_main": exists_in_main,
            "semantic_neighbors": neighbors,
            "matrix_lane": lane,
            "compose_profile": compose_profile,
            "target_path": row.get("target_path_suggestion"),
            "review_reasons": row.get("review_reasons", []),
            "member_side_effect_markers": sorted(list(set((occurrence or {}).get("member_side_effect_markers") or []))),
            "member_side_effect_imports": sorted(list(set((occurrence or {}).get("member_side_effect_imports") or []))),
            "member_side_effect_calls": sorted(list(set((occurrence or {}).get("member_side_effect_calls") or []))),
            "rationale": rationale,
        }
        matrix_rows.append(enriched)
        lane_counter[lane] += 1
        if compose_profile:
            compose_counter[compose_profile] += 1
            if len(compose_examples[compose_profile]) < 15:
                compose_examples[compose_profile].append(enriched)
        if len(lane_examples[lane]) < 20:
            lane_examples[lane].append(enriched)

    audit_hits = []
    violations_list = audit.get("violations", [])
    grouped_violations = defaultdict(list)
    
    for v in violations_list:
        grouped_violations[v.get("rule", "Unknown")].append(v.get("file", ""))
        
    for category, items in grouped_violations.items():
        filtered = [
            item for item in items 
            if not scope_path_normalized
            or f"/{scope_path_normalized}/" in str(item).replace("\\", "/") 
            or str(item).replace("\\", "/").startswith(scope_path_normalized)
            or str(item).replace("\\", "/").startswith(f"src/{scope_path_normalized}")
        ]
        if filtered:
            audit_hits.append(
                {
                    "category": category,
                    "count": len(filtered),
                    "samples": filtered[:8],
                }
            )

    matrix_payload = {
        "meta": {
            "kind": "scoped_host_matrix",
            "scope": scope_display,
            "version": "v1",
        },
        "summary": {
            "candidate_count": len(matrix_rows),
            "lane_counts": dict(lane_counter),
            "semantic_neighbor_hits": sum(1 for row in matrix_rows if row.get("semantic_neighbors")),
            "compose_breakdown": dict(compose_counter),
        },
        "lane_examples": dict(lane_examples),
        "compose_examples": dict(compose_examples),
        "audit_hits": audit_hits,
        "rows": matrix_rows,
    }

    json_path = RAW_DIR / f"{scope_filename_safe.lower()}_host_matrix.json"
    save_json_atomic(json_path, matrix_payload)

    lines = [
        f"# Scoped Host-Aware Candidate Matrix ({scope_display})",
        "",
        f"This report combines donor evidence with host policy for `{scope_display}`.",
        "",
        "## Lane Summary",
        "| Lane | Count | Meaning |",
        "|---|---:|---|",
        f"| `file_level_ready` | {lane_counter['file_level_ready']} | Candidate can likely enter through normal file-level intake. |",
        f"| `compose_preferred` | {lane_counter['compose_preferred']} | Host exists or host policy says evolve in place. |",
        f"| `manual_mapping` | {lane_counter['manual_mapping']} | Needs careful architectural mapping before any intake. |",
        f"| `blocked_host_locked` | {lane_counter['blocked_host_locked']} | Target path is protected host boundary. |",
        f"| `host_retained` | {lane_counter['host_retained']} | Main already wins and should remain authoritative. |",
        "",
        f"Semantic neighbor hits: `{matrix_payload['summary']['semantic_neighbor_hits']}`",
        "",
    ]

    if compose_counter:
        lines += [
            "## Compose Breakdown",
            "| Compose Profile | Count | Meaning |",
            "|---|---:|---|",
            f"| `logic_compose_ready` | {compose_counter['logic_compose_ready']} | Hooks, entities, or domain-adjacent logic that can pilot symbol-compose first. |",
            f"| `service_compose_ready` | {compose_counter['service_compose_ready']} | Application-service or orchestration compose targets. |",
            f"| `ui_compose_hotspot` | {compose_counter['ui_compose_hotspot']} | UI-coupled compose targets that need more caution. |",
            f"| `boundary_compose_sensitive` | {compose_counter['boundary_compose_sensitive']} | Public boundary or integration seam candidates; highest contract sensitivity. |",
            "",
        ]

    lane_titles = {
        "file_level_ready": "File-Level Ready",
        "compose_preferred": "Compose-Preferred",
        "manual_mapping": "Manual Mapping",
        "blocked_host_locked": "Blocked By Host Lock",
        "host_retained": "Host Retained",
    }

    for lane in ["file_level_ready", "compose_preferred", "manual_mapping", "blocked_host_locked", "host_retained"]:
        lines += [
            f"## {lane_titles[lane]}",
            "| Name | Source | Layer | Action | Host Policy | Exists | Readiness | Risk | Delta | Neighbor | Side-Effect Import | Side-Effect Call | Rationale |",
            "|---|---|---|---|---|---|---|---|---:|---|---|---|---|",
        ]
        for row in lane_examples[lane]:
            neighbor_label = "-"
            if row["semantic_neighbors"]:
                best = row["semantic_neighbors"][0]
                neighbor_label = f"`{best['path']}` ({best['score']})"
            side_effect_label = "-"
            if row.get("member_side_effect_imports"):
                side_effect_label = f"`{row['member_side_effect_imports'][0]}`"
            side_effect_call_label = "-"
            if row.get("member_side_effect_calls"):
                side_effect_call_label = f"`{row['member_side_effect_calls'][0]}`"
            lines.append(
                f"| `{row['name']}` | {row['source']} | `{row['target_layer']}` | {row['action']} | "
                f"`{row['host_policy']}` | {'yes' if row['exists_in_main'] else 'no'} | "
                f"`{row['merge_readiness']}` | {row['risk']} | {row['delta']} | {neighbor_label} | {side_effect_label} | {side_effect_call_label} | {row['rationale']} |"
            )
        lines.append("")

    if compose_counter:
        compose_titles = {
            "logic_compose_ready": "Logic Compose Ready",
            "service_compose_ready": "Service Compose Ready",
            "ui_compose_hotspot": "UI Compose Hotspot",
            "boundary_compose_sensitive": "Boundary Compose Sensitive",
        }
        for profile in ["logic_compose_ready", "service_compose_ready", "ui_compose_hotspot", "boundary_compose_sensitive"]:
            lines += [
                f"## {compose_titles[profile]}",
                "| Name | Source | Layer | Action | Risk | Neighbor | Why |",
                "|---|---|---|---|---|---|---|",
            ]
            for row in compose_examples[profile]:
                neighbor_label = "-"
                if row["semantic_neighbors"]:
                    best = row["semantic_neighbors"][0]
                    neighbor_label = f"`{best['path']}` ({best['score']})"
                lines.append(
                    f"| `{row['name']}` | {row['source']} | `{row['target_layer']}` | {row['action']} | "
                    f"{row['risk']} | {neighbor_label} | {row['rationale']} |"
                )
            lines.append("")

    lines += [
        f"## {scope_display} Audit Pressure",
        "| Category | Count |",
        "|---|---:|",
    ]
    for hit in audit_hits:
        lines.append(f"| `{hit['category']}` | {hit['count']} |")
    lines.append("")

    lines += [
        "## Interpretation",
        "1. `file_level_ready` is the safest first intake lane.",
        "2. `compose_preferred` now includes both exact host collisions and semantic host-neighbor matches.",
        "3. `logic_compose_ready` and `service_compose_ready` are the best pilot lanes before full symbol-level compose automation.",
        "4. `ui_compose_hotspot` marks the cluster where later symbol-level compose will matter most.",
        "5. `manual_mapping` is dominated by UI and weak target-match cases.",
        "6. `host_retained` means main already wins and should not be re-imported.",
        "7. `blocked_host_locked` should remain outside direct donor overwrite flows.",
    ]

    md_path = REPORTS_DIR / f"{scope_filename_safe}_AWARE_CANDIDATE_MATRIX.md"
    save_text_atomic(md_path, "\n".join(lines))

    # [Phase 5] Global Dashboard Integration (Hardened)
    integration_payload = {
        "meta": {"kind": "scoped_host_analysis", "version": "v1"},
        "scope": scope_display,
        "timestamp": datetime.now().isoformat(),
        "studios": {
            scope_filename_safe: {
                "protected_files": [r.get("target_path") for r in matrix_rows if r.get("matrix_lane") == "blocked_host_locked"],
                "compose_preferred_files": [r.get("target_path") for r in matrix_rows if r.get("matrix_lane") == "compose_preferred"],
                "manual_review_needed": [r.get("target_path") for r in matrix_rows if r.get("matrix_lane") == "manual_mapping"]
            }
        }
    }
    json_path = RAW_DIR / f"{scope_filename_safe}_scoped_host_analysis.json"
    save_json_atomic(json_path, integration_payload)

    logger.info(f"Scoped host-aware matrix written: {to_posix_path(md_path.relative_to(REPORTS_DIR.parent))}")
    logger.info(f"Scoped integration artifact written: {to_posix_path(json_path.relative_to(REPORTS_DIR.parent))}")
    logger.info(
        "[SCOPED_HOST_PROFILE] scope=%s rows=%s host_files=%s semantic_neighbor_hits=%s total_seconds=%.3f",
        scope_display,
        len(matrix_rows),
        len(host_inventory),
        matrix_payload["summary"]["semantic_neighbor_hits"],
        time.perf_counter() - started_at,
    )
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        raise SystemExit(0 if run_scoped_host_analyzer(sys.argv[1]) else 1)
    else:
        logger.error("Usage: python scoped_host_analyzer.py <scope_path>")
        raise SystemExit(2)

"""
Variation Engine (Variation Engine MVP)
Performs intelligent Semantic Intent Matching across project variations
to identify Strategy Drift, Refinement, and Collateral Clones.
"""

from collections import defaultdict
from pathlib import Path
from tools.core.atlas_io import load_atlas_data
from tools.core.config import RAW_DIR, REPORTS_DIR, DOCTRINE, save_json_atomic, save_text_atomic
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.logger import logger
from tools.core.workspace_mode import get_workspace_mode

def sanitize_symbol_name(name: str, strip_prefixes: list, strip_suffixes: list) -> str:
    cleaned = name
    for pref in strip_prefixes:
        if cleaned.startswith(pref):
            cleaned = cleaned[len(pref):]
            break
    for suff in strip_suffixes:
        if cleaned.endswith(suff):
            cleaned = cleaned[:-len(suff)]
            break
    return cleaned.lower().strip()

def jaccard_similarity(set_a, set_b) -> float:
    if not set_a or not set_b:
        return 0.0
    u = len(set_a.union(set_b))
    if u == 0:
        return 0.0
    return len(set_a.intersection(set_b)) / u

def build_file_profile(file_path: str, file_data: dict, strip_prefixes: list, strip_suffixes: list) -> dict:
    symbols = file_data.get("symbols", [])
    imports = file_data.get("import_records", [])
    features = set(file_data.get("features", []))
    
    symbol_names = set()
    sanitized_symbols = set()
    for sym in symbols:
        if isinstance(sym, dict):
            name = sym.get("name", "")
            if name:
                symbol_names.add(name)
                sanitized_symbols.add(sanitize_symbol_name(name, strip_prefixes, strip_suffixes))
            features.update(sym.get("features", []))
            
    import_sources = {imp.get("source", "") for imp in imports if isinstance(imp, dict)}
    
    # Strip filename prefix/suffix
    stem = Path(file_path).stem
    sanitized_filename = sanitize_symbol_name(stem, strip_prefixes, strip_suffixes)
    
    return {
        "file": file_path,
        "filename": stem,
        "sanitized_filename": sanitized_filename,
        "symbols": list(symbol_names),
        "sanitized_symbols": list(sanitized_symbols),
        "features": list(features),
        "imports": list(import_sources),
        "dna_hash": file_data.get("genome_hash", "")
    }

def _build_base_lookup(base_profiles: dict) -> dict:
    by_name = defaultdict(set)
    by_token = defaultdict(set)
    for base_file, profile in base_profiles.items():
        by_name[profile.get("sanitized_filename", "")].add(base_file)
        for token in set(profile.get("features", []) or []).union(profile.get("imports", []) or []):
            if token:
                by_token[token].add(base_file)
    return {"by_name": by_name, "by_token": by_token}

def analyze_variation():
    logger.info("Variation Engine: Aligning Semantic Intent across project variants...")

    atlas = load_atlas_data()
    if not atlas:
        logger.error("[FAIL] atlas payload not found in SQLite or shadow JSON. Cannot perform variation analysis.")
        return False
    workspace_mode = get_workspace_mode()
    
    project_keys = list(atlas.keys())
    if len(project_keys) < 2 or not workspace_mode.get("comparative_enabled"):
        logger.info("[INFO] Variation comparative analysis skipped: non-comparative workspace.")
        _write_noop_reports(workspace_mode, project_keys)
        return True

    # Resolve host vs variant projects
    from tools.core.config import DYNAMIC_CONFIG
    roles = DYNAMIC_CONFIG.get("project_roles", {})
    base_proj = next((k for k, v in roles.items() if v == "host" and k in project_keys), None)
    if not base_proj:
        base_proj = "MAIN" if "MAIN" in project_keys else project_keys[0]
        
    target_projects = [proj for proj in project_keys if proj != base_proj]
    
    naming_doctrine = require_doctrine_mapping("dead_code_heuristics").get("naming_doctrine")
    strip_prefixes = naming_doctrine.get("strip_prefixes", ["use", "create", "get", "set", "handle"])
    strip_suffixes = naming_doctrine.get("strip_suffixes", ["Service", "Manager", "Adapter", "Repository", "Orchestrator", "Provider", "Controller", "Delegate", "Store", "Slice", "Hook", "Logic", "Utils"])

    # 1. Compile semantic profiles for all projects
    project_profiles = {}
    for pkey in project_keys:
        project_profiles[pkey] = {}
        files_data = atlas.get(pkey, {}).get("files", {})
        for rel_path, a_data in files_data.items():
            project_profiles[pkey][rel_path] = build_file_profile(
                rel_path, a_data, strip_prefixes, strip_suffixes
            )

    results = {}
    for target_proj in target_projects:
        results[target_proj] = _reconcile_variants(
            base_proj, project_profiles[base_proj],
            target_proj, project_profiles[target_proj]
        )
        
        report_md = _generate_markdown_reconciliation(base_proj, target_proj, results[target_proj])
        md_path = REPORTS_DIR / f"variation_reconciler_{base_proj}_vs_{target_proj}.md"
        save_text_atomic(md_path, "\n".join(report_md))
        logger.info(f"[OK] Premium Variation Reconciler Report generated: {md_path.name}")

    save_json_atomic(RAW_DIR / "variation_analysis.json", results)
    return True

def _reconcile_variants(base_name: str, base_profiles: dict, target_name: str, target_profiles: dict) -> dict:
    base_files = set(base_profiles.keys())
    target_files = set(target_profiles.keys())
    base_lookup = _build_base_lookup(base_profiles)
    
    alignments = []
    
    # Track which base files have been mapped/reconciled
    mapped_base_files = set()

    for target_file in sorted(target_files):
        t_prof = target_profiles[target_file]
        t_features = set(t_prof["features"])
        t_imports = set(t_prof["imports"])
        t_sanitized = t_prof["sanitized_filename"]
        
        best_base_file = None
        best_score = -1.0
        best_match_type = "NEW_COMPLEMENT"
        recommended_strategy = "Adopt as a new complement module."
        
        # 1. Exact relative path match (Strongest Candidate)
        if target_file in base_files:
            best_base_file = target_file
            mapped_base_files.add(target_file)
            
            b_prof = base_profiles[target_file]
            b_features = set(b_prof["features"])
            b_imports = set(b_prof["imports"])
            
            jaccard = jaccard_similarity(t_features.union(t_imports), b_features.union(b_imports))
            
            if t_prof["dna_hash"] == b_prof["dna_hash"]:
                best_match_type = "IDENTICAL"
                recommended_strategy = "Keep base implementation. No action required."
            elif jaccard >= 0.70:
                best_match_type = "REFINED"
                recommended_strategy = "Apply selective merge. Target contains incremental improvements."
            else:
                best_match_type = "STRATEGY_DRIFT"
                recommended_strategy = "Strategy drift detected! Compare Zustand vs context logic before merging."
            best_score = jaccard
        else:
            # 2. Semantic Search across plausible base files. A file can only
            # reach the score threshold through a name match or overlapping
            # feature/import tokens, so avoid comparing every base file.
            candidate_base_files = set(base_lookup["by_name"].get(t_sanitized, set()))
            for token in t_features.union(t_imports):
                candidate_base_files.update(base_lookup["by_token"].get(token, set()))

            for base_file in sorted(candidate_base_files):
                b_prof = base_profiles[base_file]
                b_features = set(b_prof["features"])
                b_imports = set(b_prof["imports"])
                
                # Check Jaccard Overlap
                jaccard = jaccard_similarity(t_features.union(t_imports), b_features.union(b_imports))
                
                # Check Name Equivalence
                is_name_match = (t_sanitized == b_prof["sanitized_filename"])
                
                # Combined score
                score = (0.50 if is_name_match else 0.0) + (0.50 * jaccard)
                
                if score > best_score and score >= 0.40:
                    best_score = score
                    best_base_file = base_file
                    
            if best_base_file:
                mapped_base_files.add(best_base_file)
                b_prof = base_profiles[best_base_file]
                if t_sanitized == b_prof["sanitized_filename"]:
                    best_match_type = "STRATEGY_DRIFT"
                    recommended_strategy = f"Feature intent matches base module '{best_base_file}' but resolved path or name varies. Consolidate to keep codebase clean."
                else:
                    best_match_type = "COLLATERAL_CANDIDATE"
                    recommended_strategy = f"High structural overlap with base module '{best_base_file}'. Potential duplication or side branch."
            else:
                best_score = 0.0

        alignments.append({
            "target_file": target_file,
            "mapped_base_file": best_base_file,
            "match_type": best_match_type,
            "similarity_score": round(best_score, 2),
            "recommended_strategy": recommended_strategy,
            "target_symbols": t_prof["symbols"],
            "target_features": t_prof["features"]
        })

    # Record deleted/legacy files in base
    deleted_files = base_files - mapped_base_files
    for del_file in sorted(deleted_files):
        alignments.append({
            "target_file": None,
            "mapped_base_file": del_file,
            "match_type": "DELETED_LEGACY",
            "similarity_score": 0.0,
            "recommended_strategy": "Safe to discard or archive, no equivalents in target variant.",
            "target_symbols": [],
            "target_features": []
        })

    return {
        "base_project": base_name,
        "target_project": target_name,
        "alignments": alignments
    }

def _generate_markdown_reconciliation(base_name: str, target_name: str, recon_data: dict) -> list:
    alignments = recon_data["alignments"]
    
    # Calculate stats
    total_files = len([a for a in alignments if a["target_file"]])
    drifts = [a for a in alignments if a["match_type"] == "STRATEGY_DRIFT"]
    refined = [a for a in alignments if a["match_type"] == "REFINED"]
    collateral = [a for a in alignments if a["match_type"] == "COLLATERAL_CANDIDATE"]
    complements = [a for a in alignments if a["match_type"] == "NEW_COMPLEMENT"]
    identical = [a for a in alignments if a["match_type"] == "IDENTICAL"]
    deleted = [a for a in alignments if a["match_type"] == "DELETED_LEGACY"]

    lines = [
        f"# 🎭 S.A.G.E. Variation Reconciler Report: `{base_name}` vs `{target_name}`",
        "",
        "> [!NOTE]",
        "> This Variation Reconciler provides intelligent **Semantic Intent Matching** over raw textual diffs. It flags components that represent similar business intents but have drifted strategy.",
        "",
        "## 📊 Semantic Intent Alignment Summary",
        "",
        f"- **Analyzed Target Files:** `{total_files}`",
        f"- **Identical Implementations:** `{len(identical)}` (Auto-merge completely safe)",
        f"- **Refined Implementations:** `{len(refined)}` (Target contains refinements/fixes)",
        f"- **Strategy Drifts:** `{len(drifts)}` (⚠ Crucial architectural variations)",
        f"- **Collateral Candidates:** `{len(collateral)}` (⚠ Potential file cloning/duplications)",
        f"- **New Complements:** `{len(complements)}` (Brand new modules in variant)",
        f"- **Legacy Deletions:** `{len(deleted)}` (Discarded in target variant)",
        "",
        "---",
        ""
    ]

    if drifts:
        lines.extend([
            "## ⚠ Strategy Drifts (Mimarî Strateji Sapmaları)",
            "These files solve the same feature intent but have drifted technically (e.g. importing different styling/state libraries).",
            "",
            "| Target File | Equivalent Base | Overlap | Recommended Merge Strategy |",
            "|---|---|---:|---|",
        ])
        for a in drifts:
            lines.append(
                f"| `{a['target_file']}` | `{a['mapped_base_file']}` | `{a['similarity_score'] * 100}%` | {a['recommended_strategy']} |"
            )
        lines.append("")

    if collateral:
        lines.extend([
            "## 🕵️ Collateral Candidates (Gölge Klonlar)",
            "High structural similarity was detected with base files under different names/paths. These are highly likely shadow duplicates.",
            "",
            "| Target File | Shadowing Base | Overlap | Recommendation |",
            "|---|---|---:|---|",
        ])
        for a in collateral:
            lines.append(
                f"| `{a['target_file']}` | `{a['mapped_base_file']}` | `{a['similarity_score'] * 100}%` | {a['recommended_strategy']} |"
            )
        lines.append("")

    if refined:
        lines.extend([
            "## ⚡ Refined Modules (İnce İşlenmiş Modüller)",
            "These files have minor implementation details modified but preserve identical architectural bindings.",
            "",
            "| File Path | Overlap | Action |",
            "|---|---:|---|",
        ])
        for a in refined:
            lines.append(
                f"| `{a['target_file']}` | `{a['similarity_score'] * 100}%` | {a['recommended_strategy']} |"
            )
        lines.append("")

    if complements:
        lines.extend([
            "## ➕ New Complements (Yeni Tamamlayıcılar)",
            "Unique modules introduced in the target variant with no structural equivalent in base.",
            "",
            "| New File Path | Primary Features |",
            "|---|---|",
        ])
        for a in complements[:15]:
            feats = ", ".join(f"`{f}`" for f in a["target_features"][:3]) or '-'
            lines.append(f"| `{a['target_file']}` | {feats} |")
        if len(complements) > 15:
            lines.append(f"| *and {len(complements) - 15} more new files...* | |")
        lines.append("")

    return lines

def _write_noop_reports(workspace_mode, project_keys):
    payload = {
        "status": "not_applicable",
        "reason": "variation_analysis_requires_variant_projects",
        "workspace_mode": workspace_mode,
        "projects": project_keys,
    }
    save_json_atomic(RAW_DIR / "variation_analysis.json", payload)
    
    md_lines = [
        "# 🎭 S.A.G.E. Variation Reconciler Report",
        "",
        "Variation comparative analysis is not applicable in this workspace.",
        "",
        f"- Workspace Mode: `{workspace_mode.get('mode')}`",
        f"- Project Count: `{workspace_mode.get('project_count')}`",
        f"- Projects: `{', '.join(project_keys) if project_keys else '-'}`",
        "",
        "> A comparative variation scan only runs when the workspace contains at least one project role set to `variant` in `codemaps.config.json`.",
    ]
    save_text_atomic(REPORTS_DIR / "variation_reconciler_summary.md", "\n".join(md_lines))

if __name__ == "__main__":
    analyze_variation()

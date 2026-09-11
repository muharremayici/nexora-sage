import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Any

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import ROOT, RAW_DIR, REPORTS_DIR, DOCTRINE, save_json_atomic
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.json_io import load_json_file
from tools.core.artifact_freshness_contract import evaluate_named_artifact_chain
from tools.core.logger import logger
from tools.core.language_registry import language_for_extension
from tools.core.source_snapshot_reader import load_source_text
from tools.core.projects_registry import resolve_runtime_projects

# Dynamic indicators representing runtime framework magic, reflection, dynamic imports, etc.
REFLECTIVE_INDICATORS = {
    # Python
    r"\bgetattr\b",
    r"\bsetattr\b",
    r"\bhasattr\b",
    r"\b__import__\b",
    r"\bimportlib\b",
    r"\beval\b",
    r"\bexec\b",
    r"\bglobals\b",
    r"\blocals\b",
    r"\bpatch\b",
    r"@\w+decorator",
    
    # TS/JS
    r"\bReflect\b",
    r"\bProxy\b",
    r"\bimport\s*\(",
    r"\brequire\s*\(",
    r"decorator",
    r"injection",
    
    # C#/Java
    r"\bAssembly\.Load\b",
    r"\btypeof\b",
    r"\bGetMethod\b",
    r"\bFieldInfo\b",
    r"\bClass\.forName\b",
    r"\bMethod\.invoke\b"
}

def scan_reflection_indicators(content: str) -> List[str]:
    """Scans code content for dynamic reflection or injection patterns."""
    found = []
    if not content:
        return found
        
    for pattern in REFLECTIVE_INDICATORS:
        if re.search(pattern, content):
            # Clean display name
            display = pattern.strip(r"\b").replace(r"\s*", " ").replace(r"\b", "")
            found.append(display)
    return found


def _resolve_fallback_source_path(project_key: str, target_rel: str) -> Path:
    """Choose a live-source fallback path without duplicating project root names."""

    normalized_rel = str(target_rel or "").replace("\\", "/").strip().lstrip("/")
    root = Path(ROOT).resolve()
    candidates: list[Path] = []
    if normalized_rel:
        candidates.append((root / normalized_rel).resolve())
    projects = resolve_runtime_projects(root)
    project_root = projects.get(str(project_key or ""))
    if project_root is not None:
        project_root = project_root.resolve()
        if normalized_rel:
            candidates.append((project_root / normalized_rel).resolve())
            root_name = project_root.name.replace("\\", "/").strip("/")
            if root_name and normalized_rel.startswith(f"{root_name}/"):
                candidates.append((project_root / normalized_rel[len(root_name) + 1 :]).resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if len(candidates) >= 3:
        return candidates[2]
    if candidates:
        return candidates[0]
    return root

def _dependency_graph_evidence() -> tuple[dict[str, Any], dict[str, Any]]:
    data = load_json_file(RAW_DIR / "circular_deps.json", {})
    chain = evaluate_named_artifact_chain("confidence_dependency_input_chain", RAW_DIR)
    row = next(
        (item for item in chain.get("artifacts", []) if isinstance(item, dict) and item.get("artifact") == "circular_deps"),
        {},
    )
    shape_valid = isinstance(data, dict) and isinstance(data.get("edges"), list) and isinstance(data.get("cycles"), list)
    available = bool(row.get("exists")) and shape_valid and not chain.get("missing_contract")
    evidence = {
        "status": "PASS" if available else "UNKNOWN",
        "artifact": "circular_deps",
        "source": str(row.get("source") or "missing"),
        "shape_status": "valid" if shape_valid else "invalid",
        "payload_bytes": int(row.get("payload_bytes") or 0),
        "updated_at": str(row.get("updated_at") or ""),
    }
    return (data if isinstance(data, dict) else {}), evidence


def get_dependent_count(target_node: str, dependency_graph: dict[str, Any] | None = None) -> int:
    """Calculates direct dependents from circular_deps.json edges."""
    deps_path = RAW_DIR / "circular_deps.json"
    try:
        data = dependency_graph if isinstance(dependency_graph, dict) else load_json_file(deps_path, {})
        edges = data.get("edges", [])
        dependents = set()
        for edge in edges:
            if edge.get("target") == target_node:
                dependents.add(edge.get("source"))
        return len(dependents)
    except Exception as exc:
        logger.warning("Failed to calculate dependent count from circular_deps.json: %s", exc)
        return 0

def check_cyclic_member(target_node: str, dependency_graph: dict[str, Any] | None = None) -> bool:
    """Checks if a target node is a member of any import cycles."""
    deps_path = RAW_DIR / "circular_deps.json"
    try:
        data = dependency_graph if isinstance(dependency_graph, dict) else load_json_file(deps_path, {})
        cycles = data.get("cycles", [])
        for cycle in cycles:
            if target_node in cycle.get("chain", []):
                return True
    except Exception as exc:
        logger.warning("Failed to check cyclic member from circular_deps.json: %s", exc)
    return False

def evaluate_file_confidence(target_path_or_node: str, content: str = None) -> Dict[str, Any]:
    """
    Evaluates the risk profile and confidence matrix for a specific file or proposed content.
    Calculates dead code certainty, merge safety, architecture drift risk, and reflection hazards.
    """
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    
    content_was_supplied = content is not None

    # 1. Target Normalization
    project_key = "MAIN"
    target_rel = target_path_or_node
    target_node = target_path_or_node
    
    if "::" in target_path_or_node:
        project_key, target_rel = target_path_or_node.split("::", 1)
    else:
        if atlas:
            for pkey, pdata in atlas.items():
                files = pdata.get("files", {})
                if target_rel in files:
                    project_key = pkey
                    target_node = f"{pkey}::{target_rel}"
                    break
                    
    target_rel = target_rel.replace("\\", "/")
    atlas_file_data = (
        atlas.get(project_key, {}).get("files", {}).get(target_rel, {})
        if isinstance(atlas, dict)
        else {}
    )
    
    # 2. Retrieve actual file content if not provided
    if content is None:
        resolved_path = _resolve_fallback_source_path(project_key, target_rel)
        content = load_source_text(
            project_key,
            target_rel,
            fallback_path=resolved_path,
            component="confidence_engine",
        ) or ""
            
    # 3. Dynamic indicators & reflection scanning
    indicators = scan_reflection_indicators(content)
    
    # 4. Dead Code Confidence Scoring (Formulaic Adjustments)
    confidence_policy = require_doctrine_mapping("confidence_policy")
    base_dead_confidence = confidence_policy.get("base_dead_confidence", 0.97)
    magic_factor_degrade = confidence_policy.get("magic_factor_degrade", 0.15)
    critical_dep_threshold = confidence_policy.get("critical_dep_threshold", 10)
    low_dep_threshold = confidence_policy.get("low_dep_threshold", 5)
    loc_complexity_threshold = confidence_policy.get("loc_complexity_threshold", 400)
    drift_base = confidence_policy.get("drift_base", 0.10)
    drift_relative_factor = confidence_policy.get("drift_relative_factor", 0.15)

    # Degrade confidence based on reflective dynamics (black box factor)
    magic_factor = len(indicators) * magic_factor_degrade
    dead_code_confidence = max(0.20, round(base_dead_confidence - magic_factor, 2))
    
    # 5. Merge Safety Scoring (LOW / MEDIUM / HIGH / SAFE)
    dependency_graph, dependency_evidence = _dependency_graph_evidence()
    dependency_evidence_ready = dependency_evidence["status"] == "PASS"
    dep_count = get_dependent_count(target_node, dependency_graph) if dependency_evidence_ready else 0
    is_cyclic = check_cyclic_member(target_node, dependency_graph) if dependency_evidence_ready else False
    loc_count = len(content.splitlines()) if content else 0
    
    reasons = []
    safety_rank = "SAFE" if dependency_evidence_ready else "UNKNOWN"
    if not dependency_evidence_ready:
        reasons.append("Dependency evidence is unavailable or invalid; merge safety cannot be classified.")
    
    # Safety assessment hierarchy
    if is_cyclic:
        safety_rank = "LOW"
        reasons.append("File is a member of an active import cycle chain")
    if dep_count >= critical_dep_threshold:
        safety_rank = "CRITICAL"
        reasons.append(f"Highly volatile dependent count (Blast radius dependents: {dep_count})")
    elif dep_count >= low_dep_threshold:
        if safety_rank != "CRITICAL":
            safety_rank = "LOW"
        reasons.append(f"Volatile dependent count (Blast radius dependents: {dep_count})")
    elif dep_count >= 1:
        if safety_rank not in ["CRITICAL", "LOW"]:
            safety_rank = "MEDIUM"
        reasons.append(f"Moderate dependent count (Blast radius dependents: {dep_count})")
        
    if loc_count > loc_complexity_threshold:
        if safety_rank == "SAFE":
            safety_rank = "MEDIUM"
        reasons.append(f"High codebase complexity (> {loc_complexity_threshold} LOC: count is {loc_count})")
        
    # 6. Architecture Drift Certainty Score
    from tools.core.polyglot_imports import extract_imports
    from tools.core.layer_resolver import resolve_layer, is_violation

    file_ext = Path(target_rel).suffix.lower()
    f_lang = language_for_extension(file_ext)
    import_evidence_ready = True
    if f_lang in {"typescript", "javascript"}:
        if content_was_supplied or not isinstance(atlas_file_data, dict):
            imports = []
            import_evidence_ready = False
        else:
            import_records = atlas_file_data.get("import_records")
            if not isinstance(import_records, list):
                imports = []
                import_evidence_ready = False
            else:
                imports = []
                for record in import_records:
                    if not isinstance(record, dict) or str(record.get("kind") or "").lower() == "type":
                        continue
                    source = str(record.get("raw_source") or record.get("source") or "").strip()
                    if source and source not in imports:
                        imports.append(source)
    else:
        imports = extract_imports(content or "", f_lang)

    drift_certainty = drift_base
    relative_count = sum(str(source).count("../") for source in imports)
    if relative_count > 0:
        drift_certainty = min(0.95, round(drift_base + relative_count * drift_relative_factor, 2))
        reasons.append(f"Contains {relative_count} syntax-grounded relative import traversal (Doctrine standard alias violation)")
    if not import_evidence_ready:
        reasons.append("Syntax-grounded JS/TS import evidence is unavailable for the supplied or unindexed source; architecture drift was not inferred from raw text.")
    current_layer = resolve_layer(target_rel)
    
    for imp in imports:
        # Resolve target layer
        target_rel_path = imp.replace("@/", "") if imp.startswith("@/") else imp
        resolved_imp_layer = resolve_layer(target_rel_path)
        
        is_viol = is_violation(current_layer, resolved_imp_layer, language=f_lang)
        if not is_viol:
            # Fallback prefix matching to bridge "domain" vs "domain/logic" mismatches in doctrine configuration
            norm_src = current_layer.split("/")[0] if current_layer else ""
            norm_tgt = resolved_imp_layer.split("/")[0] if resolved_imp_layer else ""
            is_viol = is_violation(norm_src, norm_tgt, language=f_lang)
            
        if is_viol:
            drift_certainty = 1.0
            reasons.append(f"Direct architectural violation: Layer '{current_layer}' imports forbidden layer '{resolved_imp_layer}' ({imp})")
                
    # 7. Verdict logic
    if not dependency_evidence_ready:
        verdict = "Confidence unavailable until dependency evidence is refreshed."
    elif safety_rank in ["CRITICAL", "LOW"] or drift_certainty >= 0.80 or dead_code_confidence <= 0.50:
        verdict = "High Risk Level (Requires manual review / CI block)"
    elif safety_rank == "MEDIUM" or drift_certainty >= 0.40 or dead_code_confidence <= 0.80:
        verdict = "Medium Risk Level (Minor warnings present)"
    else:
        verdict = "Low Risk Level (Small patch may proceed after file inspection and required validation)"
        
    return {
        "target": target_node,
        "confidence_matrix": {
            "dead_code_confidence": dead_code_confidence,
            "merge_safety": safety_rank,
            "architecture_drift_certainty": drift_certainty,
            "dynamic_magic_hazard": round(min(1.0, len(indicators) * 0.20), 2)
        },
        "metrics": {
            "loc": loc_count,
            "blast_radius_dependents": dep_count,
            "cyclic_member": is_cyclic,
            "reflection_indicators_found": indicators,
            "risk_mitigation_reasons": reasons if reasons else ["File adheres completely to standard static and architectural safety constraints."]
        },
        "input_evidence": {"circular_deps": dependency_evidence},
        "verdict": verdict,
        "decision_boundary": "Confidence is a risk estimate, not a standalone merge or deploy approval."
    }

def run_confidence_engine(target_path_or_node: str = None):
    """Orchestrator runner entry point. Saves risk report and displays visual dashboard."""
    logger.info("Running Confidence & Risk Engine (Kurumsal Hazırlık)...")
    
    if not target_path_or_node:
        # Fallback to resolving the first file from cached atlas
        atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
        if atlas:
            default_project = "MAIN" if "MAIN" in atlas else list(atlas.keys())[0]
            first_files = list(atlas[default_project].get("files", {}).keys())
            if first_files:
                target_path_or_node = f"{default_project}::{first_files[0]}"
                
    if not target_path_or_node:
        logger.error("No valid targets found for risk matrix analysis.")
        return False
        
    result = evaluate_file_confidence(target_path_or_node)
    
    # Save persistent artifact report
    output_path = REPORTS_DIR / "confidence_risk_report.json"
    save_json_atomic(output_path, result)
    
    # Beautiful visual dashboard ASCII printing
    print("\n" + "=" * 60)
    print(f"=== CONFIDENCE & RISK MATRIX REPORT: {result['target']} ===")
    print("=" * 60)
    
    matrix = result["confidence_matrix"]
    metrics = result["metrics"]
    
    print(f"{'RISK METRIC':<30} | {'VALUE / CERTAINTY'}")
    print("-" * 60)
    print(f"{'Dead Code Confidence':<30} | {matrix['dead_code_confidence'] * 100:.0f}%")
    print(f"{'Merge Safety Rank':<30} | {matrix['merge_safety']}")
    print(f"{'Architecture Drift Certainty':<30} | {matrix['architecture_drift_certainty'] * 100:.0f}%")
    print(f"{'Dynamic Magic Hazard':<30} | {matrix['dynamic_magic_hazard'] * 100:.0f}%")
    print("-" * 60)
    print(f"VERDICT: {result['verdict']}\n")
    
    print("[+] Architectural and Safety Observations:")
    for reason in metrics["risk_mitigation_reasons"]:
        print(f"  - {reason}")
        
    if metrics["reflection_indicators_found"]:
        print(f"\n[!] Reflective / Runtime magic indicators detected: {metrics['reflection_indicators_found']}")
        
    print("=" * 60 + "\n")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", help="Normalized target file node (e.g. MAIN::src/domain/book_chapter.py)")
    args = parser.parse_args()
    
    success = run_confidence_engine(args.target)
    sys.exit(0 if success else 1)

import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Set

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import RAW_DIR, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.test_impact_profiles import (
    command_for_test,
    confidence_value,
    extract_logical_base_name,
    is_test_path,
)

def is_test_file(path_str: str) -> bool:
    """
    Determines if a filename or path matches typical test naming conventions across polyglot languages.
    """
    return is_test_path(path_str)

def extract_base_name(path_str: str) -> str:
    """
    Extracts the logical base name of a source or test file (e.g. ChapterPage from ChapterPage.test.tsx).
    Strips standard test/spec/language extensions.
    """
    return extract_logical_base_name(path_str)

def generate_test_command(test_path: str) -> str:
    """
    Generates the appropriate, language-specific run command for a test file.
    """
    return command_for_test(test_path)


def _test_command_for_context(context: dict[str, Any], fallback_path: str) -> str:
    repo_relative = str(context.get("repo_relative_path") or fallback_path).replace("\\", "/")
    return generate_test_command(repo_relative)


def _file_context(atlas: dict[str, Any], project_key: str, rel_path: str) -> dict[str, Any]:
    rel_path = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    project_data = atlas.get(project_key, {}) if isinstance(atlas, dict) else {}
    files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
    meta = files.get(rel_path) if isinstance(files, dict) else None
    atlas_rel = rel_path
    if not isinstance(meta, dict) and isinstance(files, dict):
        for key, value in files.items():
            if isinstance(value, dict) and str(value.get("workspace_rel") or "").replace("\\", "/") == rel_path:
                meta = value
                atlas_rel = str(key).replace("\\", "/")
                break
    if not isinstance(meta, dict):
        meta = {}
    workspace_rel = str(meta.get("workspace_rel") or atlas_rel).replace("\\", "/")
    return {
        "project": project_key,
        "repo_relative_path": workspace_rel,
        "atlas_relative_path": atlas_rel,
        "atlas_node": f"{project_key}::{atlas_rel}" if project_key and atlas_rel else "",
    }


def _resolve_target(atlas: dict[str, Any], target_path_or_node: str) -> tuple[str, str, str, dict[str, Any]]:
    target_node = str(target_path_or_node or "").replace("\\", "/").strip()
    target_rel = target_node
    project_key = "MAIN"
    if "::" in target_node:
        project_key, target_rel = target_node.split("::", 1)
    else:
        for pkey, pdata in atlas.items():
            files = pdata.get("files", {}) if isinstance(pdata, dict) else {}
            if not isinstance(files, dict):
                continue
            if target_rel in files:
                project_key = pkey
                target_node = f"{pkey}::{target_rel}"
                break
            for rel_path, meta in files.items():
                if isinstance(meta, dict) and str(meta.get("workspace_rel") or "").replace("\\", "/") == target_rel:
                    project_key = pkey
                    target_rel = str(rel_path).replace("\\", "/")
                    target_node = f"{pkey}::{target_rel}"
                    break
            if "::" in target_node:
                break
    target_rel = target_rel.replace("\\", "/")
    context = _file_context(atlas, project_key, target_rel)
    return project_key, target_rel, target_node, context

def _get_impact_graph(circular_deps: dict[str, Any] | None = None) -> tuple:
    deps_path = RAW_DIR / "circular_deps.json"
    try:
        data = circular_deps if isinstance(circular_deps, dict) else load_json_file(deps_path, {})
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])
        
        # Build reverse adjacency list (dependents)
        rev_adj = {node: [] for node in nodes}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if target in rev_adj and source in rev_adj:
                rev_adj[target].append(source)
                
        return nodes, rev_adj
    except Exception as e:
        logger.warning(f"Failed to load dependency graph for test matching: {e}")
        return None, None

def get_reachable_dependents(start_node: str, rev_adj: dict) -> Set[str]:
    visited = set()
    queue = [start_node]
    while queue:
        current = queue.pop(0)
        if current not in visited:
            visited.add(current)
            queue.extend(rev_adj.get(current, []))
    return visited - {start_node}

def find_impacted_tests(
    target_path_or_node: str,
    *,
    atlas: dict[str, Any] | None = None,
    circular_deps: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Main algorithmic entry point for matching a changed file to test candidates.
    Uses dual vectors: Static Dependency Tracing and Semantic Convention Mapping.
    """
    atlas = atlas if isinstance(atlas, dict) else project_runtime_atlas(load_atlas_data())[0]
    if not atlas:
        return {"target": target_path_or_node, "impacted_tests": [], "error": "Atlas not loaded."}
        
    # 1. Target Normalization
    project_key, target_rel, target_node, target_context = _resolve_target(atlas, target_path_or_node)
    
    # 2. Extract base name for naming conventions
    target_base = extract_base_name(target_rel)
    
    impacted_tests_dict = {}
    
    # --- Vector A: Static Dependency Tracing ---
    nodes, rev_adj = _get_impact_graph(circular_deps)
    if nodes and target_node in nodes:
        direct_dependents = rev_adj.get(target_node, [])
        all_dependents = get_reachable_dependents(target_node, rev_adj)
        
        # Check direct dependents (Confidence: 1.0)
        for dep in direct_dependents:
            _, dep_rel = dep.split("::", 1) if "::" in dep else ("UNKNOWN", dep)
            if is_test_file(dep_rel):
                dep_context = _file_context(atlas, dep.split("::", 1)[0] if "::" in dep else project_key, dep_rel)
                impacted_tests_dict[dep_rel] = {
                    "file": dep_rel,
                    "project": dep.split("::", 1)[0] if "::" in dep else project_key,
                    "repo_relative_path": dep_context.get("repo_relative_path"),
                    "atlas_node": dep,
                    "type": "Direct Static Import",
                    "confidence": confidence_value("direct_static_import"),
                    "run_command": _test_command_for_context(dep_context, dep_rel)
                }
                
        # Check transitive dependents (Confidence: 0.6)
        for dep in all_dependents:
            _, dep_rel = dep.split("::", 1) if "::" in dep else ("UNKNOWN", dep)
            if dep_rel not in impacted_tests_dict and is_test_file(dep_rel):
                dep_context = _file_context(atlas, dep.split("::", 1)[0] if "::" in dep else project_key, dep_rel)
                impacted_tests_dict[dep_rel] = {
                    "file": dep_rel,
                    "project": dep.split("::", 1)[0] if "::" in dep else project_key,
                    "repo_relative_path": dep_context.get("repo_relative_path"),
                    "atlas_node": dep,
                    "type": "Transitive Static Dependency",
                    "confidence": confidence_value("transitive_static_dependency"),
                    "run_command": _test_command_for_context(dep_context, dep_rel)
                }
                
    # --- Vector B: Semantic Convention Match ---
    # Naming-convention matches are scoped to the target project; cross-project
    # matches require a real static graph edge rather than name similarity.
    semantic_projects = [(project_key, atlas.get(project_key, {}))] if project_key in atlas else []
    for pkey, pdata in semantic_projects:
        files = pdata.get("files", {})
        for rel_path in files.keys():
            if is_test_file(rel_path):
                test_base = extract_base_name(rel_path)
                if test_base == target_base:
                    # Found naming match!
                    if rel_path in impacted_tests_dict:
                        # Promote to Dual Vector Match if already statically matching
                        existing = impacted_tests_dict[rel_path]
                        existing["type"] = "Dual Vector Match"
                        existing["confidence"] = confidence_value("dual_vector_match")
                    else:
                        rel_context = _file_context(atlas, pkey, rel_path)
                        impacted_tests_dict[rel_path] = {
                            "file": rel_path,
                            "project": pkey,
                            "repo_relative_path": rel_context.get("repo_relative_path"),
                            "atlas_node": f"{pkey}::{rel_path}",
                            "type": "Semantic Convention Match",
                            "confidence": confidence_value("semantic_convention_match"),
                            "run_command": _test_command_for_context(rel_context, rel_path)
                        }
                        
    # Sort matched tests by confidence (descending) and path name
    sorted_tests = sorted(
        impacted_tests_dict.values(),
        key=lambda item: (-item["confidence"], item["file"])
    )
    
    return {
        "target": target_node,
        "target_ref": f"{target_context.get('project')}::{target_context.get('repo_relative_path')}"
        if target_context.get("project") and target_context.get("repo_relative_path")
        else target_node,
        "target_project": target_context.get("project") or project_key,
        "target_file": target_context.get("repo_relative_path"),
        "target_file_context": target_context,
        "impacted_tests": sorted_tests
    }

def run_test_impact_matcher(target_path_or_node: str = None):
    """
    CLI/Orchestrator runner entry point. Performs matching, prints report, and saves JSON output.
    """
    logger.info("Running Test-Impact Matcher (value flow)...")
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    circular_deps = load_json_file(RAW_DIR / "circular_deps.json", {})

    if not target_path_or_node:
        # Resolve target dynamically from changed files if possible, or fallback to first project files
        if atlas:
            default_project = "MAIN" if "MAIN" in atlas else list(atlas.keys())[0]
            first_files = list(atlas[default_project].get("files", {}).keys())
            if first_files:
                # Find a non-test source file
                for f in first_files:
                    if not is_test_file(f):
                        target_path_or_node = f"{default_project}::{f}"
                        break
                        
    if not target_path_or_node:
        logger.error("No valid targets found for test impact mapping.")
        return False
        
    result = find_impacted_tests(
        target_path_or_node,
        atlas=atlas,
        circular_deps=circular_deps,
    )
    
    # Save test impact results dynamically
    output_path = RAW_DIR / "test_impact_report.json"
    save_json_atomic(output_path, result)
    write_current_atlas_lineage(
        artifact_id="test_impact_report",
        producer="tools.engines.test_impact_matcher",
        artifact_payload=result,
        atlas=atlas,
        dependency_payloads={"circular_deps": circular_deps},
    )
    
    # Pretty print ASCII report
    print("\n" + "=" * 60)
    print(f"=== TEST-IMPACT MATCH REPORT: {result['target']} ===")
    print("=" * 60)
    
    tests = result.get("impacted_tests", [])
    if not tests:
        print("[-] No matching test candidates detected for this file.")
        print("    Ensure test naming conventions or imports are correctly configured.")
    else:
        print(f"[+] Found {len(tests)} impacted test candidates:\n")
        print(f"{'CONFIDENCE':<12} | {'TYPE':<30} | {'TEST FILE'}")
        print("-" * 80)
        for t in tests:
            conf_str = f"{t['confidence'] * 100:.0f}%"
            print(f"{conf_str:<12} | {t['type']:<30} | {t['file']}")
            print(f"  > Run command: {t['run_command']}\n")
            
    print("=" * 60 + "\n")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", help="Normalized target file node (e.g. MAIN::src/domain/book_chapter.py)")
    args = parser.parse_args()
    
    success = run_test_impact_matcher(args.target)
    sys.exit(0 if success else 1)

import json
import os
import re
from pathlib import Path

from tools.core.json_io import load_json_file
from tools.core.fractal_io import load_fractal_map_data

class LookupEngine:
    """
    Sovereign Lookup Engine for Destination-Aware Semantic Mapping.
    Uses fractal_map.json and decision_evidence.json to resolve Variation imports to MAIN aliases.
    """
    def __init__(self, workspace_root):
        self.workspace_root = workspace_root
        self.fractal_map_path = os.path.join(workspace_root, "output", ".raw", "fractal_map.json")
        self.decision_evidence_path = os.path.join(workspace_root, "output", ".raw", "decision_evidence.json")
        
        self.main_files = {} # path -> file_info
        self.variation_to_main = {} # (project, path) -> target_main_path
        self.name_to_main_path = {} # basename -> [full_paths]
        
        # Load architecture doctrine for naming/proximity rules
        doctrine_path = os.path.join(workspace_root, "config", "architecture_doctrine.json")
        if os.path.exists(doctrine_path):
            with open(doctrine_path, 'r', encoding='utf-8') as f:
                self.doctrine = json.load(f)
        else:
            self.doctrine = {}
        
        self._load_data()

    def _load_data(self):
        # 1. Load Fractal Map
        data = load_fractal_map_data(Path(self.fractal_map_path).parent)
        projects = data.get("projects", {}) if isinstance(data, dict) else {}
        if "MAIN" in projects:
            for f_meta in projects["MAIN"].get("files", []):
                path = f_meta["path"]
                self.main_files[path] = f_meta
                basename = os.path.basename(path)
                if basename not in self.name_to_main_path:
                    self.name_to_main_path[basename] = []
                self.name_to_main_path[basename].append(path)

        # 2. Load Decision Evidence (The mapping truth)
        evidence = load_json_file(Path(self.decision_evidence_path), {})
        # [PHASE 14] Sovereign Evidence Loading: Map variation paths to trusted targets
        for candidate in evidence.get("focus_deep_dive", {}).get("trusted_top_candidates", []) if isinstance(evidence, dict) else []:
            project = candidate.get("source")
            target = candidate.get("target", "")
            if project and target:
                clean_target = self._sanitize_path(target)
                self.variation_to_main[(project, candidate["name"])] = clean_target

    def _sanitize_path(self, path: str) -> str:
        """Applies recursive path sanitization rules from naming doctrine."""
        naming = self.doctrine.get("naming_doctrine", {})
        rules = naming.get("path_sanitization_rules", [])
        clean_path = path.replace("\\", "/").strip("/")
        for rule in rules:
            pattern, replacement = rule.get("pattern"), rule.get("replacement", "")
            if pattern: clean_path = re.sub(pattern, replacement, clean_path)
        return clean_path

    def _calculate_proximity_score(self, candidate_path: str, context_meta: dict) -> int:
        """Scores a candidate path based on architectural proximity to the context."""
        from tools.core.layer_resolver import resolve_layer
        from tools.core.studio_resolver import studio_from_generic_path
        weights = self.doctrine.get("analysis_heuristics", {}).get("proximity_weights", {})
        score = 0
        c_layer = resolve_layer(candidate_path)
        c_studio, _ = studio_from_generic_path(candidate_path)
        if c_studio == context_meta.get('studio'): score += weights.get('same_studio', 0)
        if c_layer == context_meta.get('layer'): score += weights.get('same_layer', 0)
        return score

    def resolve_import_to_alias(self, source_project, current_file_path, import_str):
        """Resolves an import from a variation file to a canonical alias in MAIN."""
        from tools.core.layer_resolver import resolve_layer
        from tools.core.studio_resolver import studio_from_generic_path
        
        ref_dir = os.path.dirname(current_file_path)
        resolved_variation_path = os.path.normpath(os.path.join(ref_dir, import_str)).replace("\\", "/")
        basename = os.path.basename(resolved_variation_path)
        potential_basenames = [basename]
        if "." not in basename:
             potential_basenames = [basename + ".ts", basename + ".tsx", basename + ".js", basename + ".jsx"]

        naming = self.doctrine.get("naming_doctrine", {})
        alias_prefix = naming.get("alias_prefix", "@/")
        context_meta = {'layer': resolve_layer(current_file_path), 'studio': studio_from_generic_path(current_file_path)[0]}

        best_candidate, highest_score = None, -1
        for b in potential_basenames:
            if b in self.name_to_main_path:
                for cand in self.name_to_main_path[b]:
                    score = self._calculate_proximity_score(cand, context_meta)
                    if cand == resolved_variation_path: score += 100
                    if score > highest_score:
                        highest_score = score
                        best_candidate = cand

        if best_candidate:
            return f"{alias_prefix}{self._sanitize_path(best_candidate)}"
        return None

# Singleton-like instance for internal use
_instance = None
def get_lookup_engine(root):
    global _instance
    if _instance is None:
        _instance = LookupEngine(root)
    return _instance

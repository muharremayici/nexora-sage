import os
import json
from pathlib import Path
from typing import List, Dict, Set
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.config import ROOT, RAW_DIR, SANCTUARY_DIR
from tools.core.json_io import load_json_file
from tools.core.logger import logger

class ConvergenceEngine:
    """
    Sovereign Symbol Recovery & Dependency Convergence.
    Leverages Atlas SSOT and Oracle warnings to auto-stage missing dependencies.
    """
    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
        self._atlas = None

    def _load_atlas(self):
        if self._atlas is None:
            try:
                self._atlas = project_runtime_atlas(load_atlas_data())[0]
            except Exception as e:
                logger.error(f"[CONVERGENCE] Failed to load Atlas payload: {e}")
                self._atlas = {}
        return self._atlas

    def recover_dangling_imports(self, project_name: str) -> List[str]:
        """
        Analyzes the Oracle report for a project and attempts to find missing files
        in the variation project using Atlas.
        Returns a list of relative paths to newly discovered files that should be staged.
        """
        oracle_report_path = self.workspace_root / "output" / "reports" / f"oracle_{project_name.lower()}.json"
        if not oracle_report_path.exists():
            # Silently return if no report - normal for first-pass runs
            return []

        try:
            with open(oracle_report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
        except Exception as e:
            logger.error(f"[CONVERGENCE] Failed to read Oracle report: {e}")
            return []

        broken_imports = report.get("broken_imports", [])
        if not broken_imports:
            return []

        atlas = self._load_atlas()
        project_atlas = atlas.get(project_name, {})
        if not project_atlas:
            logger.warning(f"[CONVERGENCE] No Atlas found for {project_name}")
            return []

        recovered_files = set()
        for broken in broken_imports:
            alias = broken.get("import", "").replace("\\", "/") # Normalize alias
            if not alias.startswith("@/"):
                continue

            # 1. Try direct path resolution
            # Variations might be flat (root/stores) or nested (root/src/stores)
            candidates = [
                alias.replace("@/", "").lstrip("/"), # flat search
                alias.replace("@/", "src/").lstrip("/") # src search
            ]
            
            source_file = None
            for cand in candidates:
                source_file = self._find_source_in_atlas(project_atlas, cand)
                if source_file:
                    break
            
            if source_file:
                recovered_files.add(source_file)
                logger.info(f"[CONVERGENCE] Recovered file for {alias}: {source_file}")
                continue

            # 2. Try deep symbol DNA lookup across all Variation projects
            # If it's missing in PROJECT_A, maybe it's in PROJECT_B?
            # Derive a probable symbol name from the alias (e.g. "@/hooks/useAuth" -> "useAuth")
            path_parts = alias.split("/")
            symbol_name = path_parts[-1].split(".")[0] if path_parts else ""

            if symbol_name and symbol_name.startswith("use"):
                 # It's a hook or service-like, try to find it in any project atlas
                 found_file, found_project = self._deep_search_symbol(symbol_name)
                 if found_file:
                     recovered_files.add((found_file, found_project))
                     logger.info(f"[CONVERGENCE] Deep Recovered {symbol_name} from {found_project}: {found_file}")

        # Return a list of (rel_path, source_project)
        return list(recovered_files)

    def _deep_search_symbol(self, symbol_name: str) -> tuple[str, str]:
        """Searches all project atlases for a specific symbol."""
        atlas = self._load_atlas()
        for pkey, pdata in atlas.items():
            if pkey == "MAIN": continue
            # Check symbols dict
            syms = pdata.get("symbols", {})
            if symbol_name in syms:
                return syms[symbol_name].get("file"), pkey
            
            # Check file exports
            for rel_path, fdata in pdata.get("files", {}).items():
                if symbol_name in fdata.get("exports", []):
                    return rel_path, pkey
        return None, None

    def _find_source_in_atlas(self, project_atlas: Dict, candidate: str) -> str:
        files = project_atlas.get("files", {})
        # Precise match or with extensions
        for ext in ["", ".ts", ".tsx", ".js", ".jsx"]:
            cand_with_ext = candidate + ext
            if cand_with_ext in files:
                return cand_with_ext
            
            posix_cand = cand_with_ext.replace("\\", "/")
            if posix_cand in files:
                return posix_cand
        return None

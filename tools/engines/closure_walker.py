import json
import os
import re
import sys
import hashlib

# Ensure 'tools' is discoverable when running from the Nexora SAGE root
if os.getcwd() not in sys.path:
    sys.path.append(os.getcwd())

from tools.core.config import RAW_DIR, ROOT, save_json_atomic
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import canonical_project_name, resolve_runtime_projects

SAFE_SYMBOL_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def _closure_artifact_name(symbol_name: str) -> str:
    raw_name = str(symbol_name or "").strip()
    sanitized = SAFE_SYMBOL_RE.sub("_", raw_name).strip("._")
    digest = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:12]
    return f"closure_{sanitized or 'symbol'}_{digest}.json"


class ClosureWalker:
    def __init__(self, target_project=None, target_file=None, target_symbol=None):
        self.target_project = canonical_project_name(target_project) if target_project else None
        self.target_file = target_file
        self.target_symbol = target_symbol

        self.genome = load_genome_data()
        self.projects = resolve_runtime_projects(ROOT)

        self.visited_symbols = set()
        self.internal_closure = {}
        self.external_imports = set()
        self.external_contract_sources = set()
        self.member_dependency_sources = set()
        self.member_dependencies = set()
        self.ui_dependencies = set()
        self.member_ui_dependencies = set()
        self.member_dynamic_imports = set()
        self.architectural_markers = set()
        self.member_architectural_markers = set()
        self.member_side_effect_markers = set()
        self.member_side_effect_imports = set()
        self.studio_dependencies = []

        self.bundle = {
            "meta": {
                "target_symbol": target_symbol,
                "target_project": target_project,
                "target_file": target_file,
                "closure_level": "Symbolic-Recursive",
            },
            "source_symbol": {},
            "internal_closure": [],
            "external_manifest": {
                "studio_deps": [],
                "global_imports": [],
                "imported_contracts": [],
                "member_imported_contracts": [],
                "member_dependencies": [],
                "ui_dependencies": [],
                "member_ui_dependencies": [],
                "member_dynamic_imports": [],
                "architectural_markers": [],
                "member_architectural_markers": [],
                "member_side_effect_markers": [],
                "member_side_effect_imports": [],
            },
        }

    def walk(self):
        logger.info(f"Starting closure walk: {self.target_symbol}")

        occs = self.genome.get(self.target_symbol, [])
        root_occ = next(
            (
                occurrence
                for occurrence in occs
                if canonical_project_name(occurrence.get("project", "")) == self.target_project
                and occurrence.get("file") == self.target_file
            ),
            None,
        )

        if not root_occ:
            logger.error(f"Root symbol {self.target_symbol} not found in genome for {self.target_project}")
            return None

        self.bundle["source_symbol"] = root_occ
        self._resolve_recursive(self.target_symbol, self.target_file)

        self.bundle["internal_closure"] = list(self.internal_closure.values())
        self.bundle["external_manifest"]["studio_deps"] = self.studio_dependencies
        self.bundle["external_manifest"]["global_imports"] = sorted(list(self.external_imports))
        self.bundle["external_manifest"]["imported_contracts"] = sorted(list(self.external_contract_sources))
        self.bundle["external_manifest"]["member_imported_contracts"] = sorted(list(self.member_dependency_sources))
        self.bundle["external_manifest"]["member_dependencies"] = sorted(list(self.member_dependencies))
        self.bundle["external_manifest"]["ui_dependencies"] = sorted(list(self.ui_dependencies))
        self.bundle["external_manifest"]["member_ui_dependencies"] = sorted(list(self.member_ui_dependencies))
        self.bundle["external_manifest"]["member_dynamic_imports"] = sorted(list(self.member_dynamic_imports))
        self.bundle["external_manifest"]["architectural_markers"] = sorted(list(self.architectural_markers))
        self.bundle["external_manifest"]["member_architectural_markers"] = sorted(list(self.member_architectural_markers))
        self.bundle["external_manifest"]["member_side_effect_markers"] = sorted(list(self.member_side_effect_markers))
        self.bundle["external_manifest"]["member_side_effect_imports"] = sorted(list(self.member_side_effect_imports))

        output_path = RAW_DIR / _closure_artifact_name(self.target_symbol)
        save_json_atomic(output_path, self.bundle)
        logger.info(f"[OK] Closure bundle generated: {output_path}")
        return self.bundle

    def _resolve_recursive(self, symbol_name, current_file):
        if symbol_name in self.visited_symbols:
            return
        self.visited_symbols.add(symbol_name)

        occs = self.genome.get(symbol_name, [])
        occurrence = next(
            (
                item
                for item in occs
                if canonical_project_name(item.get("project", "")) == self.target_project
                and item.get("file") == current_file
            ),
            None,
        )

        if not occurrence:
            occurrence = next(
                (
                    item
                    for item in occs
                    if canonical_project_name(item.get("project", "")) == self.target_project
                ),
                None,
            )
            if occurrence:
                if occurrence["name"] not in [symbol["name"] for symbol in self.studio_dependencies]:
                    self.studio_dependencies.append(occurrence)
                return

            self.external_imports.add(symbol_name)
            return

        if current_file == self.target_file and symbol_name != self.target_symbol:
            self.internal_closure[symbol_name] = occurrence

        for imported_contract in occurrence.get("imported_contracts", []):
            self.external_contract_sources.add(imported_contract)

        for imported_contract in occurrence.get("member_imported_contracts", []):
            self.member_dependency_sources.add(imported_contract)

        for dependency in occurrence.get("member_dependencies", []):
            self.member_dependencies.add(dependency)

        for ui_dependency in occurrence.get("ui_dependencies", []):
            self.ui_dependencies.add(ui_dependency)

        for ui_dependency in occurrence.get("member_ui_dependencies", []):
            self.member_ui_dependencies.add(ui_dependency)

        for dynamic_import in occurrence.get("member_dynamic_imports", []):
            self.member_dynamic_imports.add(dynamic_import)

        for marker in occurrence.get("architectural_markers", []):
            self.architectural_markers.add(marker)

        for marker in occurrence.get("member_architectural_markers", []):
            self.member_architectural_markers.add(marker)

        for marker in occurrence.get("member_side_effect_markers", []):
            self.member_side_effect_markers.add(marker)

        for imported_contract in occurrence.get("member_side_effect_imports", []):
            self.member_side_effect_imports.add(imported_contract)

        for ref in occurrence.get("symbols_referenced", []):
            self._resolve_recursive(ref, current_file)


def run_closure_walker_readiness():
    genome = load_genome_data()
    projects = resolve_runtime_projects(ROOT)
    symbol_count = len(genome) if isinstance(genome, dict) else 0
    payload = {
        "meta": {
            "kind": "closure_walker_readiness",
            "version": "v1",
            "mode": "pipeline_readiness",
            "claim_boundary": "service_available_for_targeted_symbol_closure",
        },
        "status": "ready" if symbol_count > 0 else "not_ready",
        "source_truth": {
            "genome_loaded": isinstance(genome, dict),
            "symbol_count": symbol_count,
            "project_count": len(projects),
            "project_keys": sorted(projects.keys()),
        },
        "on_demand_outputs": {
            "artifact_pattern": "closure_*",
            "operation": "ClosureWalker(target_project, target_file, target_symbol).walk()",
            "note": "Pipeline readiness does not claim a target-specific closure artifact was generated.",
        },
    }
    save_json_atomic(RAW_DIR / "closure_walker_readiness.json", payload)
    logger.info(
        "Closure Walker readiness written: symbols=%s projects=%s",
        symbol_count,
        len(projects),
    )
    return payload


if __name__ == "__main__":
    runtime_projects = list(resolve_runtime_projects(ROOT).keys())
    default_project = runtime_projects[0] if runtime_projects else "MAIN"
    symbol = sys.argv[1] if len(sys.argv) > 1 else "useStudioLogic"
    proj = sys.argv[2] if len(sys.argv) > 2 else default_project
    genome = load_genome_data()
    occs = genome.get(symbol, [])
    target_occ = next((o for o in occs if canonical_project_name(o.get("project", "")) == proj), None)
    if target_occ:
        walker = ClosureWalker(proj, target_occ["file"], symbol)
        walker.walk()
    else:
        print(f"Error: Symbol {symbol} not found in project {proj}")

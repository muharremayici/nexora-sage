import argparse
import json
import os
from collections import defaultdict

from tools.core.atlas_io import load_atlas_data as load_shared_atlas_data
from tools.core.config import ROOT, RAW_DIR, REPORTS_DIR, DOCTRINE, save_json_atomic, save_text_atomic
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import resolve_runtime_projects

from tools.core.db import get_current_tenant

class SizeBoundedDict(dict):
    def __init__(self, max_size=10, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_size = max_size
        self._keys_order = list(self.keys())

    def __setitem__(self, key, value):
        if key in self:
            self._keys_order.remove(key)
        self._keys_order.append(key)
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            oldest = self._keys_order.pop(0)
            super().pop(oldest, None)

    def pop(self, key, default=None):
        if key in self._keys_order:
            self._keys_order.remove(key)
        return super().pop(key, default)

    def clear(self):
        self._keys_order.clear()
        super().clear()

class TenantIsolatedCache(dict):
    def __init__(self, max_size=10):
        self._tenants_map = SizeBoundedDict(max_size=max_size)

    def _get_current_dict(self):
        tenant_id = get_current_tenant()
        if tenant_id not in self._tenants_map:
            self._tenants_map[tenant_id] = {}
        return self._tenants_map[tenant_id]

    def __getitem__(self, key):
        return self._get_current_dict()[key]

    def __setitem__(self, key, value):
        self._get_current_dict()[key] = value

    def __delitem__(self, key):
        del self._get_current_dict()[key]

    def __contains__(self, key):
        return key in self._get_current_dict()

    def __iter__(self):
        return iter(self._get_current_dict())

    def get(self, key, default=None):
        return self._get_current_dict().get(key, default)

    def setdefault(self, key, default=None):
        return self._get_current_dict().setdefault(key, default)

    def update(self, *args, **kwargs):
        self._get_current_dict().update(*args, **kwargs)

    def clear(self):
        self._get_current_dict().clear()

    def pop(self, key, default=None):
        return self._get_current_dict().pop(key, default)

    def keys(self):
        return self._get_current_dict().keys()

    def values(self):
        return self._get_current_dict().values()

    def items(self):
        return self._get_current_dict().items()

    def __len__(self):
        return len(self._get_current_dict())

    def __bool__(self):
        return bool(self._get_current_dict())

    def __repr__(self):
        return f"TenantIsolatedCache(current_tenant={get_current_tenant()}, data={self._get_current_dict()!r})"

GLOBAL_UI_CACHE = TenantIsolatedCache(max_size=10)



class UIMapper:
    def __init__(self, stale_projects=None):
        self.projects = resolve_runtime_projects(ROOT)
        from tools.core.doctrine_contract import require_doctrine_path
        self.ui_dirs = require_doctrine_path("ui_discovery", "dirs", expected_type=list)
        self.stale_projects = stale_projects
        self.previous_results = {}
        self.file_cache = {}

    def load_previous_results(self):
        global GLOBAL_UI_CACHE
        if 'data' in GLOBAL_UI_CACHE:
            self.previous_results = GLOBAL_UI_CACHE['data']
            self.file_cache = GLOBAL_UI_CACHE['file_cache']
            return self.previous_results

        path = RAW_DIR / 'ui_architecture_map.json'
        try:
            data = load_json_file(path, {})
            self.previous_results = data
            file_cache = {}
            for pkey, categories in data.items():
                for _, files in categories.items():
                    for item in files:
                        file_cache[(pkey, item['file'])] = item
            self.file_cache = file_cache
            GLOBAL_UI_CACHE['data'] = data
            GLOBAL_UI_CACHE['file_cache'] = file_cache
            return data
        except Exception as exc:
            logger.warning(f"Could not load previous UI data: {exc}")
            return {}

    def load_atlas_data(self):
        return load_shared_atlas_data()

    def categorize_file(self, rel_path, features):
        path_lower = rel_path.lower().replace('\\', '/')
        
        ui_config = require_doctrine_mapping("ui_discovery")
        categories = ui_config.get("structural_categories", {})
        structural = ui_config.get("default_structural", "Components")
        
        for trigger, label in categories.items():
            if trigger in path_lower:
                structural = label
                break

        functional_matches = [feature.split(':', 1)[1] for feature in features if feature.startswith('Cat:')]
        return {'structural': structural, 'functional': list(set(functional_matches))}

    def scan_projects(self, changed_files=None):
        logger.info("Starting sovereign UI mapping scan (Atlas-Only)...")
        self.load_previous_results()
        atlas = self.load_atlas_data()

        final_results = defaultdict(lambda: defaultdict(list))
        for pkey, categories in self.previous_results.items():
            if self.stale_projects and pkey not in self.stale_projects:
                for category, files in categories.items():
                    final_results[pkey][category] = list(files)

        for pkey, _ in self.projects.items():
            if self.stale_projects is not None and pkey not in self.stale_projects:
                continue

            surgical_files = None
            if changed_files:
                surgical_files = [item.split("::", 1)[1] for item in changed_files if item.startswith(f"{pkey}::")]

            project_atlas = atlas.get(pkey, {})
            atlas_files = project_atlas.get("files", {})

            if surgical_files:
                target_rel_paths = set(surgical_files)
                for category in list(final_results[pkey].keys()):
                    final_results[pkey][category] = [item for item in final_results[pkey][category] if item['file'] not in target_rel_paths]
                files_to_iter = [(rel, atlas_files[rel]) for rel in surgical_files if rel in atlas_files]
            else:
                final_results[pkey] = defaultdict(list)
                files_to_iter = atlas_files.items()

            for rel_path, atlas_file in files_to_iter:
                normalized = rel_path.lower().replace('\\', '/')
                is_ui_file = rel_path.endswith(('.tsx', '.jsx')) or (
                    rel_path.endswith(('.ts', '.js')) and any(ui_dir in normalized for ui_dir in self.ui_dirs)
                )
                if not is_ui_file:
                    continue

                symbols_raw = atlas_file.get("symbols", [])
                all_features = set(atlas_file.get("features", []))
                for symbol in symbols_raw:
                    if isinstance(symbol, dict):
                        all_features.update(symbol.get("features", []))

                ui_symbols = {
                    'all_exports': [symbol['name'] for symbol in symbols_raw if isinstance(symbol, dict) and symbol['name'] != '__file_meta__'],
                    'hooks': [symbol['name'] for symbol in symbols_raw if isinstance(symbol, dict) and symbol.get('type') == 'Hook'],
                    'features': list(all_features),
                }

                categories = self.categorize_file(rel_path, list(all_features))
                entry = {
                    'file': rel_path,
                    'scoped_file': f"{pkey}::{rel_path}",
                    'loc': atlas_file.get('loc', 0),
                    'symbols': ui_symbols,
                    'features': list(all_features),
                    'functional_matches': categories['functional'],
                    'structural': categories['structural'],
                    'mtime': atlas_file.get('mtime', -1),
                    'hash': atlas_file.get('hash'),
                }
                final_results[pkey][categories['structural']].append(entry)

        return final_results

    def generate_report(self, results):
        report = ["# SOVEREIGN UI ARCHITECTURE MAP\n", "Generated via high-fidelity AST sequencing (v15.0).\n"]
        for pkey, structural_categories in sorted(results.items()):
            report.append(f"## Project: {pkey}")
            for category_name, files in sorted(structural_categories.items()):
                if not files:
                    continue
                report.append(f"### {category_name} ({len(files)} files)")
                files.sort(key=lambda item: item['loc'], reverse=True)
                for item in files:
                    if item['loc'] < 5:
                        continue
                    scoped_file = item.get('scoped_file') or f"{pkey}::{item['file']}"
                    report.append(f"- **{os.path.basename(item['file'])}** (LOC: {item['loc']}) - `{scoped_file}`")
                    if item['functional_matches']:
                        report.append(f"  - `Concepts`: {', '.join(item['functional_matches'])}")
            report.append("")

        output_file = REPORTS_DIR / 'ui_architecture_map.md'
        save_text_atomic(output_file, "\n".join(report))
        json_file = RAW_DIR / 'ui_architecture_map.json'
        save_json_atomic(json_file, dict(results))
        logger.info("[OK] Sovereign UI report & JSON updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--stale-projects', help="Comma-separated list of stale projects")
    parser.add_argument('--changed-files', help="Comma-separated list of changed files")
    args = parser.parse_args()

    stale_list = args.stale_projects.split(',') if args.stale_projects else None
    changed_list = args.changed_files.split(',') if args.changed_files else None
    mapper = UIMapper(stale_projects=stale_list)
    results = mapper.scan_projects(changed_files=changed_list)
    mapper.generate_report(results)

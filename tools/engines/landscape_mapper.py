import os
import json
from collections import defaultdict
from tools.core.atlas_io import load_atlas_data
from tools.core.config import ROOT, RAW_DIR, REPORTS_DIR, save_json_atomic
from tools.core.json_io import load_json_file
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.semantic_tokens import ARCHITECTURAL_MARKERS
from tools.core.logger import logger

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

GLOBAL_LANDSCAPE_CACHE = TenantIsolatedCache(max_size=10)


class LandscapeMapper:
    def __init__(self, stale_projects=None):
        self.projects = resolve_runtime_projects(ROOT)
        self.stale_projects = stale_projects
        self.previous_results = {}
        self.file_cache = {'hierarchy': {}, 'architectural': {}, 'concepts': {}}
        self.atlas_data = load_atlas_data()

    def load_previous_results(self):
        global GLOBAL_LANDSCAPE_CACHE
        if 'data' in GLOBAL_LANDSCAPE_CACHE and 'file_cache' in GLOBAL_LANDSCAPE_CACHE:
            self.previous_results = GLOBAL_LANDSCAPE_CACHE['data']
            self.file_cache = GLOBAL_LANDSCAPE_CACHE['file_cache']
            return self.previous_results
        
        self.previous_results = {'hierarchy': {}, 'architectural': {}, 'concepts': {}}
        self.file_cache = {'hierarchy': {}, 'architectural': defaultdict(list), 'concepts': defaultdict(lambda: defaultdict(list))}

        try:
            data = load_json_file(RAW_DIR / 'landscape_map.json', self.previous_results)
            self.previous_results = data
            
            if 'hierarchy' in data:
                for pkey, dirs in data['hierarchy'].items():
                    for rel_dir, files in dirs.items():
                        for fname, meta in files.items():
                            rel_path = os.path.join('' if rel_dir == '/' else rel_dir, fname).replace('\\', '/')
                            self.file_cache['hierarchy'][(pkey, rel_path)] = meta
            if 'architectural' in data:
                for marker, items in data['architectural'].items():
                    for item in items: self.file_cache['architectural'][(item['project'], item['path'])].append(item)
            if 'concepts' in data:
                for pkey, cats in data['concepts'].items():
                    for cat, items in cats.items():
                        for item in items: self.file_cache['concepts'][(pkey, item['file'])][cat].append(item)

            GLOBAL_LANDSCAPE_CACHE['data'] = data
            GLOBAL_LANDSCAPE_CACHE['file_cache'] = self.file_cache
            return data
        except Exception as e:
            logger.warning(f"Could not load previous landscape data: {e}")
            return self.previous_results

    def _extract_symbols_from_atlas(self, a_data):
        symbols = {'classes': [], 'functions': [], 'hooks': []}
        for s in a_data.get('symbols', []):
            if not isinstance(s, dict): continue
            s_type = s.get('type')
            s_name = s.get('name')
            if s_name == '__file_meta__': continue
            if s_type == 'Class': symbols['classes'].append(s_name)
            elif s_type == 'Hook': symbols['hooks'].append(s_name)
            elif s_type in ('Function', 'Arrow'): symbols['functions'].append(s_name)
        return symbols

    def scan_hierarchy_surgical(self, pkey, ppath, changed_files=None):
        target_rel_paths = {f.split("::", 1)[1] for f in changed_files if f.startswith(f"{pkey}::")} if changed_files else None
        proj_land = defaultdict(dict)
        for (pk, rel), meta in self.file_cache['hierarchy'].items():
            if pk == pkey and (target_rel_paths is None or rel not in target_rel_paths):
                rel_dir = os.path.dirname(rel); proj_land['/' if rel_dir == '' else rel_dir][os.path.basename(rel)] = meta
        
        atlas_files = self.atlas_data.get(pkey, {}).get("files", {})
        files_to_iter = [(r, atlas_files[r]) for r in target_rel_paths if r in atlas_files] if target_rel_paths else atlas_files.items()
        for rel_path, a_data in files_to_iter:
            symbols = self._extract_symbols_from_atlas(a_data)
            symbols['loc'] = a_data.get('loc', 0)
            proj_land['/' if os.path.dirname(rel_path) == '' else os.path.dirname(rel_path)][os.path.basename(rel_path)] = symbols
        return proj_land

    def scan_architectural_surgical(self, pkey, ppath, changed_files=None):
        target_rel_paths = {f.split("::", 1)[1] for f in changed_files if f.startswith(f"{pkey}::")} if changed_files else None
        results = []
        for (pk, rel), items in self.file_cache['architectural'].items():
            if pk == pkey and (target_rel_paths is None or rel not in target_rel_paths): results.extend(items)
        
        atlas_files = self.atlas_data.get(pkey, {}).get("files", {})
        files_to_iter = [(r, atlas_files[r]) for r in target_rel_paths if r in atlas_files] if target_rel_paths else atlas_files.items()
        for rel_path, a_data in files_to_iter:
            for s in a_data.get('symbols', []):
                if not isinstance(s, dict): continue
                for feat in s.get('features', []):
                    if feat.startswith('Arch:'):
                        results.append(
                            {
                                'project': pkey,
                                'path': rel_path,
                                'scoped_path': f"{pkey}::{rel_path}",
                                'type': s.get('type'),
                                'name': s.get('name'),
                                'marker': feat.split(':', 1)[1],
                            }
                        )
        return results

    def scan_concepts_surgical(self, pkey, ppath, changed_files=None):
        target_rel_paths = {f.split("::", 1)[1] for f in changed_files if f.startswith(f"{pkey}::")} if changed_files else None
        proj_gems = defaultdict(list)
        for (pk, rel), cat_dict in self.file_cache['concepts'].items():
            if pk == pkey and (target_rel_paths is None or rel not in target_rel_paths):
                for cat, items in cat_dict.items(): proj_gems[cat].extend(items)
        
        atlas_files = self.atlas_data.get(pkey, {}).get("files", {})
        files_to_iter = [(r, atlas_files[r]) for r in target_rel_paths if r in atlas_files] if target_rel_paths else atlas_files.items()
        for rel_path, a_data in files_to_iter:
            # Aggregate all features (Symbol-level + File-level metadata symbol)
            all_feats = set(a_data.get("features", []))
            for s in a_data.get("symbols", []):
                if isinstance(s, dict): all_feats.update(s.get("features", []))
            
            for f in all_feats:
                if f.startswith('Tech:'):
                    token = f.split(':', 1)[1]
                    cat = next((feat.split(':', 1)[1] for feat in all_feats if feat.startswith('Cat:')), 'Other')
                    proj_gems[cat].append({'file': rel_path, 'scoped_file': f"{pkey}::{rel_path}", 'token': token})
        return proj_gems

    def generate_full_report(self, changed_files=None):
        logger.info("Generating sovereign landscape report (Atlas-Only)...")
        self.load_previous_results()
        hierarchy, arch_symbols, concepts = {}, [], {}
        
        for pkey, ppath in self.projects.items():
            if self.stale_projects is not None and pkey not in self.stale_projects and not changed_files:
                hierarchy[pkey] = self.previous_results['hierarchy'].get(pkey, {})
                concepts[pkey] = self.previous_results['concepts'].get(pkey, {})
                continue
            hierarchy[pkey] = self.scan_hierarchy_surgical(pkey, ppath, changed_files)
            arch_symbols.extend(self.scan_architectural_surgical(pkey, ppath, changed_files))
            concepts[pkey] = self.scan_concepts_surgical(pkey, ppath, changed_files)

        if self.stale_projects:
            for marker, items in self.previous_results.get('architectural', {}).items():
                for item in items:
                    if item['project'] not in self.stale_projects: arch_symbols.append(item)

        results = {
            'hierarchy': hierarchy,
            'architectural': {k: [i for i in arch_symbols if i['marker'] == k] for k in ARCHITECTURAL_MARKERS},
            'concepts': concepts
        }
        save_json_atomic(RAW_DIR / 'landscape_map.json', results)
        logger.info(f"[OK] Sovereign landscape map saved.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--stale-projects', help="Comma-separated list of stale projects")
    parser.add_argument('--changed-files', help="Comma-separated list of changed files")
    args = parser.parse_args()
    stale_list = args.stale_projects.split(',') if args.stale_projects else None
    changed_list = args.changed_files.split(',') if args.changed_files else None
    mapper = LandscapeMapper(stale_projects=stale_list)
    mapper.generate_full_report(changed_files=changed_list)

import os
import json
import argparse
import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path
from tools.core.atlas_io import load_atlas_data
from tools.core.config import ROOT, RAW_DIR, REPORTS_DIR, DOCTRINE, save_json_atomic
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.source_snapshot_reader import load_source_text
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

GLOBAL_KEYWORD_CACHE = TenantIsolatedCache(max_size=10)


def _payload_hash(payload) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except TypeError:
        encoded = repr(payload).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


class KeywordScanner:
    def __init__(self, stale_projects=None):
        self.projects = resolve_runtime_projects(ROOT)
        self.stale_projects = stale_projects
        self.previous_results = {}
        self._atlas_data = None
        self._genome = None
        self._genome_files_by_project = None
        self._cache_signature = None
        self._cache_current = None

    @property
    def atlas_data(self):
        if self._atlas_data is None:
            self._atlas_data = load_atlas_data()
        return self._atlas_data

    @property
    def genome(self):
        if self._genome is None:
            self._genome = load_genome_data()
        return self._genome

    def _state_payload_sha(self, name: str) -> str:
        db_path = RAW_DIR / "codemaps.db"
        if not db_path.exists():
            return ""
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT payload_sha FROM state_payloads WHERE name = ?;", (name,)).fetchone()
            return str(row[0] or "") if row else ""
        except Exception:
            return ""

    def _current_cache_signature(self):
        if self._cache_signature is None:
            commit = load_json_file(RAW_DIR / "atlas_commit.json", {})
            atlas_sha = self._state_payload_sha("atlas")
            genome_sha = self._state_payload_sha("genome")
            self._cache_signature = {
                "atlas_snapshot_id": str(commit.get("snapshot_id") or "") if isinstance(commit, dict) else "",
                "atlas_hash": atlas_sha or _payload_hash(self.atlas_data),
                "genome_hash": genome_sha or _payload_hash(self.genome),
            }
        return self._cache_signature

    def _cache_is_current(self) -> bool:
        if self._cache_current is not None:
            return bool(self._cache_current)
        meta = load_json_file(RAW_DIR / "keyword_scanner_cache_meta.json", {})
        self._cache_current = isinstance(meta, dict) and meta.get("signature") == self._current_cache_signature()
        return bool(self._cache_current)

    @staticmethod
    def _with_scoped_path(item, project=None):
        result = dict(item)
        pkey = project or result.get('project')
        rel_path = result.get('path')
        if pkey and rel_path:
            result['scoped_path'] = f"{pkey}::{rel_path}"
        return result

    def _get_project_data(self, mode, pkey, scanner_func):
        global GLOBAL_KEYWORD_CACHE
        if 'all' not in GLOBAL_KEYWORD_CACHE:
            p = RAW_DIR / 'keyword_scanner_all.json'
            if self._cache_is_current():
                try:
                    data = load_json_file(p, {})
                    processed = defaultdict(lambda: defaultdict(list))
                    if isinstance(data, dict):
                        for cat, items in data.items():
                            for item in items:
                                if 'project' in item:
                                    processed[cat][item['project']].append(self._with_scoped_path(item))
                    GLOBAL_KEYWORD_CACHE['all'] = processed
                except (json.JSONDecodeError, KeyError, TypeError): GLOBAL_KEYWORD_CACHE['all'] = defaultdict(lambda: defaultdict(list))
            else:
                GLOBAL_KEYWORD_CACHE['all'] = defaultdict(lambda: defaultdict(list))
        
        if self._cache_is_current() and (self.stale_projects is None or pkey not in self.stale_projects):
            cache = GLOBAL_KEYWORD_CACHE.get('all', {})
            cat_data = cache.get(mode, {})
            project_items = cat_data.get(pkey, [])
            if project_items:
                return [self._with_scoped_path(item, pkey) for item in project_items]

        return scanner_func(pkey, self.projects[pkey])

    def _get_files_from_genome(self, pkey):
        """Authoritative discovery: all files present in any genome entry for this project."""
        if self._genome_files_by_project is None:
            files_by_project = defaultdict(set)
            for occs in self.genome.values():
                for o in occs:
                    if not isinstance(o, dict):
                        continue
                    project = o.get("project")
                    file_path = o.get("file")
                    if project and file_path:
                        files_by_project[project].add(file_path)
            self._genome_files_by_project = files_by_project
        return set(self._genome_files_by_project.get(pkey, set()))

    def scan_file_stats(self, pkey, ppath, target_files=None):
        stats = []
        all_prev_stats = GLOBAL_KEYWORD_CACHE.get('all', {}).get('stats', {}).get(pkey, [])
        target_rel_paths = set(target_files) if target_files else None
        
        if target_rel_paths:
            for item in all_prev_stats:
                if item.get('path') not in target_rel_paths:
                    stats.append(self._with_scoped_path(item, pkey))
        
        atlas_files = self.atlas_data.get(pkey, {}).get("files", {})
        # FALLBACK: If atlas is empty for project, use genome discovery
        files_to_scan = target_rel_paths if target_rel_paths else (set(atlas_files.keys()) | self._get_files_from_genome(pkey))

        for rel_path in files_to_scan:
            a_data = atlas_files.get(rel_path, {})
            stats.append({
                'project': pkey,
                'path': rel_path,
                'scoped_path': f"{pkey}::{rel_path}",
                'lines': a_data.get('loc', 0),
                'size': 0,
                'file_hash': a_data.get('hash'),
                'mtime': a_data.get('mtime')
            })
        return stats

    def _greedy_content_scan(self, pkey, rel_path):
        """Greedy regex fallback to restore keyword coverage when Atlas is empty."""
        try:
            ppath = self.projects.get(pkey)
            if not ppath: return []
            
            full_path = os.path.join(ppath, rel_path)
            content = load_source_text(
                pkey,
                rel_path,
                fallback_path=Path(full_path),
                component="keyword_scanner",
            )
            if not content:
                return []
            
            found = set()
            glossary = DOCTRINE.get("discovery_taxonomy", {}).get("semantic_tokens", {})
            for cat, words in glossary.items():
                for word in words:
                    if word.lower() in content.lower():
                        found.add(cat)
                        break # Found one word in this category, move to next category
            return list(found)
        except Exception as e:
            logger.debug(f"Greedy scan failed for {rel_path}: {e}")
            return []

    def scan_keywords(self, pkey, ppath, target_files=None):
        results = []
        all_prev = GLOBAL_KEYWORD_CACHE.get('all', {}).get('keywords', {}).get(pkey, [])
        target_rel_paths = set(target_files) if target_files else None
        
        if target_rel_paths:
            for item in all_prev:
                if item.get('path') not in target_rel_paths:
                    results.append(self._with_scoped_path(item, pkey))
        
        atlas_files = self.atlas_data.get(pkey, {}).get("files", {})
        # FALLBACK: Discover from genome if target_files not provided
        files_to_scan = target_rel_paths if target_rel_paths else (set(atlas_files.keys()) | self._get_files_from_genome(pkey))

        for rel_path in files_to_scan:
            a_data = atlas_files.get(rel_path, {})
            all_features = set(a_data.get('features', []))
            for s in a_data.get('symbols', []):
                if isinstance(s, dict): all_features.update(s.get('features', []))
            
            # Greedy source scanning is a last-resort fallback for missing Atlas
            # coverage, not a normal second pass over every low-feature file.
            if len(all_features) < 2 and (not atlas_files or rel_path not in atlas_files):
                all_features.update(self._greedy_content_scan(pkey, rel_path))

            if all_features:
                results.append({
                    'project': pkey, 'path': rel_path, 'lines': a_data.get('loc', 0),
                    'scoped_path': f"{pkey}::{rel_path}",
                    'found': list(all_features), 'file_hash': a_data.get('hash'), 'mtime': a_data.get('mtime')
                })
        return results

    def score_gems(self, pkey, ppath, target_files=None):
        results = []
        all_prev = GLOBAL_KEYWORD_CACHE.get('all', {}).get('gems', {}).get(pkey, [])
        target_rel_paths = set(target_files) if target_files else None
        if target_rel_paths:
            for item in all_prev:
                if item.get('path') not in target_rel_paths:
                    results.append(self._with_scoped_path(item, pkey))
        
        atlas_files = self.atlas_data.get(pkey, {}).get("files", {})
        files_to_scan = target_rel_paths if target_rel_paths else (set(atlas_files.keys()) | self._get_files_from_genome(pkey))

        for rel_path in files_to_scan:
            a_data = atlas_files.get(rel_path, {})
            all_feats = set(a_data.get("features", []))
            for s in a_data.get("symbols", []):
                if isinstance(s, dict): all_feats.update(s.get("features", []))
            
            file_score = 0.0
            matched_categories = defaultdict(int)
            matched_words = []
            
            for f in all_feats:
                if f.startswith('GemScore:'): file_score = float(f.split(':', 1)[1])
                elif f.startswith('Tech:'): matched_words.append(f.split(':', 1)[1])
                elif f.startswith('Cat:'): matched_categories[f.split(':', 1)[1]] += 1
            
            if file_score > 0:
                results.append({
                    'project': pkey, 'path': rel_path, 'score': file_score,
                    'scoped_path': f"{pkey}::{rel_path}",
                    'matches': list(set(matched_words)), 'categories': dict(matched_categories),
                    'hash': a_data.get('hash'), 'mtime': a_data.get('mtime')
                })
        return results

    def run(self, mode, changed_files=None, write_standalone=True):
        logger.info(f"Running Keyword Scanner ({mode}) - Sovereign Mode...")
        results = []
        for pkey, ppath in self.projects.items():
            surgical_files = None
            if changed_files:
                surgical_files = [f.split("::", 1)[1] for f in changed_files if f.startswith(f"{pkey}::")]
            
            if mode == "stats": results.extend(self._get_project_data("stats", pkey, lambda pk, pp: self.scan_file_stats(pk, pp, surgical_files)))
            elif mode == "keywords": results.extend(self._get_project_data("keywords", pkey, lambda pk, pp: self.scan_keywords(pk, pp, surgical_files)))
            elif mode == "gems": results.extend(self._get_project_data("gems", pkey, lambda pk, pp: self.score_gems(pk, pp, surgical_files)))
        
        if write_standalone:
            output_file = RAW_DIR / f'keyword_scanner_{mode}.json'
            save_json_atomic(output_file, results)
        else:
            logger.info(
                "[KEYWORD_SCANNER_PROFILE] standalone keyword_scanner_%s.json shadow export suppressed; bundled evidence remains in keyword_scanner_all.json",
                mode,
            )
        
        if mode == "gems":
            combined = {
                "stats": load_json_file(RAW_DIR / 'keyword_scanner_stats.json', []),
                "keywords": load_json_file(RAW_DIR / 'keyword_scanner_keywords.json', []),
                "gems": results
            }
            save_json_atomic(RAW_DIR / 'keyword_scanner_all.json', combined)
            save_json_atomic(
                RAW_DIR / "keyword_scanner_cache_meta.json",
                {
                    "meta": {"kind": "keyword_scanner_cache_meta", "version": "v1"},
                    "signature": self._current_cache_signature(),
                },
            )
            self._cache_current = True
        return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['stats', 'keywords', 'gems', 'all'], default='all')
    parser.add_argument('--stale-projects', help="Comma-separated list of stale projects")
    parser.add_argument('--changed-files', help="Comma-separated list of changed files")
    args = parser.parse_args()
    stale_list = args.stale_projects.split(',') if args.stale_projects else None
    changed_list = args.changed_files.split(',') if args.changed_files else None
    scanner = KeywordScanner(stale_projects=stale_list)
    
    if args.mode == 'all':
        scanner.run("stats", changed_files=changed_list)
        scanner.run("keywords", changed_files=changed_list)
        scanner.run("gems", changed_files=changed_list)
    else:
        scanner.run(args.mode, changed_files=changed_list)

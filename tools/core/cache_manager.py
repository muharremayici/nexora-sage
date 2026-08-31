"""
Smart Cache Manager determines if the source files have changed since the last run.
Allows the pipeline to skip extracting engines and reuse previous JSON artifacts.
"""
import os
import json
import time
import hashlib
import tools.core.config as runtime_config
from tools.core.config import ROOT, RAW_DIR, SOURCE_EXTENSIONS, SKIP_DIRS, CONFIG_FILE, DISCOVERY_FILE, CONFIG_DIR, save_json_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.atlas_integrity import generator_fingerprint as atlas_generator_fingerprint
from tools.core.json_io import load_json_file
from tools.core.projects_registry import resolve_projects
from tools.core.logger import logger

CACHE_FILE = RAW_DIR / ".pipeline_cache.json"


def get_project_source_fingerprints(projects=None):
    """Hash source identity so equal/coarse mtimes cannot hide edits or deletions."""
    project_roots = projects if isinstance(projects, dict) else resolve_projects(ROOT)
    results = {}
    for pkey, project_root in project_roots.items():
        digest = hashlib.sha256()
        root_path = os.fspath(project_root)
        entries = []
        for walk_root, dirs, files in os.walk(root_path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for filename in sorted(files):
                if any(filename.endswith(ext) for ext in SOURCE_EXTENSIONS):
                    path = os.path.join(walk_root, filename)
                    rel_path = os.path.relpath(path, root_path).replace("\\", "/")
                    entries.append((rel_path, path))
        for rel_path, path in sorted(entries):
            digest.update(rel_path.encode("utf-8", errors="surrogatepass"))
            digest.update(b"\0")
            try:
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"<source-disappeared-during-fingerprint>")
            digest.update(b"\0")
        results[str(pkey)] = digest.hexdigest()
    return results


def _project_mtimes_from_atlas(atlas=None):
    """Return per-project max mtime from the SQLite-first Atlas artifact."""
    payload = atlas if isinstance(atlas, dict) else load_atlas_data()
    if not isinstance(payload, dict) or not payload:
        return {}
    results = {}
    for pkey, project_payload in payload.items():
        if pkey == "symbols" or not isinstance(project_payload, dict):
            continue
        files = project_payload.get("files", {})
        if not isinstance(files, dict):
            continue
        max_mtime = 0.0
        for file_meta in files.values():
            if not isinstance(file_meta, dict):
                continue
            try:
                mtime = float(file_meta.get("mtime") or 0.0)
            except (TypeError, ValueError):
                mtime = 0.0
            if mtime > max_mtime:
                max_mtime = mtime
        results[str(pkey)] = max_mtime
    return results

def get_project_mtimes():
    """Returns a dictionary mapping project names to their maximum modification time."""
    atlas_mtimes = _project_mtimes_from_atlas()
    if atlas_mtimes:
        logger.info("[FAST] [CACHE] Project mtimes resolved from SQLite-first Atlas metadata.")
        return atlas_mtimes

    projects = resolve_projects(ROOT)
    results = {}
    
    for pkey, p_path in projects.items():
        if not os.path.exists(p_path): continue
        max_mtime = 0.0
        for root, dirs, files in os.walk(p_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for file in files:
                if any(file.endswith(ext) for ext in SOURCE_EXTENSIONS):
                    fpath = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except OSError:
                        logger.warning(f"[FAST] [CACHE] Source disappeared during cache scan: {fpath}")
                        continue
                    if mtime > max_mtime:
                        max_mtime = mtime
        results[pkey] = max_mtime
    
    return results

def get_config_mtime():
    """Returns the maximum modification time for relevant config files."""
    max_mtime = 0.0
    policy_files = (
        CONFIG_FILE,
        DISCOVERY_FILE,
        CONFIG_DIR / "architecture_doctrine.json",
        CONFIG_DIR / "architecture_profiles.json",
        CONFIG_DIR / "codemaps.overrides.json",
        CONFIG_DIR / "react_runtime_policy.json",
        CONFIG_DIR / "react_universal_analysis_doctrine.json",
    )
    for cfg_path in policy_files:
        if cfg_path.exists():
            mtime = os.path.getmtime(cfg_path)
            if mtime > max_mtime:
                max_mtime = mtime
    return max_mtime

def get_stale_projects(force=False, *, invalidation_reason=""):
    """
    Returns a list of project keys that need re-scanning.
    If global config changed, returns ALL projects.
    """
    all_projects = list(resolve_projects(ROOT).keys())
    
    if force:
        reason = str(invalidation_reason or "force").strip().lower()
        if reason == "refresh":
            logger.info(
                "[FAST] [CACHE] '--refresh' requested. All projects stale; "
                "normal execution profile and applicability gates remain active."
            )
        else:
            logger.info("[FAST] [CACHE] '--force' flag provided. All projects stale.")
        return all_projects
        
    if not CACHE_FILE.exists():
        logger.info("[FAST] [CACHE] No cache found. All projects stale.")
        return all_projects
        
    try:
        cache_data = load_json_file(CACHE_FILE, {})
        cached_mtimes = cache_data.get("project_mtimes", {})
        cached_config_mtime = cache_data.get("global_config_mtime", 0)
        cached_atlas_generator = str(cache_data.get("atlas_generator_fingerprint") or "")
        current_atlas_generator = atlas_generator_fingerprint()
        if not cached_atlas_generator or cached_atlas_generator != current_atlas_generator:
            logger.info("[FAST] [CACHE] Atlas producer fingerprint changed or is missing. Invalidating all project caches.")
            return all_projects
        
        current_config_mtime = get_config_mtime()
        if current_config_mtime > cached_config_mtime:
            logger.info("[FAST] [CACHE] Runtime Configuration changed! Invalidating all project caches.")
            return all_projects
            
        current_mtimes = get_project_mtimes()
        stale = []
        for pkey, mtime in current_mtimes.items():
            if mtime > cached_mtimes.get(pkey, 0):
                stale.append(pkey)

        cached_fingerprints = cache_data.get("project_source_fingerprints", {})
        if not isinstance(cached_fingerprints, dict) or set(cached_fingerprints) != set(all_projects):
            logger.info("[FAST] [CACHE] Source fingerprints changed schema or are missing. Invalidating all project caches.")
            return all_projects
        runtime_projects = resolve_projects(ROOT, runtime_config.PROJECT_FILTER)
        unchanged_projects = {
            key: path
            for key, path in runtime_projects.items()
            if key not in stale
        }
        current_fingerprints = get_project_source_fingerprints(unchanged_projects)
        for pkey, fingerprint in current_fingerprints.items():
            if fingerprint != str(cached_fingerprints.get(pkey) or ""):
                stale.append(pkey)
        
        if not stale:
            logger.info("[FAST] [CACHE] All projects are frozen/cached. skipping heavy scans! [LAUNCH]")
        else:
            logger.info(f"[FAST] [CACHE] Stale projects detected: {stale}")
            
        return stale
    except Exception as e:
        logger.warning(f"Failed to read cache: {e}. Defaulting to all projects.")
        return all_projects

def update_cache(changed_files=None):
    """Updates the cache file with per-project mtimes. Call this at the END of pipeline."""
    atlas_mtimes = _project_mtimes_from_atlas()
    if atlas_mtimes:
        p_mtimes = atlas_mtimes
        logger.info("[FAST] [CACHE] Per-Project Cache update used SQLite-first Atlas metadata.")
    else:
        p_mtimes = get_project_mtimes()
    c_mtime = get_config_mtime()
    previous_cache = load_json_file(CACHE_FILE, {}) if CACHE_FILE.exists() else {}
    previous_fingerprints = previous_cache.get("project_source_fingerprints", {})
    all_projects = resolve_projects(ROOT)
    fingerprint_scope = (
        resolve_projects(ROOT, runtime_config.PROJECT_FILTER)
        if isinstance(previous_fingerprints, dict) and set(previous_fingerprints) == set(all_projects)
        else all_projects
    )
    source_fingerprints = dict(previous_fingerprints) if isinstance(previous_fingerprints, dict) else {}
    source_fingerprints.update(get_project_source_fingerprints(fingerprint_scope))
    save_json_atomic(CACHE_FILE, {
        "project_mtimes": p_mtimes,
        "project_source_fingerprints": source_fingerprints,
        "global_config_mtime": c_mtime,
        "atlas_generator_fingerprint": atlas_generator_fingerprint(),
        "timestamp": time.time(),
        "source": "sqlite_first_atlas_metadata" if atlas_mtimes else "filesystem_walk_fallback",
        "changed_files_count": len(changed_files or []),
    })
    logger.info("[FAST] [CACHE] Per-Project Cache updated successfully.")

"""
Smart Cache Manager determines if the source files have changed since the last run.
Allows the pipeline to skip extracting engines and reuse previous JSON artifacts.
"""
import os
import time
import hashlib
import codecs
from pathlib import Path
import tools.core.config as runtime_config
from tools.core.config import ROOT, RAW_DIR, SOURCE_EXTENSIONS, SKIP_DIRS, CONFIG_FILE, DISCOVERY_FILE, CONFIG_DIR, save_json_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.atlas_integrity import generator_fingerprint as atlas_generator_fingerprint
from tools.core.atlas_integrity import configuration_fingerprint, payload_sha256, source_fingerprint
from tools.core.atlas_typescript_inputs import auxiliary_context_identity, auxiliary_input_policy
from tools.core.json_io import load_json_file, load_raw_artifact_path, raw_artifact_content_fingerprint
from tools.core.repository_topology import (
    configured_project_ownership_exclusions, is_project_owned_path, prune_owned_walk_dirs,
)
from tools.core.source_files import is_analysis_source_file
from tools.core.source_snapshot_integrity import snapshot_hash_algorithms
from tools.core.projects_registry import resolve_projects
from tools.core.logger import logger

CACHE_FILE = RAW_DIR / ".pipeline_cache.json"
CACHE_COMPLETION_AUTHORITY = "committed_atlas_inputs_v1"


def _raise_walk_error(error):
    raise error


def _atlas_input_references(project, root, source_paths, auxiliary_paths):
    """Return ingestion identities only for the exact live owned inventory."""
    if not isinstance(project, dict):
        return None
    inventory = project.get("typescript_auxiliary_inputs", {})
    files = project.get("files")
    if (not auxiliary_context_identity(inventory)
            or inventory.get("project_root") != root.as_posix()
            or not isinstance(files, dict) or set(files) != source_paths
            or not isinstance(inventory.get("files"), dict)
            or set(inventory["files"]) != auxiliary_paths):
        return None
    if any(not isinstance(row, dict) or row.get("status") != "ok"
           for row in inventory["files"].values()):
        return None
    return files, inventory["files"]


def get_project_source_fingerprints(projects=None, *, expected_atlas=None):
    """One existing walk/read pass; optional Atlas binding never blesses new inputs."""
    project_roots = projects if isinstance(projects, dict) else resolve_projects(ROOT)
    auxiliary_suffixes = tuple(auxiliary_input_policy()["file_suffixes"])
    ownership = configured_project_ownership_exclusions(
        resolve_projects(ROOT) | project_roots, root=ROOT, dynamic_config=runtime_config.DYNAMIC_CONFIG,
    )
    algorithms = snapshot_hash_algorithms()
    results = {}
    for pkey, project_root in project_roots.items():
        digest = hashlib.sha256()
        root_path = os.fspath(project_root)
        resolved_root = Path(root_path).resolve()
        excluded = ownership.get(pkey, [])
        digest.update(payload_sha256({
            "root": resolved_root.as_posix(),
            "excluded": [path.as_posix() for path in excluded],
        }).encode("ascii"))
        entries = []
        try:
            for walk_root, dirs, files in os.walk(root_path, onerror=_raise_walk_error):
                prune_owned_walk_dirs(walk_root, dirs, skipped_names=SKIP_DIRS, excluded_roots=excluded)
                dirs.sort()
                for filename in sorted(files):
                    path = os.path.join(walk_root, filename)
                    rel_path = os.path.relpath(path, root_path).replace("\\", "/")
                    if ((is_analysis_source_file(rel_path) or rel_path.lower().endswith(auxiliary_suffixes))
                            and is_project_owned_path(resolved_root, rel_path, excluded_roots=excluded)):
                        entries.append((rel_path, path))
            references = None
            if expected_atlas is not None:
                references = _atlas_input_references(
                    expected_atlas.get(pkey), resolved_root,
                    {rel for rel, _ in entries if is_analysis_source_file(rel)},
                    {rel for rel, _ in entries if rel.lower().endswith(auxiliary_suffixes)},
                )
                if references is None:
                    raise ValueError("Atlas source/auxiliary inventory does not cover live owned inputs")
            for rel_path, path in sorted(entries):
                digest.update(rel_path.encode("utf-8", errors="surrogatepass"))
                digest.update(b"\0")
                source_hash = None
                reference = ""
                if references is not None and rel_path in references[0]:
                    row = references[0][rel_path]
                    reference = row.get("hash") if isinstance(row, dict) else None
                    if (not isinstance(reference, str) or not reference
                            or any(c not in "0123456789abcdef" for c in reference)
                            or str(len(reference)) not in algorithms):
                        raise ValueError("Atlas source identity is unavailable")
                    source_hash = hashlib.new(algorithms[str(len(reference))])
                raw_hash = hashlib.sha256()
                decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                        raw_hash.update(chunk)
                        if source_hash is not None:
                            decoder.decode(chunk)
                            source_hash.update(chunk)
                if source_hash is not None:
                    decoder.decode(b"", final=True)
                    if source_hash.hexdigest() != reference:
                        raise ValueError("Live source differs from completed Atlas")
                if references is not None and rel_path in references[1]:
                    if raw_hash.hexdigest() != references[1][rel_path].get("raw_sha256"):
                        raise ValueError("Live auxiliary input differs from completed Atlas")
                digest.update(b"\0")
            results[str(pkey)] = digest.hexdigest()
        except (OSError, ValueError) as exc:
            logger.warning("[CACHE] No complete input identity for %s: %s", pkey, exc)
            results[str(pkey)] = ""
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
        if cache_data.get("completion_authority") != CACHE_COMPLETION_AUTHORITY:
            logger.info("[CACHE] Missing completed Atlas authority. Invalidating legacy cache.")
            return all_projects
        current_commit = load_raw_artifact_path(RAW_DIR / "atlas_commit.json", {})
        if (not isinstance(current_commit, dict) or current_commit.get("state") != "complete"
                or current_commit.get("snapshot_id") != cache_data.get("verified_atlas_snapshot_id")
                or raw_artifact_content_fingerprint(RAW_DIR / "atlas.json")
                != cache_data.get("atlas_state_identity")):
            logger.info("[CACHE] Atlas completion identity changed. Invalidating cache.")
            return all_projects
        cached_mtimes = cache_data.get("project_mtimes", {})
        cached_config_mtime = cache_data.get("global_config_mtime", 0)
        cached_atlas_generator = str(cache_data.get("atlas_generator_fingerprint") or "")
        current_atlas_generator = atlas_generator_fingerprint()
        if not cached_atlas_generator or cached_atlas_generator != current_atlas_generator:
            logger.info("[FAST] [CACHE] Atlas producer fingerprint changed or is missing. Invalidating all project caches.")
            return all_projects
        
        current_config_mtime = get_config_mtime()
        cached_context = cache_data.get("execution_context", {})
        if (current_config_mtime != cached_config_mtime
                or not isinstance(cached_context, dict)
                or cached_context.get("configuration_fingerprint") != configuration_fingerprint()):
            logger.info("[FAST] [CACHE] Runtime Configuration changed! Invalidating all project caches.")
            return all_projects
            
        current_mtimes = get_project_mtimes()
        stale = []
        for pkey, mtime in current_mtimes.items():
            if mtime > cached_mtimes.get(pkey, 0):
                stale.append(pkey)

        cached_fingerprints = cache_data.get("project_source_fingerprints", {})
        if not isinstance(cached_fingerprints, dict):
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
            if not fingerprint or fingerprint != str(cached_fingerprints.get(pkey) or ""):
                stale.append(pkey)
        
        if not stale:
            logger.info("[FAST] [CACHE] All projects are frozen/cached. skipping heavy scans! [LAUNCH]")
        else:
            logger.info(f"[FAST] [CACHE] Stale projects detected: {stale}")
            
        return stale
    except Exception as e:
        logger.warning(f"Failed to read cache: {e}. Defaulting to all projects.")
        return all_projects

def cache_execution_context():
    """Small producer/config fence captured before analysis, not a source rescan."""
    return {
        "atlas_generator_fingerprint": atlas_generator_fingerprint(),
        "configuration_fingerprint": configuration_fingerprint(),
        "global_config_mtime": get_config_mtime(),
    }


def _project_atlas_input_identity(project_key, project):
    return payload_sha256({
        "sources": source_fingerprint({project_key: project}),
        "auxiliary": auxiliary_context_identity(project.get("typescript_auxiliary_inputs", {})),
        "root": project.get("project", {}).get("root"),
    })


def completed_atlas_identity():
    """Capture this generation immediately after Atlas returns, before consumers."""
    commit = load_raw_artifact_path(RAW_DIR / "atlas_commit.json", {})
    if not isinstance(commit, dict) or commit.get("state") != "complete":
        return None
    return {key: commit.get(key) for key in ("snapshot_id", "atlas_sha256")}


def update_cache(changed_files=None, *, atlas_completion=None, execution_context=None, failed_steps=()):
    """Publish only live identities bound to the committed Atlas of this run.

    A cached/no-Atlas run leaves its existing baseline untouched. The existing
    completion walk also verifies Atlas inputs, so edits during execution remain
    stale. A cache receipt is not compiler-semantic or negative-resolution proof.
    """
    if (not isinstance(atlas_completion, dict) or failed_steps
            or not isinstance(execution_context, dict)):
        logger.info("[CACHE] Completion has no successful fresh Atlas authority; baseline unchanged.")
        return False
    if execution_context != cache_execution_context():
        logger.warning("[CACHE] Producer/config changed during execution; baseline unchanged.")
        return False
    commit_path = RAW_DIR / "atlas_commit.json"
    atlas_path = RAW_DIR / "atlas.json"
    commit = load_raw_artifact_path(commit_path, {})
    meta = commit.get("meta", {}) if isinstance(commit, dict) else {}
    if (not isinstance(commit, dict) or commit.get("state") != "complete"
            or not isinstance(meta, dict) or meta.get("kind") != "nexora.atlas_commit"
            or meta.get("version") != "v1"
            or not commit.get("snapshot_id")
            or atlas_completion != {key: commit.get(key) for key in ("snapshot_id", "atlas_sha256")}
            or commit.get("generator_fingerprint") != execution_context["atlas_generator_fingerprint"]
            or commit.get("configuration_fingerprint") != execution_context["configuration_fingerprint"]):
        logger.warning("[CACHE] Completed Atlas commit is missing or has different inputs.")
        return False
    state_identity = raw_artifact_content_fingerprint(atlas_path)
    atlas = load_atlas_data()
    atlas_hash = (state_identity.removeprefix("sqlite:") if state_identity.startswith("sqlite:")
                  else payload_sha256(atlas))
    if not atlas or atlas_hash != commit.get("atlas_sha256"):
        logger.warning("[CACHE] Atlas payload does not match its complete commit.")
        return False
    fingerprint_scope = resolve_projects(ROOT, runtime_config.PROJECT_FILTER)
    verified = get_project_source_fingerprints(fingerprint_scope, expected_atlas=atlas)
    verified = {key: value for key, value in verified.items() if value}
    if not verified:
        logger.warning("[CACHE] No project matched completed Atlas inputs; baseline unchanged.")
        return False
    # A concurrent artifact replacement or configuration change invalidates the
    # whole observation. Ordinary source edits after their read remain detectable
    # against the old, Atlas-bound digest on the next lookup.
    if (state_identity != raw_artifact_content_fingerprint(atlas_path)
            or commit != load_raw_artifact_path(commit_path, {})
            or execution_context != cache_execution_context()):
        logger.warning("[CACHE] Atlas/config changed during completion verification.")
        return False
    previous_cache = load_json_file(CACHE_FILE, {}) if CACHE_FILE.exists() else {}
    if not isinstance(previous_cache, dict):
        previous_cache = {}
    all_projects = resolve_projects(ROOT)
    project_identities = {
        key: _project_atlas_input_identity(key, atlas[key])
        for key in all_projects if isinstance(atlas.get(key), dict)
    }
    previous_fingerprints = previous_cache.get("project_source_fingerprints", {})
    previous_project_identities = previous_cache.get("project_atlas_input_identities", {})
    reusable_previous = (
        previous_cache.get("completion_authority") == CACHE_COMPLETION_AUTHORITY
        and previous_cache.get("execution_context") == execution_context
        and isinstance(previous_fingerprints, dict)
        and isinstance(previous_project_identities, dict)
    )
    source_fingerprints = {
        key: value for key, value in previous_fingerprints.items()
        if key in project_identities and key not in fingerprint_scope
        and previous_project_identities.get(key) == project_identities[key]
    } if reusable_previous else {}
    source_fingerprints.update(verified)
    save_json_atomic(CACHE_FILE, {
        "project_mtimes": _project_mtimes_from_atlas(atlas),
        "project_source_fingerprints": source_fingerprints,
        "project_atlas_input_identities": {key: project_identities[key] for key in source_fingerprints},
        "global_config_mtime": execution_context["global_config_mtime"],
        "atlas_generator_fingerprint": execution_context["atlas_generator_fingerprint"],
        "execution_context": execution_context,
        "completion_authority": CACHE_COMPLETION_AUTHORITY,
        "verified_atlas_snapshot_id": commit["snapshot_id"],
        "atlas_state_identity": state_identity,
        "verified_projects": sorted(verified),
        "timestamp": time.time(),
        "source": "committed_atlas_verified_inputs",
        "changed_files_count": len(changed_files or []),
    })
    logger.info("[CACHE] Completed Atlas input baseline updated for %s project(s).", len(verified))
    return True

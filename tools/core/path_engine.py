from __future__ import annotations

import json
import os

from tools.core.config import DYNAMIC_CONFIG, ENVIRONMENT, PRIMARY_ALIAS, normalize_path
from tools.core.language_registry import index_files, language_extensions
from tools.core.path_identity import strip_current_directory_prefix


_NEAREST_CONFIG_ROOT_CACHE: dict[tuple[str, str], str] = {}
_WORKSPACE_PACKAGE_ROOT_CACHE: dict[str, dict[str, tuple[tuple[str, tuple[str, ...]], ...]]] = {}


def reset_path_resolution_caches() -> None:
    _NEAREST_CONFIG_ROOT_CACHE.clear()
    _WORKSPACE_PACKAGE_ROOT_CACHE.clear()


def to_posix_path(path: str) -> str:
    return normalize_path(path)


def to_os_path(path: str) -> str:
    normalized = to_posix_path(path)
    return normalized.replace("/", os.sep)


def resolve_internal_import(source: str, rel_path: str, alias: str | None = None) -> str:
    source = source or ""
    rel_path = to_posix_path(rel_path)
    alias = alias or PRIMARY_ALIAS or "@/"

    if source.startswith(alias):
        return to_posix_path(source[len(alias):])
    if source.startswith("@/"):
        return to_posix_path(source[2:])
    if source.startswith("."):
        current_dir = os.path.dirname(to_os_path(rel_path))
        resolved = os.path.normpath(os.path.join(current_dir, source))
        return to_posix_path(resolved)
    return ""


def expand_module_candidates(path: str) -> list[str]:
    normalized = to_posix_path(path)
    if not normalized:
        return []
    candidates = [normalized]
    for ext in sorted(language_extensions()):
        candidates.append(f"{normalized}{ext}")
    for index_file in index_files():
        candidates.append(f"{normalized}/{index_file}")
    return candidates


def expand_filesystem_candidates(path: str) -> list[str]:
    if not path:
        return []
    candidates = [path]
    for ext in sorted(language_extensions()):
        candidates.append(f"{path}{ext}")
    
    for index_file in index_files():
        candidates.append(os.path.join(path, index_file))
    return candidates


def _workspace_package_roots(project_root: str) -> dict[str, tuple[tuple[str, tuple[str, ...]], ...]]:
    root = os.path.abspath(project_root)
    cached = _WORKSPACE_PACKAGE_ROOT_CACHE.get(root)
    if cached is not None:
        return cached

    skip_dirs = set(ENVIRONMENT.get("skip_dirs", []) or [])
    package_roots: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in skip_dirs)
        if "package.json" not in filenames:
            continue
        manifest_path = os.path.join(current_root, "package.json")
        try:
            with open(manifest_path, "r", encoding="utf-8-sig") as handle:
                manifest = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        package_name = manifest.get("name") if isinstance(manifest, dict) else None
        if isinstance(package_name, str) and package_name.strip():
            exports = manifest.get("exports")
            if isinstance(exports, dict) and any(str(key).startswith(".") for key in exports):
                exported_subpaths = tuple(sorted(str(key) for key in exports if str(key).startswith(".")))
            elif exports is not None:
                exported_subpaths = (".",)
            else:
                exported_subpaths = (".",)
            package_roots.setdefault(package_name.strip(), []).append(
                (current_root, exported_subpaths)
            )

    resolved = {
        name: tuple(
            sorted(
                (os.path.normpath(path), exported_subpaths)
                for path, exported_subpaths in roots
            )
        )
        for name, roots in package_roots.items()
    }
    _WORKSPACE_PACKAGE_ROOT_CACHE[root] = resolved
    return resolved


def _resolve_workspace_package_import(source: str, project_root: str) -> str:
    package_roots = _workspace_package_roots(project_root)
    matching_names = [
        name
        for name in package_roots
        if source == name or source.startswith(f"{name}/")
    ]
    if not matching_names:
        return ""

    package_name = max(matching_names, key=len)
    entries = package_roots[package_name]
    if len(entries) != 1:
        return ""

    package_root, exported_subpaths = entries[0]
    subpath = source[len(package_name):].lstrip("/\\")
    export_key = f"./{subpath}" if subpath else "."
    if export_key not in exported_subpaths:
        return ""
    candidate_bases = []
    if subpath:
        candidate_bases.extend(
            [
                os.path.join(package_root, "src", to_os_path(subpath)),
                os.path.join(package_root, to_os_path(subpath)),
            ]
        )
    else:
        candidate_bases.extend(
            [
                os.path.join(package_root, "src", "index"),
                os.path.join(package_root, "index"),
            ]
        )

    for candidate_base in candidate_bases:
        for candidate in expand_filesystem_candidates(candidate_base):
            if os.path.isfile(candidate):
                return to_posix_path(os.path.relpath(candidate, project_root))
    return ""


def _scoped_alias_map(current_file_dir: str, project_root: str) -> dict[str, str]:
    scopes = ENVIRONMENT.get("scoped_path_aliases", []) or []
    if not current_file_dir or not project_root or not isinstance(scopes, list):
        return {}
    project_abs = os.path.abspath(project_root)
    current_abs = os.path.abspath(current_file_dir)
    try:
        current_rel = to_posix_path(os.path.relpath(current_abs, project_abs)).strip("/")
    except ValueError:
        return {}

    matches = []
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        scope_root = to_posix_path(str(scope.get("scope_root") or ".")).strip("/") or "."
        if scope_root != "." and current_rel != scope_root and not current_rel.startswith(f"{scope_root}/"):
            continue
        matches.append(scope)
    matches.sort(key=lambda item: len(to_posix_path(str(item.get("scope_root") or ".")).split("/")), reverse=True)
    if not matches:
        return {}

    selected = matches[0]
    scope_root = to_os_path(str(selected.get("scope_root") or "."))
    base_url = to_os_path(str(selected.get("base_url") or "."))
    alias_map: dict[str, str] = {}
    for alias_key, alias_targets in (selected.get("path_aliases") or {}).items():
        if not isinstance(alias_targets, list) or not alias_targets:
            continue
        clean_alias = str(alias_key)[:-1] if str(alias_key).endswith("*") else str(alias_key)
        clean_target = strip_current_directory_prefix(str(alias_targets[0]).replace("/*", "")).strip("/") or "."
        target_abs = os.path.abspath(os.path.join(project_abs, scope_root, base_url, to_os_path(clean_target)))
        try:
            if os.path.commonpath((project_abs, target_abs)) != project_abs:
                continue
        except ValueError:
            continue
        alias_map[clean_alias] = to_posix_path(os.path.relpath(target_abs, project_abs))
    return alias_map


def get_alias_map(current_file_dir: str = "", project_root: str = "") -> dict[str, str]:
    scoped = _scoped_alias_map(current_file_dir, project_root)
    if scoped:
        return scoped
    path_aliases = ENVIRONMENT.get("path_aliases", {}) or {}
    if not path_aliases:
        target_override = DYNAMIC_CONFIG.get("_target_root_override", {})
        if isinstance(target_override, dict) and target_override.get("enabled"):
            return {}
        return {"@/": "src"}

    alias_map = {}
    for alias_key, alias_targets in path_aliases.items():
        if not alias_targets:
            continue
        clean_alias = alias_key[:-1] if alias_key.endswith("*") else alias_key
        clean_target = strip_current_directory_prefix(alias_targets[0].replace("/*", "")).strip("/")
        alias_map[clean_alias] = clean_target or "."
    return alias_map or {"@/": "src"}


def resolve_project_import(source: str, current_file_dir: str, workspace_root: str, project_root: str, alias_map: dict[str, str] | None = None, language: str = "typescript") -> str:
    source = source or ""
    
    # [Polyglot] Python Module Resolution (e.g. "my_package.utils")
    if language == "python" and not source.startswith(".") and not os.path.isabs(source):
        # Convert dots to paths and check project root
        dot_path = source.replace(".", os.sep)
        candidate_abs = os.path.join(project_root, dot_path)
        for candidate in expand_filesystem_candidates(candidate_abs):
            if os.path.isfile(candidate):
                return to_posix_path(os.path.relpath(candidate, project_root))
    if language in {"java", "csharp", "go"} and not source.startswith(".") and not os.path.isabs(source):
        package_path = source.replace(".", os.sep).replace("/", os.sep)
        candidate_abs = os.path.join(project_root, package_path)
        for candidate in expand_filesystem_candidates(candidate_abs):
            if os.path.isfile(candidate):
                return to_posix_path(os.path.relpath(candidate, project_root))

    if language in {"typescript", "javascript"} and not source.startswith("."):
        workspace_package_target = _resolve_workspace_package_import(source, project_root)
        if workspace_package_target:
            return workspace_package_target

    alias_map = alias_map or get_alias_map(current_file_dir, project_root)

    sorted_aliases = sorted(alias_map.items(), key=lambda item: len(item[0]), reverse=True)

    def _source_matches_alias(value: str, alias_value: str) -> bool:
        if not alias_value:
            return False
        if value == alias_value:
            return True
        if value.startswith(alias_value):
            # Boundary-aware matching avoids accidental partial captures.
            next_idx = len(alias_value)
            if alias_value.endswith("/"):
                return True
            if next_idx < len(value) and value[next_idx] in {"/", "\\"}:
                return True
        return False

    def _candidate_bases_for_alias(rel_target: str) -> list[str]:
        rel_target_norm = to_os_path(rel_target)
        return [
            os.path.join(project_root, rel_target_norm),
            os.path.join(workspace_root, rel_target_norm),
        ]

    def _nearest_config_root(start_dir: str, stop_root: str) -> str | None:
        cache_key = (os.path.normpath(start_dir), os.path.normpath(stop_root))
        cached = _NEAREST_CONFIG_ROOT_CACHE.get(cache_key)
        if cached:
            return cached

        current = os.path.normpath(start_dir)
        stop = os.path.normpath(stop_root)
        config_markers = ("tsconfig.json", "jsconfig.json", "tsconfig.app.json")

        while True:
            if any(os.path.exists(os.path.join(current, marker)) for marker in config_markers):
                _NEAREST_CONFIG_ROOT_CACHE[cache_key] = current
                return current
            if current == stop:
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        _NEAREST_CONFIG_ROOT_CACHE[cache_key] = ""
        return None

    def _heuristic_alias_candidate_bases(alias_value: str, rel_target: str) -> list[str]:
        """
        Universal fallback for alias-based imports when static alias map is stale
        against a specific project's tsconfig/jsconfig.
        """
        candidates = []
        # Common monorepo/app conventions where "@/" may point to project root.
        if alias_value.startswith("@"):
            candidates.extend(
                [
                    project_root,
                    os.path.join(project_root, "src"),
                    os.path.join(project_root, "app"),
                ]
            )
            nearest_cfg_root = _nearest_config_root(current_file_dir, project_root)
            if nearest_cfg_root:
                candidates.extend(
                    [
                        nearest_cfg_root,
                        os.path.join(nearest_cfg_root, "src"),
                        os.path.join(nearest_cfg_root, "app"),
                    ]
                )
        # If alias target is not root, still probe project root as fallback.
        if rel_target not in {"", ".", "./"}:
            candidates.append(project_root)

        deduped = []
        seen = set()
        for candidate in candidates:
            norm = os.path.normpath(candidate)
            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(norm)
        return deduped

    matched_alias_entries: list[tuple[str, str, str]] = []
    for alias, rel_target in sorted_aliases:
        if not _source_matches_alias(source, alias):
            continue

        source_tail = source[len(alias):].lstrip("/\\")
        matched_alias_entries.append((alias, rel_target, source_tail))
        candidate_roots = _candidate_bases_for_alias(rel_target)
        for base_root in candidate_roots:
            resolved_base = os.path.join(base_root, source_tail.replace("/", os.sep))
            for candidate in expand_filesystem_candidates(resolved_base):
                if os.path.isfile(candidate):
                    return to_posix_path(os.path.relpath(candidate, project_root))

    # Alias matched but static map couldn't resolve path; apply conservative
    # project-root fallbacks to reduce false negatives.
    for alias, rel_target, source_tail in matched_alias_entries:
        for base_root in _heuristic_alias_candidate_bases(alias, rel_target):
            resolved_base = os.path.join(base_root, source_tail.replace("/", os.sep))
            for candidate in expand_filesystem_candidates(resolved_base):
                if os.path.isfile(candidate):
                    return to_posix_path(os.path.relpath(candidate, project_root))

    if source.startswith("."):
        resolved_base = os.path.abspath(os.path.join(current_file_dir, source.replace("/", os.sep)))
        for candidate in expand_filesystem_candidates(resolved_base):
            if os.path.isfile(candidate):
                return to_posix_path(os.path.relpath(candidate, project_root))
    return source

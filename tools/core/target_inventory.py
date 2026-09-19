from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from tools.core.json_syntax import loads_json_strict
from tools.core.inventory_classification import (
    finalize_inventory_classification as _finalize_inventory_classification,
    inventory_classification_policy as _inventory_classification_policy,
    is_analysis_source_file as _is_analysis_source_file,
    matches_marker as _matches_marker,
    new_inventory_classification_state as _inventory_classification_state,
    record_inventory_classification as _record_inventory_classification,
)
from tools.core.target_repository_trust import is_target_path_contained


TARGET_OBSERVATION_IDENTITY_ALGORITHM = "path-kind-size-mtime-config-content-sha256-v1"
CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
LANGUAGE_REGISTRY_FILE = CODE_MAPS_DIR / "config" / "language_registry.json"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _parse_language_registry(content: bytes) -> dict[str, Any]:
    try:
        payload = loads_json_strict(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"Language registry is invalid: {LANGUAGE_REGISTRY_FILE}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("languages"), dict):
        raise ValueError(f"Language registry root is invalid: {LANGUAGE_REGISTRY_FILE}")
    return payload


def _language_inventory_policy() -> dict[str, Any]:
    """Load target-inventory policy from its source SSoT without runtime config imports."""

    try:
        content = LANGUAGE_REGISTRY_FILE.read_bytes()
    except OSError as exc:
        raise ValueError(f"Language registry is unavailable: {LANGUAGE_REGISTRY_FILE}") from exc
    return _parse_language_registry(content)


def _observable_extension_language_map(registry: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for collection in ("languages", "observation_only_languages"):
        for language, payload in (registry.get(collection) or {}).items():
            if not isinstance(payload, dict):
                continue
            for extension in payload.get("extensions") or []:
                mapping.setdefault(str(extension).lower(), str(language))
    return mapping


def _analysis_source_policy(
    registry: dict[str, Any],
) -> tuple[set[str], set[str], set[str]]:
    source_extensions = {
        str(extension).lower()
        for payload in (registry.get("languages") or {}).values()
        if isinstance(payload, dict)
        for extension in payload.get("extensions") or []
        if str(extension).startswith(".")
    }
    compound_suffixes = {
        str(extension).lower()
        for extension in registry.get("non_source_compound_suffixes", [])
        if str(extension).startswith(".")
    }
    template_extensions = {
        str(extension).lower()
        for extension in registry.get("non_source_template_extensions", [])
        if str(extension).startswith(".")
    }
    return source_extensions, compound_suffixes, template_extensions


def _has_skipped_ancestor(
    relative: Path,
    skip: set[str],
    root_only_skip: set[str],
) -> bool:
    for depth, part in enumerate(relative.parts[:-1]):
        lowered = part.lower()
        if lowered not in skip:
            continue
        if lowered not in root_only_skip or depth == 0:
            return True
    return False


def source_inventory(
    root: Path,
    policy: dict[str, Any],
    config_patterns: list[str],
    manifest_patterns: dict[str, list[str]],
    package_react_ownership: dict[Path, bool],
    *,
    identity_root: Path | None = None,
    excluded_roots: set[Path] | None = None,
    file_count_limit: int | None = None,
    file_observer: Callable[[Path], bool | None] | None = None,
    continue_for_observer_after_limit: bool = False,
    traversal_state: dict[str, Any] | None = None,
    observation_state: dict[str, Any] | None = None,
    classification_state: dict[str, Any] | None = None,
    path_boundary_state: dict[str, Any] | None = None,
) -> tuple[
    int,
    bool,
    dict[str, int],
    dict[str, int],
    int,
    int,
    list[str],
    dict[str, list[str]],
]:
    count = 0
    truncated = False
    limit = max(
        1,
        int(
            file_count_limit
            if file_count_limit is not None
            else policy.get("file_count_limit", 10000) or 10000
        ),
    )
    identity_root = Path(identity_root or root).resolve()
    excluded_roots = {
        Path(path).resolve()
        for path in (excluded_roots or set())
    }
    language_policy = _language_inventory_policy()
    skip = {
        str(item).lower()
        for item in language_policy.get("skip_dirs", [])
        if str(item).strip()
    }
    root_only_skip = {
        str(value).lower()
        for value in policy.get("root_only_skip_dirs", [])
    }
    language_by_extension = _observable_extension_language_map(language_policy)
    source_extensions, compound_suffixes, template_extensions = _analysis_source_policy(
        language_policy
    )
    react_policy = policy.get("framework_source_evidence", {}).get("react", {})
    react_extensions = {
        str(value).lower()
        for value in react_policy.get("source_extensions", [])
    }
    excluded_react_segments = {
        str(value).lower()
        for value in react_policy.get("excluded_path_segments", [])
    }
    package_roots = sorted(
        package_react_ownership,
        key=lambda item: len(item.parts),
        reverse=True,
    )
    language_counts: dict[str, int] = {}
    analysis_language_counts: dict[str, int] = {}
    react_source_files = 0
    react_fixture_source_files = 0
    config_files: set[str] = set()
    manifest_files: dict[str, set[str]] = {
        ecosystem: set()
        for ecosystem in manifest_patterns
    }
    state = traversal_state if isinstance(traversal_state, dict) else {}
    state.update({"completed": False, "error": None, "stopped_at_limit": False})
    observation = observation_state if isinstance(observation_state, dict) else None
    observation_hasher = hashlib.sha256() if observation is not None else None
    observation_digests: list[bytes] = []
    observation_entries = 0
    observation_decision_files = 0
    observation_contract_sha256 = hashlib.sha256(
        json.dumps(
            {
                "algorithm": TARGET_OBSERVATION_IDENTITY_ALGORITHM,
                "skip_dirs": sorted(skip),
                "root_only_skip_dirs": sorted(root_only_skip),
                "config_patterns": sorted(str(value).lower() for value in config_patterns),
                "manifest_patterns": {
                    str(ecosystem): sorted(str(value).lower() for value in patterns)
                    for ecosystem, patterns in sorted(manifest_patterns.items())
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if observation is not None:
        observation.update(
            {
                "algorithm": TARGET_OBSERVATION_IDENTITY_ALGORITHM,
                "status": "incomplete",
                "fingerprint": None,
                "entry_count": 0,
                "decision_file_count": 0,
                "observation_contract_sha256": observation_contract_sha256,
                "error": None,
            }
        )
    try:
        for path in root.rglob("*"):
            if not is_target_path_contained(root, path, state=path_boundary_state):
                continue
            resolved_path = path.resolve()
            if any(
                resolved_path == excluded_root
                or excluded_root in resolved_path.parents
                for excluded_root in excluded_roots
            ):
                continue
            relative = path.relative_to(root)
            is_dir = path.is_dir()
            path_name = path.name.lower()
            path_is_skipped_dir = (
                is_dir
                and path_name in skip
                and (path_name not in root_only_skip or len(relative.parts) == 1)
            )
            if path_is_skipped_dir or _has_skipped_ancestor(relative, skip, root_only_skip):
                continue
            is_file = path.is_file()
            if observation_hasher is not None:
                relative_identity = path.relative_to(identity_root).as_posix()
                if is_file:
                    stat = path.stat()
                    decision_file = _matches_marker(path.name, config_patterns) or any(
                        _matches_marker(path.name, patterns)
                        for patterns in manifest_patterns.values()
                    )
                    content_identity = ""
                    if decision_file:
                        content_identity = hashlib.sha256(path.read_bytes()).hexdigest()
                        observation_decision_files += 1
                    identity_row = (
                        f"file\0{relative_identity}\0{stat.st_size}\0{stat.st_mtime_ns}"
                        f"\0{content_identity}\n"
                    )
                elif is_dir:
                    identity_row = f"directory\0{relative_identity}\n"
                else:
                    identity_row = f"other\0{relative_identity}\n"
                observation_digests.append(
                    hashlib.sha256(identity_row.encode("utf-8")).digest()
                )
                observation_entries += 1
            if not is_file:
                continue
            if count >= limit:
                truncated = True
                observer_needs_more = file_observer(path) if file_observer is not None else False
                if observation_hasher is not None or (
                    continue_for_observer_after_limit and observer_needs_more is not False
                ):
                    continue
                state["stopped_at_limit"] = True
                break
            count += 1
            observer_needs_more = None
            if file_observer is not None:
                observer_needs_more = file_observer(path)
            relative_path = path.relative_to(identity_root).as_posix()
            if isinstance(classification_state, dict):
                _record_inventory_classification(
                    classification_state,
                    path,
                    relative_path,
                    policy=policy,
                    config_patterns=config_patterns,
                    manifest_patterns=manifest_patterns,
                    language_by_extension=language_by_extension,
                    skip=skip,
                    source_extensions=source_extensions,
                    compound_suffixes=compound_suffixes,
                    template_extensions=template_extensions,
                )
            if _matches_marker(path.name, config_patterns):
                config_files.add(relative_path)
            for ecosystem, patterns in manifest_patterns.items():
                if _matches_marker(path.name, patterns):
                    manifest_files[ecosystem].add(relative_path)
            language = language_by_extension.get(path.suffix.lower())
            if language:
                language_counts[language] = language_counts.get(language, 0) + 1
                if _is_analysis_source_file(
                    path,
                    skip=skip,
                    source_extensions=source_extensions,
                    compound_suffixes=compound_suffixes,
                    template_extensions=template_extensions,
                ):
                    analysis_language_counts[language] = analysis_language_counts.get(language, 0) + 1
            owning_package_root = next(
                (
                    package_root
                    for package_root in package_roots
                    if path == package_root or package_root in path.parents
                ),
                None,
            )
            if (
                path.suffix.lower() in react_extensions
                and owning_package_root is not None
                and package_react_ownership[owning_package_root]
            ):
                relative_parts = {
                    part.lower()
                    for part in path.relative_to(root).parts
                }
                if relative_parts & excluded_react_segments:
                    react_fixture_source_files += 1
                else:
                    react_source_files += 1
            if count >= limit:
                truncated = True
                if observation_hasher is None and (
                    not continue_for_observer_after_limit or observer_needs_more is False
                ):
                    state["stopped_at_limit"] = True
                    break
        else:
            state["completed"] = True
    except Exception as exc:
        truncated = True
        state["error"] = f"{type(exc).__name__}:{exc}"
        logger.warning(
            "Target source inventory traversal is incomplete for %s: %s",
            root,
            state["error"],
        )
    finally:
        if observation is not None and observation_hasher is not None:
            complete = bool(state.get("completed")) and not state.get("error")
            observation_hasher.update(observation_contract_sha256.encode("ascii"))
            for digest in sorted(observation_digests):
                observation_hasher.update(digest)
            observation.update(
                {
                    "status": "complete" if complete else "incomplete",
                    "fingerprint": observation_hasher.hexdigest(),
                    "entry_count": observation_entries,
                    "decision_file_count": observation_decision_files,
                    "error": state.get("error"),
                }
            )
    return (
        count,
        truncated,
        dict(sorted(language_counts.items())),
        dict(sorted(analysis_language_counts.items())),
        react_source_files,
        react_fixture_source_files,
        sorted(config_files),
        {
            ecosystem: sorted(files)
            for ecosystem, files in manifest_files.items()
            if files
        },
    )


def repository_and_selected_project_inventory(
    target: Path,
    repository_topology: dict[str, Any],
    policy: dict[str, Any],
    config_patterns: list[str],
    manifest_patterns: dict[str, list[str]],
    package_react_ownership: dict[Path, bool],
    *,
    excluded_roots: set[Path] | None = None,
    projects: dict[str, Any] | None = None,
    observation_state: dict[str, Any] | None = None,
    classification_projection: dict[str, Any] | None = None,
    path_boundary_state: dict[str, Any] | None = None,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Produce repository and nearest-owner project summaries from one filesystem walk."""

    total_limit = max(1, int(policy.get("file_count_limit", 10000) or 10000))
    inventory_projects = (
        projects
        if isinstance(projects, dict)
        else repository_topology["selected_projects"]
    )
    selected_roots = {
        str(project): (
            target.resolve()
            if str(relative_path) == "."
            else (target / str(relative_path)).resolve()
        )
        for project, relative_path in inventory_projects.items()
    }
    selected_specs = sorted(
        (
            (
                project,
                str(inventory_projects[project]),
                root,
                {
                    (target / relative).resolve()
                    for relative in repository_topology["project_ownership_exclusions"].get(project, [])
                }
                | {Path(path).resolve() for path in (excluded_roots or set())},
            )
            for project, root in selected_roots.items()
        ),
        key=lambda row: len(row[2].parts),
        reverse=True,
    )
    language_policy = _language_inventory_policy()
    skip = {
        str(item).lower()
        for item in language_policy.get("skip_dirs", [])
        if str(item).strip()
    }
    language_by_extension = _observable_extension_language_map(language_policy)
    source_extensions, compound_suffixes, template_extensions = _analysis_source_policy(
        language_policy
    )
    react_policy = policy.get("framework_source_evidence", {}).get("react", {})
    react_extensions = {str(value).lower() for value in react_policy.get("source_extensions", [])}
    excluded_react_segments = {
        str(value).lower()
        for value in react_policy.get("excluded_path_segments", [])
    }
    package_roots = sorted(package_react_ownership, key=lambda item: len(item.parts), reverse=True)
    project_states: dict[str, dict[str, Any]] = {
        project: {
            "relative_path": relative_path,
            "file_count": 0,
            "language_counts": {},
            "analysis_language_counts": {},
            "extension_counts": {},
            "config_files": set(),
            "manifest_files": {ecosystem: set() for ecosystem in manifest_patterns},
            "react_source_files": 0,
            "react_fixture_source_files": 0,
        }
        for project, relative_path, _root, _exclusions in selected_specs
    }
    selected_total = 0
    selected_truncated = False
    repository_classification_state = _inventory_classification_state()
    selected_classification_state = _inventory_classification_state()

    def observe_selected(path: Path) -> bool:
        nonlocal selected_total, selected_truncated
        owner = None
        resolved = path.resolve()
        for project, _relative_path, project_root, project_exclusions in selected_specs:
            if not (resolved == project_root or project_root in resolved.parents):
                continue
            if any(resolved == excluded or excluded in resolved.parents for excluded in project_exclusions):
                continue
            owner = project
            break
        if owner is None:
            return True
        if selected_total >= total_limit:
            selected_truncated = True
            return False

        selected_total += 1
        state = project_states[owner]
        state["file_count"] += 1
        extension = path.suffix.lower() or "<none>"
        state["extension_counts"][extension] = state["extension_counts"].get(extension, 0) + 1
        relative_path = path.relative_to(target).as_posix()
        _record_inventory_classification(
            selected_classification_state,
            path,
            relative_path,
            policy=policy,
            config_patterns=config_patterns,
            manifest_patterns=manifest_patterns,
            language_by_extension=language_by_extension,
            skip=skip,
            source_extensions=source_extensions,
            compound_suffixes=compound_suffixes,
            template_extensions=template_extensions,
        )
        if _matches_marker(path.name, config_patterns):
            state["config_files"].add(relative_path)
        for ecosystem, patterns in manifest_patterns.items():
            if _matches_marker(path.name, patterns):
                state["manifest_files"][ecosystem].add(relative_path)
        language = language_by_extension.get(path.suffix.lower())
        if language:
            state["language_counts"][language] = state["language_counts"].get(language, 0) + 1
            if _is_analysis_source_file(
                path,
                skip=skip,
                source_extensions=source_extensions,
                compound_suffixes=compound_suffixes,
                template_extensions=template_extensions,
            ):
                state["analysis_language_counts"][language] = (
                    state["analysis_language_counts"].get(language, 0) + 1
                )
        owning_package_root = next(
            (
                package_root
                for package_root in package_roots
                if path == package_root or package_root in path.parents
            ),
            None,
        )
        if (
            path.suffix.lower() in react_extensions
            and owning_package_root is not None
            and package_react_ownership[owning_package_root]
        ):
            relative_parts = {part.lower() for part in path.relative_to(target).parts}
            key = (
                "react_fixture_source_files"
                if relative_parts & excluded_react_segments
                else "react_source_files"
            )
            state[key] += 1
        if selected_total >= total_limit:
            selected_truncated = True
            return False
        return True

    traversal_state: dict[str, Any] = {}
    repository_result = source_inventory(
        target,
        policy,
        config_patterns,
        manifest_patterns,
        package_react_ownership,
        excluded_roots=excluded_roots,
        file_observer=observe_selected,
        continue_for_observer_after_limit=True,
        traversal_state=traversal_state,
        observation_state=observation_state,
        classification_state=repository_classification_state,
        path_boundary_state=path_boundary_state,
    )
    if traversal_state.get("error"):
        selected_truncated = True

    language_counts: dict[str, int] = {}
    analysis_language_counts: dict[str, int] = {}
    config_files: set[str] = set()
    manifest_files: dict[str, set[str]] = {}
    project_file_counts: dict[str, int] = {}
    project_inventory_evidence: dict[str, dict[str, Any]] = {}
    react_source_files = 0
    react_fixture_source_files = 0
    for project in sorted(project_states):
        state = project_states[project]
        project_file_counts[project] = int(state["file_count"])
        project_inventory_evidence[project] = {
            "relative_path": str(state["relative_path"] or ".").replace("\\", "/"),
            "file_count": int(state["file_count"]),
            "inventory_truncated": bool(selected_truncated),
            "observed_source_file_count": sum(state["language_counts"].values()),
            "analysis_source_file_count": sum(state["analysis_language_counts"].values()),
            "analysis_config_file_count": sum(
                1
                for path in state["config_files"]
                if _is_analysis_source_file(
                    Path(path),
                    skip=skip,
                    source_extensions=source_extensions,
                    compound_suffixes=compound_suffixes,
                    template_extensions=template_extensions,
                )
            ),
            "language_counts": dict(sorted(state["language_counts"].items())),
            "analysis_language_counts": dict(sorted(state["analysis_language_counts"].items())),
            "extension_counts": dict(sorted(state["extension_counts"].items())),
            "config_files": sorted(state["config_files"]),
            "manifest_files": {
                ecosystem: sorted(files)
                for ecosystem, files in state["manifest_files"].items()
                if files
            },
        }
        react_source_files += int(state["react_source_files"])
        react_fixture_source_files += int(state["react_fixture_source_files"])
        config_files.update(state["config_files"])
        for language, count in state["language_counts"].items():
            language_counts[language] = language_counts.get(language, 0) + count
        for language, count in state["analysis_language_counts"].items():
            analysis_language_counts[language] = analysis_language_counts.get(language, 0) + count
        for ecosystem, files in state["manifest_files"].items():
            manifest_files.setdefault(ecosystem, set()).update(files)

    selected_result = (
        selected_total,
        selected_truncated,
        dict(sorted(language_counts.items())),
        dict(sorted(analysis_language_counts.items())),
        react_source_files,
        react_fixture_source_files,
        sorted(config_files),
        {
            ecosystem: sorted(files)
            for ecosystem, files in manifest_files.items()
            if files
        },
        project_file_counts,
        project_inventory_evidence,
    )
    if isinstance(classification_projection, dict):
        classification_policy = _inventory_classification_policy(policy)
        classification_projection.update(
            {
                "contract": classification_policy.get("contract"),
                "precedence": list(classification_policy.get("precedence") or []),
                "decision_effect": classification_policy.get(
                    "decision_effect",
                    "observability_only",
                ),
                "repository": _finalize_inventory_classification(
                    repository_classification_state,
                    policy=policy,
                    truncated=bool(repository_result[1]),
                ),
                "effective_scope": _finalize_inventory_classification(
                    selected_classification_state,
                    policy=policy,
                    truncated=bool(selected_truncated),
                ),
            }
        )
    return repository_result, selected_result


def target_observation_identity(
    target: Path,
    policy: dict[str, Any],
    config_patterns: list[str],
    manifest_patterns: dict[str, list[str]],
    *,
    excluded_roots: set[Path] | None = None,
) -> dict[str, Any]:
    """Recompute the lightweight identity that authorizes cross-process Preflight reuse."""

    state: dict[str, Any] = {}
    if not target.exists() or not target.is_dir():
        return {
            "algorithm": TARGET_OBSERVATION_IDENTITY_ALGORITHM,
            "status": "incomplete",
            "fingerprint": None,
            "entry_count": 0,
            "decision_file_count": 0,
            "observation_contract_sha256": None,
            "error": "invalid_target",
        }
    source_inventory(
        target,
        policy,
        config_patterns,
        manifest_patterns,
        {},
        excluded_roots=excluded_roots,
        file_count_limit=1,
        observation_state=state,
    )
    return state


def selected_project_inventory(
    target: Path,
    repository_topology: dict[str, Any],
    policy: dict[str, Any],
    config_patterns: list[str],
    manifest_patterns: dict[str, list[str]],
    package_react_ownership: dict[Path, bool],
    *,
    excluded_roots: set[Path] | None = None,
    projects: dict[str, Any] | None = None,
) -> tuple[
    int,
    bool,
    dict[str, int],
    dict[str, int],
    int,
    int,
    list[str],
    dict[str, list[str]],
    dict[str, int],
    dict[str, dict[str, Any]],
]:
    language_policy = _language_inventory_policy()
    skip = {
        str(item).lower()
        for item in language_policy.get("skip_dirs", [])
        if str(item).strip()
    }
    source_extensions, compound_suffixes, template_extensions = _analysis_source_policy(
        language_policy
    )
    total_limit = max(1, int(policy.get("file_count_limit", 10000) or 10000))
    total_count = 0
    truncated = False
    language_counts: dict[str, int] = {}
    analysis_language_counts: dict[str, int] = {}
    react_source_files = 0
    react_fixture_source_files = 0
    config_files: set[str] = set()
    manifest_files: dict[str, set[str]] = {}
    project_file_counts: dict[str, int] = {}
    project_inventory_evidence: dict[str, dict[str, Any]] = {}

    inventory_projects = (
        projects
        if isinstance(projects, dict)
        else repository_topology["selected_projects"]
    )
    for project_key, relative_path in inventory_projects.items():
        remaining = total_limit - total_count
        if remaining <= 0:
            truncated = True
            break
        project_root = (
            target
            if str(relative_path) == "."
            else (target / str(relative_path)).resolve()
        )
        project_excluded_roots = {
            (target / relative).resolve()
            for relative in repository_topology["project_ownership_exclusions"].get(
                project_key,
                [],
            )
        }
        project_excluded_roots.update(
            Path(path).resolve()
            for path in (excluded_roots or set())
        )
        extension_counts: dict[str, int] = {}

        def observe_project_file(path: Path) -> None:
            extension = path.suffix.lower() or "<none>"
            extension_counts[extension] = extension_counts.get(extension, 0) + 1

        (
            project_count,
            project_truncated,
            project_languages,
            project_analysis_languages,
            project_react_sources,
            project_react_fixtures,
            project_configs,
            project_manifests,
        ) = source_inventory(
            project_root,
            policy,
            config_patterns,
            manifest_patterns,
            package_react_ownership,
            identity_root=target,
            excluded_roots=project_excluded_roots,
            file_count_limit=remaining,
            file_observer=observe_project_file,
        )
        project_file_counts[str(project_key)] = project_count
        project_inventory_evidence[str(project_key)] = {
            "relative_path": str(relative_path or ".").replace("\\", "/"),
            "file_count": project_count,
            "inventory_truncated": project_truncated,
            "observed_source_file_count": sum(project_languages.values()),
            "analysis_source_file_count": sum(project_analysis_languages.values()),
            "analysis_config_file_count": sum(
                1
                for path in project_configs
                if _is_analysis_source_file(
                    Path(path),
                    skip=skip,
                    source_extensions=source_extensions,
                    compound_suffixes=compound_suffixes,
                    template_extensions=template_extensions,
                )
            ),
            "language_counts": dict(sorted(project_languages.items())),
            "analysis_language_counts": dict(sorted(project_analysis_languages.items())),
            "extension_counts": dict(sorted(extension_counts.items())),
            "config_files": sorted(project_configs),
            "manifest_files": {
                ecosystem: sorted(files)
                for ecosystem, files in project_manifests.items()
                if files
            },
        }
        total_count += project_count
        truncated = truncated or project_truncated
        react_source_files += project_react_sources
        react_fixture_source_files += project_react_fixtures
        config_files.update(project_configs)
        for language, count in project_languages.items():
            language_counts[language] = language_counts.get(language, 0) + count
        for language, count in project_analysis_languages.items():
            analysis_language_counts[language] = analysis_language_counts.get(language, 0) + count
        for ecosystem, files in project_manifests.items():
            manifest_files.setdefault(ecosystem, set()).update(files)
        if project_truncated:
            break

    return (
        total_count,
        truncated,
        dict(sorted(language_counts.items())),
        dict(sorted(analysis_language_counts.items())),
        react_source_files,
        react_fixture_source_files,
        sorted(config_files),
        {
            ecosystem: sorted(files)
            for ecosystem, files in manifest_files.items()
            if files
        },
        project_file_counts,
        project_inventory_evidence,
    )

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from tools.core.language_registry import observable_extension_language_map, skip_dirs
from tools.core.source_files import is_analysis_source_file


def _matches_marker(filename: str, patterns: list[str]) -> bool:
    lowered = filename.lower()
    return any(fnmatchcase(lowered, str(pattern).lower()) for pattern in patterns)


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
    skip = set(skip_dirs())
    root_only_skip = {
        str(value).lower()
        for value in policy.get("root_only_skip_dirs", [])
    }
    language_by_extension = observable_extension_language_map()
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
    try:
        for path in root.rglob("*"):
            resolved_path = path.resolve()
            if any(
                resolved_path == excluded_root
                or excluded_root in resolved_path.parents
                for excluded_root in excluded_roots
            ):
                continue
            relative = path.relative_to(root)
            if _has_skipped_ancestor(relative, skip, root_only_skip):
                continue
            if not path.is_file():
                continue
            count += 1
            relative_path = path.relative_to(identity_root).as_posix()
            if _matches_marker(path.name, config_patterns):
                config_files.add(relative_path)
            for ecosystem, patterns in manifest_patterns.items():
                if _matches_marker(path.name, patterns):
                    manifest_files[ecosystem].add(relative_path)
            language = language_by_extension.get(path.suffix.lower())
            if language:
                language_counts[language] = language_counts.get(language, 0) + 1
                if is_analysis_source_file(path):
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
                break
    except Exception:
        truncated = True
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
]:
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
        )
        project_file_counts[str(project_key)] = project_count
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
    )

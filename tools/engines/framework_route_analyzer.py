from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.framework_capabilities import framework_config_files, framework_package_index
from tools.core.engine_progress import EngineProgress
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.atlas_io import load_atlas_data
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.source_evidence import atlas_file_paths, read_atlas_bound_source
from tools.core.path_identity import strip_current_directory_prefix


ROUTE_PATH_RE = re.compile(r"\bpath\s*=\s*['\"]([^'\"]+)['\"]|\bpath\s*:\s*['\"]([^'\"]+)['\"]")
TANSTACK_FILE_ROUTE_RE = re.compile(r"\bcreateFileRoute\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
TANSTACK_CODE_ROUTE_RE = re.compile(r"\bcreateRoute\s*\(\s*\{[\s\S]*?\bpath\s*:\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
REACT_ROUTER_FRAMEWORK_RE = re.compile(r"\b(?:Links|Meta|Scripts|ScrollRestoration|useFetcher|useLoaderData)\b")
ROUTE_FILE_EXTENSIONS = {".tsx", ".jsx", ".ts", ".js"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage", "__pycache__"}


def _normalize_rel(path: str) -> str:
    return strip_current_directory_prefix(str(path or "").replace("\\", "/"))


def _next_app_route_segment(part: str) -> str:
    if not part:
        return ""
    if part.startswith("(") and part.endswith(")"):
        return ""
    if part.startswith("@"):
        return ""
    while part.startswith("("):
        close = part.find(")")
        if close <= 0:
            break
        marker = part[1:close]
        if marker and set(marker) <= {"."}:
            part = part[close + 1:]
            continue
        break
    return part


def _route_from_next_app_file(rel_path: str) -> tuple[str, str] | None:
    rel = _normalize_rel(rel_path)
    parts = rel.split("/")
    if "app" not in parts or not parts[-1].startswith("page."):
        return None
    app_index = parts.index("app")
    route_parts = parts[app_index + 1:-1]
    route_segments = []
    for part in route_parts:
        part = _next_app_route_segment(part)
        if not part:
            continue
        if part.startswith("[[...") and part.endswith("]]"):
            route_segments.append(f":{part[5:-2]}*")
        elif part.startswith("[...") and part.endswith("]"):
            route_segments.append(f":{part[4:-1]}*")
        elif part.startswith("[") and part.endswith("]"):
            route_segments.append(f":{part[1:-1]}")
        else:
            route_segments.append(part)
    route = "/" + "/".join(route_segments)
    return route if route != "/" else "/", "next_app_router"


def _next_app_route_metadata(rel_path: str) -> dict | None:
    rel = _normalize_rel(rel_path)
    parts = rel.split("/")
    if "app" not in parts or Path(parts[-1]).suffix not in ROUTE_FILE_EXTENSIONS:
        return None
    file_stem = Path(parts[-1]).stem
    if file_stem not in {"page", "route", "loading", "error", "not-found", "layout", "template", "default"}:
        return None
    app_index = parts.index("app")
    raw_segments = parts[app_index + 1:-1]
    route_segments = []
    segment_kinds = []
    for raw_part in raw_segments:
        if raw_part.startswith("(") and raw_part.endswith(")"):
            segment_kinds.append("route_group")
        if raw_part.startswith("@"):
            segment_kinds.append("parallel_route")
        if raw_part.startswith("(.)") or raw_part.startswith("(..)") or raw_part.startswith("(...)"):
            segment_kinds.append("intercepted_route")
        part = _next_app_route_segment(raw_part)
        if not part:
            continue
        if part.startswith("[[...") and part.endswith("]]" ):
            route_segments.append(f":{part[5:-2]}*")
            segment_kinds.append("optional_catch_all")
        elif part.startswith("[...") and part.endswith("]"):
            route_segments.append(f":{part[4:-1]}*")
            segment_kinds.append("catch_all")
        elif part.startswith("[") and part.endswith("]"):
            route_segments.append(f":{part[1:-1]}")
            segment_kinds.append("dynamic")
        else:
            route_segments.append(part)
    route = "/" + "/".join(route_segments)
    return {
        "route": route if route != "/" else "/",
        "framework": "next_app_router",
        "route_kind": {
            "page": "page",
            "route": "route_handler",
            "not-found": "not_found_boundary",
            "loading": "loading_boundary",
            "error": "error_boundary",
        }.get(file_stem, f"{file_stem}_boundary"),
        "segment_kinds": sorted(set(segment_kinds)),
    }


def _route_from_next_pages_file(rel_path: str) -> tuple[str, str] | None:
    rel = _normalize_rel(rel_path)
    parts = rel.split("/")
    if "pages" not in parts or Path(parts[-1]).suffix not in ROUTE_FILE_EXTENSIONS:
        return None
    pages_index = parts.index("pages")
    route_parts = parts[pages_index + 1:]
    route_parts[-1] = Path(route_parts[-1]).stem
    if route_parts[-1] == "index":
        route_parts = route_parts[:-1]
    if any(part.startswith("_") for part in route_parts):
        return None
    converted = []
    for part in route_parts:
        if part.startswith("[[...") and part.endswith("]]" ):
            converted.append(f":{part[5:-2]}*")
        elif part.startswith("[...") and part.endswith("]"):
            converted.append(f":{part[4:-1]}*")
        elif part.startswith("[") and part.endswith("]"):
            converted.append(f":{part[1:-1]}")
        else:
            converted.append(part)
    route = "/" + "/".join(converted)
    return route if route != "/" else "/", "next_pages_router"


def _next_pages_route_metadata(rel_path: str) -> dict | None:
    route_result = _route_from_next_pages_file(rel_path)
    if not route_result:
        return None
    rel = _normalize_rel(rel_path)
    parts = rel.split("/")
    pages_index = parts.index("pages")
    raw_segments = parts[pages_index + 1:]
    segment_kinds = []
    for part in raw_segments:
        stem = Path(part).stem
        if stem.startswith("[[..."):
            segment_kinds.append("optional_catch_all")
        elif stem.startswith("[..."):
            segment_kinds.append("catch_all")
        elif stem.startswith("["):
            segment_kinds.append("dynamic")
    return {
        "route": route_result[0],
        "framework": route_result[1],
        "route_kind": "api_route" if raw_segments and raw_segments[0] == "api" else "page",
        "segment_kinds": sorted(set(segment_kinds)),
    }


def smoke_path_for_route(route: str) -> str:
    parts = []
    for part in str(route or "/").split("/"):
        if not part:
            continue
        if part.startswith(":"):
            token = part[1:].rstrip("*") or "param"
            parts.append(f"__{token}__")
        else:
            parts.append(part)
    return "/" + "/".join(parts) if parts else "/"


def detect_frameworks(project_root: Path) -> list[str]:
    package_json = load_json_file(project_root / "package.json", {})
    deps = {}
    if isinstance(package_json, dict):
        deps.update(package_json.get("dependencies", {}) or {})
        deps.update(package_json.get("devDependencies", {}) or {})
    config_files = framework_config_files()
    package_index = framework_package_index()
    frameworks = {
        ecosystem
        for package in deps
        for ecosystem in package_index.get(str(package), [])
    }
    for ecosystem, filenames in config_files.items():
        if any((project_root / name).exists() for name in filenames):
            frameworks.add(ecosystem)
    return sorted(frameworks)


def analyze_project_routes(
    project: str,
    project_root: Path,
    project_atlas: dict,
    progress: EngineProgress | None = None,
) -> dict:
    routes = []
    framework_counter = Counter()
    route_kind_counter = Counter()
    segment_counter = Counter()
    detected_frameworks = detect_frameworks(project_root)
    atlas_files = project_atlas.get("files", {}) if isinstance(project_atlas, dict) else {}
    route_files = atlas_file_paths(project_atlas, ROUTE_FILE_EXTENSIONS)
    if progress is not None:
        progress.phase("route_file_scan", total=len(route_files), current_project=project)
    for file_completed, rel in enumerate(route_files, start=1):
        if progress is not None:
            progress.advance(file_completed, current_project=project)
        route_metadata = _next_app_route_metadata(rel) or _next_pages_route_metadata(rel)
        if route_metadata:
            route = route_metadata["route"]
            framework = route_metadata["framework"]
            framework_counter[framework] += 1
            route_kind_counter[route_metadata["route_kind"]] += 1
            segment_counter.update(route_metadata["segment_kinds"])
            routes.append({
                "project": project,
                "file": rel,
                "scoped_file": f"{project}::{rel}",
                "framework": framework,
                "route": route,
                "smoke_path": smoke_path_for_route(route),
                "source": "atlas_path_contract",
                "route_kind": route_metadata["route_kind"],
                "segment_kinds": route_metadata["segment_kinds"],
            })
            continue
        content = read_atlas_bound_source(
            component="framework_route_analyzer",
            project=project,
            project_root=project_root,
            rel_path=rel,
            atlas_entry=atlas_files.get(rel, {}),
            reason="route declaration extraction not represented as an Atlas feature",
        )
        if content is None:
            continue
        tanstack_matches = list(TANSTACK_FILE_ROUTE_RE.finditer(content)) + list(TANSTACK_CODE_ROUTE_RE.finditer(content))
        for match in tanstack_matches:
            route = match.group(1)
            framework_counter["tanstack_router"] += 1
            route_kind = "file_route" if "createFileRoute" in match.group(0) else "code_route"
            route_kind_counter[route_kind] += 1
            routes.append({
                "project": project,
                "file": rel,
                "scoped_file": f"{project}::{rel}",
                "framework": "tanstack_router",
                "route": route if route.startswith("/") else f"/{route}",
                "smoke_path": smoke_path_for_route(route if route.startswith("/") else f"/{route}"),
                "source": "route_declaration",
                "route_kind": route_kind,
                "segment_kinds": [],
            })
        if tanstack_matches:
            continue
        for match in ROUTE_PATH_RE.finditer(content):
            route = match.group(1) or match.group(2)
            if not route or route.startswith(("http", "*")):
                continue
            framework_counter["react_router"] += 1
            route_kind_counter["framework_route" if REACT_ROUTER_FRAMEWORK_RE.search(content) else "data_route"] += 1
            routes.append({
                "project": project,
                "file": rel,
                "scoped_file": f"{project}::{rel}",
                "framework": "react_router",
                "route": route if route.startswith("/") else f"/{route}",
                "smoke_path": smoke_path_for_route(route if route.startswith("/") else f"/{route}"),
                "source": "route_declaration",
                "route_kind": "framework_route" if REACT_ROUTER_FRAMEWORK_RE.search(content) else "data_route",
                "segment_kinds": [],
            })
    hybrid_next = framework_counter.get("next_app_router", 0) > 0 and framework_counter.get("next_pages_router", 0) > 0
    return {
        "project": project,
        "root": str(project_root),
        "frameworks": detected_frameworks,
        "route_counts": dict(framework_counter),
        "route_kind_counts": dict(route_kind_counter),
        "segment_kind_counts": dict(segment_counter),
        "hybrid_next_app_pages": hybrid_next,
        "routes": sorted(routes, key=lambda item: (item["framework"], item["route"], item["file"])),
    }


def run_framework_route_analyzer() -> dict:
    logger.info("Analyzing framework-aware route maps...")
    progress = EngineProgress("framework_route_analyzer")
    progress.start()
    projects = resolve_runtime_projects(ROOT)
    atlas = load_atlas_data()
    by_project = {}
    for project, root in projects.items():
        progress.checkpoint("project_start", project=project)
        by_project[project] = analyze_project_routes(project, root, atlas.get(project, {}), progress=progress)
    all_routes = [route for payload in by_project.values() for route in payload.get("routes", [])]
    framework_counter = Counter(route["framework"] for route in all_routes)
    route_kind_counter = Counter(route.get("route_kind", "unknown") for route in all_routes)
    payload = {
        "meta": {"kind": "framework_routes", "version": "v1", "file_universe": "committed_atlas"},
        "summary": {
            "projects": len(by_project),
            "routes": len(all_routes),
            "framework_route_counts": dict(framework_counter),
            "route_kind_counts": dict(route_kind_counter),
            "hybrid_next_projects": sorted(
                project for project, item in by_project.items() if item.get("hybrid_next_app_pages")
            ),
        },
        "by_project": by_project,
        "routes": all_routes,
    }
    save_json_atomic(RAW_DIR / "framework_routes.json", payload)
    write_current_atlas_lineage(
        artifact_id="framework_routes",
        producer="tools.engines.framework_route_analyzer",
        artifact_payload=payload,
        atlas=atlas,
    )

    lines = [
        "# Framework Route Analyzer",
        "",
        "Framework-aware route map for Next App Router, Next Pages Router, and React Router declarations.",
        "",
        f"- projects: `{len(by_project)}`",
        f"- routes: `{len(all_routes)}`",
        f"- framework route counts: `{dict(framework_counter)}`",
        "",
        "## Routes",
    ]
    for route in all_routes[:120]:
        lines.append(
            f"- `{route['project']}` | `{route['framework']}` | `{route['route']}` | "
            f"smoke `{route['smoke_path']}` | `{route['file']}`"
        )
    save_text_atomic(REPORTS_DIR / "framework_routes.md", "\n".join(lines))
    progress.complete("PASS", projects=len(by_project), routes=len(all_routes))
    logger.info("Framework route artifacts written.")
    return payload


if __name__ == "__main__":
    run_framework_route_analyzer()

import argparse
import json
import re
import sys
import os
from pathlib import Path
from typing import Any

# Sovereign Root Recovery (v19.8)
import sys
import os
from pathlib import Path

# Ensure the Nexora SAGE root is in sys.path
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.honesty_telemetry import record_honesty_event
from tools.core.jsonc import loads_jsonc

from tools.core.config import (
    DISCOVERY_FILE,
    ROOT,
    CODE_MAPS_DIR,
    PROFILES_FILE,
    REPORTS_DIR,
    save_json_atomic,
    save_text_atomic,
    ensure_output_dir,
    DOCTRINE,
)
from tools.core.language_registry import (
    is_config_or_manifest_file,
    language_registry_provenance,
    load_language_registry,
    language_extensions,
    extension_language_map,
    plugins_for_bundler,
    plugins_for_dependencies,
    skip_dirs,
)
from tools.core.installation_identity import (
    path_is_excluded,
    prune_walk_directories,
    runtime_installation_excluded_roots,
)
from tools.core.quality_gate_policy import quality_gate_discovery_seed
from tools.core.distribution_policy import is_managed_clean_mirror_path
from tools.core.repository_topology import (
    classify_project_roles as classify_repository_project_roles,
    resolve_repository_topology,
    workspace_patterns,
)

PUBLIC_DISTRIBUTION_MANIFEST = CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json"


def resolve_discovery_root(
    *,
    code_maps_dir: Path = CODE_MAPS_DIR,
    environment: dict[str, str] | None = None,
) -> Path:
    """Resolve analyzed reality independently from the SAGE installation root."""

    env = environment if environment is not None else os.environ
    target_root = str(env.get("CODEMAPS_TARGET_ROOT") or "").strip()
    if target_root:
        target = Path(target_root).expanduser()
        if not target.is_absolute():
            target = (Path.cwd() / target).resolve()
        else:
            target = target.resolve()
        if not target.is_dir():
            raise RuntimeError(f"Explicit discovery target root is not a directory: {target}")
        return target

    if (code_maps_dir / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file():
        raise RuntimeError(
            "A public SAGE distribution requires an explicit target repository. "
            "Run `python sage.py init --target-root <repository>`."
        )
    return code_maps_dir.parent.resolve()


def workspace_root_reference(target_root: Path, *, code_maps_dir: Path = CODE_MAPS_DIR) -> str:
    """Persist generated workspace truth relative to the installation when possible."""

    return Path(os.path.relpath(target_root.resolve(), code_maps_dir.resolve())).as_posix()


ROOT_DIR = resolve_discovery_root()

LANGUAGE_REGISTRY: dict[str, Any] = {}
SKIP_DIRS: set[str] = set()
SOURCE_EXTS: set[str] = set()
LANGUAGE_MAP: dict[str, str] = {}
ROOT_LEVEL_APP_MARKERS: set[str] = set()
MONOREPO_ROOT_MARKERS: set[str] = set()
MONOREPO_ROOT_FILES: set[str] = set()
CONFIG_FILE_MARKERS: dict[str, Any] = {}


def refresh_language_registry_context() -> dict[str, str]:
    global LANGUAGE_REGISTRY, SKIP_DIRS, SOURCE_EXTS, LANGUAGE_MAP
    global ROOT_LEVEL_APP_MARKERS, MONOREPO_ROOT_MARKERS, MONOREPO_ROOT_FILES, CONFIG_FILE_MARKERS

    LANGUAGE_REGISTRY = load_language_registry()
    SKIP_DIRS = skip_dirs()
    SOURCE_EXTS = language_extensions()
    LANGUAGE_MAP = extension_language_map()
    ROOT_LEVEL_APP_MARKERS = set(LANGUAGE_REGISTRY.get("root_level_app_markers", []))
    MONOREPO_ROOT_MARKERS = set(LANGUAGE_REGISTRY.get("monorepo_root_markers", []))
    MONOREPO_ROOT_FILES = set(LANGUAGE_REGISTRY.get("monorepo_root_files", []))
    CONFIG_FILE_MARKERS = LANGUAGE_REGISTRY.get("config_file_markers", {})
    return language_registry_provenance()


refresh_language_registry_context()
def load_profiles():
    if not PROFILES_FILE.exists():
        return {}
    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        return data.get("profiles", {})
    except Exception as exc:
        record_honesty_event(
            component="discovery",
            category="caught_error",
            operation="load_profiles",
            subject=str(PROFILES_FILE),
            reason="architecture profile registry could not be loaded",
            fallback="empty_profile_registry",
            claim_impact="architecture_detection_degraded",
            exception=exc,
        )
        return {}


ARCH_PROFILES = load_profiles()


def is_skipped_dir_name(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    return normalized in {item.lower() for item in SKIP_DIRS}


def runtime_installation_exclusions() -> set[Path]:
    return runtime_installation_excluded_roots(ROOT_DIR, CODE_MAPS_DIR)


def is_skipped_dir_path(path: Path) -> bool:
    return is_skipped_dir_name(path.name) or path_is_excluded(
        path,
        runtime_installation_exclusions(),
    )


def parse_tsconfig(proj_path: Path):
    tsconfig = proj_path / "tsconfig.json"
    if not tsconfig.exists():
        tsconfig = ROOT_DIR / "tsconfig.json"
    if tsconfig.exists():
        try:
            content = tsconfig.read_text(encoding="utf-8")
            data = loads_jsonc(content)
            return data.get("compilerOptions", {}).get("paths", {})
        except Exception as exc:
            record_honesty_event(
                component="discovery",
                category="caught_error",
                operation="parse_tsconfig",
                subject=str(tsconfig),
                reason="tsconfig paths could not be parsed",
                fallback="empty_alias_map",
                claim_impact="alias_resolution_degraded",
                exception=exc,
            )
            return {}
    return {}


def detect_languages(proj_path: Path, *, profile: str = "full"):
    """
    Fingerprints the languages present in the project based on file extensions.
    Detects polyglot status if multiple primary languages are found.
    """
    counts = {}
    total_files = 0
    try:
        if profile == "entrypoint-smoke":
            scan_roots = [proj_path]
            if (proj_path / "src").exists():
                scan_roots.append(proj_path / "src")
            for scan_root in scan_roots:
                for entry in scan_root.iterdir():
                    if not entry.is_file():
                        continue
                    ext = entry.suffix.lower()
                    if ext in LANGUAGE_MAP:
                        lang = LANGUAGE_MAP[ext]
                        counts[lang] = counts.get(lang, 0) + 1
                        total_files += 1
        else:
            excluded_roots = runtime_installation_exclusions()
            for root, dirs, files in os.walk(str(proj_path)):
                prune_walk_directories(
                    root,
                    dirs,
                    skipped_names=SKIP_DIRS,
                    excluded_roots=excluded_roots,
                )
                for file in files:
                    ext = Path(file).suffix.lower()
                    if ext in LANGUAGE_MAP:
                        lang = LANGUAGE_MAP[ext]
                        counts[lang] = counts.get(lang, 0) + 1
                        total_files += 1
    except Exception as exc:
        record_honesty_event(
            component="discovery",
            category="caught_error",
            operation="detect_languages",
            subject=str(proj_path),
            reason="language fingerprint walk failed",
            fallback="empty_language_detection",
            claim_impact="project_dna_degraded",
            exception=exc,
        )

    detected = []
    for lang, count in counts.items():
        percentage = (count / total_files * 100) if total_files > 0 else 0
        if percentage > 5:  # Threshold for significance
            detected.append(lang)
    
    return sorted(detected), len(detected) > 1


def load_package_json(proj_path: Path):
    candidates = [proj_path / "package.json"]
    if proj_path != ROOT_DIR:
        candidates.append(ROOT_DIR / "package.json")
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8")), candidate
        except Exception as exc:
            record_honesty_event(
                component="discovery",
                category="caught_error",
                operation="load_package_json",
                subject=str(candidate),
                reason="package manifest could not be parsed",
                fallback="try_next_manifest_candidate",
                claim_impact="framework_detection_degraded",
                exception=exc,
            )
            continue
    return {}, None


def _confidence_label(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _evidence_origin(token: str) -> str:
    value = str(token or "").lower().strip()
    if not value:
        return "unknown"
    deterministic_paths = {"path:src", "path:is_project_src_root", "path:app"}
    if value in deterministic_paths:
        return "deterministic"
    deterministic_prefixes = (
        "config:",
        "file:",
        "package.json:",
        "dependency:",
        "script:",
        "tsconfig:",
    )
    if value.startswith(deterministic_prefixes):
        return "deterministic"
    heuristic_prefixes = ("path:", "dir:", "workspace:", "bundler:")
    if value.startswith(heuristic_prefixes):
        return "heuristic"
    return "unknown"


def _classify_evidence(evidence: list[str]) -> dict[str, Any]:
    deterministic = [token for token in evidence if _evidence_origin(token) == "deterministic"]
    heuristic = [token for token in evidence if _evidence_origin(token) == "heuristic"]
    unknown = [token for token in evidence if _evidence_origin(token) == "unknown"]
    primary = "deterministic" if deterministic else ("heuristic" if heuristic else "unknown")
    return {
        "primary": primary,
        "deterministic": deterministic,
        "heuristic": heuristic,
        "unknown": unknown,
    }


def _summarize_signal_determinism(metadata_report: dict[str, Any]) -> dict[str, Any]:
    projects = metadata_report.values() if isinstance(metadata_report, dict) else []
    summary = {
        "total_projects": 0,
        "bundler_primary": {"deterministic": 0, "heuristic": 0, "unknown": 0},
        "monorepo_primary": {"deterministic": 0, "heuristic": 0, "unknown": 0},
        "source_root_primary": {"deterministic": 0, "heuristic": 0, "unknown": 0},
    }
    for project in projects:
        signals = (project.get("workspace_signals") or {}) if isinstance(project, dict) else {}
        quality = (signals.get("signal_quality") or {}) if isinstance(signals, dict) else {}
        summary["total_projects"] += 1
        for key, bucket in (
            ("bundler", "bundler_primary"),
            ("monorepo", "monorepo_primary"),
            ("source_root", "source_root_primary"),
        ):
            primary = ((quality.get(key) or {}).get("primary") or "unknown").strip().lower()
            if primary not in summary[bucket]:
                primary = "unknown"
            summary[bucket][primary] += 1
    return summary


def _write_determinism_report(metadata_report: dict[str, Any], summary: dict[str, Any]) -> None:
    lines = [
        "# Discovery Determinism Report",
        "",
        "This report shows deterministic-vs-heuristic signal ownership during discovery.",
        "",
        f"- Total projects: {summary.get('total_projects', 0)}",
        f"- Bundler primary: {summary.get('bundler_primary', {})}",
        f"- Monorepo primary: {summary.get('monorepo_primary', {})}",
        f"- Source root primary: {summary.get('source_root_primary', {})}",
        "",
        "| Project | Bundler Primary | Monorepo Primary | Source Root Primary |",
        "|---|---|---|---|",
    ]
    for project_key in sorted(metadata_report.keys()):
        signals = ((metadata_report.get(project_key) or {}).get("workspace_signals") or {})
        quality = signals.get("signal_quality") or {}
        bundler_primary = (quality.get("bundler") or {}).get("primary", "unknown")
        monorepo_primary = (quality.get("monorepo") or {}).get("primary", "unknown")
        source_root_primary = (quality.get("source_root") or {}).get("primary", "unknown")
        lines.append(
            f"| `{project_key}` | `{bundler_primary}` | `{monorepo_primary}` | `{source_root_primary}` |"
        )
    save_text_atomic(REPORTS_DIR / "discovery_determinism.md", "\n".join(lines) + "\n")


def _detect_bundler(pkg_dir: Path, deps: dict[str, Any], scripts: dict[str, Any]):
    evidence: list[str] = []
    score = 0
    bundler = None

    next_files = [name for name in CONFIG_FILE_MARKERS["nextjs"] if (pkg_dir / name).exists()]
    vite_files = [name for name in CONFIG_FILE_MARKERS["vite"] if (pkg_dir / name).exists()]
    expo_files = [name for name in CONFIG_FILE_MARKERS["expo"] if (pkg_dir / name).exists()]
    webpack_files = [name for name in CONFIG_FILE_MARKERS["webpack"] if (pkg_dir / name).exists()]

    if next_files:
        bundler = "nextjs"
        score += 85
        evidence.extend([f"config:{name}" for name in next_files])
    elif "next" in deps:
        bundler = "nextjs"
        score += 70
        evidence.append("dependency:next")
    elif any("next" in str(v).lower() for v in scripts.values()):
        bundler = "nextjs"
        score += 55
        evidence.append("script:next")

    if bundler is None:
        if vite_files:
            bundler = "vite"
            score += 85
            evidence.extend([f"config:{name}" for name in vite_files])
        elif "vite" in deps:
            bundler = "vite"
            score += 70
            evidence.append("dependency:vite")
        elif any("vite" in str(v).lower() for v in scripts.values()):
            bundler = "vite"
            score += 55
            evidence.append("script:vite")

    if bundler is None:
        if expo_files:
            bundler = "expo"
            score += 85
            evidence.extend([f"config:{name}" for name in expo_files])
        elif "expo" in deps:
            bundler = "expo"
            score += 70
            evidence.append("dependency:expo")

    if bundler is None:
        if webpack_files:
            bundler = "webpack"
            score += 85
            evidence.extend([f"config:{name}" for name in webpack_files])
        elif "webpack" in deps or "webpack-cli" in deps or "webpack-dev-server" in deps:
            bundler = "webpack"
            score += 70
            evidence.append("dependency:webpack")
        elif any("webpack" in str(v).lower() for v in scripts.values()):
            bundler = "webpack"
            score += 55
            evidence.append("script:webpack")

    return bundler, _confidence_label(score), evidence


def _detect_monorepo(workspaces, package_manager, root_dirs: set[str], root_files: set[str]):
    evidence: list[str] = []
    score = 0

    if workspaces:
        score += 90
        evidence.append("package.json:workspaces")
    else:
        evidence.append("package.json:no-workspaces")
    if root_dirs.intersection({"apps", "packages", "libs"}):
        score += 70
        evidence.extend([f"dir:{name}" for name in sorted(root_dirs.intersection({"apps", "packages", "libs"}))])
    if root_files.intersection({"turbo.json", "nx.json", "lerna.json"}):
        score += 80
        evidence.extend([f"file:{name}" for name in sorted(root_files.intersection({"turbo.json", "nx.json", "lerna.json"}))])
    if package_manager and "pnpm" in str(package_manager).lower() and root_files.intersection({"pnpm-workspace.yaml"}):
        score += 60
        evidence.append("file:pnpm-workspace.yaml")

    return score >= 70, _confidence_label(score), evidence


def _detect_source_root(proj_path: Path, ts_paths: dict[str, Any], is_monorepo: bool, bundler: str | None):
    evidence: list[str] = []
    score = 0
    source_root = None

    if (proj_path / "src").exists():
        source_root = "src"
        score += 90
        evidence.append("path:src")
    elif (proj_path / "package.json").exists() and not (proj_path / "src").exists():
        source_root = "."
        score += 75
        evidence.append("package.json:root")
    elif proj_path.name == "src" and (proj_path.parent / "package.json").exists():
        source_root = "src"
        score += 85
        evidence.append("path:is_project_src_root")
    elif any(str(key).startswith("@/") for key in ts_paths.keys()):
        source_root = "src" if (proj_path / "src").exists() else "."
        score += 60
        evidence.append("tsconfig:path_aliases")
    elif is_monorepo:
        source_root = "."
        score += 80
        evidence.append("workspace:monorepo_root")
    elif bundler == "nextjs" and (proj_path / "app").exists():
        source_root = "."
        score += 75
        evidence.append("path:app")
    elif bundler in {"vite", "expo"}:
        source_root = "."
        score += 35
        evidence.append(f"bundler:{bundler}")

    return source_root, _confidence_label(score), evidence


def read_workspace_signals(proj_path: Path):
    package_json, package_path = load_package_json(proj_path)
    pkg_dir = package_path.parent if package_path else proj_path
    pkg_name = package_json.get("name") if isinstance(package_json, dict) else None
    deps = {}
    if isinstance(package_json, dict):
        deps = {
            **(package_json.get("dependencies") or {}),
            **(package_json.get("devDependencies") or {}),
        }
    scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
    package_manager = package_json.get("packageManager") if isinstance(package_json, dict) else None
    workspaces = package_json.get("workspaces") if isinstance(package_json, dict) else None

    root_files = {
        entry.name.lower()
        for entry in proj_path.iterdir()
        if entry.is_file()
    } if proj_path.exists() else set()
    root_dirs = {
        entry.name.lower()
        for entry in proj_path.iterdir()
        if entry.is_dir() and not is_skipped_dir_path(entry)
    } if proj_path.exists() else set()

    bundler, bundler_confidence, bundler_evidence = _detect_bundler(pkg_dir, deps, scripts)
    is_monorepo, monorepo_confidence, monorepo_evidence = _detect_monorepo(workspaces, package_manager, root_dirs, root_files)
    ts_paths = parse_tsconfig(proj_path)
    source_root, source_root_confidence, source_root_evidence = _detect_source_root(proj_path, ts_paths, is_monorepo, bundler)
    signal_quality = {
        "bundler": _classify_evidence(bundler_evidence),
        "monorepo": _classify_evidence(monorepo_evidence),
        "source_root": _classify_evidence(source_root_evidence),
    }

    return {
        "package_name": pkg_name,
        "package_manager": package_manager,
        "deps": deps,
        "scripts": scripts,
        "workspaces": workspaces,
        "bundler": bundler,
        "bundler_confidence": bundler_confidence,
        "bundler_evidence": bundler_evidence,
        "is_monorepo": is_monorepo,
        "monorepo_confidence": monorepo_confidence,
        "monorepo_evidence": monorepo_evidence,
        "source_root": source_root,
        "source_root_confidence": source_root_confidence,
        "source_root_evidence": source_root_evidence,
        "signal_quality": signal_quality,
        "signal_sources": {
            "config_files": [
                name
                for names in CONFIG_FILE_MARKERS.values()
                for name in names
                if (pkg_dir / name).exists()
            ],
            "root_files": sorted(root_files.intersection(MONOREPO_ROOT_FILES)),
            "root_dirs": sorted(root_dirs.intersection(MONOREPO_ROOT_MARKERS)),
        },
    }


def detect_module_root(proj_path: Path):
    workspace_signals = read_workspace_signals(proj_path)
    if workspace_signals.get("source_root") == "src":
        src_path = proj_path / "src"
        if src_path.exists():
            return "src", src_path
    if workspace_signals.get("source_root") == ".":
        return ".", proj_path

    src_path = proj_path / "src"
    if src_path.exists():
        return "src", src_path

    workspace_files = {
        entry.name.lower()
        for entry in proj_path.iterdir()
        if entry.is_file()
    }
    root_markers = {
        entry.name.lower()
        for entry in proj_path.iterdir()
        if entry.is_dir() and not is_skipped_dir_path(entry)
    }
    has_source_files = any(
        entry.is_file() and entry.suffix in SOURCE_EXTS for entry in proj_path.iterdir()
    )
    if (
        has_source_files
        or root_markers.intersection(ROOT_LEVEL_APP_MARKERS)
        or root_markers.intersection(MONOREPO_ROOT_MARKERS)
        or workspace_files.intersection(MONOREPO_ROOT_FILES)
    ):
        return ".", proj_path
    return ".", proj_path


def detect_structure(abs_path: Path, *, profile: str = "full"):
    """Autonomous DNA Matcher using ARCH_PROFILES."""
    module_root_name, src_path = detect_module_root(abs_path)
    if not src_path.exists():
        return ["MODULAR_FLAT"], module_root_name, "MODULAR_FLAT"
    if profile == "entrypoint-smoke":
        signals = read_workspace_signals(abs_path)
        bundler = signals.get("bundler")
        if bundler == "nextjs":
            return ["NEXTJS_APP"], module_root_name, "NEXTJS_APP"
        return ["MODULAR_FLAT"], module_root_name, "MODULAR_FLAT"

    # Profile Scoring
    profile_scores = {}
    found_markers = {entry.name.lower() for entry in src_path.iterdir() if entry.is_dir()}
    
    # Deep marker search (recursive)
    deep_markers = set()
    for arch in ARCH_PROFILES.values():
        for marker in arch.get("markers", []):
            if any(src_path.glob(f"**/{marker}")):
                deep_markers.add(marker)

    for profile_id, profile_def in ARCH_PROFILES.items():
        markers = set(profile_def.get("markers", []))
        matches = markers.intersection(deep_markers)
        score = len(matches) / len(markers) if markers else 0
        profile_scores[profile_id] = score

    # Select best match exceeding threshold (Ordered by complexity)
    best_profile = "MODULAR_FLAT"
    
    # 3. Symmetry Analysis (Modular Monolith Detection)
    # Check if markers are grouped into sibling 'Module' roots
    potential_module_roots = {}
    for marker in deep_markers:
        matches = list(src_path.glob(f"*/{marker}"))
        for m in matches:
            if m.is_dir():
                mod_name = m.parent.name
                potential_module_roots[mod_name] = potential_module_roots.get(mod_name, 0) + 1
    
    # If multiple sibling folders share the same architectural pattern (e.g., 3+ markers)
    strong_modules = [m for m, count in potential_module_roots.items() if count >= 2]
    is_modular = len(strong_modules) >= 2

    # Priority Rule
    hex_score = profile_scores.get("HEXAGONAL_PURE", 0)
    fsd_score = profile_scores.get("FSD_STANDARD", 0)
    
    if hex_score >= 0.5 and fsd_score >= 0.5 and is_modular:
        best_profile = "SOVEREIGN_ELITE"
    elif is_modular:
        # If it's modular but not full hybrid elite, it could be a Modular Monolith of FSD or Hex
        best_profile = "MODULAR_MONOLITH"
    else:
        # Standard scoring
        max_score = 0
        for profile_id, score in profile_scores.items():
            threshold = ARCH_PROFILES.get(profile_id, {}).get("threshold", 0.3)
            if score >= threshold and score > max_score:
                max_score = score
                best_profile = profile_id

    # Fallback/Combination Logic
    final_arch = [best_profile]
    return final_arch, module_root_name, best_profile


def scan_package_libraries(proj_path: Path):
    plugins = set()
    signals = read_workspace_signals(proj_path)
    deps = signals.get("deps", {}) or {}
    plugins.update(plugins_for_dependencies(deps))
    plugins.update(plugins_for_bundler(signals.get("bundler")))
    return sorted(plugins)


def detect_workspace_boundaries():
    """Extract workspace hints from pnpm-workspace.yaml or package.json."""
    return workspace_patterns(ROOT_DIR)


def resolve_discovery_topology(*, profile: str = "full") -> dict:
    """Resolve canonical repository topology for the active workspace."""

    print(f"[SCOUT] Starting recursive evidence-first crawl at: {ROOT_DIR}")
    ws_patterns = detect_workspace_boundaries()
    if ws_patterns:
        print(f"     [SIGNAL] Workspace boundaries detected: {', '.join(ws_patterns)}")
    structural_markers = set(ROOT_LEVEL_APP_MARKERS)
    for profile in ARCH_PROFILES.values():
        structural_markers.update(str(marker).lower() for marker in profile.get("markers", []) if str(marker).strip())
    main_module_root, main_scan_root = detect_module_root(ROOT_DIR)
    if profile == "entrypoint-smoke":
        projects = {"MAIN": main_module_root} if main_scan_root.exists() else {}
        return {
            "selected_projects": projects,
            "selected_project_roles": classify_project_roles(projects),
            "project_ownership_exclusions": {
                key: []
                for key in projects
            },
            "file_ownership_contract": "nearest_discovered_project_root_v1",
        }
    topology = resolve_repository_topology(
        ROOT_DIR,
        main_project_path=main_module_root,
        structural_markers=structural_markers,
        role_markers=DOCTRINE.get("discovery_project_role_markers", {}),
        excluded_paths=runtime_installation_exclusions(),
        excluded_path_predicate=is_managed_clean_mirror_path,
        config_or_manifest_predicate=is_config_or_manifest_file,
        skipped_names=skip_dirs(),
    )
    for key, rel_path in topology["selected_projects"].items():
        if key != "MAIN":
            print(f"     [BOUNDARY] Found project: {key} at {rel_path}")
    for key, rel_path in topology["excluded_projects"].items():
        print(f"     [CANDIDATE] Explicit selection required: {key} at {rel_path}")
    return topology


def discover_project_roots(*, profile: str = "full"):
    """Universal Evidence-First Discovery Crawler with Workspace Signal Support."""

    return resolve_discovery_topology(profile=profile)["selected_projects"]


def classify_project_roles(projects: dict):
    role_markers = DOCTRINE.get("discovery_project_role_markers", {}) if isinstance(DOCTRINE, dict) else {}
    return classify_repository_project_roles(projects, role_markers)


def build_audit_seed(main_arch):
    rules = {}
    for arch in main_arch:
        profile = ARCH_PROFILES.get(arch, {}) if isinstance(ARCH_PROFILES, dict) else {}
        for rule_name in profile.get("enabled_rules", []) if isinstance(profile, dict) else []:
            rules[str(rule_name)] = {"enabled": True, "source": "architecture_profiles.json"}
    return {
        "file_extensions": sorted(SOURCE_EXTS),
        "rules": rules,
        # Section objects belong to Audit Policy. Discovery may omit the override,
        # but must never emit display-name strings into an object-only contract.
        "report_sections": [],
    }


def build_quality_gate_seed():
    return quality_gate_discovery_seed()


def build_host_intelligence_seed() -> dict[str, Any]:
    defaults = DOCTRINE.get("discovery_host_intelligence_defaults", {}) if isinstance(DOCTRINE, dict) else {}
    if isinstance(defaults, dict):
        return {
            str(key): list(value) if isinstance(value, list) else value
            for key, value in defaults.items()
        }
    return {
        "host_locked_patterns": [],
        "compose_preferred_patterns": [],
        "manual_review_patterns": [],
        "public_boundary_patterns": [],
        "integration_seam_patterns": [],
    }


def run_discovery(*, profile: str = "full", output_path: Path | None = None, write_reports: bool = True):
    language_registry_state = refresh_language_registry_context()
    print("\n" + "=" * 80)
    print("[SOVEREIGN SCOUT] Machine Discovery Proposal".center(80))
    print("=" * 80 + "\n")

    repository_topology = resolve_discovery_topology(profile=profile)
    projects = repository_topology["selected_projects"]
    project_roles = repository_topology["selected_project_roles"]
    metadata_report = {}

    for name, rel_path in projects.items():
        abs_path = ROOT_DIR / rel_path
        print(f"  [SCAN] {name:<25} | Path: {rel_path}")

        plugins = scan_package_libraries(abs_path)
        arch_types, mod_root, best_profile = detect_structure(abs_path, profile=profile)
        workspace_signals = read_workspace_signals(abs_path)
        languages, is_polyglot = detect_languages(abs_path, profile=profile)

        metadata_report[name] = {
            "path": rel_path,
            "metadata": {
                "architecture": arch_types,
                "detected_profile": best_profile,
                "plugins": plugins,
                "languages": languages,
                "is_polyglot": is_polyglot
            },
            "module_root": mod_root.replace("src/", ""),
            "workspace_signals": workspace_signals,
        }
        poly_label = " [POLYGLOT]" if is_polyglot else ""
        print(f"     [RESULT] Profile: {best_profile} | Languages: {', '.join(languages)}{poly_label} | Root Candidate: {mod_root}")

    main_info = metadata_report.get("MAIN", {})
    main_arch = main_info.get("metadata", {}).get("architecture", ["STANDARD_SPA"])
    main_plugins = main_info.get("metadata", {}).get("plugins", [])
    main_workspace_signals = read_workspace_signals(ROOT_DIR)
    main_bundler = main_workspace_signals.get("bundler") or ("vite" if "vite" in main_plugins else "unknown")

    host_intel = build_host_intelligence_seed()

    determinism_overview = _summarize_signal_determinism(metadata_report)

    proposal = {
        "_meta": {
            "kind": "codemaps.discovery",
            "version": "19.0",
            "generated_by": "tools/orchestrators/discovery.py",
            "purpose": "Machine-generated architectural proposal layer",
            "profile": profile,
            "language_registry": language_registry_state,
        },
        "workspace_root": workspace_root_reference(ROOT_DIR),
        "skip_dirs": sorted(SKIP_DIRS),
        "source_extensions": sorted(SOURCE_EXTS),
        "use_sqlite": True,
        "variations": projects,
        "project_roles": project_roles,
        "_repository_topology": repository_topology,
        "environment": {
            "bundler": main_bundler,
            "path_aliases": parse_tsconfig(ROOT_DIR),
        },
        "plugins": sorted(main_plugins),
        "architecture": {
            "type": "+".join(main_arch).lower(),
            "detected_profile": main_info.get("metadata", {}).get("detected_profile", "MODULAR_FLAT"),
            "module_root": main_info.get("module_root", "src"),
        },
        "host_intelligence": host_intel,
        "audit_seed": build_audit_seed(main_arch),
        "quality_gate_seed": build_quality_gate_seed(),
        "proposal_notes": [
            "This file is safe to regenerate.",
            "Discovery keys may need canonical alias mapping in codemaps.overrides.json.",
            "Runtime engines should consume codemaps.config.json instead of this file.",
        ],
        "_discovery_metadata": {
            "projects": metadata_report,
            "discovered_project_count": len(metadata_report),
            "raw_variation_keys": sorted(metadata_report.keys()),
            "workspace_signals": main_workspace_signals,
            "determinism_overview": determinism_overview,
        },
    }

    target_path = Path(output_path) if output_path else DISCOVERY_FILE
    save_json_atomic(target_path, proposal)
    if write_reports:
        _write_determinism_report(metadata_report, determinism_overview)
    
    from tools.core.config import CONFIG_FILE
    if not CONFIG_FILE.exists():
        print(f"     [PNP] Generating initial codemaps.config.json...")
        config = {
            "workspace_root": proposal["workspace_root"],
            "skip_dirs": proposal["skip_dirs"],
            "use_sqlite": proposal["use_sqlite"],
            "architecture": proposal["architecture"],
            "projects": {k: {"path": v} for k, v in proposal["variations"].items()},
            "doctrine_override": proposal["audit_seed"].get("DOCTRINE_TYPE", "STANDARD")
        }
        save_json_atomic(CONFIG_FILE, config)
        print(f"     [DONE] Plug & Play Config Sealed.")

    print("\n" + "=" * 80)
    print("[OK] Discovery proposal written.".center(80))
    print("=" * 80 + "\n")
    return proposal


if __name__ == "__main__":
    from tools.core.config import ensure_output_dir
    ensure_output_dir()
    parser = argparse.ArgumentParser(description="Generate a Nexora SAGE discovery proposal.")
    parser.add_argument(
        "--profile",
        choices=["full", "entrypoint-smoke"],
        default="full",
        help="Use entrypoint-smoke only for fast entrypoint contract validation.",
    )
    parser.add_argument("--output", help="Write discovery proposal to a custom path instead of the live discovery file.")
    args = parser.parse_args()
    run_discovery(
        profile=args.profile,
        output_path=Path(args.output) if args.output else None,
        write_reports=args.profile == "full" and not args.output,
    )

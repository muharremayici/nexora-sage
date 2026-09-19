import os
import json
import hashlib
import shutil
import tempfile
import time
from fnmatch import fnmatchcase
from pathlib import Path
from tools.core.text_normalizer import deep_repair
from tools.core.language_registry import (
    config_file_marker_map,
    is_config_or_manifest_file,
    language_extensions,
    load_language_registry,
    manifest_file_marker_map,
    plugins_for_bundler,
    plugins_for_dependencies,
    skip_dirs as registry_skip_dirs,
)
from tools.core.repository_topology import resolve_repository_topology
from tools.core.distribution_policy import is_managed_clean_mirror_path
from tools.core.installation_identity import (
    prune_walk_directories,
    runtime_installation_excluded_roots,
)
from tools.core.jsonc import loads_jsonc
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.core.external_target_generation import external_target_output_slug
from tools.core.target_repository_trust import is_target_path_contained

# Directory setup
# [Architect Protocol] Unified Atomic Paths (Sealed 4.0)
CORE_DIR = Path(__file__).resolve().parent
TOOLS_DIR = CORE_DIR.parent
CODE_MAPS_DIR = TOOLS_DIR.parent

# Files - AUTHORITY: config/codemaps.config.json
CONFIG_DIR = CODE_MAPS_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "codemaps.config.json"
DISCOVERY_FILE = CONFIG_DIR / "codemaps.discovery.json"
OVERRIDES_FILE = CONFIG_DIR / "codemaps.overrides.json"
DOCTRINE_FILE = CONFIG_DIR / "architecture_doctrine.json"
DOCTRINE_MANIFEST_FILE = CONFIG_DIR / "doctrines" / "manifest.json"
PROFILES_FILE = CONFIG_DIR / "architecture_profiles.json"
SCHEMAS_DIR = CONFIG_DIR / "schemas"
BOOTSTRAP_FILE = CORE_DIR / "bootstrap_env.py"
PRODUCT_OPERATIONAL_DIR = CODE_MAPS_DIR / "output" / ".operational"
MCP_OPERATIONAL_DIR = PRODUCT_OPERATIONAL_DIR / "mcp"
MCP_CALL_TELEMETRY_DB = MCP_OPERATIONAL_DIR / "mcp_call_telemetry.db"
MCP_HONESTY_TELEMETRY_FILE = MCP_OPERATIONAL_DIR / "honesty_telemetry.json"

def _load_json(path: Path, repair: bool = True):
    if not path.exists(): return {}
    try:
        # Machine-generated JSONs should be pure UTF-8. 
        # User-edited config (BOM possible) uses utf-8-sig.
        encoding = "utf-8-sig" if repair else "utf-8"
        with open(path, "r", encoding=encoding) as f:
            data = json.load(f)
            return deep_repair(data) if repair else data
    except Exception as e:
        print(f"Warning: Failed to parse {path.name}: {e}")
        return {}

def normalize_path(path_val) -> str:
    """Canonical POSIX-style path normalization for cross-engine consistency."""
    if not path_val: return ""
    return str(path_val).replace("\\", "/").strip("/")


def save_text_atomic(path: Path, content: str, encoding: str = "utf-8"):
    """Write content to a temporary file and atomically rename it to the target path."""
    path = Path(path)
    native_parent = native_filesystem_path(path.parent)
    Path(native_parent).mkdir(parents=True, exist_ok=True)
    
    # Use a custom temp file suffix for visibility
    temp_fd, temp_name = tempfile.mkstemp(dir=native_parent, prefix="cm_tmp_", suffix=".tmp")
    try:
        with os.fdopen(temp_fd, 'w', encoding=encoding) as f:
            f.write(content)
        
        # Windows can transiently lock target files (indexer/AV/readers).
        # Retry a few times with small backoff before failing hard.
        last_exc = None
        for attempt in range(6):
            try:
                # os.replace is atomic on both Unix and Windows.
                os.replace(
                    native_filesystem_path(temp_name),
                    native_filesystem_path(path),
                )
                last_exc = None
                break
            except PermissionError as exc:
                last_exc = exc
                if attempt == 5:
                    break
                time.sleep(0.05 * (2 ** attempt))
        if last_exc is not None:
            raise last_exc
    except Exception as write_error:
        native_temp = native_filesystem_path(temp_name)
        if os.path.exists(native_temp):
            try:
                os.remove(native_temp)
            except OSError as cleanup_error:
                write_error.add_note(f"Temporary-file cleanup also failed: {cleanup_error}")
                raise write_error
        raise


def save_json_atomic(path: Path, data: any, indent: int = 2, bypass_proxy: bool = False):
    """Serialize data to JSON atomically using a temporary file."""
    path = Path(path)
    managed_raw = False
    try:
        raw_dir = globals().get("RAW_DIR")
        managed_raw = (
            raw_dir is not None
            and path.resolve().parent == Path(raw_dir).resolve()
            and path.suffix.lower() == ".json"
        )
    except Exception:
        managed_raw = False
    if not bypass_proxy and managed_raw:
        from tools.core import artifact_store as artifact_store_module

        # Never let a re-bound output root use a store singleton that still
        # points at another runtime namespace.
        if (
            Path(artifact_store_module.RAW_DIR).resolve()
            != Path(raw_dir).resolve()
        ):
            raise RuntimeError(
                "Managed raw artifact root does not match the active ArtifactStore binding. "
                "Rebind the store or use bypass_proxy=True for an explicitly isolated fixture."
            )
        try:
            return artifact_store_module.STORE.save_raw(path.stem, data, indent=indent)
        except artifact_store_module.UnsafeScopedAtlasProjectionError:
            raise
        except artifact_store_module.ArtifactPrimaryWriteError:
            raise
        except Exception as e:
            # Fall back to native atomic write if proxy fails, but keep the degradation observable.
            try:
                from tools.core.honesty_telemetry import record_honesty_event

                record_honesty_event(
                    component="config.save_json_atomic",
                    category="storage_fallback",
                    operation="save_json_atomic_proxy",
                    subject=str(path),
                    reason="managed raw artifact SQLite proxy write failed",
                    fallback="direct_json_atomic_write",
                    claim_impact="artifact_freshness_requires_validation",
                    exception=e,
                )
            except Exception:
                print("Warning: Failed to record SQLite proxy write fallback telemetry")
            print(f"Warning: SQLite proxy write failed, falling back to JSON: {e}")
            
    native_parent = native_filesystem_path(path.parent)
    Path(native_parent).mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(dir=native_parent, prefix="cm_tmp_", suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=indent, ensure_ascii=False)

        last_exc = None
        for attempt in range(6):
            try:
                os.replace(
                    native_filesystem_path(temp_name),
                    native_filesystem_path(path),
                )
                last_exc = None
                break
            except PermissionError as exc:
                last_exc = exc
                if attempt == 5:
                    break
                time.sleep(0.05 * (2 ** attempt))
        if last_exc is not None:
            raise last_exc
    except Exception as write_error:
        native_temp = native_filesystem_path(temp_name)
        if os.path.exists(native_temp):
            try:
                os.remove(native_temp)
            except OSError as cleanup_error:
                write_error.add_note(f"Temporary-file cleanup also failed: {cleanup_error}")
                raise write_error
        raise


def _runtime_config_needs_compile() -> bool:
    from tools.core.runtime_config_identity import runtime_config_needs_compile

    return runtime_config_needs_compile(DISCOVERY_FILE, OVERRIDES_FILE, CONFIG_FILE)


def _compile_runtime_config_file() -> bool:
    try:
        from tools.config_compiler import compile_runtime_config

        compiled = compile_runtime_config()
        save_json_atomic(CONFIG_FILE, compiled, indent=4)
        return True
    except Exception as exc:
        print(f"Warning: Failed to auto-compile runtime config: {exc}")
        return False


def _compile_doctrine_file_if_needed() -> bool:
    try:
        if not DOCTRINE_MANIFEST_FILE.exists():
            return False
        from tools.doctrine_compiler import manifest_newer_than_output, write_compiled_doctrine

        if manifest_newer_than_output(DOCTRINE_MANIFEST_FILE, DOCTRINE_FILE):
            write_compiled_doctrine(DOCTRINE_FILE, DOCTRINE_MANIFEST_FILE)
            return True
    except Exception as exc:
        raise RuntimeError(f"Failed to auto-compile required doctrine registry: {exc}") from exc
    return False

def load_runtime_config(auto_compile: bool = True):
    """Load compiled runtime config, auto-compiling it from discovery/overrides when needed."""
    if auto_compile and _runtime_config_needs_compile() and not _compile_runtime_config_file():
        raise RuntimeError("Required runtime config compilation failed; stale runtime truth was not loaded.")

    config = _load_json(CONFIG_FILE, repair=True)
    discovery = _load_json(DISCOVERY_FILE, repair=True)
    
    if not config:
        config = {"variations": {}, "plugins": [], "architecture": {}, "environment": {"path_aliases": {}}}
    
    # Fallback only when no compiled runtime config is available.
    if not config.get("variations") and discovery.get("variations"):
        config["variations"] = discovery["variations"]
        
    return config


def _load_package_manifest(root: Path) -> dict:
    package_path = root / "package.json"
    if not is_target_path_contained(root, package_path) or not package_path.exists():
        return {}
    try:
        return json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _target_dependency_names(root: Path) -> set[str]:
    manifest = _load_package_manifest(root)
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = manifest.get(key) if isinstance(manifest, dict) else None
        if isinstance(section, dict):
            deps.update(str(name) for name in section.keys())
    return deps


def _target_has_any(root: Path, names: tuple[str, ...]) -> bool:
    return any(
        is_target_path_contained(root, root / name) and (root / name).exists()
        for name in names
    )


def _target_has_source_ext(root: Path, extensions: tuple[str, ...], limit: int = 1200) -> bool:
    seen = 0
    skip = registry_skip_dirs()
    excluded_roots = runtime_installation_excluded_roots(root, CODE_MAPS_DIR)
    try:
        for current, dirs, files in os.walk(root):
            prune_walk_directories(
                current,
                dirs,
                skipped_names=skip,
                excluded_roots=excluded_roots,
            )
            for file_name in files:
                seen += 1
                if Path(file_name).suffix.lower() in extensions:
                    return True
                if seen >= limit:
                    return False
    except OSError:
        return False
    return False


def _infer_target_bundler(root: Path, deps: set[str]) -> str:
    markers = config_file_marker_map()
    for bundler in ("nextjs", "vite", "expo", "webpack"):
        if bundler.replace("nextjs", "next") in deps or _target_has_any(root, tuple(markers.get(bundler, []))):
            return bundler
    return "unknown"


def _infer_target_path_aliases(root: Path) -> dict:
    for name in ("tsconfig.json", "jsconfig.json"):
        config_path = root / name
        if not is_target_path_contained(root, config_path) or not config_path.exists():
            continue
        try:
            raw = config_path.read_text(encoding="utf-8")
            data = loads_jsonc(raw)
            paths = ((data.get("compilerOptions") or {}).get("paths") or {})
            if isinstance(paths, dict):
                return {
                    str(alias): [str(target) for target in targets]
                    for alias, targets in paths.items()
                    if isinstance(targets, list)
                }
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    if (root / "src").exists():
        return {"@/*": ["./src/*"]}
    return {}


def _observe_target_path_aliases(root: Path) -> dict:
    """Observe workspace alias keys without flattening scoped resolver targets."""
    marker_map = config_file_marker_map()
    patterns = tuple(
        str(pattern).lower()
        for pattern in marker_map.get("javascript_typescript", [])
        if str(pattern).strip()
    )
    if not patterns:
        return {"aliases": [], "evidence_files": [], "scoped_alias_maps": []}

    aliases: set[str] = set()
    evidence_files: list[str] = []
    scoped_alias_maps: list[dict] = []
    skip = registry_skip_dirs()
    excluded_roots = runtime_installation_excluded_roots(root, CODE_MAPS_DIR)
    try:
        for current, dirs, files in os.walk(root):
            prune_walk_directories(
                current,
                dirs,
                skipped_names=skip,
                excluded_roots=excluded_roots,
            )
            for file_name in files:
                if not any(fnmatchcase(file_name.lower(), pattern) for pattern in patterns):
                    continue
                config_path = Path(current) / file_name
                try:
                    raw = config_path.read_text(encoding="utf-8")
                    data = loads_jsonc(raw)
                    paths = ((data.get("compilerOptions") or {}).get("paths") or {})
                    if not isinstance(paths, dict) or not paths:
                        continue
                    aliases.update(str(alias) for alias in paths if str(alias).strip())
                    relative_config = config_path.relative_to(root).as_posix()
                    evidence_files.append(relative_config)
                    scoped_alias_maps.append({
                        "config_file": relative_config,
                        "scope_root": config_path.parent.relative_to(root).as_posix() or ".",
                        "base_url": str((data.get("compilerOptions") or {}).get("baseUrl") or "."),
                        "path_aliases": {
                            str(alias): [str(target) for target in targets]
                            for alias, targets in paths.items()
                            if str(alias).strip() and isinstance(targets, list)
                        },
                    })
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return {
        "aliases": sorted(aliases),
        "evidence_files": sorted(set(evidence_files)),
        "scoped_alias_maps": sorted(scoped_alias_maps, key=lambda item: item["config_file"]),
    }


def _observe_target_path_aliases_from_files(root: Path, relative_files: list[str]) -> dict:
    """Read alias maps only from config files already found by bounded inventory."""
    marker_map = config_file_marker_map()
    patterns = tuple(
        str(pattern).lower()
        for pattern in marker_map.get("javascript_typescript", [])
        if str(pattern).strip()
    )
    aliases: set[str] = set()
    evidence_files: list[str] = []
    scoped_alias_maps: list[dict] = []
    resolved_root = root.resolve()
    for relative_file in sorted(set(str(value).replace("\\", "/") for value in relative_files)):
        config_path = (resolved_root / relative_file).resolve()
        try:
            config_path.relative_to(resolved_root)
        except ValueError:
            continue
        if not config_path.is_file() or not any(
            fnmatchcase(config_path.name.lower(), pattern) for pattern in patterns
        ):
            continue
        try:
            data = loads_jsonc(config_path.read_text(encoding="utf-8"))
            compiler_options = data.get("compilerOptions") or {}
            paths = compiler_options.get("paths") or {}
            if not isinstance(paths, dict) or not paths:
                continue
            normalized_paths = {
                str(alias): [str(target) for target in targets]
                for alias, targets in paths.items()
                if str(alias).strip() and isinstance(targets, list)
            }
            if not normalized_paths:
                continue
            aliases.update(normalized_paths)
            normalized_relative = config_path.relative_to(resolved_root).as_posix()
            evidence_files.append(normalized_relative)
            scoped_alias_maps.append(
                {
                    "config_file": normalized_relative,
                    "scope_root": config_path.parent.relative_to(resolved_root).as_posix() or ".",
                    "base_url": str(compiler_options.get("baseUrl") or "."),
                    "path_aliases": normalized_paths,
                }
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
    return {
        "aliases": sorted(aliases),
        "evidence_files": sorted(set(evidence_files)),
        "scoped_alias_maps": sorted(scoped_alias_maps, key=lambda item: item["config_file"]),
    }


def _infer_target_plugins(root: Path, deps: set[str], bundler: str) -> list[str]:
    plugins: set[str] = set()
    if "typescript" in deps or _target_has_any(root, ("tsconfig.json",)) or _target_has_source_ext(root, (".ts", ".tsx")):
        plugins.add("typescript")
    if {"react", "react-dom"} & deps:
        plugins.update({"react", "ui_react"})
    plugins.update(plugins_for_bundler(bundler))
    plugins.update(plugins_for_dependencies(deps))
    if _target_has_any(root, ("tailwind.config.js", "tailwind.config.ts", "postcss.config.js", "postcss.config.mjs")):
        plugins.add("styling")
    return sorted(plugins)


def _infer_target_plugins_from_inventory(
    deps: set[str],
    bundler: str,
    *,
    language_counts: dict | None = None,
    config_files: list[str] | None = None,
) -> list[str]:
    """Infer plugins from an existing inventory without another source-tree walk."""
    languages = {
        str(name).strip().lower()
        for name, count in (language_counts or {}).items()
        if int(count or 0) > 0
    }
    config_names = {Path(str(path)).name.lower() for path in (config_files or [])}
    plugins: set[str] = set()
    if "typescript" in deps or "typescript" in languages or "tsconfig.json" in config_names:
        plugins.add("typescript")
    if {"react", "react-dom"} & deps:
        plugins.update({"react", "ui_react"})
    plugins.update(plugins_for_bundler(bundler))
    plugins.update(plugins_for_dependencies(deps))
    if config_names & {
        "tailwind.config.js",
        "tailwind.config.ts",
        "postcss.config.js",
        "postcss.config.mjs",
    }:
        plugins.add("styling")
    return sorted(plugins)


def _infer_target_architecture(root: Path, deps: set[str], bundler: str) -> dict:
    def has_dir(*parts: str) -> bool:
        return (root.joinpath(*parts)).is_dir()

    src_root = root / "src" if (root / "src").exists() else root
    fsd_markers = {"app", "pages", "widgets", "features", "entities", "shared"}
    found_fsd = {name for name in fsd_markers if (src_root / name).is_dir()}
    clean_markers = {"domain", "application", "infra", "adapters", "ports"}
    found_clean = {name for name in clean_markers if (src_root / name).is_dir()}
    has_workspace_dirs = any((root / name).is_dir() for name in ("apps", "packages", "libs", "services"))
    package_manifest = _load_package_manifest(root)
    declared_workspaces = package_manifest.get("workspaces") if isinstance(package_manifest, dict) else None
    has_declared_workspaces = (
        isinstance(declared_workspaces, list)
        and any(str(value).strip() for value in declared_workspaces)
    ) or (
        isinstance(declared_workspaces, dict)
        and bool(declared_workspaces)
    )

    if bundler == "nextjs" and (has_dir("app") or has_dir("src", "app")):
        detected_profile = "NEXTJS_APP_ROUTER"
    elif has_declared_workspaces or "turbo" in deps or _target_has_any(root, ("turbo.json", "nx.json", "lerna.json")) or (
        _target_has_any(root, ("pnpm-workspace.yaml",)) and has_workspace_dirs
    ):
        detected_profile = "MONOREPO_TURBOREPO"
    elif len(found_fsd) >= 4:
        detected_profile = "FSD_STRICT"
    elif len(found_clean) >= 3:
        detected_profile = "CLEAN_ARCHITECTURE"
    elif (src_root / "components").is_dir() or (src_root / "hooks").is_dir():
        detected_profile = "MODULAR_FLAT"
    else:
        detected_profile = "MINIMAL"

    module_root = "src" if (root / "src").exists() and detected_profile not in {"NEXTJS_APP_ROUTER", "MONOREPO_TURBOREPO"} else "."
    return {
        "type": detected_profile.lower(),
        "detected_profile": detected_profile,
        "module_root": module_root,
    }


def external_target_scope_projection(root: Path, architecture: dict | None = None) -> dict:
    """Resolve the bounded project scope inside an external repository root."""
    root = Path(root).resolve()
    raw_scope = str((architecture or {}).get("module_root") or ".").replace("\\", "/").strip()
    normalized_scope = raw_scope.strip("/") or "."
    candidate = (root / normalized_scope).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return {
            "status": "invalid",
            "repository_root": str(root),
            "project_root": str(candidate),
            "project_relative_path": normalized_scope,
            "resolution_basis": "unsafe_inferred_scope_outside_repository",
        }
    if not candidate.is_dir():
        return {
            "status": "invalid",
            "repository_root": str(root),
            "project_root": str(candidate),
            "project_relative_path": normalized_scope,
            "resolution_basis": "missing_inferred_scope",
        }
    return {
        "status": "resolved",
        "repository_root": str(root),
        "project_root": str(candidate),
        "project_relative_path": normalized_scope,
        "resolution_basis": (
            "architecture_module_root"
            if normalized_scope != "."
            else "repository_root_project"
        ),
    }


def external_target_project_relative_path(
    repository_relative_path: str | Path,
    scope_projection: dict,
) -> str | None:
    """Translate a repository-relative path into the bounded project namespace."""
    if scope_projection.get("status") != "resolved":
        return None
    repository_path = Path(str(repository_relative_path).replace("\\", "/"))
    if (
        repository_path == Path(".")
        or repository_path.is_absolute()
        or ".." in repository_path.parts
    ):
        return None
    scope_path = Path(
        str(scope_projection.get("project_relative_path") or ".").replace("\\", "/")
    )
    if scope_path.is_absolute() or ".." in scope_path.parts:
        return None
    if scope_path == Path("."):
        return repository_path.as_posix()
    try:
        return repository_path.relative_to(scope_path).as_posix()
    except ValueError:
        return None


def external_target_repository_topology(
    target_path: Path,
    scope_projection: dict,
    *,
    requested_mode: str | None = None,
    path_boundary_state: dict | None = None,
) -> dict:
    """Normalize an external acquisition into the canonical repository ontology."""

    registry = load_language_registry()
    structural_markers = {
        str(value).lower()
        for value in registry.get("root_level_app_markers", [])
        if str(value).strip()
    }
    profiles = _load_json(PROFILES_FILE)
    for profile in (profiles.get("profiles", {}) if isinstance(profiles, dict) else {}).values():
        if not isinstance(profile, dict):
            continue
        structural_markers.update(
            str(value).lower()
            for value in profile.get("markers", [])
            if str(value).strip()
        )
    doctrine = _load_json(DOCTRINE_FILE)
    role_markers = (
        doctrine.get("discovery_project_role_markers", {})
        if isinstance(doctrine, dict)
        else {}
    )
    topology_mode = str(
        requested_mode
        or os.environ.get("CODEMAPS_TOPOLOGY_MODE")
        or "auto"
    ).strip()
    topology = resolve_repository_topology(
        target_path,
        main_project_path=str(scope_projection.get("project_relative_path") or "."),
        structural_markers=structural_markers,
        role_markers=role_markers,
        requested_mode=topology_mode,
        excluded_paths=runtime_installation_excluded_roots(target_path, CODE_MAPS_DIR),
        excluded_path_predicate=is_managed_clean_mirror_path,
        config_or_manifest_predicate=is_config_or_manifest_file,
        skipped_names=registry_skip_dirs(),
        path_boundary_state=path_boundary_state,
    )
    return {
        **topology,
        "source_mode": "external_target",
        "repository_root": str(target_path.resolve()),
    }


def _requested_target_projects_from_environment(
    source_env: dict[str, str] | None = None,
) -> list[str]:
    env = source_env if isinstance(source_env, dict) else os.environ
    return sorted(
        {
            value.strip().upper()
            for value in str(env.get("CODEMAPS_TARGET_PROJECTS") or "").split(",")
            if value.strip()
        }
    )


def load_external_target_preflight_receipt(
    target_path: Path,
    *,
    source_env: dict[str, str] | None = None,
    require_freshness: bool = False,
) -> dict | None:
    """Load only the exact receipt explicitly transported by the launching command."""
    env = source_env if isinstance(source_env, dict) else os.environ
    receipt_value = str(env.get("CODEMAPS_TARGET_PREFLIGHT_RECEIPT") or "").strip()
    if not receipt_value:
        return None
    receipt_path = Path(receipt_value).expanduser().resolve()
    native_receipt_path = Path(native_filesystem_path(receipt_path))
    expected_hash = str(
        env.get("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256") or ""
    ).strip().lower()
    if not expected_hash:
        raise RuntimeError("External target Preflight receipt hash is missing")
    try:
        receipt_bytes = native_receipt_path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"External target Preflight receipt is unreadable: {receipt_path}") from exc
    actual_hash = hashlib.sha256(receipt_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise RuntimeError("External target Preflight receipt content identity mismatch")
    try:
        receipt = json.loads(receipt_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("External target Preflight receipt is invalid JSON") from exc
    if not isinstance(receipt, dict) or receipt.get("meta", {}).get("kind") != "external_target_preflight":
        raise RuntimeError("External target Preflight receipt kind is invalid")
    run_id = str(receipt.get("meta", {}).get("run_id") or "").strip()
    if not run_id or receipt_path.name != f"{run_id}.json" or receipt_path.parent.name != "runs":
        raise RuntimeError("External target Preflight receipt is not an immutable run receipt")
    receipt_target_value = str(receipt.get("target", {}).get("root") or "").strip()
    if not receipt_target_value:
        raise RuntimeError("External target Preflight receipt target identity is missing")
    receipt_target = Path(receipt_target_value).expanduser().resolve()
    if receipt_target != target_path.resolve():
        raise RuntimeError("External target Preflight receipt target identity mismatch")
    summary = receipt.get("summary") if isinstance(receipt.get("summary"), dict) else {}
    scope = summary.get("analysis_scope") if isinstance(summary.get("analysis_scope"), dict) else {}
    receipt_projects = sorted(
        {
            str(value).strip().upper()
            for value in scope.get("requested_project_filter", [])
            if str(value).strip()
        }
    )
    if receipt_projects != _requested_target_projects_from_environment(env):
        raise RuntimeError("External target Preflight receipt project-filter identity mismatch")
    if str(summary.get("status") or "").upper() not in {"PASS", "ATTENTION"}:
        raise RuntimeError("External target Preflight receipt is not authorized for runtime use")
    runtime_observation = summary.get("runtime_observation")
    if not isinstance(runtime_observation, dict):
        raise RuntimeError("External target Preflight receipt lacks runtime observation")
    required_scope_fields = {
        "topology_mode",
        "discovered_topology",
        "analysis_projection",
        "selection_mode",
        "selected_projects",
        "selected_project_roles",
        "project_candidates",
        "project_candidate_roles",
        "project_candidate_relationship_roles",
        "project_candidate_role_authority",
        "project_candidate_system_kinds",
        "project_candidate_selection_evidence",
        "relationship_operation_projects",
        "coverage_only_projects",
        "excluded_projects",
        "excluded_project_reasons",
        "project_ownership_exclusions",
        "file_ownership_contract",
        "relationship_role_contract",
        "system_kind_contract",
        "analysis_coverage_contract",
        "comparative_analysis_enabled",
        "ontology_contract",
        "topology_authority_id",
        "scope_authority",
    }
    if not required_scope_fields.issubset(scope):
        raise RuntimeError("External target Preflight receipt scope projection is incomplete")
    if not isinstance(summary.get("target_policy"), dict):
        raise RuntimeError("External target Preflight receipt target policy is missing")
    required_runtime_fields = {
        "bundler": str,
        "plugins": list,
        "architecture": dict,
        "path_aliases": dict,
        "observed_path_aliases": dict,
    }
    invalid_runtime_fields = sorted(
        field
        for field, expected_type in required_runtime_fields.items()
        if not isinstance(runtime_observation.get(field), expected_type)
    )
    if invalid_runtime_fields:
        raise RuntimeError(
            "External target Preflight runtime observation is incomplete: "
            + ", ".join(invalid_runtime_fields)
        )
    observed_aliases = runtime_observation["observed_path_aliases"]
    if not all(
        isinstance(observed_aliases.get(field), list)
        for field in ("aliases", "evidence_files", "scoped_alias_maps")
    ):
        raise RuntimeError("External target Preflight alias observation is incomplete")
    if require_freshness:
        recorded_identity = summary.get("target_observation_identity")
        if not isinstance(recorded_identity, dict) or recorded_identity.get("status") != "complete":
            raise RuntimeError("External target Preflight receipt lacks a complete target observation identity")
        try:
            policy = json.loads(
                (CONFIG_DIR / "external_target_preflight_policy.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("External target Preflight freshness policy is unavailable") from exc
        if not isinstance(policy, dict):
            raise RuntimeError("External target Preflight freshness policy is invalid")
        from tools.core.target_inventory import target_observation_identity

        config_patterns = sorted(
            {
                marker
                for markers in config_file_marker_map().values()
                for marker in markers
            }
        )
        current_identity = target_observation_identity(
            target_path,
            policy,
            config_patterns,
            manifest_file_marker_map(),
            excluded_roots=runtime_installation_excluded_roots(target_path, CODE_MAPS_DIR),
        )
        if current_identity.get("status") != "complete":
            raise RuntimeError("External target Preflight current target observation is incomplete")
        if (
            current_identity.get("algorithm") != recorded_identity.get("algorithm")
            or current_identity.get("fingerprint") != recorded_identity.get("fingerprint")
        ):
            raise RuntimeError("External target Preflight target observation identity is stale")
    return receipt


def _load_external_target_preflight_receipt(target_path: Path) -> dict | None:
    """Backward-compatible internal entrypoint for ordinary same-process receipt use."""

    return load_external_target_preflight_receipt(target_path)


def _fallback_target_policy(target_path: Path, topology: dict) -> dict:
    """Keep explicit preflight bypass honest without inheriting another target's policy."""
    from tools.core.target_policy_profile import (
        aggregate_effective_target_policy,
        inventory_project_target_policy,
    )

    project_policies: dict[str, dict] = {}
    for project, relative_path in sorted(topology["selected_projects"].items()):
        project_root = target_path if str(relative_path) == "." else target_path / str(relative_path)
        package_path = project_root / "package.json"
        project_policies[str(project)] = inventory_project_target_policy(
            project_root,
            project=str(project),
            package_json=_load_package_manifest(project_root),
            package_path=package_path if package_path.is_file() else None,
            workspace_root=target_path,
        )
    return aggregate_effective_target_policy(
        project_policies,
        selected_project_count=len(topology["selected_projects"]),
    )


def _apply_target_root_override(config: dict) -> dict:
    """Apply a per-process external target root without rewriting compiled config."""
    target_root = os.environ.get("CODEMAPS_TARGET_ROOT")
    if not target_root:
        return config

    target_path = Path(target_root).expanduser()
    if not target_path.is_absolute():
        target_path = (Path.cwd() / target_path).resolve()
    else:
        target_path = target_path.resolve()

    preflight_receipt = _load_external_target_preflight_receipt(target_path)
    if preflight_receipt is not None:
        summary = preflight_receipt["summary"]
        runtime_observation = summary["runtime_observation"]
        scope_projection = summary["analysis_scope"]
        topology = {
            key: scope_projection[key]
            for key in (
                "topology_mode",
                "discovered_topology",
                "analysis_projection",
                "selection_mode",
                "project_candidates",
                "project_candidate_roles",
                "project_candidate_relationship_roles",
                "project_candidate_role_authority",
                "project_candidate_system_kinds",
                "project_candidate_selection_evidence",
                "selected_projects",
                "selected_project_roles",
                "relationship_operation_projects",
                "coverage_only_projects",
                "excluded_projects",
                "excluded_project_reasons",
                "project_ownership_exclusions",
                "file_ownership_contract",
                "relationship_role_contract",
                "system_kind_contract",
                "analysis_coverage_contract",
                "comparative_analysis_enabled",
                "ontology_contract",
                "topology_authority_id",
            )
        }
        topology["requested_mode"] = topology.pop("topology_mode")
        bundler = str(runtime_observation.get("bundler") or "unknown")
        path_aliases = dict(runtime_observation.get("path_aliases") or {})
        observed_path_aliases = dict(runtime_observation.get("observed_path_aliases") or {})
        plugins = list(runtime_observation.get("plugins") or [])
        architecture = dict(runtime_observation.get("architecture") or {})
        effective_target_policy = summary["target_policy"]
        observation_source = "external_target_preflight_receipt"
    else:
        deps = _target_dependency_names(target_path)
        bundler = _infer_target_bundler(target_path, deps)
        path_aliases = _infer_target_path_aliases(target_path)
        observed_path_aliases = _observe_target_path_aliases(target_path)
        plugins = _infer_target_plugins(target_path, deps, bundler)
        architecture = _infer_target_architecture(target_path, deps, bundler)
        scope_projection = external_target_scope_projection(target_path, architecture)
        if scope_projection.get("status") != "resolved":
            raise RuntimeError(
                "External target project scope is invalid: "
                f"{scope_projection.get('resolution_basis')}"
            )
        topology = external_target_repository_topology(target_path, scope_projection)
        effective_target_policy = _fallback_target_policy(target_path, topology)
        observation_source = "runtime_recomputed_fallback"

    overridden = dict(config)
    overridden["workspace_root"] = str(target_path)
    overridden["variations"] = topology["selected_projects"]
    overridden["project_roles"] = topology["selected_project_roles"]
    overridden["project_display_names"] = {
        key: (
            (target_path.name or "EXTERNAL_TARGET")
            if key == "MAIN"
            else Path(relative_path).name
        )
        for key, relative_path in topology["selected_projects"].items()
    }
    overridden["plugins"] = plugins
    overridden["architecture"] = architecture
    overridden["effective_target_policy"] = effective_target_policy
    inherited_audit = config.get("audit", {}) if isinstance(config.get("audit"), dict) else {}
    overridden["audit"] = {
        **inherited_audit,
        "rules": {},
    }
    overridden["environment"] = {
        **(config.get("environment", {}) if isinstance(config.get("environment"), dict) else {}),
        "bundler": bundler,
        "path_aliases": path_aliases,
        "scoped_path_aliases": observed_path_aliases["scoped_alias_maps"],
    }
    overridden["_target_root_override"] = {
        "enabled": True,
        "source": "CODEMAPS_TARGET_ROOT",
        "target_root": str(target_path),
        "mode": "external_repository",
        "source_mode": "external_target",
        "observation_source": observation_source,
        "preflight_run_id": (
            preflight_receipt.get("meta", {}).get("run_id")
            if preflight_receipt is not None
            else None
        ),
        "preflight_receipt_sha256": (
            os.environ.get("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256")
            if preflight_receipt is not None
            else None
        ),
        "topology_mode": topology["requested_mode"],
        "discovered_topology": topology["discovered_topology"],
        "analysis_projection": topology["analysis_projection"],
        "selection_mode": topology["selection_mode"],
        "project_candidates": topology["project_candidates"],
        "project_candidate_roles": topology["project_candidate_roles"],
        "project_candidate_relationship_roles": topology[
            "project_candidate_relationship_roles"
        ],
        "project_candidate_role_authority": topology["project_candidate_role_authority"],
        "project_candidate_system_kinds": topology["project_candidate_system_kinds"],
        "project_candidate_selection_evidence": topology["project_candidate_selection_evidence"],
        "selected_projects": topology["selected_projects"],
        "relationship_operation_projects": topology["relationship_operation_projects"],
        "coverage_only_projects": topology["coverage_only_projects"],
        "excluded_projects": topology["excluded_projects"],
        "excluded_project_reasons": topology["excluded_project_reasons"],
        "project_ownership_exclusions": topology["project_ownership_exclusions"],
        "file_ownership_contract": topology["file_ownership_contract"],
        "relationship_role_contract": topology["relationship_role_contract"],
        "system_kind_contract": topology["system_kind_contract"],
        "analysis_coverage_contract": topology["analysis_coverage_contract"],
        "comparative_analysis_enabled": topology["comparative_analysis_enabled"],
        "ontology_contract": topology["ontology_contract"],
        "topology_authority_id": topology["topology_authority_id"],
        "inferred_bundler": bundler,
        "inferred_plugins": plugins,
        "inferred_architecture": architecture,
        "analysis_scope": {
            **scope_projection,
            "analysis_projection": topology["analysis_projection"],
            "selection_mode": topology["selection_mode"],
            "topology_authority_id": topology["topology_authority_id"],
            "selected_projects": topology["selected_projects"],
            "relationship_operation_projects": topology["relationship_operation_projects"],
            "coverage_only_projects": topology["coverage_only_projects"],
            "excluded_projects": topology["excluded_projects"],
            "excluded_project_reasons": topology["excluded_project_reasons"],
            "project_ownership_exclusions": topology["project_ownership_exclusions"],
            "file_ownership_contract": topology["file_ownership_contract"],
        },
        "path_aliases": sorted(path_aliases.keys()),
        "observed_path_aliases": observed_path_aliases["aliases"],
        "path_alias_evidence_files": observed_path_aliases["evidence_files"],
        "audit_rule_source": "canonical_doctrine_plus_external_target_evidence",
    }
    return overridden


_target_output_slug = external_target_output_slug


def _resolve_source_extensions(dynamic_config: dict) -> set[str]:
    """Use explicit runtime scope when present, otherwise the language registry."""
    configured = dynamic_config.get("source_extensions")
    if configured is None:
        return set(language_extensions())
    return {
        str(extension).lower()
        for extension in configured
        if str(extension).startswith(".")
    }


# --- Runtime Globals ---
# Keep import-time initialization read-only. Callers that need self-healing
# should invoke load_runtime_config(auto_compile=True) explicitly.
DYNAMIC_CONFIG = _apply_target_root_override(load_runtime_config(auto_compile=False))
_compile_doctrine_file_if_needed()
DOCTRINE = _load_json(DOCTRINE_FILE, repair=True)

workspace_root_rel = DYNAMIC_CONFIG.get("workspace_root", "..")
ROOT = (CODE_MAPS_DIR / workspace_root_rel).resolve().absolute()


def _resolve_variation_root(variation_key: str) -> Path:
    rel_path = normalize_path((DYNAMIC_CONFIG.get("variations", {}) or {}).get(variation_key))
    if not rel_path or rel_path == ".":
        return ROOT
    return (ROOT / rel_path).resolve().absolute()


MAIN_PROJECT_ROOT = _resolve_variation_root("MAIN")


def _default_source_root() -> Path:
    module_root = normalize_path(((DYNAMIC_CONFIG.get("architecture", {}) or {})).get("module_root"))
    if not module_root or module_root == ".":
        return MAIN_PROJECT_ROOT
    return (MAIN_PROJECT_ROOT / module_root).resolve().absolute()


SRC = _default_source_root()
DEFAULT_WATCH_PATH = SRC if SRC.exists() else MAIN_PROJECT_ROOT

_TARGET_OUTPUT_ROOT = os.environ.get("CODEMAPS_TARGET_ROOT")
_TARGET_RUN_ID = os.environ.get("CODEMAPS_EXTERNAL_RUN_ID", "").strip()
_TARGET_BASE_DIR = (
    CODE_MAPS_DIR / "output" / "external_targets" / _target_output_slug(_TARGET_OUTPUT_ROOT)
    if _TARGET_OUTPUT_ROOT else None
)
if (
    _TARGET_BASE_DIR is not None
    and not _TARGET_RUN_ID
    and Path(native_filesystem_path(_TARGET_BASE_DIR / "current.json")).is_file()
):
    from tools.core.external_target_generation import resolve_current_external_target_generation

    _current_generation, _current_pointer, _current_reason = resolve_current_external_target_generation(
        _TARGET_BASE_DIR
    )
    _TARGET_RUN_ID = (
        str(_current_pointer.get("run_id") or "")
        if _current_generation is not None
        else ".invalid-current"
    )
OUTPUT_DIR = (
    _TARGET_BASE_DIR / "generations" / _TARGET_RUN_ID
    if _TARGET_BASE_DIR is not None and _TARGET_RUN_ID
    else _TARGET_BASE_DIR if _TARGET_BASE_DIR is not None
    else CODE_MAPS_DIR / "output"
)
RAW_DIR = OUTPUT_DIR / ".raw"
SANCTUARY_DIR = OUTPUT_DIR / ".sanctuary"
REPORTS_DIR = OUTPUT_DIR / "reports"
SCRIPTS_DIR = OUTPUT_DIR / "scripts"
LOGS_DIR = OUTPUT_DIR / "logs"
SNAPSHOTS_DIR = OUTPUT_DIR / ".snapshots"
SNAPSHOT_RETENTION = 50

SKIP_DIRS = set(DYNAMIC_CONFIG.get("skip_dirs", [
    "node_modules", ".git", "dist", "build", ".next",
    "coverage", "__pycache__", "venv", ".idea", ".vscode"
]))

SOURCE_EXTENSIONS = _resolve_source_extensions(DYNAMIC_CONFIG)
ARCH_CONFIG = DYNAMIC_CONFIG.get("architecture", {})
PLUGINS = set(DYNAMIC_CONFIG.get("plugins", []))
ENVIRONMENT = DYNAMIC_CONFIG.get("environment", {})

PRIMARY_ALIAS = "@/"
if ENVIRONMENT.get("path_aliases"):
    sorted_aliases = sorted(ENVIRONMENT["path_aliases"].keys(), key=lambda x: len(x))
    PRIMARY_ALIAS = sorted_aliases[0].replace("*", "") if sorted_aliases else "@/"

PROJECT_FILTER = None

def ensure_output_dir():
    for directory in (OUTPUT_DIR, RAW_DIR, SANCTUARY_DIR, REPORTS_DIR, SCRIPTS_DIR, LOGS_DIR, SNAPSHOTS_DIR):
        Path(native_filesystem_path(directory)).mkdir(parents=True, exist_ok=True)
    _migrate_legacy_output_layout()


def _migrate_legacy_output_layout():
    legacy_master_report = OUTPUT_DIR / "MASTER_ARCHITECTURE_REPORT.md"
    current_master_report = REPORTS_DIR / "MASTER_ARCHITECTURE_REPORT.md"
    if legacy_master_report.exists() and (
        not current_master_report.exists() or current_master_report.stat().st_size == 0
    ):
        shutil.move(str(legacy_master_report), str(current_master_report))

    legacy_pipeline_log = OUTPUT_DIR / "pipeline.log"
    current_pipeline_log = LOGS_DIR / "pipeline.log"
    if legacy_pipeline_log.exists() and (
        not current_pipeline_log.exists() or current_pipeline_log.stat().st_size == 0
    ):
        shutil.move(str(legacy_pipeline_log), str(current_pipeline_log))
    elif legacy_pipeline_log.exists():
        archived_legacy_pipeline_log = LOGS_DIR / "pipeline.legacy.log"
        if not archived_legacy_pipeline_log.exists():
            shutil.move(str(legacy_pipeline_log), str(archived_legacy_pipeline_log))

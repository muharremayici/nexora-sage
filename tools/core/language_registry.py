from __future__ import annotations

from copy import deepcopy
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_content_cached_with_identity


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
REGISTRY_FILE = CODE_MAPS_DIR / "config" / "language_registry.json"


_FALLBACK_REGISTRY: dict[str, Any] = {
    "languages": {
        "typescript": {"extensions": [".ts", ".tsx", ".mts", ".cts"], "index_files": ["index.ts", "index.tsx", "index.mts", "index.cts"], "structure_extensions": [".css", ".html"]},
        "javascript": {"extensions": [".js", ".jsx", ".mjs", ".cjs"], "index_files": ["index.js", "index.jsx", "index.mjs", "index.cjs"], "structure_extensions": [".css", ".html"]},
        "python": {"extensions": [".py", ".pyi"], "index_files": ["__init__.py"]},
        "java": {"extensions": [".java"], "index_files": []},
        "csharp": {"extensions": [".cs"], "index_files": []},
        "go": {"extensions": [".go"], "index_files": ["index.go", "main.go"]},
    },
    "observation_only_languages": {
        "vue": {"extensions": [".vue"], "reason": "Vue source remains visible while Vue SFC parsing is outside the active capability contract."},
        "svelte": {"extensions": [".svelte"], "reason": "Svelte source remains visible while Svelte component parsing is outside the active capability contract."},
        "rust": {"extensions": [".rs"], "reason": "Rust source remains visible while Rust AST and Cargo semantics are outside the active capability contract."},
    },
    "non_source_template_extensions": [".ejs", ".hbs", ".handlebars", ".mustache", ".liquid", ".njk", ".twig", ".html", ".md", ".mdx", ".txt"],
    "non_source_compound_suffixes": [".d.ts", ".d.tsx"],
    "watch_extra_extensions": [".json"],
    "skip_dirs": ["node_modules", ".git", "dist", "build", ".next", "coverage", "__pycache__", "venv", ".idea", ".vscode", "output", "external_fixture_seed"],
    "root_level_app_markers": ["app", "pages", "widgets", "features", "entities", "shared", "components", "hooks", "services", "server", "contexts", "types", "interfaces"],
    "monorepo_root_markers": ["apps", "packages", "libs", "tooling", "turbo", "services"],
    "monorepo_root_files": ["pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json"],
    "config_file_markers": {},
    "manifest_file_markers": {},
    "plugin_library_map": {},
    "plugin_substring_match_packages": ["trpc", "sentry", "tiptap", "prosemirror"],
    "bundler_plugin_map": {
        "nextjs": ["nextjs", "react", "ui_react", "routing"],
        "vite": ["vite", "bundler_vite"],
        "expo": ["expo", "react", "ui_react"],
        "webpack": ["webpack", "bundler_webpack"],
    },
    "legacy_path_shims": [],
}
_FALLBACK_TELEMETRY_KEYS: set[str] = set()


def _fallback_registry(reason: str, exception: Exception | None = None) -> tuple[dict[str, Any], str]:
    telemetry_key = f"{REGISTRY_FILE}:{reason}"
    if telemetry_key not in _FALLBACK_TELEMETRY_KEYS:
        try:
            from tools.core.honesty_telemetry import record_honesty_event

            record_honesty_event(
                component="language_registry",
                category="central_contract_fallback",
                operation="load_language_registry",
                subject=str(REGISTRY_FILE),
                severity="warning",
                reason=reason,
                fallback="embedded_bootstrap_language_registry",
                claim_impact="configured_polyglot_coverage_not_available",
                exception=exception,
            )
            _FALLBACK_TELEMETRY_KEYS.add(telemetry_key)
        except Exception:
            pass
    return deepcopy(_FALLBACK_REGISTRY), f"fallback:{reason}"


def language_registry_with_identity() -> tuple[dict[str, Any], str, str]:
    try:
        data, fingerprint = load_json_content_cached_with_identity(REGISTRY_FILE)
    except FileNotFoundError as exc:
        payload, identity = _fallback_registry("registry_missing", exc)
        return payload, identity, "fallback"
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        payload, identity = _fallback_registry("registry_unreadable_or_invalid", exc)
        return payload, identity, "fallback"
    if not isinstance(data, dict) or not isinstance(data.get("languages"), dict):
        payload, identity = _fallback_registry("registry_shape_invalid")
        return payload, identity, "fallback"
    merged = deepcopy(_FALLBACK_REGISTRY)
    merged.update(data)
    return merged, fingerprint, "configured"


def language_registry_identity() -> str:
    _, identity, _ = language_registry_with_identity()
    return identity


def language_registry_provenance() -> dict[str, str]:
    _, identity, source = language_registry_with_identity()
    return {"source": source, "identity": identity, "path": str(REGISTRY_FILE)}


def load_language_registry() -> dict[str, Any]:
    payload, _, _ = language_registry_with_identity()
    return payload


def language_extensions() -> set[str]:
    registry, _, _ = language_registry_with_identity()
    exts: set[str] = set()
    for payload in (registry.get("languages") or {}).values():
        exts.update(str(ext).lower() for ext in (payload.get("extensions") or []) if str(ext).startswith("."))
    return exts


def extension_language_map() -> dict[str, str]:
    registry, _, _ = language_registry_with_identity()
    mapping: dict[str, str] = {}
    for language, payload in (registry.get("languages") or {}).items():
        for ext in payload.get("extensions") or []:
            mapping[str(ext).lower()] = str(language)
    return mapping


def extensions_for_language(language: str) -> tuple[str, ...]:
    registry, _, _ = language_registry_with_identity()
    payload = (registry.get("languages") or {}).get(str(language), {})
    return tuple(sorted({
        str(ext).lower()
        for ext in payload.get("extensions") or []
        if str(ext).startswith(".")
    }))


def observation_only_extension_language_map() -> dict[str, str]:
    registry, _, _ = language_registry_with_identity()
    mapping: dict[str, str] = {}
    for language, payload in (registry.get("observation_only_languages") or {}).items():
        for ext in payload.get("extensions") or []:
            mapping[str(ext).lower()] = str(language)
    return mapping


def observable_extension_language_map() -> dict[str, str]:
    mapping = extension_language_map()
    for extension, language in observation_only_extension_language_map().items():
        mapping.setdefault(extension, language)
    return mapping

def structure_extensions() -> set[str]:
    registry, _, _ = language_registry_with_identity()
    exts: set[str] = set()
    for payload in (registry.get("languages") or {}).values():
        exts.update(str(ext).lower() for ext in (payload.get("extensions") or []) if str(ext).startswith("."))
        exts.update(str(ext).lower() for ext in (payload.get("structure_extensions") or []) if str(ext).startswith("."))
    return exts


def non_source_template_extensions() -> set[str]:
    registry, _, _ = language_registry_with_identity()
    return {
        str(ext).lower()
        for ext in registry.get("non_source_template_extensions", [])
        if str(ext).startswith(".")
    }


def non_source_compound_suffixes() -> set[str]:
    registry, _, _ = language_registry_with_identity()
    return {
        str(ext).lower()
        for ext in registry.get("non_source_compound_suffixes", [])
        if str(ext).startswith(".")
    }


def index_files() -> list[str]:
    registry, _, _ = language_registry_with_identity()
    files: list[str] = []
    seen = set()
    for payload in (registry.get("languages") or {}).values():
        for item in payload.get("index_files") or []:
            value = str(item).strip()
            if value and value not in seen:
                seen.add(value)
                files.append(value)
    return files


def watch_extensions() -> set[str]:
    registry, _, _ = language_registry_with_identity()
    extensions: set[str] = set()
    for payload in (registry.get("languages") or {}).values():
        extensions.update(str(ext).lower() for ext in (payload.get("extensions") or []) if str(ext).startswith("."))
    extensions.update(str(ext).lower() for ext in registry.get("watch_extra_extensions", []) if str(ext).startswith("."))
    return extensions


def skip_dirs() -> set[str]:
    registry, _, _ = language_registry_with_identity()
    return {str(item).lower() for item in registry.get("skip_dirs", []) if str(item).strip()}


def config_file_markers() -> set[str]:
    registry, _, _ = language_registry_with_identity()
    markers: set[str] = set()
    for mapping_key in ("config_file_markers", "manifest_file_markers"):
        for files in (registry.get(mapping_key) or {}).values():
            if isinstance(files, list):
                markers.update(str(name).lower() for name in files if str(name).strip())
    markers.update(str(name).lower() for name in registry.get("monorepo_root_files", []) if str(name).strip())
    return markers


def is_config_or_manifest_file(filename: str) -> bool:
    normalized = str(filename).strip().lower()
    return bool(normalized) and any(
        fnmatchcase(normalized, pattern)
        for pattern in config_file_markers()
    )


def config_file_marker_map() -> dict[str, list[str]]:
    registry, _, _ = language_registry_with_identity()
    mapping = registry.get("config_file_markers", {})
    if not isinstance(mapping, dict):
        return {}
    return {
        str(key): [str(item) for item in value if str(item).strip()]
        for key, value in mapping.items()
        if isinstance(value, list)
    }


def manifest_file_marker_map() -> dict[str, list[str]]:
    registry, _, _ = language_registry_with_identity()
    mapping = registry.get("manifest_file_markers", {})
    if not isinstance(mapping, dict):
        return {}
    return {
        str(key): [str(item) for item in value if str(item).strip()]
        for key, value in mapping.items()
        if isinstance(value, list)
    }


def _plugin_library_map(registry: dict[str, Any]) -> dict[str, set[str]]:
    mapping = registry.get("plugin_library_map", {})
    if not isinstance(mapping, dict):
        return {}
    return {
        str(key): {str(item) for item in value if str(item).strip()}
        for key, value in mapping.items()
        if isinstance(value, list)
    }


def plugin_library_map() -> dict[str, set[str]]:
    registry, _, _ = language_registry_with_identity()
    return _plugin_library_map(registry)


def plugins_for_dependencies(dependencies: set[str] | dict[str, Any]) -> set[str]:
    registry, _, _ = language_registry_with_identity()
    names = set(dependencies.keys()) if isinstance(dependencies, dict) else set(dependencies or set())
    normalized = {str(name).lower() for name in names}
    plugins: set[str] = set()
    substring_packages = {
        str(item).lower()
        for item in registry.get("plugin_substring_match_packages", [])
        if str(item).strip()
    }
    for package, mapped_plugins in _plugin_library_map(registry).items():
        package_key = str(package).lower()
        if package_key in normalized:
            plugins.update(mapped_plugins)
        elif package_key in substring_packages and any(package_key in name for name in normalized):
            plugins.update(mapped_plugins)
    return plugins


def plugins_for_bundler(bundler: str | None) -> set[str]:
    registry, _, _ = language_registry_with_identity()
    mapping = registry.get("bundler_plugin_map", {})
    if not isinstance(mapping, dict):
        return set()
    value = str(bundler or "").lower()
    values = mapping.get(value, [])
    return {str(item) for item in values if str(item).strip()} if isinstance(values, list) else set()


def language_for_extension(ext: str) -> str:
    return extension_language_map().get(str(ext or "").lower(), "unknown")


def legacy_path_shims() -> list[dict[str, Any]]:
    return [item for item in (load_language_registry().get("legacy_path_shims") or []) if isinstance(item, dict)]


def apply_legacy_path_shims(path: str) -> str:
    value = str(path or "")
    for shim in legacy_path_shims():
        if not shim.get("enabled"):
            continue
        source = str(shim.get("from") or "")
        target = str(shim.get("to") or "")
        if source and value.startswith(source):
            return value.replace(source, target, 1)
    return value

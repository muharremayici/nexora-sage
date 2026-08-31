from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_FILE, DISCOVERY_FILE, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic, DOCTRINE
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.architecture_blueprints import blueprint_coordinates, canonical_profile_id


def _oracle_policy() -> dict[str, Any]:
    policy = DOCTRINE.get("architecture_oracle_policy", {}) if isinstance(DOCTRINE, dict) else {}
    return policy if isinstance(policy, dict) else {}


def _policy_dict(section: str, key: str, default: dict[str, Any]) -> dict[str, Any]:
    value = (_oracle_policy().get(section) or {}).get(key, default)
    return value if isinstance(value, dict) else default


def _policy_list(section: str, key: str, default: list[str]) -> list[str]:
    value = (_oracle_policy().get(section) or {}).get(key, default)
    return [str(item) for item in value] if isinstance(value, list) else list(default)


def _policy_float(section: str, key: str, default: float) -> float:
    try:
        return float((_oracle_policy().get(section) or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def _policy_int(section: str, key: str, default: int) -> int:
    try:
        return int((_oracle_policy().get(section) or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def _policy_str(section: str, key: str, default: str) -> str:
    value = (_oracle_policy().get(section) or {}).get(key, default)
    return str(value or default)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parts(path: str) -> list[str]:
    return [part for part in str(path or "").replace("\\", "/").split("/") if part]


def _fsd_ranks() -> dict[str, int]:
    value = _policy_dict(
        "blueprint_markers",
        "fsd_ranks",
        {"app": 0, "pages": 1, "widgets": 2, "features": 3, "entities": 4, "shared": 5},
    )
    ranks: dict[str, int] = {}
    for key, rank in value.items():
        try:
            ranks[str(key)] = int(rank)
        except (TypeError, ValueError):
            continue
    return ranks


def _clean_layers() -> set[str]:
    return set(
        _policy_list(
            "blueprint_markers",
            "clean_layers",
            ["domain", "application", "usecases", "use-cases", "infrastructure", "infra", "adapters", "presentation", "ui"],
        )
    )


def _package_library_markers() -> set[str]:
    return set(_policy_list("blueprint_markers", "package_library_markers", ["packages", "libs", "library", "libraries"]))


def _plugin_markers() -> set[str]:
    return set(_policy_list("blueprint_markers", "plugin_markers", ["plugins", "plugin", "extensions", "extension", "adapters", "providers", "integrations"]))


def _module_anchors() -> list[str]:
    return _policy_list("blueprint_markers", "module_anchors", ["features", "entities", "widgets", "pages", "apps", "packages", "modules"])


def _public_api_filenames() -> set[str]:
    return set(_policy_list("blueprint_markers", "public_api_filenames", ["index.ts", "index.tsx", "index.js", "index.jsx"]))


def _public_api_prefixes() -> tuple[str, ...]:
    return tuple(_policy_list("blueprint_markers", "public_api_prefixes", ["public."]))


def _next_app_route_names() -> set[str]:
    return set(_policy_list("blueprint_markers", "next_app_route_filenames", ["page.tsx", "layout.tsx", "route.ts", "loading.tsx", "error.tsx", "template.tsx"]))


def _fsd_layer(path: str) -> str | None:
    fsd_ranks = _fsd_ranks()
    parts = _parts(path)
    for idx, part in enumerate(parts):
        if part in {"app", "pages"} and not (idx == 0 or (idx == 1 and parts[0] == "src")):
            continue
        if part in fsd_ranks:
            return part
    return None


def _clean_layer(path: str) -> str | None:
    clean_layers = _clean_layers()
    for part in _parts(path):
        if part in clean_layers:
            return part
    return None


def _module_key(path: str) -> str:
    parts = _parts(path)
    if not parts:
        return ""
    for anchor in _module_anchors():
        if anchor in parts:
            idx = parts.index(anchor)
            if idx + 1 < len(parts):
                return "/".join(parts[: idx + 2])
    return parts[0]


def _is_public_api(path: str) -> bool:
    name = Path(path).name.lower()
    return name in _public_api_filenames() or name.startswith(_public_api_prefixes())


def _has_config_marker(path: str, names: set[str]) -> bool:
    return Path(str(path or "").replace("\\", "/")).name.lower() in names


def _iter_projects(atlas: dict[str, Any]):
    for project_key, project_data in atlas.items():
        if project_key == "symbols" or not isinstance(project_data, dict):
            continue
        files = project_data.get("files") or {}
        if not isinstance(files, dict):
            files = {}
        deps = project_data.get("dependencies") or {}
        if not isinstance(deps, dict):
            deps = {}
        yield project_key, project_data, files, deps


def _score_fsd(files: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    fsd_ranks = _fsd_ranks()
    layer_counts = Counter()
    for rel in files:
        layer = _fsd_layer(rel)
        if layer:
            layer_counts[layer] += 1

    comparable = 0
    aligned = 0
    reverse = 0
    cross_private = 0
    for source, targets in deps.items():
        source_layer = _fsd_layer(source)
        if not source_layer or not isinstance(targets, list):
            continue
        for target in targets:
            target_layer = _fsd_layer(str(target))
            if not target_layer:
                continue
            comparable += 1
            if fsd_ranks.get(source_layer, 999) <= fsd_ranks.get(target_layer, 999):
                aligned += 1
            else:
                reverse += 1
            if _module_key(source) != _module_key(str(target)) and not _is_public_api(str(target)):
                cross_private += 1

    layer_coverage = sum(layer_counts.values()) / max(len(files), 1)
    direction_ratio = aligned / max(comparable, 1)
    encapsulation_ratio = 1.0 - (cross_private / max(comparable, 1))
    score = round(
        (layer_coverage * _policy_float("fsd_weights", "layer_coverage", 0.35))
        + (direction_ratio * _policy_float("fsd_weights", "direction_ratio", 0.45))
        + (encapsulation_ratio * _policy_float("fsd_weights", "encapsulation_ratio", 0.20)),
        3,
    )
    structural_layers = set(layer_counts) - {"app", "pages"}
    if layer_counts and not structural_layers:
        score = min(score, _policy_float("fsd_caps", "app_pages_only", 0.65))
    elif len(layer_counts) < _policy_int("fsd_thresholds", "few_layers_count", 3):
        score = min(score, _policy_float("fsd_caps", "few_layers", 0.70))
    if layer_coverage < _policy_float("fsd_thresholds", "very_low_coverage", 0.05):
        score = min(score, _policy_float("fsd_caps", "very_low_coverage", 0.40))
    if layer_coverage < _policy_float("fsd_thresholds", "low_coverage", 0.20):
        score = min(score, _policy_float("fsd_caps", "low_coverage", 0.62))
    return {
        "score": score,
        "layer_coverage": round(layer_coverage, 3),
        "direction_ratio": round(direction_ratio, 3),
        "encapsulation_ratio": round(encapsulation_ratio, 3),
        "comparable_edges": comparable,
        "aligned_edges": aligned,
        "reverse_edges": reverse,
        "cross_private_edges": cross_private,
        "layer_counts": dict(layer_counts),
    }


def _score_next(files: dict[str, Any]) -> dict[str, Any]:
    app_router = 0
    pages_router = 0
    route_handlers = 0
    server_client_markers = 0
    for rel, meta in files.items():
        norm = str(rel).replace("\\", "/")
        parts = _parts(norm)
        is_app_router_path = bool(
            parts
            and (
                parts[0] == "app"
                or (len(parts) > 1 and parts[0] == "src" and parts[1] == "app")
                or (len(parts) > 2 and parts[0] == "apps" and parts[2] == "app")
            )
        )
        if is_app_router_path:
            app_router += 1
        if "pages" in parts:
            pages_router += 1
        name = Path(norm).name.lower()
        if is_app_router_path and name in _next_app_route_names():
            route_handlers += 1
        features = meta.get("features") if isinstance(meta, dict) else []
        if isinstance(features, list) and any(
            str(feature).startswith(("ContractKind:next_", "Next:")) or str(feature) in {"ServerComponent", "ClientComponent"}
            for feature in features
        ):
            server_client_markers += 1

    route_cap = _policy_float("nextjs_caps", "route_handler_cap", 3.0)
    marker_cap = _policy_float("nextjs_caps", "server_client_marker_cap", 10.0)
    app_cap = _policy_float("nextjs_caps", "app_router_file_cap", 60.0)
    score = min(
        1.0,
        (min(route_handlers, route_cap) / max(route_cap, 1.0) * _policy_float("nextjs_weights", "route_handlers", 0.70))
        + (min(server_client_markers, marker_cap) / max(marker_cap, 1.0) * _policy_float("nextjs_weights", "server_client_markers", 0.20))
        + (min(app_router, app_cap) / max(app_cap, 1.0) * _policy_float("nextjs_weights", "app_router_files", 0.10)),
    )
    if route_handlers >= _policy_int("nextjs_thresholds", "strong_route_handlers", 2) and app_router >= _policy_int("nextjs_thresholds", "strong_app_router_files", 3):
        score = max(score, _policy_float("nextjs_caps", "strong_app_router_score", 0.82))
    return {
        "score": round(score, 3),
        "app_router_files": app_router,
        "pages_router_files": pages_router,
        "route_convention_files": route_handlers,
        "next_boundary_markers": server_client_markers,
    }


def _score_clean_hex(files: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    layer_counts = Counter()
    for rel in files:
        layer = _clean_layer(rel)
        if layer:
            layer_counts[layer] += 1

    domain_to_infra = 0
    comparable = 0
    for source, targets in deps.items():
        source_layer = _clean_layer(source)
        if not source_layer or not isinstance(targets, list):
            continue
        for target in targets:
            target_layer = _clean_layer(str(target))
            if not target_layer:
                continue
            comparable += 1
            if source_layer == "domain" and target_layer in {"infra", "infrastructure", "adapters"}:
                domain_to_infra += 1

    clean_coverage = sum(layer_counts.values()) / max(len(files), 1)
    purity = 1.0 - (domain_to_infra / comparable) if comparable else 0.0
    hex_signal = (
        layer_counts.get("domain", 0)
        + layer_counts.get("adapters", 0)
        + layer_counts.get("infra", 0)
        + layer_counts.get("infrastructure", 0)
    ) / max(len(files), 1)
    return {
        "clean_score": round(
            (clean_coverage * _policy_float("clean_hex_weights", "clean_coverage", 0.55))
            + (purity * _policy_float("clean_hex_weights", "purity", 0.45)),
            3,
        ),
        "hexagonal_score": round(
            min(1.0, hex_signal * _policy_float("clean_hex_weights", "hex_signal_multiplier", 2.5))
            * _policy_float("clean_hex_weights", "hex_signal", 0.65)
            + purity * _policy_float("clean_hex_weights", "hex_purity", 0.35),
            3,
        ),
        "layer_counts": dict(layer_counts),
        "domain_to_infra_edges": domain_to_infra,
        "comparable_edges": comparable,
        "purity_ratio": round(purity, 3),
    }


def _load_discovery_profile(project_key: str) -> str:
    for source in (CONFIG_FILE, DISCOVERY_FILE):
        payload = load_json_file(source, {})
        if not isinstance(payload, dict):
            continue
        projects = (((payload.get("_discovery_metadata") or {}).get("projects") or {}) if isinstance(payload.get("_discovery_metadata"), dict) else {})
        project_meta = ((projects.get(project_key) or {}).get("metadata") or {}) if isinstance(projects, dict) else {}
        value = project_meta.get("detected_profile") if isinstance(project_meta, dict) else ""
        if isinstance(value, str) and value:
            return value
        profiles = payload.get("project_profiles") or {}
        if isinstance(profiles, dict):
            direct = profiles.get(project_key)
            if isinstance(direct, str):
                return direct
            if isinstance(direct, dict):
                value = direct.get("detected_profile") or direct.get("profile")
                if isinstance(value, str) and value:
                    return value
        if project_key == "MAIN":
            architecture = payload.get("architecture") or {}
            value = architecture.get("detected_profile") if isinstance(architecture, dict) else ""
            if isinstance(value, str) and value:
                return value
    return ""


def _score_sovereign_hybrid(fsd: dict[str, Any], clean_hex: dict[str, Any], discovery_profile: str) -> dict[str, Any]:
    fsd_direction = float(fsd.get("direction_ratio") or 0.0)
    fsd_coverage = float(fsd.get("layer_coverage") or 0.0)
    clean_score = float(clean_hex.get("clean_score") or 0.0)
    purity = float(clean_hex.get("purity_ratio") or 0.0)
    clean_layers = clean_hex.get("layer_counts") or {}
    has_clean_spine = bool(
        isinstance(clean_layers, dict)
        and (clean_layers.get("domain") or clean_layers.get("application") or clean_layers.get("infra") or clean_layers.get("adapters"))
    )
    sovereign_profile = _policy_str("classification", "sovereign_profile_name", "SOVEREIGN_ELITE")
    profile_prior = 1.0 if str(discovery_profile).upper() == sovereign_profile else 0.0
    score = (
        (fsd_direction * _policy_float("sovereign_hybrid_weights", "fsd_direction", 0.25))
        + (fsd_coverage * _policy_float("sovereign_hybrid_weights", "fsd_coverage", 0.18))
        + (clean_score * _policy_float("sovereign_hybrid_weights", "clean_score", 0.22))
        + (purity * _policy_float("sovereign_hybrid_weights", "purity", 0.20))
        + (profile_prior * _policy_float("sovereign_hybrid_weights", "profile_prior", 0.15))
    )
    if not has_clean_spine:
        score *= _policy_float("sovereign_hybrid_caps", "missing_clean_spine_multiplier", 0.75)
    return {
        "score": round(min(1.0, score), 3),
        "discovery_profile": discovery_profile,
        "has_clean_spine": has_clean_spine,
        "fsd_direction_ratio": round(fsd_direction, 3),
        "fsd_layer_coverage": round(fsd_coverage, 3),
        "clean_score": round(clean_score, 3),
        "purity_ratio": round(purity, 3),
    }


def _score_package_library(files: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    package_files = 0
    public_api_files = 0
    package_json_files = 0
    package_private_edges = 0
    package_edges = 0
    package_roots: set[str] = set()
    for rel in files:
        parts = _parts(rel)
        if any(part in _package_library_markers() for part in parts):
            package_files += 1
            root = _module_key(rel)
            if root:
                package_roots.add(root)
            if _is_public_api(rel):
                public_api_files += 1
        if _has_config_marker(rel, {"package.json"}):
            package_json_files += 1

    for source, targets in deps.items():
        if "packages" not in _parts(source) or not isinstance(targets, list):
            continue
        for target in targets:
            if "packages" not in _parts(str(target)):
                continue
            package_edges += 1
            if _module_key(source) != _module_key(str(target)) and not _is_public_api(str(target)):
                package_private_edges += 1

    package_coverage = package_files / max(len(files), 1)
    public_api_density = public_api_files / max(package_files, 1)
    package_root_density = min(len(package_roots), 12) / 12
    encapsulation = 1.0 - (package_private_edges / max(package_edges, 1))
    score = (
        package_coverage * _policy_float("package_library_weights", "package_coverage", 0.35)
        + public_api_density * _policy_float("package_library_weights", "public_api_density", 0.25)
        + package_root_density * _policy_float("package_library_weights", "package_root_density", 0.20)
        + encapsulation * _policy_float("package_library_weights", "encapsulation", 0.20)
    )
    if package_files < _policy_int("package_library_thresholds", "min_package_files", 8):
        score = min(score, _policy_float("package_library_caps", "few_package_files", 0.44))
    return {
        "score": round(min(1.0, score), 3),
        "package_files": package_files,
        "package_roots": len(package_roots),
        "public_api_files": public_api_files,
        "package_json_files": package_json_files,
        "package_coverage": round(package_coverage, 3),
        "public_api_density": round(public_api_density, 3),
        "encapsulation_ratio": round(encapsulation, 3),
        "cross_package_private_edges": package_private_edges,
    }


def _score_turborepo_saas(files: dict[str, Any], nextjs: dict[str, Any]) -> dict[str, Any]:
    apps_files = 0
    package_files = 0
    workspace_markers = 0
    for rel in files:
        parts = _parts(rel)
        if parts and parts[0] == "apps":
            apps_files += 1
        if parts and parts[0] == "packages":
            package_files += 1
        if _has_config_marker(rel, {"turbo.json", "pnpm-workspace.yaml", "yarn.lock", "package.json"}):
            workspace_markers += 1

    app_package_balance = min(apps_files, package_files) / max(max(apps_files, package_files), 1)
    workspace_signal = min(workspace_markers, 8) / 8
    next_signal = min(float(nextjs.get("score") or 0.0), 1.0)
    scale_signal = min((apps_files + package_files), 200) / 200
    score = (
        app_package_balance * _policy_float("turborepo_saas_weights", "app_package_balance", 0.35)
        + workspace_signal * _policy_float("turborepo_saas_weights", "workspace_signal", 0.20)
        + next_signal * _policy_float("turborepo_saas_weights", "next_signal", 0.25)
        + scale_signal * _policy_float("turborepo_saas_weights", "scale_signal", 0.20)
    )
    if apps_files < _policy_int("turborepo_saas_thresholds", "min_app_files", 5) or package_files < _policy_int("turborepo_saas_thresholds", "min_package_files", 5):
        score = min(score, _policy_float("turborepo_saas_caps", "missing_app_or_package_side", 0.42))
    return {
        "score": round(min(1.0, score), 3),
        "apps_files": apps_files,
        "package_files": package_files,
        "workspace_markers": workspace_markers,
        "app_package_balance": round(app_package_balance, 3),
        "next_signal": round(next_signal, 3),
    }


def _score_plugin_platform(files: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    plugin_files = 0
    adapter_files = 0
    provider_files = 0
    integration_files = 0
    plugin_roots: set[str] = set()
    host_to_plugin_edges = 0
    for rel in files:
        parts = set(_parts(rel))
        plugin_markers = _plugin_markers()
        if parts & plugin_markers:
            plugin_files += 1
            root = _module_key(rel)
            if root:
                plugin_roots.add(root)
        if "adapters" in parts:
            adapter_files += 1
        if "providers" in parts:
            provider_files += 1
        if "integrations" in parts:
            integration_files += 1

    for source, targets in deps.items():
        if not isinstance(targets, list):
            continue
        source_parts = set(_parts(source))
        for target in targets:
            target_parts = set(_parts(str(target)))
            plugin_markers = _plugin_markers()
            if not (source_parts & plugin_markers) and (target_parts & plugin_markers):
                host_to_plugin_edges += 1

    plugin_coverage = plugin_files / max(len(files), 1)
    role_diversity = sum(1 for value in (adapter_files, provider_files, integration_files) if value > 0) / 3
    root_density = min(len(plugin_roots), 8) / 8
    score = (
        min(plugin_coverage * 3, 1.0) * _policy_float("plugin_platform_weights", "plugin_coverage", 0.35)
        + role_diversity * _policy_float("plugin_platform_weights", "role_diversity", 0.30)
        + root_density * _policy_float("plugin_platform_weights", "root_density", 0.20)
        + min(host_to_plugin_edges, 20) / 20 * _policy_float("plugin_platform_weights", "host_edges", 0.15)
    )
    if plugin_files < _policy_int("plugin_platform_thresholds", "min_plugin_files", 6):
        score = min(score, _policy_float("plugin_platform_caps", "few_plugin_files", 0.40))
    return {
        "score": round(min(1.0, score), 3),
        "plugin_files": plugin_files,
        "plugin_roots": len(plugin_roots),
        "adapter_files": adapter_files,
        "provider_files": provider_files,
        "integration_files": integration_files,
        "host_to_plugin_edges": host_to_plugin_edges,
        "plugin_coverage": round(plugin_coverage, 3),
        "role_diversity": round(role_diversity, 3),
    }


def _score_mixed_architecture(scores: dict[str, float], *, fsd: dict[str, Any]) -> dict[str, Any]:
    threshold = _policy_float("mixed_architecture_thresholds", "candidate_score", 0.55)
    close_delta = _policy_float("mixed_architecture_thresholds", "close_delta", 0.20)
    excluded = {
        _policy_str("classification", "modular_profile_name", "MODULAR_FLAT"),
        _policy_str("classification", "minimal_profile_name", "MINIMAL"),
        _policy_str("classification", "turborepo_saas_profile_name", "TURBOREPO_SAAS"),
    }
    candidates = {name: score for name, score in scores.items() if score >= threshold and name not in excluded}
    fsd_profile = _policy_str("classification", "fsd_profile_name", "FSD_STRICT")
    fsd_layers = set((fsd.get("layer_counts") or {}).keys()) if isinstance(fsd, dict) else set()
    if fsd_profile in candidates and not (fsd_layers - {"app", "pages"}):
        candidates.pop(fsd_profile, None)
    ranked = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) < 2:
        return {"score": 0.0, "candidates": candidates, "reason": "fewer_than_two_strong_families"}
    top_score = ranked[0][1]
    close = [(name, score) for name, score in ranked if top_score - score <= close_delta]
    if len(close) < 2:
        return {"score": 0.0, "candidates": candidates, "reason": "single_dominant_family"}
    score = round(sum(score for _name, score in close[:3]) / min(len(close), 3), 3)
    return {
        "score": score,
        "candidates": candidates,
        "close_families": [{"profile": name, "score": score} for name, score in close],
        "reason": "multiple_strong_architecture_families",
    }


def _classify_project(project_key: str, files: dict[str, Any], deps: dict[str, Any], *, use_discovery_prior: bool = True) -> dict[str, Any]:
    fsd = _score_fsd(files, deps)
    nextjs = _score_next(files)
    clean_hex = _score_clean_hex(files, deps)
    discovery_profile = _load_discovery_profile(project_key) if use_discovery_prior else ""
    sovereign = _score_sovereign_hybrid(fsd, clean_hex, discovery_profile)
    package_library = _score_package_library(files, deps)
    turborepo_saas = _score_turborepo_saas(files, nextjs)
    plugin_platform = _score_plugin_platform(files, deps)
    file_count = len(files)
    dep_count = sum(len(v) for v in deps.values() if isinstance(v, list))

    fsd_profile = _policy_str("classification", "fsd_profile_name", "FSD_STRICT")
    nextjs_profile = _policy_str("classification", "nextjs_profile_name", "NEXTJS_APP_ROUTER")
    clean_profile = _policy_str("classification", "clean_profile_name", "CLEAN_ARCHITECTURE")
    sovereign_profile = _policy_str("classification", "sovereign_profile_name", "SOVEREIGN_ELITE")
    modular_profile = _policy_str("classification", "modular_profile_name", "MODULAR_FLAT")
    minimal_profile = _policy_str("classification", "minimal_profile_name", "MINIMAL")
    package_library_profile = _policy_str("classification", "package_library_profile_name", "MONOREPO_PACKAGE_LIBRARY")
    turborepo_saas_profile = _policy_str("classification", "turborepo_saas_profile_name", "TURBOREPO_SAAS")
    plugin_platform_profile = _policy_str("classification", "plugin_platform_profile_name", "PLUGIN_PLATFORM")
    mixed_profile = _policy_str("classification", "mixed_profile_name", "MIXED_ARCHITECTURE")

    scores = {
        fsd_profile: fsd["score"],
        nextjs_profile: nextjs["score"],
        clean_profile: clean_hex["clean_score"],
        sovereign_profile: sovereign["score"],
        package_library_profile: package_library["score"],
        turborepo_saas_profile: turborepo_saas["score"],
        plugin_platform_profile: plugin_platform["score"],
        modular_profile: round(min(_policy_float("classification", "modular_flat_cap", 0.68), dep_count / max(file_count * 2, 1)), 3),
    }
    mixed = _score_mixed_architecture(scores, fsd=fsd)
    scores[mixed_profile] = mixed["score"]
    recommended = max(scores, key=scores.get)
    confidence = scores[recommended]
    if (
        scores.get(turborepo_saas_profile, 0.0) >= _policy_float("classification", "turborepo_specificity_threshold", 0.55)
        and recommended == nextjs_profile
        and scores.get(nextjs_profile, 0.0) - scores.get(turborepo_saas_profile, 0.0)
        <= _policy_float("classification", "specificity_override_delta", 0.20)
    ):
        recommended = turborepo_saas_profile
        confidence = scores[recommended]
    if (
        scores.get(sovereign_profile, 0.0) >= _policy_float("classification", "sovereign_specificity_threshold", 0.68)
        and recommended in {fsd_profile, clean_profile, modular_profile}
        and scores.get(recommended, 0.0) - scores.get(sovereign_profile, 0.0)
        <= _policy_float("classification", "specificity_override_delta", 0.20)
    ):
        recommended = sovereign_profile
        confidence = scores[recommended]
    if (
        scores.get(mixed_profile, 0.0) >= _policy_float("classification", "mixed_specificity_threshold", 0.70)
        and len((mixed.get("close_families") or [])) >= _policy_int("classification", "mixed_min_close_families", 2)
        and recommended not in {sovereign_profile, turborepo_saas_profile}
        and scores.get(recommended, 0.0) - scores.get(mixed_profile, 0.0)
        <= _policy_float("classification", "specificity_override_delta", 0.20)
    ):
        recommended = mixed_profile
        confidence = scores[recommended]
    if confidence < _policy_float("classification", "minimal_confidence_threshold", 0.45):
        recommended = minimal_profile
        confidence = round(max(confidence, _policy_float("classification", "minimal_confidence_floor", 0.2)), 3)

    recommended = canonical_profile_id(recommended)
    blueprint = blueprint_coordinates(recommended)

    non_sealable = set(((_oracle_policy().get("classification") or {}).get("non_sealable_profiles") or [modular_profile]))
    seal_ready = (
        confidence >= _policy_float("classification", "seal_confidence_threshold", 0.72)
        and file_count >= _policy_int("classification", "seal_min_file_count", 20)
        and recommended not in non_sealable
        and blueprint.get("seal_policy") != "advisory_only"
    )
    return {
        "project": project_key,
        "file_count": file_count,
        "dependency_edges": dep_count,
        "recommended_profile": recommended,
        "blueprint": blueprint,
        "confidence": round(confidence, 3),
        "seal_ready": seal_ready,
        "requires_human_approval": True,
        "scores": scores,
        "evidence": {
            "fsd": fsd,
            "nextjs": nextjs,
            "clean_hexagonal": clean_hex,
            "sovereign_hybrid": sovereign,
            "package_library": package_library,
            "turborepo_saas": turborepo_saas,
            "plugin_platform": plugin_platform,
            "mixed_architecture": mixed,
        },
        "seal_proposal": {
            "status": "PROPOSED" if seal_ready else "ADVISORY_ONLY",
            "doctrine_profile": recommended,
            "reason": (
                "Post-Atlas evidence is strong enough to ask for a human seal."
                if seal_ready
                else "Evidence is useful for onboarding, but not strong enough to auto-seal."
            ),
        },
    }


def build_architecture_oracle(atlas: dict[str, Any] | None = None, *, use_discovery_prior: bool = True) -> dict[str, Any]:
    atlas_payload = atlas if isinstance(atlas, dict) else project_runtime_atlas(load_atlas_data())[0]
    projects = []
    for project_key, _project_data, files, deps in _iter_projects(atlas_payload if isinstance(atlas_payload, dict) else {}):
        projects.append(_classify_project(project_key, files, deps, use_discovery_prior=use_discovery_prior))

    ready = [project for project in projects if project.get("seal_ready")]
    top_profile = None
    if projects:
        profile_counts = Counter(project.get("recommended_profile") for project in projects)
        top_profile = profile_counts.most_common(1)[0][0]

    return {
        "meta": {
            "kind": "architecture_oracle",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.engines.architecture_oracle",
            "input": "post_atlas",
            "mode": "advisory_human_seal",
        },
        "summary": {
            "project_count": len(projects),
            "seal_ready_projects": len(ready),
            "top_recommended_profile": top_profile,
            "status": "PROPOSE_SEAL" if ready else "ADVISORY_ONLY",
            "hard_gate_enforced": False,
        },
        "projects": projects,
        "policy": {
            "pre_atlas_discovery_role": "scope_alias_driver_selection_only",
            "post_atlas_oracle_role": "evidence_based_doctrine_proposal",
            "seal_requires_human_approval": True,
            "audit_blocking_before_seal": False,
            "reason": "1.0.0 keeps existing audits operational; Oracle proposes doctrine instead of locking the pipeline.",
        },
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Architecture Oracle",
        "",
        "Post-Atlas doctrine proposal. This report diagnoses the observed dependency geometry and proposes, but does not automatically seal, an architecture doctrine.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- projects: `{summary.get('project_count')}`",
        f"- seal-ready projects: `{summary.get('seal_ready_projects')}`",
        f"- top recommended profile: `{summary.get('top_recommended_profile')}`",
        f"- hard gate enforced: `{summary.get('hard_gate_enforced')}`",
        "",
        "| Project | Files | Edges | Profile | Confidence | Seal Ready | FSD Direction | Encapsulation |",
        "|---|---:|---:|---|---:|---|---:|---:|",
    ]
    for project in payload.get("projects", []):
        fsd = ((project.get("evidence") or {}).get("fsd") or {})
        lines.append(
            f"| `{project.get('project')}` | `{project.get('file_count')}` | `{project.get('dependency_edges')}` | "
            f"`{project.get('recommended_profile')}` | `{project.get('confidence')}` | "
            f"`{project.get('seal_ready')}` | `{fsd.get('direction_ratio')}` | `{fsd.get('encapsulation_ratio')}` |"
        )

    lines.extend(
        [
            "",
            "## Seal Policy",
            "",
            "- Discovery remains a logistical scout: scope, alias and driver selection.",
            "- Atlas is the material fact layer.",
            "- Oracle proposes doctrine from observed graph evidence.",
            "- Human approval is required before treating a proposal as sealed governance.",
            "- 1.0.0 does not block Audit/Quality Gates when a project is unsealed.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_architecture_oracle() -> dict[str, Any]:
    logger.info("Running Post-Atlas Architecture Oracle...")
    payload = build_architecture_oracle()
    save_json_atomic(RAW_DIR / "architecture_oracle.json", payload)
    save_text_atomic(REPORTS_DIR / "architecture_oracle.md", render_report(payload))
    logger.info(
        "Architecture Oracle completed: projects=%s status=%s",
        payload.get("summary", {}).get("project_count"),
        payload.get("summary", {}).get("status"),
    )
    return payload


if __name__ == "__main__":
    run_architecture_oracle()

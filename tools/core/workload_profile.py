from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from tools.core.atlas_integrity import atlas_counts
from tools.core.config import CONFIG_DIR


POLICY_PATH = CONFIG_DIR / "workload_profile.json"


def load_workload_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = path or POLICY_PATH
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def atlas_workload_counts(atlas: dict[str, Any]) -> dict[str, int]:
    return atlas_counts(atlas if isinstance(atlas, dict) else {})


def workload_band(counts: dict[str, int], policy: dict[str, Any]) -> str:
    bands = policy.get("bands", []) if isinstance(policy.get("bands"), list) else []
    for band in bands:
        if not isinstance(band, dict) or not band.get("id"):
            continue
        limits = {
            "files": band.get("max_files"),
            "symbols": band.get("max_symbols"),
            "dependency_edges": band.get("max_dependency_edges"),
        }
        if all(limit is None or int(counts.get(key, 0)) <= int(limit) for key, limit in limits.items()):
            return str(band["id"])
    return "L"


def _scaled_budget(counts: dict[str, int], config: dict[str, Any]) -> float:
    total = float(config.get("base_seconds", 45.0) or 45.0)
    dimensions = (
        ("files", "file"),
        ("symbols", "symbol"),
        ("dependency_edges", "dependency_edge"),
    )
    for count_key, prefix in dimensions:
        count = int(counts.get(count_key, 0) or 0)
        soft_cap = int(config.get(f"{prefix}_soft_cap", 0) or 0)
        bucket = int(config.get(f"{prefix}_bucket", 0) or 0)
        per_bucket = float(config.get(f"per_{prefix}_bucket_seconds", 0.0) or 0.0)
        if count > soft_cap and bucket > 0:
            total += ((count - soft_cap) / float(bucket)) * per_bucket
    return round(total, 2)


def build_workload_profile(
    atlas: dict[str, Any],
    *,
    snapshot_id: str = "",
    execution_profile: str = "unknown",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_policy = policy if isinstance(policy, dict) else load_workload_policy()
    counts = atlas_workload_counts(atlas if isinstance(atlas, dict) else {})
    timeout_config = effective_policy.get("adaptive_timeout", {}) if isinstance(effective_policy.get("adaptive_timeout"), dict) else {}
    timeout_seconds = (
        counts["files"] * float(timeout_config.get("per_file_seconds", 0.0) or 0.0)
        + counts["symbols"] * float(timeout_config.get("per_symbol_seconds", 0.0) or 0.0)
        + counts["dependency_edges"] * float(timeout_config.get("per_dependency_edge_seconds", 0.0) or 0.0)
    )
    timeout_seconds = min(float(timeout_config.get("max_seconds", 1800) or 1800), timeout_seconds)
    dead_code_config = effective_policy.get("dead_code_budget", {}) if isinstance(effective_policy.get("dead_code_budget"), dict) else {}
    return {
        "meta": {"kind": "workload_profile", "version": "v1"},
        "snapshot_id": str(snapshot_id or ""),
        "execution_profile": str(execution_profile or "unknown"),
        "band": workload_band(counts, effective_policy),
        "counts": counts,
        "budgets": {
            "adaptive_timeout_seconds": int(math.ceil(timeout_seconds)),
            "dead_code_seconds": _scaled_budget(counts, dead_code_config),
        },
    }


def adaptive_timeout_seconds(profile: dict[str, Any], default_seconds: int = 180) -> int:
    budgets = profile.get("budgets", {}) if isinstance(profile, dict) else {}
    calculated = int(budgets.get("adaptive_timeout_seconds", 0) or 0) if isinstance(budgets, dict) else 0
    return max(int(default_seconds), calculated)


def dead_code_budget_seconds(profile: dict[str, Any], fallback_seconds: float = 45.0) -> float:
    budgets = profile.get("budgets", {}) if isinstance(profile, dict) else {}
    value = budgets.get("dead_code_seconds") if isinstance(budgets, dict) else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback_seconds)


def manual_review_budget_selection(
    atlas: dict[str, Any],
    project_roles: dict[str, Any],
    fractal_meta: dict[str, Any],
    quality_gates: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select one explainable workload budget without weakening its safety ceiling."""

    effective_policy = policy if isinstance(policy, dict) else load_workload_policy()
    config = effective_policy.get("manual_review_budget", {})
    if not isinstance(config, dict) or not config:
        raise ValueError("Workload policy manual_review_budget must be a non-empty object.")

    allowed_roles = {
        str(role).strip().lower()
        for role in config.get("applicable_project_roles", [])
        if str(role).strip()
    }
    normalized_roles = {
        str(project): str(role).strip().lower()
        for project, role in (project_roles or {}).items()
    }
    applicable_projects = sorted(
        project for project, role in normalized_roles.items() if role in allowed_roles
    )
    indexed_projects = [project for project in applicable_projects if isinstance(atlas.get(project), dict)]
    counts = atlas_workload_counts({project: atlas[project] for project in indexed_projects})

    analyzer_contract = config.get("analyzer_families", {})
    analyzer_contract = analyzer_contract if isinstance(analyzer_contract, dict) else {}
    applicable_families: list[str] = []
    observed_active_families: list[str] = []
    for family, family_config in sorted(analyzer_contract.items()):
        if not isinstance(family_config, dict):
            continue
        family_roles = {
            str(role).strip().lower()
            for role in family_config.get("applicable_project_roles", [])
            if str(role).strip()
        }
        if family_roles and not (family_roles & set(normalized_roles.values())):
            continue
        applicable_families.append(str(family))
        meta_field = str(family_config.get("meta_field") or "")
        if meta_field and int(fractal_meta.get(meta_field, 0) or 0) > 0:
            observed_active_families.append(str(family))

    def bucket_items(count_key: str, bucket_key: str, allowance_key: str) -> int:
        bucket = max(1, int(config.get(bucket_key, 1) or 1))
        allowance = max(0, int(config.get(allowance_key, 0) or 0))
        return int(math.ceil(int(counts.get(count_key, 0) or 0) / float(bucket))) * allowance

    components = {
        "base_items": max(0, int(config.get("base_items", 0) or 0)),
        "indexed_project_items": len(indexed_projects)
        * max(0, int(config.get("per_indexed_project_items", 0) or 0)),
        "file_items": bucket_items("files", "file_bucket", "per_file_bucket_items"),
        "symbol_items": bucket_items("symbols", "symbol_bucket", "per_symbol_bucket_items"),
        "dependency_edge_items": bucket_items(
            "dependency_edges", "dependency_edge_bucket", "per_dependency_edge_bucket_items"
        ),
        "analyzer_family_items": len(applicable_families)
        * max(0, int(config.get("per_applicable_analyzer_family_items", 0) or 0)),
    }
    workload_budget = sum(components.values())

    baseline = quality_gates.get("manual_review_budget_baseline", {})
    baseline = baseline if isinstance(baseline, dict) else {}
    baseline_budget = 0
    baseline_status = "not_configured"
    if baseline:
        observed_items = max(0, int(baseline.get("observed_items", 0) or 0))
        reviewed_suppressions = max(0, int(baseline.get("reviewed_suppressions", 0) or 0))
        if reviewed_suppressions > observed_items:
            raise ValueError("Manual-review baseline suppressions cannot exceed observed items.")
        baseline_net_items = observed_items - reviewed_suppressions
        trend_ratio = max(0.0, float(config.get("baseline_trend_headroom_ratio", 0.0) or 0.0))
        minimum_headroom = max(0, int(config.get("baseline_minimum_headroom_items", 0) or 0))
        trend_headroom = max(minimum_headroom, int(math.ceil(baseline_net_items * trend_ratio)))
        baseline_budget = baseline_net_items + trend_headroom
        baseline_status = "reviewed_operational_envelope"
    else:
        observed_items = 0
        reviewed_suppressions = 0
        baseline_net_items = 0
        trend_headroom = 0

    hard_upper_bound = max(1, int(config.get("hard_upper_bound_items", 1) or 1))
    uncapped_budget = max(workload_budget, baseline_budget)
    selected_budget = min(hard_upper_bound, uncapped_budget)
    return {
        "policy_source": "config/workload_profile.json#/manual_review_budget",
        "baseline_source": "quality_gates.manual_review_budget_baseline" if baseline else None,
        "formula": "min(hard_upper_bound, max(sum(workload_components), baseline_net_items + max(minimum_headroom, ceil(baseline_net_items * trend_ratio))))",
        "selected_budget": selected_budget,
        "uncapped_budget": uncapped_budget,
        "hard_upper_bound": hard_upper_bound,
        "hard_upper_bound_applied": uncapped_budget > hard_upper_bound,
        "workload_budget": workload_budget,
        "workload_components": components,
        "workload_counts": counts,
        "applicable_project_roles": sorted(allowed_roles),
        "applicable_projects": applicable_projects,
        "indexed_projects": indexed_projects,
        "missing_indexed_projects": sorted(set(applicable_projects) - set(indexed_projects)),
        "applicable_analyzer_families": applicable_families,
        "observed_active_analyzer_families": observed_active_families,
        "baseline_status": baseline_status,
        "baseline_observed_items": observed_items,
        "baseline_reviewed_suppressions": reviewed_suppressions,
        "baseline_net_items": baseline_net_items,
        "baseline_trend_headroom_items": trend_headroom,
        "baseline_budget": baseline_budget,
        "baseline_evidence": baseline,
        "confidence": "reviewed_repository_baseline" if baseline else "policy_and_indexed_workload_only",
    }


def oracle_profiles(policy: dict[str, Any] | None = None) -> set[str]:
    effective_policy = policy if isinstance(policy, dict) else load_workload_policy()
    oracle = effective_policy.get("oracle", {}) if isinstance(effective_policy.get("oracle"), dict) else {}
    values = oracle.get("profiles", []) if isinstance(oracle.get("profiles"), list) else []
    return {str(value).strip().lower() for value in values if str(value).strip()}


def oracle_worker_count(profile: dict[str, Any], project_count: int, policy: dict[str, Any] | None = None) -> int:
    effective_policy = policy if isinstance(policy, dict) else load_workload_policy()
    oracle = effective_policy.get("oracle", {}) if isinstance(effective_policy.get("oracle"), dict) else {}
    platform_cap = int(oracle.get("windows_max_workers" if os.name == "nt" else "default_max_workers", 1) or 1)
    files_per_worker = max(1, int(oracle.get("files_per_worker", 2500) or 2500))
    counts = profile.get("counts", {}) if isinstance(profile, dict) else {}
    total_files = int(counts.get("files", 0) or 0) if isinstance(counts, dict) else 0
    load_workers = max(1, math.ceil(total_files / float(files_per_worker)))
    return max(1, min(int(project_count or 1), platform_cap, load_workers))


def atlas_project_worker_count(
    project_count: int,
    *,
    total_files: int = 0,
    policy: dict[str, Any] | None = None,
    cpu_count: int | None = None,
) -> int:
    effective_policy = policy if isinstance(policy, dict) else load_workload_policy()
    atlas = effective_policy.get("atlas", {}) if isinstance(effective_policy.get("atlas"), dict) else {}
    config = atlas.get("project_workers", {}) if isinstance(atlas.get("project_workers"), dict) else {}
    if not bool(config.get("enabled_by_default", False)):
        return 1
    projects = max(1, int(project_count or 1))
    if projects < max(1, int(config.get("min_projects_for_parallel", 2) or 2)):
        return 1
    platform_cap = int(config.get("windows_max_workers" if os.name == "nt" else "default_max_workers", 1) or 1)
    files_per_worker = max(1, int(config.get("files_per_worker", 2500) or 2500))
    cpu_reserve = max(0, int(config.get("cpu_reserve", 0) or 0))
    detected_cpu = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or platform_cap)))
    cpu_capacity = max(1, detected_cpu - cpu_reserve)
    load_workers = max(1, math.ceil(max(0, int(total_files or 0)) / float(files_per_worker)))
    return max(1, min(projects, platform_cap, cpu_capacity, load_workers))


def ast_batch_strategy(
    file_count: int,
    atlas_project_workers: int = 1,
    *,
    policy: dict[str, Any] | None = None,
    cpu_count: int | None = None,
    force_legacy: bool = False,
    env_chunk_size: int | None = None,
    env_workers: int | None = None,
    allow_unsafe: bool = False,
) -> dict[str, Any]:
    effective_policy = policy if isinstance(policy, dict) else load_workload_policy()
    atlas = effective_policy.get("atlas", {}) if isinstance(effective_policy.get("atlas"), dict) else {}
    config = atlas.get("ast_batch", {}) if isinstance(atlas.get("ast_batch"), dict) else {}
    adaptive_enabled = bool(config.get("adaptive_by_default", True)) and not force_legacy
    safe_cap = max(1, int(config.get("safe_worker_cap", 2) or 2))

    if env_chunk_size and int(env_chunk_size) > 0:
        chunk_size = int(env_chunk_size)
    elif not adaptive_enabled:
        chunk_size = int(config.get("default_chunk_size", 24) or 24)
    else:
        count = max(0, int(file_count or 0))
        if count >= int(config.get("large_file_threshold", 1200) or 1200):
            chunk_size = int(config.get("large_chunk_size", 12) or 12)
        elif count >= int(config.get("medium_file_threshold", 800) or 800):
            chunk_size = int(config.get("medium_chunk_size", 14) or 14)
        elif count >= int(config.get("small_file_threshold", 400) or 400):
            chunk_size = int(config.get("small_chunk_size", 18) or 18)
        elif count >= int(config.get("tiny_file_threshold", 160) or 160):
            chunk_size = int(config.get("tiny_chunk_size", 20) or 20)
        else:
            chunk_size = int(config.get("default_chunk_size", 24) or 24)

    if env_workers and int(env_workers) > 0:
        requested_workers = int(env_workers)
        effective_workers = requested_workers if allow_unsafe else min(requested_workers, safe_cap)
        workers = max(1, effective_workers)
        clamped_worker_override = requested_workers != workers
    elif not adaptive_enabled:
        if atlas_project_workers > 1:
            workers = 1
        else:
            detected_cpu = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 2)))
            workers = min(6, max(1, detected_cpu // 2))
        clamped_worker_override = False
    else:
        detected_cpu = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 2)))
        cpu_reserve = max(0, int(config.get("cpu_reserve", 0) or 0))
        cpu_capacity = max(1, detected_cpu - cpu_reserve)
        if atlas_project_workers > 1:
            workers = 1
        else:
            count = max(0, int(file_count or 0))
            if count >= int(config.get("large_file_threshold", 1200) or 1200):
                divisor = max(1, int(config.get("large_cpu_divisor", 6) or 6))
                workers = min(safe_cap, max(1, cpu_capacity // divisor))
            elif count >= int(config.get("medium_file_threshold", 800) or 800):
                divisor = max(1, int(config.get("medium_cpu_divisor", 5) or 5))
                workers = min(safe_cap, max(1, cpu_capacity // divisor))
            elif count >= int(config.get("small_file_threshold", 400) or 400):
                divisor = max(1, int(config.get("small_cpu_divisor", 4) or 4))
                workers = min(safe_cap, max(1, cpu_capacity // divisor))
            elif count >= int(config.get("tiny_file_threshold", 160) or 160):
                divisor = max(1, int(config.get("tiny_cpu_divisor", 4) or 4))
                workers = min(safe_cap, max(1, cpu_capacity // divisor))
            else:
                workers = 1
        clamped_worker_override = False

    return {
        "chunk_size": max(1, int(chunk_size)),
        "workers": max(1, int(workers)),
        "legacy_mode": bool(force_legacy),
        "adaptive_mode": bool(adaptive_enabled),
        "env_chunk_override": bool(env_chunk_size),
        "env_worker_override": bool(env_workers),
        "worker_override_clamped": bool(clamped_worker_override),
        "safe_worker_cap": int(safe_cap),
    }


def pipeline_worker_count(policy: dict[str, Any] | None = None, cpu_count: int | None = None) -> int:
    effective_policy = policy if isinstance(policy, dict) else load_workload_policy()
    config = (
        effective_policy.get("pipeline_workers", {})
        if isinstance(effective_policy.get("pipeline_workers"), dict)
        else {}
    )
    platform_cap = int(config.get("windows_max_workers" if os.name == "nt" else "default_max_workers", 4) or 4)
    min_workers = max(1, int(config.get("min_workers", 1) or 1))
    cpu_reserve = max(0, int(config.get("cpu_reserve", 0) or 0))
    detected_cpu = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or platform_cap)))
    cpu_capacity = max(1, detected_cpu - cpu_reserve)
    requested = max(min_workers, cpu_capacity)
    return max(1, min(platform_cap, detected_cpu, requested))


def allow_oracle_npx_fallback(policy: dict[str, Any] | None = None) -> bool:
    effective_policy = policy if isinstance(policy, dict) else load_workload_policy()
    oracle = effective_policy.get("oracle", {}) if isinstance(effective_policy.get("oracle"), dict) else {}
    return bool(oracle.get("allow_npx_fallback", False))

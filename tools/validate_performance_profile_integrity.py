from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.core.pipeline_registry import load_pipeline_execution_policy, normalize_step_slug
from tools.core.workload_profile import build_workload_profile, oracle_profiles
from tools.orchestrators.orchestrator import (
    apply_execution_profile,
    build_step_catalog,
    execution_profile,
    select_steps_smart,
    should_include_step,
)


def _args(**overrides: Any) -> SimpleNamespace:
    defaults = {
        "step": None,
        "from_step": None,
        "skip_audit": False,
        "full": True,
        "force": False,
        "projects": None,
        "scope": None,
        "ai_context": False,
        "smart_trigger": True,
        "watchdog_profile": "live",
        "profile": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _names(steps: list[dict[str, Any]]) -> set[str]:
    return {str(step.get("name")) for step in steps}


def _check(name: str, passed: bool, details: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "details": details,
        "evidence": evidence,
    }


def _policy_keep_slugs(policy: dict[str, Any], profile: str) -> set[str]:
    profiles = policy.get("execution_profiles", {}) if isinstance(policy.get("execution_profiles"), dict) else {}
    profile_config = profiles.get(profile, {}) if isinstance(profiles.get(profile), dict) else {}
    keep = profile_config.get("keep_slugs", []) if isinstance(profile_config.get("keep_slugs"), list) else []
    return {normalize_step_slug(item) for item in keep if str(item).strip()}


def _policy_watchdog_skipped_slugs(policy: dict[str, Any]) -> set[str]:
    guidance = policy.get("step_profile_guidance", {}) if isinstance(policy.get("step_profile_guidance"), dict) else {}
    skipped: set[str] = set()
    for slug, config in guidance.items():
        if not isinstance(config, dict):
            continue
        behavior = str(config.get("watchdog_behavior", config.get("daily_behavior", "kept")) or "kept").strip().lower()
        if behavior in {"skipped", "release_deep_only"}:
            skipped.add(normalize_step_slug(slug))
    return skipped


def _step_names_by_slug(names: set[str], slug: str) -> set[str]:
    target = normalize_step_slug(slug)
    return {name for name in names if normalize_step_slug(name) == target}


def _dependency_gaps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_names = _names(steps)
    gaps = []
    for step in steps:
        missing = sorted(
            str(dependency)
            for dependency in step.get("depends_on", []) or []
            if str(dependency) not in selected_names
        )
        if missing:
            gaps.append(
                {
                    "consumer": str(step.get("name") or ""),
                    "missing_producers": missing,
                }
            )
    return gaps


def run_validation() -> dict[str, Any]:
    execution_policy = load_pipeline_execution_policy()
    full_args = _args(profile="full")
    catalog = build_step_catalog(full_args, stale_projects=["MAIN"], changed_files=None)
    selected = [step for step in catalog if should_include_step(step, full_args)]
    selected_names = _names(selected)

    daily_args = _args(profile="daily")
    daily_steps = apply_execution_profile(selected, daily_args)
    daily_names = _names(daily_steps)
    daily_slugs = {normalize_step_slug(name) for name in daily_names}
    daily_dependency_gaps = _dependency_gaps(daily_steps)
    policy_daily_keep_slugs = _policy_keep_slugs(execution_policy, "daily")
    forbidden_daily_slugs = {
        normalize_step_slug(item)
        for item in (
            execution_policy.get("vocabularies", {}).get("daily_forbidden_heavy_slugs", [])
            if isinstance(execution_policy.get("vocabularies"), dict)
            else []
        )
    }
    vocabularies = execution_policy.get("vocabularies", {}) if isinstance(execution_policy.get("vocabularies"), dict) else {}
    oracle_slug = normalize_step_slug(vocabularies.get("oracle_validation_slug", ""))
    guidance = execution_policy.get("step_profile_guidance", {}) if isinstance(execution_policy.get("step_profile_guidance"), dict) else {}
    oracle_guidance = guidance.get(oracle_slug, {}) if isinstance(guidance.get(oracle_slug), dict) else {}
    oracle_preferred_profiles = {
        str(profile).strip().lower()
        for profile in oracle_guidance.get("preferred_profiles", [])
        if str(profile).strip()
    }

    release_args = _args(profile="release-deep")
    release_catalog = build_step_catalog(release_args, stale_projects=["MAIN"], changed_files=None)
    release_selected = [step for step in release_catalog if should_include_step(step, release_args)]
    release_steps = apply_execution_profile(release_selected, release_args)
    release_names = _names(release_steps)
    selected_oracle_names = _step_names_by_slug(selected_names, oracle_slug)
    release_oracle_names = _step_names_by_slug(release_names, oracle_slug)

    force_args = _args(profile=None, force=True)
    default_args = _args(profile=None, force=False)

    live_args = _args(profile="daily", watchdog_profile="live")
    live_steps = select_steps_smart(
        catalog,
        live_args,
        changed_files=["MAIN::src/components/Example.tsx"],
        dna_changed_files=["MAIN::src/components/Example.tsx"],
    )
    live_names = _names(live_steps)
    live_slugs = {normalize_step_slug(name) for name in live_names}

    smoke_args = _args(profile="daily", watchdog_profile="smoke")
    smoke_steps = select_steps_smart(
        catalog,
        smoke_args,
        changed_files=["MAIN::src/components/Example.tsx"],
        dna_changed_files=["MAIN::src/components/Example.tsx"],
    )
    smoke_names = _names(smoke_steps)
    policy_watchdog_skipped_slugs = _policy_watchdog_skipped_slugs(execution_policy)

    atlas = load_atlas_data()
    commit = load_json_file(RAW_DIR / "atlas_commit.json", {})
    workload = load_json_file(RAW_DIR / "workload_profile.json", {})
    atlas_generated = bool(atlas)
    expected_workload = build_workload_profile(
        atlas if isinstance(atlas, dict) else {},
        snapshot_id=str(commit.get("snapshot_id") or "") if isinstance(commit, dict) else "",
        execution_profile=str(workload.get("execution_profile") or "unknown") if isinstance(workload, dict) else "unknown",
    )

    checks = [
        _check(
            "default_profile_is_full_and_force_is_release_deep",
            execution_profile(default_args) == "full" and execution_profile(force_args) == "release-deep",
            "Default runs should use full profile; force runs should upgrade to release-deep.",
            {
                "default": execution_profile(default_args),
                "force": execution_profile(force_args),
            },
        ),
        _check(
            "daily_profile_matches_execution_policy_keep_slugs",
            bool(policy_daily_keep_slugs) and daily_slugs == policy_daily_keep_slugs,
            "Daily profile step selection must be loaded from pipeline_execution_policy.execution_profiles.daily.keep_slugs.",
            {
                "missing_from_daily": sorted(policy_daily_keep_slugs - daily_slugs),
                "unexpected_in_daily": sorted(daily_slugs - policy_daily_keep_slugs),
            },
        ),
        _check(
            "daily_profile_excludes_policy_forbidden_heavy_slugs",
            bool(forbidden_daily_slugs) and not forbidden_daily_slugs.intersection(daily_slugs),
            "Daily profile should skip heavy step slugs declared by the execution policy vocabulary.",
            sorted(forbidden_daily_slugs.intersection(daily_slugs)),
        ),
        _check(
            "daily_profile_preserves_direct_producer_closure",
            not daily_dependency_gaps,
            "Every daily consumer must retain each declared direct producer; omitted evidence cannot be interpreted as zero, PASS, FAIL, or NOT_APPLICABLE.",
            daily_dependency_gaps,
        ),
        _check(
            "release_deep_profile_preserves_full_catalog",
            release_names.issuperset(selected_names) and bool(release_oracle_names),
            "Release-deep must preserve full-profile steps and add release-only validation gates.",
            {
                "selected_count": len(selected_names),
                "release_deep_count": len(release_names),
                "missing": sorted(selected_names - release_names),
                "oracle_slug": oracle_slug,
                "release_oracle_names": sorted(release_oracle_names),
            },
        ),
        _check(
            "oracle_validation_is_release_deep_only",
            bool(oracle_slug) and not selected_oracle_names and bool(release_oracle_names),
            "The expensive Sanctuary compiler gate should run only in release-deep or explicit-step execution.",
            {
                "oracle_slug": oracle_slug,
                "full_oracle_names": sorted(selected_oracle_names),
                "release_deep_oracle_names": sorted(release_oracle_names),
            },
        ),
        _check(
            "oracle_profiles_are_policy_backed",
            bool(oracle_preferred_profiles) and oracle_preferred_profiles.issubset(oracle_profiles()),
            "Oracle execution profiles must align pipeline execution guidance with workload policy rather than a validator-local literal.",
            {
                "oracle_slug": oracle_slug,
                "pipeline_preferred_profiles": sorted(oracle_preferred_profiles),
                "workload_oracle_profiles": sorted(oracle_profiles()),
            },
        ),
        _check(
            "workload_profile_matches_atlas_snapshot",
            (
                not atlas_generated
                and not workload
            )
            or (
                bool(workload)
                and workload.get("snapshot_id") == expected_workload.get("snapshot_id")
                and workload.get("counts") == expected_workload.get("counts")
                and workload.get("band") == expected_workload.get("band")
                and workload.get("budgets") == expected_workload.get("budgets")
            ),
            "Workload budgets and scheduler inputs must describe the committed Atlas snapshot; clean installs may start without generated artifacts.",
            {
                "atlas_generated": atlas_generated,
                "actual": workload,
                "expected": expected_workload,
            },
        ),
        _check(
            "watchdog_live_profile_keeps_surgical_focus_only",
            bool(policy_watchdog_skipped_slugs) and not policy_watchdog_skipped_slugs.intersection(live_slugs),
            "Live watchdog pulse should not include steps explicitly skipped by step_profile_guidance watchdog behavior.",
            {
                "unexpected_in_live": sorted(policy_watchdog_skipped_slugs.intersection(live_slugs)),
                "live": sorted(live_names),
            },
        ),
        _check(
            "watchdog_smoke_profile_matches_live_surgical_contract",
            smoke_names == live_names,
            "Smoke watchdog profile should exercise the same surgical selection contract without requiring a long-running watcher.",
            {
                "live": sorted(live_names),
                "smoke": sorted(smoke_names),
            },
        ),
    ]

    payload = {
        "meta": {"kind": "performance_profile_integrity_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
        "profiles": {
            "full_count": len(selected_names),
            "daily_count": len(daily_names),
            "release_deep_count": len(release_names),
            "watchdog_live_steps": sorted(live_names),
        },
    }
    save_json_atomic(RAW_DIR / "performance_profile_integrity_validation.json", payload)

    lines = [
        "# Performance Profile Integrity Validation",
        "",
        f"- total_checks: `{payload['summary']['total_checks']}`",
        f"- passed_checks: `{payload['summary']['passed_checks']}`",
        f"- failed_checks: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |")
    save_text_atomic(REPORTS_DIR / "performance_profile_integrity_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

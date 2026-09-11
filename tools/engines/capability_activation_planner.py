from __future__ import annotations

import json
import fnmatch
from datetime import datetime, timezone
from typing import Any

from tools.core.architecture_blueprints import (
    load_effective_architecture_policy_context,
    resolve_effective_architecture_project,
)
from tools.core.capability_registry import load_capability_registry, summarize_capabilities
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.roadmap_phase_registry import activation_planning_window
from tools.core.language_registry import config_file_marker_map
from tools.engines.project_dna_profiler import (
    build_project_dna_profile,
    render_report as render_project_dna_report,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _detected_signal_ids(project: dict[str, Any]) -> set[str]:
    signals: set[str] = set()
    for group_name in ("languages", "frameworks", "infrastructure", "package_managers", "policy_tools"):
        for item in _as_list(project.get(group_name)):
            if isinstance(item, dict) and item.get("id"):
                signals.add(str(item["id"]))
    shape = _as_dict(project.get("repo_shape")).get("id")
    if shape:
        signals.add(str(shape))
    return signals


def _activation_intent_map(project: dict[str, Any]) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for item in _as_list(project.get("activation_intents")):
        if isinstance(item, dict) and item.get("capability"):
            rows[str(item["capability"])] = [str(signal) for signal in _as_list(item.get("matched_signals"))]
    return rows


def _capability_by_id() -> dict[str, dict[str, Any]]:
    summary = summarize_capabilities(load_capability_registry())
    return {
        str(capability.get("id")): capability
        for capability in summary.get("capabilities", [])
        if isinstance(capability, dict) and capability.get("id")
    }


def activation_plan_requires_refresh(changed_files: list[str] | None) -> bool:
    if not changed_files:
        return False
    policy = load_json_object_strict(CONFIG_DIR / "project_dna_profile_policy.json", label="Project DNA profile policy")
    patterns: set[str] = set()
    for group_name in ("package_managers", "framework_config_signals", "dependency_manifest_signals"):
        group = _as_dict(policy.get(group_name))
        for values in group.values():
            patterns.update(str(item).replace("\\", "/") for item in _as_list(values) if str(item).strip())
    for values in config_file_marker_map().values():
        patterns.update(str(item).replace("\\", "/") for item in values if str(item).strip())
    for raw in changed_files:
        relative = str(raw or "").split("::", 1)[-1].replace("\\", "/").strip("/")
        name = relative.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns):
            return True
    return False


def _production_enabled(capability: dict[str, Any], matched_signals: list[str]) -> bool:
    if str(capability.get("maturity")) != "production_candidate":
        return False
    scopes = {str(item) for item in _as_list(capability.get("language_scope"))}
    return "language_agnostic" in scopes or bool(matched_signals)


def _roadmap_plan_allowed(capability: dict[str, Any], matched_signals: list[str]) -> bool:
    if not matched_signals:
        return False
    return (
        str(capability.get("maturity")) == "roadmap"
        and str(capability.get("target_release") or "") in activation_planning_window()
    )


def _refresh_project_dna_profile() -> dict[str, Any]:
    payload = build_project_dna_profile()
    save_json_atomic(RAW_DIR / "project_dna_profile.json", payload)
    save_text_atomic(
        REPORTS_DIR / "project_dna_profile.md",
        render_project_dna_report(payload),
    )
    return payload


def build_capability_activation_plan(*, refresh_dna: bool = False) -> dict[str, Any]:
    dna = load_json_file(RAW_DIR / "project_dna_profile.json", {})
    if refresh_dna or not isinstance(dna, dict) or dna.get("meta", {}).get("kind") != "project_dna_profile":
        dna = _refresh_project_dna_profile()

    capability_map = _capability_by_id()
    effective_architecture_policy, atlas_snapshot_id = (
        load_effective_architecture_policy_context(RAW_DIR)
    )
    rows: list[dict[str, Any]] = []
    enabled_counts: dict[str, int] = {}
    disabled_counts: dict[str, int] = {}
    architecture_status_counts: dict[str, int] = {}

    for project in _as_list(dna.get("projects")):
        if not isinstance(project, dict):
            continue
        intent_map = _activation_intent_map(project)
        detected = sorted(_detected_signal_ids(project))
        enabled: list[dict[str, Any]] = []
        disabled: list[dict[str, Any]] = []
        project_id = str(project.get("project") or "")
        architecture_policy = resolve_effective_architecture_project(
            effective_architecture_policy,
            project_id,
            expected_snapshot_id=atlas_snapshot_id,
        )
        architecture_status = str(
            architecture_policy.get("effective_policy_status") or "UNAVAILABLE"
        )
        architecture_status_counts[architecture_status] = (
            architecture_status_counts.get(architecture_status, 0) + 1
        )

        for capability_id, capability in sorted(capability_map.items()):
            matched = intent_map.get(capability_id, [])
            if _production_enabled(capability, matched):
                reason = "language_agnostic_production_baseline"
                if matched:
                    reason = "production_candidate_with_project_dna_match"
                enabled.append(_activation_row(capability, "enabled", reason, matched))
                enabled_counts[capability_id] = enabled_counts.get(capability_id, 0) + 1
            elif _roadmap_plan_allowed(capability, matched):
                enabled.append(_activation_row(capability, "planned", "roadmap_capability_has_project_dna_match", matched))
                enabled_counts[capability_id] = enabled_counts.get(capability_id, 0) + 1
            else:
                reason = "roadmap_or_language_scoped_capability_without_required_project_dna"
                if str(capability.get("maturity")) == "roadmap" and matched:
                    reason = "roadmap_phase_not_in_activation_window"
                disabled.append(_activation_row(capability, "disabled", reason, matched))
                disabled_counts[capability_id] = disabled_counts.get(capability_id, 0) + 1

        rows.append(
            {
                "project": project.get("project"),
                "display_name": project.get("display_name"),
                "detected_signals": detected,
                "target_policy_feature_flags": _as_dict(project.get("target_policy_feature_flags")),
                "architecture_policy": architecture_policy,
                "enabled_capabilities": enabled,
                "disabled_capabilities": disabled,
            }
        )

    status = "PASS" if rows and capability_map else "FAIL"
    architecture_policy_steps = sorted(
        str(step)
        for step in _as_list(
            _as_dict(capability_map.get("architecture_governance")).get("engines")
        )
        if str(step).strip()
    )
    unavailable_architecture_statuses = {
        "ATLAS_SNAPSHOT_UNAVAILABLE",
        "UNAVAILABLE",
        "PROJECT_UNAVAILABLE",
        "STALE_SNAPSHOT",
    }
    architecture_bound_projects = sum(
        count
        for policy_status, count in architecture_status_counts.items()
        if policy_status not in unavailable_architecture_statuses
    )
    architecture_context_status = (
        "BOUND"
        if rows and architecture_bound_projects == len(rows)
        else "PARTIAL"
        if architecture_bound_projects
        else "UNAVAILABLE"
    )
    payload = {
        "meta": {
            "kind": "capability_activation_plan",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.engines.capability_activation_planner",
            "source_artifacts": [
                "output/.raw/project_dna_profile.json",
                "output/.raw/effective_architecture_policy.json",
                "output/.raw/atlas_commit.json",
                "config/capability_registry.json",
            ],
            "scope": "scheduler_input",
        },
        "summary": {
            "status": status,
            "projects": len(rows),
            "capabilities": len(capability_map),
            "pipeline_scheduler_enforced": True,
            "planning_only": False,
            "scheduler_contract": "capability_filter_v1",
            "architecture_policy_contract": "effective_architecture_policy_project_context_v1",
            "architecture_policy_context_status": architecture_context_status,
            "architecture_policy_status_counts": dict(sorted(architecture_status_counts.items())),
            "architecture_rules_enabled_projects": sorted(
                str(project.get("project") or "")
                for project in rows
                if _as_dict(project.get("architecture_policy")).get(
                    "architecture_sensitive_rules_enabled"
                )
            ),
            "architecture_policy_step_policy": {
                "always_preserve_steps": architecture_policy_steps,
                "reason": "policy_producer_and_internal_rule_consumers_must_run_when_selected",
                "rule_activation_owner": "exact_project_consumers",
            },
            "disabled_semantics": "no_current_project_dna_signal_not_a_policy_ban",
            "dependency_change_policy": "intentional dependency additions must refresh project DNA and regenerate this plan before relying on capability status",
            "enabled_capability_ids": sorted(enabled_counts),
            "disabled_capability_ids": sorted(disabled_counts),
        },
        "projects": rows,
    }
    return payload


def _activation_row(capability: dict[str, Any], status: str, reason: str, matched_signals: list[str]) -> dict[str, Any]:
    return {
        "id": capability.get("id"),
        "title": capability.get("title"),
        "domain": capability.get("domain"),
        "maturity": capability.get("maturity"),
        "introduced_in": capability.get("introduced_in"),
        "target_release": capability.get("target_release"),
        "status": status,
        "reason": reason,
        "matched_signals": sorted(set(matched_signals)),
        "trusted_artifacts": capability.get("artifacts", []),
        "validators": capability.get("validators", []),
        "claim_boundary": capability.get("claim_boundary"),
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = _as_dict(payload.get("summary"))
    lines = [
        "# Capability Activation Plan",
        "",
        "Capability activation projection consumed by the pipeline scheduler for non-release execution profiles.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- projects: `{summary.get('projects')}`",
        f"- capabilities: `{summary.get('capabilities')}`",
        f"- pipeline_scheduler_enforced: `{summary.get('pipeline_scheduler_enforced')}`",
        f"- architecture_policy_context_status: `{summary.get('architecture_policy_context_status')}`",
        "",
        "| Project | Architecture policy | Profile | Architecture rules | Enabled/Planned | Disabled | Signals |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for project in _as_list(payload.get("projects")):
        enabled = len(_as_list(project.get("enabled_capabilities")))
        disabled = len(_as_list(project.get("disabled_capabilities")))
        signals = ", ".join(str(item) for item in _as_list(project.get("detected_signals"))[:16])
        architecture_policy = _as_dict(project.get("architecture_policy"))
        lines.append(
            f"| `{project.get('project')}` | `{architecture_policy.get('effective_policy_status')}` | "
            f"`{architecture_policy.get('recommended_profile')}` | "
            f"`{architecture_policy.get('architecture_sensitive_rules_enabled')}` | "
            f"{enabled} | {disabled} | `{signals}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Daily and normal full profiles may skip capability-bound steps when no project DNA signal enables them.",
            "- Explicit steps, forced runs and release-deep runs bypass capability filtering.",
            "- `disabled` means no current Project DNA signal was found. It is not a dependency or library ban.",
            "- If a task intentionally adds a new library/framework, refresh Project DNA and regenerate this plan before relying on the updated capability status.",
            "- Production-candidate capabilities remain enabled as the safety baseline.",
            "- Roadmap capabilities are only marked `planned` when matching project DNA exists and the target phase is inside the near-term activation window.",
            "- Architecture Oracle and its universal/internal-gating consumers remain schedulable when selected; exact project policy activates rules inside those consumers, not by deleting the producer step.",
        ]
    )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_capability_activation_plan()
    save_json_atomic(RAW_DIR / "capability_activation_plan.json", payload)
    save_text_atomic(REPORTS_DIR / "capability_activation_plan.md", render_report(payload))
    return payload


if __name__ == "__main__":
    print(json.dumps(run().get("summary", {}), ensure_ascii=False))

from __future__ import annotations

import json
import fnmatch
from datetime import datetime, timezone
from typing import Any

from tools.core.capability_registry import load_capability_registry, summarize_capabilities
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.roadmap_phase_registry import activation_planning_window
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
    for group_name in ("languages", "frameworks", "infrastructure", "package_managers"):
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
    rows: list[dict[str, Any]] = []
    enabled_counts: dict[str, int] = {}
    disabled_counts: dict[str, int] = {}

    for project in _as_list(dna.get("projects")):
        if not isinstance(project, dict):
            continue
        intent_map = _activation_intent_map(project)
        detected = sorted(_detected_signal_ids(project))
        enabled: list[dict[str, Any]] = []
        disabled: list[dict[str, Any]] = []

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
                "enabled_capabilities": enabled,
                "disabled_capabilities": disabled,
            }
        )

    status = "PASS" if rows and capability_map else "FAIL"
    payload = {
        "meta": {
            "kind": "capability_activation_plan",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.engines.capability_activation_planner",
            "source_artifacts": ["output/.raw/project_dna_profile.json", "config/capability_registry.json"],
            "scope": "scheduler_input",
        },
        "summary": {
            "status": status,
            "projects": len(rows),
            "capabilities": len(capability_map),
            "pipeline_scheduler_enforced": True,
            "planning_only": False,
            "scheduler_contract": "capability_filter_v1",
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
        "",
        "| Project | Enabled/Planned | Disabled | Signals |",
        "|---|---:|---:|---|",
    ]
    for project in _as_list(payload.get("projects")):
        enabled = len(_as_list(project.get("enabled_capabilities")))
        disabled = len(_as_list(project.get("disabled_capabilities")))
        signals = ", ".join(str(item) for item in _as_list(project.get("detected_signals"))[:16])
        lines.append(f"| `{project.get('project')}` | {enabled} | {disabled} | `{signals}` |")
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

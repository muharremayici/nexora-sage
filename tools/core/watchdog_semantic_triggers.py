from __future__ import annotations

from typing import Any

from tools.core.atlas_io import load_atlas_data
from tools.core.pipeline_registry import load_pipeline_execution_policy


def load_watchdog_semantic_trigger_rule(rule_id: str) -> dict[str, Any]:
    policy = load_pipeline_execution_policy()
    rules = policy.get("watchdog_semantic_trigger_rules")
    if not isinstance(rules, dict):
        raise ValueError("pipeline execution policy is missing watchdog_semantic_trigger_rules")
    rule = rules.get(str(rule_id or "").strip())
    if not isinstance(rule, dict):
        raise ValueError(f"unknown watchdog semantic trigger rule: {rule_id}")
    if not rule.get("step_set") or not rule.get("unknown_behavior"):
        raise ValueError(f"watchdog semantic trigger rule is incomplete: {rule_id}")
    return rule


def evaluate_watchdog_semantic_trigger(
    rule_id: str,
    changed_files: list[str] | None,
    *,
    atlas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rule = load_watchdog_semantic_trigger_rule(rule_id)
    unknown_keeps = str(rule.get("unknown_behavior")) == "keep_for_safety"
    result_identity = {"rule_id": rule_id, "step_set": str(rule["step_set"])}
    files = [str(item) for item in (changed_files or []) if str(item).strip()]
    if not files:
        return {**result_identity, "relevant": False, "reason": "no_changed_files", "unknown_files": []}

    atlas_payload = load_atlas_data() if atlas is None else atlas
    if not isinstance(atlas_payload, dict):
        return {
            **result_identity,
            "relevant": unknown_keeps,
            "reason": "atlas_not_available",
            "unknown_files": files,
        }

    truthy_fields = [str(item) for item in rule.get("truthy_object_fields", []) if str(item).strip()]
    feature_prefixes = [str(item) for item in rule.get("feature_prefixes", []) if str(item)]
    unknown_files: list[str] = []
    for target_ref in files:
        if "::" not in target_ref:
            unknown_files.append(target_ref)
            continue
        project_key, rel_path = target_ref.split("::", 1)
        project = atlas_payload.get(project_key)
        project_files = project.get("files") if isinstance(project, dict) else None
        meta = project_files.get(rel_path) if isinstance(project_files, dict) else None
        if not isinstance(meta, dict):
            unknown_files.append(target_ref)
            continue
        if any(isinstance(meta.get(field), dict) and any(meta[field].values()) for field in truthy_fields):
            return {
                **result_identity,
                "relevant": True,
                "reason": "truthy_atlas_field",
                "matched_file": target_ref,
                "unknown_files": unknown_files,
            }
        features = [str(feature or "") for feature in meta.get("features", []) or []]
        if any(feature.startswith(prefix) for feature in features for prefix in feature_prefixes):
            return {
                **result_identity,
                "relevant": True,
                "reason": "atlas_feature_prefix",
                "matched_file": target_ref,
                "unknown_files": unknown_files,
            }

    if unknown_files and unknown_keeps:
        return {
            **result_identity,
            "relevant": True,
            "reason": "unknown_file_kept_for_safety",
            "unknown_files": unknown_files,
        }
    return {
        **result_identity,
        "relevant": False,
        "reason": "no_semantic_signal",
        "unknown_files": unknown_files,
    }

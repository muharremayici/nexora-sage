from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


WAVE_REGISTRY_PATH = CONFIG_DIR / "sage_execution_wave_registry.json"


def load_execution_wave_registry() -> dict[str, Any]:
    return load_json_object_strict(WAVE_REGISTRY_PATH, label="SAGE execution wave registry")


def release_scope_matches_wave(
    wave: dict[str, Any],
    work_items: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    known_releases: set[str],
    current_release: str,
) -> tuple[bool, dict[str, Any]]:
    """Verify finite wave delivery scope without conflating it with dependency order."""
    scope = wave.get("release_scope") if isinstance(wave.get("release_scope"), dict) else {}
    kind = str(scope.get("kind") or "")
    declared_releases = {str(item) for item in scope.get("target_releases", []) if str(item)}
    item_ids = [str(item) for item in wave.get("work_item_ids", []) if str(item)]
    item_releases = {
        str(work_items[item_id].get("target_release") or "")
        for item_id in item_ids
        if item_id in work_items
    }
    allowed_kinds = {str(item) for item in policy.get("allowed_kinds", []) if str(item)}
    closure_kind = str(policy.get("release_closure_kind") or "")
    requires_active_release = policy.get("release_closure_requires_active_product_release") is True
    closure_valid = (
        kind != closure_kind
        or (
            not item_ids
            and (not requires_active_release or declared_releases == {current_release})
        )
    )
    valid = (
        kind in allowed_kinds
        and bool(declared_releases)
        and declared_releases.issubset(known_releases)
        and item_releases.issubset(declared_releases)
        and closure_valid
    )
    return valid, {
        "wave": str(wave.get("id") or ""),
        "scope_kind": kind,
        "declared_target_releases": sorted(declared_releases),
        "work_item_target_releases": sorted(item_releases),
        "current_product_release": current_release,
        "unknown_declared_releases": sorted(declared_releases - known_releases),
        "outside_scope_work_item_releases": sorted(item_releases - declared_releases),
        "closure_scope_valid": closure_valid,
    }


def project_execution_waves(
    work_items: list[dict[str, Any]],
    *,
    active_package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = load_execution_wave_registry()
    status_by_id = {
        str(row.get("id") or ""): str(row.get("status") or "")
        for row in work_items
        if isinstance(row, dict) and row.get("id")
    }
    item_by_id = {
        str(row.get("id") or ""): row
        for row in work_items
        if isinstance(row, dict) and row.get("id")
    }
    rows: list[dict[str, Any]] = []
    next_open_delivery_wave = ""
    wave_ids = {
        str(wave.get("id") or "")
        for wave in registry.get("waves", [])
        if isinstance(wave, dict)
    }
    active_wave = (
        str(active_package.get("execution_wave") or "")
        if isinstance(active_package, dict) and str(active_package.get("status") or "") == "in_progress"
        else ""
    )
    if active_wave not in wave_ids:
        active_wave = ""
    for wave in registry.get("waves", []):
        if not isinstance(wave, dict):
            continue
        item_ids = [str(item) for item in wave.get("work_item_ids", [])]
        open_ids = [item for item in item_ids if status_by_id.get(item) != "closed"]
        if open_ids and not next_open_delivery_wave:
            next_open_delivery_wave = str(wave.get("id") or "")
        rows.append(
            {
                "id": wave.get("id"),
                "title": wave.get("title"),
                "computed_status": "complete" if item_ids and not open_ids else "planned",
                "blocked_by": wave.get("blocked_by", []),
                "objective": wave.get("objective"),
                "release_scope": wave.get("release_scope", {}),
                "work_items": len(item_ids),
                "open_work_items": len(open_ids),
                "open_work_item_ids": open_ids,
                "next_actions": [
                    {
                        "id": item_id,
                        "priority": item_by_id.get(item_id, {}).get("priority"),
                        "target_release": item_by_id.get(item_id, {}).get("target_release"),
                        "next_action": item_by_id.get(item_id, {}).get("next_action"),
                    }
                    for item_id in open_ids[:5]
                ],
                "capability_ids": wave.get("capability_ids", []),
            }
        )
    current_wave = active_wave or next_open_delivery_wave
    for row in rows:
        if row["id"] == current_wave:
            row["computed_status"] = "in_progress"
        elif row["open_work_items"] and row["computed_status"] != "in_progress":
            row["computed_status"] = "blocked"
    return {
        "meta": {"kind": "sage_execution_wave_projection", "version": "v2"},
        "current_wave": current_wave,
        "next_open_delivery_wave": next_open_delivery_wave,
        "selection_basis": "active_work_package" if active_wave else "first_open_delivery_wave",
        "waves": rows,
        "source": "config/sage_execution_wave_registry.json",
        "detail_source": "config/sage_work_item_registry.json",
    }

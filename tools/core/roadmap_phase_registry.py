from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


PHASE_REGISTRY_PATH = CONFIG_DIR / "roadmap_phase_registry.json"


def load_roadmap_phase_registry() -> dict[str, Any]:
    return load_json_object_strict(PHASE_REGISTRY_PATH, label="Roadmap phase registry")


def roadmap_phase_rows(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    rows = doc.get("phases", []) if isinstance(doc, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def allowed_roadmap_releases(registry: dict[str, Any] | None = None) -> set[str]:
    return {
        str(row.get("release") or "")
        for row in roadmap_phase_rows(registry)
        if row.get("status") == "roadmap" and str(row.get("release") or "").strip()
    }


def current_product_release(registry: dict[str, Any] | None = None) -> str:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    value = doc.get("current_product_release", "") if isinstance(doc, dict) else ""
    return str(value).strip()


def active_phase(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = [
        row
        for row in roadmap_phase_rows(registry)
        if row.get("status") == "active"
    ]
    return rows[0] if len(rows) == 1 else {}


def phases_by_capability_domain(registry: dict[str, Any] | None = None, domain: str = "") -> set[str]:
    needle = str(domain).strip().lower()
    if not needle:
        return set()
    return {
        str(row.get("release") or "")
        for row in roadmap_phase_rows(registry)
        if any(
            needle in str(row.get(field) or "").lower()
            for field in ("capability_domain", "title", "claim_profile_id")
        )
        and str(row.get("release") or "").strip()
    }


def required_roadmap_profile_ids(registry: dict[str, Any] | None = None) -> set[str]:
    return {
        str(row.get("claim_profile_id") or "")
        for row in roadmap_phase_rows(registry)
        if row.get("status") == "roadmap"
        and row.get("release_claim_profile_required") is True
        and str(row.get("claim_profile_id") or "").strip()
    }


def activation_planning_window(registry: dict[str, Any] | None = None) -> set[str]:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    values = doc.get("activation_planning_window", []) if isinstance(doc, dict) else []
    return {str(value) for value in values if str(value).strip()}

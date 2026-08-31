from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR
from tools.core.json_io import load_json_file, load_json_object_strict


def current_release_version() -> str:
    validation = load_json_file(RAW_DIR / "release_identity_validation.json", {})
    if isinstance(validation, dict):
        summary = validation.get("summary", {})
        if isinstance(summary, dict) and summary.get("product_version"):
            return str(summary["product_version"])

    identity = load_json_object_strict(CONFIG_DIR / "release_identity.json", label="Release identity")
    product = identity.get("product", {})
    if isinstance(product, dict) and product.get("public_version"):
        return str(product["public_version"])
    return "unknown"


def release_agent_surface_human_seal_scope(version: str | None = None) -> str:
    release_version = str(version or current_release_version()).strip() or "unknown"
    return f"v{release_version}_agent_surface_human_seal"


def release_agent_surface_human_seal_gate() -> str:
    return "architecture_doctrine_seal"


def release_human_seal_context(version: str | None = None) -> dict[str, Any]:
    release_version = str(version or current_release_version()).strip() or "unknown"
    return {
        "release_version": release_version,
        "gate": release_agent_surface_human_seal_gate(),
        "scope": release_agent_surface_human_seal_scope(release_version),
    }

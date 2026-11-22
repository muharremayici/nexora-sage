"""Source inventory and large-source maintenance checks shared by proof and preparation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_object_strict


ROOT = Path(__file__).resolve().parents[2]


def _policy_list(raw: Any) -> list[str]:
    return [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []


def source_inventory_policy(policy: dict[str, Any]) -> dict[str, Any]:
    raw = policy.get("source_inventory", {})
    return raw if isinstance(raw, dict) else {}


def large_source_allowlist(policy: dict[str, Any]) -> dict[str, str]:
    raw = policy.get("large_source_allowlist", {})
    return {str(key): str(value) for key, value in raw.items() if str(key).strip()} if isinstance(raw, dict) else {}


def iter_source_files(policy: dict[str, Any], *, root: Path = ROOT) -> list[Path]:
    inventory_policy = source_inventory_policy(policy)
    ignored_parts = set(_policy_list(inventory_policy.get("ignored_path_parts")))
    source_suffixes = _policy_list(inventory_policy.get("source_suffixes"))
    files: list[Path] = []
    for suffix in source_suffixes:
        glob_pattern = f"*{suffix}" if suffix.startswith(".") else suffix
        for path in root.rglob(glob_pattern):
            if any(part in ignored_parts for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def large_source_inventory(policy: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    threshold = int(source_inventory_policy(policy).get("large_source_line_threshold") or 0)
    allowlist = large_source_allowlist(policy)
    large_files = []
    for path in iter_source_files(policy, root=root):
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        lines = len(text.splitlines())
        if lines < threshold:
            continue
        relative = path.relative_to(root).as_posix()
        large_files.append({"file": relative, "lines": lines, "rationale": allowlist.get(relative)})
    return {
        "threshold": threshold,
        "large_files": large_files,
        "unmanaged_large_files": [item for item in large_files if item["file"] not in allowlist],
    }


def require_managed_large_sources(root: Path) -> None:
    policy = load_json_object_strict(root / "config/source_contract_policy.json", label="Source contract policy")
    inventory = source_inventory_policy(policy)
    threshold = inventory.get("large_source_line_threshold")
    if (type(threshold) is not int or threshold <= 0
            or not _policy_list(inventory.get("source_suffixes"))
            or not isinstance(inventory.get("ignored_path_parts"), list)
            or not isinstance(policy.get("large_source_allowlist"), dict)):
        raise ValueError("source_maintainability_policy_invalid")
    unmanaged = large_source_inventory(policy, root=root)["unmanaged_large_files"]
    if unmanaged:
        sample = ", ".join(f"{item['file']} ({item['lines']} lines)" for item in unmanaged[:8])
        raise ValueError(f"source_maintainability_unmanaged_large_files: {len(unmanaged)} at threshold {threshold}: {sample}")

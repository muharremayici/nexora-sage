from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from tools.core.path_engine import to_posix_path
from tools.core.path_identity import strip_current_directory_prefix


PACKAGE_ENTRY_FIELDS = (
    "exports",
    "main",
    "module",
    "browser",
    "types",
    "typings",
    "bin",
    "typesVersions",
    "sideEffects",
)


def _string_values(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [item for value in node.values() for item in _string_values(value)]
    if isinstance(node, list):
        return [item for value in node for item in _string_values(value)]
    return []


def build_package_public_contracts(project_root: Path, manifest_paths: Iterable[Path]) -> dict[str, Any]:
    root = Path(project_root).resolve()
    packages: list[dict[str, Any]] = []
    aggregate_patterns: set[str] = set()
    for manifest_path in sorted({Path(path).resolve() for path in manifest_paths}, key=lambda item: item.as_posix()):
        try:
            manifest_path.relative_to(root)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        package_dir = manifest_path.parent
        package_rel = to_posix_path(os.path.relpath(package_dir, root))
        package_rel = "" if package_rel == "." else package_rel
        patterns: set[str] = set()
        for field in PACKAGE_ENTRY_FIELDS:
            for raw_value in _string_values(payload.get(field)):
                candidate = str(raw_value or "").strip()
                if not candidate or candidate.startswith("#") or "://" in candidate:
                    continue
                candidate = candidate[2:] if candidate.startswith("./") else candidate
                candidate = to_posix_path(candidate)
                if not candidate or candidate.startswith("../"):
                    continue
                combined = strip_current_directory_prefix(
                    to_posix_path(f"{package_rel}/{candidate}" if package_rel else candidate)
                )
                if combined:
                    patterns.add(combined)
                    aggregate_patterns.add(combined)
        packages.append(
            {
                "manifest": to_posix_path(os.path.relpath(manifest_path, root)),
                "package_root": package_rel,
                "name": str(payload.get("name") or ""),
                "entry_patterns": sorted(patterns),
            }
        )
    return {
        "source": "package_manifests",
        "entry_patterns": sorted(aggregate_patterns),
        "packages": packages,
    }

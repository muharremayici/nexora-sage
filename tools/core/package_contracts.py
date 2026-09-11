from __future__ import annotations

import json
import os
import re
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


def _existing_source_entry_patterns(package_dir: Path, package_rel: str, exports: Any) -> set[str]:
    """Project package-export keys onto source files when physical evidence exists.

    Published targets commonly point at generated distribution files while the
    analyzed checkout contains source files. Export keys express the public
    subpath contract without guessing a package name.
    """
    if not isinstance(exports, dict):
        return set()

    patterns: set[str] = set()

    def scoped(value: str) -> str:
        return strip_current_directory_prefix(
            to_posix_path(f"{package_rel}/{value}" if package_rel else value)
        )

    source_extensions = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".java", ".cs")
    for raw_key in exports:
        key = str(raw_key or "").strip()
        if key == ".":
            for relative in ("src/index", "index"):
                if any((package_dir / f"{relative}{extension}").is_file() for extension in source_extensions):
                    patterns.add(scoped(f"{relative}.*"))
            continue
        if not key.startswith("./"):
            continue
        subpath = key[2:]
        if "*" in subpath:
            for prefix in ("src", ""):
                relative_pattern = f"{prefix}/{subpath}".strip("/")
                if any(package_dir.glob(f"{relative_pattern}{extension}") for extension in source_extensions):
                    patterns.add(scoped(f"{relative_pattern}.*"))
            continue
        for prefix in ("src", ""):
            relative = f"{prefix}/{subpath}".strip("/")
            if any((package_dir / f"{relative}{extension}").is_file() for extension in source_extensions):
                patterns.add(scoped(f"{relative}.*"))
    return patterns


def _existing_compiled_entry_source_patterns(
    package_dir: Path,
    package_rel: str,
    manifest: dict[str, Any],
) -> set[str]:
    """Map manifest entry values to physical source files only when they exist."""
    patterns: set[str] = set()
    source_extensions = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

    def scoped(value: str) -> str:
        return strip_current_directory_prefix(
            to_posix_path(f"{package_rel}/{value}" if package_rel else value)
        )

    for field in ("main", "module", "browser", "bin"):
        for raw_value in _string_values(manifest.get(field)):
            candidate = to_posix_path(str(raw_value or "").strip())
            candidate = candidate[2:] if candidate.startswith("./") else candidate
            if not candidate or "*" in candidate or candidate.startswith("../"):
                continue
            stem = re.sub(r"(?:\.d\.(?:ts|mts|cts)|\.(?:js|jsx|mjs|cjs|ts|tsx))$", "", candidate)
            stem_variants = {stem}
            parts = stem.split("/")
            if parts and parts[0] in {"dist", "build", "lib"}:
                stripped = parts[1:]
                if stripped and stripped[0] in {"dev", "prod", "types"}:
                    stripped = stripped[1:]
                if stripped:
                    stem_variants.add("/".join(stripped))
            for stem_variant in stem_variants:
                for relative in (stem_variant, f"src/{stem_variant}"):
                    for extension in source_extensions:
                        source_path = package_dir / f"{relative}{extension}"
                        if source_path.is_file():
                            patterns.add(scoped(f"{relative}{extension}"))
    return patterns


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
        source_entry_patterns = _existing_source_entry_patterns(
            package_dir,
            package_rel,
            payload.get("exports"),
        )
        source_entry_patterns.update(
            _existing_compiled_entry_source_patterns(package_dir, package_rel, payload)
        )
        patterns.update(source_entry_patterns)
        aggregate_patterns.update(source_entry_patterns)
        packages.append(
            {
                "manifest": to_posix_path(os.path.relpath(manifest_path, root)),
                "package_root": package_rel,
                "name": str(payload.get("name") or ""),
                "entry_patterns": sorted(patterns),
                "source_entry_patterns": sorted(source_entry_patterns),
            }
        )
    return {
        "source": "package_manifests",
        "entry_patterns": sorted(aggregate_patterns),
        "packages": packages,
    }

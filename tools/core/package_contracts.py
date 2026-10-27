from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from typing import Any, Iterable

from tools.core.path_engine import to_posix_path
from tools.core.path_identity import strip_current_directory_prefix
from tools.core.atlas_typescript_inputs import auxiliary_input_policy
from tools.core.language_registry import extensions_for_language, non_source_compound_suffixes


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


def _script_source_extensions() -> tuple[str, ...]:
    return tuple(sorted(set(extensions_for_language("typescript")) | set(extensions_for_language("javascript"))))


def _ordinary_source_file(path: Path, compound_suffixes: tuple[str, ...]) -> bool:
    return not path.name.lower().endswith(compound_suffixes) and path.is_file()


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

    # Only script suffixes change here; preserve existing non-script coverage.
    source_extensions = (*_script_source_extensions(), ".py", ".go", ".java", ".cs")
    compound_suffixes = tuple(non_source_compound_suffixes())
    for raw_key in exports:
        key = str(raw_key or "").strip()
        if key == ".":
            for relative in ("src/index", "index"):
                if any(_ordinary_source_file(package_dir / f"{relative}{extension}", compound_suffixes) for extension in source_extensions):
                    patterns.add(scoped(f"{relative}.*"))
            continue
        if not key.startswith("./"):
            continue
        subpath = key[2:]
        if "*" in subpath:
            for prefix in ("src", ""):
                relative_pattern = f"{prefix}/{subpath}".strip("/")
                if any(_ordinary_source_file(candidate, compound_suffixes) for extension in source_extensions
                       for candidate in package_dir.glob(f"{relative_pattern}{extension}")):
                    patterns.add(scoped(f"{relative_pattern}.*"))
            continue
        for prefix in ("src", ""):
            relative = f"{prefix}/{subpath}".strip("/")
            if any(_ordinary_source_file(package_dir / f"{relative}{extension}", compound_suffixes) for extension in source_extensions):
                patterns.add(scoped(f"{relative}.*"))
    return patterns


def _existing_compiled_entry_source_patterns(
    package_dir: Path,
    package_rel: str,
    manifest: dict[str, Any],
) -> set[str]:
    """Retain physical source candidates, not native resolver winner selection."""
    patterns: set[str] = set()
    source_extensions = _script_source_extensions()
    compound_suffixes = tuple(non_source_compound_suffixes())
    stem_suffixes = sorted(set(source_extensions) | set(compound_suffixes), key=lambda value: (-len(value), value))

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
            stem = next((candidate[:-len(suffix)] for suffix in stem_suffixes if candidate.endswith(suffix)), candidate)
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
                        if _ordinary_source_file(source_path, compound_suffixes):
                            patterns.add(scoped(f"{relative}{extension}"))
    return patterns


def _type_entry_policy() -> tuple[dict[str, Any], str]:
    settings = auxiliary_input_policy()["package_type_entry_evidence"]
    if not isinstance(settings, dict) or settings.get("version") != "v1":
        raise ValueError("Invalid package type-entry evidence policy")
    for key in ("max_nodes", "max_records", "max_depth"):
        if type(settings.get(key)) is not int or settings[key] < 1:
            raise ValueError(f"Invalid package type-entry evidence budget: {key}")
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return settings, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def unavailable_type_entry_evidence(manifest: str, reason: str) -> dict[str, Any]:
    """Do not relabel old manifest observations after an unavailable capture."""
    return {"version": "v1", "status": "unavailable", "source_manifest": manifest,
        "source_hash": None, "hash_basis": "sha256_raw_bytes", "policy_sha256": None,
        "scope": "literal_type_fields_and_explicit_type_conditions", "records": [], "shown_count": 0,
        "omitted_count": None, "reasons": [reason], "runtime_execution": "not_established"}


def _type_entry_evidence(root: Path, manifest: str, data: bytes, payload: dict[str, Any],
                         duplicate_keys: bool, auxiliary: Any, policy: Any) -> dict[str, Any]:
    """Literal observations only; neither package resolution nor schema validity."""
    result = unavailable_type_entry_evidence(manifest, "auxiliary_inputs_unavailable")

    def unavailable(reason: str) -> dict[str, Any]:
        result["reasons"] = [reason]
        return result

    if not isinstance(auxiliary, dict):
        return unavailable("auxiliary_inputs_unavailable")
    if (auxiliary.get("version") != "v1" or auxiliary.get("status") not in {"captured", "partial"}
            or auxiliary.get("scope") != "owned_walk_positive_inputs_only"
            or auxiliary.get("negative_resolution_authority") is not False
            or auxiliary.get("project_root") != root.as_posix()):
        return unavailable("auxiliary_scope_unavailable")
    entries = auxiliary.get("files")
    entry = entries.get(manifest) if isinstance(entries, dict) else None
    if (not isinstance(entry, dict) or entry.get("status") != "ok"
            or entry.get("canonical_file_path") is not True
            or type(entry.get("size_bytes")) is not int or entry["size_bytes"] != len(data)):
        return unavailable("manifest_input_unavailable")
    digest = hashlib.sha256(data).hexdigest()
    if entry.get("raw_sha256") != digest:
        return unavailable("manifest_input_mismatch")
    if duplicate_keys:
        return unavailable("duplicate_manifest_keys")
    settings, policy_identity = policy
    result.update(status="observed", source_hash=digest, policy_sha256=policy_identity, omitted_count=0, reasons=[])
    reasons: set[str] = set()
    records = result["records"]

    def children(node: Any, path: list[Any], order: list[int]):
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for ordinal, (key, value) in enumerate(items):
            if not path and key not in {"types", "typings", "typesVersions", "exports"}:
                continue
            yield value, [*path, key], [*order, ordinal]

    # Iterator frames avoid recursion and eager expansion of large arrays.
    stack = [iter(children(payload, [], []))]
    visited = 0
    while stack:
        try:
            node, path, order = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if visited >= settings["max_nodes"]:
            reasons.add("node_budget_exceeded")
            break
        visited += 1
        if isinstance(node, (dict, list)):
            if node and len(path) >= settings["max_depth"]:
                reasons.add("depth_budget_exceeded")
            elif node:
                stack.append(iter(children(node, path, order)))
            continue
        field = path[0]
        conditions: list[str] = []
        subpath = None
        selector = None
        if field == "exports":
            keys = [key for key in path[1:] if isinstance(key, str)]
            conditions = [key for key in keys if not key.startswith(".")]
            if not any(key == "types" or key.startswith("types@") for key in conditions):
                continue
            subpath = next((key for key in keys if key.startswith(".")), ".")
        elif field == "typesVersions":
            selector = path[1] if len(path) > 1 and isinstance(path[1], str) else None
            subpath = path[2] if len(path) > 2 and isinstance(path[2], str) else None
        if not isinstance(node, str):
            reasons.add("nonliteral_type_value")
            continue
        if len(records) >= settings["max_records"]:
            reasons.add("record_budget_exceeded")
            break
        records.append({"field_path": path, "sibling_order": order, "condition_path": conditions,
            "subpath": subpath, "version_selector": selector, "target_literal": node,
            "resolution_status": "not_evaluated"})
    result.update(shown_count=len(records), reasons=sorted(reasons))
    if reasons:
        result.update(status="partial", omitted_count=None)
    return result


def build_package_public_contracts(project_root: Path, manifest_paths: Iterable[Path], *,
                                   auxiliary_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve()
    packages: list[dict[str, Any]] = []
    aggregate_patterns: set[str] = set()
    type_policy = _type_entry_policy() if auxiliary_inputs is not None else None
    for manifest_path in sorted({Path(path).resolve() for path in manifest_paths}, key=lambda item: item.as_posix()):
        duplicate_keys = False

        def manifest_object(pairs):
            nonlocal duplicate_keys
            result = {}
            for key, value in pairs:
                duplicate_keys |= key in result
                result[key] = value
            return result

        try:
            manifest_path.relative_to(root)
            manifest_data = manifest_path.read_bytes()
            # Decode only a leading UTF-8 BOM; evidence still hashes manifest_data.
            payload = json.loads(manifest_data.decode("utf-8-sig"), object_pairs_hook=manifest_object)
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
                "type_entry_evidence": _type_entry_evidence(root,
                    to_posix_path(os.path.relpath(manifest_path, root)), manifest_data, payload,
                    duplicate_keys, auxiliary_inputs, type_policy),
            }
        )
    return {
        "source": "package_manifests",
        "entry_patterns": sorted(aggregate_patterns),
        "packages": packages,
    }

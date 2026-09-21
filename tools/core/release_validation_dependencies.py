"""Fail-closed semantic source closure for release-proof receipt reuse.

This module is an invalidation helper only.  It does not skip proof steps and it
does not grant machine, human, release, or publication authority.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.core.config import CODE_MAPS_DIR
from tools.core.source_layer_classifier import classify_source_layer
from tools.core.source_state_identity import source_state_identity


CONTRACT_PATH = CODE_MAPS_DIR / "config" / "release_validation_dependency_contract.json"
SCHEMA_PATH = CODE_MAPS_DIR / "config" / "schemas" / "release_validation_dependency_contract.schema.json"

_DISPOSITIONS = {
    "semantic_closure_reviewed",
    "same_invocation_shared_producer",
    "always_fresh",
    "global_source_fingerprint_until_mapped",
}
_DYNAMIC_IMPORT_CALLS = {
    "__import__",
    "exec",
    "eval",
    "import_module",
    "run_module",
    "run_path",
    "spec_from_file_location",
}
_LITERAL_MODULE_IMPORT_CALLS = {"__import__", "import_module"}
_REQUIRED_SEMANTIC_REUSE_ENGINE_PATHS = {
    "tools/core/release_proof_resume.py",
    "tools/run_release_proof_bundle.py",
    "tools/core/release_validation_dependencies.py",
    "config/release_validation_dependency_contract.json",
    "config/schemas/release_validation_dependency_contract.schema.json",
}
_REVIEWED_SEMANTIC_CLOSURE_FLOORS = {
    "source_layer_classification_policy_validation": {
        "exact_paths": {
            "tools/validate_source_layer_classification_policy.py",
            "tools/core/source_layer_classifier.py",
            "tools/core/source_layer_classification_policy.py",
            "tools/core/source_layer_taxonomy.py",
            "config/source_layer_taxonomy.json",
            "config/source_layer_classification_policy.json",
        },
        "contract_path_expansions": {
            (
                "config/source_layer_classification_policy.json",
                "/exact_path_rules/*/path",
                "path",
            )
        },
    }
}


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def load_release_validation_dependency_contract(
    path: Path | None = None,
) -> dict[str, Any]:
    return _load_json(path or CONTRACT_PATH, label="release validation dependency contract")


def _safe_relative_path(value: Any) -> str | None:
    text = str(value or "").strip()
    candidate = Path(text)
    if (
        not text
        or "\\" in text
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.as_posix() != text
    ):
        return None
    return text


def _resolve_under_root(root: Path, relative: str) -> Path | None:
    safe = _safe_relative_path(relative)
    if safe is None:
        return None
    root_resolved = root.resolve()
    candidate = (root_resolved / safe).resolve()
    if candidate == root_resolved or root_resolved not in candidate.parents:
        return None
    return candidate


def _foreign_contracts(root: Path, contract: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    authorities = contract.get("authorities")
    if not isinstance(authorities, Mapping):
        raise ValueError("dependency contract authorities are missing")
    rows: dict[str, dict[str, Any]] = {}
    for key in ("proof_steps", "proof_scope", "source_layers", "source_classification", "test_inventory"):
        relative = _safe_relative_path(authorities.get(key))
        path = _resolve_under_root(root, relative or "")
        if path is None or not path.is_file():
            raise ValueError(f"dependency authority is missing or unsafe: {key}")
        rows[key] = _load_json(path, label=f"dependency authority {key}")
    return rows


def _proof_domain_map(scope_contract: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    domains = scope_contract.get("domains")
    if not isinstance(domains, list):
        return result
    for domain in domains:
        if not isinstance(domain, Mapping):
            continue
        domain_id = str(domain.get("id") or "")
        for step_id in domain.get("step_ids", []) if isinstance(domain.get("step_ids"), list) else []:
            text = str(step_id or "")
            if text in result:
                result[text] = ""
            else:
                result[text] = domain_id
    return result


def disposition_by_step(contract: Mapping[str, Any]) -> dict[str, str]:
    rows = contract.get("step_dispositions")
    if not isinstance(rows, Mapping):
        return {}
    result: dict[str, str] = {}
    for disposition, step_ids in rows.items():
        if disposition not in _DISPOSITIONS or not isinstance(step_ids, list):
            continue
        for step_id in step_ids:
            text = str(step_id or "")
            if text in result:
                result[text] = "duplicate"
            else:
                result[text] = str(disposition)
    return result


def validate_dependency_contract(
    contract: Mapping[str, Any],
    *,
    root: Path = CODE_MAPS_DIR,
) -> list[str]:
    errors: list[str] = []
    meta = contract.get("meta") if isinstance(contract.get("meta"), Mapping) else {}
    if meta.get("kind") != "release_validation_dependency_contract":
        errors.append("meta.kind_invalid")
    if meta.get("version") != "v1":
        errors.append("meta.version_invalid")
    if meta.get("authority") != "reuse_invalidation_only_no_release_or_publication_authority":
        errors.append("meta.authority_invalid")
    try:
        foreign = _foreign_contracts(root, contract)
    except ValueError as exc:
        return [*errors, f"foreign_authority:{exc}"]

    proof_steps = foreign["proof_steps"].get("steps")
    proof_steps = proof_steps if isinstance(proof_steps, list) else []
    step_rows = {
        str(row.get("id") or ""): row
        for row in proof_steps
        if isinstance(row, Mapping) and str(row.get("id") or "")
    }
    if len(step_rows) != len(proof_steps):
        errors.append("proof_steps_not_unique_or_complete")
    domain_map = _proof_domain_map(foreign["proof_scope"])
    if set(domain_map) != set(step_rows) or any(not value for value in domain_map.values()):
        errors.append("proof_domain_partition_mismatch")

    validation = contract.get("validation") if isinstance(contract.get("validation"), Mapping) else {}
    required_dispositions = validation.get("required_dispositions")
    if not isinstance(required_dispositions, list) or set(map(str, required_dispositions)) != _DISPOSITIONS:
        errors.append("required_dispositions_mismatch")
    rows = contract.get("step_dispositions")
    if not isinstance(rows, Mapping) or set(map(str, rows)) != _DISPOSITIONS:
        errors.append("step_disposition_groups_mismatch")
    flattened: list[str] = []
    if isinstance(rows, Mapping):
        for disposition in sorted(_DISPOSITIONS):
            values = rows.get(disposition)
            if not isinstance(values, list) or len(values) != len(set(map(str, values))):
                errors.append(f"step_disposition_invalid:{disposition}")
                continue
            flattened.extend(map(str, values))
    if len(flattened) != len(set(flattened)):
        errors.append("step_dispositions_overlap")
    if set(flattened) != set(step_rows):
        errors.append("step_dispositions_not_exact_proof_step_partition")

    disposition_map = disposition_by_step(contract)
    shared_steps = {
        step_id
        for step_id, row in step_rows.items()
        if isinstance(row.get("evidence_from_dependency"), Mapping)
    }
    declared_shared = {
        step_id
        for step_id, disposition in disposition_map.items()
        if disposition == "same_invocation_shared_producer"
    }
    if shared_steps != declared_shared:
        errors.append("same_invocation_step_set_mismatch")
    reuse = foreign["proof_scope"].get("evidence_reuse")
    fresh_domains = {
        str(value)
        for value in (reuse.get("always_fresh_domains", []) if isinstance(reuse, Mapping) else [])
    }
    expected_fresh = {
        step_id for step_id, domain_id in domain_map.items() if domain_id in fresh_domains
    }
    declared_fresh = {
        step_id for step_id, disposition in disposition_map.items() if disposition == "always_fresh"
    }
    if expected_fresh != declared_fresh:
        errors.append("always_fresh_step_set_mismatch")
    if (
        validation.get("engine_test_sharding_status")
        != "deterministic_exact_union_proof_family_steps_active_global_reuse_disabled"
    ):
        errors.append("engine_test_sharding_status_invalid")
    aggregate_step_id = str(validation.get("engine_test_aggregate_step_id") or "")
    partition = foreign["test_inventory"].get("partition")
    execution_order = (
        partition.get("execution_order") if isinstance(partition, Mapping) else None
    )
    expected_shards = (
        [f"engine_contract_tests_{family}" for family in map(str, execution_order)]
        if isinstance(execution_order, list)
        else []
    )
    declared_shards = validation.get("engine_test_shard_step_ids")
    if aggregate_step_id != "engine_contract_tests":
        errors.append("engine_test_aggregate_step_id_invalid")
    if not expected_shards or declared_shards != expected_shards:
        errors.append("engine_test_shard_step_ids_invalid")
    if disposition_map.get("engine_contract_tests") != "global_source_fingerprint_until_mapped":
        errors.append("engine_contract_tests_must_remain_global_until_semantic_shard_closures_exist")
    for shard_step_id in expected_shards:
        if disposition_map.get(shard_step_id) != "global_source_fingerprint_until_mapped":
            errors.append(
                f"engine_test_shard_must_remain_global_until_semantic_closure_exists:{shard_step_id}"
            )

    engine_paths = contract.get("semantic_reuse_engine_paths")
    if not isinstance(engine_paths, list) or len(engine_paths) != len(set(map(str, engine_paths))):
        errors.append("semantic_reuse_engine_paths_invalid")
    else:
        declared_engine_paths = {str(value) for value in engine_paths}
        if not _REQUIRED_SEMANTIC_REUSE_ENGINE_PATHS.issubset(declared_engine_paths):
            errors.append("semantic_reuse_engine_paths_incomplete")
        for value in engine_paths:
            relative = _safe_relative_path(value)
            path = _resolve_under_root(root, relative or "")
            if relative is None or path is None:
                errors.append(f"semantic_reuse_engine_path_unsafe:{value}")
            elif not path.is_file():
                errors.append(f"semantic_reuse_engine_path_missing:{relative}")

    taxonomy_rows = foreign["source_layers"].get("layers")
    taxonomy_ids = {
        str(row.get("id") or "")
        for row in (taxonomy_rows if isinstance(taxonomy_rows, list) else [])
        if isinstance(row, Mapping) and str(row.get("id") or "")
    }
    closures = contract.get("semantic_closures")
    closures = closures if isinstance(closures, Mapping) else {}
    reviewed = validation.get("reviewed_semantic_step_ids")
    reviewed_ids = {str(value) for value in reviewed} if isinstance(reviewed, list) else set()
    declared_reviewed = {
        step_id for step_id, disposition in disposition_map.items() if disposition == "semantic_closure_reviewed"
    }
    if reviewed_ids != declared_reviewed or set(map(str, closures)) != reviewed_ids:
        errors.append("reviewed_semantic_step_set_mismatch")
    if reviewed_ids != set(_REVIEWED_SEMANTIC_CLOSURE_FLOORS):
        errors.append("reviewed_semantic_step_hard_floor_mismatch")
    for step_id in sorted(reviewed_ids):
        row = closures.get(step_id)
        if not isinstance(row, Mapping):
            errors.append(f"semantic_closure_missing:{step_id}")
            continue
        hard_floor = _REVIEWED_SEMANTIC_CLOSURE_FLOORS.get(step_id)
        if not isinstance(hard_floor, Mapping):
            errors.append(f"semantic_closure_hard_floor_missing:{step_id}")
            continue
        if row.get("review_status") != "reviewed":
            errors.append(f"semantic_closure_not_reviewed:{step_id}")
        if row.get("proof_domain") != domain_map.get(step_id):
            errors.append(f"semantic_closure_proof_domain_mismatch:{step_id}")
        if row.get("transitive_local_python_imports") is not True:
            errors.append(f"semantic_closure_transitive_imports_required:{step_id}")
        if row.get("require_contract_path_expansions") is not True:
            errors.append(f"semantic_closure_contract_expansions_required:{step_id}")
        allowed_layers = row.get("allowed_source_layers")
        allowed = {str(value) for value in allowed_layers} if isinstance(allowed_layers, list) else set()
        if not allowed or not allowed.issubset(taxonomy_ids):
            errors.append(f"semantic_closure_layer_foreign_key_invalid:{step_id}")
        exact_paths = row.get("exact_paths")
        if not isinstance(exact_paths, list) or not exact_paths:
            errors.append(f"semantic_closure_exact_paths_missing:{step_id}")
        else:
            declared_exact_paths = {str(value) for value in exact_paths}
            required_exact_paths = {
                str(value) for value in hard_floor.get("exact_paths", set())
            }
            for missing_path in sorted(required_exact_paths - declared_exact_paths):
                errors.append(
                    f"semantic_closure_required_seed_missing:{step_id}:{missing_path}"
                )
            for value in exact_paths:
                relative = _safe_relative_path(value)
                path = _resolve_under_root(root, relative or "")
                if relative is None or path is None:
                    errors.append(f"semantic_closure_path_unsafe:{step_id}:{value}")
                elif not path.is_file():
                    errors.append(f"semantic_closure_path_missing:{step_id}:{relative}")
        expansions = row.get("contract_path_expansions")
        if not isinstance(expansions, list) or not expansions:
            errors.append(f"semantic_closure_expansion_missing:{step_id}")
            continue
        declared_expansions = {
            (
                str(expansion.get("contract") or ""),
                str(expansion.get("json_pointer") or ""),
                str(expansion.get("path_field") or ""),
            )
            for expansion in expansions
            if isinstance(expansion, Mapping)
        }
        required_expansions = {
            tuple(str(part) for part in expansion)
            for expansion in hard_floor.get("contract_path_expansions", set())
        }
        for missing_expansion in sorted(required_expansions - declared_expansions):
            errors.append(
                "semantic_closure_required_expansion_missing:"
                f"{step_id}:{'|'.join(missing_expansion)}"
            )
        for expansion in expansions:
            if not isinstance(expansion, Mapping):
                errors.append(f"semantic_closure_expansion_invalid:{step_id}")
                continue
            relative = _safe_relative_path(expansion.get("contract"))
            path = _resolve_under_root(root, relative or "")
            if relative is None or path is None or not path.is_file():
                errors.append(f"semantic_closure_expansion_contract_invalid:{step_id}")
            if expansion.get("json_pointer") != "/exact_path_rules/*/path" or expansion.get("path_field") != "path":
                errors.append(f"semantic_closure_expansion_selector_invalid:{step_id}")
    return sorted(set(errors))


def _module_index(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in {".git", ".tmp", "node_modules", "output", "__pycache__"} for part in relative.parts):
            continue
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if parts:
            result[".".join(parts)] = path
    return result


def _module_name(path: Path, root: Path) -> tuple[str, bool]:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    is_package = bool(parts and parts[-1] == "__init__")
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _relative_import_base(
    source_module: str,
    *,
    is_package: bool,
    level: int,
    module: str | None,
) -> str:
    parts = source_module.split(".") if is_package else source_module.split(".")[:-1]
    parent_hops = max(0, int(level or 0) - 1)
    if parent_hops:
        parts = parts[:-parent_hops]
    if module:
        parts.extend(str(module).split("."))
    return ".".join(part for part in parts if part)


def _call_name(node: ast.Call) -> str:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _literal_module_import(node: ast.Call) -> str | None:
    if _call_name(node) not in _LITERAL_MODULE_IMPORT_CALLS or not node.args:
        return None
    target = node.args[0]
    if (
        isinstance(target, ast.Constant)
        and isinstance(target.value, str)
        and target.value
        and not target.value.startswith(".")
    ):
        return target.value
    return None


def _local_python_import_closure(
    seeds: set[Path],
    *,
    root: Path,
    module_index: Mapping[str, Path] | None = None,
) -> tuple[set[Path], list[str]]:
    index = dict(module_index or _module_index(root))
    pending = sorted((path for path in seeds if path.suffix == ".py"), reverse=True)
    visited: set[Path] = set()
    risks: list[str] = []
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        except (OSError, UnicodeError, SyntaxError) as exc:
            risks.append(f"python_parse_failed:{path.relative_to(root).as_posix()}:{type(exc).__name__}")
            continue
        source_module, is_package = _module_name(path, root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC_IMPORT_CALLS:
                literal_module = _literal_module_import(node)
                if literal_module:
                    target = index.get(literal_module)
                    if target is not None:
                        if target not in visited:
                            pending.append(target)
                    elif literal_module.startswith("tools"):
                        risks.append(
                            "unresolved_local_import:"
                            f"{path.relative_to(root).as_posix()}:{literal_module}"
                        )
                else:
                    risks.append(
                        f"dynamic_import_risk:{path.relative_to(root).as_posix()}:"
                        f"{getattr(node, 'lineno', 0)}:{_call_name(node)}"
                    )
            candidates: list[tuple[str, bool]] = []
            if isinstance(node, ast.Import):
                candidates.extend((alias.name, True) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = (
                    _relative_import_base(
                        source_module,
                        is_package=is_package,
                        level=node.level,
                        module=node.module,
                    )
                    if node.level
                    else str(node.module or "")
                )
                if base:
                    candidates.append((base, bool(node.level or base.startswith("tools"))))
                    candidates.extend(
                        (f"{base}.{alias.name}", False)
                        for alias in node.names
                        if alias.name != "*"
                    )
                elif node.level:
                    risks.append(
                        f"unresolved_local_import:{path.relative_to(root).as_posix()}:{getattr(node, 'lineno', 0)}"
                    )
            for module, required_local in candidates:
                target = index.get(module)
                if target is not None and target not in visited:
                    pending.append(target)
                elif required_local and module.startswith("tools") and module not in index:
                    parent = ".".join(module.split(".")[:-1])
                    if parent not in index:
                        risks.append(
                            f"unresolved_local_import:{path.relative_to(root).as_posix()}:{module}"
                        )
    return visited, sorted(set(risks))


def _expanded_paths(
    closure: Mapping[str, Any],
    *,
    root: Path,
) -> tuple[set[Path], list[str]]:
    paths: set[Path] = set()
    reasons: list[str] = []
    expansions = closure.get("contract_path_expansions")
    for expansion in expansions if isinstance(expansions, list) else []:
        if not isinstance(expansion, Mapping):
            reasons.append("contract_path_expansion_invalid")
            continue
        relative = _safe_relative_path(expansion.get("contract"))
        contract_path = _resolve_under_root(root, relative or "")
        if contract_path is None or not contract_path.is_file():
            reasons.append(f"contract_path_expansion_missing:{relative or 'unsafe'}")
            continue
        paths.add(contract_path)
        if expansion.get("json_pointer") != "/exact_path_rules/*/path" or expansion.get("path_field") != "path":
            reasons.append(f"contract_path_expansion_selector_unsupported:{relative}")
            continue
        try:
            payload = _load_json(contract_path, label=f"expansion contract {relative}")
        except ValueError as exc:
            reasons.append(f"contract_path_expansion_unreadable:{relative}:{exc}")
            continue
        rows = payload.get("exact_path_rules")
        if not isinstance(rows, list):
            reasons.append(f"contract_path_expansion_rows_missing:{relative}")
            continue
        for row in rows:
            value = row.get("path") if isinstance(row, Mapping) else None
            expanded_relative = _safe_relative_path(value)
            expanded = _resolve_under_root(root, expanded_relative or "")
            if expanded_relative is None or expanded is None:
                reasons.append(f"expanded_path_unsafe:{value}")
            elif not expanded.is_file():
                reasons.append(f"expanded_path_missing:{expanded_relative}")
            else:
                paths.add(expanded)
    return paths, sorted(set(reasons))


def _file_rows(paths: set[Path], *, root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        content = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    return rows


def resolve_local_python_import_closure(
    exact_paths: list[str] | tuple[str, ...] | set[str],
    *,
    root: Path = CODE_MAPS_DIR,
) -> dict[str, Any]:
    """Resolve one fail-closed local Python import closure for another owner.

    This is intentionally a source-identity helper, not evidence or release
    authority.  Unsafe/missing seeds, unresolved local imports, dynamic imports
    and parse failures remain explicit blocking reasons for the caller.
    """

    root = root.resolve()
    seeds: set[Path] = set()
    reasons: list[str] = []
    for value in exact_paths:
        relative = _safe_relative_path(value)
        path = _resolve_under_root(root, relative or "")
        if relative is None or path is None:
            reasons.append(f"closure_seed_unsafe:{value}")
        elif not path.is_file():
            reasons.append(f"closure_seed_missing:{relative}")
        elif path.suffix != ".py":
            reasons.append(f"closure_seed_not_python:{relative}")
        else:
            seeds.add(path)

    resolved: set[Path] = set()
    if seeds and not reasons:
        resolved, import_reasons = _local_python_import_closure(seeds, root=root)
        reasons.extend(import_reasons)
    rows = _file_rows(resolved, root=root) if resolved else []
    return {
        "status": "BLOCKED" if reasons else "COMPLETE",
        "paths": [row["path"] for row in rows],
        "files": rows,
        "content_sha256": canonical_sha256(rows),
        "reasons": sorted(set(reasons)),
        "authority": "source_dependency_identity_only_no_evidence_or_release_authority",
    }


def _global_fallback(
    step_id: str,
    disposition: str,
    reasons: list[str],
    *,
    root: Path,
    contract_sha256: str,
) -> dict[str, Any]:
    global_identity = source_state_identity(root)
    return {
        "step_id": step_id,
        "disposition": disposition,
        "reuse_eligible": False,
        "identity_mode": "global_source_fingerprint_execute",
        "source_dependency_sha256": str(global_identity.get("fingerprint") or ""),
        "semantic_dependency_contract_sha256": contract_sha256,
        "closure": {"paths": [], "layers": [], "file_count": 0},
        "reasons": sorted(set(reasons)) or ["global_source_fingerprint_required"],
    }


def _resolve_reviewed_step_dependency_identity(
    step_id: str,
    *,
    root: Path,
    payload: Mapping[str, Any],
    disposition: str,
    contract_sha256: str,
    module_index: Mapping[str, Path],
) -> dict[str, Any]:
    closures = payload.get("semantic_closures")
    closure = closures.get(step_id) if isinstance(closures, Mapping) else None
    if not isinstance(closure, Mapping):
        return _global_fallback(
            str(step_id),
            disposition,
            ["semantic_closure_missing"],
            root=root,
            contract_sha256=contract_sha256,
        )
    paths: set[Path] = set()
    reasons: list[str] = []
    for value in closure.get("exact_paths", []) if isinstance(closure.get("exact_paths"), list) else []:
        relative = _safe_relative_path(value)
        path = _resolve_under_root(root, relative or "")
        if relative is None or path is None:
            reasons.append(f"exact_path_unsafe:{value}")
        elif not path.is_file():
            reasons.append(f"exact_path_missing:{relative}")
        else:
            paths.add(path)
    expanded, expansion_reasons = _expanded_paths(closure, root=root)
    paths.update(expanded)
    reasons.extend(expansion_reasons)
    if closure.get("transitive_local_python_imports") is True and not reasons:
        imported, import_reasons = _local_python_import_closure(
            paths,
            root=root,
            module_index=module_index,
        )
        paths.update(imported)
        reasons.extend(import_reasons)

    allowed_layers = {str(value) for value in closure.get("allowed_source_layers", [])}
    observed_layers: set[str] = set()
    for path in paths:
        layer, _ = classify_source_layer(path, root=root)
        observed_layers.add(layer)
        if layer == "unknown_or_review":
            reasons.append(f"closure_path_has_unknown_owner:{path.relative_to(root).as_posix()}")
        elif layer not in allowed_layers:
            reasons.append(
                f"closure_path_layer_not_declared:{path.relative_to(root).as_posix()}:{layer}"
            )
    if reasons:
        return _global_fallback(
            str(step_id),
            disposition,
            reasons,
            root=root,
            contract_sha256=contract_sha256,
        )

    rows = _file_rows(paths, root=root)
    semantic_identity = canonical_sha256(
        {
            "step_id": step_id,
            "disposition": disposition,
            "dependency_contract_sha256": contract_sha256,
            "proof_domain": closure.get("proof_domain"),
            "review_status": closure.get("review_status"),
            "layers": sorted(observed_layers),
            "files": rows,
        }
    )
    return {
        "step_id": step_id,
        "disposition": disposition,
        "reuse_eligible": True,
        "identity_mode": "semantic_closure_content_identity",
        "source_dependency_sha256": semantic_identity,
        "semantic_dependency_contract_sha256": contract_sha256,
        "closure": {
            "paths": rows,
            "layers": sorted(observed_layers),
            "file_count": len(rows),
        },
        "reasons": ["reviewed_semantic_closure_complete"],
    }


def build_release_validation_dependency_context(
    *,
    root: Path = CODE_MAPS_DIR,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile all reviewed semantic closures once for one proof invocation."""
    payload = dict(contract or load_release_validation_dependency_contract())
    contract_sha256 = canonical_sha256(payload)
    errors = validate_dependency_contract(payload, root=root)
    dispositions = disposition_by_step(payload)
    engine_rows: list[dict[str, Any]] = []
    if not errors:
        resolved_engine_paths: set[Path] = set()
        for value in payload.get("semantic_reuse_engine_paths", []):
            relative = _safe_relative_path(value)
            path = _resolve_under_root(root, relative or "")
            if relative is None or path is None or not path.is_file():
                errors.append(f"semantic_reuse_engine_path_unavailable:{value}")
            else:
                resolved_engine_paths.add(path)
        if not errors:
            engine_rows = _file_rows(resolved_engine_paths, root=root)
    engine_sha256 = (
        canonical_sha256(
            {
                "semantic_dependency_contract_sha256": contract_sha256,
                "files": engine_rows,
            }
        )
        if engine_rows and not errors
        else ""
    )
    reviewed_identities: dict[str, dict[str, Any]] = {}
    if not errors:
        module_index = _module_index(root)
        for step_id, disposition in sorted(dispositions.items()):
            if disposition != "semantic_closure_reviewed":
                continue
            identity = _resolve_reviewed_step_dependency_identity(
                step_id,
                root=root,
                payload=payload,
                disposition=disposition,
                contract_sha256=contract_sha256,
                module_index=module_index,
            )
            reviewed_identities[step_id] = identity
            if identity.get("reuse_eligible") is not True:
                errors.extend(
                    f"semantic_closure_incomplete:{step_id}:{reason}"
                    for reason in identity.get("reasons", [])
                )
    valid_engine_sha = len(engine_sha256) == 64 and all(
        character in "0123456789abcdef" for character in engine_sha256
    )
    return {
        "complete": not errors and valid_engine_sha,
        "semantic_dependency_contract_sha256": contract_sha256,
        "semantic_reuse_engine_sha256": engine_sha256,
        "step_dispositions": dispositions,
        "reviewed_step_identities": reviewed_identities,
        "errors": sorted(set(errors)),
    }


def resolve_step_dependency_identity(
    step_id: str,
    *,
    root: Path = CODE_MAPS_DIR,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(contract or load_release_validation_dependency_contract())
    context = build_release_validation_dependency_context(root=root, contract=payload)
    contract_sha256 = str(context.get("semantic_dependency_contract_sha256") or "")
    disposition = disposition_by_step(payload).get(str(step_id), "undeclared")
    errors = list(context.get("errors") or [])
    if errors:
        return _global_fallback(
            str(step_id),
            disposition,
            [f"dependency_contract_invalid:{error}" for error in errors],
            root=root,
            contract_sha256=contract_sha256,
        )
    reviewed = context.get("reviewed_step_identities")
    if disposition == "semantic_closure_reviewed" and isinstance(reviewed, Mapping):
        identity = reviewed.get(str(step_id))
        if isinstance(identity, Mapping):
            return dict(identity)
    return _global_fallback(
        str(step_id),
        disposition,
        [
            "same_invocation_shared_evidence_required"
            if disposition == "same_invocation_shared_producer"
            else "fresh_execution_required"
            if disposition == "always_fresh"
            else "semantic_closure_not_reviewed"
            if disposition == "global_source_fingerprint_until_mapped"
            else "step_disposition_unknown"
        ],
        root=root,
        contract_sha256=contract_sha256,
    )

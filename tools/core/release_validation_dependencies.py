"""Fail-closed semantic source closure for release-proof receipt reuse.

This module is an invalidation helper only.  It does not skip proof steps and it
does not grant machine, human, release, or publication authority.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from tools.core.config import CODE_MAPS_DIR
from tools.core.source_layer_classifier import classify_source_layer
from tools.core.source_state_identity import source_state_identity
from tools.core.release_source_index import build_local_python_module_index


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
    "tools/core/release_proof_steps.py",
    "tools/run_release_proof_bundle.py",
    "tools/core/release_validation_dependencies.py",
    "tools/core/release_validation_source_recipe.py",
    "tools/core/distribution_test_selection.py",
    "tools/core/release_source_index.py",
    "tools/doctrine_compiler.py",
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
    },
    "next_boundary_policy_core": {
        "exact_paths": {
            "tools/validate_next_boundary_policy_core.py",
            "tools/engines/next_boundary_analyzer.py",
            "config/next_boundary_policy.json",
        },
        "source_text_only_paths": {"tools/engines/next_boundary_analyzer.py"},
        "startup_configuration_tree": "config_full_tree_v1",
        "contract_path_expansions": set(),
    },
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
    if "canonical_test_unit_admission" in contract:
        from tools.core.distribution_unit_input_identity import validate_canonical_unit_policy as validate_unit_admission

        try:
            validate_unit_admission(contract["canonical_test_unit_admission"], contract.get("diagnostic_test_unit_pilots"))
        except ValueError:
            errors.append("canonical_test_unit_admission_policy_invalid")
    if "persisted_test_unit_reuse" in contract:
        from tools.core.distribution_unit_input_identity import validate_persisted_unit_policy as validate_policy

        try:
            validate_policy(contract["persisted_test_unit_reuse"], contract.get("diagnostic_test_unit_pilots"))
        except ValueError:
            errors.append("persisted_test_unit_reuse_policy_invalid")
    meta = contract.get("meta") if isinstance(contract.get("meta"), Mapping) else {}
    if meta.get("kind") != "release_validation_dependency_contract":
        errors.append("meta.kind_invalid")
    if meta.get("version") != "v1":
        errors.append("meta.version_invalid")
    if meta.get("authority") != "reuse_invalidation_only_no_release_or_publication_authority":
        errors.append("meta.authority_invalid")
    projection_policy = contract.get("completed_dependency_input_projection")
    if "completed_dependency_input_projection" in contract and (
        not isinstance(projection_policy, Mapping)
        or set(projection_policy) != {"version", "authority", "default_role"}
        or not isinstance(projection_policy.get("version"), str)
        or not projection_policy.get("version")
        or projection_policy.get("authority") != "completed_identity_only_no_gate_or_checkpoint_relaxation"
        or projection_policy.get("default_role") != "full_evidence"
    ):
        errors.append("completed_dependency_input_projection_invalid")
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
        if "dependency_input_roles" in row:
            roles = row["dependency_input_roles"]
            if not isinstance(roles, Mapping) or any(
                not isinstance(key, str) or not key or not isinstance(role, str) or not role
                for key, role in roles.items()
            ):
                errors.append(f"semantic_closure_dependency_input_roles_invalid:{step_id}")
        if row.get("transitive_local_python_imports") is not True:
            errors.append(f"semantic_closure_transitive_imports_required:{step_id}")
        needs_expansions = bool(hard_floor.get("contract_path_expansions"))
        if row.get("require_contract_path_expansions") is not needs_expansions:
            errors.append(f"semantic_closure_contract_expansions_required:{step_id}")
        if row.get("startup_configuration_tree") != hard_floor.get("startup_configuration_tree"):
            errors.append(f"semantic_closure_startup_inputs_invalid:{step_id}")
        text_paths = row.get("source_text_only_paths", [])
        if (not isinstance(text_paths, list)
                or any(not isinstance(path, str) for path in text_paths)
                or set(text_paths) != hard_floor.get("source_text_only_paths", set())
                or len(text_paths) != len(set(text_paths))):
            errors.append(f"semantic_closure_text_input_roles_invalid:{step_id}")
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
        if not isinstance(expansions, list) or (needs_expansions and not expansions):
            errors.append(f"semantic_closure_expansion_missing:{step_id}")
            continue
        if not needs_expansions and expansions:
            errors.append(f"semantic_closure_expansion_not_reviewed:{step_id}")
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
    from tools.core.release_validation_source_recipe import validate_source_recipes
    errors.extend(validate_source_recipes(
        contract.get("source_recipes", {}), domain_map, disposition_map,
        family_context={"steps": step_rows, "inventory": foreign["test_inventory"],
                        "step_ids": validation.get("engine_test_shard_step_ids", [])}))
    return sorted(set(errors))


def _module_index(root: Path) -> dict[str, Path]:
    return build_local_python_module_index(
        root, excluded_parts={".git", ".tmp", "node_modules", "output", "__pycache__"})


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


def _package_initializers(path: Path, *, root: Path, index: Mapping[str, Path]) -> tuple[set[Path], list[str]]:
    initializers: set[Path] = set()
    risks: list[str] = []
    for parent in path.parents:
        if parent == root:
            break
        initializer = parent / "__init__.py"
        if initializer == path:
            continue
        try:
            mode = initializer.stat().st_mode
        except FileNotFoundError:
            package = ".".join(parent.relative_to(root).parts)
            shadow = index.get(package)
            if shadow is not None and shadow.name != "__init__.py":
                risks.append(f"namespace_parent_shadowed_by_module:{package}")
            continue
        except OSError as exc:
            risks.append(f"package_initializer_unavailable:{type(exc).__name__}")
            continue
        if not stat.S_ISREG(mode):
            risks.append(f"package_initializer_not_regular:{initializer.relative_to(root).as_posix()}")
        else:
            initializers.add(initializer)
    return initializers, risks


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
        try:
            resolved = _resolve_under_root(root, path.relative_to(root).as_posix())
        except (OSError, RuntimeError):
            resolved = None
        if resolved is None or resolved != path:
            risks.append(f"local_import_outside_or_aliased_root:{path.relative_to(root).as_posix()}")
            continue
        visited.add(path)
        initializers, package_risks = _package_initializers(path, root=root, index=index)
        pending.extend(initializers - visited)
        risks.extend(package_risks)
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
    try:
        rows = _file_rows(resolved, root=root) if resolved else []
    except OSError as exc:
        rows = []
        reasons.append(f"closure_file_identity_unavailable:{type(exc).__name__}")
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


def _completed_dependency_input_plan(step_id, closure, payload, root, contract_sha256):
    """Compile optional reviewed roles; unknown coverage retains full evidence."""
    if "dependency_input_roles" not in closure:
        return None  # Legacy declarations incur no extra authority read.
    plan = {
        "mode": "full_evidence", "roles": {}, "reasons": [],
        "semantic_dependency_contract_sha256": contract_sha256,
        "dependency_contract_sha256": "",
    }
    try:
        relative = _safe_relative_path(payload["authorities"]["proof_steps"])
        path = _resolve_under_root(root, relative or "")
        steps = _load_json(path, label="dependency role steps")["steps"] if path else []
        step = next(row for row in steps if row.get("id") == step_id)
        dependencies = step.get("depends_on", [])
        plan["roles"] = {dependency: "full_evidence" for dependency in dependencies}
        plan["dependency_contract_sha256"] = canonical_sha256({
            "depends_on": dependencies,
            "dependency_failure_modes": step.get("dependency_failure_modes", {}),
        })
    except (OSError, ValueError, TypeError, KeyError, StopIteration, AttributeError):
        plan["reasons"] = ["dependency_role_step_unavailable"]
        return plan
    policy = payload.get("completed_dependency_input_projection")
    roles = closure["dependency_input_roles"]
    if not isinstance(policy, Mapping) or policy.get("version") != "v1":
        plan["reasons"] = ["dependency_role_protocol_absent_or_unsupported"]
    elif not isinstance(roles, Mapping) or set(roles) != set(dependencies):
        plan["reasons"] = ["dependency_roles_not_exact_direct_dependency_set"]
    elif any(role not in {"full_evidence", "prerequisite_only"} for role in roles.values()):
        plan["reasons"] = ["dependency_role_unclassified_or_unsupported"]
    else:
        from tools.core.release_proof_steps import dependency_failure_mode

        try:
            nonblocking = any(
                role == "prerequisite_only" and dependency_failure_mode(step, dependency) != "blocking"
                for dependency, role in roles.items())
        except ValueError:
            plan["reasons"] = ["dependency_failure_policy_unavailable"]
            return plan
        if any(role == "prerequisite_only" and disposition_by_step(payload).get(dependency) == "same_invocation_shared_producer"
               for dependency, role in roles.items()):
            plan["reasons"] = ["same_invocation_dependency_retains_full_evidence"]
        elif nonblocking:
            plan["reasons"] = ["advisory_dependency_retains_full_evidence"]
        else:
            plan.update(mode="reviewed_prerequisite_roles_v1", roles=dict(roles),
                        reasons=["reviewed_direct_dependency_roles_complete"])
    return plan


def _startup_configuration_paths(root: Path) -> tuple[set[Path], list[str], list[str]]:
    """Bind conservative startup data without running its compiler or loaders."""
    if any(value and (key.startswith("CODEMAPS_TARGET_") or key in {
            "CODEMAPS_TOPOLOGY_MODE", "CODEMAPS_EXTERNAL_RUN_ID"})
           for key, value in os.environ.items()):
        return set(), [], ["startup_external_context_not_reviewed"]
    config = root / "config"
    files: set[Path] = set()
    directories: list[str] = []
    pending = [config]
    total_bytes = 0
    try:
        while pending:
            directory = pending.pop()
            state = directory.lstat()
            if stat.S_ISLNK(state.st_mode) or getattr(state, "st_file_attributes", 0) & 0x400:
                return set(), [], ["startup_configuration_alias_not_reviewed"]
            directories.append(directory.relative_to(root).as_posix())
            if len(directories) + len(files) > 2048:
                return set(), [], ["startup_configuration_inventory_budget_exceeded"]
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    state = path.lstat()
                    if (stat.S_ISLNK(state.st_mode)
                            or getattr(state, "st_file_attributes", 0) & 0x400):
                        return set(), [], ["startup_configuration_alias_not_reviewed"]
                    if stat.S_ISDIR(state.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(state.st_mode):
                        files.add(path)
                        total_bytes += state.st_size
                    else:
                        return set(), [], ["startup_configuration_nonregular_input"]
                    if len(pending) + len(directories) + len(files) > 2048 or total_bytes > 64 * 1024 * 1024:
                        return set(), [], ["startup_configuration_inventory_budget_exceeded"]
        manifest = config / "doctrines/manifest.json"
        if manifest in files:
            data = _load_json(manifest, label="startup doctrine manifest")
            rows = data.get("active_doctrines")
            if not isinstance(rows, list) or not rows:
                return set(), [], ["startup_doctrine_manifest_invalid"]
            for row in rows:
                value = row.get("path") if isinstance(row, Mapping) else None
                relative = _safe_relative_path(value)
                target = (manifest.parent / relative).resolve() if relative else None
                if target not in files:
                    return set(), [], ["startup_doctrine_member_unavailable_or_unconfined"]
    except (OSError, ValueError, UnicodeError) as exc:
        return set(), [], [f"startup_configuration_unavailable:{type(exc).__name__}"]
    return files, sorted(directories), []


def reviewed_source_inputs_current(projection: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    """Refuse reuse and baseline creation if mutable startup inputs drifted."""
    closure = projection.get("closure", {})
    paths, directories, reasons = _startup_configuration_paths(root)
    if reasons:
        return {"matches": False, "reasons": reasons}
    try:
        expected = closure.get("paths", [])
        if not expected or any(not isinstance(row, Mapping) for row in expected):
            return {"matches": False, "reasons": ["reviewed_input_rows_missing"]}
        for row in expected:
            path = _resolve_under_root(root, row.get("path", ""))
            if path is None:
                return {"matches": False, "reasons": ["reviewed_input_path_unavailable"]}
            paths.add(path)
        matches = (_file_rows(paths, root=root) == expected
                   and directories == closure.get("startup_configuration_directories"))
    except (OSError, ValueError, UnicodeError) as exc:
        return {"matches": False, "reasons": [f"reviewed_input_recheck_unavailable:{type(exc).__name__}"]}
    return {"matches": matches, "reasons": [] if matches else ["reviewed_source_inputs_changed"]}


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
        # Observed source text remains content-bound; only execution roots
        # seed import traversal. A real import can still reach a text input.
        text_paths = {root / value for value in closure.get("source_text_only_paths", [])}
        imported, import_reasons = _local_python_import_closure(
            paths - text_paths,
            root=root,
            module_index=module_index,
        )
        paths.update(imported)
        reasons.extend(import_reasons)

    startup_directories: list[str] = []
    if closure.get("startup_configuration_tree") == "config_full_tree_v1":
        startup_paths, startup_directories, startup_reasons = _startup_configuration_paths(root)
        paths.update(startup_paths)
        reasons.extend(startup_reasons)

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
            "startup_configuration_directories": startup_directories,
        }
    )
    result = {
        "step_id": step_id,
        "disposition": disposition,
        "reuse_eligible": True,
        "current_input_recheck_required": closure.get("startup_configuration_tree") == "config_full_tree_v1",
        "identity_mode": "semantic_closure_content_identity",
        "source_dependency_sha256": semantic_identity,
        "semantic_dependency_contract_sha256": contract_sha256,
        "closure": {
            "paths": rows,
            "layers": sorted(observed_layers),
            "file_count": len(rows),
            "source_text_only_paths": list(closure.get("source_text_only_paths", [])),
            "startup_configuration_directories": startup_directories,
        },
        "reasons": ["reviewed_semantic_closure_complete"],
    }
    plan = _completed_dependency_input_plan(step_id, closure, payload, root, contract_sha256)
    if plan is not None:
        result["completed_dependency_input_plan"] = plan
    return result


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
        "source_root": str(root.resolve()),
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


def observe_release_source_recipe(
    step_id: str, *, root: Path = CODE_MAPS_DIR, contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe a disabled recipe without granting native receipt reuse."""
    from tools.core.release_validation_source_recipe import (
        observe_family_source_recipe, observe_source_recipe, source_recipe_context_reasons,
    )
    selected = load_release_validation_dependency_contract() if contract is None else contract
    errors = [*validate_dependency_contract(selected, root=root), *source_recipe_context_reasons()]
    recipes = selected.get("source_recipes", {})
    recipe = recipes.get(step_id) if isinstance(recipes, Mapping) else None
    if recipe and recipe.get("kind") in ("test_family_input_recipe_v1", "test_family_input_recipe_v2"):
        return observe_family_source_recipe(step_id, root=root, contract=selected, errors=errors,
                                            python_resolver=resolve_local_python_import_closure)
    recipe = recipe if not errors else None
    closure = resolve_local_python_import_closure(recipe["executable_python_roots"], root=root) if recipe else None
    return observe_source_recipe(step_id, root=root, recipe=recipe, python_closure=closure, errors=errors)

"""Bounded source-input observations; never a receipt-reuse admission owner.

Recipes live in the existing release dependency contract. Reading a recipe
does not import its Python roots, run a producer, or accept a completed proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from tools.doctrine_compiler import doctrine_source_fingerprint_from_payloads


_ROLES = ("executable_python_roots", "source_text_reads", "data_reads", "startup_inputs")
_FIELDS = {*_ROLES, "kind", "proof_domain", "native_reuse_enabled",
           "manifest_expansions", "producer_policy", "context_policy"}
_OPTIONAL_FIELDS = {"producer_bindings"}
_EXTERNAL_CONTEXT_KEYS = (
    "CODEMAPS_TARGET_ROOT", "CODEMAPS_TARGET_PROJECTS", "CODEMAPS_TOPOLOGY_MODE",
    "CODEMAPS_EXTERNAL_RUN_ID", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT",
    "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA",
)
_SELECTOR = re.compile(r"/([A-Za-z_][A-Za-z0-9_]*)/\*/([A-Za-z_][A-Za-z0-9_]*)\Z")
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_FILES = 512
_MAX_MANIFEST_ENTRIES = 128
_FAMILY_FIELDS = {"kind", "proof_domain", "native_reuse_enabled", "profile", "family",
                  "launch_python_roots", "declared_inputs", "coverage_gaps"}
_FAMILY_KINDS = ("test_family_input_recipe_v1", "test_family_input_recipe_v2")
_FAMILY_V2_FIELDS = _FAMILY_FIELDS | {"presence_inputs", "manifest_inputs"}


def _pointer_parts(pointer: Any) -> list[str]:
    if (not isinstance(pointer, str) or not pointer.startswith("/")
            or len(pointer) > 1024 or len(pointer.split("/")) > 33):
        raise ValueError("family_manifest_pointer_invalid")
    parts = pointer.split("/")[1:]
    if any("*" in part or re.search(r"~(?![01])", part) for part in parts):
        raise ValueError("family_manifest_pointer_invalid")
    return [part.replace("~1", "/").replace("~0", "~") for part in parts]


def _manifest_target(payload: Any, pointer: str) -> str:
    value = payload
    for part in _pointer_parts(pointer):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]{0,7}", part) and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError("family_manifest_pointer_missing")
    if not _relative(value):
        raise ValueError("family_manifest_target_invalid")
    return value


def _presence_state(root: Path, relative: str) -> bool:
    if not _relative(relative):
        raise ValueError("unsafe_input_path")
    path = root / relative
    resolved = path.resolve(strict=False)
    if resolved != path or root not in resolved.parents:
        raise ValueError("presence_aliased_or_escaping_input")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("presence_not_regular")
    return True


def _family_v2_inputs(recipe, root, note_role, capture):
    presence, selections, manifests = {}, [], {}
    for path in recipe["presence_inputs"]:
        note_role(path, "presence_input")
        presence[path] = _presence_state(root, path)
        if presence[path]:
            capture(path, "presence_input")
    for ref in recipe["manifest_inputs"]:
        path = ref["manifest"]
        content = capture(path, "input_manifest")
        if path not in manifests:
            manifests[path] = _json(content)
        target = _manifest_target(manifests[path], ref["json_pointer"])
        capture(target, "manifest_selected_input")
        selections.append({**ref, "target": target})
    return presence, selections


def validate_family_declared_inputs(recipe, *, root: Path) -> list[str]:
    """Cheap captured-reference availability guard, not a family observation.

    No import traversal, test collection, installed-package discovery or repair.
    It does not prove a complete read set or source stability.
    """
    if not isinstance(recipe, Mapping) or recipe.get("kind") != "test_family_input_recipe_v2":
        return []
    errors = _family_recipe_errors(recipe)
    if errors:
        return errors
    contents, paths = {}, {}

    def note_role(path, _role):
        previous = paths.get(path.casefold())
        if previous is not None and previous != path:
            raise ValueError("input_paths_ambiguous")
        paths[path.casefold()] = path
        if len(paths) > _MAX_FILES:
            raise ValueError("input_count_limit")

    def capture(path, role):
        note_role(path, role)
        if path not in contents:
            content = _read(root, path)
            if sum(map(len, contents.values())) + len(content) > _MAX_TOTAL_BYTES:
                raise ValueError("input_total_size_limit")
            contents[path] = content
        return contents[path]

    try:
        root = root.resolve(strict=True)
        for path in recipe["declared_inputs"]:
            capture(path, "declared_input")
        _family_v2_inputs(recipe, root, note_role, capture)
    except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else type(exc).__name__
        return [f"family_declared_input_unavailable:{reason}"]
    return []


def _family_recipe_errors(recipe: Mapping[str, Any]) -> list[str]:
    v2 = recipe.get("kind") == "test_family_input_recipe_v2"
    if set(recipe) != (_FAMILY_V2_FIELDS if v2 else _FAMILY_FIELDS):
        return ["family_recipe_fields_invalid"]
    errors = []
    if recipe["kind"] not in _FAMILY_KINDS or recipe["profile"] != "canonical_all":
        errors.append("family_recipe_protocol_invalid")
    if recipe["native_reuse_enabled"] is not False:
        errors.append("recipe_native_admission_forbidden")
    if not isinstance(recipe["family"], str) or not re.fullmatch(r"[a-z][a-z_]{0,63}", recipe["family"]):
        errors.append("family_recipe_name_invalid")
    for key in ("launch_python_roots", "declared_inputs", *(["presence_inputs"] if v2 else [])):
        paths = recipe[key]
        if (not isinstance(paths, list) or len(paths) > _MAX_FILES
                or any(not _relative(p) for p in paths)
                or len({p.casefold() for p in paths}) != len(paths)):
            errors.append(f"family_recipe_paths_invalid:{key}")
    roots = recipe["launch_python_roots"]
    if not isinstance(roots, list) or not roots or any(not isinstance(p, str) or not p.endswith(".py") for p in roots):
        errors.append("family_recipe_python_roots_invalid")
    gaps = recipe["coverage_gaps"]
    if (not isinstance(gaps, list) or not 1 <= len(gaps) <= 32
            or any(not isinstance(g, str) or not re.fullmatch(r"[a-z][a-z_]{0,95}", g) for g in gaps)
            or len(set(gaps)) != len(gaps)):
        errors.append("family_recipe_gaps_invalid")
    if v2:
        refs = recipe["manifest_inputs"]
        if not isinstance(refs, list) or len(refs) > 16:
            return [*errors, "family_manifest_inputs_invalid"]
        seen = set()
        for ref in refs:
            if (not isinstance(ref, Mapping) or set(ref) != {"manifest", "json_pointer", "base"}
                    or not _relative(ref["manifest"]) or ref["base"] != "project_root"):
                errors.append("family_manifest_reference_invalid")
                continue
            try:
                _pointer_parts(ref["json_pointer"])
            except ValueError:
                errors.append("family_manifest_pointer_invalid")
                continue
            key = (ref["manifest"].casefold(), ref["json_pointer"])
            if key in seen:
                errors.append("family_manifest_reference_duplicate")
            seen.add(key)
    return errors


def _relative(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.strip() != value:
        return False
    path = PurePosixPath(value)
    return ("\\" not in value and ":" not in value and "\x00" not in value
            and not path.is_absolute() and not PureWindowsPath(value).drive
            and ".." not in path.parts and path.as_posix() == value and value != ".")


def _recipe_errors(recipe: Any) -> list[str]:
    if isinstance(recipe, Mapping) and recipe.get("kind") in _FAMILY_KINDS:
        return _family_recipe_errors(recipe)
    if not isinstance(recipe, Mapping) or not _FIELDS <= set(recipe) <= _FIELDS | _OPTIONAL_FIELDS:
        return ["recipe_fields_invalid"]
    errors = []
    if recipe["kind"] != "source_input_recipe_v1":
        errors.append("recipe_kind_invalid")
    if recipe["native_reuse_enabled"] is not False:
        errors.append("recipe_native_admission_forbidden")
    if recipe["producer_policy"] != "existing_prerequisite_required_not_verified":
        errors.append("recipe_producer_policy_invalid")
    if recipe["context_policy"] != "product_local_parent_observation_only":
        errors.append("recipe_context_policy_invalid")
    for role in _ROLES:
        paths = recipe[role]
        if (not isinstance(paths, list) or len(paths) > _MAX_FILES
                or any(not _relative(p) for p in paths)):
            errors.append(f"recipe_paths_invalid:{role}")
        elif len({p.casefold() for p in paths}) != len(paths):
            errors.append(f"recipe_paths_ambiguous:{role}")
    roots = recipe["executable_python_roots"]
    if not isinstance(roots, list) or not roots or any(
            not isinstance(p, str) or not p.endswith(".py") for p in roots):
        errors.append("recipe_python_roots_invalid")
    expansions = recipe["manifest_expansions"]
    if not isinstance(expansions, list) or len(expansions) > 16:
        return [*errors, "recipe_expansions_invalid"]
    owners = set()
    declared_data = {p for role in ("data_reads", "startup_inputs")
                     for p in recipe[role] if isinstance(p, str)} if not errors else set()
    for row in expansions:
        if not isinstance(row, Mapping) or set(row) != {"manifest", "json_pointer", "base"}:
            errors.append("recipe_expansion_fields_invalid")
            continue
        manifest = row["manifest"]
        if not _relative(manifest) or manifest not in declared_data:
            errors.append("recipe_manifest_owner_not_declared_data")
        elif manifest.casefold() in owners:
            errors.append("recipe_manifest_owner_ambiguous")
        else:
            owners.add(manifest.casefold())
        if row["base"] != "manifest_parent" or not isinstance(row["json_pointer"], str) or not _SELECTOR.fullmatch(row["json_pointer"]):
            errors.append("recipe_manifest_selector_invalid")
    bindings = recipe.get("producer_bindings", [])
    if not isinstance(bindings, list) or len(bindings) > 1:
        return [*errors, "recipe_producer_bindings_invalid"]
    for binding in bindings:
        if not isinstance(binding, Mapping) or set(binding) != {"owner", "manifest", "output"}:
            errors.append("recipe_producer_binding_fields_invalid")
        elif binding["owner"] != "doctrine_manifest_fingerprint_v1":
            errors.append("recipe_producer_owner_unknown")
        elif (not _relative(binding["manifest"]) or not _relative(binding["output"])
              or binding["manifest"] not in declared_data or binding["output"] not in declared_data
              or binding["manifest"] == binding["output"] or not any(
                  row.get("manifest") == binding["manifest"] and row.get("json_pointer") == "/active_doctrines/*/path"
                  for row in expansions if isinstance(row, Mapping))):
            errors.append("recipe_producer_inputs_not_declared")
    return errors


def validate_source_recipes(
    recipes: Any, domains: Mapping[str, str], dispositions: Mapping[str, str],
    *, family_context: Mapping[str, Any] | None = None,
) -> list[str]:
    """Validate declarations without reading their inputs or invoking producers."""
    if not isinstance(recipes, Mapping):
        return ["source_recipes_not_mapping"]
    errors = []
    for step_id, recipe in recipes.items():
        errors.extend(f"source_recipe:{step_id}:{e}" for e in _recipe_errors(recipe))
        if step_id not in domains:
            errors.append(f"source_recipe_unknown_step:{step_id}")
        elif isinstance(recipe, Mapping) and recipe.get("proof_domain") != domains[step_id]:
            errors.append(f"source_recipe_domain_mismatch:{step_id}")
        if dispositions.get(step_id) != "global_source_fingerprint_until_mapped":
            errors.append(f"source_recipe_must_remain_global:{step_id}")
        if isinstance(recipe, Mapping) and recipe.get("kind") in _FAMILY_KINDS and not _recipe_errors(recipe):
            errors.extend(_family_binding_errors(step_id, recipe, family_context))
    return sorted(set(errors))


def _family_binding_errors(step_id, recipe, context):
    if not isinstance(context, Mapping):
        return [f"family_recipe_binding_missing:{step_id}"]
    errors = []
    family = recipe["family"]
    families = context["inventory"].get("partition", {}).get("families", {})
    family_row = families.get(family)
    command = context["steps"].get(step_id, {}).get("command")
    if (step_id not in context["step_ids"] or not isinstance(family_row, Mapping)
            or not isinstance(family_row.get("profiles"), list)
            or recipe["profile"] not in family_row.get("profiles", [])):
        errors.append(f"family_recipe_inventory_binding_invalid:{step_id}")
    if (not isinstance(command, list) or len(command) < 2
            or command[1] != "${code_maps}/tools/run_distribution_tests.py"
            or "tools/run_distribution_tests.py" not in recipe["launch_python_roots"]):
        errors.append(f"family_recipe_runner_binding_invalid:{step_id}")
    else:
        for option, expected in (("--profile", recipe["profile"]), ("--shard", family)):
            if (command.count(option) != 1 or command.index(option) + 1 >= len(command)
                    or command[command.index(option) + 1] != expected):
                errors.append(f"family_recipe_command_binding_invalid:{step_id}:{option}")
    return errors


def _json(content: bytes) -> dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("ambiguous_json_key")
            result[key] = value
        return result

    value = json.loads(content.decode("utf-8-sig"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def _read(root: Path, relative: str) -> bytes:
    if not _relative(relative):
        raise ValueError("unsafe_input_path")
    path = root / relative
    resolved = path.resolve(strict=True)
    if resolved != path or root not in resolved.parents:
        raise ValueError("aliased_or_escaping_input_path")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
        raise ValueError("nonregular_or_oversize_input")
    with path.open("rb") as stream:
        content = stream.read(_MAX_FILE_BYTES + 1)
    if len(content) > _MAX_FILE_BYTES:
        raise ValueError("oversize_input")
    return content


def source_recipe_context_reasons() -> list[str]:
    return [f"unsupported_context_present:{key}" for key in _EXTERNAL_CONTEXT_KEYS if key in os.environ]


def _family_policy_projection(step_id, contract, inventory, selected, step):
    """Partial observation policy; never a native completed-reuse identity.

    Keep unknown/shared fields. Only documented other-consumer rows and the
    preparation-only selector are omitted; raw digests remain provenance.
    """
    shared = {k: v for k, v in contract.items() if k not in {
        "source_recipes", "semantic_closures", "step_dispositions", "validation"}}
    validation = contract["validation"]
    shared["validation"] = {k: v for k, v in validation.items() if k != "reviewed_semantic_step_ids"}
    shared["own_reviewed_membership"] = step_id in validation["reviewed_semantic_step_ids"]
    shared["own_dispositions"] = sorted(k for k, ids in contract["step_dispositions"].items() if step_id in ids)
    shared["own_recipe"] = contract["source_recipes"][step_id]
    shared["own_semantic_closure"] = contract["semantic_closures"].get(step_id)
    family = shared["own_recipe"]["family"]
    classification = inventory["classification"]
    partition = inventory["partition"]
    projected_inventory = {k: v for k, v in inventory.items() if k not in {
        "classification", "partition", "profiles", "test_execution"}}
    projected_inventory["classification"] = {k: v for k, v in classification.items() if k != "overrides"}
    projected_inventory["selected_classifications"] = {
        path: name for name, paths in classification["overrides"].items()
        for path in paths if path in selected}
    projected_inventory["partition"] = {k: v for k, v in partition.items() if k != "families"}
    projected_inventory["own_family"] = partition["families"][family]
    projected_inventory["own_profile"] = inventory["profiles"][shared["own_recipe"]["profile"]]
    projected_inventory["test_execution"] = {k: v for k, v in inventory["test_execution"].items()
                                              if k not in {"early_checks", "candidate_preflight"}}
    return {"kind": "partial_family_input_policy_v1", "shared_dependency_policy": shared,
            "inventory_policy": projected_inventory, "own_step": step}


def observe_family_source_recipe(step_id, *, root, contract, errors, python_resolver):
    """Observe declared family inputs with native inventory/import owners.

    No tests are collected/imported/executed. This does not attest complete
    reads, environment, producer roles, an atomic snapshot or cache eligibility.
    Capture bounds do not claim a bound on the existing import/index traversal.
    """
    from tools.core import distribution_test_selection as inventory_owner

    declared = contract.get("source_recipes") if isinstance(contract, Mapping) else None
    recipe = declared.get(step_id) if isinstance(declared, Mapping) else None
    v2 = isinstance(recipe, Mapping) and recipe.get("kind") == "test_family_input_recipe_v2"
    result = {"status": "BLOCKED", "step_id": step_id, "files": [],
              "source_observation_sha256": None, "policy_projection_sha256": None,
              "native_reuse_enabled": False, "family_reuse_eligible": False,
              "coverage_complete": False, "origin_authenticated": False,
              "source_stability_verified": False,
              "authority": "partial_source_observation_only_no_evidence_or_reuse_authority",
              "admission_blockers": ["family_recipe_v2_is_incomplete" if v2 else "family_recipe_v1_is_incomplete"], "reasons": []}
    reasons = [*errors, *source_recipe_context_reasons()]
    if not isinstance(recipe, Mapping):
        reasons.append("family_recipe_missing")
    else:
        reasons.extend(_family_recipe_errors(recipe))
    if reasons:
        return {**result, "reasons": sorted(set(reasons))}
    result["admission_blockers"].extend(recipe["coverage_gaps"])
    contents, roles, folded_paths = {}, {}, {}

    def note_role(path, role):
        if v2:
            previous = folded_paths.get(path.casefold())
            if previous is not None and previous != path:
                raise ValueError("input_paths_ambiguous")
            if path not in roles and len(roles) >= _MAX_FILES:
                raise ValueError("input_count_limit")
            folded_paths[path.casefold()] = path
        roles.setdefault(path, set()).add(role)

    def capture(path, role):
        note_role(path, role)
        if path not in contents:
            if len(contents) >= _MAX_FILES:
                raise ValueError("input_count_limit")
            content = _read(root, path)
            if sum(map(len, contents.values())) + len(content) > _MAX_TOTAL_BYTES:
                raise ValueError("input_total_size_limit")
            contents[path] = content
        return contents[path]

    def conftest_states(paths):
        states = {}
        for path in paths:
            for parent in PurePosixPath(path).parents:
                candidate = (parent / "conftest.py").as_posix()
                if candidate in states:
                    continue
                try:
                    info = (root / candidate).lstat()
                except FileNotFoundError:
                    states[candidate] = False
                else:
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("conftest_not_regular")
                    states[candidate] = True
        return states

    try:
        root = root.resolve(strict=True)
        profile_path = contract["authorities"]["test_inventory"]
        inventory = _json(capture(profile_path, "inventory_policy_provenance"))
        # The v1 observer supports the existing finite canonical inventory only.
        if "public_projection" in inventory or inventory.get("classification", {}).get("test_glob") != "tools/tests/test_*.py":
            raise ValueError("unsupported_family_inventory")
        partitions, _, _ = inventory_owner._resolve_test_partitions_from_payload(
            inventory, recipe["profile"], root=root, alternate_root=True)
        selected = partitions[recipe["family"]]
        if not selected or len(selected) > _MAX_FILES:
            raise ValueError("family_selection_size_invalid")
        states = conftest_states(selected)
        roots = sorted(set(selected) | set(recipe["launch_python_roots"]) |
                       {p for p, present in states.items() if present})
        if len(roots) > _MAX_FILES:
            raise ValueError("family_python_seed_limit")
        closure = python_resolver(roots, root=root)
        if closure.get("status") != "COMPLETE":
            return {**result, "reasons": closure.get("reasons") or ["family_python_closure_incomplete"]}
        for row in closure["files"]:
            content = capture(row["path"], "python_import_closure")
            if hashlib.sha256(content).hexdigest() != row["sha256"] or len(content) != row["size_bytes"]:
                raise ValueError("python_closure_changed_during_observation")
        for path in selected:
            capture(path, "selected_test")
        for path in recipe["declared_inputs"]:
            capture(path, "declared_input")
        presence, selections = {}, []
        if v2:
            presence, selections = _family_v2_inputs(recipe, root, note_role, capture)
        for path in contract["semantic_reuse_engine_paths"]:
            # The dependency declaration is projected above, not secretly
            # reintroduced as a full-file engine hash into this partial key.
            if path != "config/release_validation_dependency_contract.json":
                capture(path, "reuse_engine_input")
        steps_path = contract["authorities"]["proof_steps"]
        steps = _json(capture(steps_path, "steps_policy_provenance"))
        step = next(row for row in steps["steps"] if row["id"] == step_id)
        projected = _family_policy_projection(step_id, contract, inventory, selected, step)
        if (_read(root, profile_path) != contents[profile_path]
                or inventory_owner._resolve_test_partitions_from_payload(
                    inventory, recipe["profile"], root=root, alternate_root=True)[0][recipe["family"]] != selected
                or conftest_states(selected) != states):
            raise ValueError("family_selection_changed_during_observation")
        if v2 and any(_presence_state(root, path) != present for path, present in presence.items()):
            raise ValueError("presence_changed_during_observation")
        rows = [{"path": p, "roles": sorted(roles[p]), "size_bytes": len(b),
                 "sha256": hashlib.sha256(b).hexdigest()} for p, b in sorted(contents.items())]
        provenance_only = {"inventory_policy_provenance", "steps_policy_provenance"}
        source_rows = [row for row in rows if not set(row["roles"]) <= provenance_only]
        policy_sha = _digest(projected)
        observed_inputs = {"kind": "partial_family_source_observation_v2" if v2 else "partial_family_source_observation_v1",
                           "step_id": step_id, "policy_sha256": policy_sha, "selected_tests": selected,
                           "conftest_presence": states, "files": source_rows}
        if v2:
            observed_inputs.update(presence_inputs=presence, manifest_selections=selections)
            result.update(presence_inputs=presence, manifest_selections=selections,
                          path_observation_count=len(roles), captured_file_count=len(contents))
        observed_sha = _digest(observed_inputs)
    except (OSError, ValueError, UnicodeError, RuntimeError, KeyError, TypeError, StopIteration) as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else type(exc).__name__
        return {**result, "reasons": [f"family_observation_refused:{reason}"]}
    return {**result, "status": "OBSERVED_INCOMPLETE_NOT_ADMITTED", "files": rows,
            "selected_tests": selected, "conftest_presence": states,
            "source_observation_sha256": observed_sha, "policy_projection_sha256": policy_sha,
            "provenance": {"supplied_contract_sha256": _digest(contract),
                           "inventory_payload_sha256": _digest(inventory),
                           "steps_payload_sha256": _digest(steps)},
            "reasons": ["declared_inputs_observed_complete_family_inputs_not_proven"]}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _compiler_metadata_digest(value, limit):
    """Match the capture owner's canonical JSON without retaining another body."""
    digest, size = hashlib.sha256(), 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    for piece in encoder.iterencode(value):
        raw = piece.encode("utf-8")
        size += len(raw)
        if size > limit:
            raise ValueError("compiler_metadata_budget_exceeded")
        digest.update(raw)
    return digest.hexdigest()


def observe_compiler_inventory_role(inventory, *, expected_origin, capture_policy):
    """Relate supplied compiler metadata only; never capture or attest runtime code.

    Caller retains independent origin references. Lexical checks cannot re-prove
    the capturer's filesystem aliases, byte sizes or source stability.
    """
    result = {"kind": "compiler_inventory_role_observation_v1", "status": "BLOCKED",
              "authority": "supplied_metadata_relation_only_no_runtime_or_reuse_authority",
              "content_observation_sha256": None, "origin_observation_sha256": None,
              "origin_reference": None, "files_observed": 0, "reasons": [],
              **dict.fromkeys(("native_reuse_enabled", "family_reuse_eligible", "coverage_complete",
                               "origin_authenticated", "runtime_equivalence_proven", "source_stability_verified"), False)}
    try:
        ceilings = {"max_directory_entries": _MAX_FILES, "max_files": _MAX_FILES,
                    "max_file_bytes": _MAX_FILE_BYTES, "max_module_bytes": _MAX_TOTAL_BYTES,
                    "max_total_bytes": _MAX_TOTAL_BYTES}
        if (type(capture_policy) is not dict or any(type(k) is not str for k in capture_policy)
                or set(capture_policy) != set(ceilings) | {"rule"}
                or any(type(capture_policy[k]) is not int or not 1 <= capture_policy[k] <= cap for k, cap in ceilings.items())
                or type(capture_policy["rule"]) is not str or not capture_policy["rule"]
                or len(capture_policy["rule"]) > _MAX_FILE_BYTES):
            raise ValueError("compiler_capture_policy_invalid")
        policy = dict(capture_policy)
        limit = policy["max_total_bytes"]
        if (type(expected_origin) is not dict or any(type(k) is not str for k in expected_origin)
                or set(expected_origin) != {"collector_run_id", "inventory_sha256"}
                or any(type(v) is not str or not v or len(v) > _MAX_FILE_BYTES for v in expected_origin.values())):
            raise ValueError("compiler_expected_origin_invalid")
        expected = dict(expected_origin)
        strings = ("version", "authority", "collector_run_id", "status", "scope", "library_root",
                   "installed_module", "compiler_version", "manifest_sha256", "lockfile_sha256",
                   "package_sha256", "installed_module_sha256")
        fields = set(strings) | {"files", "loaded_code_attested", "snapshot_bound", "inventory_sha256"}
        if (type(inventory) is not dict or any(type(k) is not str for k in inventory) or set(inventory) != fields
                or any(type(inventory[k]) is not str or not inventory[k] or len(inventory[k]) > _MAX_FILE_BYTES for k in strings)
                or inventory["loaded_code_attested"] is not False or inventory["snapshot_bound"] is not False):
            raise ValueError("compiler_inventory_shape_invalid")
        body = {k: inventory[k] for k in strings}
        body.update(loaded_code_attested=False, snapshot_bound=False)
        if (body["version"] != "v1" or body["authority"] != "producer_preinvocation_installation_capture"
                or body["status"] != "captured" or body["scope"] != "positive_compiler_library_reads_only"):
            raise ValueError("compiler_inventory_role_invalid")
        hashes = ("manifest_sha256", "lockfile_sha256", "package_sha256", "installed_module_sha256")
        original = inventory["inventory_sha256"]
        if any(type(v) is not str or not re.fullmatch(r"[a-f0-9]{64}", v) for v in [original, expected["inventory_sha256"], *[body[k] for k in hashes]]):
            raise ValueError("compiler_inventory_hash_invalid")
        for key in ("library_root", "installed_module"):
            value = body[key]
            relative = value[3:] if re.match(r"^[A-Za-z]:/", value) else value.lstrip("/")
            if (not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute())
                    or PurePosixPath(value).as_posix() != value or not _relative(relative)):
                raise ValueError("compiler_location_metadata_invalid")
        if PurePosixPath(body["installed_module"]).parent.as_posix() != body["library_root"]:
            raise ValueError("compiler_location_metadata_invalid")
        files = inventory["files"]
        if type(files) is not dict or not 1 <= len(files) <= min(policy["max_files"], policy["max_directory_entries"]):
            raise ValueError("compiler_file_metadata_invalid")
        held, folded = {}, set()
        for name, row in files.items():
            if (type(name) is not str or not _relative(name) or len(name) > _MAX_FILE_BYTES or PurePosixPath(name).name != name
                    or not name.startswith("lib") or not name.endswith(".d.ts") or name.casefold() in folded
                    or type(row) is not dict or any(type(k) is not str for k in row) or set(row) != {"sha256", "host_text_sha256"}
                    or any(type(v) is not str or not re.fullmatch(r"[a-f0-9]{64}", v) for v in row.values())):
                raise ValueError("compiler_file_metadata_invalid")
            folded.add(name.casefold())
            held[name] = dict(row)
        body["files"] = held
        if body["collector_run_id"] != expected["collector_run_id"] or original != expected["inventory_sha256"]:
            raise ValueError("compiler_origin_reference_mismatch")
        if _compiler_metadata_digest(body, limit) != original:
            raise ValueError("compiler_inventory_digest_mismatch")
        content = {"kind": "compiler_library_content_observation_v1", "compiler_version": body["compiler_version"],
                   "files": held, **{k: body[k] for k in hashes}}
        content_sha = _compiler_metadata_digest(content, limit)
        origin = {"inventory_sha256": original,
                  "collector_run_id_sha256": _compiler_metadata_digest(body["collector_run_id"], limit),
                  "installation_context_sha256": _compiler_metadata_digest({k: body[k] for k in ("library_root", "installed_module")}, limit),
                  "capture_policy_sha256": _compiler_metadata_digest(policy, limit)}
        origin_sha = _compiler_metadata_digest({"kind": result["kind"], "content_observation_sha256": content_sha, **origin}, limit)
    except (ValueError, TypeError, KeyError, RuntimeError, OverflowError) as exc:
        reason = str(exc) if type(exc) is ValueError and re.fullmatch(r"compiler_[a-z_]+", str(exc)) else "compiler_inventory_metadata_refused"
        return {**result, "reasons": [reason]}
    return {**result, "status": "OBSERVED_INCOMPLETE_NOT_ADMITTED", "content_observation_sha256": content_sha,
            "origin_observation_sha256": origin_sha, "origin_reference": origin, "files_observed": len(held),
            "reasons": ["referenced_content_only_capture_origin_and_runtime_not_authenticated"]}


def observe_process_launch_context(command: list[str], cwd: Path, environment: Mapping[str, str]) -> dict[str, Any]:
    """Describe the exact supplied Popen inputs; do not persist paths or values."""
    result = {"kind": "release_process_context_v1", "status": "BLOCKED", "reasons": []}
    if (not isinstance(command, list) or not command or len(command) > 256
            or any(not isinstance(v, str) or not v or "\x00" in v for v in command)
            or not isinstance(environment, Mapping) or len(environment) > 4096
            or any(not isinstance(k, str) or not k or "=" in k or "\x00" in k
                   or not isinstance(v, str) or "\x00" in v for k, v in environment.items())):
        return {**result, "reasons": ["invalid_launch_context"]}
    normalized = {(k.upper() if sys.platform == "win32" else k): v for k, v in environment.items()}
    if len(normalized) != len(environment):
        return {**result, "reasons": ["ambiguous_environment_keys"]}
    unsupported = sorted(set(normalized) & set(_EXTERNAL_CONTEXT_KEYS))
    if unsupported:
        return {**result, "reasons": [f"unsupported_context_present:{key}" for key in unsupported]}
    try:
        directory = str(cwd.resolve(strict=True))
    except (OSError, RuntimeError):
        return {**result, "reasons": ["launch_directory_unavailable"]}
    return {**result, "status": "OBSERVED", "command_sha256": _digest(command),
            "cwd_sha256": _digest(directory), "environment_sha256": _digest(normalized)}


def observe_current_process_context() -> dict[str, Any]:
    """Child-side observation of interpreter argv, actual cwd and environment."""
    return observe_process_launch_context(list(sys.orig_argv), Path.cwd(), dict(os.environ))


def reconcile_process_context(launched: Any, reported: Any) -> dict[str, Any]:
    """Match metadata only; the existing execution/artifact chain supplies origin."""
    fields = {"kind", "status", "reasons", "command_sha256", "cwd_sha256", "environment_sha256"}
    valid = lambda row: (isinstance(row, Mapping) and set(row) == fields
                         and row.get("kind") == "release_process_context_v1" and row.get("status") == "OBSERVED"
                         and row.get("reasons") == [] and all(isinstance(row.get(k), str) and
                             re.fullmatch(r"[0-9a-f]{64}", row[k]) for k in fields if k.endswith("_sha256")))
    if reported is None:
        status = "NOT_REPORTED"
    elif not valid(launched) or not valid(reported):
        status = "UNSUPPORTED_OR_INCOMPLETE"
    else:
        status = "MATCHED_DECLARATIONS" if launched == reported else "MISMATCH"
    return {"status": status, "matches": status == "MATCHED_DECLARATIONS", "native_reuse_enabled": False,
            "authority": "metadata_match_only_existing_execution_chain_required"}


def compiled_input_payload_identity(payload: Any) -> dict[str, Any]:
    meta = payload.get("_meta") if isinstance(payload, dict) else None
    return {"source_fingerprint": meta.get("source_fingerprint") if isinstance(meta, dict) else None,
            "semantic_sha256": _digest(payload) if isinstance(payload, dict) else None}


def reconcile_source_recipe_execution(observation: Any, execution: Any) -> dict[str, Any]:
    """Join supplied source/child metadata without authenticating a native run."""
    result = {"status": "BLOCKED", "native_reuse_enabled": False, "origin_authenticated": False,
              "authority": "metadata_relation_only_native_receipt_and_source_stability_required"}
    if not isinstance(observation, Mapping) or not isinstance(execution, Mapping):
        return result
    inputs = observation.get("compiled_inputs")
    summary = execution.get("raw_artifact_summary")
    child = summary.get("source_input_context") if isinstance(summary, Mapping) else None
    if (observation.get("status") != "OBSERVED_NOT_ADMITTED" or not isinstance(inputs, list) or len(inputs) != 1
            or observation.get("step_id") != execution.get("id") or execution.get("execution_status") != "COMPLETED"
            or execution.get("passed") is not True or execution.get("returncode") != 0
            or execution.get("timed_out") is not False or execution.get("fresh_artifact_required") is not True
            or execution.get("fresh_artifact_produced") is not True or not isinstance(child, Mapping)):
        return result
    match = reconcile_process_context(execution.get("launch_context"), child.get("process"))
    expected = inputs[0]
    if not isinstance(expected, Mapping) or not all(isinstance(expected.get(k), str) and
            re.fullmatch(r"[0-9a-f]{64}", expected[k]) for k in ("source_fingerprint", "semantic_sha256")):
        return result
    if (match["matches"] and child.get("compiled_doctrine_source_fingerprint") == expected.get("source_fingerprint")
            and child.get("compiled_doctrine_semantic_sha256") == expected.get("semantic_sha256")
            and isinstance(expected.get("semantic_sha256"), str)):
        result.update(status="MATCHED_DECLARED_SOURCE_AND_EXECUTION", source_observation_sha256=observation.get("source_observation_sha256"))
    return result


def _compiled_input_observations(recipe, contents):
    observations = []
    for binding in recipe.get("producer_bindings", []):
        manifest_path = binding["manifest"]
        manifest = _json(contents[manifest_path])
        packs = {entry["path"]: _json(contents[(PurePosixPath(manifest_path).parent / entry["path"]).as_posix()])
                 for entry in manifest["active_doctrines"]}
        expected = doctrine_source_fingerprint_from_payloads(manifest, packs)
        output = _json(contents[binding["output"]])
        meta = output.get("_meta")
        if not isinstance(meta, dict) or meta.get("source_fingerprint") != expected:
            raise ValueError("compiled_input_stale_or_unbound")
        observations.append({"owner": binding["owner"], "manifest": manifest_path, "output": binding["output"],
                             "source_fingerprint": expected, "output_sha256": hashlib.sha256(contents[binding["output"]]).hexdigest(),
                             "semantic_sha256": compiled_input_payload_identity(output)["semantic_sha256"],
                             "status": "EXISTING_OUTPUT_FRESHNESS_MATCHED", "producer_execution_verified": False})
    return observations


def observe_source_recipe(
    step_id: str, *, root: Path, recipe: Mapping[str, Any] | None,
    python_closure: Mapping[str, Any] | None, errors: list[str],
) -> dict[str, Any]:
    """Capture roles from supplied declarations/import observation; no admission.

    COMPLETE is deliberately not returned: child context and the existing
    producer prerequisite still need a separate reviewed evidence binding.
    The native owner validates the full contract and acquires the import
    observation itself; supplied data cannot authenticate that acquisition.
    """
    result: dict[str, Any] = {
        "status": "BLOCKED", "step_id": step_id, "files": [],
        "source_observation_sha256": None, "native_reuse_enabled": False,
        "producer_prerequisite": "NOT_VERIFIED", "child_context_verified": False,
        "admission_blockers": ["producer_prerequisite_not_bound", "child_context_not_bound"],
        "authority": "source_observation_only_no_reuse_evidence_or_release_authority",
        "reasons": [],
    }
    if errors or not isinstance(recipe, Mapping):
        result["reasons"] = errors or ["source_recipe_missing"]
        return result
    reasons = [*_recipe_errors(recipe), *source_recipe_context_reasons()]
    if reasons:
        result["reasons"] = reasons
        return result
    root = root.resolve(strict=True)
    closure = python_closure
    if not isinstance(closure, Mapping):
        result["reasons"] = ["python_closure_observation_missing"]
        return result
    if closure["status"] != "COMPLETE":
        result["reasons"] = closure["reasons"]
        return result
    roles: dict[str, set[str]] = {}
    for role in _ROLES:
        for path in recipe[role]:
            roles.setdefault(path, set()).add(role)
    for path in closure["paths"]:
        roles.setdefault(path, set()).add("python_import_closure")
    contents: dict[str, bytes] = {}

    def capture(relative):
        if relative not in contents:
            if len(contents) >= _MAX_FILES:
                raise ValueError("input_count_limit")
            content = _read(root, relative)
            if sum(map(len, contents.values())) + len(content) > _MAX_TOTAL_BYTES:
                raise ValueError("input_total_size_limit")
            contents[relative] = content
        return contents[relative]

    try:
        for row in recipe["manifest_expansions"]:
            manifest = row["manifest"]
            payload = _json(capture(manifest))
            list_field, path_field = _SELECTOR.fullmatch(row["json_pointer"]).groups()
            entries = payload.get(list_field)
            if not isinstance(entries, list) or not entries or len(entries) > _MAX_MANIFEST_ENTRIES:
                raise ValueError("manifest_entries_invalid")
            seen_paths, seen_ids = set(), set()
            for entry in entries:
                if not isinstance(entry, dict) or not _relative(entry.get(path_field)):
                    raise ValueError("manifest_entry_path_invalid")
                relative = (PurePosixPath(manifest).parent / entry[path_field]).as_posix()
                key = relative.casefold()
                entry_id = entry.get("id")
                if key in seen_paths or (entry_id is not None and (
                        not isinstance(entry_id, str) or not entry_id or entry_id in seen_ids)):
                    raise ValueError("manifest_entries_ambiguous")
                seen_paths.add(key)
                if entry_id is not None:
                    seen_ids.add(entry_id)
                capture(relative)
                roles.setdefault(relative, set()).add("manifest_data")
        if len({p.casefold() for p in roles}) != len(roles):
            raise ValueError("input_paths_ambiguous")
        for relative in sorted(roles):
            capture(relative)
        # Do not accept a Python file changed between import traversal and capture.
        for row in closure["files"]:
            content = contents[row["path"]]
            if hashlib.sha256(content).hexdigest() != row["sha256"] or len(content) != row["size_bytes"]:
                raise ValueError("python_closure_changed_during_observation")
        compiled_inputs = _compiled_input_observations(recipe, contents)
        rows = [{"path": path, "roles": sorted(roles[path]), "size_bytes": len(content),
                 "sha256": hashlib.sha256(content).hexdigest()}
                for path, content in sorted(contents.items())]
    except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
        # Paths/content and environment values are not copied into diagnostics.
        result["reasons"] = [f"source_input_unavailable:{type(exc).__name__}"]
        if type(exc) is ValueError and str(exc) in {
                "unsafe_input_path", "aliased_or_escaping_input_path", "nonregular_or_oversize_input",
                "oversize_input", "input_count_limit", "input_total_size_limit", "manifest_entries_invalid",
                "manifest_entry_path_invalid", "manifest_entries_ambiguous", "ambiguous_json_key",
                "json_object_required", "input_paths_ambiguous", "python_closure_changed_during_observation",
                "compiled_input_stale_or_unbound"}:
            result["reasons"].append(str(exc))
        return result
    digest = hashlib.sha256(json.dumps({"recipe": recipe, "files": rows}, ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    result.update(status="OBSERVED_NOT_ADMITTED", files=rows,
                  source_observation_sha256=digest,
                  context="product_local_parent_environment_only")
    if compiled_inputs:
        result.update(compiled_inputs=compiled_inputs, producer_prerequisite="EXISTING_OUTPUT_FRESHNESS_MATCHED",
                      producer_execution_verified=False,
                      admission_blockers=["producer_order_not_bound", "child_context_not_bound"])
    return result

"""Conservative source-bound admission of manually reviewed analysis deltas.

This is a cadence input, not proof that arbitrary Python cannot affect setup.
Unknown/eager/signature changes stay on the independent-environment lane.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import re
import subprocess
import sys
from pathlib import Path


RISK_REVIEW = {
    "installation_behavior": "unchanged",
    "dependency_setup": "unchanged",
    "runtime_support": "unchanged",
    "os_support": "unchanged",
    "module_initialization": "unchanged_or_declarative",
}


def _dump(node):
    return ast.dump(node, include_attributes=False)


def _imports(tree):
    return {_dump(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}


def _literal_value(node):
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError):
        return False


def _declarative_value(node):
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError):
        # Self-file metadata only; never read a file or inspect host state.
        return bool(re.fullmatch(
            r"Path\(__file__\)\.resolve\(\)\.parents\[\d+\](?: / '[^']*')*",
            ast.unparse(node),
        ))


def _reject_initialized_symbols(tree, definitions, changed):
    """Conservatively follow syntactically visible initializer calls, not intent."""
    eager_tree = copy.deepcopy(tree)
    def strip_bodies(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child.body = []
            else:
                strip_bodies(child)
    strip_bodies(eager_tree)
    def calls(node):
        found = set()
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""
            found.update(k for k in definitions if k.split(".")[-1] == name or k == name + ".__init__")
        return found
    pending, visited = list(calls(eager_tree)), set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        if key in changed:
            raise ValueError("changed_symbol_reachable_during_initialization")
        visited.add(key)
        pending.extend(calls(definitions[key]))


def inspect_python_delta(before: bytes | None, after: bytes) -> dict:
    """AST boundary check; body intent and initializer reachability need review."""
    old = ast.parse(before.decode("utf-8-sig")) if before is not None else ast.Module(body=[], type_ignores=[])
    new = ast.parse(after.decode("utf-8-sig"))
    old_copy, new_copy = copy.deepcopy(old), copy.deepcopy(new)
    old_defs, new_defs = {}, {}

    def erase(tree, definitions, prefix=""):
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = prefix + node.name
                if key in definitions:
                    raise ValueError("duplicate_function_identity")
                definitions[key] = copy.deepcopy(node)
                node.body = [ast.Pass()]
            elif isinstance(node, ast.ClassDef):
                erase(node, definitions, prefix + node.name + ".")

    erase(old_copy, old_defs)
    erase(new_copy, new_defs)
    changed = sorted(k for k in old_defs.keys() | new_defs.keys()
                     if k not in old_defs or k not in new_defs
                     or _dump(old_defs[k]) != _dump(new_defs[k]))
    if not changed:
        raise ValueError("no_changed_function_bodies")
    _reject_initialized_symbols(old, old_defs, set(changed))
    _reject_initialized_symbols(new, new_defs, set(changed))
    for key in changed:
        if key.split(".")[-1] in {"__init__", "__new__", "__init_subclass__", "__class_getitem__"}:
            raise ValueError("initialization_method_changed")
        if key not in new_defs:
            raise ValueError("removed_function")
        node = new_defs[key]
        if node.decorator_list:
            raise ValueError("decorated_function_changed")
        if key in old_defs:
            a, b = copy.deepcopy(old_defs[key]), copy.deepcopy(node)
            a.body, b.body = [], []
            if _dump(a) != _dump(b):
                raise ValueError("function_signature_changed")
            if _imports(old_defs[key]) != _imports(node):
                raise ValueError("lazy_import_boundary_changed")
        elif (any(not _literal_value(x) for x in node.args.defaults)
              or any(x is not None and not _literal_value(x) for x in node.args.kw_defaults)
              or _imports(node)):
            raise ValueError("new_function_default_or_import")

    # New functions may be added, but not eager declarations or changed classes.
    def skeleton(tree):
        body = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.ClassDef):
                node.body = skeleton(node)
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                body.append(node)
        return body

    old_skeleton, new_skeleton = skeleton(old_copy), skeleton(new_copy)
    old_top = {_dump(n): n for n in old.body if isinstance(n, (ast.Import, ast.ImportFrom))}
    new_top = {_dump(n): n for n in new.body if isinstance(n, (ast.Import, ast.ImportFrom))}
    if old_top.keys() - new_top.keys():
        raise ValueError("existing_top_import_changed_or_removed")
    def bindings(node):
        if isinstance(node, ast.Import):
            return {a.asname or a.name.split(".")[0] for a in node.names}
        return {a.asname or a.name for a in node.names}
    old_bindings = {name for n in old.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for name in [n.name]}
    old_bindings.update(name for n in old_top.values() for name in bindings(n))
    old_bindings.update(n.id for statement in old.body if isinstance(statement, (ast.Assign, ast.AnnAssign))
                        for n in ast.walk(statement) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
    for key in new_top.keys() - old_top.keys():
        if "*" in bindings(new_top[key]) or bindings(new_top[key]) & old_bindings:
            raise ValueError("new_import_rebinds_existing_or_unknown_names")
    future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                 and any(a.name == "annotations" for a in n.names) for n in new.body)
    if set(new_defs) - set(old_defs) and not future:
        raise ValueError("new_definition_requires_deferred_annotations")
    if before is None:
        path_bindings = [n for n in new.body if isinstance(n, ast.ImportFrom)
                         and n.module == "pathlib" and not n.level
                         and any(a.name == "Path" and not a.asname for a in n.names)]
        for node in new_skeleton:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets) and _declarative_value(node.value):
                if not _literal_value(node.value):
                    all_path_imports = [n for n in new_top.values() if "Path" in bindings(n)]
                    if len(path_bindings) != 1 or len(all_path_imports) != 1 or any(
                            isinstance(t, ast.Name) and t.id == "Path" for statement in new.body
                            if isinstance(statement, ast.Assign) for t in statement.targets):
                        raise ValueError("self_file_metadata_requires_stdlib_path_binding")
                continue
            raise ValueError("new_module_has_eager_behavior")
    elif [_dump(n) for n in old_skeleton] != [_dump(n) for n in new_skeleton]:
        raise ValueError("module_or_class_initialization_changed")
    return {"changed_symbols": changed,
            "added_top_imports": [new_top[k] for k in sorted(new_top.keys() - old_top.keys())]}


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, timeout=30).stdout


def _local_import_paths(node, importer: str, exists, *, baseline=False) -> list[str]:
    if isinstance(node, ast.Import) and len(node.names) > 1:
        return sorted({p for alias in node.names for p in _local_import_paths(
            ast.Import(names=[alias]), importer, exists, baseline=baseline)})
    if isinstance(node, ast.ImportFrom):
        if node.level:
            parts = importer.split("/")[:-1]
            if node.level > len(parts):
                raise ValueError("relative_import_escapes_package")
            parts = parts[:len(parts) - node.level + 1]
            module = "/".join(parts + (node.module.split(".") if node.module else []))
        else:
            module = (node.module or "").replace(".", "/")
        modules = [module] + [module + "/" + n.name for n in node.names if n.name != "*"]
    else:
        modules = [n.name.replace(".", "/") for n in node.names]
    result = set()
    for module in modules:
        candidates = [module + ".py", module + "/__init__.py"]
        result.update(p for p in candidates if exists(p))
        parts = module.split("/")
        result.update(p for i in range(1, len(parts)) if exists(p := "/".join(parts[:i]) + "/__init__.py"))
    top = (node.module or "").split(".")[0] if isinstance(node, ast.ImportFrom) else node.names[0].name.split(".")[0]
    if not result and (getattr(node, "level", 0) or (not baseline and top not in sys.stdlib_module_names | {"__future__"})):
        raise ValueError("new_or_unresolved_external_import")
    return sorted(result)


def validate_analysis_delta_review(review: dict, *, root: Path,
                                   changed_paths: list[str], eligible_paths: set[str],
                                   proof_seeds: list[str]) -> dict[str, dict]:
    """Regenerate both source versions from exact Git identities, never user text."""
    if (not isinstance(review, dict)
            or set(review) != {"meta", "actor_type", "unknown_risks", "baseline_source_commit", "current_source_commit", "files"}
            or review.get("meta") != {"kind": "nexora.installation_analysis_delta_review", "version": "v1"}
            or review.get("actor_type") != "codex_agent_manual"
            or review.get("unknown_risks") != []):
        raise ValueError("unresolved_or_unknown_analysis_delta_review")
    base, current = review.get("baseline_source_commit"), review.get("current_source_commit")
    if any(not isinstance(c, str) or not re.fullmatch(r"[0-9a-f]{40}", c) for c in (base, current)):
        raise ValueError("exact_delta_source_commits_required")
    git_root = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    prefix = root.resolve().relative_to(git_root).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    scope = prefix.rstrip("/") or "."
    _git(git_root, "merge-base", "--is-ancestor", base, current)
    if (_git(git_root, "diff", current, "--", scope).strip()
            or _git(git_root, "ls-files", "--others", "--exclude-standard", "--", scope).strip()):
        raise ValueError("delta_review_current_source_is_dirty")
    changes = sorted(p[len(prefix):] for p in _git(git_root, "diff", "--name-only", "-z", base, current, "--", scope).decode().split("\0") if p)
    if changes != changed_paths:
        raise ValueError("delta_review_changed_path_inventory_mismatch")
    rows = review.get("files")
    if not isinstance(rows, list) or not rows:
        raise ValueError("analysis_delta_review_files_missing")
    paths = [r.get("path") for r in rows if isinstance(r, dict)]
    if (len(paths) != len(rows) or any(not isinstance(p, str) for p in paths)
            or len(set(paths)) != len(paths) or set(paths) - eligible_paths):
        raise ValueError("analysis_delta_review_contains_ineligible_or_duplicate_paths")
    cache = {}
    inventory = {
        commit: {p[len(prefix):] for p in _git(git_root, "ls-tree", "-r", "--name-only", "-z", commit, "--", scope).decode().split("\0") if p}
        for commit in (base, current)
    }

    def read(commit, path):
        key = commit, path
        if key not in cache:
            cache[key] = (_git(git_root, "show", f"{commit}:{prefix}{path}")
                          if path in inventory[commit] else None)
        return cache[key]

    # Only top-level imports actually reachable at baseline initialization count.
    eager, pending = set(), list(proof_seeds)
    while pending:
        path = pending.pop()
        if path in eager:
            continue
        data = read(base, path)
        if data is None:
            raise ValueError("baseline_proof_owner_missing")
        eager.add(path)
        for node in ast.parse(data.decode("utf-8-sig")).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                pending.extend(_local_import_paths(node, path, lambda p: read(base, p) is not None, baseline=True))

    def check_new_import(node, importer, visiting):
        for path in _local_import_paths(node, importer, lambda p: read(current, p) is not None):
            if path in eager:
                continue
            if path in visiting or read(base, path) is not None:
                raise ValueError("new_initialization_of_existing_module_or_cycle")
            data = read(current, path)
            inspected = inspect_python_delta(None, data)
            if path not in paths:
                raise ValueError("new_declarative_module_is_not_reviewed")
            for nested in inspected["added_top_imports"]:
                check_new_import(nested, path, visiting | {path})

    result = {}
    for row in rows:
        if set(row) != {"path", "before_sha256", "after_sha256", "worktree_after_sha256", "changed_symbols", "classification", "risk_review", "rationale"}:
            raise ValueError("unknown_analysis_review_file_fields")
        path = row["path"]
        if (not path or "\\" in path or ":" in path or path.startswith("/")
                or any(p in {".", "..", ""} for p in path.split("/"))
                or not (root / path).resolve().is_relative_to(root.resolve())):
            raise ValueError("unsafe_analysis_review_path")
        before, after = read(base, path), read(current, path)
        actual = (root / path).read_bytes()
        # Bind real bytes separately from Git's canonical LF blob. Admit only
        # explicit CRLF checkout representation, never arbitrary clean filters.
        if (after is None or (actual != after and actual.replace(b"\r\n", b"\n") != after)
                or row.get("worktree_after_sha256") != hashlib.sha256(actual).hexdigest()):
            raise ValueError("delta_current_bytes_mismatch")
        before_hash = hashlib.sha256(before).hexdigest() if before is not None else None
        after_hash = hashlib.sha256(after).hexdigest()
        if row.get("before_sha256") != before_hash or row.get("after_sha256") != after_hash:
            raise ValueError("delta_review_source_hash_mismatch")
        inspected = inspect_python_delta(before, after)
        if (row.get("changed_symbols") != inspected["changed_symbols"]
                or row.get("classification") != "analysis_only"
                or row.get("risk_review") != RISK_REVIEW
                or not isinstance(row.get("rationale"), str) or not row["rationale"].strip()):
            raise ValueError("delta_review_membership_or_risk_mismatch")
        for node in inspected["added_top_imports"]:
            check_new_import(node, path, {path})
        result[path] = {"before_sha256": before_hash, "after_sha256": after_hash,
                        "worktree_after_sha256": row["worktree_after_sha256"],
                        "changed_symbols": inspected["changed_symbols"]}
    return result

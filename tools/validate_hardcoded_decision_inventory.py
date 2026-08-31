from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.validator_progress import ValidatorProgress
from tools.core.validator_policy_registry import hardcoded_decision_inventory_policy


RAW_OUTPUT_PATH = RAW_DIR / "hardcoded_decision_inventory_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "hardcoded_decision_inventory_validation.md"


@lru_cache(maxsize=1)
def _inventory_policy() -> dict[str, Any]:
    """Load one strict policy snapshot per validator process."""
    return hardcoded_decision_inventory_policy()
ABSOLUTE_PATH_RE = re.compile(r"(^[A-Za-z]:[\\/]|^/Users/|^/home/|^/var/|^/tmp/)")
RELEASE_LITERAL_RE = re.compile(r"^\d+\.\d+\.\d+$")
@dataclass(frozen=True)
class StringLiteral:
    file: str
    line: int
    value: str
    assignment_name: str | None
    key_name: str | None


@dataclass(frozen=True)
class CentralContractFallback:
    file: str
    line: int
    contract: str
    section: str
    fallback: Any


@dataclass(frozen=True)
class ModuleDecisionTable:
    file: str
    line: int
    assignment_name: str
    row_count: int
    matching_fields: tuple[str, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _is_test_file(path: Path) -> bool:
    relative_parts = set(path.relative_to(ROOT).parts)
    return bool(relative_parts & set(_inventory_policy()["allowed_test_or_fixture_parts"]))


def _parse_python_source(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _read_python_literals(path: Path, tree: ast.Module | None = None) -> list[StringLiteral]:
    tree = tree if tree is not None else _parse_python_source(path)
    if tree is None:
        return []
    relative = str(path.relative_to(ROOT)).replace("\\", "/")
    parent_names: dict[ast.AST, str] = {}
    key_names: dict[ast.AST, str] = {}

    class ParentVisitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            name = None
            if node.targets and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
            for child in ast.walk(node.value):
                parent_names[child] = name or ""
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            name = node.target.id if isinstance(node.target, ast.Name) else ""
            if node.value is not None:
                for child in ast.walk(node.value):
                    parent_names[child] = name
            self.generic_visit(node)

        def visit_Dict(self, node: ast.Dict) -> None:
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    key_text = str(key.value)
                    for child in ast.walk(value):
                        key_names[child] = key_text
            self.generic_visit(node)

        def visit_Compare(self, node: ast.Compare) -> None:
            for comparator in node.comparators:
                for child in ast.walk(comparator):
                    parent_names.setdefault(child, "comparison")
            self.generic_visit(node)

    ParentVisitor().visit(tree)
    rows: list[StringLiteral] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            rows.append(
                StringLiteral(
                    file=relative,
                    line=int(getattr(node, "lineno", 0) or 0),
                    value=node.value,
                    assignment_name=parent_names.get(node) or None,
                    key_name=key_names.get(node) or None,
                )
            )
    return rows


def _contains_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def _nonempty_literal(node: ast.AST) -> tuple[bool, Any]:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return False, None
    if value in (None, False, 0, "", (), [], {}):
        return False, value
    return True, _json_safe_literal(value)


def _json_safe_literal(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_literal(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_literal(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe_literal(item) for item in value), key=lambda item: repr(item))
    return value


def _read_central_contract_fallbacks(
    path: Path,
    tree: ast.Module | None = None,
) -> list[CentralContractFallback]:
    tree = tree if tree is not None else _parse_python_source(path)
    if tree is None:
        return []
    relative = str(path.relative_to(ROOT)).replace("\\", "/")
    rows: list[CentralContractFallback] = []

    class ScopeVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.nodes: list[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.nodes.append(node)
            super().generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    scopes: list[list[ast.stmt]] = [tree.body]
    scopes.extend(node.body for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    for body in scopes:
        visitor = ScopeVisitor()
        for statement in body:
            visitor.visit(statement)
        scope_nodes = visitor.nodes
        alias_roots = {"DOCTRINE": "DOCTRINE", "POLICY": "POLICY", "REGISTRY": "REGISTRY"}
        changed = True
        while changed:
            changed = False
            for node in scope_nodes:
                value = None
                targets: list[ast.AST] = []
                if isinstance(node, ast.Assign):
                    value = node.value
                    targets = list(node.targets)
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    value = node.value
                    targets = [node.target]
                if value is None:
                    continue
                source_alias = next((alias for alias in sorted(alias_roots) if _contains_name(value, alias)), "")
                if not source_alias:
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in alias_roots:
                        alias_roots[target.id] = alias_roots[source_alias]
                        changed = True
        for node in scope_nodes:
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
                continue
            alias = next((name for name in sorted(alias_roots) if _contains_name(node.func.value, name)), "")
            if not alias:
                continue
            is_nonempty, fallback = _nonempty_literal(node.args[1])
            if not is_nonempty:
                continue
            section = "<dynamic>"
            try:
                section_value = ast.literal_eval(node.args[0])
                if isinstance(section_value, str):
                    section = section_value
            except (ValueError, TypeError, SyntaxError):
                pass
            rows.append(
                CentralContractFallback(
                    file=relative,
                    line=int(getattr(node, "lineno", 0) or 0),
                    contract=alias_roots[alias],
                    section=section,
                    fallback=fallback,
                )
            )
    return rows


def _read_module_decision_tables(
    path: Path,
    tree: ast.Module | None = None,
) -> list[ModuleDecisionTable]:
    tree = tree if tree is not None else _parse_python_source(path)
    if tree is None:
        return []
    relative = str(path.relative_to(ROOT)).replace("\\", "/")
    return _module_decision_tables_from_tree(tree, relative, _inventory_policy())


def _module_decision_tables_from_tree(
    tree: ast.Module,
    relative: str,
    policy: dict[str, Any],
) -> list[ModuleDecisionTable]:
    field_markers = set(policy["decision_table_row_field_markers"])
    min_rows = int(policy["decision_table_min_rows"])
    min_fields = int(policy["decision_table_min_matching_fields"])
    rows: list[ModuleDecisionTable] = []
    for statement in tree.body:
        assignment_name = ""
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            assignment_name = statement.targets[0].id
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            assignment_name = statement.target.id
            value = statement.value
        if not assignment_name or not isinstance(value, ast.Dict):
            continue
        row_fields: list[set[str]] = []
        for row_value in value.values:
            if not isinstance(row_value, ast.Dict):
                continue
            keys = {
                str(key.value)
                for key in row_value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if keys:
                row_fields.append(keys)
        if len(row_fields) < min_rows:
            continue
        field_counts = Counter(field for fields in row_fields for field in fields)
        matching_fields = tuple(sorted(field for field in field_markers if field_counts[field] >= min_rows))
        if len(matching_fields) < min_fields:
            continue
        rows.append(
            ModuleDecisionTable(
                file=relative,
                line=int(getattr(statement, "lineno", 0) or 0),
                assignment_name=assignment_name,
                row_count=len(row_fields),
                matching_fields=matching_fields,
            )
        )
    return rows


def _python_files() -> list[Path]:
    scan_root = ROOT / _inventory_policy()["scan_root"]
    return [
        path
        for path in scan_root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _looks_decision_like(row: StringLiteral) -> bool:
    haystack = " ".join(
        [
            row.file.lower(),
            (row.assignment_name or "").lower(),
            (row.key_name or "").lower(),
            row.value.lower(),
        ]
    )
    return any(word in haystack for word in _inventory_policy()["decision_words"])


def _candidate_category(row: StringLiteral) -> str:
    text = " ".join(
        [
            row.file.lower(),
            row.value.lower(),
            (row.assignment_name or "").lower(),
            (row.key_name or "").lower(),
        ]
    )
    if "timeout" in text or "budget" in text:
        return "runtime_budget_or_timeout"
    if "release" in text or RELEASE_LITERAL_RE.match(row.value):
        return "release_or_phase_decision"
    if "claim" in text or "gate" in text:
        return "claim_or_gate_decision"
    if "policy" in text or "required" in text or "allowed" in text:
        return "policy_or_contract_decision"
    if "profile" in text or "mode" in text or "scope" in text:
        return "profile_mode_or_scope_decision"
    return "other_decision_like"


def _looks_regex_or_parser_pattern(row: StringLiteral) -> bool:
    assignment = row.assignment_name or ""
    value = row.value
    if assignment.endswith("_RE") or assignment.endswith("_PATTERN"):
        return True
    return "\\b" in value or "\\s" in value or "(?:" in value or re.search(r"\[[^\]]+\]|\{\\d", value) is not None


def _looks_path_or_artifact_pointer(row: StringLiteral) -> bool:
    value = row.value.replace("\\", "/")
    lowered = value.lower()
    if any(lowered.endswith(suffix) for suffix in _inventory_policy()["path_or_artifact_suffixes"]):
        return True
    return "/" in value and not any(space in value for space in (" ", "\n", "\t"))


def _looks_report_or_message(row: StringLiteral) -> bool:
    value = row.value.strip()
    if "\n" in value:
        return True
    if value.startswith(("# ", "## ", "| ", "- ")):
        return True
    return len(value.split()) >= 4 and not (row.assignment_name or "").isupper()


def _looks_check_or_artifact_id(row: StringLiteral) -> bool:
    value = row.value.strip()
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{2,}", value)) and (row.key_name is None or row.key_name in {"name", "id", "artifact", "validator"})


def _triage(row: StringLiteral) -> dict[str, str]:
    assignment = row.assignment_name or ""
    if ABSOLUTE_PATH_RE.search(row.value):
        return {"class": "machine_absolute_path", "action": "must_fix"}
    if assignment == "_POLICY_CACHE":
        return {"class": "policy_cache_shape", "action": "usually_keep_local_unless_duplicated_contract"}
    if assignment.startswith("FALLBACK_"):
        return {"class": "minimal_runtime_fallback_shape", "action": "usually_keep_local_unless_duplicated_contract"}
    if assignment.startswith("REQUIRED_") and assignment.endswith("FIELDS"):
        return {"class": "contract_field_shape", "action": "usually_keep_local_unless_duplicated_contract"}
    if _is_release_decision_literal(row):
        return {"class": "release_literal", "action": "inspect_for_registry"}
    if _looks_regex_or_parser_pattern(row):
        return {"class": "regex_or_parser_pattern", "action": "usually_keep_local"}
    if _looks_path_or_artifact_pointer(row):
        return {"class": "artifact_or_config_pointer", "action": "usually_keep_local_unless_shared_policy"}
    if _looks_report_or_message(row):
        return {"class": "report_or_message_text", "action": "exclude_from_refactor_queue"}
    if _looks_check_or_artifact_id(row):
        return {"class": "check_or_artifact_identifier", "action": "usually_keep_local_unless_duplicated_contract"}
    if any(marker in assignment for marker in _inventory_policy()["policy_assignment_markers"]):
        return {"class": "policy_constant_candidate", "action": "inspect_for_shared_contract"}
    return {"class": "decision_like_tail", "action": "inspect_if_family_is_large"}


def _planning_bucket(count: int) -> str:
    if count >= 15:
        return "large_family"
    if count >= 5:
        return "medium_family"
    return "small_or_tail_family"


def _group_counter(rows: list[StringLiteral], key: str) -> list[dict[str, Any]]:
    if key == "file":
        counter = Counter(row.file for row in rows)
    elif key == "category":
        counter = Counter(_candidate_category(row) for row in rows)
    elif key == "assignment":
        counter = Counter(row.assignment_name or "<inline>" for row in rows)
    elif key == "triage_class":
        counter = Counter(_triage(row)["class"] for row in rows)
    elif key == "triage_action":
        counter = Counter(_triage(row)["action"] for row in rows)
    else:
        counter = Counter()
    return [
        {"name": name, "count": count}
        for name, count in counter.most_common()
    ]


def _work_plan(rows: list[StringLiteral]) -> list[dict[str, Any]]:
    file_groups = _group_counter(rows, "file")
    rows_by_file: dict[str, list[StringLiteral]] = {}
    for row in rows:
        rows_by_file.setdefault(row.file, []).append(row)
    return [
        {
            "file": group["name"],
            "candidate_count": group["count"],
            "bucket": _planning_bucket(int(group["count"])),
            "triage_actions": _group_counter(rows_by_file.get(str(group["name"]), []), "triage_action"),
            "recommended_action": _recommended_file_action(rows_by_file.get(str(group["name"]), []), int(group["count"])),
        }
        for group in file_groups
    ]


def _rows_by_triage_action(rows: list[StringLiteral], actions: set[str]) -> list[StringLiteral]:
    return [row for row in rows if _triage(row)["action"] in actions]


def _recommended_file_action(rows: list[StringLiteral], count: int) -> str:
    actions = Counter(_triage(row)["action"] for row in rows)
    inspect_count = actions.get("inspect_for_shared_contract", 0) + actions.get("inspect_for_registry", 0)
    if inspect_count:
        return "inspect_for_shared_contract"
    if count >= 15 and actions.get("usually_keep_local_unless_shared_policy", 0):
        return "sample_then_probably_defer"
    if count >= 5:
        return "classify_before_refactor"
    return "leave_local_unless_suspicious"


def _samples_by_file(rows: list[StringLiteral], *, file_limit: int = 40, sample_limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[StringLiteral]] = {}
    for row in rows:
        grouped.setdefault(row.file, []).append(row)
    ordered_files = [item["name"] for item in _group_counter(rows, "file")[:file_limit]]
    return {
        file: [dict(row.__dict__, triage=_triage(row)) for row in grouped.get(file, [])[:sample_limit]]
        for file in ordered_files
    }


def _is_schema_or_artifact_version(row: StringLiteral) -> bool:
    return (row.key_name or "").lower() == "version" and (row.assignment_name or "") != "comparison"


def _is_release_decision_literal(row: StringLiteral) -> bool:
    if not RELEASE_LITERAL_RE.match(row.value):
        return False
    if _is_schema_or_artifact_version(row):
        return False
    decision_context = " ".join(
        [
            row.file.lower(),
            (row.assignment_name or "").lower(),
            (row.key_name or "").lower(),
        ]
    )
    return any(
        word in decision_context
        for word in ("release", "phase", "claim", "product", "included", "target", "current", "comparison")
    )


def build_validation(progress: ValidatorProgress | None = None) -> dict[str, Any]:
    _inventory_policy.cache_clear()
    validator_policy = hardcoded_decision_inventory_policy()
    source_contract_policy = load_json_file(ROOT / "config" / "source_contract_policy.json", {})
    decision_domain_policy = source_contract_policy.get("decision_domain_classification", {})
    sample_limits = decision_domain_policy.get("ambiguous_sample_limits", {})
    ambiguous_file_limit = int(sample_limits["file_limit"])
    ambiguous_sample_limit = int(sample_limits["sample_limit_per_file"])
    if ambiguous_file_limit <= 0 or ambiguous_sample_limit <= 0:
        raise ValueError("Ambiguous decision sample limits must be positive integers")
    python_files = _python_files()
    if progress is not None:
        progress.phase("inventory", total=len(python_files))
    literals: list[StringLiteral] = []
    central_contract_fallbacks: list[CentralContractFallback] = []
    module_decision_tables: list[ModuleDecisionTable] = []
    for index, path in enumerate(python_files, start=1):
        tree = _parse_python_source(path)
        literals.extend(_read_python_literals(path, tree))
        if not _is_test_file(path):
            central_contract_fallbacks.extend(_read_central_contract_fallbacks(path, tree))
            module_decision_tables.extend(_read_module_decision_tables(path, tree))
        if progress is not None:
            progress.advance(index, current_file=str(path.relative_to(ROOT)).replace("\\", "/"))
    if progress is not None:
        progress.phase(
            "classification",
            total=len(literals),
            python_files=len(python_files),
            string_literals=len(literals),
        )
    absolute_path_hits = [
        row
        for row in literals
        if ABSOLUTE_PATH_RE.search(row.value)
        and not _is_test_file(ROOT / row.file)
    ]
    roadmap_release_literals = [
        row
        for row in literals
        if _is_release_decision_literal(row)
        and "roadmap_phase_registry" not in row.file
        and row.file not in validator_policy["allowed_decision_table_files"]
        and not _is_test_file(ROOT / row.file)
    ]
    decision_like_literals = [
        row
        for row in literals
        if _looks_decision_like(row)
        and row.file not in validator_policy["allowed_decision_table_files"]
        and not _is_test_file(ROOT / row.file)
    ]
    high_signal_candidates = [
        row
        for row in decision_like_literals
        if (
            (row.assignment_name or "").isupper()
            or RELEASE_LITERAL_RE.match(row.value)
            or "timeout" in row.value.lower()
            or "budget" in row.value.lower()
        )
    ]
    actionable_candidates = _rows_by_triage_action(high_signal_candidates, set(validator_policy["triage_actions"]["actionable"]))
    bulk_deferred_candidates = _rows_by_triage_action(high_signal_candidates, set(validator_policy["triage_actions"]["bulk_deferred"]))
    ambiguous_candidates = [
        row
        for row in high_signal_candidates
        if row not in actionable_candidates and row not in bulk_deferred_candidates
    ]
    ambiguous_samples = _samples_by_file(
        ambiguous_candidates,
        file_limit=ambiguous_file_limit,
        sample_limit=ambiguous_sample_limit,
    )
    actionable_plan = _work_plan(actionable_candidates)
    unowned_module_decision_tables = [
        row
        for row in module_decision_tables
        if row.file not in validator_policy["allowed_decision_table_files"]
    ]
    if progress is not None:
        progress.advance(
            len(literals),
            high_signal=len(high_signal_candidates),
            actionable=len(actionable_candidates),
        )
    checks = [
        _check(
            "python_string_inventory_completed",
            len(literals) > 0,
            {"python_files": len(python_files), "string_literals": len(literals)},
        ),
        _check(
            "no_machine_absolute_paths_in_runtime_python",
            not absolute_path_hits,
            {"hits": [row.__dict__ for row in absolute_path_hits[:50]], "total": len(absolute_path_hits)},
        ),
        _check(
            "roadmap_release_literals_are_registry_or_table_owned",
            not roadmap_release_literals,
            {"hits": [row.__dict__ for row in roadmap_release_literals[:50]], "total": len(roadmap_release_literals)},
        ),
        _check(
            "central_contract_nonempty_local_fallbacks_are_visible_inventory",
            True,
            {
                "hits": [row.__dict__ for row in central_contract_fallbacks[:100]],
                "total": len(central_contract_fallbacks),
                "rule": "Required central doctrine, policy and registry decisions must fail closed unless a separate machine-readable bootstrap fallback contract explicitly owns the fallback.",
                "status_note": "Inventory evidence is not a zero-fallback claim. Active migration debt is tracked separately; new P0/P0.5 cases still require immediate source-grounded triage.",
            },
        ),
        _check(
            "module_level_decision_tables_are_centrally_owned",
            not unowned_module_decision_tables,
            {
                "hits": [row.__dict__ for row in unowned_module_decision_tables[:100]],
                "total": len(unowned_module_decision_tables),
                "detected_total": len(module_decision_tables),
                "rule": "Repeated module-level decision rows belong in an owning machine-readable contract unless the central validator policy explicitly allows the implementation table.",
            },
        ),
        _check(
            "decision_like_literals_are_visible_inventory",
            True,
            {
                "high_signal_candidates": [row.__dict__ for row in high_signal_candidates[:100]],
                "high_signal_total": len(high_signal_candidates),
                "decision_like_total": len(decision_like_literals),
                "note": "Inventory-only check. Candidates are not failures unless they violate a concrete central-contract rule.",
            },
        ),
        _check(
            "ambiguous_candidates_keep_bounded_source_provenance",
            all(
                sample.get("file") == file
                and int(sample.get("line") or 0) > 0
                and sample.get("triage", {}).get("class") == "decision_like_tail"
                for file, samples in ambiguous_samples.items()
                for sample in samples
            ),
            {
                "candidate_count": len(ambiguous_candidates),
                "candidate_files": len(_group_counter(ambiguous_candidates, "file")),
                "sampled_files": len(ambiguous_samples),
                "sampled_candidates": sum(len(samples) for samples in ambiguous_samples.values()),
                "file_limit": ambiguous_file_limit,
                "sample_limit_per_file": ambiguous_sample_limit,
                "bounded_note": "All candidate counts remain visible; source-provenance samples are bounded by the central policy.",
            },
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    return {
        "meta": {
            "kind": "hardcoded_decision_inventory_validation",
            "version": "v1",
            "generated_at": _utc_now(),
        },
        "status": status,
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
            "python_files": len(python_files),
            "string_literals": len(literals),
            "decision_like_total": len(decision_like_literals),
            "high_signal_candidates": len(high_signal_candidates),
            "actionable_candidates": len(actionable_candidates),
            "bulk_deferred_candidates": len(bulk_deferred_candidates),
            "ambiguous_candidates": len(ambiguous_candidates),
            "central_contract_nonempty_fallback_candidates": len(central_contract_fallbacks),
            "module_decision_tables": len(module_decision_tables),
            "unowned_module_decision_tables": len(unowned_module_decision_tables),
            "actionable_large_families": sum(1 for item in actionable_plan if item["bucket"] == "large_family"),
            "actionable_medium_families": sum(1 for item in actionable_plan if item["bucket"] == "medium_family"),
            "actionable_small_or_tail_families": sum(1 for item in actionable_plan if item["bucket"] == "small_or_tail_family"),
            "large_families": sum(1 for item in _work_plan(high_signal_candidates) if item["bucket"] == "large_family"),
            "medium_families": sum(1 for item in _work_plan(high_signal_candidates) if item["bucket"] == "medium_family"),
            "small_or_tail_families": sum(1 for item in _work_plan(high_signal_candidates) if item["bucket"] == "small_or_tail_family"),
        },
        "inventory": {
            "high_signal_by_file": _group_counter(high_signal_candidates, "file"),
            "high_signal_by_category": _group_counter(high_signal_candidates, "category"),
            "high_signal_by_assignment": _group_counter(high_signal_candidates, "assignment")[:50],
            "high_signal_by_triage_class": _group_counter(high_signal_candidates, "triage_class"),
            "high_signal_by_triage_action": _group_counter(high_signal_candidates, "triage_action"),
            "high_signal_samples_by_file": _samples_by_file(high_signal_candidates),
            "work_plan": _work_plan(high_signal_candidates),
            "actionable_by_file": _group_counter(actionable_candidates, "file"),
            "actionable_by_triage_action": _group_counter(actionable_candidates, "triage_action"),
            "actionable_work_plan": actionable_plan,
            "bulk_deferred_by_triage_action": _group_counter(bulk_deferred_candidates, "triage_action"),
            "bulk_deferred_by_triage_class": _group_counter(bulk_deferred_candidates, "triage_class"),
            "ambiguous_by_file": _group_counter(ambiguous_candidates, "file"),
            "ambiguous_samples_by_file": ambiguous_samples,
        },
        "checks": checks,
    }


def write_report(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {})
    inventory = payload.get("inventory", {}) if isinstance(payload.get("inventory"), dict) else {}
    lines = [
        "# Hardcoded Decision Inventory Validation",
        "",
        f"- status: `{payload.get('status')}`",
        f"- python_files: `{summary.get('python_files')}`",
        f"- string_literals: `{summary.get('string_literals')}`",
        f"- decision_like_total: `{summary.get('decision_like_total')}`",
        f"- high_signal_candidates: `{summary.get('high_signal_candidates')}`",
        f"- actionable_candidates: `{summary.get('actionable_candidates')}`",
        f"- bulk_deferred_candidates: `{summary.get('bulk_deferred_candidates')}`",
        f"- ambiguous_candidates: `{summary.get('ambiguous_candidates')}`",
        f"- module_decision_tables: `{summary.get('module_decision_tables')}`",
        f"- unowned_module_decision_tables: `{summary.get('unowned_module_decision_tables')}`",
        f"- large_families: `{summary.get('large_families')}`",
        f"- medium_families: `{summary.get('medium_families')}`",
        f"- small_or_tail_families: `{summary.get('small_or_tail_families')}`",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"| `{check.get('name')}` | `{marker}` |")
    lines.extend(
        [
            "",
            "## Triage Actions",
            "",
            "| Action | Candidates |",
            "|---|---:|",
        ]
    )
    for item in inventory.get("high_signal_by_triage_action", []):
        lines.append(f"| `{item.get('name')}` | `{item.get('count')}` |")
    lines.extend(
        [
            "",
            "## Actionable Work Queue",
            "",
            "| File | Candidates | Bucket | Recommended action |",
            "|---|---:|---|---|",
        ]
    )
    for item in inventory.get("actionable_work_plan", [])[:40]:
        lines.append(
            "| `{file}` | `{count}` | `{bucket}` | `{action}` |".format(
                file=item.get("file"),
                count=item.get("candidate_count"),
                bucket=item.get("bucket"),
                action=item.get("recommended_action"),
            )
        )
    lines.extend(
        [
            "",
            "## Bulk Deferred Actions",
            "",
            "| Action | Candidates |",
            "|---|---:|",
        ]
    )
    for item in inventory.get("bulk_deferred_by_triage_action", []):
        lines.append(f"| `{item.get('name')}` | `{item.get('count')}` |")
    lines.extend(
        [
            "",
            "## Triage Classes",
            "",
            "| Class | Candidates |",
            "|---|---:|",
        ]
    )
    for item in inventory.get("high_signal_by_triage_class", []):
        lines.append(f"| `{item.get('name')}` | `{item.get('count')}` |")
    lines.extend(
        [
            "",
            "## High-Signal Families",
            "",
            "| File | Candidates | Bucket | Recommended action |",
            "|---|---:|---|---|",
        ]
    )
    for item in inventory.get("work_plan", [])[:40]:
        lines.append(
            "| `{file}` | `{count}` | `{bucket}` | `{action}` |".format(
                file=item.get("file"),
                count=item.get("candidate_count"),
                bucket=item.get("bucket"),
                action=item.get("recommended_action"),
            )
        )
    lines.extend(
        [
            "",
            "## Category Breakdown",
            "",
            "| Category | Candidates |",
            "|---|---:|",
        ]
    )
    for item in inventory.get("high_signal_by_category", []):
        lines.append(f"| `{item.get('name')}` | `{item.get('count')}` |")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")


def main() -> int:
    progress = ValidatorProgress("validate_hardcoded_decision_inventory")
    progress.start()
    try:
        payload = build_validation(progress)
        progress.phase("artifact_write", total=2)
        save_json_atomic(RAW_OUTPUT_PATH, payload)
        progress.advance(1, artifact=RAW_OUTPUT_PATH.name)
        write_report(payload)
        progress.advance(2, artifact=REPORT_OUTPUT_PATH.name)
    except Exception as exc:
        progress.complete("FAIL", error=type(exc).__name__)
        raise
    progress.complete(payload.get("status", "FAIL"), checks=payload.get("summary", {}).get("total_checks"))
    print(json.dumps({
        "status": payload.get("status"),
        "python_files": payload.get("summary", {}).get("python_files"),
        "string_literals": payload.get("summary", {}).get("string_literals"),
        "decision_like_total": payload.get("summary", {}).get("decision_like_total"),
        "high_signal_candidates": payload.get("summary", {}).get("high_signal_candidates"),
        "actionable_candidates": payload.get("summary", {}).get("actionable_candidates"),
        "bulk_deferred_candidates": payload.get("summary", {}).get("bulk_deferred_candidates"),
        "ambiguous_candidates": payload.get("summary", {}).get("ambiguous_candidates"),
        "large_families": payload.get("summary", {}).get("large_families"),
        "medium_families": payload.get("summary", {}).get("medium_families"),
        "small_or_tail_families": payload.get("summary", {}).get("small_or_tail_families"),
    }, ensure_ascii=False))
    return 0 if payload.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

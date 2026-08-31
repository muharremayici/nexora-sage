from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.artifact_validator import ensure_valid_payload
from tools.core.source_layer_classifier import classify_source_layer


POLICY_PATH = CONFIG_DIR / "layer_import_boundary_policy.json"


def _rel(path: Path) -> str:
    return path.relative_to(CODE_MAPS_DIR).as_posix()


def _load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _policy_list(raw: Any) -> list[str]:
    return [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []


def _module_to_path(module: str, module_prefixes: list[str]) -> Path | None:
    if not any(module.startswith(prefix) for prefix in module_prefixes):
        return None
    candidate = CODE_MAPS_DIR / (module.replace(".", "/") + ".py")
    if candidate.exists():
        return candidate
    package_candidate = CODE_MAPS_DIR / module.replace(".", "/") / "__init__.py"
    if package_candidate.exists():
        return package_candidate
    return None


def _imported_modules(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=_rel(path))
    except SyntaxError as exc:
        return [{"module": "", "line": int(exc.lineno or 0), "syntax_error": str(exc)}]

    imports: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"module": alias.name, "line": int(node.lineno or 0)})
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append({"module": node.module, "line": int(node.lineno or 0)})
    return imports


def _module_name(path: Path) -> str:
    relative = path.relative_to(CODE_MAPS_DIR).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _relative_import_base(source_module: str, *, is_package: bool, level: int, module: str | None) -> str:
    package_parts = source_module.split(".") if is_package else source_module.split(".")[:-1]
    parent_hops = max(0, int(level or 0) - 1)
    if parent_hops:
        package_parts = package_parts[:-parent_hops]
    if module:
        package_parts.extend(str(module).split("."))
    return ".".join(part for part in package_parts if part)


def _internal_import_targets(
    path: Path,
    *,
    module_by_path: dict[Path, str],
    path_by_module: dict[str, Path],
) -> tuple[set[str], list[dict[str, Any]]]:
    source_module = module_by_path[path]
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=_rel(path))
    except SyntaxError as exc:
        return set(), [{"file": _rel(path), "line": int(exc.lineno or 0), "error": str(exc)}]

    targets: set[str] = set()
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = (
                _relative_import_base(
                    source_module,
                    is_package=path.name == "__init__.py",
                    level=node.level,
                    module=node.module,
                )
                if node.level
                else str(node.module or "")
            )
            candidates.extend(f"{base}.{alias.name}".strip(".") for alias in node.names if alias.name != "*")
            candidates.append(base)
        for candidate in candidates:
            if candidate in path_by_module and candidate != source_module:
                targets.add(candidate)
    return targets, []


def _strongly_connected_components(adjacency: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(adjacency.get(node, set())):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] != indexes[node]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        components.append(sorted(component))

    for node in sorted(adjacency):
        if node not in indexes:
            visit(node)
    return sorted(components, key=lambda component: (component[0], len(component)))


def _unexpected_cycle_components(
    component_rows: list[dict[str, Any]],
    baseline_components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unexpected: list[dict[str, Any]] = []
    for current in component_rows:
        current_modules = set(current["modules"])
        owner = next(
            (
                baseline
                for baseline in baseline_components
                if current_modules.issubset(set(baseline["modules"]))
            ),
            None,
        )
        if owner is None:
            unexpected.append({**current, "reason": "new_cycle_family"})
            continue
        new_edges = sorted(set(current.get("edges", [])) - set(owner.get("edges", [])))
        if new_edges:
            unexpected.append(
                {
                    **current,
                    "reason": "known_cycle_contains_new_internal_edges",
                    "new_edges": new_edges,
                }
            )
    return unexpected


def _cycle_baseline_contract_errors(baseline_rows: list[Any]) -> list[str]:
    errors: list[str] = []
    module_sets: list[set[str]] = []
    for index, row in enumerate(baseline_rows):
        if not isinstance(row, dict):
            errors.append(f"repository_cycle_no_worsening.known_component_not_object:{index}")
            continue
        modules = [str(module) for module in row.get("modules", []) if str(module)]
        edges = [str(edge) for edge in row.get("edges", []) if str(edge)]
        module_set = set(modules)
        module_sets.append(module_set)
        if len(modules) != len(module_set):
            errors.append(f"repository_cycle_no_worsening.known_component_duplicate_modules:{index}")
        if len(edges) != len(set(edges)):
            errors.append(f"repository_cycle_no_worsening.known_component_duplicate_edges:{index}")
        if int(row.get("maximum_internal_edges") or 0) != len(edges):
            errors.append(f"repository_cycle_no_worsening.known_component_edge_count_mismatch:{index}")
        for edge in edges:
            source, separator, target = edge.partition("->")
            if not separator or source not in module_set or target not in module_set:
                errors.append(f"repository_cycle_no_worsening.known_component_invalid_edge:{index}:{edge}")
    for left in range(len(module_sets)):
        for right in range(left + 1, len(module_sets)):
            if module_sets[left] & module_sets[right]:
                errors.append(f"repository_cycle_no_worsening.known_components_overlap:{left}:{right}")
    return errors


def _cycle_baseline_drift(
    component_rows: list[dict[str, Any]],
    baseline_components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_by_modules = {
        tuple(sorted(str(module) for module in row.get("modules", []))): set(row.get("edges", []))
        for row in component_rows
    }
    baseline_by_modules = {
        tuple(sorted(str(module) for module in row.get("modules", []))): set(row.get("edges", []))
        for row in baseline_components
    }
    drift: list[dict[str, Any]] = []
    for modules in sorted(set(current_by_modules) | set(baseline_by_modules)):
        current_edges = current_by_modules.get(modules)
        baseline_edges = baseline_by_modules.get(modules)
        if current_edges is None:
            drift.append(
                {
                    "modules": list(modules),
                    "reason": "baseline_component_no_longer_current",
                    "removed_edges": sorted(baseline_edges or set()),
                }
            )
            continue
        if baseline_edges is None:
            drift.append(
                {
                    "modules": list(modules),
                    "reason": "current_component_not_exactly_baselined",
                    "current_edges": sorted(current_edges),
                }
            )
            continue
        if current_edges != baseline_edges:
            drift.append(
                {
                    "modules": list(modules),
                    "reason": "cycle_edge_set_not_exactly_baselined",
                    "new_edges": sorted(current_edges - baseline_edges),
                    "removed_edges": sorted(baseline_edges - current_edges),
                }
            )
    return drift


def _repository_cycle_projection(policy: dict[str, Any]) -> dict[str, Any]:
    cycle_policy = (
        policy.get("repository_cycle_no_worsening")
        if isinstance(policy.get("repository_cycle_no_worsening"), dict)
        else {}
    )
    scan_roots = _policy_list(cycle_policy.get("scan_roots"))
    excluded_globs = _policy_list(cycle_policy.get("excluded_globs"))
    baseline_rows = cycle_policy.get("known_components") if isinstance(cycle_policy.get("known_components"), list) else []
    scope_errors: list[str] = []
    if not scan_roots:
        scope_errors.append("repository_cycle_no_worsening.scan_roots_missing")
    if not baseline_rows:
        scope_errors.append("repository_cycle_no_worsening.known_components_missing")
    elif any(not isinstance(row, dict) or not row.get("edges") for row in baseline_rows):
        scope_errors.append("repository_cycle_no_worsening.known_component_edges_missing")
    scope_errors.extend(_cycle_baseline_contract_errors(baseline_rows))

    eligible_paths: list[Path] = []
    for root in scan_roots:
        root_path = CODE_MAPS_DIR / root
        if not root_path.exists():
            scope_errors.append(f"repository_cycle_no_worsening.scan_root_missing:{root}")
            continue
        eligible_paths.extend(sorted(root_path.rglob("*.py")))
    eligible_paths = sorted(set(eligible_paths))
    excluded_paths = [
        path
        for path in eligible_paths
        if any(path.relative_to(CODE_MAPS_DIR).match(pattern) for pattern in excluded_globs)
    ]
    inspected_paths = [path for path in eligible_paths if path not in set(excluded_paths)]
    module_by_path = {path: _module_name(path) for path in inspected_paths}
    paths_by_module: dict[str, list[Path]] = {}
    for path, module in module_by_path.items():
        paths_by_module.setdefault(module, []).append(path)
    module_collisions = {
        module: [_rel(path) for path in paths]
        for module, paths in paths_by_module.items()
        if len(paths) > 1
    }
    scope_errors.extend(f"repository_cycle_no_worsening.module_collision:{module}" for module in module_collisions)
    path_by_module = {module: path for path, module in module_by_path.items()}
    adjacency: dict[str, set[str]] = {module: set() for module in path_by_module}
    parse_errors: list[dict[str, Any]] = []
    for path, source_module in module_by_path.items():
        targets, errors = _internal_import_targets(
            path,
            module_by_path=module_by_path,
            path_by_module=path_by_module,
        )
        adjacency[source_module].update(targets)
        parse_errors.extend(errors)

    components = [
        component
        for component in _strongly_connected_components(adjacency)
        if len(component) > 1 or component[0] in adjacency.get(component[0], set())
    ]
    component_rows: list[dict[str, Any]] = []
    for component in components:
        members = set(component)
        edges = sorted(
            f"{source}->{target}"
            for source in members
            for target in adjacency.get(source, set())
            if target in members
        )
        component_rows.append({"modules": component, "internal_edges": len(edges), "edges": edges})

    baseline_components = [
        {
            "modules": sorted(str(module) for module in row.get("modules", []) if str(module)),
            "maximum_internal_edges": int(row.get("maximum_internal_edges") or 0),
            "edges": sorted(str(edge) for edge in row.get("edges", []) if str(edge)),
        }
        for row in baseline_rows
        if isinstance(row, dict)
    ]
    unexpected_components = _unexpected_cycle_components(component_rows, baseline_components)
    baseline_drift = _cycle_baseline_drift(component_rows, baseline_components)

    return {
        "status": (
            "PASS"
            if not scope_errors and not parse_errors and not unexpected_components and not baseline_drift
            else "FAIL"
        ),
        "scope": {
            "eligible_python_files": len(eligible_paths),
            "inspected_python_files": len(inspected_paths),
            "excluded_python_files": len(excluded_paths),
            "scan_roots": scan_roots,
            "excluded_globs": excluded_globs,
            "explicit_non_claims": _policy_list(cycle_policy.get("explicit_non_claims")),
            "algorithm": str(cycle_policy.get("algorithm") or ""),
            "edge_semantics": str(cycle_policy.get("edge_semantics") or ""),
            "baseline_update_rule": str(cycle_policy.get("baseline_update_rule") or ""),
            "module_collisions": module_collisions,
        },
        "internal_edges": sum(len(targets) for targets in adjacency.values()),
        "cyclic_modules": sum(len(row["modules"]) for row in component_rows),
        "components": component_rows,
        "known_components": baseline_components,
        "unexpected_components": unexpected_components,
        "baseline_drift": baseline_drift,
        "parse_errors": parse_errors,
        "scope_errors": scope_errors,
    }


def _allowed(module: str, rule: dict[str, Any]) -> bool:
    if module in set(rule.get("allowed_modules") or []):
        return True
    return any(module.startswith(str(prefix)) for prefix in rule.get("allowed_module_prefixes") or [])


def validate_layer_import_boundaries() -> dict[str, Any]:
    policy = _load_policy()
    validation_scope = policy.get("validation_scope", {}) if isinstance(policy.get("validation_scope"), dict) else {}
    scan_roots = _policy_list(validation_scope.get("scan_roots"))
    python_module_prefixes = _policy_list(validation_scope.get("python_module_prefixes"))
    protected = policy.get("protected_consumers", {}) if isinstance(policy.get("protected_consumers"), dict) else {}

    findings: list[dict[str, Any]] = []
    protected_files = 0
    imports_checked = 0
    scope_errors: list[str] = []
    if not scan_roots:
        scope_errors.append("validation_scope.scan_roots_missing")
    if not python_module_prefixes:
        scope_errors.append("validation_scope.python_module_prefixes_missing")
    scan_paths: list[Path] = []
    for root in scan_roots:
        root_path = CODE_MAPS_DIR / root
        if not root_path.exists():
            scope_errors.append(f"validation_scope.scan_root_missing:{root}")
            continue
        scan_paths.extend(sorted(root_path.rglob("*.py")))
    for path in scan_paths:
        source_layer, _reason = classify_source_layer(path, root=CODE_MAPS_DIR)
        rule = protected.get(source_layer)
        if not isinstance(rule, dict):
            continue
        protected_files += 1
        for imported in _imported_modules(path):
            module = str(imported.get("module") or "")
            if not any(module.startswith(prefix) for prefix in python_module_prefixes):
                continue
            imports_checked += 1
            target_path = _module_to_path(module, python_module_prefixes)
            target_layer = ""
            target_file = ""
            if target_path is not None:
                target_layer, _target_reason = classify_source_layer(target_path, root=CODE_MAPS_DIR)
                target_file = _rel(target_path)
            allowed = _allowed(module, rule)
            findings.append(
                {
                    "source_file": _rel(path),
                    "source_layer": source_layer,
                    "line": imported.get("line"),
                    "module": module,
                    "target_file": target_file,
                    "target_layer": target_layer,
                    "allowed": allowed,
                    "reason": rule.get("reason", ""),
                }
            )

    violations = [item for item in findings if not item.get("allowed")]
    cycle_projection = _repository_cycle_projection(policy)
    payload = {
        "meta": {"kind": "layer_import_boundary_validation", "version": "v1"},
        "summary": {
            "status": (
                "PASS"
                if not violations and not scope_errors and cycle_projection.get("status") == "PASS"
                else "FAIL"
            ),
            "protected_files": protected_files,
            "imports_checked": imports_checked,
            "allowed_imports": len(findings) - len(violations),
            "violations": len(violations),
            "scope_errors": len(scope_errors),
        },
        "principles": policy.get("principles", {}),
        "validation_scope": validation_scope,
        "scope_errors": scope_errors,
        "violations": violations,
        "findings": findings,
        "repository_cycle_no_worsening": cycle_projection,
    }
    ensure_valid_payload("layer_import_boundary_validation", payload)
    save_json_atomic(RAW_DIR / "layer_import_boundary_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "layer_import_boundary_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Layer Import Boundary Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- protected_files: `{summary.get('protected_files')}`",
        f"- imports_checked: `{summary.get('imports_checked')}`",
        f"- allowed_imports: `{summary.get('allowed_imports')}`",
        f"- violations: `{summary.get('violations')}`",
        f"- scope_errors: `{summary.get('scope_errors')}`",
        f"- repository_cycle_no_worsening: `{payload.get('repository_cycle_no_worsening', {}).get('status')}`",
        "",
        "## Scope Errors",
        "",
    ]
    for item in payload.get("scope_errors", []):
        lines.append(f"- `{item}`")
    lines.extend(
        [
            "",
        "## Violations",
        "",
        "| Source | Line | Imported Module | Target Layer |",
        "|---|---:|---|---|",
        ]
    )
    for item in payload.get("violations", [])[:80]:
        lines.append(
            f"| `{item.get('source_file')}` | {item.get('line')} | "
            f"`{item.get('module')}` | `{item.get('target_layer')}` |"
        )
    lines.extend(
        [
            "",
            "## Allowed Direct Adapter Imports",
            "",
            "| Source | Line | Imported Module | Target Layer |",
            "|---|---:|---|---|",
        ]
    )
    for item in [entry for entry in payload.get("findings", []) if entry.get("allowed")][:120]:
        lines.append(
            f"| `{item.get('source_file')}` | {item.get('line')} | "
            f"`{item.get('module')}` | `{item.get('target_layer')}` |"
        )
    cycle = payload.get("repository_cycle_no_worsening", {})
    lines.extend(
        [
            "",
            "## Repository Cycle No-Worsening",
            "",
            f"- eligible_python_files: `{cycle.get('scope', {}).get('eligible_python_files')}`",
            f"- inspected_python_files: `{cycle.get('scope', {}).get('inspected_python_files')}`",
            f"- internal_edges: `{cycle.get('internal_edges')}`",
            f"- cyclic_modules: `{cycle.get('cyclic_modules')}`",
            f"- unexpected_components: `{len(cycle.get('unexpected_components', []))}`",
            f"- baseline_drift: `{len(cycle.get('baseline_drift', []))}`",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_layer_import_boundaries()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

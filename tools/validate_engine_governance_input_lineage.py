from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


POLICY_PATH = ROOT / "config" / "engine_governance_input_lineage_policy.json"


def _literal_args(node: ast.Call) -> tuple[str, ...] | None:
    values: list[str] = []
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.strip():
            values.append(arg.value.strip())
        else:
            break
    return tuple(values) if values else None


def _doctrine_access_path(node: ast.Call) -> tuple[str, ...] | None:
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and isinstance(func.value, ast.Name)
        and func.value.id == "DOCTRINE"
    ):
        args = _literal_args(node)
        return (args[0],) if args else ("<dynamic>",)
    if isinstance(func, ast.Name) and func.id in {"require_doctrine_mapping", "require_doctrine_path"}:
        return _literal_args(node) or ("<dynamic>",)
    if isinstance(func, ast.Name) and func.id == "require_dead_code_policy":
        args = _literal_args(node)
        return ("dead_code_heuristics", args[0]) if args else ("<dynamic>",)
    return None


def _path_exists(payload: object, path: tuple[str, ...]) -> bool:
    value = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return False
        value = value[key]
    return True


def _render(payload: dict) -> str:
    summary = payload["summary"]
    lines = [
        "# Engine Governance Input Lineage",
        "",
        f"- status: `{summary['status']}`",
        f"- consumers: `{summary['consumers']}`",
        f"- doctrine_sections: `{summary['doctrine_sections']}`",
        f"- access_sites: `{summary['access_sites']}`",
        f"- missing_sections: `{summary['missing_sections']}`",
        f"- dynamic_section_accesses: `{summary['dynamic_section_accesses']}`",
        "",
        "| Consumer | Doctrine sections |",
        "|---|---|",
    ]
    for row in payload["consumers"]:
        lines.append(f"| `{row['module']}` | `{', '.join(row['sections'])}` |")
    lines.append("")
    return "\n".join(lines)


def validate() -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    doctrine_path = ROOT / str(policy.get("compiled_doctrine") or "")
    doctrine = json.loads(doctrine_path.read_text(encoding="utf-8"))
    doctrine_sections = set(doctrine) if isinstance(doctrine, dict) else set()
    consumers: dict[str, set[str]] = {}
    sites: list[dict] = []
    parse_errors: list[dict] = []
    for root_text in policy.get("scan_roots", []):
        scan_root = ROOT / str(root_text)
        for path in sorted(scan_root.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (OSError, SyntaxError, UnicodeError) as exc:
                parse_errors.append({"file": rel, "error": str(exc)})
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                access_path = _doctrine_access_path(node)
                if access_path is None:
                    continue
                section = ".".join(access_path)
                consumers.setdefault(rel, set()).add(section)
                sites.append({"module": rel, "section": section, "path": list(access_path), "line": int(node.lineno or 0)})
    declared = {section for sections in consumers.values() for section in sections if section != "<dynamic>"}
    missing = sorted(
        site["section"]
        for site in sites
        if site["section"] != "<dynamic>" and not _path_exists(doctrine, tuple(site["path"]))
    )
    missing = sorted(set(missing))
    dynamic = [site for site in sites if site["section"] == "<dynamic>"]
    dynamic_allowlist = policy.get("dynamic_access_module_allowlist", {})
    if not isinstance(dynamic_allowlist, dict):
        dynamic_allowlist = {}
    blocking_dynamic = [site for site in dynamic if site["module"] not in dynamic_allowlist]
    errors = []
    validation = policy.get("validation", {})
    if validation.get("empty_lineage_is_failure") and not consumers:
        errors.append("empty_governance_input_lineage")
    if validation.get("declared_sections_must_exist") and missing:
        errors.append("declared_doctrine_sections_missing")
    if validation.get("literal_section_names_required") and blocking_dynamic:
        errors.append("dynamic_doctrine_section_access")
    if parse_errors:
        errors.append("consumer_parse_errors")
    payload = {
        "meta": {"kind": "engine_governance_input_lineage", "version": "v1"},
        "summary": {
            "status": "PASS" if not errors else "FAIL",
            "consumers": len(consumers),
            "doctrine_sections": len(declared),
            "access_sites": len(sites),
            "missing_sections": len(missing),
            "dynamic_section_accesses": len(dynamic),
            "blocking_dynamic_section_accesses": len(blocking_dynamic),
            "errors": errors,
        },
        "policy_source": POLICY_PATH.relative_to(ROOT).as_posix(),
        "doctrine_source": doctrine_path.relative_to(ROOT).as_posix(),
        "consumers": [
            {"module": module, "sections": sorted(sections)}
            for module, sections in sorted(consumers.items())
        ],
        "sites": sites,
        "missing_sections": missing,
        "dynamic_accesses": dynamic,
        "blocking_dynamic_accesses": blocking_dynamic,
        "parse_errors": parse_errors,
    }
    save_json_atomic(RAW_DIR / "engine_governance_input_lineage.json", payload)
    save_text_atomic(REPORTS_DIR / "engine_governance_input_lineage.md", _render(payload))
    return payload


if __name__ == "__main__":
    result = validate()
    print(json.dumps(result["summary"], indent=2))
    raise SystemExit(0 if result["summary"]["status"] == "PASS" else 1)

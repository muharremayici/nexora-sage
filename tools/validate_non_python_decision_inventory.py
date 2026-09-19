from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


RAW_OUTPUT_PATH = RAW_DIR / "non_python_decision_inventory_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "non_python_decision_inventory_validation.md"
POLICY_PATH = CONFIG_DIR / "non_python_decision_inventory_policy.json"


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    key_path: str
    value: str
    reason: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _policy() -> dict[str, Any]:
    payload = load_json_file(POLICY_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _policy_list(policy: dict[str, Any], key: str) -> list[str]:
    values = policy.get(key, [])
    return [str(item) for item in values if str(item).strip()] if isinstance(values, list) else []


def _sample_limit(policy: dict[str, Any], key: str) -> int:
    limits = policy.get("sample_limits", {}) if isinstance(policy.get("sample_limits"), dict) else {}
    return int(limits.get(key) or 0)


def _pattern(policy: dict[str, Any], key: str) -> re.Pattern[str]:
    patterns = policy.get("patterns", {}) if isinstance(policy.get("patterns"), dict) else {}
    return re.compile(str(patterns.get(key) or r"a^"))


def _is_ignored(path: Path) -> bool:
    parts = set(path.relative_to(ROOT).parts)
    return bool(parts & set(_policy_list(_policy(), "ignored_dir_parts")))


def _line_for_text(lines: list[str], text: str) -> int:
    if not text:
        return 0
    needle = text[:120]
    for index, line in enumerate(lines, 1):
        if needle in line:
            return index
    return 0


def _json_files() -> list[Path]:
    return [
        path
        for path in CONFIG_DIR.rglob("*.json")
        if path.is_file() and not _is_ignored(path)
    ]


def _markdown_files() -> list[Path]:
    docs = ROOT / "docs"
    files = [path for path in docs.rglob("*.md") if path.is_file() and not _is_ignored(path)]
    files.extend(path for path in ROOT.glob("*.md") if path.is_file())
    return sorted(files)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _walk_json(value: Any, key_path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    rows = [(key_path, value)]
    if isinstance(value, dict):
        for key, item in value.items():
            rows.extend(_walk_json(item, (*key_path, str(key))))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(_walk_json(item, (*key_path, str(index))))
    return rows


def _key_is_decision(key_path: tuple[str, ...], policy: dict[str, Any]) -> bool:
    if not key_path:
        return False
    last = key_path[-1].lower()
    joined = ".".join(part.lower() for part in key_path)
    return last in set(_policy_list(policy, "decision_keys")) or any(
        token in joined for token in _policy_list(policy, "decision_key_tokens")
    )


def _key_is_numeric_policy(key_path: tuple[str, ...], policy: dict[str, Any]) -> bool:
    joined = ".".join(part.lower() for part in key_path)
    return any(token in joined for token in _policy_list(policy, "numeric_policy_keys"))


def _path_matches_pattern(key_path: tuple[str, ...], pattern: str) -> bool:
    expected = tuple(part for part in str(pattern or "").split(".") if part)
    return len(key_path) == len(expected) and all(
        declared == "*" or declared == actual
        for actual, declared in zip(key_path, expected)
    )


def _is_historical_release_identity_reference(
    rel: str,
    key_path: tuple[str, ...],
    policy: dict[str, Any],
) -> bool:
    declarations = policy.get("historical_release_identity_reference_paths", {})
    if not isinstance(declarations, dict):
        return False
    patterns = declarations.get(rel, [])
    return isinstance(patterns, list) and any(
        _path_matches_pattern(key_path, str(pattern))
        for pattern in patterns
    )


def _json_findings(policy: dict[str, Any]) -> tuple[list[Finding], list[Finding], list[Finding]]:
    release_literal_violations: list[Finding] = []
    release_literal_inventory: list[Finding] = []
    numeric_policy_inventory: list[Finding] = []
    semver_re = _pattern(policy, "semver_regex")
    owner_json = set(_policy_list(policy, "release_decision_owner_json"))
    reference_json = set(_policy_list(policy, "release_decision_reference_json"))
    roadmap_payload = load_json_file(CONFIG_DIR / "roadmap_phase_registry.json", {})
    roadmap_releases = {
        str(phase.get("release") or "")
        for phase in roadmap_payload.get("phases", [])
        if isinstance(phase, dict) and str(phase.get("release") or "").strip()
    }
    for path in _json_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            release_literal_violations.append(
                Finding(rel, int(exc.lineno), "<json>", "<parse-error>", "json_parse_error")
            )
            continue
        for key_path, value in _walk_json(payload):
            key_text = ".".join(key_path)
            if isinstance(value, str) and semver_re.search(value) and _key_is_decision(key_path, policy):
                finding = Finding(rel, _line_for_text(lines, value), key_text, value, "release_decision_literal")
                release_literal_inventory.append(finding)
                if rel in owner_json:
                    continue
                if _is_historical_release_identity_reference(rel, key_path, policy):
                    continue
                if rel in reference_json:
                    referenced_releases = set(semver_re.findall(value))
                    if referenced_releases and referenced_releases <= roadmap_releases:
                        continue
                reason = "unknown_release_reference" if rel in reference_json else "release_decision_literal"
                release_literal_violations.append(
                    Finding(rel, finding.line, key_text, value, reason)
                )
            if isinstance(value, (int, float)) and _key_is_numeric_policy(key_path, policy):
                numeric_policy_inventory.append(
                    Finding(rel, _line_for_text(lines, str(value)), key_text, str(value), "numeric_policy_literal")
                )
    return release_literal_violations, release_literal_inventory, numeric_policy_inventory


def _path_findings(policy: dict[str, Any]) -> tuple[list[Finding], list[Finding]]:
    current_doc_violations: list[Finding] = []
    archive_inventory: list[Finding] = []
    machine_absolute_path_re = _pattern(policy, "machine_absolute_path_regex")
    archive_markers = _policy_list(policy, "archive_path_markers")
    archive_prefixes = _policy_list(policy, "archive_path_prefixes")
    regex_definition_files = set(_policy_list(policy, "machine_path_regex_definition_files"))
    for path in [*_markdown_files(), *_json_files()]:
        rel = _rel(path)
        if rel in regex_definition_files:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in machine_absolute_path_re.finditer(text):
            finding = Finding(rel, _line_for_text(text.splitlines(), match.group(0)), "<text>", match.group(0), "machine_absolute_path")
            if any(marker in rel for marker in archive_markers) or any(rel.startswith(prefix) for prefix in archive_prefixes):
                archive_inventory.append(finding)
            else:
                current_doc_violations.append(finding)
    return current_doc_violations, archive_inventory


def _markdown_release_inventory(policy: dict[str, Any]) -> list[Finding]:
    rows: list[Finding] = []
    semver_re = _pattern(policy, "semver_regex")
    release_tokens = _policy_list(policy, "markdown_release_tokens")
    archive_prefixes = _policy_list(policy, "archive_path_prefixes")
    for path in _markdown_files():
        rel = _rel(path)
        if any(rel.startswith(prefix) for prefix in archive_prefixes):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines, 1):
            if semver_re.search(line) and any(token in line.lower() for token in release_tokens):
                rows.append(Finding(rel, index, "<markdown>", line.strip()[:240], "markdown_release_reference"))
    return rows


def build_validation() -> dict[str, Any]:
    policy = _policy()
    release_violations, release_inventory, numeric_policy_inventory = _json_findings(policy)
    path_violations, archive_path_inventory = _path_findings(policy)
    markdown_release_inventory = _markdown_release_inventory(policy)
    checks = [
        _check(
            "non_python_decision_inventory_policy_is_valid",
            policy.get("_meta", {}).get("kind") == "non_python_decision_inventory_policy"
            and bool(_policy_list(policy, "decision_keys"))
            and bool(_policy_list(policy, "numeric_policy_keys"))
            and bool(_policy_list(policy, "release_decision_owner_json"))
            and bool(_policy_list(policy, "release_decision_reference_json"))
            and isinstance(policy.get("historical_release_identity_reference_paths"), dict)
            and bool(_policy_list(policy, "ignored_dir_parts"))
            and _sample_limit(policy, "numeric_policy_inventory") > 0,
            {
                "policy": str(POLICY_PATH.relative_to(ROOT)),
                "decision_keys": len(_policy_list(policy, "decision_keys")),
                "numeric_policy_keys": len(_policy_list(policy, "numeric_policy_keys")),
                "release_decision_owner_json": len(_policy_list(policy, "release_decision_owner_json")),
                "release_decision_reference_json": len(_policy_list(policy, "release_decision_reference_json")),
                "historical_release_identity_reference_json": len(policy.get("historical_release_identity_reference_paths", {})),
            },
        ),
        _check(
            "non_python_inventory_completed",
            True,
            {"json_files": len(_json_files()), "markdown_files": len(_markdown_files())},
        ),
        _check(
            "current_docs_and_config_have_no_machine_absolute_paths",
            not path_violations,
            {
                "hits": [item.__dict__ for item in path_violations[: _sample_limit(policy, "machine_absolute_paths")]],
                "total": len(path_violations),
            },
        ),
        _check(
            "json_release_decisions_are_owner_file_scoped",
            not release_violations,
            {
                "owner_files": sorted(_policy_list(policy, "release_decision_owner_json")),
                "hits": [item.__dict__ for item in release_violations[: _sample_limit(policy, "release_literal_violations")]],
                "total": len(release_violations),
            },
        ),
        _check(
            "numeric_policy_literals_are_visible_inventory",
            True,
            {
                "sample": [item.__dict__ for item in numeric_policy_inventory[: _sample_limit(policy, "numeric_policy_inventory")]],
                "total": len(numeric_policy_inventory),
                "note": "Inventory-only. Numeric budgets/thresholds are allowed in doctrine and policy files but must remain visible.",
            },
        ),
        _check(
            "markdown_release_references_are_visible_inventory",
            True,
            {
                "sample": [item.__dict__ for item in markdown_release_inventory[: _sample_limit(policy, "markdown_release_inventory")]],
                "total": len(markdown_release_inventory),
                "note": "Inventory-only. Human docs may mention releases, but release decisions must stay in machine-readable registries.",
            },
        ),
        _check(
            "archive_machine_paths_are_visible_inventory",
            True,
            {
                "sample": [item.__dict__ for item in archive_path_inventory[: _sample_limit(policy, "archive_machine_paths")]],
                "total": len(archive_path_inventory),
                "note": "Archive-only paths are retained for historical traceability and are not runtime decisions.",
            },
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    return {
        "meta": {
            "kind": "non_python_decision_inventory_validation",
            "version": "v1",
            "generated_at": _utc_now(),
        },
        "status": status,
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
            "json_files": len(_json_files()),
            "markdown_files": len(_markdown_files()),
            "release_decision_inventory": len(release_inventory),
            "numeric_policy_inventory": len(numeric_policy_inventory),
            "markdown_release_inventory": len(markdown_release_inventory),
            "archive_machine_path_inventory": len(archive_path_inventory),
        },
        "checks": checks,
    }


def write_report(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {})
    lines = [
        "# Non-Python Decision Inventory Validation",
        "",
        f"- status: `{payload.get('status')}`",
        f"- json_files: `{summary.get('json_files')}`",
        f"- markdown_files: `{summary.get('markdown_files')}`",
        f"- release_decision_inventory: `{summary.get('release_decision_inventory')}`",
        f"- numeric_policy_inventory: `{summary.get('numeric_policy_inventory')}`",
        f"- markdown_release_inventory: `{summary.get('markdown_release_inventory')}`",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"| `{check.get('name')}` | `{marker}` |")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")


def main() -> int:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    write_report(payload)
    print(json.dumps({
        "status": payload.get("status"),
        "json_files": payload.get("summary", {}).get("json_files"),
        "markdown_files": payload.get("summary", {}).get("markdown_files"),
        "release_decision_inventory": payload.get("summary", {}).get("release_decision_inventory"),
        "numeric_policy_inventory": payload.get("summary", {}).get("numeric_policy_inventory"),
        "markdown_release_inventory": payload.get("summary", {}).get("markdown_release_inventory"),
    }, ensure_ascii=False))
    return 0 if payload.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.text_surface_policy import POLICY_PATH, load_text_surface_policy


REPORT_PATH = REPORTS_DIR / "release_language_validation.md"


def _policy() -> dict[str, Any]:
    return load_text_surface_policy()


def _release_scope(policy: dict[str, Any]) -> dict[str, Any]:
    scope = policy.get("release_language_scope", {})
    return scope if isinstance(scope, dict) else {}


def _is_excluded(rel_path: str, prefixes: set[str]) -> bool:
    normalized = rel_path.replace("\\", "/").strip("/")
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in prefixes)


def _iter_release_docs(policy: dict[str, Any]) -> list[Path]:
    scope = _release_scope(policy)
    include_roots = [str(item) for item in scope.get("include_roots", []) if str(item).strip()]
    suffixes = {str(item).lower() for item in scope.get("include_suffixes", []) if str(item).strip()}
    excluded = {str(item).replace("\\", "/").strip("/") for item in scope.get("exclude_path_prefixes", [])}

    paths: set[Path] = set()
    for item in include_roots:
        candidate = ROOT / item
        if candidate.is_file() and candidate.suffix.lower() in suffixes:
            paths.add(candidate)
        elif candidate.is_dir():
            for path in sorted(candidate.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in suffixes:
                    continue
                rel = path.relative_to(ROOT).as_posix()
                if _is_excluded(rel, excluded):
                    continue
                paths.add(path)
    return sorted(paths)


def _disallowed_markers(policy: dict[str, Any]) -> list[str]:
    scope = _release_scope(policy)
    character_sets = scope.get("disallowed_character_sets", {})
    markers: list[str] = []
    if isinstance(character_sets, dict):
        for values in character_sets.values():
            if isinstance(values, list):
                markers.extend(str(value) for value in values if str(value))
    markers.extend(str(marker) for marker in policy.get("mojibake_markers", []) if str(marker))
    return sorted(set(markers))


def _scan_file(path: Path, markers: list[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line_no, line in enumerate(text.splitlines(), start=1):
        matched = [marker for marker in markers if marker in line]
        if matched:
            findings.append(
                {
                    "file": path.relative_to(ROOT).as_posix(),
                    "line": line_no,
                    "markers": sorted(set(matched)),
                    "excerpt": line[:220],
                }
            )
    return findings


def _write_report(payload: dict[str, object]) -> None:
    summary = payload.get("summary", {})
    lines = [
        "# Release Language Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- files_scanned: `{summary.get('files_scanned')}`",
        f"- findings: `{summary.get('findings')}`",
        "",
        "This gate scans SAGE-owned release-facing Markdown from the central text surface policy. Historical `docs/archive/` notes are excluded from current operating truth.",
    ]
    findings = payload.get("findings", [])
    if isinstance(findings, list) and findings:
        lines.extend(["", "## Findings"])
        for finding in findings[:50]:
            if isinstance(finding, dict):
                lines.append(f"- `{finding.get('file')}` line `{finding.get('line')}` markers `{finding.get('markers')}`")
    save_text_atomic(REPORT_PATH, "\n".join(lines) + "\n")


def validate() -> dict[str, object]:
    policy = _policy()
    files = _iter_release_docs(policy)
    markers = _disallowed_markers(policy)
    findings: list[dict[str, object]] = []
    for path in files:
        findings.extend(_scan_file(path, markers))
    payload = {
        "meta": {
            "kind": "release_language_validation",
            "version": "v1",
            "policy": POLICY_PATH.relative_to(ROOT).as_posix(),
            "scope": "SAGE-owned release-facing Markdown docs; docs/archive is excluded as historical traceability.",
        },
        "summary": {
            "status": "PASS" if not findings else "FAIL",
            "files_scanned": len(files),
            "findings": len(findings),
        },
        "findings": findings[:200],
    }
    save_json_atomic(RAW_DIR / "release_language_validation.json", payload)
    _write_report(payload)
    return payload


def main() -> int:
    payload = validate()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

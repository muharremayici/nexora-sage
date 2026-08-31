from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, save_json_atomic, save_text_atomic
from tools.core.text_surface_policy import POLICY_PATH, load_text_surface_policy

REPORT_PATH = ROOT / "output" / "reports" / "text_surface_integrity_validation.md"


def _load_policy() -> dict[str, Any]:
    return load_text_surface_policy()


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_excluded(rel_path: str, prefixes: set[str], path_parts: set[str]) -> bool:
    normalized = rel_path.replace("\\", "/")
    if any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in prefixes):
        return True
    return bool(set(normalized.split("/")) & path_parts)


def _iter_text_files(policy: dict[str, Any]) -> list[Path]:
    scope = policy.get("source_clean_scope", {}) if isinstance(policy, dict) else {}
    include_roots = scope.get("include_roots", []) if isinstance(scope, dict) else []
    suffixes = {str(item).lower() for item in scope.get("include_suffixes", [])} if isinstance(scope, dict) else set()
    excluded = {str(item).replace("\\", "/").strip("/") for item in scope.get("exclude_path_prefixes", [])} if isinstance(scope, dict) else set()
    excluded_parts = {str(item).strip() for item in scope.get("exclude_path_parts", [])} if isinstance(scope, dict) else set()

    paths: set[Path] = set()
    for item in include_roots:
        candidate = ROOT / str(item)
        if candidate.is_file() and candidate.suffix.lower() in suffixes:
            rel_path = _rel(candidate)
            if not _is_excluded(rel_path, excluded, excluded_parts):
                paths.add(candidate)
        elif candidate.is_dir():
            for child in candidate.rglob("*"):
                if not child.is_file() or child.suffix.lower() not in suffixes:
                    continue
                rel_path = _rel(child)
                if not _is_excluded(rel_path, excluded, excluded_parts):
                    paths.add(child)
    return sorted(paths)


def _scan_file(
    path: Path,
    policy: dict[str, Any],
    *,
    relative_path: str | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    rel_path = relative_path or _rel(path)
    checks = policy.get("checks", {}) if isinstance(policy.get("checks"), dict) else {}
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        findings.append(
            {
                "file": rel_path,
                "kind": "invalid_utf8",
                "message": str(exc),
                "byte_offset": exc.start,
            }
        )
        return findings

    if checks.get("replacement_character") and "\ufffd" in text:
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "\ufffd" in line:
                findings.append(
                    {
                        "file": rel_path,
                        "line": line_no,
                        "kind": "replacement_character",
                        "excerpt": line[:220],
                    }
                )

    if checks.get("mojibake_marker_visibility"):
        allowed = policy.get("mojibake_marker_allowed_files", {})
        allowed_files = set(allowed.keys()) if isinstance(allowed, dict) else set()
        if rel_path not in allowed_files:
            markers = [str(marker) for marker in policy.get("mojibake_markers", []) if str(marker) and str(marker) in text]
            if markers:
                findings.append(
                    {
                        "file": rel_path,
                        "kind": "mojibake_marker",
                        "markers": sorted(set(markers)),
                    }
                )
    return findings


def _write_report(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# Text Surface Integrity Validation",
        "",
        f"- status: `{summary['status']}`",
        f"- files_scanned: `{summary['files_scanned']}`",
        f"- findings: `{summary['findings']}`",
        "",
        "This gate validates source bytes and decoded text. Terminal mojibake is a rendering issue unless the decoded source contains invalid UTF-8, replacement characters or unexpected mojibake markers.",
    ]
    if payload["findings"]:
        lines.extend(["", "## Findings"])
        for finding in payload["findings"][:50]:
            lines.append(f"- `{finding.get('file')}`: `{finding.get('kind')}`")
    save_text_atomic(REPORT_PATH, "\n".join(lines) + "\n")


def validate() -> dict[str, Any]:
    policy = _load_policy()
    files = _iter_text_files(policy)
    findings: list[dict[str, Any]] = []
    for path in files:
        findings.extend(_scan_file(path, policy))
    payload = {
        "meta": {
            "kind": "text_surface_integrity_validation",
            "version": "1.0.0",
            "policy": POLICY_PATH.relative_to(ROOT).as_posix(),
        },
        "summary": {
            "status": "PASS" if not findings else "FAIL",
            "files_scanned": len(files),
            "findings": len(findings),
            "strict_utf8_invariant": bool(policy.get("checks", {}).get("strict_utf8_decode")),
        },
        "findings": findings[:200],
    }
    save_json_atomic(RAW_DIR / "text_surface_integrity_validation.json", payload)
    _write_report(payload)
    return payload


def main() -> int:
    payload = validate()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

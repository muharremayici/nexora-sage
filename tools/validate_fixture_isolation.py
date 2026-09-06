from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file

POLICY_PATH = CONFIG_DIR / "fixture_isolation_policy.json"


def _rel(path: Path) -> str:
    return path.relative_to(CODE_MAPS_DIR).as_posix()


def _protected_files(protected_paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in protected_paths:
        root = (CODE_MAPS_DIR / item).resolve()
        if root.is_file() and root.suffix == ".py":
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return sorted({path for path in files if "__pycache__" not in path.parts})


def _allowed(path: str, marker: str, policy: dict[str, Any]) -> bool:
    allowed = policy.get("allowed_references", {})
    if not isinstance(allowed, dict):
        return False
    allowed_markers = allowed.get(path, [])
    if not isinstance(allowed_markers, list):
        return False
    return any(str(item) in marker or marker in str(item) for item in allowed_markers)


def validate_fixture_isolation() -> dict[str, Any]:
    policy = load_json_file(POLICY_PATH, {})
    protected_paths = policy.get("protected_paths", []) if isinstance(policy, dict) else []
    markers = policy.get("fixture_markers", []) if isinstance(policy, dict) else []
    protected_paths = [str(item) for item in protected_paths if str(item).strip()]
    markers = [str(item) for item in markers if str(item).strip()]

    inspected = 0
    references: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    for path in _protected_files(protected_paths):
        inspected += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            violations.append({"file": _rel(path), "marker": "<read_error>", "reason": str(exc)})
            continue
        rel_path = _rel(path)
        for marker in markers:
            if marker not in text:
                continue
            row = {
                "file": rel_path,
                "marker": marker,
                "allowed": _allowed(rel_path, marker, policy),
            }
            references.append(row)
            if not row["allowed"]:
                violations.append({**row, "reason": "fixture_or_golden_reference_in_protected_runtime_surface"})

    payload = {
        "meta": {"kind": "fixture_isolation_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if not violations else "FAIL",
            "protected_files": inspected,
            "references": len(references),
            "allowed_references": sum(1 for row in references if row.get("allowed")),
            "violations": len(violations),
        },
        "policy": {
            "protected_paths": protected_paths,
            "fixture_markers": markers,
        },
        "references": references,
        "violations": violations,
    }
    save_json_atomic(RAW_DIR / "fixture_isolation_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "fixture_isolation_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Fixture Isolation Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- protected_files: `{summary.get('protected_files')}`",
        f"- references: `{summary.get('references')}`",
        f"- allowed_references: `{summary.get('allowed_references')}`",
        f"- violations: `{summary.get('violations')}`",
        "",
        "| File | Marker | Allowed |",
        "|---|---|---:|",
    ]
    for row in payload.get("references", []):
        if not isinstance(row, dict):
            continue
        lines.append(f"| `{row.get('file')}` | `{row.get('marker')}` | `{row.get('allowed')}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = validate_fixture_isolation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

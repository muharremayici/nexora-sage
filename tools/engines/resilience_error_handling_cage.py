from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.json_io import load_json_object_strict
from tools.core.logger import logger
from tools.core.source_snapshot_reader import load_source_text


POLICY_PATH = CONFIG_DIR / "resilience_error_handling_policy.json"


def _policy() -> dict[str, Any]:
    return load_json_object_strict(POLICY_PATH, label="Resilience error-handling policy")


def _policy_list(policy: dict[str, Any], key: str) -> list[str]:
    value = policy.get(key, [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _project_root(project: str) -> Path:
    rel = (DYNAMIC_CONFIG.get("variations") or {}).get(project, "")
    return (ROOT / rel).resolve()


def _read_project_file(project: str, rel_path: str) -> str:
    path = (_project_root(project) / rel_path).resolve()
    return load_source_text(
        project,
        rel_path,
        fallback_path=path,
        component="resilience_error_handling_cage",
    ) or ""


def _path_in_scope(rel_path: str, policy: dict[str, Any]) -> bool:
    normalized = rel_path.replace("\\", "/")
    suffixes = tuple(_policy_list(policy, "source_file_extensions"))
    return bool(suffixes and normalized.endswith(suffixes))


def _text_has_any(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def _risk(policy: dict[str, Any], risk_id: str) -> dict[str, Any]:
    catalog = policy.get("risk_catalog", {})
    if not isinstance(catalog, dict):
        return {}
    value = catalog.get(risk_id, {})
    return value if isinstance(value, dict) else {}


def _finding(*, project: str, rel_path: str, line: int, marker: str, policy: dict[str, Any]) -> dict[str, Any]:
    risk_id = "external_call_without_visible_resilience_contract"
    risk = _risk(policy, risk_id)
    return {
        "project": project,
        "file": rel_path.replace("\\", "/"),
        "line": line,
        "dimension": risk.get("dimension", "resilience_error_handling"),
        "risk": risk.get("risk", risk_id),
        "risk_id": risk_id,
        "score": int(risk.get("score", 5) or 5),
        "confidence": "candidate",
        "evidence_kinds": ["static_policy", "local_context_window"],
        "evidence": f"External call marker {marker!r} has no visible configured error, timeout, retry or fallback contract nearby.",
        "recommended_action": risk.get("recommended_action", "Add visible error handling and timeout/fallback contracts."),
        "policy_source": "config/resilience_error_handling_policy.json",
        "runtime_proof_status": "needs_runtime_proof",
        "evidence_spans": [{"file": rel_path.replace("\\", "/"), "line": line, "source": "static_policy"}],
    }


def _call_matches(line: str, markers: list[str]) -> list[str]:
    lowered = line.lower()
    return [marker for marker in markers if marker.lower() in lowered]


def _context_for(lines: list[str], index: int, window: int) -> str:
    start = max(0, index - window)
    end = min(len(lines), index + window + 1)
    return "\n".join(lines[start:end])


def analyze_resilience_file(project: str, rel_path: str, content: str, policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    policy = policy or _policy()
    normalized = rel_path.replace("\\", "/")
    if not _path_in_scope(normalized, policy):
        return None

    call_markers = _policy_list(policy, "external_call_markers")
    contract_markers = _policy_list(policy, "required_contract_markers")
    safe_wrappers = _policy_list(policy, "safe_wrapper_markers")
    if not call_markers:
        return {"project": project, "file": normalized, "findings": []}

    window = int(policy.get("context_window_lines", 8) or 8)
    lines = content.splitlines()
    findings: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for index, line in enumerate(lines):
        markers = _call_matches(line, call_markers)
        if not markers:
            continue
        context = _context_for(lines, index, window)
        if _text_has_any(context, safe_wrappers) or _text_has_any(context, contract_markers):
            continue
        for marker in markers:
            key = (index + 1, marker)
            if key in seen:
                continue
            seen.add(key)
            findings.append(_finding(project=project, rel_path=normalized, line=index + 1, marker=marker, policy=policy))

    return {"project": project, "file": normalized, "findings": findings}


def run_resilience_error_handling_cage() -> dict[str, Any]:
    logger.info("Analyzing resilience error-handling cage...")
    policy = _policy()
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    if isinstance(atlas, dict):
        for project, pdata in atlas.items():
            project_files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            for rel_path in project_files:
                rel_text = str(rel_path)
                if not _path_in_scope(rel_text, policy):
                    continue
                content = _read_project_file(str(project), rel_text)
                if not content:
                    continue
                row = analyze_resilience_file(str(project), rel_text, content, policy)
                if not row:
                    continue
                rows.append({"project": str(project), "file": rel_text.replace("\\", "/")})
                findings.extend(row["findings"])

    risk_counts = Counter(str(item.get("risk")) for item in findings)
    by_project = Counter(str(item.get("project")) for item in findings)
    sorted_findings = sorted(
        findings,
        key=lambda item: (-int(item.get("score", 0)), item.get("project", ""), item.get("file", ""), int(item.get("line", 0) or 0)),
    )
    payload = {
        "meta": {
            "kind": "resilience_error_handling_cage",
            "version": "v1",
            "policy_source": "config/resilience_error_handling_policy.json",
        },
        "summary": {
            "status": "PASS",
            "files_analyzed": len(rows),
            "findings": len(sorted_findings),
            "risk_counts": dict(sorted(risk_counts.items())),
            "by_project": dict(sorted(by_project.items())),
        },
        "policy": {
            "source_file_extensions": _policy_list(policy, "source_file_extensions"),
            "external_call_markers": _policy_list(policy, "external_call_markers"),
            "required_contract_markers": _policy_list(policy, "required_contract_markers"),
            "safe_wrapper_markers": _policy_list(policy, "safe_wrapper_markers"),
            "false_positive_policy": policy.get("false_positive_policy", {}),
        },
        "files": rows,
        "findings": sorted_findings,
    }
    save_json_atomic(RAW_DIR / "resilience_error_handling_cage.json", payload)
    save_text_atomic(REPORTS_DIR / "resilience_error_handling_cage.md", _render_markdown(payload))
    return payload


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Resilience And Error Handling Cage",
        "",
        f"- status: `{summary.get('status')}`",
        f"- files_analyzed: `{summary.get('files_analyzed', 0)}`",
        f"- findings: `{summary.get('findings', 0)}`",
        "",
        "| Risk | Count |",
        "|---|---:|",
    ]
    for risk, count in (summary.get("risk_counts") or {}).items():
        lines.append(f"| `{risk}` | {count} |")
    lines.extend(["", "## Findings", "", "| Project | File | Line | Risk | Evidence | Action |", "|---|---|---:|---|---|---|"])
    for item in payload.get("findings", [])[:100]:
        evidence = str(item.get("evidence") or "").replace("|", "\\|")
        action = str(item.get("recommended_action") or "").replace("|", "\\|")
        lines.append(
            f"| `{item.get('project')}` | `{item.get('file')}` | {item.get('line')} | "
            f"`{item.get('risk')}` | {evidence} | {action} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run_resilience_error_handling_cage()

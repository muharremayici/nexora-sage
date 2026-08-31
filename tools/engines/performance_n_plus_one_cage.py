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


POLICY_PATH = CONFIG_DIR / "performance_n_plus_one_policy.json"


def _line_for(content: str, index: int) -> int:
    return content[: max(0, index)].count("\n") + 1


def _policy() -> dict[str, Any]:
    return load_json_object_strict(POLICY_PATH, label="Performance N+1 policy")


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
        component="performance_n_plus_one_cage",
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


def _finding(
    *,
    project: str,
    rel_path: str,
    line: int,
    risk_id: str,
    evidence: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    risk = _risk(policy, risk_id)
    return {
        "project": project,
        "file": rel_path.replace("\\", "/"),
        "line": line,
        "dimension": risk.get("dimension", "performance_n_plus_one"),
        "risk": risk.get("risk", risk_id),
        "risk_id": risk_id,
        "score": int(risk.get("score", 5) or 5),
        "confidence": "likely",
        "evidence_kinds": ["static_policy", "loop_scope"],
        "evidence": evidence,
        "recommended_action": risk.get("recommended_action", "Batch or bound per-item async I/O."),
        "policy_source": "config/performance_n_plus_one_policy.json",
        "runtime_proof_status": "needs_runtime_proof",
        "evidence_spans": [{"file": rel_path.replace("\\", "/"), "line": line, "source": "static_policy"}],
    }


def _loop_ranges(content: str, policy: dict[str, Any]) -> list[tuple[int, int, int, str]]:
    patterns = [re.compile(pattern) for pattern in _policy_list(policy, "loop_start_patterns")]
    max_lines = int(policy.get("max_loop_scan_lines", 80) or 80)
    ranges: list[tuple[int, int, int, str]] = []
    lines = content.splitlines()
    for index, line in enumerate(lines):
        matched = next((pattern.pattern for pattern in patterns if pattern.search(line)), "")
        if not matched:
            continue
        brace_depth = line.count("{") - line.count("}")
        end = min(len(lines), index + max_lines)
        if brace_depth <= 0:
            ranges.append((index, min(len(lines), index + 1), index + 1, matched))
            continue
        for cursor in range(index + 1, min(len(lines), index + max_lines)):
            brace_depth += lines[cursor].count("{") - lines[cursor].count("}")
            if brace_depth <= 0:
                end = cursor + 1
                break
        ranges.append((index, end, index + 1, matched))
    return ranges


def analyze_performance_file(project: str, rel_path: str, content: str, policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    policy = policy or _policy()
    normalized = rel_path.replace("\\", "/")
    if not _path_in_scope(normalized, policy):
        return None

    async_markers = _policy_list(policy, "async_markers")
    call_markers = _policy_list(policy, "call_markers")
    safe_wrappers = _policy_list(policy, "safe_wrapper_patterns")
    if not async_markers or not call_markers:
        return {"project": project, "file": normalized, "findings": []}

    findings: list[dict[str, Any]] = []
    lines = content.splitlines()
    for start, end, line_no, pattern in _loop_ranges(content, policy):
        block = "\n".join(lines[start:end])
        if _text_has_any(block, safe_wrappers):
            continue
        has_async = _text_has_any(block, async_markers)
        has_call = _text_has_any(block, call_markers)
        if not (has_async and has_call):
            continue
        risk_id = "iterator_callback_async_call" if "." in lines[start] else "loop_contained_async_call"
        findings.append(
            _finding(
                project=project,
                rel_path=normalized,
                line=line_no,
                risk_id=risk_id,
                evidence=f"Loop-like scope matched {pattern!r} and contains configured async I/O markers.",
                policy=policy,
            )
        )

    return {"project": project, "file": normalized, "findings": findings}


def run_performance_n_plus_one_cage() -> dict[str, Any]:
    logger.info("Analyzing performance N+1 cage...")
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
                row = analyze_performance_file(str(project), rel_text, content, policy)
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
            "kind": "performance_n_plus_one_cage",
            "version": "v1",
            "policy_source": "config/performance_n_plus_one_policy.json",
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
            "loop_start_patterns": _policy_list(policy, "loop_start_patterns"),
            "async_markers": _policy_list(policy, "async_markers"),
            "call_markers": _policy_list(policy, "call_markers"),
            "safe_wrapper_patterns": _policy_list(policy, "safe_wrapper_patterns"),
            "false_positive_policy": policy.get("false_positive_policy", {}),
        },
        "files": rows,
        "findings": sorted_findings,
    }
    save_json_atomic(RAW_DIR / "performance_n_plus_one_cage.json", payload)
    save_text_atomic(REPORTS_DIR / "performance_n_plus_one_cage.md", _render_markdown(payload))
    return payload


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Performance And N+1 Cage",
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
    run_performance_n_plus_one_cage()

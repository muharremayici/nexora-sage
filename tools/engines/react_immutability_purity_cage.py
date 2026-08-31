from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.json_io import load_json_object_strict
from tools.core.logger import logger
from tools.core.react_evidence import attach_react_evidence_contract
from tools.core.source_snapshot_reader import load_source_text


POLICY_PATH = CONFIG_DIR / "react_immutability_policy.json"
PROGRESS_INTERVAL_SECONDS = 10.0
SLOW_FILE_SECONDS = 1.0
SCOPE_SOURCE = "sqlite_backed_atlas"
CONTENT_SOURCE = "sqlite_source_snapshots_primary"
SQLITE_SOURCE_CONTENT_STATUS = "enabled_with_live_source_fallback"
SOURCE_CONTENT_BOUNDARY = (
    "Atlas/SQLite selects project and file scope. Source text is read from "
    "hash-validated SQLite source_snapshots first; live target files are fallback only."
)
STATE_DECLARATION_TEMPLATE = r"(?:const|let|var)\s*\[\s*(?P<state>[A-Za-z_$][\w$]*)\s*,\s*(?P<setter>[A-Za-z_$][\w$]*)\s*\]\s*=\s*(?P<hook>{hooks})\s*\("
ASSIGNMENT_OPERATOR_PATTERN = r"(?:\+\+|--|\+=|-=|\*=|/=|%=|(?<![=!<>])=(?!=|>))"
TARGET_BOUNDARY_TEMPLATE = r"(?<![.\w$]){target}\b"
DIRECT_ASSIGNMENT_TEMPLATE = TARGET_BOUNDARY_TEMPLATE + r"\s*(?:\[[^\]]+\]|\.[A-Za-z_$][\w$]*)\s*" + ASSIGNMENT_OPERATOR_PATTERN
METHOD_CALL_TEMPLATE = TARGET_BOUNDARY_TEMPLATE + r"\s*\.\s*(?P<method>{methods})\s*\("
RENDER_MUTATION_TEMPLATE = r"^\s*(?P<target>{roots})(?:\.[A-Za-z_$][\w$]*|\[[^\]]+\])?\s*" + ASSIGNMENT_OPERATOR_PATTERN


def _line_for(content: str, index: int) -> int:
    return content[: max(0, index)].count("\n") + 1


def _log(message: str) -> None:
    print(f"[react-immutability-purity-cage] {message}", flush=True)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _policy() -> dict[str, Any]:
    return load_json_object_strict(POLICY_PATH, label="React immutability policy")


def _policy_list(policy: dict[str, Any], key: str) -> list[str]:
    value = policy.get(key, [])
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _project_root(project: str) -> Path:
    rel = (DYNAMIC_CONFIG.get("variations") or {}).get(project, "")
    return (ROOT / rel).resolve()


def _read_project_file(project: str, rel_path: str) -> str:
    path = (_project_root(project) / rel_path).resolve()
    return load_source_text(
        project,
        rel_path,
        fallback_path=path,
        component="react_immutability_purity_cage",
    ) or ""


def _is_react_candidate(rel_path: str, content: str, policy: dict[str, Any]) -> bool:
    normalized = rel_path.replace("\\", "/")
    suffixes = tuple(_policy_list(policy, "source_file_extensions"))
    if suffixes and not normalized.endswith(suffixes):
        return False
    if normalized.endswith((".tsx", ".jsx")):
        return True
    hook_names = _policy_list(policy, "state_hook_names")
    return any(f"{hook}(" in content for hook in hook_names)


def _path_can_contain_react(rel_path: str, policy: dict[str, Any]) -> bool:
    normalized = rel_path.replace("\\", "/")
    suffixes = tuple(_policy_list(policy, "source_file_extensions"))
    return bool(suffixes and normalized.endswith(suffixes))


def _atlas_has_react_or_state_signal(fdata: Any) -> bool:
    if not isinstance(fdata, dict):
        return False
    features = {str(item) for item in fdata.get("features", []) or []}
    symbol_types = {
        str((symbol or {}).get("type") or "")
        for symbol in fdata.get("symbols", []) or []
        if isinstance(symbol, dict)
    }
    imports = {str(item) for item in fdata.get("imports", []) or []}
    return (
        any(item.startswith(("Hook:", "React", "Zustand", "Redux")) for item in features)
        or bool(symbol_types & {"component", "hook"})
        or any("react" in item.lower() for item in imports)
    )


def _state_variables(content: str, policy: dict[str, Any]) -> dict[str, str]:
    hooks = _policy_list(policy, "state_hook_names")
    if not hooks:
        return {}
    hook_pattern = "|".join(re.escape(hook) for hook in hooks)
    declaration_re = re.compile(STATE_DECLARATION_TEMPLATE.format(hooks=hook_pattern))
    return {
        str(match.group("state")): str(match.group("hook"))
        for match in declaration_re.finditer(content)
    }


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
    state_variable: str | None = None,
    method: str | None = None,
) -> dict[str, Any]:
    risk = _risk(policy, risk_id)
    item = {
        "project": project,
        "file": rel_path.replace("\\", "/"),
        "line": line,
        "dimension": risk.get("dimension", "immutability_purity"),
        "risk": risk.get("risk", risk_id),
        "risk_id": risk_id,
        "score": int(risk.get("score", 5) or 5),
        "confidence": "likely",
        "evidence_kinds": ["static_policy", "react_state_signal"],
        "evidence": evidence,
        "state_variable": state_variable,
        "mutating_method": method,
        "recommended_action": risk.get("recommended_action", "Avoid direct mutation and preserve pure render/state contracts."),
        "policy_source": "config/react_immutability_policy.json",
    }
    return attach_react_evidence_contract(
        item,
        evidence_kinds=set(item["evidence_kinds"]),
        line=line,
        current_confidence=str(item["confidence"]),
    )


def analyze_immutability_file(project: str, rel_path: str, content: str, policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    policy = policy or _policy()
    normalized = rel_path.replace("\\", "/")
    if not _is_react_candidate(normalized, content, policy):
        return None

    state_vars = _state_variables(content, policy)
    methods = _policy_list(policy, "mutating_methods")
    roots = _policy_list(policy, "protected_roots")
    findings: list[dict[str, Any]] = []

    if methods:
        method_pattern = "|".join(re.escape(method) for method in methods)
        for state_var, hook_name in state_vars.items():
            method_re = re.compile(METHOD_CALL_TEMPLATE.format(target=re.escape(state_var), methods=method_pattern))
            for match in method_re.finditer(content):
                method = str(match.group("method"))
                findings.append(
                    _finding(
                        project=project,
                        rel_path=normalized,
                        line=_line_for(content, match.start()),
                        risk_id="state_variable_mutating_method",
                        evidence=f"{state_var}.{method}(...) mutates state variable created by {hook_name}.",
                        policy=policy,
                        state_variable=state_var,
                        method=method,
                    )
                )

    for state_var, hook_name in state_vars.items():
        assignment_re = re.compile(DIRECT_ASSIGNMENT_TEMPLATE.format(target=re.escape(state_var)))
        for match in assignment_re.finditer(content):
            findings.append(
                _finding(
                    project=project,
                    rel_path=normalized,
                    line=_line_for(content, match.start()),
                    risk_id="state_variable_direct_assignment",
                    evidence=f"{state_var} is directly assigned after being created by {hook_name}.",
                    policy=policy,
                    state_variable=state_var,
                )
            )

    for root in roots:
        assignment_re = re.compile(DIRECT_ASSIGNMENT_TEMPLATE.format(target=re.escape(root)))
        for match in assignment_re.finditer(content):
            findings.append(
                _finding(
                    project=project,
                    rel_path=normalized,
                    line=_line_for(content, match.start()),
                    risk_id="props_direct_assignment",
                    evidence=f"{root} is mutated directly.",
                    policy=policy,
                )
            )

    if roots:
        render_roots = "|".join(re.escape(root) for root in roots)
        render_re = re.compile(RENDER_MUTATION_TEMPLATE.format(roots=render_roots), re.MULTILINE)
        for match in render_re.finditer(content):
            findings.append(
                _finding(
                    project=project,
                    rel_path=normalized,
                    line=_line_for(content, match.start()),
                    risk_id="render_time_mutable_assignment",
                    evidence=f"{match.group('target')} is assigned in render-visible code.",
                    policy=policy,
                )
            )

    return {
        "project": project,
        "file": normalized,
        "state_variables": sorted(state_vars),
        "findings": findings,
    }


def run_react_immutability_purity_cage() -> dict[str, Any]:
    started = time.perf_counter()
    last_progress = started
    logger.info("Analyzing React immutability and purity cage...")
    _log("START load_policy_and_atlas")
    policy = _policy()
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    _log(f"PASS load_policy_and_atlas ({_elapsed_ms(started)}ms)")
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    candidate_files = 0
    skipped_non_source = 0
    skipped_no_react_signal = 0
    slow_files: list[dict[str, Any]] = []

    if isinstance(atlas, dict):
        for project, pdata in atlas.items():
            project_files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            _log(f"START project {project} files={len(project_files)}")
            project_started = time.perf_counter()
            project_candidates = 0
            project_analyzed = 0
            for rel_path, fdata in project_files.items():
                now = time.perf_counter()
                if now - last_progress >= PROGRESS_INTERVAL_SECONDS:
                    _log(
                        "RUNNING scan "
                        f"project={project} candidates={candidate_files} analyzed={len(rows)} "
                        f"findings={len(findings)} elapsed={now - started:.0f}s"
                    )
                    last_progress = now
                if not _path_can_contain_react(str(rel_path), policy):
                    skipped_non_source += 1
                    continue
                if not _atlas_has_react_or_state_signal(fdata) and not str(rel_path).replace("\\", "/").endswith((".tsx", ".jsx")):
                    skipped_no_react_signal += 1
                    continue
                candidate_files += 1
                project_candidates += 1
                file_started = time.perf_counter()
                content = _read_project_file(str(project), str(rel_path))
                row = analyze_immutability_file(str(project), str(rel_path), content, policy)
                file_duration = time.perf_counter() - file_started
                if file_duration >= SLOW_FILE_SECONDS:
                    slow_files.append(
                        {
                            "project": str(project),
                            "file": str(rel_path).replace("\\", "/"),
                            "duration_ms": round(file_duration * 1000, 2),
                            "bytes": len(content.encode("utf-8", errors="replace")),
                        }
                    )
                if not row:
                    continue
                project_analyzed += 1
                rows.append({key: value for key, value in row.items() if key != "findings"})
                findings.extend(row["findings"])
            _log(
                "PASS project "
                f"{project} candidates={project_candidates} analyzed={project_analyzed} "
                f"({time.perf_counter() - project_started:.2f}s)"
            )

    risk_counts = Counter(str(item.get("risk")) for item in findings)
    by_project = Counter(str(item.get("project")) for item in findings)
    sorted_findings = sorted(
        findings,
        key=lambda item: (-int(item.get("score", 0)), item.get("project", ""), item.get("file", ""), int(item.get("line", 0) or 0)),
    )
    payload = {
        "meta": {
            "kind": "react_immutability_purity_cage",
            "version": "v1",
            "policy_source": "config/react_immutability_policy.json",
        },
        "summary": {
            "status": "PASS",
            "files_analyzed": len(rows),
            "findings": len(sorted_findings),
            "candidate_files": candidate_files,
            "skipped_non_source": skipped_non_source,
            "skipped_no_react_signal": skipped_no_react_signal,
            "duration_ms": _elapsed_ms(started),
            "slow_files": slow_files[:20],
            "risk_counts": dict(sorted(risk_counts.items())),
            "by_project": dict(sorted(by_project.items())),
        },
        "source_contract": {
            "scope_source": SCOPE_SOURCE,
            "content_source": CONTENT_SOURCE,
            "sqlite_source_content_status": SQLITE_SOURCE_CONTENT_STATUS,
            "boundary": SOURCE_CONTENT_BOUNDARY,
            "sqlite_first_operational_boundary": (
                "Source content is SQLite-first through source_snapshots when the snapshot row is ok "
                "and hash-backed. Live source fallback remains explicit telemetry-backed degradation "
                "for missing, invalid, oversized, or unreadable snapshot rows."
            ),
        },
        "policy": {
            "source_file_extensions": _policy_list(policy, "source_file_extensions"),
            "state_hook_names": _policy_list(policy, "state_hook_names"),
            "mutating_methods": _policy_list(policy, "mutating_methods"),
            "protected_roots": _policy_list(policy, "protected_roots"),
            "false_positive_policy": policy.get("false_positive_policy", {}),
        },
        "files": rows,
        "findings": sorted_findings,
    }
    _log(
        "PASS analysis "
        f"candidates={candidate_files} analyzed={len(rows)} findings={len(sorted_findings)} "
        f"slow_files={len(slow_files)} ({_elapsed_ms(started)}ms)"
    )
    save_json_atomic(RAW_DIR / "react_immutability_purity_cage.json", payload)
    save_text_atomic(REPORTS_DIR / "react_immutability_purity_cage.md", _render_markdown(payload))
    _log("PASS write_artifacts")
    return payload


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React Immutability And Purity Cage",
        "",
        f"- status: `{summary.get('status')}`",
        f"- files_analyzed: `{summary.get('files_analyzed', 0)}`",
        f"- findings: `{summary.get('findings', 0)}`",
        "",
        "## Source Contract",
        "",
        f"- scope_source: `{payload.get('source_contract', {}).get('scope_source')}`",
        f"- content_source: `{payload.get('source_contract', {}).get('content_source')}`",
        f"- sqlite_source_content_status: `{payload.get('source_contract', {}).get('sqlite_source_content_status')}`",
        f"- boundary: {payload.get('source_contract', {}).get('boundary')}",
        "",
        "| Risk | Count |",
        "|---|---:|",
    ]
    for risk, count in (summary.get("risk_counts") or {}).items():
        lines.append(f"| `{risk}` | {count} |")
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "| Project | File | Line | Risk | Evidence | Action |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for item in payload.get("findings", [])[:100]:
        evidence = str(item.get("evidence") or "").replace("|", "\\|")
        action = str(item.get("recommended_action") or "").replace("|", "\\|")
        lines.append(
            f"| `{item.get('project')}` | `{item.get('file')}` | {item.get('line')} | "
            f"`{item.get('risk')}` | {evidence} | {action} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run_react_immutability_purity_cage()

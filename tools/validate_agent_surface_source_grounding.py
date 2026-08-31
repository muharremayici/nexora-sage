#!/usr/bin/env python3
"""Validate that agent-facing packets resolve to real target-repository files."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.config import ROOT as TARGET_ROOT
from tools.core.agent_packet_budget import BOUNDED_AGENT_PACKET_TOKENS
from tools.core.atlas_io import load_atlas_data


MAX_AGENT_PACKET_TOKENS = BOUNDED_AGENT_PACKET_TOKENS
INTERNAL_LEAK_TERMS = (
    "target_abs",
    "resolved_node",
    "sqlite_writer",
    "canonical_step_command",
    "direct_file_invocation",
)
FAIL_CLOSED_STATUS_TERMS = (
    "missing_target",
    "missing_required_target_artifacts",
    "missing_watchdog_session",
    "invalid_external_target_root",
    "no_active_signals",
)


def _extract_yaml_block(body: str) -> str:
    match = re.search(r"```yaml\s*(.*?)\s*```", body, re.DOTALL)
    return match.group(1) if match else ""


def _extract_field(text: str, key: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}:\s*\"?([^\"\n]+)\"?\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_list(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    values: list[str] = []
    in_list = False
    base_indent = 0
    for line in lines:
        if re.match(rf"^\s*{re.escape(key)}:\s*$", line):
            in_list = True
            base_indent = len(line) - len(line.lstrip())
            continue
        if not in_list:
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if not stripped:
            continue
        if indent <= base_indent and not stripped.startswith("- "):
            break
        if stripped.startswith("- "):
            item = stripped[2:].strip().strip("\"'")
            if item:
                values.append(item)
            continue
        if indent <= base_indent:
            break
    return values


def _extract_inline_list_values(value: str) -> list[str]:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return []
    return [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]


def _extract_import_evidence_pairs(text: str) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    current_file = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(("target_file:", "file:")) and ": " in line:
            current_file = line.split(": ", 1)[1].strip().strip("\"'")
            continue
        if not current_file or not line.startswith("evidence_samples:"):
            continue
        raw_value = line.split(":", 1)[1].strip()
        for evidence in _extract_inline_list_values(raw_value):
            match = re.search(r"\bimports\s+(.+)$", evidence)
            if not match:
                continue
            import_spec = match.group(1).strip().strip("\"'").rstrip(".")
            if import_spec:
                pairs.append(
                    {
                        "target_file": current_file,
                        "evidence": evidence,
                        "import_spec": import_spec,
                    }
                )
    return pairs


def _import_evidence_failures(analysis_root: str, body: str) -> list[dict[str, str]]:
    if not analysis_root:
        return []
    root = Path(analysis_root)
    failures: list[dict[str, str]] = []
    for pair in _extract_import_evidence_pairs(body):
        target_file = pair["target_file"]
        target_path = root / target_file
        if not target_path.exists() or not target_path.is_file():
            continue
        text = target_path.read_text(encoding="utf-8", errors="replace")
        if pair["import_spec"] not in text:
            failures.append(pair)
    return failures


def _snippet_line_failures(analysis_root: str, target_files: list[str], body: str) -> list[dict[str, Any]]:
    if not analysis_root or not target_files or "code: |-" not in body:
        return []
    root = Path(analysis_root)
    source_by_target: dict[str, list[str]] = {}

    def _target_lines(target: str) -> list[str] | None:
        target = str(target or "").replace("\\", "/").strip("/")
        if not target:
            return None
        if target not in source_by_target:
            target_path = root / target
            if not target_path.exists() or not target_path.is_file():
                return None
            source_by_target[target] = target_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return source_by_target[target]

    failures: list[dict[str, Any]] = []
    checked = 0
    in_target_snippet_section = False
    in_code_block = False
    current_target = target_files[0] if len(target_files) == 1 else ""
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        target_match = re.match(r"^-?\s*target_file:\s*\"?([^\"\n]+)\"?\s*$", stripped)
        if target_match:
            current_target = target_match.group(1).replace("\\", "/").strip("/")
            in_code_block = False
            continue
        if stripped == "target_source_snippets:":
            in_target_snippet_section = True
            in_code_block = False
            continue
        if stripped in {"test_source_snippets:", "evidence_source_snippets:", "dependency_source_snippets:", "patch_source_snippets:"}:
            in_target_snippet_section = False
            in_code_block = False
            continue
        if not in_target_snippet_section:
            continue
        if stripped == "code: |-":
            in_code_block = True
            continue
        if not in_code_block:
            continue
        match = re.match(r"^\s*(\d+): ?(.*)$", raw_line)
        if not match:
            continue
        line_no = int(match.group(1))
        snippet_text = match.group(2).rstrip()
        checked += 1
        source_lines = _target_lines(current_target)
        if source_lines is None:
            failures.append(
                {
                    "target_file": current_target,
                    "line": line_no,
                    "issue": "snippet_target_file_missing",
                    "actual": snippet_text,
                }
            )
            if len(failures) >= 5:
                break
            continue
        if line_no < 1 or line_no > len(source_lines) or source_lines[line_no - 1].rstrip() != snippet_text:
            failures.append(
                {
                    "target_file": current_target,
                    "line": line_no,
                    "issue": "snippet_line_not_found_in_declared_targets",
                    "actual": snippet_text,
                    "expected": source_lines[line_no - 1].rstrip() if 1 <= line_no <= len(source_lines) else "",
                }
            )
            if len(failures) >= 5:
                break
    if checked == 0 and "target_source_snippets:" in body:
        failures.append({"target_files": list(source_by_target), "issue": "snippet_code_block_has_no_numbered_lines"})
    return failures


def _source_lines_for(root: Path, cache: dict[str, list[str]], source_file: str) -> list[str] | None:
    source_file = source_file.replace("\\", "/").strip("/")
    if not source_file:
        return None
    if source_file not in cache:
        source_path = root / source_file
        if not source_path.exists() or not source_path.is_file():
            return None
        cache[source_file] = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return cache[source_file]


def _source_file_from_evidence(evidence: str) -> str:
    match = re.search(r"\b([A-Za-z0-9_./@+\-\[\]]+\.[A-Za-z0-9]+)\s+imports\s+", evidence)
    return match.group(1).replace("\\", "/").strip("/") if match else ""


def _nested_source_snippet_failures(analysis_root: str, body: str) -> list[dict[str, Any]]:
    has_nested_source_section = any(line.strip() == "source_snippets:" for line in body.splitlines())
    if not analysis_root or not has_nested_source_section or "code: |-" not in body:
        return []
    root = Path(analysis_root)
    source_cache: dict[str, list[str]] = {}
    failures: list[dict[str, Any]] = []
    current_file = ""
    current_evidence = ""
    active_source_snippets = False
    active_code_file = ""
    checked = 0

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        file_match = re.match(r"^-?\s*file:\s*\"?([^\"\n]+)\"?\s*$", stripped)
        if file_match:
            current_file = file_match.group(1).replace("\\", "/").strip("/")
            continue
        if stripped in {
            "target_source_snippets:",
            "evidence_source_snippets:",
            "dependency_evidence_snippets:",
            "test_source_snippets:",
            "proposed_change_snippets:",
            "impacted_tests:",
            "upstream_candidates:",
            "direct_dependents_sample:",
            "violations:",
        }:
            active_source_snippets = False
            active_code_file = ""
            current_evidence = ""
        if stripped == "source_snippets:":
            active_source_snippets = True
            active_code_file = ""
            current_evidence = ""
            continue
        if not active_source_snippets:
            continue
        evidence_match = re.match(r"^-\s*evidence:\s*\"?(.+?)\"?\s*$", stripped)
        if evidence_match:
            current_evidence = evidence_match.group(1)
            active_code_file = _source_file_from_evidence(current_evidence) or current_file
            continue
        if stripped == "code: |-":
            active_code_file = _source_file_from_evidence(current_evidence) or current_file
            continue
        match = re.match(r"^\s*(\d+): ?(.*)$", raw_line)
        if not match or not active_code_file:
            continue
        line_no = int(match.group(1))
        snippet_text = match.group(2).rstrip()
        source_lines = _source_lines_for(root, source_cache, active_code_file)
        if source_lines is None:
            failures.append(
                {
                    "source_file": active_code_file,
                    "line": line_no,
                    "issue": "source_snippet_file_missing",
                    "evidence": current_evidence,
                }
            )
            if len(failures) >= 5:
                break
            continue
        checked += 1
        if line_no < 1 or line_no > len(source_lines):
            failures.append(
                {
                    "source_file": active_code_file,
                    "line": line_no,
                    "issue": "source_snippet_line_out_of_range",
                    "source_line_count": len(source_lines),
                    "evidence": current_evidence,
                }
            )
        elif source_lines[line_no - 1].rstrip() != snippet_text:
            failures.append(
                {
                    "source_file": active_code_file,
                    "line": line_no,
                    "issue": "source_snippet_line_mismatch",
                    "expected": source_lines[line_no - 1].rstrip(),
                    "actual": snippet_text,
                    "evidence": current_evidence,
                }
            )
        if len(failures) >= 5:
            break

    if checked == 0 and has_nested_source_section:
        failures.append({"issue": "source_snippet_code_block_has_no_verified_numbered_lines"})
    return failures


def _partial_snippet_policy_failures(body: str) -> list[str]:
    if "partial_included_span_too_large" not in body:
        return []
    required = [
        "omitted_lines:",
        "edit_scope:",
        "do_not_edit_omitted_lines_without_follow_up",
        "follow_up_if_needed:",
    ]
    return [marker for marker in required if marker not in body]


def _status_is_fail_closed(body: str) -> bool:
    lower = body.lower()
    missing_grounding = (
        "target_grounding_status: \"missing_or_unindexed\"" in lower
        or "target_exists: false" in lower
        or "target_indexed: false" in lower
    )
    explicit_block = (
        "safe_to_apply: false" in lower
        or "status: \"fail\"" in lower
        or "unknown_target_not_grounded" in lower
        or "refresh_target_analysis_before_confidence_decision" in lower
    )
    return (any(term in lower for term in FAIL_CLOSED_STATUS_TERMS) or missing_grounding or explicit_block) and (
        "do not" in lower or "no edit" in lower or "before editing" in lower
    )


def _source_check(sample: dict[str, Any]) -> dict[str, Any]:
    name = sample.get("name", "")
    body = sample.get("body", "")
    yaml_block = _extract_yaml_block(body)
    estimated_tokens = int(sample.get("estimated_tokens") or 0)
    analysis_root = _extract_field(yaml_block, "analysis_root")
    target_files = _extract_list(yaml_block, "target_files")
    target_file = _extract_field(yaml_block, "target_file")
    if target_file:
        target_files.append(target_file)

    unique_targets = []
    for target in target_files:
        if target and target not in unique_targets:
            unique_targets.append(target)

    missing_targets: list[str] = []
    if analysis_root and unique_targets:
        root = Path(analysis_root)
        for target in unique_targets:
            if not (root / target).exists():
                missing_targets.append(target)
    import_evidence_failures = _import_evidence_failures(analysis_root, body)
    snippet_line_failures = _snippet_line_failures(analysis_root, unique_targets, body)
    nested_source_snippet_failures = _nested_source_snippet_failures(analysis_root, body)
    partial_snippet_policy_failures = _partial_snippet_policy_failures(body)

    has_target_contract = bool(
        "target_file:" in body
        or "target_files:" in body
        or "target_ref:" in body
        or "target_refs:" in body
    )
    has_open_contract = "analysis_root" in body and "open_files_with" in body
    if not has_open_contract and ("target_file:" in body or "target_files:" in body):
        has_open_contract = bool(analysis_root)

    internal_leaks = [term for term in INTERNAL_LEAK_TERMS if term in body]
    fail_closed = _status_is_fail_closed(body)
    needs_source_grounding = has_target_contract and not fail_closed

    passed = (
        estimated_tokens <= MAX_AGENT_PACKET_TOKENS
        and not internal_leaks
        and (not needs_source_grounding or bool(analysis_root))
        and (not missing_targets or fail_closed)
        and not import_evidence_failures
        and not snippet_line_failures
        and not nested_source_snippet_failures
        and not partial_snippet_policy_failures
    )
    return {
        "name": name,
        "passed": passed,
        "estimated_tokens": estimated_tokens,
        "analysis_root": analysis_root,
        "target_count": len(unique_targets),
        "missing_targets": missing_targets,
        "has_target_contract": has_target_contract,
        "has_open_contract": has_open_contract,
        "fail_closed": fail_closed,
        "internal_leaks": internal_leaks,
        "import_evidence_failures": import_evidence_failures,
        "snippet_line_failures": snippet_line_failures,
        "nested_source_snippet_failures": nested_source_snippet_failures,
        "partial_snippet_policy_failures": partial_snippet_policy_failures,
    }


def _path_contract_checks() -> list[dict[str, Any]]:
    """Validate the path naming contract across graph, editor and MCP refs."""

    checks: list[dict[str, Any]] = []
    atlas = load_atlas_data()
    sample: dict[str, Any] | None = None
    sample_project = ""
    sample_atlas_rel = ""

    project_items = list(atlas.items()) if isinstance(atlas, dict) else []
    project_items.sort(key=lambda item: 0 if str(item[0]) == "MAIN" else 1)
    for project_key, project_payload in project_items:
        if not isinstance(project_payload, dict):
            continue
        files = project_payload.get("files", {})
        if not isinstance(files, dict):
            continue
        for atlas_rel, meta in files.items():
            if not isinstance(meta, dict):
                continue
            workspace_rel = str(meta.get("workspace_rel") or meta.get("repo_relative_path") or "").replace("\\", "/").strip("/")
            if workspace_rel and workspace_rel != str(atlas_rel).replace("\\", "/").strip("/"):
                sample = meta
                sample_project = str(project_key)
                sample_atlas_rel = str(atlas_rel).replace("\\", "/").strip("/")
                break
        if sample:
            break

    if not sample:
        return [
            {
                "name": "path_contract_has_workspace_relative_sample",
                "passed": False,
                "kind": "path_contract",
                "details": "No Atlas file with distinct atlas_rel_path and workspace_rel was found.",
            }
        ]

    from tools.core.contextos_mcp import _agent_file_context

    workspace_rel = str(sample.get("workspace_rel") or sample.get("repo_relative_path") or "").replace("\\", "/").strip("/")
    repo_relative_path = str(sample.get("repo_relative_path") or workspace_rel).replace("\\", "/").strip("/")
    target_ref = str(sample.get("target_ref") or "").replace("\\", "/").strip()
    source_path = (TARGET_ROOT / repo_relative_path).resolve()
    try:
        inside_root = source_path.relative_to(TARGET_ROOT.resolve()) is not None
    except ValueError:
        inside_root = False

    graph_context = _agent_file_context(f"{sample_project}::{sample_atlas_rel}", default_project=sample_project)
    editor_context = _agent_file_context(f"{sample_project}::{workspace_rel}", default_project=sample_project)

    checks.append(
        {
            "name": "path_contract_distinguishes_graph_path_from_editor_path",
            "passed": bool(sample_project and sample_atlas_rel and repo_relative_path and sample_atlas_rel != repo_relative_path),
            "kind": "path_contract",
            "project_key": sample_project,
            "atlas_relative_path": sample_atlas_rel,
            "repo_relative_path": repo_relative_path,
            "target_ref": target_ref,
        }
    )
    checks.append(
        {
            "name": "path_contract_editor_path_resolves_under_analysis_root",
            "passed": inside_root and source_path.exists() and source_path.is_file(),
            "kind": "path_contract",
            "analysis_root": str(TARGET_ROOT),
            "repo_relative_path": repo_relative_path,
            "source_path": str(source_path),
        }
    )
    checks.append(
        {
            "name": "path_contract_target_ref_uses_project_and_editor_path",
            "passed": target_ref == f"{sample_project}::{repo_relative_path}",
            "kind": "path_contract",
            "target_ref": target_ref,
            "expected": f"{sample_project}::{repo_relative_path}",
        }
    )
    checks.append(
        {
            "name": "path_contract_context_resolves_atlas_and_workspace_forms",
            "passed": (
                graph_context.get("repo_relative_path") == repo_relative_path
                and graph_context.get("atlas_relative_path") == sample_atlas_rel
                and editor_context.get("repo_relative_path") == repo_relative_path
                and editor_context.get("atlas_relative_path") == sample_atlas_rel
            ),
            "kind": "path_contract",
            "graph_context": graph_context,
            "editor_context": editor_context,
        }
    )
    return checks


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Agent Surface Source Grounding Validation",
        "",
        f"- status: `{payload['summary']['status']}`",
        f"- checks: `{payload['summary']['total_checks']}`",
        f"- failed: `{payload['summary']['failed_checks']}`",
        f"- import evidence failures: `{payload['summary'].get('import_evidence_failures')}`",
        f"- snippet line failures: `{payload['summary'].get('snippet_line_failures')}`",
        f"- nested source snippet failures: `{payload['summary'].get('nested_source_snippet_failures')}`",
        f"- partial snippet policy failures: `{payload['summary'].get('partial_snippet_policy_failures')}`",
        "",
        "| Sample | Status | Targets | Missing | Tokens |",
        "|---|---:|---:|---:|---:|",
    ]
    for check in payload.get("checks", []):
        status = "PASS" if check.get("passed") else "FAIL"
        lines.append(
            f"| `{check.get('name')}` | {status} | "
            f"{check.get('target_count', 0)} | {len(check.get('missing_targets') or [])} | "
            f"{check.get('estimated_tokens', 0)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    source = RAW_DIR / "agent_surface_quality_review.json"
    if not source.exists():
        payload = {
            "meta": {"kind": "agent_surface_source_grounding_validation", "version": "v1"},
            "summary": {"status": "FAIL", "total_checks": 0, "passed_checks": 0, "failed_checks": 1},
            "checks": [
                {
                    "name": "agent_surface_quality_review_exists",
                    "passed": False,
                    "details": str(source),
                }
            ],
        }
    else:
        review = json.loads(source.read_text(encoding="utf-8"))
        checks = [_source_check(sample) for sample in review.get("samples", [])]
        checks.extend(_path_contract_checks())
        failed = [check for check in checks if not check.get("passed")]
        payload = {
            "meta": {"kind": "agent_surface_source_grounding_validation", "version": "v1"},
            "summary": {
                "status": "PASS" if not failed else "FAIL",
                "total_checks": len(checks),
                "passed_checks": len(checks) - len(failed),
                "failed_checks": len(failed),
                "max_agent_packet_tokens": MAX_AGENT_PACKET_TOKENS,
                "import_evidence_failures": sum(len(check.get("import_evidence_failures") or []) for check in checks),
                "snippet_line_failures": sum(len(check.get("snippet_line_failures") or []) for check in checks),
                "nested_source_snippet_failures": sum(
                    len(check.get("nested_source_snippet_failures") or []) for check in checks
                ),
                "partial_snippet_policy_failures": sum(len(check.get("partial_snippet_policy_failures") or []) for check in checks),
            },
            "checks": checks,
        }

    save_json_atomic(RAW_DIR / "agent_surface_source_grounding_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "agent_surface_source_grounding_validation.md", _render_report(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

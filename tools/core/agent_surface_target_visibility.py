from __future__ import annotations

import json
import re
from typing import Any


def sample_text(sample: dict[str, Any]) -> str:
    return str(sample.get("body") or sample.get("excerpt") or "")


def extract_target_file(sample: dict[str, Any]) -> str:
    text = sample_text(sample)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    structured_payloads = [payload] if isinstance(payload, dict) else []
    if structured_payloads and isinstance(payload.get("tool_result"), str):
        try:
            tool_result = json.loads(payload["tool_result"])
        except (TypeError, ValueError):
            tool_result = None
        if isinstance(tool_result, dict):
            structured_payloads.append(tool_result)
    for structured in structured_payloads:
        direct_target = structured.get("target_file")
        if isinstance(direct_target, str) and direct_target.strip():
            return direct_target.strip().replace("\\", "/").strip("/")
        target_files = structured.get("target_files")
        if isinstance(target_files, list) and target_files and isinstance(target_files[0], str):
            return target_files[0].strip().replace("\\", "/").strip("/")
        affected_scope = structured.get("affected_scope")
        affected_files = affected_scope.get("files") if isinstance(affected_scope, dict) else None
        if isinstance(affected_files, list) and affected_files and isinstance(affected_files[0], str):
            return affected_files[0].strip().replace("\\", "/").strip("/")

    patterns = [
        r"(?m)^\s*target_file:\s*\"?([^\"\n]+)\"?\s*$",
        r"\"target_file\":\s*\"([^\"]+)\"",
        r"(?m)^\s*target_files:\s*\[\s*\"([^\"]+)\"",
        r"(?m)^\s*target_files:\s*\n\s*-\s*\"?([^\"\n]+)\"?\s*$",
        r"(?m)^\s*file:\s*\"?([^\"\n]+)\"?\s*$",
        r"(?m)^\s*-\s*file:\s*\"?([^\"\n]+)\"?\s*$",
        r"\"file\":\s*\"([^\"]+)\"",
        r"(?m)^\s*target:\s*\"?([^\"\n]+)\"?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip().replace("\\", "/").strip("/")
    return ""


def extract_target_ref(sample: dict[str, Any]) -> str:
    text = sample_text(sample)
    patterns = [
        r"(?m)^\s*target_ref:\s*\"?([^\"\n]+)\"?\s*$",
        r"\"target_ref\":\s*\"([^\"]+)\"",
        r"(?m)^\s*target_refs:\s*\[\s*\"([^\"]+)\"",
        r"(?m)^\s*target_refs:\s*\n\s*-\s*\"?([^\"\n]+)\"?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def extract_named_path(sample: dict[str, Any], keys: list[str]) -> str:
    text = sample_text(sample)
    for key in keys:
        patterns = [
            rf"(?m)^\s*{re.escape(key)}:\s*\"?([^\"\n]+)\"?\s*$",
            rf"(?m)^\s*\"{re.escape(key)}\":\s*\"([^\"]+)\"",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip().replace("\\", "/").strip("/")
    return ""


def has_review_target(sample: dict[str, Any]) -> bool:
    return bool(
        extract_target_file(sample)
        or extract_target_ref(sample)
        or extract_named_path(sample, ["source_file"])
        or extract_named_path(sample, ["proposed_target_path", "target_path"])
    )


def _line_value(text: str, key: str) -> str:
    prefix = f"{key}:"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip().strip("\"'")
    return ""


def _has_explicit_empty_list(text: str, key: str) -> bool:
    lines = text.splitlines()
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        inline = stripped[len(prefix) :].strip()
        if inline == "[]":
            return True
        for following in lines[index + 1 :]:
            candidate = following.strip()
            if not candidate:
                continue
            return candidate == "[]"
    return False


def is_evidence_blocked_empty_result(sample: dict[str, Any]) -> bool:
    """Accept targetless output only when it proves a safe, actionable empty state."""
    text = sample_text(sample)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        target_proof = payload.get("target_proof")
        target_proof = target_proof if isinstance(target_proof, dict) else {}
        policy_boundary = payload.get("policy_boundary")
        policy_boundary = policy_boundary if isinstance(policy_boundary, dict) else {}
        required_action = payload.get("required_action") or target_proof.get("required_action")
        return (
            str(payload.get("status") or "") in {"BLOCKED", "INCOMPLETE_EVIDENCE"}
            and payload.get("items") == []
            and str(target_proof.get("verdict") or "") == "BLOCKED"
            and policy_boundary.get("mutation_allowed_by_this_packet") is False
            and bool(str(required_action or "").strip())
        )

    return (
        _line_value(text, "status") in {"BLOCKED", "INCOMPLETE_EVIDENCE"}
        and _line_value(text, "verdict") == "BLOCKED"
        and _has_explicit_empty_list(text, "items")
        and _line_value(text, "mutation_allowed_by_this_packet").lower() == "false"
        and bool(_line_value(text, "required_action"))
    )


def is_structured_precondition_block(
    sample: dict[str, Any],
    *,
    expected_tool: str = "",
) -> bool:
    """Recognize an exact fail-closed lifecycle response, not an arbitrary error."""
    text = sample_text(sample)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        tool_matches = not expected_tool or str(payload.get("tool") or "") == expected_tool
        return (
            str(payload.get("status") or "") in {"INVALID_CONTEXT", "INCOMPLETE_EVIDENCE"}
            and payload.get("blocking") is True
            and tool_matches
            and bool(str(payload.get("required_action") or "").strip())
            and bool(str(payload.get("claim_boundary") or "").strip())
        )

    tool_matches = not expected_tool or _line_value(text, "tool") == expected_tool
    return (
        _line_value(text, "status") in {"INVALID_CONTEXT", "INCOMPLETE_EVIDENCE"}
        and _line_value(text, "blocking").lower() == "true"
        and tool_matches
        and bool(_line_value(text, "required_action"))
        and bool(_line_value(text, "claim_boundary"))
    )


def is_successful_surgical_packet(sample: dict[str, Any]) -> bool:
    """Recognize target-bound surgical evidence separately from safe blocking."""
    text = sample_text(sample)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        return (
            str(payload.get("status") or "") in {"ACCEPTED", "PASS"}
            and payload.get("blocking") is not True
            and bool(str(payload.get("analysis_root") or "").strip())
            and bool(
                payload.get("agent_action_directives")
                or payload.get("inspect_first")
                or payload.get("target_files")
            )
        )
    return (
        text.startswith("# Repository Surgical Brief")
        and bool(_line_value(text, "analysis_root"))
        and "output/.raw" not in text
        and "debug_internal_refs:" not in text
    )


def allows_no_review_target(sample: dict[str, Any], policy: dict[str, Any]) -> bool:
    text = sample_text(sample)
    markers = policy.get("allowed_no_review_target_markers", [])
    return is_evidence_blocked_empty_result(sample) or any(
        str(marker).strip() and str(marker) in text for marker in markers
    )


def target_visibility_status(sample: dict[str, Any], review_target: str, policy: dict[str, Any]) -> str:
    if review_target:
        return "review_target_available"
    if allows_no_review_target(sample, policy):
        return "fail_closed_or_clean_no_direct_target"
    return "missing_review_target"

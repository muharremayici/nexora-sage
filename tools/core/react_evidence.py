from __future__ import annotations

import re
from typing import Any


CONFIDENCE_RANK = {
    "needs_runtime_proof": 0,
    "probable": 1,
    "likely": 2,
    "confirmed": 3,
}

EVIDENCE_KIND_TO_LADDER = {
    "static": "static_regex",
    "static_regex": "static_regex",
    "type_contract": "static_regex",
    "security": "static_regex",
    "hydration_contract": "static_regex",
    "guarded_context": "static_regex",
    "rsc_boundary": "static_regex",
    "atlas_feature": "atlas_feature",
    "ast_span": "ast_span",
    "typescript_compiler": "typescript_diagnostic",
    "bundle_stats": "build_artifact",
    "runtime_smoke_ready": "runtime_smoke_ready",
    "runtime_profile": "runtime_smoke",
    "runtime_smoke": "runtime_smoke",
    "needs_runtime_proof": "runtime_proof_required",
    "react_ecosystem_corroboration": "human_review",
    "human_review": "human_review",
}
EVIDENCE_LADDER_ORDER = [
    "static_regex",
    "atlas_feature",
    "ast_span",
    "typescript_diagnostic",
    "build_artifact",
    "runtime_smoke_ready",
    "runtime_smoke",
    "runtime_proof_required",
    "human_review",
]


def line_for_offset(content: str, offset: int | None, default: int = 1) -> int:
    if not isinstance(offset, int) or offset < 0:
        return default
    return content[:offset].count("\n") + 1


def first_pattern_line(content: str, pattern: re.Pattern[str], default: int = 1) -> int:
    match = pattern.search(content)
    if not match:
        return default
    return line_for_offset(content, match.start(), default)


def normalize_evidence_kinds(evidence_kinds: set[str] | list[str] | tuple[str, ...] | None) -> list[str]:
    if not evidence_kinds:
        return ["static_regex"]
    normalized = sorted({str(kind) for kind in evidence_kinds if str(kind or "").strip()})
    return normalized or ["static_regex"]


def evidence_ladder_from_kinds(evidence_kinds: set[str] | list[str] | tuple[str, ...] | None) -> list[str]:
    raw_steps: list[str] = []
    for kind in normalize_evidence_kinds(evidence_kinds):
        step = EVIDENCE_KIND_TO_LADDER.get(kind, kind)
        if step not in raw_steps:
            raw_steps.append(step)
    if "static_regex" not in raw_steps:
        raw_steps.insert(0, "static_regex")
    ladder = [step for step in EVIDENCE_LADDER_ORDER if step in raw_steps]
    ladder.extend(step for step in raw_steps if step not in ladder)
    return ladder


def confidence_from_ladder(score: int, evidence_ladder: list[str], current: str | None = None) -> str:
    if "runtime_proof_required" in evidence_ladder and "runtime_smoke" not in evidence_ladder:
        inferred = "needs_runtime_proof"
        return inferred
    if "runtime_smoke" in evidence_ladder:
        inferred = "confirmed" if score >= 8 else "likely"
    elif "typescript_diagnostic" in evidence_ladder or "ast_span" in evidence_ladder or "atlas_feature" in evidence_ladder:
        inferred = "likely" if score >= 6 else "probable"
    elif score >= 4:
        inferred = "probable"
    else:
        inferred = "needs_runtime_proof"

    if current and CONFIDENCE_RANK.get(current, -1) >= CONFIDENCE_RANK[inferred]:
        return current
    return inferred


def runtime_proof_status(evidence_ladder: list[str]) -> str:
    if "runtime_smoke" in evidence_ladder:
        return "runtime_confirmed"
    if "build_artifact" in evidence_ladder:
        return "build_correlated"
    if "runtime_smoke_ready" in evidence_ladder:
        return "runtime_smoke_ready"
    if "runtime_proof_required" in evidence_ladder:
        return "needs_runtime_proof"
    if "typescript_diagnostic" in evidence_ladder or "ast_span" in evidence_ladder or "atlas_feature" in evidence_ladder:
        return "static_correlated"
    return "needs_runtime_proof"


def attach_react_evidence_contract(
    item: dict[str, Any],
    *,
    evidence_kinds: set[str] | list[str] | tuple[str, ...] | None = None,
    line: int | None = None,
    current_confidence: str | None = None,
) -> dict[str, Any]:
    kinds = normalize_evidence_kinds(evidence_kinds or item.get("evidence_kinds") or ["static_regex"])
    ladder = evidence_ladder_from_kinds(kinds)
    score = int(item.get("score", 0) or 0)
    normalized_line = int(item.get("line") or line or 1)
    item["line"] = normalized_line
    item["evidence_kinds"] = kinds
    item["evidence_ladder"] = ladder
    item["evidence_spans"] = item.get("evidence_spans") or [
        {
            "file": item.get("file"),
            "line": normalized_line,
            **({"end_line": int(item.get("end_line"))} if item.get("end_line") else {}),
            **({"scope": str(item.get("evidence_scope"))} if item.get("evidence_scope") else {}),
            "source": ladder[-1],
        }
    ]
    item["runtime_proof_status"] = runtime_proof_status(ladder)
    item["confidence"] = confidence_from_ladder(score, ladder, current_confidence or item.get("confidence"))
    return item


def atlas_evidence_kinds(atlas_file: dict[str, Any] | None) -> set[str]:
    if not isinstance(atlas_file, dict):
        return set()
    kinds: set[str] = set()
    if atlas_file.get("features"):
        kinds.add("atlas_feature")
    for symbol in atlas_file.get("symbols") or []:
        if not isinstance(symbol, dict):
            continue
        if symbol.get("features"):
            kinds.add("atlas_feature")
        if symbol.get("start") or symbol.get("end") or symbol.get("source_lines"):
            kinds.add("ast_span")
    return kinds

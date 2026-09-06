from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.logger import logger
from tools.core.report_surface_limits import report_surface_limit


POLICY_PATH = CONFIG_DIR / "react_compiler_readiness_policy.json"
_POLICY_CACHE: dict[str, Any] | None = None


def _load_compiler_readiness_policy() -> dict[str, Any]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE
    policy = load_json_object_strict(POLICY_PATH, label="React compiler readiness policy")
    for key in ("compiler_dimensions", "blocking_risks", "review_risks"):
        values = policy.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            raise ValueError(f"React compiler readiness policy must define a non-empty string list: {key}")
    _POLICY_CACHE = {
        "policy_source": str(POLICY_PATH),
        "meta": policy.get("meta", {}),
        "compiler_dimensions": set(policy["compiler_dimensions"]),
        "blocking_risks": set(policy["blocking_risks"]),
        "review_risks": set(policy["review_risks"]),
    }
    return _POLICY_CACHE


def _findings_from_artifact(name: str) -> list[dict[str, Any]]:
    payload = load_json_file(RAW_DIR / f"{name}_full.json", {})
    if not isinstance(payload, dict) or not payload.get("findings"):
        payload = load_json_file(RAW_DIR / f"{name}.json", {})
    findings = payload.get("findings", []) if isinstance(payload, dict) else []
    return [item for item in findings if isinstance(item, dict)]


def _readiness_status(item: dict[str, Any]) -> str:
    policy = _load_compiler_readiness_policy()
    blocking_risks = policy["blocking_risks"]
    review_risks = policy["review_risks"]
    risk = str(item.get("risk") or "")
    tier = str(item.get("risk_tier") or "")
    lane = str(item.get("calibration_lane") or "")
    score = int(item.get("score", 0) or 0)
    proof = str(item.get("runtime_proof_status") or "")
    actionability = str(item.get("actionability") or "")

    if risk in blocking_risks and (tier == "high" or score >= 8 or lane == "act_now"):
        return "blocked"
    if actionability == "reference_only":
        return "observe"
    if risk in blocking_risks or risk in review_risks or tier == "medium" or proof == "needs_runtime_proof":
        return "review"
    return "ready"


def _recommendation_for(item: dict[str, Any], status: str) -> str:
    existing = str(item.get("recommended_action") or "").strip()
    if status == "blocked":
        return existing or "Resolve render purity, memo dependency, and provider identity risks before enabling React Compiler."
    if status == "review":
        return existing or "Review compiler-sensitive hooks, context values, and effects before enabling React Compiler broadly."
    return existing or "No compiler-blocking signal found for this finding; keep it in observation during rollout."


def _normalize_finding(item: dict[str, Any], source_artifact: str) -> dict[str, Any]:
    status = _readiness_status(item)
    return {
        "project": str(item.get("project") or ""),
        "file": str(item.get("file") or ""),
        "line": int(item.get("line") or 1),
        "source_artifact": source_artifact,
        "dimension": str(item.get("dimension") or ""),
        "risk": str(item.get("risk") or ""),
        "risk_tier": str(item.get("risk_tier") or "low"),
        "confidence": str(item.get("confidence") or "needs_runtime_proof"),
        "score": int(item.get("score", 0) or 0),
        "readiness_status": status,
        "evidence": str(item.get("evidence") or ""),
        "evidence_kinds": list(item.get("evidence_kinds") or []),
        "evidence_ladder": list(item.get("evidence_ladder") or []),
        "evidence_spans": list(item.get("evidence_spans") or []),
        "evidence_scope": str(item.get("evidence_scope") or ""),
        "runtime_proof_status": str(item.get("runtime_proof_status") or "needs_runtime_proof"),
        "source_context": str(item.get("source_context") or "production_source"),
        "actionability": str(item.get("actionability") or "production_actionable"),
        "recommended_action": _recommendation_for(item, status),
    }


def _compiler_relevant_findings() -> list[dict[str, Any]]:
    policy = _load_compiler_readiness_policy()
    compiler_dimensions = policy["compiler_dimensions"]
    collected: list[dict[str, Any]] = []
    for artifact in ("react_runtime_intelligence", "react_ecosystem_analysis"):
        for item in _findings_from_artifact(artifact):
            if str(item.get("dimension") or "") in compiler_dimensions:
                collected.append(_normalize_finding(item, artifact))
    collected.sort(
        key=lambda item: (
            {"blocked": 0, "review": 1, "observe": 2, "ready": 3}.get(item["readiness_status"], 4),
            -int(item.get("score", 0) or 0),
            item.get("project", ""),
            item.get("file", ""),
        )
    )
    return collected


def _summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    status_counter = Counter(item["readiness_status"] for item in findings)
    dimension_counter = Counter(item["dimension"] for item in findings)
    project_counter = Counter(item["project"] for item in findings)
    proof_counter = Counter(item["runtime_proof_status"] for item in findings)
    files_by_project: dict[str, set[str]] = defaultdict(set)
    for item in findings:
        files_by_project[item["project"]].add(item["file"])
    return {
        "findings": len(findings),
        "files_analyzed": sum(len(files) for files in files_by_project.values()),
        "ready": status_counter.get("ready", 0),
        "observe": status_counter.get("observe", 0),
        "review": status_counter.get("review", 0),
        "blocked": status_counter.get("blocked", 0),
        "status_counts": dict(status_counter),
        "dimension_counts": dict(dimension_counter),
        "runtime_proof_status_counts": dict(proof_counter),
        "by_project": dict(project_counter),
    }


def run_react_compiler_readiness() -> dict[str, Any]:
    logger.info("Building React Compiler readiness artifact...")
    policy = _load_compiler_readiness_policy()
    findings = _compiler_relevant_findings()
    primary_limit = report_surface_limit("react_compiler_readiness.primary_findings")
    primary_findings = findings[:primary_limit]
    summary = _summary(findings)
    summary.update(
        {
            "primary_findings": len(primary_findings),
            "truncated_in_primary_report": len(findings) > len(primary_findings),
            "full_artifact": "react_compiler_readiness_full.json",
        }
    )
    full_payload = {
        "meta": {"kind": "react_compiler_readiness_full", "version": "v1"},
        "summary": dict(summary, primary_findings=len(findings), truncated_in_primary_report=False),
        "policy": {
            "source": policy["policy_source"],
            "meta": policy["meta"],
            "compiler_dimensions": sorted(policy["compiler_dimensions"]),
            "blocking_risks": sorted(policy["blocking_risks"]),
            "review_risks": sorted(policy["review_risks"]),
        },
        "findings": findings,
    }
    payload = {
        "meta": {"kind": "react_compiler_readiness", "version": "v1"},
        "summary": summary,
        "policy": {
            "source": policy["policy_source"],
            "meta": policy["meta"],
            "compiler_dimensions": sorted(policy["compiler_dimensions"]),
            "blocking_risks": sorted(policy["blocking_risks"]),
            "review_risks": sorted(policy["review_risks"]),
        },
        "findings": primary_findings,
    }
    save_json_atomic(RAW_DIR / "react_compiler_readiness_full.json", full_payload)
    save_json_atomic(RAW_DIR / "react_compiler_readiness.json", payload)

    summary = payload["summary"]
    lines = [
        "# React Compiler Readiness",
        "",
        "Compiler-sensitive React findings distilled from runtime and ecosystem intelligence.",
        "",
        f"- findings: `{summary['findings']}`",
        f"- blocked: `{summary['blocked']}`",
        f"- review: `{summary['review']}`",
        f"- observe: `{summary.get('observe', 0)}`",
        f"- ready: `{summary['ready']}`",
        f"- runtime proof: `{summary['runtime_proof_status_counts']}`",
        f"- primary findings: `{summary['primary_findings']}`",
        f"- truncated primary report: `{summary['truncated_in_primary_report']}`",
        f"- full artifact: `{summary['full_artifact']}`",
        "",
        "## Priority Findings",
    ]
    for item in findings[:80]:
        lines.append(
            f"- `{item['readiness_status']}` | {item['project']}::{item['file']}:{item['line']} | "
            f"`{item['dimension']}` | `{item['risk']}` | score `{item['score']}`"
        )
    save_text_atomic(REPORTS_DIR / "react_compiler_readiness.md", "\n".join(lines))
    logger.info("React Compiler readiness artifacts written.")
    return payload


if __name__ == "__main__":
    run_react_compiler_readiness()

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.artifact_freshness_contract import evaluate_artifact_freshness_contract, load_artifact_freshness_contract
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any, *, severity: str = "error") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "severity": severity, "details": details}


def _requires_global_currentness(chain: dict[str, Any]) -> bool:
    return str(chain.get("currentness_requirement") or "global_validation") == "global_validation"


def build_validation() -> dict[str, Any]:
    contract = load_artifact_freshness_contract()
    default_policy = contract.get("default_policy", {}) if isinstance(contract.get("default_policy"), dict) else {}
    chains = [row for row in contract.get("chains", []) if isinstance(row, dict)] if isinstance(contract, dict) else []
    evaluation = evaluate_artifact_freshness_contract(RAW_DIR)
    checks: list[dict[str, Any]] = []
    chain_ids = [str(row.get("id") or "") for row in chains]
    chains_by_id = {str(row.get("id") or ""): row for row in chains}
    checks.append(_check("contract_has_default_policy", isinstance(contract.get("default_policy"), dict), contract.get("default_policy")))
    checks.append(
        _check(
            "contract_bounds_auto_refresh_attempts",
            int(default_policy.get("max_auto_refresh_attempts_per_request") or 0) == 1
            and default_policy.get("recursive_refresh_allowed") is False
            and "one attempt" in str(default_policy.get("auto_refresh_rule") or ""),
            default_policy,
        )
    )
    checks.append(_check("contract_has_unique_chain_ids", len(chain_ids) == len(set(chain_ids)) and bool(chain_ids), chain_ids))
    for chain in chains:
        chain_id = str(chain.get("id") or "")
        ordered_artifacts = chain.get("ordered_artifacts") if isinstance(chain.get("ordered_artifacts"), list) else []
        required_artifacts = chain.get("required_artifacts") if isinstance(chain.get("required_artifacts"), list) else []
        artifacts = [*ordered_artifacts, *required_artifacts]
        refresh_plan = chain.get("refresh_plan") if isinstance(chain.get("refresh_plan"), list) else []
        consumers = chain.get("consumer_surfaces") if isinstance(chain.get("consumer_surfaces"), list) else []
        checks.extend(
            [
                _check(
                    f"{chain_id}:has_artifact_scope",
                    bool(artifacts),
                    {"ordered_artifacts": ordered_artifacts, "required_artifacts": required_artifacts},
                ),
                _check(f"{chain_id}:has_refresh_plan", bool(refresh_plan), refresh_plan),
                _check(f"{chain_id}:has_consumer_surfaces", bool(consumers), consumers),
                _check(f"{chain_id}:has_stale_behavior", bool(chain.get("stale_behavior")), chain.get("stale_behavior")),
                _check(f"{chain_id}:has_agent_message", bool(chain.get("agent_message")), chain.get("agent_message")),
                _check(
                    f"{chain_id}:has_valid_currentness_requirement",
                    str(chain.get("currentness_requirement") or "global_validation")
                    in {"global_validation", "consumer_activation"},
                    str(chain.get("currentness_requirement") or "global_validation"),
                ),
            ]
        )
        if chain_id == "agent_surface_action_chain":
            checks.append(
                _check(
                    "agent_surface_action_chain:quality_gate_ordered_after_audit",
                    "audit_report" in ordered_artifacts
                    and "quality_gate" in ordered_artifacts
                    and ordered_artifacts.index("quality_gate") > ordered_artifacts.index("audit_report"),
                    ordered_artifacts,
                )
            )
    for row in evaluation.get("chains", []):
        if not isinstance(row, dict):
            continue
        chain_id = str(row.get("id") or "unknown")
        chain = chains_by_id.get(chain_id, {})
        requires_global_currentness = _requires_global_currentness(chain)
        # Missing/stale chains are hard failures; JSON-shadow-only chains are
        # warnings because clean installs may not have SQLite state yet. A
        # consumer-activated chain may be stale while idle, provided its
        # consumer fails closed and refreshes the exact operational scope.
        checks.append(
            _check(
                f"{chain_id}:current_chain_not_stale",
                row.get("status") != "FAIL" or not requires_global_currentness,
                {
                    "status": row.get("status"),
                    "currentness_requirement": str(
                        chain.get("currentness_requirement") or "global_validation"
                    ),
                    "missing": row.get("missing_artifacts"),
                    "stale_edges": row.get("stale_edges"),
                    "refresh_plan": row.get("refresh_plan"),
                },
            )
        )
        checks.append(
            _check(
                f"{chain_id}:sqlite_first_or_visible_shadow_warning",
                row.get("status") in {"PASS", "WARN"},
                {
                    "status": row.get("status"),
                    "json_shadow_only_artifacts": row.get("json_shadow_only_artifacts"),
                },
                severity="warning",
            )
        )
    failures = [row for row in checks if not row.get("passed") and row.get("severity") == "error"]
    warnings = [row for row in checks if not row.get("passed") and row.get("severity") == "warning"]
    return {
        "meta": {
            "kind": "artifact_freshness_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_artifact_freshness_contract",
        },
        "summary": {
            "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
            "checks": len(checks),
            "passed": sum(1 for row in checks if row.get("passed")),
            "failed": len(failures),
            "warnings": len(warnings),
            "chains": len(chains),
        },
        "checks": checks,
        "evaluation": evaluation,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Artifact Freshness Contract Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('checks')}`",
        f"- failed: `{summary.get('failed')}`",
        f"- warnings: `{summary.get('warnings')}`",
        "",
        "| Chain | Status | Stale behavior | Refresh plan |",
        "|---|---|---|---|",
    ]
    for chain in (payload.get("evaluation", {}) or {}).get("chains", []):
        if not isinstance(chain, dict):
            continue
        plan = "<br>".join(f"`{item}`" for item in chain.get("refresh_plan", [])[:4])
        lines.append(f"| `{chain.get('id')}` | `{chain.get('status')}` | `{chain.get('stale_behavior')}` | {plan} |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_validation()
    save_json_atomic(RAW_DIR / "artifact_freshness_contract_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "artifact_freshness_contract_validation.md", render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

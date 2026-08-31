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
from tools.engines.react_immutability_purity_cage import analyze_immutability_file


ENGINE_PATH = CODE_MAPS_DIR / "tools" / "engines" / "react_immutability_purity_cage.py"
POLICY_PATH = CONFIG_DIR / "react_immutability_policy.json"


def _check(name: str, passed: bool, details: str, evidence: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details, "evidence": evidence}


def _load_policy() -> dict[str, Any]:
    policy = load_json_file(POLICY_PATH, {})
    return policy if isinstance(policy, dict) else {}


def run_validation() -> dict[str, Any]:
    policy = _load_policy()
    engine_text = ENGINE_PATH.read_text(encoding="utf-8", errors="replace")
    mutating_methods = [str(item) for item in policy.get("mutating_methods", []) if str(item).strip()]
    hook_names = [str(item) for item in policy.get("state_hook_names", []) if str(item).strip()]
    protected_roots = [str(item) for item in policy.get("protected_roots", []) if str(item).strip()]
    risk_catalog = policy.get("risk_catalog", {}) if isinstance(policy.get("risk_catalog"), dict) else {}

    policy_owned_tokens = sorted(set(mutating_methods + hook_names + protected_roots))
    engine_token_hits = [
        token for token in policy_owned_tokens
        if f'"{token}"' in engine_text or f"'{token}'" in engine_text
    ]

    sample = """
import React, { useState } from 'react';

export function Demo(props) {
  const [items, setItems] = useState([]);
  items.push('x');
  items[0] = 'y';
  props.title = 'changed';
  return <button onClick={() => setItems([...items])}>Save</button>;
}
"""
    sample_result = analyze_immutability_file("FIXTURE", "src/Demo.tsx", sample, policy) or {}
    sample_findings = sample_result.get("findings", []) if isinstance(sample_result, dict) else []
    sample_risks = sorted({str(item.get("risk_id")) for item in sample_findings if isinstance(item, dict)})

    read_only_sample = """
import React from 'react';

export function ReadOnlyProps(props) {
  const resolvedTitle = props.title || (props.type === 'item' ? 'Item' : 'Other');
  return <span>{resolvedTitle}</span>;
}

export class ReadOnlyClassProps extends React.Component {
  render() {
    return this.props.variant === 'card' ? <div /> : null;
  }
}
"""
    read_only_result = analyze_immutability_file("FIXTURE", "src/ReadOnlyProps.tsx", read_only_sample, policy) or {}
    read_only_findings = read_only_result.get("findings", []) if isinstance(read_only_result, dict) else []

    checks = [
        _check(
            "immutability_policy_file_exists",
            POLICY_PATH.exists() and policy.get("meta", {}).get("kind") == "react_immutability_policy",
            "The immutability cage must be policy-backed.",
            {"path": str(POLICY_PATH), "meta": policy.get("meta")},
        ),
        _check(
            "immutability_policy_declares_detection_surfaces",
            bool(mutating_methods) and bool(hook_names) and bool(protected_roots) and bool(risk_catalog),
            "State hooks, mutating methods, protected roots and risk catalog must be configured.",
            {
                "mutating_methods": mutating_methods,
                "state_hook_names": hook_names,
                "protected_roots": protected_roots,
                "risk_catalog_keys": sorted(risk_catalog),
            },
        ),
        _check(
            "immutability_engine_has_no_policy_owned_literal_tokens",
            not engine_token_hits,
            "Mutating methods, hook names and protected roots must live in config/react_immutability_policy.json.",
            engine_token_hits,
        ),
        _check(
            "immutability_fixture_detects_state_and_props_mutation",
            {"state_variable_mutating_method", "state_variable_direct_assignment", "props_direct_assignment"}.issubset(set(sample_risks)),
            "The minimal fixture should catch state mutating methods, state assignment and props assignment.",
            sample_risks,
        ),
        _check(
            "immutability_fixture_does_not_flag_props_reads_or_equality_checks",
            not read_only_findings,
            "Read-only props access and equality comparisons must not be treated as mutation.",
            read_only_findings,
        ),
    ]

    payload = {
        "meta": {"kind": "react_immutability_purity_cage_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "react_immutability_purity_cage_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "react_immutability_purity_cage_validation.md", _render_report(payload))
    return payload


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React Immutability And Purity Cage Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- passed: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = str(check.get("details") or "").replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | {details} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

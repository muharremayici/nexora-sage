from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR_FOR_IMPORT))

from tools.core.artifact_validator import ensure_valid_payload
from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.contextos_mcp import render_active_signals
from tools.core.json_io import load_json_file
from tools.core.watchdog_runtime_contract import watchdog_artifact_path


SIGNALS_PATH = RAW_DIR / "signals.json"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _projection_preserves_scope_boundary(
    rendered_json: dict[str, Any],
    *,
    current_change_scope: str,
    current_turn_claim: str,
) -> bool:
    return (
        rendered_json.get("current_change_scope") == current_change_scope
        and rendered_json.get("current_turn_claim") == current_turn_claim
    )


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if not SIGNALS_PATH.exists():
        payload = {
            "meta": {"kind": "contextos_signal_validation", "version": "v1"},
            "summary": {
                "total_checks": 1,
                "passed_checks": 1,
                "failed_checks": 0,
                "status": "PASS_NO_ACTIVE_SIGNALS",
            },
            "checks": [
                _check(
                    "missing_signals_artifact_is_safe_idle_state",
                    True,
                    "No ContextOS signal artifact exists yet; MCP active-signal surfaces must fail closed and request a concrete target before edits.",
                )
            ],
        }
        save_json_atomic(RAW_DIR / "contextos_signal_validation.json", payload)
        return payload

    signals = load_json_file(SIGNALS_PATH, {})
    try:
        ensure_valid_payload("signals", signals)
        schema_errors: list[str] = []
    except Exception as exc:
        schema_errors = [str(exc)]
    checks.append(_check("signals_schema_valid", not schema_errors, schema_errors or "schema ok"))

    active = signals.get("active_signals", []) if isinstance(signals.get("active_signals"), list) else []
    summary_payload = signals.get("summary", {}) if isinstance(signals.get("summary"), dict) else {}
    input_evidence = signals.get("input_evidence", {}) if isinstance(signals.get("input_evidence"), dict) else {}
    unavailable_inputs = summary_payload.get("unavailable_inputs", [])
    deferred_inputs = summary_payload.get("deferred_inputs", [])
    evidence_status = str(summary_payload.get("input_evidence_status") or "")
    source_mode = str((signals.get("meta") or {}).get("source_mode") or "")
    current_change_scope = str((signals.get("meta") or {}).get("current_change_scope") or "")
    current_turn_claim = str((signals.get("meta") or {}).get("current_turn_claim") or "")
    signal_origin = str((signals.get("meta") or {}).get("signal_origin") or "")
    claim_pair_valid = (
        (source_mode == "direct_pipeline_scope" and current_change_scope == "bounded" and current_turn_claim == "supported")
        or (source_mode != "direct_pipeline_scope" and current_change_scope == "unknown" and current_turn_claim == "not_established")
    )
    checks.append(
        _check(
            "current_turn_claim_requires_bounded_direct_scope",
            claim_pair_valid and bool(signal_origin),
            {
                "source_mode": source_mode,
                "current_change_scope": current_change_scope,
                "current_turn_claim": current_turn_claim,
                "signal_origin": signal_origin,
            },
        )
    )
    evidence_rows_match = all(
        isinstance(row, dict)
        and row.get("status") in {"available", "unavailable", "deferred"}
        and row.get("shape_status") in {"valid", "invalid", "not_evaluated"}
        for row in input_evidence.values()
    )
    if source_mode == "direct_pipeline_scope":
        expected_evidence = {
            "atlas",
            "watchdog_audit_report",
            "circular_deps",
            "blast_radius",
            "change_scope",
        }
        mode_evidence_valid = (
            set(input_evidence) == expected_evidence
            and set(deferred_inputs) == {"circular_deps", "blast_radius"}
            and all(input_evidence[name].get("status") == "deferred" for name in deferred_inputs)
        )
    else:
        expected_evidence = {"circular_deps", "blast_radius", "audit_report", "change_scope"}
        mode_evidence_valid = set(input_evidence) == expected_evidence and not deferred_inputs
    checks.append(
        _check(
            "signals_publish_quant_input_evidence",
            mode_evidence_valid and evidence_rows_match,
            input_evidence,
        )
    )
    status_matches_unavailable = (
        evidence_status == "PASS" and not unavailable_inputs
    ) or (
        evidence_status == "PARTIAL" and bool(unavailable_inputs)
    )
    checks.append(
        _check(
            "signals_input_evidence_status_matches_unavailable_inputs",
            status_matches_unavailable,
            {"status": evidence_status, "unavailable_inputs": unavailable_inputs},
        )
    )
    false_low_risk = [
        item.get("relative_path")
        for item in active
        if isinstance(item, dict)
        and evidence_status != "PASS"
        and (item.get("signal_actionability") or {}).get("priority") in {"low", "medium"}
    ]
    checks.append(
        _check(
            "partial_quant_evidence_cannot_emit_low_risk_actionability",
            not false_low_risk,
            false_low_risk or "partial evidence is fail-closed for actionability",
        )
    )
    if source_mode == "direct_pipeline_scope":
        graph_path = watchdog_artifact_path("graph_advisories")
        graph_payload = load_json_file(graph_path, {})
        try:
            ensure_valid_payload("watchdog_graph_advisories", graph_payload)
            graph_schema_errors: list[str] = []
        except Exception as exc:
            graph_schema_errors = [str(exc)]
        checks.append(
            _check(
                "watchdog_graph_advisories_schema_valid",
                not graph_schema_errors,
                graph_schema_errors or "schema ok",
            )
        )
        graph_rows = {
            str(row.get("target_ref") or ""): row
            for row in graph_payload.get("advisories", [])
            if isinstance(row, dict) and row.get("target_ref")
        }
        scoped_contract_failures = []
        for item in active:
            if not isinstance(item, dict):
                continue
            target_ref = str(item.get("target_ref") or "")
            graph_row = graph_rows.get(target_ref, {})
            impact = graph_row.get("impact_advisory") if isinstance(graph_row, dict) else None
            cycle = graph_row.get("cycle_membership") if isinstance(graph_row, dict) else None
            expected_cycles = []
            if isinstance(cycle, dict) and cycle.get("status") == "member":
                expected_cycles = [list(cycle.get("witness_chain") or [])]
            mismatches = []
            if not graph_row:
                mismatches.append("missing_graph_advisory")
            if item.get("impact_score_status") != "scoped_dependency_lower_bound":
                mismatches.append("impact_score_status")
            if not isinstance(impact, dict) or item.get("impact_score") != impact.get("dependency_score"):
                mismatches.append("impact_score")
            if item.get("circular_cycles_status") != "scoped_scc_membership":
                mismatches.append("circular_cycles_status")
            if item.get("circular_cycles") != expected_cycles:
                mismatches.append("circular_cycles")
            if item.get("risk_claim_boundary") != "scoped_dependency_scc_witness_and_active_violation_context":
                mismatches.append("risk_claim_boundary")
            if mismatches:
                scoped_contract_failures.append({"target_ref": target_ref, "mismatches": mismatches})
        graph_summary = graph_payload.get("summary") if isinstance(graph_payload.get("summary"), dict) else {}
        if graph_summary.get("full_repository_claim") is not False:
            scoped_contract_failures.append({"summary": "full_repository_claim_must_be_false"})
        if graph_summary.get("canonical_artifacts_overwritten") is not False:
            scoped_contract_failures.append({"summary": "canonical_overwrite_must_be_false"})
        checks.append(
            _check(
                "watchdog_signals_match_scoped_graph_claim_boundaries",
                not scoped_contract_failures,
                scoped_contract_failures or "scoped SCC and dependency lower-bound projections match their source artifact",
            )
        )
        canonical_deferred = (
            set(deferred_inputs) == {"circular_deps", "blast_radius"}
            and all(input_evidence.get(name, {}).get("status") == "deferred" for name in deferred_inputs)
        )
        checks.append(
            _check(
                "watchdog_scoped_graph_never_impersonates_canonical_full_proof",
                canonical_deferred,
                {"deferred_inputs": deferred_inputs, "input_evidence": input_evidence},
            )
        )
    noisy_suffixes = {".zip", ".rar", ".7z", ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".pdf"}
    noisy = [
        item.get("relative_path")
        for item in active
        if isinstance(item, dict) and Path(str(item.get("relative_path", ""))).suffix.lower() in noisy_suffixes
    ]
    checks.append(_check("signals_exclude_binary_archive_noise", not noisy, noisy[:25] or "no archive/binary signal paths"))

    invalid_kind = [
        item.get("relative_path")
        for item in active
        if isinstance(item, dict) and item.get("signal_kind") not in {"source", "config"}
    ]
    checks.append(_check("signals_are_source_or_config_only", not invalid_kind, invalid_kind[:25] or "all signals are source/config"))

    missing_breadcrumbs = [
        item.get("relative_path")
        for item in active
        if isinstance(item, dict) and item.get("signal_kind") == "source" and not item.get("reasoning_breadcrumbs")
    ]
    checks.append(
        _check(
            "signals_include_reasoning_breadcrumbs_for_source_focus",
            not missing_breadcrumbs,
            missing_breadcrumbs[:25] or "source focus signals explain why they are hot",
        )
    )

    malformed_halo = [
        item.get("relative_path")
        for item in active
        if isinstance(item, dict)
        and any(not isinstance(halo, dict) or not halo.get("node_key") for halo in (item.get("focus_halo") or []))
    ]
    checks.append(
        _check(
            "signals_focus_halo_is_structured",
            not malformed_halo,
            malformed_halo[:25] or "halo entries are structured",
        )
    )
    missing_relative_path = [
        item.get("node_key")
        for item in active
        if isinstance(item, dict) and not str(item.get("relative_path") or "").strip()
    ]
    checks.append(
        _check(
            "signals_include_repo_relative_paths_for_agent_navigation",
            not missing_relative_path,
            missing_relative_path[:25] or "all active signals carry repo-relative paths",
        )
    )
    rendered_summary = render_active_signals(
        signals,
        output_format="markdown",
        include_bodies=False,
        max_files=8,
        max_chars_per_file=0,
        scope="summary",
        resolve_absolute_path=lambda rel_path, project_key: ROOT / str(rel_path or ""),
    )
    rendered_json = json.loads(
        render_active_signals(
            signals,
            output_format="json",
            include_bodies=False,
            max_files=8,
            max_chars_per_file=0,
            scope="full",
            resolve_absolute_path=lambda rel_path, project_key: ROOT / str(rel_path or ""),
        )
    )
    rendered_full = render_active_signals(
        signals,
        output_format="markdown",
        include_bodies=False,
        max_files=8,
        max_chars_per_file=0,
        scope="full",
        resolve_absolute_path=lambda rel_path, project_key: ROOT / str(rel_path or ""),
    )
    checks.append(
        _check(
            "agent_projection_preserves_current_change_scope_boundary",
            _projection_preserves_scope_boundary(
                rendered_json,
                current_change_scope=current_change_scope,
                current_turn_claim=current_turn_claim,
            ),
            {
                "producer_current_change_scope": current_change_scope,
                "producer_current_turn_claim": current_turn_claim,
                "projected_current_change_scope": rendered_json.get("current_change_scope"),
                "projected_current_turn_claim": rendered_json.get("current_turn_claim"),
                "human_disclosure_regression": "tools/tests/test_scoped_graph_projection.py",
            },
        )
    )
    expected_target_refs = [
        f"{str(item.get('node_key') or 'MAIN').split('::', 1)[0]}::{str(item.get('relative_path') or '').replace(chr(92), '/')}"
        for item in active[:8]
        if isinstance(item, dict) and str(item.get("relative_path") or "").strip()
    ]
    missing_rendered_refs = [
        ref
        for ref in expected_target_refs
        if f'"{ref}"' not in rendered_summary
    ]
    checks.append(
        _check(
            "signals_summary_renders_target_refs_from_repo_relative_paths",
            not missing_rendered_refs,
            missing_rendered_refs[:25] or "rendered target_refs use repo-relative paths",
        )
    )
    qualifier_fields = {
        "impact_score_status",
        "direct_dependents_omitted",
        "transitive_dependents_omitted",
        "signal_actionability",
        "active_violations_omitted",
        "circular_cycles_omitted",
        "circular_cycles_status",
        "risk_claim_boundary",
    }
    rendered_by_ref = {
        str(item.get("target_ref") or ""): item
        for item in rendered_json.get("active_signals", [])
        if isinstance(item, dict)
    }
    qualifier_transport_failures = []
    for item in active[:8]:
        if not isinstance(item, dict):
            continue
        target_ref = str(item.get("target_ref") or "")
        projected = rendered_by_ref.get(target_ref, {})
        missing = sorted(field for field in qualifier_fields if projected.get(field) != item.get(field))
        if not target_ref or missing:
            qualifier_transport_failures.append({"target_ref": target_ref, "mismatched_fields": missing})
    checks.append(
        _check(
            "agent_projection_preserves_signal_truth_qualifiers",
            not qualifier_transport_failures,
            qualifier_transport_failures or "all bounded signal qualifiers survive the MCP projection",
        )
    )
    deferred_cycle_signals = [
        item
        for item in active[:8]
        if isinstance(item, dict) and item.get("circular_cycles_status") == "deferred_broad_proof"
    ]
    checks.append(
        _check(
            "deferred_cycle_proof_never_renders_as_cycle_absence",
            not deferred_cycle_signals
            or (
                "Circular Dependency Proof:** deferred" in rendered_full
                and "Circular Dependency Cycles:** None" not in rendered_full
            ),
            "deferred broad proof remains explicit on the detailed agent surface",
        )
    )

    oversized = SIGNALS_PATH.stat().st_size > 750_000
    checks.append(
        _check(
            "signals_artifact_is_summary_sized",
            not oversized,
            f"size={SIGNALS_PATH.stat().st_size} bytes threshold<=750000",
        )
    )

    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check["passed"]),
        "failed_checks": sum(1 for check in checks if not check["passed"]),
    }
    payload = {
        "meta": {"kind": "contextos_signal_validation", "version": "v1"},
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "contextos_signal_validation.json", payload)
    lines = [
        "# ContextOS Signal Validation",
        "",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {escaped_details} |")
    save_text_atomic(REPORTS_DIR / "contextos_signal_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

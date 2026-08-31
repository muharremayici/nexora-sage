from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.artifact_validator import validate_payload
from tools.core.external_target_retention import DEFAULT_KEEP_PER_PREFIX, prune_generated_external_target_fixtures
from tools.core.operational_limits import external_target_smoke_timeout_seconds
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.mcp import server as mcp_server


def _safe_prefix(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    before_default_atlas_mtime = (RAW_DIR / "atlas.json").stat().st_mtime if (RAW_DIR / "atlas.json").exists() else None

    with tempfile.TemporaryDirectory(prefix="nexora_external_target_smoke_") as tmp:
        target_root = Path(tmp)
        (target_root / "pkg").mkdir()
        (target_root / "pkg" / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        (target_root / "app.py").write_text("from pkg.service import run\nprint(run())\n", encoding="utf-8")

        env = dict(os.environ)
        env["CODEMAPS_TARGET_ROOT"] = str(target_root)
        proc, _duration = run_observed_subprocess(
            [sys.executable, "-m", "tools.engines.generate_atlas"],
            cwd=CODE_MAPS_DIR,
            env=env,
            label="external_target_generate_atlas",
            timeout=external_target_smoke_timeout_seconds(),
            log=lambda message: print(f"[external-target-smoke] {message}", flush=True),
        )
        checks.append(
            {
                "name": "external_target_atlas_command",
                "passed": proc.returncode == 0,
                "details": {"returncode": proc.returncode, "stderr": (proc.stderr or "")[-500:]},
            }
        )

        prefix = _safe_prefix(target_root.name)
        candidates = sorted((CODE_MAPS_DIR / "output" / "external_targets").glob(f"{prefix}_*/.raw/atlas.json"))
        atlas_path = candidates[-1] if candidates else None
        atlas = json.loads(atlas_path.read_text(encoding="utf-8")) if atlas_path and atlas_path.exists() else {}
        files = atlas.get("MAIN", {}).get("files", {})
        checks.append(
            {
                "name": "external_target_output_is_target_scoped",
                "passed": bool(atlas_path and "app.py" in files and "pkg/service.py" in files.get("app.py", {}).get("imports", [])),
                "details": {"atlas_path": str(atlas_path) if atlas_path else None},
            }
        )
        app_meta = files.get("app.py", {}) if isinstance(files, dict) else {}
        checks.append(
            {
                "name": "external_target_atlas_files_carry_canonical_agent_paths",
                "passed": isinstance(app_meta, dict)
                and app_meta.get("project_key") == "MAIN"
                and app_meta.get("atlas_rel_path") == "app.py"
                and app_meta.get("repo_relative_path") == "app.py"
                and app_meta.get("target_ref") == "MAIN::app.py",
                "details": {"app_meta": {key: app_meta.get(key) for key in ("project_key", "atlas_rel_path", "repo_relative_path", "target_ref")} if isinstance(app_meta, dict) else app_meta},
            }
        )
        checks.append(
            {
                "name": "external_target_atlas_schema_valid",
                "passed": not validate_payload("atlas", atlas) if atlas else False,
                "details": {"project_keys": sorted(atlas.keys()) if isinstance(atlas, dict) else []},
            }
        )
        surgical_brief = mcp_server.get_surgical_operation_packet(max_signals=3, target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_surgical_packet_reads_isolated_output",
                "passed": surgical_brief.startswith("# Repository Surgical Brief")
                and "Invalid external target root" not in surgical_brief
                and "output/.raw" not in surgical_brief
                and "SOVEREIGN_ELITE" not in surgical_brief,
                "details": {"brief_chars": len(surgical_brief), "artifact_root": str(atlas_path.parent) if atlas_path else None},
            }
        )
        invalid_brief = mcp_server.get_surgical_operation_packet(max_signals=3, target_root=str(target_root / "__missing__"))
        checks.append(
            {
                "name": "external_target_mcp_surgical_packet_fails_closed_for_invalid_root",
                "passed": invalid_brief.startswith("# Invalid External Target")
                and "invalid_external_target_root" in invalid_brief
                and "Do not fall back to the current SAGE workspace" in invalid_brief,
                "details": {"response": invalid_brief[:200]},
            }
        )
        inspection_brief = mcp_server.inspect_file("app.py", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_inspect_file_reads_isolated_output",
                "passed": inspection_brief.startswith("# Target Inspection Brief")
                and '"app.py"' in inspection_brief
                and "target_files:" in inspection_brief
                and "atlas_node" not in inspection_brief
                and "SOVEREIGN_ELITE" not in inspection_brief,
                "details": {"brief_chars": len(inspection_brief)},
            }
        )
        invalid_inspection = mcp_server.inspect_file("app.py", target_root=str(target_root / "__missing__"))
        checks.append(
            {
                "name": "external_target_mcp_inspect_file_fails_closed_for_invalid_root",
                "passed": invalid_inspection.startswith("# Invalid External Target")
                and "invalid_external_target_root" in invalid_inspection
                and "Do not fall back to the current SAGE workspace" in invalid_inspection,
                "details": {"response": invalid_inspection[:200]},
            }
        )
        missing_inspection = mcp_server.inspect_file("src/App.tsx", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_inspect_file_reports_missing_target",
                "passed": "target_not_found_in_current_artifacts" in missing_inspection
                and "run/refresh external target analysis" in missing_inspection,
                "details": {"brief_chars": len(missing_inspection)},
            }
        )
        search_result = mcp_server.search_symbols("service", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_search_symbols_reads_isolated_output",
                "passed": search_result.startswith("# Target Search Brief")
                and "pkg/service.py" in search_result
                and "atlas_node" not in search_result
                and "Invalid external target root" not in search_result,
                "details": {"response": search_result[:500]},
            }
        )
        surgical_context = mcp_server.get_surgical_context("service", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_surgical_context_reads_isolated_atlas",
                "passed": surgical_context.startswith("# Surgical Context Brief")
                and "pkg/service.py" in surgical_context
                and "atlas_node" not in surgical_context
                and "SOVEREIGN_ELITE" not in surgical_context,
                "details": {"response": surgical_context[:500]},
            }
        )
        impact_result = mcp_server.get_impact_radius("app.py", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_impact_radius_uses_isolated_atlas_fallback",
                "passed": impact_result.startswith("# Impact Radius Brief")
                and (
                    "dependency_graph_source: \"atlas_imports_fallback\"" in impact_result
                    or 'dependency_graph_source: "sqlite_dependencies"' in impact_result
                )
                and "analysis_root:" in impact_result
                and target_root.name in impact_result
                and "SAGE source workspace" not in impact_result,
                "details": {"response": impact_result[:500]},
            }
        )
        test_impact = mcp_server.get_test_impact("app.py", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_test_impact_uses_isolated_atlas",
                "passed": test_impact.startswith("# Test Impact Brief")
                and "analysis_root:" in test_impact
                and 'target_project: "MAIN"' in test_impact
                and 'target_file: "app.py"' in test_impact
                and 'target_ref: "MAIN::app.py"' in test_impact
                and "atlas_node" not in test_impact
                and "SOVEREIGN_ELITE" not in test_impact
                and "output/.raw" not in test_impact,
                "details": {"response": test_impact[:500]},
            }
        )
        confidence = mcp_server.get_confidence_score("app.py", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_confidence_uses_isolated_atlas",
                "passed": confidence.startswith("# Confidence Brief")
                and '"app.py"' in confidence
                and "output/.raw" not in confidence,
                "details": {"response": confidence[:500]},
            }
        )
        upstream = mcp_server.trace_upstream_cause("MAIN::app.py", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_upstream_trace_uses_isolated_atlas_fallback",
                "passed": upstream.startswith("# Upstream Cause Brief")
                and "dependency_graph_source: \"atlas_imports_fallback\"" in upstream
                and "analysis_root:" in upstream
                and target_root.name in upstream
                and "SAGE source workspace" not in upstream,
                "details": {"response": upstream[:500]},
            }
        )
        active_signals = mcp_server.get_active_signals(target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_active_signals_fails_closed_without_signals",
                "passed": active_signals.startswith("# Target Evidence Missing")
                and "signals.json" in active_signals
                and "SAGE source workspace" in active_signals,
                "details": {"response": active_signals[:500]},
            }
        )
        dead_code = mcp_server.get_dead_code("app.py", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_dead_code_fails_closed_without_artifact",
                "passed": dead_code.startswith("# Target Evidence Missing")
                and "dead_code.json" in dead_code
                and "SAGE source workspace" in dead_code,
                "details": {"response": dead_code[:500]},
            }
        )
        module_integrity = mcp_server.check_module_integrity("app.py", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_module_integrity_uses_isolated_target_scope",
                "passed": module_integrity.startswith("# Module Integrity Brief")
                and "analysis_root:" in module_integrity
                and target_root.name in module_integrity
                and "SAGE source workspace" not in module_integrity
                and "output/.raw" not in module_integrity,
                "details": {"response": module_integrity[:500]},
            }
        )
        work_queue_missing = mcp_server.get_violation_work_queue(target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_violation_work_queue_fails_closed_without_artifact",
                "passed": work_queue_missing.startswith("# Technical Debt Work Queue")
                and "refresh_sage_evidence_before_editing" in work_queue_missing
                and "items:\n  []" in work_queue_missing
                and "SAGE source workspace" not in work_queue_missing,
                "details": {"response": work_queue_missing[:500]},
            }
        )
        if atlas_path:
            synthetic_audit = {
                "meta": {"kind": "audit_report", "fixture": "external_target_violation_work_queue"},
                "summary": {"total": 2},
                "violations": [
                    {
                        "file": "MAIN::app.py",
                        "rule": "relative_imports_no_alias",
                        "mode": "heal",
                        "detail": "Use canonical import aliases where the workspace exposes an alias contract.",
                    },
                    {
                        "file": "MAIN::pkg/service.py",
                        "rule": "loc_limits_service",
                        "mode": "enforced",
                        "detail": "Service file exceeds configured maintainability boundary in this fixture.",
                    },
                ],
            }
            (atlas_path.parent / "audit_report.json").write_text(json.dumps(synthetic_audit, indent=2), encoding="utf-8")
        work_queue = mcp_server.get_violation_work_queue(page_size=1, target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_violation_work_queue_fails_closed_on_incomplete_trust_chain",
                "passed": work_queue.startswith("# Technical Debt Work Queue")
                and "returned_work_items: 0" in work_queue
                and "refresh_sage_evidence_before_editing" in work_queue
                and "Do not edit from this queue until SAGE evidence is refreshed." in work_queue
                and "items:\n  []" in work_queue
                and "human_approval_required: true" not in work_queue
                and "target_ref: \"MAIN::app.py\"" not in work_queue
                and "SOVEREIGN_ELITE" not in work_queue,
                "details": {"response": work_queue[:900]},
            }
        )
        work_queue_json = mcp_server.get_violation_work_queue(page=2, page_size=1, target_root=str(target_root), format="json")
        try:
            work_queue_payload = json.loads(work_queue_json)
        except json.JSONDecodeError:
            work_queue_payload = {}
        checks.append(
            {
                "name": "external_target_mcp_violation_work_queue_supports_json_pagination",
                "passed": work_queue_payload.get("analysis_root") == str(target_root)
                and work_queue_payload.get("page") == 2
                and work_queue_payload.get("page_size") == 1
                and isinstance(work_queue_payload.get("items", []), list)
                and (work_queue_payload.get("artifact_trust") or {}).get("status") == "FAIL",
                "details": {"payload": work_queue_payload},
            }
        )
        clone_context = mcp_server.find_clones("service", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_clones_fails_closed_without_artifact",
                "passed": clone_context.startswith("# Target Evidence Missing")
                and "clone_detector.json" in clone_context
                and "SAGE source workspace" in clone_context,
                "details": {"response": clone_context[:500]},
            }
        )
        simulated_impact = mcp_server.simulate_change_impact("MAIN::app.py", target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_simulate_change_impact_uses_target_root",
                "passed": simulated_impact.startswith("# Impact Radius Brief")
                and (
                    "dependency_graph_source: \"atlas_imports_fallback\"" in simulated_impact
                    or 'dependency_graph_source: "sqlite_dependencies"' in simulated_impact
                )
                and "analysis_root:" in simulated_impact
                and target_root.name in simulated_impact
                and "SAGE source workspace" not in simulated_impact,
                "details": {"response": simulated_impact[:500]},
            }
        )
        state_flow = mcp_server.get_state_flow(target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_state_flow_fails_closed_without_artifact",
                "passed": state_flow.startswith("# Target Evidence Missing")
                and "state_flow.json" in state_flow
                and "SAGE source workspace" in state_flow,
                "details": {"response": state_flow[:500]},
            }
        )
        ui_architecture = mcp_server.get_ui_architecture(target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_ui_architecture_fails_closed_without_artifact",
                "passed": ui_architecture.startswith("# Target Evidence Missing")
                and "ui_architecture_map.json" in ui_architecture
                and "ui_mapper.json" in ui_architecture
                and "SAGE source workspace" in ui_architecture,
                "details": {"response": ui_architecture[:500]},
            }
        )
        circular = mcp_server.get_circular_dependencies(target_root=str(target_root))
        checks.append(
            {
                "name": "external_target_mcp_circular_deps_fails_closed_without_artifact",
                "passed": circular.startswith("# Target Evidence Missing")
                and "circular_deps.json" in circular
                and "SAGE source workspace" in circular,
                "details": {"response": circular[:500]},
            }
        )
        patch_validation = mcp_server.validate_patch(
            "app.py",
            "from pkg.service import run\nvalue = run()\nprint(value)\n",
            target_root=str(target_root),
        )
        checks.append(
            {
                "name": "external_target_mcp_validate_patch_uses_target_root",
                "passed": patch_validation.startswith("# Patch Validation Brief")
                and 'analysis_root:' in patch_validation
                and 'target_project: "MAIN"' in patch_validation
                and 'target_file: "app.py"' in patch_validation
                and 'target_ref: "MAIN::app.py"' in patch_validation
                and (
                    'status: "PASS"' in patch_validation
                    or (
                        'status: "FAIL"' in patch_validation
                        and "safe_to_apply: false" in patch_validation
                        and "full_replacement_patch: true" in patch_validation
                        and "violation_count: 1" in patch_validation
                    )
                    or (
                        'status: "REVIEW_REQUIRED"' in patch_validation
                        and "full_replacement_patch: true" in patch_validation
                        and "human_approval_required: true" in patch_validation
                    )
                )
                and "canonical alias prefix '@/'" not in patch_validation
                and "mcp_path_escape" not in patch_validation,
                "details": {"response": patch_validation[:500]},
            }
        )

    after_default_atlas_mtime = (RAW_DIR / "atlas.json").stat().st_mtime if (RAW_DIR / "atlas.json").exists() else None
    checks.append(
        {
            "name": "default_workspace_atlas_not_rewritten",
            "passed": before_default_atlas_mtime == after_default_atlas_mtime,
            "details": {"before": before_default_atlas_mtime, "after": after_default_atlas_mtime},
        }
    )
    retention_result = prune_generated_external_target_fixtures(
        keep_per_prefix=DEFAULT_KEEP_PER_PREFIX,
        dry_run=False,
    )
    checks.append(
        {
            "name": "external_target_generated_fixture_retention_applied",
            "passed": retention_result.get("status") == "PASS",
            "details": {
                "keep_per_prefix": retention_result.get("keep_per_prefix"),
                "selected_for_removal": retention_result.get("selected_for_removal"),
                "failed": retention_result.get("failed", []),
            },
        }
    )

    payload = {
        "meta": {"kind": "external_target_smoke_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "external_target_smoke_validation.json", payload)
    lines = [
        "# External Target Smoke Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} |")
    save_text_atomic(REPORTS_DIR / "external_target_smoke_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

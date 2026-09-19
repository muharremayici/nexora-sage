from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.agent_surface_target_visibility import (
    is_evidence_blocked_empty_result,
    is_structured_precondition_block,
    is_successful_surgical_packet,
)
from tools.core.external_target_retention import (
    DEFAULT_KEEP_PER_PREFIX,
    generated_fixture_lease,
    prune_generated_external_target_fixtures,
)
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.mcp import server as mcp_server


RAW_OUTPUT_PATH = RAW_DIR / "external_target_agent_surface_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "external_target_agent_surface_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(message: str) -> None:
    print(f"[external-target-agent-surface] {message}", flush=True)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _write_target(root: Path) -> None:
    (root / "src" / "shared").mkdir(parents=True)
    (root / "src" / "main.tsx").write_text(
        "import React from 'react';\n"
        "import { createRoot } from 'react-dom/client';\n"
        "import { App } from './App';\n"
        "\n"
        "createRoot(document.getElementById('root')!).render(<App />);\n",
        encoding="utf-8",
    )
    (root / "src" / "App.tsx").write_text(
        "import { formatTitle } from './shared/format';\n"
        "\n"
        "export function App() {\n"
        "  return <main>{formatTitle('SAGE external target')}</main>;\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "src" / "shared" / "format.ts").write_text(
        "export function formatTitle(value: string): string {\n"
        "  return value.trim().toUpperCase();\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "src" / "shared" / "format.test.ts").write_text(
        "import { formatTitle } from './format';\n"
        "\n"
        "test('formatTitle trims and uppercases', () => {\n"
        "  expect(formatTitle(' sage ')).toBe('SAGE');\n"
        "});\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "sage-external-target-agent-surface-fixture",
                "private": True,
                "type": "module",
                "dependencies": {
                    "@vitejs/plugin-react": "latest",
                    "react": "latest",
                    "react-dom": "latest",
                },
                "devDependencies": {
                    "typescript": "latest",
                    "vite": "latest",
                    "vitest": "latest",
                },
                "scripts": {
                    "test": "vitest run",
                    "typecheck": "tsc --noEmit",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "jsx": "react-jsx",
                    "module": "ESNext",
                    "moduleResolution": "Bundler",
                    "target": "ES2020",
                    "strict": True,
                    "baseUrl": ".",
                    "paths": {"@/*": ["src/*"]},
                },
                "include": ["src"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _json_call(name: str, producer) -> dict[str, Any]:
    started = time.perf_counter()
    _log(f"START {name}")
    try:
        result = json.loads(producer())
        _log(f"PASS {name} ({_elapsed_ms(started)}ms)")
        return result
    except Exception as exc:
        _log(f"FAIL {name} ({_elapsed_ms(started)}ms): {exc}")
        return {"__error__": f"{name}: {exc}"}


def _brief_call(name: str, producer) -> str:
    started = time.perf_counter()
    _log(f"START {name}")
    try:
        result = str(producer())
        _log(f"PASS {name} ({_elapsed_ms(started)}ms)")
        return result
    except Exception as exc:
        _log(f"FAIL {name} ({_elapsed_ms(started)}ms): {exc}")
        return f"[exception:{name}] {exc}"


def _require_successful_analysis(payload: dict[str, Any]) -> None:
    if payload.get("status") == "PASS":
        return
    detail = str(
        payload.get("command_output")
        or payload.get("__error__")
        or payload.get("required_action")
        or "unknown external-target producer failure"
    )
    raise RuntimeError(
        "External target analysis did not produce validated current authority: "
        + detail[-2000:]
    )


def _target_file_exists(payload: dict[str, Any], root: Path) -> bool:
    target = str(payload.get("target_file") or "").replace("\\", "/").strip("/")
    return bool(target) and (root / target).exists()


def _inspect_payload_has_openable_file(payload: dict[str, Any], root: Path, expected: str) -> bool:
    if _target_file_exists(payload, root):
        return True
    rows = payload.get("target_file_context") if isinstance(payload, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        candidate = str(row.get("repo_relative_path") or row.get("workspace_rel") or row.get("file") or "").replace("\\", "/").strip("/")
        if candidate == expected and (root / candidate).exists():
            return True
    return False


def _preflight_status(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else payload
    return str(summary.get("status") or "") if isinstance(summary, dict) else ""


def _seed_external_raw_fixture(raw_dir: Path, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Seed external-target fixture truth through SQLite with a JSON shadow."""
    db_path = raw_dir / "codemaps.db"
    native_db_path = Path(native_filesystem_path(db_path))
    if not native_db_path.exists():
        raise RuntimeError(f"External target SQLite store is missing: {db_path}")
    serialized = json.dumps(payload, ensure_ascii=False)
    payload_sha = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    source_mtime = time.time()
    with sqlite3.connect(native_db_path) as conn:
        conn.execute(
            """
            INSERT INTO state_payloads (name, payload, payload_sha, source_mtime, updated_at)
            VALUES (?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'))
            ON CONFLICT(name) DO UPDATE SET
                payload = excluded.payload,
                payload_sha = excluded.payload_sha,
                source_mtime = excluded.source_mtime,
                updated_at = excluded.updated_at
            """,
            (name, serialized, payload_sha, source_mtime),
        )
    shadow_path = raw_dir / f"{name}.json"
    save_text_atomic(shadow_path, json.dumps(payload, indent=2, ensure_ascii=False))
    with sqlite3.connect(native_db_path) as conn:
        row = conn.execute(
            "SELECT payload_sha FROM state_payloads WHERE name = ?",
            (name,),
        ).fetchone()
    return {
        "db_path": str(db_path),
        "shadow_path": str(shadow_path),
        "sqlite_row_present": bool(row),
        "payload_sha_matches": bool(row and row[0] == payload_sha),
    }


def build_validation() -> dict[str, Any]:
    _log("START fixture")
    with tempfile.TemporaryDirectory(
        prefix="sage_external_agent_surface_"
    ) as tmp, generated_fixture_lease(Path(tmp)):
        target_root = Path(tmp).resolve()
        _write_target(target_root)
        _log("PASS fixture")
        target = str(target_root)

        preflight = _json_call("external_target_preflight", lambda: mcp_server.external_target_preflight(target))
        analysis = _json_call(
            "run_external_target_analysis",
            lambda: mcp_server.run_external_target_analysis(target, full=True, skip_preflight=False, include_brief=True, format="brief"),
        )
        _require_successful_analysis(analysis)
        surgical_brief = _brief_call("get_surgical_operation_packet", lambda: mcp_server.get_surgical_operation_packet(target_root=target))
        search_brief = _brief_call("search_symbols", lambda: mcp_server.search_symbols("App", target_root=target))
        inspect_payload = _json_call("inspect_file", lambda: mcp_server.inspect_file("src/App.tsx", target_root=target, format="json"))
        missing_inspect_payload = _json_call("inspect_file_missing", lambda: mcp_server.inspect_file("src/missing.tsx", target_root=target, format="json"))
        inspect_symbol_payload = _json_call("inspect_symbol", lambda: mcp_server.inspect_symbol("App", target_root=target, format="json"))
        missing_symbol_payload = _json_call("inspect_symbol_missing", lambda: mcp_server.inspect_symbol("DefinitelyMissingSymbol", target_root=target, format="json"))
        impact_payload = _json_call("get_impact_radius", lambda: mcp_server.get_impact_radius("MAIN::src/shared/format.ts", target_root=target, format="json"))
        missing_impact_payload = _json_call("get_impact_radius_missing", lambda: mcp_server.get_impact_radius("MAIN::src/missing.ts", target_root=target, format="json"))
        upstream_payload = _json_call("trace_upstream_cause", lambda: mcp_server.trace_upstream_cause("MAIN::src/App.tsx", target_root=target, format="json"))
        merge_raw_dir = mcp_server._raw_dir_for_target(target)
        synthetic_merge = {
            "meta": {"kind": "merge_decision_cockpit", "fixture": "external_agent_surface"},
            "decisions": [
                {
                    "candidate": "App component import review",
                    "action": "Import With Review",
                    "source": "MAIN",
                    "target_path": "src/App.tsx",
                    "closure_size": 1,
                    "reasons": ["Fixture merge candidate for agent-facing review-only packet."],
                    "required_actions": ["Inspect source and target before proposing any merge patch."],
                    "confidence": {"tier": "medium", "score": 0.72},
                    "route": {"smoke_path": "/"},
                    "evidence": {
                        "source_contract_file": "MAIN::src/App.tsx",
                        "external_deps": [],
                        "unresolved_internal_deps": [],
                        "target_conflicts": [],
                        "harness_plan": {"i18n_keys": {"missing": []}},
                    },
                }
            ],
        }
        merge_fixture_seed = _seed_external_raw_fixture(
            merge_raw_dir,
            "merge_decision_cockpit",
            synthetic_merge,
        )
        merge_payload = _json_call("get_merge_review_queue", lambda: mcp_server.get_merge_review_queue(target_root=target, format="json"))
        merge_brief = _brief_call("get_merge_review_queue", lambda: mcp_server.get_merge_review_queue(target_root=target, format="brief"))
        confidence_payload = _json_call("get_confidence_score", lambda: mcp_server.get_confidence_score("src/App.tsx", target_root=target, format="json"))
        test_payload = _json_call("get_test_impact", lambda: mcp_server.get_test_impact("src/shared/format.ts", target_root=target, format="json"))
        patch_payload = _json_call(
            "validate_patch",
            lambda: mcp_server.validate_patch("src/does-not-exist.ts", "export const x = 1;\n", target_root=target, format="json"),
        )

        with tempfile.TemporaryDirectory(prefix="sage_external_missing_surface_") as missing_tmp:
            missing_root = Path(missing_tmp).resolve()
            _write_target(missing_root)
            missing_brief = _brief_call(
                "missing_target_signals",
                lambda: mcp_server.get_active_signals(target_root=str(missing_root)),
            )

        analysis_root = str(target_root)
        successful_surgical_brief = is_successful_surgical_packet({"body": surgical_brief})
        successful_merge_payload = (
            merge_payload.get("analysis_root") == analysis_root
            and (merge_payload.get("policy_boundary") or {}).get("human_approval_required") is True
            and (merge_payload.get("policy_boundary") or {}).get("mutation_allowed_by_this_packet") is False
            and len(merge_payload.get("items") or []) == 1
            and (merge_payload.get("items") or [{}])[0].get("source_file") == "src/App.tsx"
            and ((merge_payload.get("items") or [{}])[0].get("source_file_status") or {}).get("exists") is True
            and ((merge_payload.get("items") or [{}])[0].get("source_file_status") or {}).get("indexed") is True
            and ((merge_payload.get("items") or [{}])[0].get("proposed_target_status") or {}).get("inside_root") is True
        )
        blocked_merge_payload = is_evidence_blocked_empty_result(
            {"body": json.dumps(merge_payload, ensure_ascii=False)}
        ) or is_structured_precondition_block(
            {"body": json.dumps(merge_payload, ensure_ascii=False)},
            expected_tool="get_merge_review_queue",
        )
        successful_merge_brief = (
            merge_brief.startswith("# Merge Review Queue")
            and "mutation_allowed_by_this_packet: false" in merge_brief
            and "human_approval_required: true" in merge_brief
            and "source_file_status:" in merge_brief
            and "proposed_target_status:" in merge_brief
            and "output/.raw" not in merge_brief
            and "SOVEREIGN_ELITE" not in merge_brief
        )
        blocked_merge_brief = is_evidence_blocked_empty_result(
            {"body": merge_brief}
        ) or is_structured_precondition_block(
            {"body": merge_brief},
            expected_tool="get_merge_review_queue",
        )
        checks = [
            _check(
                "external_preflight_passes_for_fixture",
                _preflight_status(preflight) == "PASS",
                preflight,
            ),
            _check(
                "external_analysis_reports_success_for_isolated_target_root",
                analysis.get("status") == "PASS"
                and analysis.get("analysis_succeeded") is True
                and analysis.get("context_closure_requested") is True
                and analysis.get("context_closure_succeeded") is True
                and analysis.get("context_closure_mode")
                in {"reused_current_primary_full_run", "agent_context_recovery"}
                and analysis.get("external_target_index_succeeded") is True
                and analysis.get("surgical_packet_succeeded") is True
                and analysis.get("target_root") == analysis_root
                and "surgical_packet" in analysis,
                {
                    "status": analysis.get("status"),
                    "analysis_succeeded": analysis.get("analysis_succeeded"),
                    "context_closure_requested": analysis.get("context_closure_requested"),
                    "context_closure_succeeded": analysis.get("context_closure_succeeded"),
                    "context_closure_mode": analysis.get("context_closure_mode"),
                    "external_target_index_succeeded": analysis.get("external_target_index_succeeded"),
                    "surgical_packet_succeeded": analysis.get("surgical_packet_succeeded"),
                    "target_root": analysis.get("target_root"),
                    "has_surgical_packet": "surgical_packet" in analysis,
                    "command_output_excerpt": str(analysis.get("command_output") or "")[-1600:],
                    "context_closure_output_excerpt": str(
                        analysis.get("context_closure_output") or ""
                    )[-1600:],
                    "index_output_excerpt": str(analysis.get("external_target_index_output") or "")[-800:],
                },
            ),
            _check(
                "full_external_analysis_produces_successful_surgical_surface",
                successful_surgical_brief,
                {
                    "successful_brief": successful_surgical_brief,
                    "response_excerpt": surgical_brief[:1200],
                },
            ),
            _check(
                "search_brief_points_to_external_target_inspection",
                "target_ref:" in search_brief
                and "next_tool:" in search_brief
                and "Do not edit from search results alone." in search_brief,
                search_brief[:1200],
            ),
            _check(
                "inspect_file_payload_is_openable_in_external_root",
                inspect_payload.get("analysis_root") == analysis_root
                and inspect_payload.get("target_file") == "src/App.tsx"
                and inspect_payload.get("target_ref") == "MAIN::src/App.tsx"
                and inspect_payload.get("source_grounded_for_inspection") is True
                and inspect_payload.get("safe_to_edit_from_inspection") is False
                and inspect_payload.get("one_shot_edit_ready") is False
                and inspect_payload.get("inspection_authority") == "orientation_only"
                and _inspect_payload_has_openable_file(inspect_payload, target_root, "src/App.tsx"),
                {
                    "analysis_root": inspect_payload.get("analysis_root"),
                    "target_file": inspect_payload.get("target_file"),
                    "target_ref": inspect_payload.get("target_ref"),
                    "source_grounded_for_inspection": inspect_payload.get("source_grounded_for_inspection"),
                    "safe_to_edit_from_inspection": inspect_payload.get("safe_to_edit_from_inspection"),
                    "one_shot_edit_ready": inspect_payload.get("one_shot_edit_ready"),
                    "inspection_authority": inspect_payload.get("inspection_authority"),
                },
            ),
            _check(
                "inspect_file_missing_external_target_fails_closed",
                missing_inspect_payload.get("analysis_root") == analysis_root
                and missing_inspect_payload.get("source_grounded_for_inspection") is False
                and missing_inspect_payload.get("safe_to_edit_from_inspection") is False
                and missing_inspect_payload.get("agent_action") == "refresh_or_correct_target_before_editing"
                and (missing_inspect_payload.get("target_path_status") or {}).get("exists") is False,
                {
                    "analysis_root": missing_inspect_payload.get("analysis_root"),
                    "safe_to_edit_from_inspection": missing_inspect_payload.get("safe_to_edit_from_inspection"),
                    "agent_action": missing_inspect_payload.get("agent_action"),
                    "target_path_status": missing_inspect_payload.get("target_path_status"),
                },
            ),
            _check(
                "inspect_symbol_payload_is_openable_in_external_root",
                inspect_symbol_payload.get("analysis_root") == analysis_root
                and inspect_symbol_payload.get("target_file") == "src/App.tsx"
                and inspect_symbol_payload.get("target_ref") == "MAIN::src/App.tsx"
                and inspect_symbol_payload.get("source_grounded_for_inspection") is True
                and inspect_symbol_payload.get("safe_to_edit_from_inspection") is True
                and inspect_symbol_payload.get("one_shot_edit_ready") is True
                and inspect_symbol_payload.get("inspection_authority") == "bounded_edit_context"
                and _inspect_payload_has_openable_file(inspect_symbol_payload, target_root, "src/App.tsx"),
                {
                    "analysis_root": inspect_symbol_payload.get("analysis_root"),
                    "target_file": inspect_symbol_payload.get("target_file"),
                    "target_ref": inspect_symbol_payload.get("target_ref"),
                    "source_grounded_for_inspection": inspect_symbol_payload.get("source_grounded_for_inspection"),
                    "safe_to_edit_from_inspection": inspect_symbol_payload.get("safe_to_edit_from_inspection"),
                    "one_shot_edit_ready": inspect_symbol_payload.get("one_shot_edit_ready"),
                    "inspection_authority": inspect_symbol_payload.get("inspection_authority"),
                },
            ),
            _check(
                "inspect_symbol_missing_external_target_fails_closed",
                missing_symbol_payload.get("analysis_root") == analysis_root
                and missing_symbol_payload.get("source_grounded_for_inspection") is False
                and missing_symbol_payload.get("safe_to_edit_from_inspection") is False
                and missing_symbol_payload.get("agent_action") == "refresh_or_correct_target_before_editing"
                and missing_symbol_payload.get("grounding_failure") == "requested_target_not_found_in_current_artifacts",
                {
                    "analysis_root": missing_symbol_payload.get("analysis_root"),
                    "safe_to_edit_from_inspection": missing_symbol_payload.get("safe_to_edit_from_inspection"),
                    "agent_action": missing_symbol_payload.get("agent_action"),
                    "grounding_failure": missing_symbol_payload.get("grounding_failure"),
                },
            ),
            _check(
                "impact_radius_uses_external_graph_source_with_repo_relative_paths",
                impact_payload.get("analysis_root") == analysis_root
                and impact_payload.get("target_file") == "src/shared/format.ts"
                and impact_payload.get("dependency_graph_source") == "sqlite_dependencies"
                and "src/App.tsx" in (impact_payload.get("direct_dependents") or [])
                and "src/main.tsx" in (impact_payload.get("transitive_dependents") or []),
                {
                    "analysis_root": impact_payload.get("analysis_root"),
                    "target_file": impact_payload.get("target_file"),
                    "dependency_graph_source": impact_payload.get("dependency_graph_source"),
                    "direct_dependents": impact_payload.get("direct_dependents"),
                    "transitive_dependents": impact_payload.get("transitive_dependents"),
                },
            ),
            _check(
                "impact_radius_missing_external_target_fails_closed_with_grounding_status",
                missing_impact_payload.get("analysis_root") == analysis_root
                and missing_impact_payload.get("status") == "target_not_found_in_dependency_graph"
                and missing_impact_payload.get("agent_action") == "inspect_or_refresh_target_before_editing"
                and (missing_impact_payload.get("target_path_status") or {}).get("exists") is False,
                {
                    "analysis_root": missing_impact_payload.get("analysis_root"),
                    "status": missing_impact_payload.get("status"),
                    "agent_action": missing_impact_payload.get("agent_action"),
                    "target_path_status": missing_impact_payload.get("target_path_status"),
                },
            ),
            _check(
                "upstream_trace_uses_external_graph_source_with_repo_relative_paths",
                upstream_payload.get("analysis_root") == analysis_root
                and upstream_payload.get("target_file") == "src/App.tsx"
                and upstream_payload.get("dependency_graph_source") == "sqlite_dependencies"
                and "src/shared/format.ts" in (upstream_payload.get("upstream_dependency_files") or [])
                and "src/main.tsx" in (upstream_payload.get("direct_dependent_files") or []),
                {
                    "analysis_root": upstream_payload.get("analysis_root"),
                    "target_file": upstream_payload.get("target_file"),
                    "dependency_graph_source": upstream_payload.get("dependency_graph_source"),
                    "upstream_dependency_files": upstream_payload.get("upstream_dependency_files"),
                    "direct_dependent_files": upstream_payload.get("direct_dependent_files"),
                },
            ),
            _check(
                "merge_review_fixture_is_sqlite_first_with_json_shadow",
                merge_fixture_seed.get("sqlite_row_present") is True
                and merge_fixture_seed.get("payload_sha_matches") is True,
                merge_fixture_seed,
            ),
            _check(
                "merge_review_queue_is_external_review_only_and_lifecycle_safe",
                successful_merge_payload or blocked_merge_payload,
                {
                    "successful_queue": successful_merge_payload,
                    "safe_evidence_block": blocked_merge_payload,
                    "analysis_root": merge_payload.get("analysis_root"),
                    "policy_boundary": merge_payload.get("policy_boundary"),
                    "item": (merge_payload.get("items") or [{}])[0],
                },
            ),
            _check(
                "merge_review_brief_preserves_review_only_lifecycle_boundary",
                successful_merge_brief or blocked_merge_brief,
                {
                    "successful_queue": successful_merge_brief,
                    "safe_evidence_block": blocked_merge_brief,
                    "response_excerpt": merge_brief[:1400],
                },
            ),
            _check(
                "confidence_payload_is_external_and_not_merge_approval",
                confidence_payload.get("analysis_root") == analysis_root
                and _target_file_exists(confidence_payload, target_root)
                and "not a standalone merge or deploy approval" in str(confidence_payload.get("decision_boundary") or "").lower(),
                {
                    "analysis_root": confidence_payload.get("analysis_root"),
                    "target_file": confidence_payload.get("target_file"),
                    "decision_boundary": confidence_payload.get("decision_boundary"),
                },
            ),
            _check(
                "test_impact_payload_uses_external_repo_relative_paths",
                test_payload.get("analysis_root") == analysis_root
                and test_payload.get("target_file") == "src/shared/format.ts"
                and all(
                    not str(row.get("file") or "").startswith(str(ROOT))
                    for row in test_payload.get("impacted_tests", [])
                    if isinstance(row, dict)
                ),
                {"analysis_root": test_payload.get("analysis_root"), "target_file": test_payload.get("target_file"), "tests": test_payload.get("impacted_tests", [])[:3]},
            ),
            _check(
                "validate_patch_missing_external_target_fails_closed",
                patch_payload.get("status") == "FAIL"
                and patch_payload.get("safe_to_apply") is False
                and patch_payload.get("target_exists") is False
                and patch_payload.get("target_indexed") is False,
                patch_payload,
            ),
            _check(
                "missing_external_target_artifact_fails_closed",
                "# Target Evidence Missing" in missing_brief
                and "missing_required_target_artifacts" in missing_brief
                and "Do not use impact, test, confidence, or upstream data from the SAGE source workspace" in missing_brief,
                missing_brief[:1200],
            ),
        ]

    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    retention_result = prune_generated_external_target_fixtures(
        keep_per_prefix=DEFAULT_KEEP_PER_PREFIX,
        dry_run=False,
    )
    checks.append(
        _check(
            "external_agent_surface_generated_fixture_retention_applied",
            retention_result.get("status") == "PASS",
            {
                "keep_per_prefix": retention_result.get("keep_per_prefix"),
                "selected_for_removal": retention_result.get("selected_for_removal"),
                "failed": retention_result.get("failed", []),
            },
        )
    )
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    _log(f"{status} validation ({sum(1 for check in checks if check['passed'])}/{len(checks)} checks)")
    return {
        "meta": {
            "kind": "external_target_agent_surface_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_external_target_agent_surface",
        },
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
        },
        "checks": checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# External Target Agent Surface Validation",
        "",
        "Checks that target_root MCP flows return source-grounded target-repository briefs instead of SAGE workspace context.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- passed: `{summary.get('passed')}/{summary.get('checks')}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

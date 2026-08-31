from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.atlas_io import load_atlas_data
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, SOURCE_EXTENSIONS, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.engines.test_impact_matcher import extract_base_name, generate_test_command, is_test_file


SOURCE_SUFFIXES = {str(ext).lower() for ext in SOURCE_EXTENSIONS}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _suffix(path: str) -> str:
    return Path(path).suffix.lower()


def _is_source_file(path: str) -> bool:
    return _suffix(path) in SOURCE_SUFFIXES


def _default_agent_project_scope(atlas: dict[str, Any]) -> str:
    policy = load_json_file(CONFIG_DIR / "pipeline_execution_policy.json", {})
    project_scope = policy.get("project_scope_policy", {}) if isinstance(policy, dict) else {}
    default_scope = str(project_scope.get("default_agent_read_scope") or "MAIN")
    if default_scope in atlas:
        return default_scope
    return next(iter(atlas.keys()), "MAIN") if isinstance(atlas, dict) else "MAIN"


def _scope_items(items: list[dict[str, Any]], project_scope: str) -> list[dict[str, Any]]:
    if project_scope in {"*", "all", "ALL"}:
        return items
    return [item for item in items if str(item.get("project") or "") == project_scope]


def _iter_atlas_files(atlas: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for project_key, project_data in atlas.items():
        if not isinstance(project_data, dict):
            continue
        project_files = project_data.get("files", {})
        if not isinstance(project_files, dict):
            continue
        for rel_path, file_data in project_files.items():
            if not isinstance(rel_path, str):
                continue
            rel_path = rel_path.replace("\\", "/")
            node_key = f"{project_key}::{rel_path}"
            files.append(
                {
                    "project": project_key,
                    "relative_path": rel_path,
                    "node_key": node_key,
                    "file_data": file_data if isinstance(file_data, dict) else {},
                    "is_test": is_test_file(rel_path),
                    "is_source": _is_source_file(rel_path),
                    "base_name": extract_base_name(rel_path),
                }
            )
    return files


def _load_reverse_dependency_graph() -> dict[str, list[str]]:
    graph = load_json_file(RAW_DIR / "circular_deps.json", {})
    reverse: dict[str, list[str]] = defaultdict(list)
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and isinstance(target, str):
            reverse[target].append(source)
    return dict(reverse)


def _reachable_tests(start_node: str, reverse: dict[str, list[str]], test_nodes: set[str], limit: int = 5000) -> list[dict[str, Any]]:
    direct = sorted(set(reverse.get(start_node, [])))
    found: dict[str, dict[str, Any]] = {}
    queue: deque[tuple[str, int]] = deque((node, 1) for node in direct)
    visited = {start_node}
    steps = 0
    while queue and steps < limit:
        node, depth = queue.popleft()
        steps += 1
        if node in visited:
            continue
        visited.add(node)
        if node in test_nodes:
            project, rel_path = node.split("::", 1) if "::" in node else ("UNKNOWN", node)
            found[node] = {
                "node_key": node,
                "project": project,
                "file": rel_path,
                "type": "Direct Static Import" if depth == 1 else "Transitive Static Dependency",
                "confidence": 1.0 if depth == 1 else 0.6,
                "run_command": generate_test_command(rel_path),
            }
        if depth < 8:
            for parent in sorted(set(reverse.get(node, []))):
                if parent not in visited:
                    queue.append((parent, depth + 1))
    return sorted(found.values(), key=lambda item: (-float(item["confidence"]), item["file"]))


def _blast_scores() -> dict[str, float]:
    data = load_json_file(RAW_DIR / "blast_radius.json", {})
    entries = data.get("blast_radius", []) if isinstance(data, dict) else []
    scores: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_key = entry.get("file")
        score = entry.get("total_impact_score", 0)
        if isinstance(file_key, str):
            try:
                scores[file_key] = float(score or 0)
            except (TypeError, ValueError):
                scores[file_key] = 0.0
    return scores


def _active_signal_nodes() -> set[str]:
    signals = load_json_file(RAW_DIR / "signals.json", {})
    active = signals.get("active_signals", []) if isinstance(signals, dict) else []
    nodes = set()
    for signal in active:
        if isinstance(signal, dict) and isinstance(signal.get("node_key"), str):
            nodes.add(signal["node_key"])
    return nodes


def _priority_label(entry: dict[str, Any]) -> str:
    if entry.get("active_signal"):
        return "P0_ACTIVE_UNTESTED_CHANGE"
    if float(entry.get("impact_score", 0.0) or 0.0) >= 250:
        return "P1_HIGH_BLAST_RADIUS_GAP"
    if float(entry.get("impact_score", 0.0) or 0.0) >= 100:
        return "P2_ARCHITECTURAL_TEST_GAP"
    return "P3_LOCAL_TEST_GAP"


def _recommended_action(entry: dict[str, Any]) -> str:
    if entry.get("active_signal"):
        return "Add or identify the impacted test before merging the active change."
    if float(entry.get("impact_score", 0.0) or 0.0) >= 250:
        return "Create a characterization test around the public behavior before refactoring this hub."
    if float(entry.get("impact_score", 0.0) or 0.0) >= 100:
        return "Add a focused unit/integration test or document the existing external coverage owner."
    return "Backfill when touching this file; not a release blocker by itself."


def _with_surgical_priority(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prioritized: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        item["priority"] = _priority_label(item)
        item["recommended_action"] = _recommended_action(item)
        item["why"] = [
            reason
            for reason in (
                "active ContextOS focus" if item.get("active_signal") else "",
                f"impact_score={item.get('impact_score')}",
                "no static or convention test candidate",
            )
            if reason
        ]
        prioritized.append(item)
    return prioritized


def build_test_gap_report() -> dict[str, Any]:
    atlas = load_atlas_data()
    default_project_scope = _default_agent_project_scope(atlas)
    files = _iter_atlas_files(atlas)
    all_source_files = [item for item in files if item["is_source"] and not item["is_test"]]
    all_test_files = [item for item in files if item["is_source"] and item["is_test"]]
    source_files = _scope_items(all_source_files, default_project_scope)
    test_files = _scope_items(all_test_files, default_project_scope)
    test_nodes = {item["node_key"] for item in test_files}
    source_project_bases = {(item["project"], item["base_name"]) for item in source_files if item["base_name"]}

    tests_by_project_base: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for test in test_files:
        tests_by_project_base[(test["project"], test["base_name"])].append(test)

    reverse = _load_reverse_dependency_graph()
    blast = _blast_scores()
    active_nodes = _active_signal_nodes()

    covered_sources: list[dict[str, Any]] = []
    uncovered_sources: list[dict[str, Any]] = []
    changed_without_impacted_tests: list[dict[str, Any]] = []

    for source in source_files:
        convention_tests = tests_by_project_base.get((source["project"], source["base_name"]), [])
        static_tests = _reachable_tests(source["node_key"], reverse, test_nodes, limit=1200)
        convention_entries = [
            {
                "node_key": test["node_key"],
                "project": test["project"],
                "file": test["relative_path"],
                "type": "Semantic Convention Match",
                "confidence": 0.8,
                "run_command": generate_test_command(test["relative_path"]),
            }
            for test in convention_tests
        ]
        candidates_by_node = {entry["node_key"]: entry for entry in static_tests}
        for entry in convention_entries:
            existing = candidates_by_node.get(entry["node_key"])
            if existing:
                existing["type"] = "Dual Vector Match"
                existing["confidence"] = 1.0
            else:
                candidates_by_node[entry["node_key"]] = entry
        candidates = sorted(candidates_by_node.values(), key=lambda item: (-float(item["confidence"]), item["file"]))
        impact_score = float(blast.get(source["node_key"], 0.0))
        active = source["node_key"] in active_nodes
        risk_score = impact_score + (40.0 if active else 0.0)
        entry = {
            "node_key": source["node_key"],
            "project": source["project"],
            "file": source["relative_path"],
            "base_name": source["base_name"],
            "impact_score": round(impact_score, 2),
            "active_signal": active,
            "risk_score": round(risk_score, 2),
            "test_candidates": candidates[:8],
            "test_candidate_count": len(candidates),
        }
        if candidates:
            covered_sources.append(entry)
        else:
            uncovered_sources.append(entry)
        if active and not candidates:
            changed_without_impacted_tests.append(entry)

    orphan_tests = [
        {
            "node_key": test["node_key"],
            "project": test["project"],
            "file": test["relative_path"],
            "base_name": test["base_name"],
            "run_command": generate_test_command(test["relative_path"]),
        }
        for test in test_files
        if test["base_name"] and (test["project"], test["base_name"]) not in source_project_bases
    ]

    uncovered_sources = sorted(uncovered_sources, key=lambda item: (-item["risk_score"], item["file"]))
    covered_sources = sorted(covered_sources, key=lambda item: (-item["risk_score"], item["file"]))
    changed_without_impacted_tests = sorted(changed_without_impacted_tests, key=lambda item: (-item["risk_score"], item["file"]))
    critical_without_tests = _with_surgical_priority(
        [item for item in uncovered_sources if item["risk_score"] >= 50.0 or item.get("active_signal")]
    )
    source_without_test_candidates = _with_surgical_priority(uncovered_sources)
    top_surgical_priorities = sorted(
        critical_without_tests,
        key=lambda item: (
            0 if item.get("priority") == "P0_ACTIVE_UNTESTED_CHANGE" else 1,
            -float(item.get("risk_score", 0.0) or 0.0),
            str(item.get("file") or ""),
        ),
    )[:20]

    return {
        "meta": {
            "kind": "test_gap_report",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_test_gap_report",
        },
        "scope": {
            "default_agent_project_scope": default_project_scope,
            "agent_visible_scope": "default_agent_read_scope",
            "all_project_counts_preserved": True,
            "variation_visibility": "explicit_only",
        },
        "summary": {
            "total_source_files": len(source_files),
            "total_test_files": len(test_files),
            "source_with_test_candidates": len(covered_sources),
            "source_without_test_candidates": len(uncovered_sources),
            "critical_without_tests": len(critical_without_tests),
            "orphan_test_candidates": len(orphan_tests),
            "changed_files_without_impacted_tests": len(changed_without_impacted_tests),
            "coverage_candidate_ratio": round(len(covered_sources) / max(1, len(source_files)), 3),
            "top_surgical_priorities": len(top_surgical_priorities),
        },
        "all_project_summary": {
            "total_source_files": len(all_source_files),
            "total_test_files": len(all_test_files),
            "project_count": len(atlas) if isinstance(atlas, dict) else 0,
        },
        "top_surgical_priorities": top_surgical_priorities,
        "critical_without_tests": critical_without_tests[:100],
        "source_without_test_candidates": source_without_test_candidates[:200],
        "source_with_test_candidates": covered_sources[:100],
        "changed_files_without_impacted_tests": _with_surgical_priority(changed_without_impacted_tests)[:50],
        "orphan_test_candidates": sorted(orphan_tests, key=lambda item: item["file"])[:100],
        "agent_action_plan": [
            "Start with top_surgical_priorities, not the full source_without_test_candidates list.",
            "Treat P0 active untested changes as immediate review/test-selection work.",
            "Use high-blast P1 entries as refactor blockers until characterization coverage exists.",
            "Do not assume every untested candidate is a defect; public API and runtime-only coverage can require human annotation.",
        ],
        "evidence_artifacts": [
            "output/.raw/atlas.json",
            "output/.raw/circular_deps.json",
            "output/.raw/blast_radius.json",
            "output/.raw/signals.json",
        ],
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Test Gap Report v1",
        "",
        f"- total_source_files: `{summary.get('total_source_files')}`",
        f"- total_test_files: `{summary.get('total_test_files')}`",
        f"- source_with_test_candidates: `{summary.get('source_with_test_candidates')}`",
        f"- source_without_test_candidates: `{summary.get('source_without_test_candidates')}`",
        f"- critical_without_tests: `{summary.get('critical_without_tests')}`",
        f"- changed_files_without_impacted_tests: `{summary.get('changed_files_without_impacted_tests')}`",
        f"- coverage_candidate_ratio: `{summary.get('coverage_candidate_ratio')}`",
        f"- top_surgical_priorities: `{summary.get('top_surgical_priorities')}`",
        "",
        "## Top Surgical Priorities",
        "",
        "| Priority | Risk | Impact | Active | Source | Action |",
        "|---|---:|---:|---|---|---|",
    ]
    for item in payload.get("top_surgical_priorities", [])[:20]:
        action = str(item.get("recommended_action", "")).replace("|", "\\|")
        lines.append(
            f"| `{item.get('priority')}` | {item.get('risk_score')} | {item.get('impact_score')} | {item.get('active_signal')} | `{item.get('node_key')}` | {action} |"
        )
    if not payload.get("top_surgical_priorities"):
        lines.append("| n/a | 0 | 0 | false | No surgical test priorities detected. | n/a |")
    lines.extend(
        [
        "",
        "## Highest-Risk Source Files Without Test Candidates",
        "",
        "| Risk | Impact | Active | Source |",
        "|---:|---:|---|---|",
        ]
    )
    for item in payload.get("critical_without_tests", [])[:40]:
        lines.append(
            f"| {item.get('risk_score')} | {item.get('impact_score')} | {item.get('active_signal')} | `{item.get('node_key')}` |"
        )
    if not payload.get("critical_without_tests"):
        lines.append("| 0 | 0 | false | No critical untested source candidates detected. |")

    lines.extend(["", "## Active Changed Files Without Impacted Tests", "", "| Risk | Source |", "|---:|---|"])
    for item in payload.get("changed_files_without_impacted_tests", [])[:30]:
        lines.append(f"| {item.get('risk_score')} | `{item.get('node_key')}` |")
    if not payload.get("changed_files_without_impacted_tests"):
        lines.append("| 0 | No active changed source file is missing impacted test candidates. |")

    lines.extend(["", "## Orphan Test Candidates", "", "| Test | Command |", "|---|---|"])
    for item in payload.get("orphan_test_candidates", [])[:40]:
        lines.append(f"| `{item.get('node_key')}` | `{item.get('run_command')}` |")
    if not payload.get("orphan_test_candidates"):
        lines.append("| No orphan test candidates detected. | n/a |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_test_gap_report()
    save_json_atomic(RAW_DIR / "test_gap_report.json", payload)
    save_text_atomic(REPORTS_DIR / "test_gap_report.md", render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path

from tools import validate_agent_semantic_contract_smoke as smoke


def _clean_queue_payload() -> dict:
    return {
        "status": "clean_within_sage_audit",
        "analysis_root": "C:/target",
        "analysis_snapshot_id": "snapshot-1",
        "total_violations": 0,
        "queue_source": "sqlite_findings",
        "filters": {"project": "MAIN"},
        "coverage": {
            "status": "partial",
            "sage_audit": "evaluated",
            "target_native": "not_evaluated",
            "combined_verdict": "not_available",
            "clean_scope": "sage_audit_only",
        },
        "artifact_trust": {
            "status": "PASS",
            "scope": {"audited_projects": ["MAIN"]},
            "failures": [],
        },
        "authority_projection": {
            "actionability": "no_action",
            "mutation_proposed": False,
            "mutation_authority": "not_granted_by_this_directive",
        },
        "items": [],
    }


def test_clean_sage_audit_queue_requires_complete_bounded_contract():
    report = smoke._clean_sage_audit_queue_contract(_clean_queue_payload())

    assert report["ok"] is True
    assert all(report["expectations"].values())


def test_empty_queue_without_coverage_and_trust_fails_closed():
    report = smoke._clean_sage_audit_queue_contract(
        {"status": "clean_within_sage_audit", "total_violations": 0, "items": []}
    )

    assert report["ok"] is False
    assert report["expectations"]["coverage_status"] is False
    assert report["expectations"]["artifact_trust"] is False


def test_brief_scalar_accepts_canonical_quoted_or_plain_yaml():
    assert smoke._brief_has_scalar('status: "no_actionable_items"', "status", "no_actionable_items")
    assert smoke._brief_has_scalar("status: no_actionable_items", "status", "no_actionable_items")
    assert not smoke._brief_has_scalar('status: "unknown"', "status", "no_actionable_items")


def test_work_queue_and_task_scenario_accept_bounded_clean_projection():
    payload = _clean_queue_payload()
    brief = (
        'status: "clean_within_sage_audit"\n'
        'combined_verdict: "not_available"\n'
        "items:\n  []\n"
    )

    grounding = smoke._work_queue_grounding_report({"payload": payload, "brief": brief})
    scenario = smoke._agent_task_scenario_report({"payload": payload, "brief": brief})

    assert grounding["mode"] == "clean_within_sage_audit"
    assert grounding["issues"] == []
    assert scenario["mode"] == "clean_within_sage_audit"
    assert scenario["issues"] == []


def test_work_queue_and_task_scenario_reject_unproven_empty_projection():
    payload = {"status": "clean_within_sage_audit", "total_violations": 0, "items": []}

    grounding = smoke._work_queue_grounding_report({"payload": payload, "brief": "items:\n  []\n"})
    scenario = smoke._agent_task_scenario_report({"payload": payload, "brief": "items:\n  []\n"})

    assert grounding["issues"][0]["issue"] == "empty_queue_missing_fail_closed_clean_contract"
    assert scenario["issues"][0]["issue"] == "no_work_queue_items_without_clean_contract"


def test_clean_circular_scope_requires_explicit_main_summary(tmp_path: Path, monkeypatch):
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    (raw_dir / "circular_deps.json").write_text(
        '{"cycles": [], "by_project": {"MAIN": {"cycle_count": 0, "has_cycles": false}}, '
        '"meta": {"kind": "circular_dependencies", "execution_scope": {'
        '"analyzed_projects": ["MAIN"], "target_decision_eligible": true}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(smoke, "_cached_mcp_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        smoke,
        "_cached_mcp_text",
        lambda *_args, **_kwargs: "status: no_actionable_items\nfilter: MAIN\nitems:\n",
    )

    report = smoke._scenario_circular_dependency_chain({}, tmp_path, raw_dir)

    assert report["ok"] is True
    assert report["mode"] == "clean_release_scope"


def test_clean_circular_scope_without_main_summary_fails_closed(tmp_path: Path, monkeypatch):
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    (raw_dir / "circular_deps.json").write_text(
        '{"cycles": [], "by_project": {}, "meta": {"kind": "circular_dependencies", '
        '"execution_scope": {"analyzed_projects": ["MAIN"], "target_decision_eligible": true}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(smoke, "_cached_mcp_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        smoke,
        "_cached_mcp_text",
        lambda *_args, **_kwargs: "status: no_actionable_items\nfilter: MAIN\nitems:\n",
    )

    report = smoke._scenario_circular_dependency_chain({}, tmp_path, raw_dir)

    assert report["ok"] is False
    assert report["expectations"]["main_summary"] is False


def test_bidirectional_dependency_target_is_selected_deterministically_by_graph_evidence():
    payload = {
        "edges": [
            {"source": "MAIN::entry.ts", "target": "MAIN::balanced.ts"},
            {"source": "MAIN::balanced.ts", "target": "MAIN::leaf-a.ts"},
            {"source": "MAIN::balanced.ts", "target": "MAIN::leaf-b.ts"},
            {"source": "MAIN::other.ts", "target": "MAIN::balanced.ts"},
            {"source": "MAIN::entry.ts", "target": "MAIN::weak.ts"},
            {"source": "MAIN::weak.ts", "target": "MAIN::leaf-a.ts"},
            {"source": "MAIN::one-sided.ts", "target": "MAIN::leaf-b.ts"},
        ]
    }

    assert smoke._select_bidirectional_dependency_target(payload) == "MAIN::balanced.ts"


def test_bidirectional_dependency_target_fails_closed_without_semantic_candidate():
    payload = {
        "edges": [
            {"source": "MAIN::entry-a.ts", "target": "MAIN::leaf-a.ts"},
            {"source": "MAIN::entry-b.ts", "target": "MAIN::leaf-b.ts"},
            {"source": "MAIN::entry-a.ts", "target": "MAIN::entry-a.ts"},
            {"source": "", "target": "MAIN::leaf-c.ts"},
        ]
    }

    assert smoke._select_bidirectional_dependency_target(payload) == ""

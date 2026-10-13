from __future__ import annotations

import ast
from copy import deepcopy
import inspect
from pathlib import Path

import pytest

from tools import validate_agent_semantic_contract_smoke as smoke
from tools.core import contextos_mcp


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


def test_all_project_search_may_equal_single_project_default_scope():
    coverage = smoke._search_scope_project_coverage(
        [{"project": "MAIN", "file": "src/App.tsx"}],
        [{"project": "MAIN", "file": "src/App.tsx"}],
    )

    assert coverage["default_scope_observed"] is True
    assert coverage["all_scope_preserves_default_projects"] is True
    assert coverage["missing_default_projects"] == []


def test_all_project_search_cannot_omit_default_project():
    coverage = smoke._search_scope_project_coverage(
        [{"project": "MAIN", "file": "src/App.tsx"}],
        [{"project": "WEB", "file": "apps/web/App.tsx"}],
    )

    assert coverage["all_scope_preserves_default_projects"] is False
    assert coverage["missing_default_projects"] == ["MAIN"]


def test_dead_code_family_accepts_explicit_bounded_no_action(monkeypatch, tmp_path: Path):
    brief = smoke.mcp_server._render_supporting_context_brief(
        "Dead Code Context Brief",
        {
            "analysis_root": str(tmp_path),
            "surface": "dead_code",
            "filter": "src",
            "status": "no_actionable_items",
            "items": [],
        },
    )
    monkeypatch.setattr(smoke, "_cached_mcp_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(smoke, "_cached_mcp_text", lambda *_args, **_kwargs: brief)

    report = smoke._scenario_dead_code_summary({}, tmp_path, tmp_path / ".raw")

    assert report["ok"] is True
    assert report["mode"] == "no_actionable_items"
    assert all(report["expectations"].values())


def test_dead_code_family_rejects_silent_empty_result(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(smoke, "_cached_mcp_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        smoke,
        "_cached_mcp_text",
        lambda *_args, **_kwargs: 'status: "no_actionable_items"\nfilter: "src"\nitems:\n  []\n',
    )

    report = smoke._scenario_dead_code_summary({}, tmp_path, tmp_path / ".raw")

    assert report["ok"] is False
    assert report["expectations"]["candidate_evidence_fields_non_applicable"] is False
    assert report["expectations"]["bounded_absence_claim"] is False


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


def _semantic_smoke_predicate(name: str, **context: dict) -> bool:
    """Exercise one actual guard without running unrelated live release consumers."""
    tree = ast.parse(inspect.getsource(smoke.validate_agent_semantic_contract_smoke))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_check" and node.args
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == name
    ]
    assert len(calls) == 1, "The named guard must exist exactly once."
    predicate = compile(ast.Expression(calls[0].args[1]), smoke.__file__, "eval")
    return bool(eval(predicate, {"__builtins__": {"str": str}}, context))


def _target_signal_packet(tmp_path: Path, monkeypatch, *, state: str = "present") -> dict:
    payload = {} if state == "missing" else {
        "summary": {"status": "FAIL" if state == "failed" else "PASS", "contracts": 1},
        "contract_summary": [{"capability_id": "contextos", "emits": "context_packet"}],
    }
    with monkeypatch.context() as scoped:
        scoped.setattr(contextos_mcp, "load_json_file", lambda path, _default: (
            deepcopy(payload) if path.name == "engine_signal_contract_validation.json" else {}
        ))
        return contextos_mcp.build_surgical_operation_packet(
            {"active_signals": []}, project_scope="MAIN", raw_dir=tmp_path,
        )


@pytest.mark.parametrize("state", ["missing", "present", "failed"])
def test_semantic_target_followup_accepts_real_orientation_not_an_artifact_verdict(tmp_path: Path, monkeypatch, state: str):
    packet = _target_signal_packet(tmp_path, monkeypatch, state=state)
    expected_status = {"missing": None, "present": "PASS", "failed": "FAIL"}[state]
    assert packet["engine_signal_contract"]["summary"]["status"] == expected_status
    assert _semantic_smoke_predicate(
        "surgical_packet_marks_contextos_as_focus_not_verdict",
        surgical_packet=packet, contextos_contract=smoke._config_contract_map()["contextos"],
    )


@pytest.mark.parametrize("mutation", [
    "private_tool", "null_tool", "missing_followup", "available", "wrong_reason",
    "missing_boundary", "missing_rule", "verdict_emission", "blocking_gate", "missing_contract",
])
def test_semantic_target_followup_rejects_authority_drift(tmp_path: Path, monkeypatch, mutation: str):
    packet = _target_signal_packet(tmp_path, monkeypatch)
    context = packet["engine_signal_contract"]
    contract = deepcopy(smoke._config_contract_map()["contextos"])
    if mutation == "private_tool":
        context["mcp_tool"] = "get_engine_signal_contracts"
    elif mutation == "null_tool":
        context["mcp_tool"] = None
    elif mutation == "missing_followup":
        context.pop("followup")
    elif mutation == "available":
        context["followup"]["availability"] = "available"
    elif mutation == "wrong_reason":
        context["followup"]["reason"] = "not_generated"
    elif mutation == "missing_boundary":
        context["followup"]["claim_boundary"] = ""
    elif mutation == "missing_rule":
        context["agent_rule"] = ""
    elif mutation == "verdict_emission":
        contract["emits"] = "verdict"
    elif mutation == "blocking_gate":
        contract["quality_gate_effect"] = "block"
    elif mutation == "missing_contract":
        contract = {}
    assert not _semantic_smoke_predicate(
        "surgical_packet_marks_contextos_as_focus_not_verdict",
        surgical_packet=packet, contextos_contract=contract,
    )


def test_semantic_target_followup_does_not_change_private_operator_guard():
    from tools.generate_nexora_operator_packet import _engine_signal_contract_context

    context = _engine_signal_contract_context({"summary": {"status": "PASS"}})
    packet = {"mission_control": {"engine_signal_contract": "PASS"}, "engine_signal_contract": context}
    assert _semantic_smoke_predicate("operator_packet_exposes_engine_signal_contract", operator_packet=packet)
    assert not _semantic_smoke_predicate(
        "surgical_packet_marks_contextos_as_focus_not_verdict",
        surgical_packet=packet, contextos_contract=smoke._config_contract_map()["contextos"],
    )
    context.pop("mcp_tool")
    assert not _semantic_smoke_predicate("operator_packet_exposes_engine_signal_contract", operator_packet=packet)

from tools.engines.ai_task_pack_generator import _proof_contract
from tools.engines.merge_simulation_engine import _confidence_components


def test_merge_confidence_components_and_taskpack_proof_contract():
    package = {
        "closure_truncated": False,
        "harness_plan": {
            "provider_tree": ["AppShell"],
            "i18n_keys": {"required": ["title"]},
            "browser_api_mocks": [],
            "router_mocks": {"route": "/demo"},
        },
    }
    signals = {
        "closure_size": 3,
        "missing_source_files": 0,
        "target_conflicts": 0,
        "unresolved_internal_deps": 0,
        "external_deps": 0,
    }

    confidence = _confidence_components(package, signals, {"status": "ready_to_run"}, 0)
    assert confidence["recommendation"] == "import-now"
    assert "dependency_harness" in confidence["components"]

    proof = _proof_contract({"required_actions": ["run_browser_smoke"], "evidence": {}})
    assert proof["required_proof_commands"]
    assert proof["rollback_criteria"]
    assert proof["human_review_checklist"]


def test_missing_typescript_evidence_cannot_receive_zero_diagnostic_credit():
    package = {
        "closure_truncated": False,
        "harness_plan": {"provider_tree": ["AppShell"]},
        "recommended_gate": "static_gate",
    }
    signals = {
        "closure_size": 1,
        "missing_source_files": 0,
        "target_conflicts": 0,
        "unresolved_internal_deps": 0,
        "external_deps": 0,
    }

    confidence = _confidence_components(package, signals, {"status": "ready_to_run"}, None)

    assert confidence["recommendation"] == "import-with-review"
    assert confidence["score"] <= 74
    assert confidence["components"]["typescript_diagnostics"] == {
        "score": None,
        "status": "unavailable",
        "evidence": {"project_diagnostics": None},
    }

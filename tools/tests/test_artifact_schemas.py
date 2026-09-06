import json

from tools.core.artifact_validator import (
    ARTIFACT_PATHS,
    ARTIFACT_SCHEMAS,
    validate_against_schema,
    validate_payload,
)


def test_nested_validation_errors_preserve_precise_paths(tmp_path):
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": False,
                },
            }
        },
    }
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    errors = validate_against_schema(
        schema_path,
        "sample",
        {"items": [{"name": 42, "extra": True}]},
    )

    assert errors == [
        "sample.items[0].name: expected string, got integer",
        "sample.items[0]: unexpected key 'extra'",
    ]


def _minimal_audit_report(violation):
    return {
        "meta": {"kind": "audit_report", "version": "test"},
        "summary": {
            "total": 1,
            "by_rule": {violation["rule"]: 1},
            "audit_scope": {},
            "rule_taxonomy": {},
            "remediation_backlog": [],
        },
        "audit_scope": {
            "atlas_project_count": 1,
            "audited_project_count": 1,
            "audited_projects": ["MAIN"],
            "violation_project_count": 1,
        },
        "atlas_project_count": 1,
        "audited_project_count": 1,
        "audited_projects": ["MAIN"],
        "violation_project_count": 1,
        "violations": [violation],
        "report_sections": [],
    }


def test_audit_schema_conditionally_requires_loc_semantics():
    base = {
        "project": "MAIN",
        "file": "src/example.ts",
        "rule": "loc_limits_symbol",
        "detail": "Oversized function.",
        "recommended_action": "Review responsibilities.",
    }
    errors = validate_payload("audit_report", _minimal_audit_report(base))
    assert any("symbol_kind" in error for error in errors)

    valid = {
        **base,
        "symbol_name": "buildExample",
        "symbol_kind": "function",
        "threshold_kind": "function",
        "start_line": 1,
        "end_line": 151,
        "observed_loc": 151,
        "limit": 150,
    }
    assert validate_payload("audit_report", _minimal_audit_report(valid)) == []


def test_new_react_and_merge_artifacts_have_schema_mappings():
    for name in (
        "atlas_commit",
        "derived_graph_integrity_validation",
        "react_compiler_readiness",
        "pipeline_step_registry",
        "merge_simulation",
        "ai_task_packs",
        "test_impact_report",
        "telemetry_traces",
        "confidence_risk_report",
        "local_telemetry_analysis",
        "exception_honesty_validation",
        "agent_harness_readiness",
    ):
        assert name in ARTIFACT_SCHEMAS
        assert name in ARTIFACT_PATHS


def test_agent_harness_readiness_schema_preserves_claim_boundaries():
    payload = {
        "meta": {
            "kind": "agent_harness_readiness",
            "version": "v1",
            "generated_at": "2026-07-13T00:00:00+00:00",
            "generator": "tools.generate_agent_harness_readiness",
            "framework": "execution",
        },
        "summary": {
            "status": "PASS_WITH_BOUNDARIES",
            "evidence_status": "PASS",
            "support_status": "PARTIAL_HARNESS_CONTROL_PLANE",
            "total_layers": 1,
            "validated_layers": 1,
            "failed_layers": [],
            "bounded_layers": ["execution"],
            "mcp_tools": 1,
            "mcp_tools_by_category": {"execution": 1},
            "mcp_tools_by_permission": {"read_only": 1},
            "artifact_projection_reduction_percent": 0.0,
            "institutional_memory_portable": True,
            "model_agnostic_control_plane": True,
            "private_eval_learning_loop": False,
            "evaluation_claim_boundary": "private evaluation is not active",
            "evaluation_baseline_status": "design_ready_not_executing",
            "evaluation_baseline_opt_in": True,
        },
        "positioning": {
            "product_category": "control plane",
            "not_claimed": "not a runtime",
            "meaning": "grounded context",
            "model_swap_rule": "switch model",
            "human_capital_rule": "human authority",
        },
        "layers": [
            {
                "id": "execution",
                "purpose": "bounded execution",
                "support_level": "external_dependency",
                "evidence_policy": "validated",
                "claim_boundary": "not a sandbox",
                "evidence": ["pipeline.json"],
                "status_basis": ["pipeline"],
                "evidence_state": [
                    {
                        "artifact": "pipeline.json",
                        "exists": True,
                        "validated": True,
                        "required_for_release": True,
                        "status": "PASS",
                    }
                ],
                "evidence_status": "PASS",
            }
        ],
        "mcp_tool_readiness": [{"name": "inspect_file"}],
        "context_value": {
            "measurement_boundary": "deterministic estimate",
            "raw_artifacts": [],
            "distilled_artifacts": [],
            "raw_bytes": 0,
            "distilled_bytes": 0,
            "raw_estimated_tokens": 0,
            "distilled_estimated_tokens": 0,
            "estimated_artifact_projection_reduction_ratio": 0.0,
            "estimated_artifact_projection_reduction_percent": 0.0,
        },
    }

    assert ARTIFACT_SCHEMAS["agent_harness_readiness"].name == "agent_harness_readiness.schema.json"
    assert validate_payload("agent_harness_readiness", payload) == []

    del payload["layers"][0]["claim_boundary"]
    assert validate_payload("agent_harness_readiness", payload)


def test_react_compiler_readiness_schema_accepts_observation_lane():
    payload = {
        "meta": {"kind": "react_compiler_readiness", "version": "v1"},
        "summary": {
            "findings": 1,
            "files_analyzed": 1,
            "ready": 0,
            "observe": 1,
            "review": 0,
            "blocked": 0,
            "status_counts": {"observe": 1},
            "dimension_counts": {"react_compiler": 1},
            "runtime_proof_status_counts": {"reference_only": 1},
            "by_project": {"MAIN": 1},
        },
        "policy": {
            "compiler_dimensions": ["react_compiler"],
            "blocking_risks": ["render_purity"],
            "review_risks": ["memo_dependency"],
        },
        "findings": [
            {
                "project": "MAIN",
                "file": "src/example.tsx",
                "line": 1,
                "source_artifact": "react_runtime_intelligence",
                "dimension": "react_compiler",
                "risk": "reference_example",
                "risk_tier": "low",
                "confidence": "reference_only",
                "score": 0,
                "readiness_status": "observe",
                "evidence_kinds": ["source"],
                "evidence_ladder": ["reference_only"],
                "runtime_proof_status": "reference_only",
                "recommended_action": "Observe only.",
            }
        ],
    }

    assert validate_payload("react_compiler_readiness", payload) == []


def test_phase5_artifacts_have_strict_schema_contracts():
    assert ARTIFACT_SCHEMAS["test_impact_report"].name == "test_impact_report.schema.json"
    assert ARTIFACT_SCHEMAS["telemetry_traces"].name == "telemetry_traces.schema.json"
    assert ARTIFACT_SCHEMAS["confidence_risk_report"].name == "confidence_risk_report.schema.json"
    assert ARTIFACT_SCHEMAS["local_telemetry_analysis"].name == "local_telemetry_analysis.schema.json"
    assert ARTIFACT_SCHEMAS["exception_honesty_validation"].name == "exception_honesty_validation.schema.json"

    valid_test_impact = {
        "target": "MAIN::src/demo.ts",
        "impacted_tests": [
            {
                "file": "src/demo.test.ts",
                "project": "MAIN",
                "type": "Semantic Convention Match",
                "confidence": 0.8,
                "run_command": "pnpm test src/demo.test.ts",
            }
        ],
    }
    assert validate_payload("test_impact_report", valid_test_impact) == []
    assert validate_payload("test_impact_report", {"target": "MAIN::src/demo.ts"})

    valid_traces = {
        "meta": {"kind": "local_telemetry_traces", "trace_origin_policy": "local only"},
        "summary": {
            "status": "ATTENTION",
            "status_reason": "Local runtime telemetry contains 1 traces across 1 hot paths.",
            "trace_count": 1,
            "hot_path_count": 1,
        },
        "traces": [
            {
                "timestamp": "2026-05-17T00:00:00Z",
                "type": "component",
                "identifier": "MAIN::src/main.tsx",
                "execution_ms": 12,
                "trace_origin": "local",
            }
        ],
        "hot_paths": {"MAIN::src/main.tsx": 1},
    }
    assert validate_payload("telemetry_traces", valid_traces) == []
    assert validate_payload("telemetry_traces", {"traces": [{}], "hot_paths": {}})

    valid_confidence = {
        "target": "MAIN::src/demo.ts",
        "confidence_matrix": {
            "dead_code_confidence": 0.97,
            "merge_safety": "SAFE",
            "architecture_drift_certainty": 0.1,
            "dynamic_magic_hazard": 0.0,
        },
        "metrics": {
            "loc": 1,
            "blast_radius_dependents": 0,
            "cyclic_member": False,
            "reflection_indicators_found": [],
            "risk_mitigation_reasons": ["clean"],
        },
        "verdict": "Low Risk Level",
    }
    assert validate_payload("confidence_risk_report", valid_confidence) == []

    valid_telemetry_analysis = {
        "mapped_hotpaths": [
            {
                "identifier": "MAIN::src/main.tsx",
                "mapped_node": "MAIN::src/main.tsx",
                "hits": 1,
                "avg_ms": 12.0,
            }
        ],
        "untriggered_nodes_count": 0,
        "untriggered_nodes_sample": [],
        "total_captured_traces": 1,
        "trace_origin_summary": {"local": 1},
        "coverage_gap_label": "local_session_coverage_gap",
    }
    assert validate_payload("local_telemetry_analysis", valid_telemetry_analysis) == []

    valid_exception_honesty = {
        "meta": {"kind": "exception_honesty_validation", "version": "v1"},
        "summary": {
            "status": "PASS",
            "handlers": 1,
            "observed": 1,
            "allowed_quiet": 0,
            "unobserved": 0,
            "blocking_unobserved_generic": 0,
        },
        "principles": {"critical_rule": "log critical generic exceptions"},
        "top_unobserved_by_file": [],
        "blocking": [],
        "findings": [
            {
                "file": "tools/core/example.py",
                "line": 10,
                "function": "example",
                "status": "observed",
                "critical": True,
                "generic_exception": True,
            }
        ],
    }
    assert validate_payload("exception_honesty_validation", valid_exception_honesty) == []
    assert validate_payload("exception_honesty_validation", {"summary": {}})

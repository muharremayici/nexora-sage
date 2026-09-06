from __future__ import annotations

import json

from tools import validate_contextos_signals as validator


def _partial_empty_signal_payload() -> dict:
    evidence = {
        name: {
            "status": "available",
            "source": "sqlite_state_payloads",
            "shape_status": "valid",
            "payload_bytes": 1,
            "updated_at": "2026-08-25T00:00:00+00:00",
        }
        for name in ("circular_deps", "blast_radius", "audit_report")
    }
    evidence["change_scope"] = {
        "status": "unavailable",
        "source": "git_changed_file_probes",
        "shape_status": "not_evaluated",
        "payload_bytes": 0,
        "updated_at": "2026-08-25T00:00:00+00:00",
    }
    return {
        "meta": {
            "kind": "contexts_active_signals",
            "version": "v1",
            "generator": "tools.engines.quant_engine",
            "source_mode": "git_fallback",
            "input_evidence_status": "PARTIAL",
            "current_change_scope": "unknown",
            "current_turn_claim": "not_established",
            "signal_origin": "git_changed_file_probes",
        },
        "summary": {
            "candidate_files": 0,
            "accepted_files": 0,
            "rejected_files": {
                "non_file": 0,
                "unsupported_kind": 0,
                "managed_projection": 0,
                "duplicate": 0,
            },
            "source_files": 0,
            "config_files": 0,
            "input_evidence_status": "PARTIAL",
            "unavailable_inputs": ["change_scope"],
            "deferred_inputs": [],
        },
        "input_evidence": evidence,
        "changed_files_count": 0,
        "active_signals": [],
    }


def test_partial_empty_projection_passes_without_prose_coupling(tmp_path, monkeypatch) -> None:
    raw_dir = tmp_path / "raw"
    reports_dir = tmp_path / "reports"
    raw_dir.mkdir()
    reports_dir.mkdir()
    signals_path = raw_dir / "signals.json"
    signals_path.write_text(json.dumps(_partial_empty_signal_payload()), encoding="utf-8")

    monkeypatch.setattr(validator, "RAW_DIR", raw_dir)
    monkeypatch.setattr(validator, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(validator, "SIGNALS_PATH", signals_path)

    result = validator.run_validation()

    assert result["summary"] == {"total_checks": 15, "passed_checks": 15, "failed_checks": 0}
    boundary = next(
        check
        for check in result["checks"]
        if check["name"] == "agent_projection_preserves_current_change_scope_boundary"
    )
    assert boundary["passed"] is True
    markdown = validator.render_active_signals(
        _partial_empty_signal_payload(),
        output_format="markdown",
        include_bodies=False,
        max_files=8,
        max_chars_per_file=0,
        scope="summary",
        resolve_absolute_path=lambda rel_path, project_key: tmp_path / rel_path,
    )
    assert "current-turn scope is unknown" in markdown


def test_scope_projection_comparison_rejects_structured_mismatch() -> None:
    assert validator._projection_preserves_scope_boundary(
        {"current_change_scope": "unknown", "current_turn_claim": "not_established"},
        current_change_scope="unknown",
        current_turn_claim="not_established",
    )
    assert not validator._projection_preserves_scope_boundary(
        {"current_change_scope": "bounded", "current_turn_claim": "supported"},
        current_change_scope="unknown",
        current_turn_claim="not_established",
    )

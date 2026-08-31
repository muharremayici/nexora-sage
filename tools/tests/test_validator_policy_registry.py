import json
import os

import pytest

from tools.core import validator_policy_registry
from tools.core.json_io import clear_strict_json_content_cache, strict_json_content_cache_metrics
from tools.validate_validator_policy_registry import run_validation


def test_validator_policy_registry_is_live_and_validated():
    assert validator_policy_registry.load_validator_policy_registry()["meta"]["kind"] == "validator_policy_registry"
    assert run_validation()["summary"]["status"] == "PASS"


def test_validator_policy_registry_refreshes_after_same_mtime_content_change(tmp_path, monkeypatch):
    registry_path = tmp_path / "validator_policy_registry.json"
    payload = json.loads(validator_policy_registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(validator_policy_registry, "REGISTRY_PATH", registry_path)
    assert "gate" in validator_policy_registry.hardcoded_decision_inventory_policy()["decision_words"]

    original = registry_path.stat()
    payload["hardcoded_decision_inventory"]["decision_words"].append("governance")
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(registry_path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert "governance" in validator_policy_registry.hardcoded_decision_inventory_policy()["decision_words"]


def test_validator_policy_registry_reuses_normalized_content_on_cache_hit():
    clear_strict_json_content_cache()

    validator_policy_registry.load_validator_policy_registry()
    validator_policy_registry.load_validator_policy_registry()
    metrics = strict_json_content_cache_metrics()

    assert metrics["reads"] == 2
    assert metrics["misses"] == 1
    assert metrics["hits"] == 1
    assert metrics["bytes_hashed"] == validator_policy_registry.REGISTRY_PATH.stat().st_size * 2


def test_validator_policy_registry_fails_closed_for_overlapping_triage_groups(tmp_path, monkeypatch):
    registry_path = tmp_path / "validator_policy_registry.json"
    payload = json.loads(validator_policy_registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    payload["hardcoded_decision_inventory"]["triage_actions"]["bulk_deferred"].append("must_fix")
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(validator_policy_registry, "REGISTRY_PATH", registry_path)
    with pytest.raises(ValueError, match="overlap"):
        validator_policy_registry.load_validator_policy_registry()


def test_validator_policy_registry_fails_closed_for_invalid_decision_table_threshold(tmp_path, monkeypatch):
    registry_path = tmp_path / "validator_policy_registry.json"
    payload = json.loads(validator_policy_registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    payload["hardcoded_decision_inventory"]["decision_table_min_rows"] = 1
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(validator_policy_registry, "REGISTRY_PATH", registry_path)

    with pytest.raises(ValueError, match="at least 2"):
        validator_policy_registry.load_validator_policy_registry()


def test_validator_policy_registry_rejects_decision_table_threshold_above_marker_count(tmp_path, monkeypatch):
    registry_path = tmp_path / "validator_policy_registry.json"
    payload = json.loads(validator_policy_registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    marker_count = len(payload["hardcoded_decision_inventory"]["decision_table_row_field_markers"])
    payload["hardcoded_decision_inventory"]["decision_table_min_matching_fields"] = marker_count + 1
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(validator_policy_registry, "REGISTRY_PATH", registry_path)

    with pytest.raises(ValueError, match="exceeds marker count"):
        validator_policy_registry.load_validator_policy_registry()

from pathlib import Path

import pytest

from tools import validate_large_artifact_sqlite_first_access as validator



def test_guard_rejects_registry_resolved_external_shadow_read() -> None:
    source = """
def build(raw_dir, artifact_id):
    path = artifact_path_for_storage_root(raw_dir, artifact_id)
    return load_json_strict(path)
"""
    findings = validator._external_raw_consumer_findings(Path.cwd() / "tools/example.py", source)
    assert [row["kind"] for row in findings] == ["external_raw_shadow_only_read"]


def test_guard_accepts_sqlite_first_external_raw_read() -> None:
    source = """
def build(raw_dir, artifact_id):
    path = artifact_path_for_storage_root(raw_dir, artifact_id)
    return load_raw_artifact_path(path)
"""
    assert validator._external_raw_consumer_findings(Path.cwd() / "tools/example.py", source) == []


def test_large_artifact_scan_recognizes_central_sqlite_first_loader() -> None:
    source = "payload = load_raw_artifact_path(RAW_DIR / 'ui_runtime_contracts.json', {})"

    result = validator._scan_file(Path.cwd() / "tools/example.py", source)

    assert [row["kind"] for row in result["ui_runtime_contracts.json"]["proxy_loads"]] == [
        "load_raw_artifact_path"
    ]


def test_state_flow_review_has_artifact_scoped_primary_access() -> None:
    consumer = "tools/core/agent_surface_review_portability.py"
    path = validator.ROOT / consumer
    artifact = "state_flow.json"
    assert consumer not in validator.ALLOWED_REFERENCE_FILES
    allowed_artifacts = {name for name, policy in validator.ARTIFACTS.items() if consumer in policy["hot_consumers"]}
    assert allowed_artifacts == {artifact}
    scan = validator._scan_file(path)[artifact]
    assert [row["kind"] for row in scan["proxy_loads"]] == ["load_raw_artifact_primary_with_identity"]
    assert not scan["bad_direct_reads"]


@pytest.mark.parametrize(("bindings", "call", "accepted"), [
    ("from tools.core.json_io import load_raw_artifact_primary_with_identity as read", "read", True),
    ("from elsewhere import load_raw_artifact_primary_with_identity", "load_raw_artifact_primary_with_identity", False),
    ("from tools.core.json_io import load_raw_artifact_primary_with_identity as read\nread = other_reader", "read", False),
    ("from tools.core.json_io import load_raw_artifact_primary_with_identity as read", "factory().read", False),
])
def test_large_guard_counts_only_bound_primary_reader(bindings, call, accepted) -> None:
    source = f"{bindings}\nvalue = {call}(raw / 'state_flow.json')\n"
    scan = validator._scan_file(validator.ROOT / "tools/example.py", source)["state_flow.json"]
    assert bool(scan["proxy_loads"]) is accepted


def test_valid_primary_read_does_not_hide_direct_json_in_same_consumer() -> None:
    source = """
from tools.core.json_io import load_raw_artifact_primary_with_identity
entry = load_raw_artifact_primary_with_identity(raw / 'state_flow.json')
shadow = json.loads((raw / 'state_flow.json').read_text())
"""
    path = validator.ROOT / "tools/core/agent_surface_review_portability.py"
    scan = validator._scan_file(path, source)["state_flow.json"]
    assert len(scan["proxy_loads"]) == 1
    assert {row["kind"] for row in scan["bad_direct_reads"]} == {"read_text", "json.loads"}

from pathlib import Path

from tools.validate_large_artifact_sqlite_first_access import _external_raw_consumer_findings


def test_guard_rejects_registry_resolved_external_shadow_read() -> None:
    source = """
def build(raw_dir, artifact_id):
    path = artifact_path_for_storage_root(raw_dir, artifact_id)
    return load_json_strict(path)
"""
    findings = _external_raw_consumer_findings(Path.cwd() / "tools/example.py", source)
    assert [row["kind"] for row in findings] == ["external_raw_shadow_only_read"]


def test_guard_accepts_sqlite_first_external_raw_read() -> None:
    source = """
def build(raw_dir, artifact_id):
    path = artifact_path_for_storage_root(raw_dir, artifact_id)
    return load_raw_artifact_path(path)
"""
    assert _external_raw_consumer_findings(Path.cwd() / "tools/example.py", source) == []

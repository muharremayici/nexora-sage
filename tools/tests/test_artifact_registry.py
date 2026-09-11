import json
import os
import sqlite3
import hashlib
import pytest
from unittest.mock import patch

from tools.core import artifact_validator
from tools.core import artifact_registry
from tools.core.artifact_registry import ARTIFACT_PATHS, ARTIFACT_SCHEMAS, mandatory_artifact_ids
from tools.validate_artifact_registry import run


@pytest.mark.parametrize('partitioned', [False, True])
def test_required_target_artifact_uses_sqlite_without_shadow(tmp_path, partitioned):
    raw = tmp_path / '.raw'
    raw.mkdir()
    payload = {'EMPTY': {
        'project': {'name': 'empty', 'language': 'typescript', 'framework': 'none', 'root': '.',
                    'sequencer_evidence': {'scope': 'fixture', 'repository_wide_claim': False, 'claim_boundary': 'empty fixture', 'coverage': {}}},
        'structure': {}, 'files': {}, 'dependencies': {}, 'symbols': [], 'features': {}, 'clusters': {}, 'public_contracts': {},
    }}
    with sqlite3.connect(raw / 'codemaps.db') as connection:
        if partitioned:
            data = json.dumps(payload).encode('utf-8')
            digest = hashlib.sha256(data).hexdigest()
            connection.execute('CREATE TABLE state_payloads (name TEXT, payload TEXT, payload_sha TEXT, payload_bytes INTEGER, storage_mode TEXT, generation_id TEXT, part_count INTEGER)')
            connection.execute('CREATE TABLE state_payload_parts (name TEXT, generation_id TEXT, part_index INTEGER, payload BLOB, payload_bytes INTEGER, payload_sha TEXT)')
            connection.execute('INSERT INTO state_payloads VALUES (?, ?, ?, ?, ?, ?, ?)', ('atlas', '{}', digest, len(data), 'partitioned_json_v1', 'fixture', 1))
            connection.execute('INSERT INTO state_payload_parts VALUES (?, ?, ?, ?, ?, ?)', ('atlas', 'fixture', 0, data, len(data), digest))
        else:
            connection.execute('CREATE TABLE state_payloads (name TEXT, payload TEXT, payload_sha TEXT)')
            connection.execute('INSERT INTO state_payloads VALUES (?, ?, ?)', ('atlas', json.dumps(payload), None))
    assert not (raw / 'atlas.json').exists()
    assert artifact_validator.validate_required_artifacts(['atlas'], storage_root=raw) == {'atlas': []}
    if partitioned:
        with sqlite3.connect(raw / 'codemaps.db') as connection:
            connection.execute('DELETE FROM state_payload_parts')
        assert artifact_validator.validate_required_artifacts(['atlas'], storage_root=raw)['atlas']


def test_required_target_artifact_rejects_missing_invalid_and_unknown(tmp_path):
    raw = tmp_path / '.raw'
    raw.mkdir()
    assert artifact_validator.validate_required_artifacts(['atlas'], storage_root=raw)['atlas']
    (raw / 'atlas.json').write_text('{', encoding='utf-8')
    assert artifact_validator.validate_required_artifacts(['atlas'], storage_root=raw)['atlas']
    (raw / 'atlas.json').write_text('[]', encoding='utf-8')
    assert artifact_validator.validate_required_artifacts(['atlas'], storage_root=raw)['atlas']
    assert artifact_validator.validate_required_artifacts(['unknown_artifact'], storage_root=raw)['unknown_artifact']


def test_required_target_does_not_borrow_registered_global_artifact(tmp_path):
    global_file = tmp_path / 'atlas.json'
    global_file.write_text('{}', encoding='utf-8')
    assert artifact_validator.validate_payload('atlas', {}) == []
    raw = tmp_path / 'target' / '.raw'
    raw.mkdir(parents=True)
    with patch.object(artifact_validator, 'ARTIFACT_PATHS', {'atlas': global_file}):
        assert artifact_validator.validate_required_artifacts(['atlas'], storage_root=raw)['atlas']


def test_scoped_required_validation_retains_other_artifact_schema_sweep(tmp_path):
    other = tmp_path / 'other.json'
    other.write_text('{}', encoding='utf-8')
    with (
        patch.object(artifact_validator, 'ARTIFACT_PATHS', {'atlas': tmp_path / 'missing.json', 'other': other}),
        patch.object(artifact_validator, 'validate_required_artifacts', return_value={'atlas': []}) as required,
        patch.object(artifact_validator, 'validate_artifact_file', return_value=['other invalid']) as validate,
    ):
        result = artifact_validator.validate_all_artifacts(required_artifacts=['atlas'], storage_root=tmp_path)
    required.assert_called_once_with(['atlas'], storage_root=tmp_path)
    validate.assert_called_once_with('other')
    assert result == {'atlas': [], 'other': ['other invalid']}
    with pytest.raises(ValueError):
        artifact_validator.validate_all_artifacts(required_artifacts=['atlas'])


def test_declarative_artifact_registry_supplies_shared_views():
    assert ARTIFACT_PATHS["atlas"].as_posix().endswith("output/.raw/atlas.json")
    assert ARTIFACT_SCHEMAS["atlas"].name == "atlas.schema.json"
    assert {"atlas", "genome"} <= mandatory_artifact_ids()


def test_artifact_registry_validator_passes_for_current_contract():
    assert run()["summary"]["status"] == "PASS"


def test_managed_runtime_validation_uses_sqlite_first_loader():
    payload = {"MAIN": {}}
    with (
        patch(
            "tools.core.json_io.load_raw_artifact_path_strict",
            return_value=payload,
        ) as loader,
        patch.object(artifact_validator, "validate_payload", return_value=[]) as validate,
    ):
        assert artifact_validator.validate_artifact_file("atlas") == []

    loader.assert_called_once_with(ARTIFACT_PATHS["atlas"])
    validate.assert_called_once_with("atlas", payload)


def test_registry_view_refreshes_after_same_mtime_content_change(tmp_path, monkeypatch):
    registry_path = tmp_path / "artifact_registry.json"
    payload = {
        "meta": {"kind": "artifact_registry"},
        "path_contract": {
            "storage_roots": {
                "managed_runtime_artifact": "output/.raw/",
            }
        },
        "row_contract": {
            "required_fields": ["id", "schema", "path", "availability", "storage_class"],
            "availability_values": ["mandatory", "optional"],
        },
        "artifacts": [
            {
                "id": "atlas",
                "schema": "config/schemas/atlas.schema.json",
                "path": "output/.raw/atlas.json",
                "availability": "mandatory",
                "storage_class": "managed_runtime_artifact",
            }
        ],
    }
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(artifact_registry, "REGISTRY_PATH", registry_path)
    assert artifact_registry.mandatory_artifact_ids() == {"atlas"}

    original = registry_path.stat()
    payload["artifacts"][0]["availability"] = "optional"
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(registry_path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert artifact_registry.mandatory_artifact_ids() == set()

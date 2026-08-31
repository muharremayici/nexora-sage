import json
import os
from unittest.mock import patch

from tools.core import artifact_validator
from tools.core import artifact_registry
from tools.core.artifact_registry import ARTIFACT_PATHS, ARTIFACT_SCHEMAS, mandatory_artifact_ids
from tools.validate_artifact_registry import run


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

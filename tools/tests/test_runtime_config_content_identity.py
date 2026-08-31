from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import codemaps
from tools import config_compiler
from tools.core import config as runtime_config
from tools.core.runtime_config_identity import (
    compiler_identity_contract,
    runtime_config_content_identity,
    runtime_config_needs_compile,
)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _runtime_payload(discovery: dict, overrides: dict) -> dict:
    payload = {
        "architecture": {"type": "modular", "module_root": "src"},
        "audit": {"rules": {}},
        "environment": {"path_aliases": {}},
        "host_intelligence": {},
        "project_display_names": {"MAIN": "Main"},
        "quality_gates": {"max_dead_code_total": 20},
        "use_sqlite": True,
        "variations": {"MAIN": "../src"},
        "_meta": {"kind": "codemaps.config", "last_validated": "volatile"},
    }
    payload["_provenance"] = runtime_config_content_identity(
        discovery,
        overrides,
        payload,
    )
    return payload


def test_runtime_config_detects_same_mtime_source_content_change(tmp_path: Path) -> None:
    discovery_path = tmp_path / "codemaps.discovery.json"
    overrides_path = tmp_path / "codemaps.overrides.json"
    config_path = tmp_path / "codemaps.config.json"
    discovery = {"workspace_root": "../one", "variations": {"MAIN": "../src"}}
    overrides = {"use_sqlite": True}
    _write(discovery_path, discovery)
    _write(overrides_path, overrides)
    _write(config_path, _runtime_payload(discovery, overrides))

    assert not runtime_config_needs_compile(
        discovery_path,
        overrides_path,
        config_path,
    )
    original = discovery_path.stat()
    discovery["workspace_root"] = "../two"
    _write(discovery_path, discovery)
    os.utime(discovery_path, ns=(original.st_atime_ns, original.st_mtime_ns))

    assert runtime_config_needs_compile(
        discovery_path,
        overrides_path,
        config_path,
    )


def test_cli_runtime_truth_status_uses_content_identity_not_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovery_path = tmp_path / "codemaps.discovery.json"
    overrides_path = tmp_path / "codemaps.overrides.json"
    config_path = tmp_path / "codemaps.config.json"
    discovery = {"workspace_root": "../one", "variations": {"MAIN": "../src"}}
    overrides = {"use_sqlite": True}
    _write(discovery_path, discovery)
    _write(overrides_path, overrides)
    _write(config_path, _runtime_payload(discovery, overrides))
    monkeypatch.setattr(codemaps, "DISCOVERY_FILE", discovery_path)
    monkeypatch.setattr(codemaps, "OVERRIDES_FILE", overrides_path)
    monkeypatch.setattr(codemaps, "CONFIG_FILE", config_path)

    assert codemaps._runtime_truth_status()["freshness_reason"] == "content_identity_match"
    original = discovery_path.stat()
    discovery["workspace_root"] = "../two"
    _write(discovery_path, discovery)
    os.utime(discovery_path, ns=(original.st_atime_ns, original.st_mtime_ns))

    status = codemaps._runtime_truth_status()
    assert status["stale_config"] is True
    assert status["freshness_reason"] == "content_identity_mismatch"


def test_runtime_config_identity_detects_baseline_change_but_ignores_volatile_meta(tmp_path: Path) -> None:
    discovery_path = tmp_path / "codemaps.discovery.json"
    overrides_path = tmp_path / "codemaps.overrides.json"
    config_path = tmp_path / "codemaps.config.json"
    discovery = {"workspace_root": "../repo", "variations": {"MAIN": "../src"}}
    overrides = {"use_sqlite": True}
    payload = _runtime_payload(discovery, overrides)
    _write(discovery_path, discovery)
    _write(overrides_path, overrides)
    _write(config_path, payload)

    payload["_meta"]["last_validated"] = "changed-but-excluded"
    _write(config_path, payload)
    assert not runtime_config_needs_compile(
        discovery_path,
        overrides_path,
        config_path,
    )

    payload["audit"]["rules"] = {"new_rule": {"enabled": True}}
    _write(config_path, payload)
    assert runtime_config_needs_compile(
        discovery_path,
        overrides_path,
        config_path,
    )


def test_compiled_runtime_identity_is_stable_after_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovery_path = tmp_path / "codemaps.discovery.json"
    overrides_path = tmp_path / "codemaps.overrides.json"
    config_path = tmp_path / "codemaps.config.json"
    discovery = {
        "_meta": {"kind": "codemaps.discovery", "workspace_id": "fixture"},
        "workspace_root": "../repo",
        "source_extensions": [".ts", ".tsx"],
        "skip_dirs": ["node_modules"],
        "project_roles": {"MAIN": "host"},
        "variations": {"MAIN": "../src"},
        "_repository_topology": {
            "ontology_contract": "canonical_repository_topology_v1",
            "project_ownership_exclusions": {"MAIN": ["embedded"]},
            "file_ownership_contract": "nearest_discovered_project_root_v1",
        },
        "architecture": {"type": "modular", "module_root": "src"},
        "environment": {"path_aliases": {}},
        "plugins": [],
    }
    overrides = {
        "_meta": {"workspace_id": "fixture"},
        "use_sqlite": True,
        "quality_gates": {
            "max_manual_review": 20,
            "manual_review_budget_baseline": {
                "observed_items": 7,
                "reviewed_suppressions": 0,
            },
        },
    }
    baseline = _runtime_payload(discovery, overrides)
    _write(discovery_path, discovery)
    _write(overrides_path, overrides)
    _write(config_path, baseline)
    monkeypatch.setattr(config_compiler, "DISCOVERY_PATH", discovery_path)
    monkeypatch.setattr(config_compiler, "OVERRIDES_PATH", overrides_path)
    monkeypatch.setattr(config_compiler, "CONFIG_PATH", config_path)

    compiled = config_compiler.compile_runtime_config()
    _write(config_path, compiled)

    assert compiled["_provenance"]["source_content_fingerprint"]
    assert compiled["_provenance"]["runtime_baseline_fingerprint"]
    assert "max_manual_review" not in compiled["quality_gates"]
    assert compiled["quality_gates"]["manual_review_budget_baseline"]["observed_items"] == 7
    assert compiled["_repository_topology"] == discovery["_repository_topology"]
    workspace_key_order = [
        key
        for key in compiled
        if key in {"workspace_root", "source_extensions", "project_roles", "skip_dirs"}
    ]
    assert workspace_key_order == ["workspace_root", "source_extensions", "project_roles", "skip_dirs"]
    assert not runtime_config_needs_compile(discovery_path, overrides_path, config_path)


def test_failed_required_runtime_compile_does_not_load_stale_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_config, "_runtime_config_needs_compile", lambda: True)
    monkeypatch.setattr(runtime_config, "_compile_runtime_config_file", lambda: False)

    with pytest.raises(RuntimeError, match="stale runtime truth was not loaded"):
        runtime_config.load_runtime_config(auto_compile=True)


def test_incomplete_runtime_identity_contract_fails_closed(tmp_path: Path) -> None:
    contract_path = tmp_path / "runtime_config_compiler_contract.json"
    _write(
        contract_path,
        {
            "_meta": {"kind": "nexora.runtime_config_compiler_contract"},
            "identity": {"algorithm": "sha256_json_canonical_v1"},
            "validation": {
                "required_identity_fields": ["algorithm", "baseline_owned_keys"],
                "required_provenance_fields": ["source_fingerprint", "baseline_fingerprint"],
            },
        },
    )

    with pytest.raises(ValueError, match="identity contract is incomplete"):
        compiler_identity_contract(contract_path)

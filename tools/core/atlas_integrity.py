from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ATLAS_COMMIT_KIND = "nexora.atlas_commit"
ATLAS_COMMIT_VERSION = "v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _sha256_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted((item for item in paths if item.exists() and item.is_file()), key=lambda item: item.as_posix()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def configuration_fingerprint() -> str:
    from tools.core.config import (
        CONFIG_FILE,
        DISCOVERY_FILE,
        DOCTRINE_FILE,
        DOCTRINE_MANIFEST_FILE,
        OVERRIDES_FILE,
    )
    return _sha256_files(
        [CONFIG_FILE, DISCOVERY_FILE, OVERRIDES_FILE, DOCTRINE_FILE, DOCTRINE_MANIFEST_FILE]
    )


def generator_fingerprint() -> str:
    root = Path(__file__).resolve().parents[2]
    return _sha256_files(
        [
            root / "tools" / "engines" / "generate_atlas.py",
            root / "tools" / "engines" / "ast_sequencer.cjs",
            root / "tools" / "engines" / "ast_sequencer_python.py",
            root / "tools" / "engines" / "ast_sequencer_java.py",
            root / "tools" / "engines" / "ast_sequencer_go.py",
            root / "tools" / "engines" / "ast_sequencer_cs.py",
            root / "tools" / "core" / "polyglot_imports.py",
            root / "tools" / "core" / "language_agnostic_symbols.py",
            root / "tools" / "core" / "package_contracts.py",
            root / "config" / "language_registry.json",
            root / "config" / "language_agnostic_symbols.json",
        ]
    )

def atlas_counts(atlas: dict[str, Any]) -> dict[str, int]:
    projects = 0
    files = 0
    symbols = 0
    dependency_edges = 0
    for project_key, project_data in atlas.items():
        if project_key == "symbols" or not isinstance(project_data, dict):
            continue
        projects += 1
        project_files = project_data.get("files", {}) or {}
        project_dependencies = project_data.get("dependencies", {}) or {}
        files += len(project_files) if isinstance(project_files, dict) else 0
        if isinstance(project_files, dict):
            symbols += sum(
                len(file_data.get("symbols", []) or [])
                for file_data in project_files.values()
                if isinstance(file_data, dict)
            )
        if isinstance(project_dependencies, dict):
            dependency_edges += sum(
                len(targets or []) for targets in project_dependencies.values() if isinstance(targets, list)
            )
    return {
        "projects": projects,
        "files": files,
        "symbols": symbols,
        "dependency_edges": dependency_edges,
    }


def source_fingerprint(atlas: dict[str, Any]) -> str:
    inventory: list[dict[str, Any]] = []
    for project_key, project_data in sorted(atlas.items()):
        if project_key == "symbols" or not isinstance(project_data, dict):
            continue
        for rel_path, file_data in sorted((project_data.get("files", {}) or {}).items()):
            if not isinstance(file_data, dict):
                continue
            inventory.append(
                {
                    "project": project_key,
                    "file": rel_path,
                    "content_identity": file_data.get("hash")
                    or file_data.get("fingerprint")
                    or file_data.get("dna")
                    or "",
                    "size": int(file_data.get("size", 0) or 0),
                }
            )
    return payload_sha256(inventory)


def _normalized_changed_file_refs(values: list[str] | None) -> list[str]:
    normalized: set[str] = set()
    for value in values or []:
        text = str(value or "").replace("\\", "/").strip()
        if "::" not in text:
            continue
        project, rel_path = text.split("::", 1)
        project = project.strip()
        rel_path = rel_path.strip("/")
        if project and rel_path:
            normalized.add(f"{project}::{rel_path}")
    return sorted(normalized)


def build_atlas_commit(
    atlas: dict[str, Any],
    *,
    generation_mode: str = "full",
    generated_at: str | None = None,
    atlas_sha256: str | None = None,
    parent_snapshot_id: str | None = None,
    changed_files: list[str] | None = None,
    deleted_files: list[str] | None = None,
) -> dict[str, Any]:
    candidate_hash = str(atlas_sha256 or "").lower()
    atlas_hash = (
        candidate_hash
        if len(candidate_hash) == 64 and all(char in "0123456789abcdef" for char in candidate_hash)
        else payload_sha256(atlas)
    )
    config_hash = configuration_fingerprint()
    generator_hash = generator_fingerprint()
    source_hash = source_fingerprint(atlas)
    snapshot_id = hashlib.sha256(
        f"{atlas_hash}:{config_hash}:{generator_hash}:{source_hash}".encode("utf-8")
    ).hexdigest()
    projects = sorted(
        key for key, value in atlas.items() if key != "symbols" and isinstance(value, dict)
    )
    commit = {
        "meta": {
            "kind": ATLAS_COMMIT_KIND,
            "version": ATLAS_COMMIT_VERSION,
            "generated_at": generated_at or _utc_now(),
            "generator": "tools.engines.generate_atlas",
        },
        "state": "complete",
        "snapshot_id": snapshot_id,
        "generation_mode": str(generation_mode or "full"),
        "atlas_sha256": atlas_hash,
        "source_fingerprint": source_hash,
        "configuration_fingerprint": config_hash,
        "generator_fingerprint": generator_hash,
        "projects": projects,
        "counts": atlas_counts(atlas),
    }
    normalized_changed_files = _normalized_changed_file_refs(changed_files)
    normalized_deleted_files = _normalized_changed_file_refs(deleted_files)
    if not set(normalized_deleted_files).issubset(normalized_changed_files):
        raise ValueError("Deleted Atlas refs must be a subset of changed refs.")
    parent = str(parent_snapshot_id or "").strip()
    if str(generation_mode or "") == "surgical" and parent and normalized_changed_files:
        commit["generation_transition"] = {
            "kind": "scoped_delta",
            "parent_snapshot_id": parent,
            "changed_files": normalized_changed_files,
            "changed_files_sha256": payload_sha256(normalized_changed_files),
            "deleted_files": normalized_deleted_files,
            "deleted_files_sha256": payload_sha256(normalized_deleted_files),
        }
    return commit


def validate_atlas_commit(atlas: dict[str, Any], commit: dict[str, Any]) -> list[dict[str, Any]]:
    expected = build_atlas_commit(
        atlas,
        generation_mode=str(commit.get("generation_mode") or "full"),
        generated_at=str((commit.get("meta") or {}).get("generated_at") or "validation"),
    )

    def check(name: str, passed: bool, expected_value: Any, actual_value: Any) -> dict[str, Any]:
        return {
            "name": name,
            "passed": bool(passed),
            "expected": expected_value,
            "actual": actual_value,
        }

    meta = commit.get("meta", {}) if isinstance(commit.get("meta"), dict) else {}
    transition = commit.get("generation_transition")
    transition_checks: list[dict[str, Any]] = []
    if isinstance(transition, dict):
        changed_files = _normalized_changed_file_refs(transition.get("changed_files"))
        deleted_files = _normalized_changed_file_refs(transition.get("deleted_files"))
        deleted_fields_present = (
            "deleted_files" in transition or "deleted_files_sha256" in transition
        )
        deleted_fields_valid = (
            not deleted_fields_present
            or (
                transition.get("deleted_files", []) == deleted_files
                and transition.get("deleted_files_sha256") == payload_sha256(deleted_files)
            )
        )
        transition_valid = (
            commit.get("generation_mode") == "surgical"
            and transition.get("kind") == "scoped_delta"
            and bool(str(transition.get("parent_snapshot_id") or "").strip())
            and bool(changed_files)
            and transition.get("changed_files") == changed_files
            and transition.get("changed_files_sha256") == payload_sha256(changed_files)
            and set(deleted_files).issubset(changed_files)
            and deleted_fields_valid
        )
        transition_checks.append(
            check(
                "atlas_generation_transition",
                transition_valid,
                "valid_scoped_delta",
                transition,
            )
        )
    return [
        check("atlas_commit_kind", meta.get("kind") == ATLAS_COMMIT_KIND, ATLAS_COMMIT_KIND, meta.get("kind")),
        check("atlas_commit_version", meta.get("version") == ATLAS_COMMIT_VERSION, ATLAS_COMMIT_VERSION, meta.get("version")),
        check("atlas_commit_complete", commit.get("state") == "complete", "complete", commit.get("state")),
        check("atlas_payload_hash", commit.get("atlas_sha256") == expected["atlas_sha256"], expected["atlas_sha256"], commit.get("atlas_sha256")),
        check("atlas_source_fingerprint", commit.get("source_fingerprint") == expected["source_fingerprint"], expected["source_fingerprint"], commit.get("source_fingerprint")),
        check(
            "atlas_configuration_fingerprint",
            commit.get("configuration_fingerprint") == expected["configuration_fingerprint"],
            expected["configuration_fingerprint"],
            commit.get("configuration_fingerprint"),
        ),
        check(
            "atlas_generator_fingerprint",
            commit.get("generator_fingerprint") == expected["generator_fingerprint"],
            expected["generator_fingerprint"],
            commit.get("generator_fingerprint"),
        ),
        check("atlas_project_set", commit.get("projects") == expected["projects"], expected["projects"], commit.get("projects")),
        check("atlas_counts", commit.get("counts") == expected["counts"], expected["counts"], commit.get("counts")),
        check("atlas_snapshot_id", commit.get("snapshot_id") == expected["snapshot_id"], expected["snapshot_id"], commit.get("snapshot_id")),
        *transition_checks,
    ]

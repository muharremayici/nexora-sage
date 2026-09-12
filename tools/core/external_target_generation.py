from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.unmanaged_atomic_io import native_filesystem_path, save_unmanaged_json_atomic

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
INVALID_CURRENT_SENTINEL = ".invalid-current"
GENERATED_RUN_ID_HEX_CHARS = 16


def external_target_output_slug(target_root: str) -> str:
    """Return the stable, backward-compatible output identity for one target."""

    target_path = Path(target_root)
    stem = target_path.name or "external_target"
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in stem).strip("_") or "external_target"
    digest = hashlib.sha256(str(target_path).encode("utf-8")).hexdigest()[:10]
    return f"{safe}_{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_external_target_run_id(prefix: str = "sage-run") -> str:
    """Create a collision-resistant run id without exhausting Windows path budgets."""
    normalized_prefix = str(prefix or "").strip()
    if not RUN_ID_RE.fullmatch(normalized_prefix):
        raise ValueError("External target generation prefix is invalid")
    run_id = f"{normalized_prefix}-{uuid.uuid4().hex[:GENERATED_RUN_ID_HEX_CHARS]}"
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("Generated external target run_id is invalid")
    return run_id


def _run_dir(target_dir: Path, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(str(run_id or "")):
        raise ValueError("External target generation run_id is invalid")
    return Path(target_dir) / "generations" / run_id


def _save_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    # External generation manifests and pointers sit outside managed .raw
    # storage, so they must not import the runtime config/SQLite proxy layer.
    save_unmanaged_json_atomic(path, payload)


def resolve_current_external_target_generation(
    target_dir: Path,
) -> tuple[Path | None, dict[str, Any], str]:
    """Resolve only a validated, manifest-bound current generation."""
    target_dir = Path(target_dir)
    pointer_path = target_dir / "current.json"
    try:
        pointer = _strict_json(pointer_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, {}, f"current_pointer_unavailable:{type(exc).__name__}"
    if pointer.get("meta", {}).get("kind") != "external_target_current_generation":
        return None, pointer, "current_pointer_kind_invalid"

    run_id = str(pointer.get("run_id") or "")
    if not RUN_ID_RE.fullmatch(run_id):
        return None, pointer, "current_run_id_invalid"
    expected_relative = f"generations/{run_id}"
    if str(pointer.get("generation_path") or "").replace("\\", "/") != expected_relative:
        return None, pointer, "current_generation_path_mismatch"
    generation = _run_dir(target_dir, run_id)
    try:
        generation.resolve().relative_to((target_dir / "generations").resolve())
        manifest = _strict_json(generation / "generation.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, pointer, f"current_generation_unavailable:{type(exc).__name__}"
    if manifest.get("meta", {}).get("kind") != "external_target_generation":
        return None, pointer, "current_generation_kind_invalid"
    if manifest.get("state") != "VALIDATED" or str(manifest.get("run_id") or "") != run_id:
        return None, pointer, "current_generation_not_validated"
    expected_manifest_sha = str(pointer.get("generation_manifest_sha256") or "")
    if not expected_manifest_sha or _sha256(generation / "generation.json") != expected_manifest_sha:
        return None, pointer, "current_generation_manifest_identity_mismatch"
    if pointer.get("sqlite") != manifest.get("sqlite"):
        return None, pointer, "current_sqlite_identity_mismatch"
    return generation, pointer, "validated_current"


def resolve_external_target_artifact_dir(target_dir: Path) -> tuple[Path, str]:
    """Resolve validated current output, legacy output, or a fail-closed sentinel."""
    target_dir = Path(target_dir)
    generation, _pointer, reason = resolve_current_external_target_generation(target_dir)
    if generation is not None:
        return generation, reason
    if Path(native_filesystem_path(target_dir / "current.json")).is_file():
        return target_dir / "generations" / INVALID_CURRENT_SENTINEL, reason
    return target_dir, "legacy_unversioned"


def begin_external_target_generation(target_dir: Path, run_id: str, target_root: Path) -> Path:
    run_dir = _run_dir(target_dir, run_id)
    if Path(native_filesystem_path(run_dir)).exists():
        raise FileExistsError(f"External target generation already exists: {run_id}")
    _save_json_atomic(run_dir / "generation.json", {
        "meta": {"kind": "external_target_generation", "version": "1.0.0"},
        "run_id": run_id,
        "target_root": str(Path(target_root).resolve()),
        "state": "ACTIVE",
        "started_at": _utc_now(),
        "claim_boundary": "An attempt is not current analysis authority until validated and atomically promoted.",
    })
    return run_dir


def close_external_target_generation_without_promotion(
    target_dir: Path,
    run_id: str,
    *,
    exit_code: int,
    reason: str,
) -> dict[str, Any]:
    """Close a staged writer that is deliberately ineligible for current authority."""
    run_dir = _run_dir(target_dir, run_id)
    manifest_path = run_dir / "generation.json"
    manifest = _strict_json(manifest_path)
    if manifest.get("meta", {}).get("kind") != "external_target_generation":
        raise ValueError("External target generation manifest kind is invalid")
    if str(manifest.get("run_id") or "") != run_id or manifest.get("state") != "ACTIVE":
        raise ValueError("External target generation is not the exact active attempt")
    manifest.update({
        "state": "COMPLETED_UNPROMOTED" if int(exit_code) == 0 else "FAILED",
        "exit_code": int(exit_code),
        "finished_at": _utc_now(),
        "non_promotion_reason": str(reason or "not_current_eligible"),
    })
    _save_json_atomic(manifest_path, manifest)
    return manifest


def _strict_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(native_filesystem_path(path)).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(native_filesystem_path(path)).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_identity(database: Path) -> dict[str, Any]:
    native_database = Path(native_filesystem_path(database))
    if not native_database.is_file():
        raise ValueError("External target SQLite database is missing")
    with closing(sqlite3.connect(native_database)) as conn:
        quick = conn.execute("PRAGMA quick_check;").fetchone()
        if not quick or str(quick[0]).lower() != "ok":
            raise ValueError("External target SQLite quick_check failed")
        row = conn.execute(
            "SELECT payload_sha, payload_bytes, storage_mode, generation_id, part_count, updated_at "
            "FROM state_payloads WHERE name = 'atlas';"
        ).fetchone()
    if row is None or not str(row[0] or ""):
        raise ValueError("External target SQLite lacks an authoritative Atlas payload identity")
    return {
        "path": ".raw/codemaps.db",
        "sha256": _sha256(database),
        "atlas_payload_sha256": str(row[0]),
        "atlas_payload_bytes": int(row[1] or 0),
        "atlas_storage_mode": str(row[2] or ""),
        "atlas_generation_id": str(row[3] or "") or None,
        "atlas_part_count": int(row[4] or 0),
        "atlas_updated_at": str(row[5] or ""),
    }


def finalize_external_target_generation(
    target_dir: Path,
    run_id: str,
    *,
    exit_code: int,
    require_audit: bool = True,
    require_preflight: bool = True,
) -> dict[str, Any]:
    run_dir = _run_dir(target_dir, run_id)
    manifest_path = run_dir / "generation.json"
    manifest = _strict_json(manifest_path)
    if manifest.get("meta", {}).get("kind") != "external_target_generation":
        raise ValueError("External target generation manifest kind is invalid")
    if str(manifest.get("run_id") or "") != run_id or manifest.get("state") != "ACTIVE":
        raise ValueError("External target generation is not the exact active attempt")
    manifest["exit_code"] = int(exit_code)
    manifest["finished_at"] = _utc_now()
    if int(exit_code) != 0:
        manifest["state"] = "FAILED"
        _save_json_atomic(manifest_path, manifest)
        return manifest

    raw_dir = run_dir / ".raw"
    required = [raw_dir / "pipeline_run_receipt.json"]
    if require_preflight:
        required.insert(0, raw_dir / "external_target_preflight.json")
    if require_audit:
        required.append(raw_dir / "audit_report.json")
    shadows: list[dict[str, str]] = []
    try:
        for path in required:
            if not Path(native_filesystem_path(path)).is_file():
                raise ValueError(f"Required external target artifact is missing: {path.name}")
            payload = _strict_json(path)
            if path.name == "external_target_preflight.json":
                preflight_target = str((payload.get("target") or {}).get("root") or "")
                preflight_output = str((payload.get("target") or {}).get("output_dir") or "")
                if not preflight_target or Path(preflight_target).resolve() != Path(manifest["target_root"]).resolve():
                    raise ValueError("Preflight target identity does not match this generation")
                if not preflight_output or Path(preflight_output).resolve() != run_dir.resolve():
                    raise ValueError("Preflight output identity does not match this generation")
            elif path.name == "pipeline_run_receipt.json":
                if payload.get("status") != "PASS" or payload.get("run_id") != run_id:
                    raise ValueError("Pipeline receipt is not a PASS for this exact generation")
            shadows.append({"path": f".raw/{path.name}", "sha256": _sha256(path)})
        sqlite_identity = _sqlite_identity(raw_dir / "codemaps.db")
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        manifest["state"] = "VALIDATION_FAILED"
        manifest["validation_error"] = f"{type(exc).__name__}: {exc}"
        _save_json_atomic(manifest_path, manifest)
        return manifest

    manifest.update({
        "state": "VALIDATED",
        "validated_at": _utc_now(),
        "preflight_status": "VALIDATED" if require_preflight else "SKIPPED_BY_OPERATOR",
        "sqlite": sqlite_identity,
        "validated_shadows": shadows,
    })
    _save_json_atomic(manifest_path, manifest)
    pointer = {
        "meta": {"kind": "external_target_current_generation", "version": "1.0.0"},
        "run_id": run_id,
        "generation_path": f"generations/{run_id}",
        "promoted_at": _utc_now(),
        "preflight_status": manifest["preflight_status"],
        "sqlite": sqlite_identity,
        "generation_manifest_sha256": _sha256(manifest_path),
    }
    _save_json_atomic(Path(target_dir) / "current.json", pointer)
    return manifest

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from tools import generate_external_target_index
from tools.core.external_target_generation import (
    begin_external_target_generation,
    close_external_target_generation_without_promotion,
    external_target_generation_history,
    finalize_external_target_generation,
    new_external_target_run_id,
    resolve_external_target_artifact_dir,
    resolve_current_external_target_generation,
)


def _valid_attempt(target: Path, run_id: str, *, audit: str = "{}") -> None:
    run_dir = begin_external_target_generation(target, run_id, target / "repo")
    raw = run_dir / ".raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "external_target_preflight.json").write_text(json.dumps({
        "target": {"root": str((target / "repo").resolve()), "output_dir": str(run_dir.resolve())}
    }), encoding="utf-8")
    (raw / "pipeline_run_receipt.json").write_text(json.dumps({"status": "PASS", "run_id": run_id}), encoding="utf-8")
    (raw / "audit_report.json").write_text(audit, encoding="utf-8")
    with sqlite3.connect(raw / "codemaps.db") as conn:
        conn.execute("CREATE TABLE state_payloads (name TEXT, payload_sha TEXT, payload_bytes INTEGER, storage_mode TEXT, generation_id TEXT, part_count INTEGER, updated_at TEXT)")
        conn.execute("INSERT INTO state_payloads VALUES ('atlas', 'atlas-sha', 42, 'inline_json', NULL, 0, 'now')")


def test_only_exact_validated_generation_becomes_current(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-good")
    result = finalize_external_target_generation(target, "sage-run-good", exit_code=0)
    pointer = json.loads((target / "current.json").read_text(encoding="utf-8"))
    assert result["state"] == "VALIDATED"
    assert pointer["run_id"] == "sage-run-good"
    assert pointer["sqlite"]["atlas_payload_sha256"] == "atlas-sha"
    current, resolved_pointer, reason = resolve_current_external_target_generation(target)
    assert current == target / "generations" / "sage-run-good"
    assert resolved_pointer == pointer
    assert reason == "validated_current"


def test_failed_or_corrupt_attempt_cannot_replace_current(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-good")
    finalize_external_target_generation(target, "sage-run-good", exit_code=0)
    begin_external_target_generation(target, "sage-run-failed", target / "repo")
    failed = finalize_external_target_generation(target, "sage-run-failed", exit_code=7)
    _valid_attempt(target, "sage-run-corrupt", audit="{")
    corrupt = finalize_external_target_generation(target, "sage-run-corrupt", exit_code=0)
    pointer = json.loads((target / "current.json").read_text(encoding="utf-8"))
    assert failed["state"] == "FAILED"
    assert corrupt["state"] == "VALIDATION_FAILED"
    assert pointer["run_id"] == "sage-run-good"


def test_missing_atlas_or_mismatched_receipt_cannot_become_current(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-good")
    finalize_external_target_generation(target, "sage-run-good", exit_code=0)

    _valid_attempt(target, "sage-run-wrong-receipt")
    receipt = target / "generations" / "sage-run-wrong-receipt" / ".raw" / "pipeline_run_receipt.json"
    receipt.write_text(json.dumps({"status": "PASS", "run_id": "another-run"}), encoding="utf-8")
    mismatch = finalize_external_target_generation(target, "sage-run-wrong-receipt", exit_code=0)

    _valid_attempt(target, "sage-run-no-atlas")
    database = target / "generations" / "sage-run-no-atlas" / ".raw" / "codemaps.db"
    with sqlite3.connect(database) as conn:
        conn.execute("DELETE FROM state_payloads WHERE name = 'atlas'")
    missing = finalize_external_target_generation(target, "sage-run-no-atlas", exit_code=0)

    assert mismatch["state"] == "VALIDATION_FAILED"
    assert missing["state"] == "VALIDATION_FAILED"
    assert json.loads((target / "current.json").read_text(encoding="utf-8"))["run_id"] == "sage-run-good"


def test_operator_skipped_preflight_is_explicit_but_other_authority_still_validates(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-skip-preflight")
    preflight = target / "generations" / "sage-run-skip-preflight" / ".raw" / "external_target_preflight.json"
    preflight.unlink()

    result = finalize_external_target_generation(
        target,
        "sage-run-skip-preflight",
        exit_code=0,
        require_preflight=False,
    )
    pointer = json.loads((target / "current.json").read_text(encoding="utf-8"))

    assert result["state"] == "VALIDATED"
    assert result["preflight_status"] == "SKIPPED_BY_OPERATOR"
    assert pointer["preflight_status"] == "SKIPPED_BY_OPERATOR"
    assert {item["path"] for item in result["validated_shadows"]} == {
        ".raw/pipeline_run_receipt.json",
        ".raw/audit_report.json",
    }


def test_pointer_or_validated_manifest_tampering_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-good")
    finalize_external_target_generation(target, "sage-run-good", exit_code=0)

    pointer_path = target / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["generation_path"] = "generations/another-run"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    current, _, reason = resolve_current_external_target_generation(target)
    assert current is None
    assert reason == "current_generation_path_mismatch"

    finalize_pointer = pointer
    finalize_pointer["generation_path"] = "generations/sage-run-good"
    pointer_path.write_text(json.dumps(finalize_pointer), encoding="utf-8")
    manifest = target / "generations" / "sage-run-good" / "generation.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["validated_at"] = "tampered"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    current, _, reason = resolve_current_external_target_generation(target)
    assert current is None
    assert reason == "current_generation_manifest_identity_mismatch"


def test_pointer_and_manifest_require_exact_artifact_kinds(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-good")
    finalize_external_target_generation(target, "sage-run-good", exit_code=0)

    pointer_path = target / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["meta"]["kind"] = "unrelated_pointer"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    current, _, reason = resolve_current_external_target_generation(target)
    assert current is None
    assert reason == "current_pointer_kind_invalid"

    pointer["meta"]["kind"] = "external_target_current_generation"
    manifest_path = target / "generations" / "sage-run-good" / "generation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["meta"]["kind"] = "unrelated_generation"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pointer["generation_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    current, _, reason = resolve_current_external_target_generation(target)
    assert current is None
    assert reason == "current_generation_kind_invalid"


def test_existing_generation_identity_cannot_be_reused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    begin_external_target_generation(target, "sage-run-one", target / "repo")

    with pytest.raises(FileExistsError, match="already exists"):
        begin_external_target_generation(target, "sage-run-one", target / "repo")


def test_generated_run_ids_preserve_entropy_with_a_bounded_path_footprint() -> None:
    first = new_external_target_run_id()
    second = new_external_target_run_id("sage-watch")

    assert first.startswith("sage-run-")
    assert second.startswith("sage-watch-")
    assert first != second
    assert len(first) <= 25
    assert len(second) <= 27
    assert all(char in "0123456789abcdef" for char in first.removeprefix("sage-run-"))


def test_artifact_directory_resolution_preserves_legacy_and_fails_closed_on_invalid_current(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    artifact_dir, reason = resolve_external_target_artifact_dir(target)
    assert artifact_dir == target
    assert reason == "legacy_unversioned"

    _valid_attempt(target, "sage-run-good")
    finalize_external_target_generation(target, "sage-run-good", exit_code=0)
    artifact_dir, reason = resolve_external_target_artifact_dir(target)
    assert artifact_dir == target / "generations" / "sage-run-good"
    assert reason == "validated_current"

    (target / "current.json").write_text("{}", encoding="utf-8")
    artifact_dir, reason = resolve_external_target_artifact_dir(target)
    assert artifact_dir == target / "generations" / ".invalid-current"
    assert reason == "current_pointer_kind_invalid"


def test_non_current_writer_closes_without_replacing_validated_pointer(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-good")
    finalize_external_target_generation(target, "sage-run-good", exit_code=0)
    begin_external_target_generation(target, "sage-watch-one", target / "repo")

    closed = close_external_target_generation_without_promotion(
        target,
        "sage-watch-one",
        exit_code=0,
        reason="watch_is_not_current_eligible",
    )

    assert closed["state"] == "COMPLETED_UNPROMOTED"
    assert json.loads((target / "current.json").read_text(encoding="utf-8"))["run_id"] == "sage-run-good"


def test_external_target_index_does_not_read_legacy_shadows_behind_invalid_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = tmp_path / "external_targets"
    target = targets / "sample-target"
    raw = target / ".raw"
    raw.mkdir(parents=True)
    (raw / "external_target_preflight.json").write_text(
        json.dumps({"summary": {"status": "STALE_LEGACY"}}),
        encoding="utf-8",
    )
    (target / "current.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(generate_external_target_index, "EXTERNAL_TARGETS_DIR", targets)

    payload = generate_external_target_index.build_index()

    row = payload["targets"][0]
    assert row["current_status"] == "UNVALIDATED_OR_LEGACY"
    assert row["current_reason"] == "current_pointer_kind_invalid"
    assert row["artifact_resolution"] == "current_pointer_kind_invalid"
    assert row["summary"]["preflight"] == {}
    assert payload["summary"]["with_preflight"] == 0


def test_generation_history_separates_attempt_completed_validated_and_current_roles(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-good")
    finalize_external_target_generation(target, "sage-run-good", exit_code=0)
    begin_external_target_generation(target, "sage-run-failed", target / "repo")
    finalize_external_target_generation(target, "sage-run-failed", exit_code=5)
    begin_external_target_generation(target, "sage-watch-done", target / "repo")
    close_external_target_generation_without_promotion(
        target,
        "sage-watch-done",
        exit_code=0,
        reason="watch_not_current_eligible",
    )
    begin_external_target_generation(target, "sage-run-active", target / "repo")

    payload = external_target_generation_history(
        target,
        max_manifest_scan=20,
        max_history_entries=20,
    )

    assert payload["roles"] == {
        "latest_attempt": "sage-run-active",
        "latest_completed": "sage-watch-done",
        "latest_validated": "sage-run-good",
        "validated_current": "sage-run-good",
    }
    by_id = {row["run_id"]: row for row in payload["history"]}
    assert by_id["sage-run-active"]["roles"] == ["latest_attempt"]
    assert "latest_completed" in by_id["sage-watch-done"]["roles"]
    assert set(by_id["sage-run-good"]["roles"]) == {
        "latest_validated",
        "validated_current",
    }
    assert payload["role_completeness"] == "COMPLETE"


def test_generation_history_bounds_scan_and_exposes_invalid_manifest_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    for index in range(7):
        begin_external_target_generation(
            target,
            f"sage-run-{index}",
            target / "repo",
        )
    corrupt = target / "generations" / "sage-run-6" / "generation.json"
    corrupt.write_text("{", encoding="utf-8")

    payload = external_target_generation_history(
        target,
        max_manifest_scan=5,
        max_history_entries=4,
    )

    assert payload["total_generation_dirs"] == 7
    assert payload["scanned_generation_dirs"] == 5
    assert payload["omitted_from_scan"] == 2
    assert payload["omitted_from_history"] == 1
    assert payload["invalid_manifests"] == 1
    assert payload["role_completeness"] == "BOUNDED_SCAN"
    assert any(row.get("manifest_error") == "JSONDecodeError" for row in payload["history"])


def test_generation_history_keeps_current_inside_manifest_scan_budget(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    _valid_attempt(target, "sage-run-current")
    finalize_external_target_generation(target, "sage-run-current", exit_code=0)
    for index in range(7):
        begin_external_target_generation(
            target,
            f"sage-run-newer-{index}",
            target / "repo",
        )

    payload = external_target_generation_history(
        target,
        max_manifest_scan=5,
        max_history_entries=5,
    )

    assert payload["scanned_generation_dirs"] == 5
    assert payload["omitted_from_scan"] == 3
    assert payload["roles"]["validated_current"] == "sage-run-current"
    assert any(
        row["run_id"] == "sage-run-current"
        and "validated_current" in row["roles"]
        for row in payload["history"]
    )

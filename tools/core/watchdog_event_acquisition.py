from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _scope_sha256(paths: list[str]) -> str:
    normalized = sorted({str(path).replace("\\", "/") for path in paths if str(path).strip()})
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _live_atlas_text_identity(path: Path) -> tuple[str, str]:
    """Read one source with the same text/hash contract as Atlas, rejecting an active write."""
    before = path.stat()
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as source_file:
        content = source_file.read()
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        return "", "source_changed_during_identity_read"
    return hashlib.md5(content.encode("utf-8")).hexdigest(), "stable"


def classify_filesystem_event_batch(
    change_events: list[dict[str, Any]],
    *,
    indexed_paths: Mapping[Path, Mapping[str, Any]],
    baseline: Mapping[str, Any],
    policy: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify a debounced event batch without silently losing an actionable path."""
    baseline_current = str(baseline.get("status") or "") == "current"
    dispositions: dict[str, int] = {}
    classified: list[dict[str, Any]] = []
    retained_existing: list[str] = []
    retained_tombstones: list[str] = []
    omitted_unchanged: list[str] = []
    omitted_transient: list[str] = []
    unknown_paths: list[str] = []

    normalized_index = {Path(path).resolve(): dict(value) for path, value in indexed_paths.items()}
    for raw in change_events:
        if not isinstance(raw, dict) or not str(raw.get("path") or "").strip():
            continue
        resolved = Path(str(raw["path"])).resolve()
        path_text = str(resolved)
        indexed = normalized_index.get(resolved)
        exists = resolved.is_file()
        observed_kind = str(raw.get("event_kind") or "modify")
        effective_kind = observed_kind
        identity_status = "not_applicable"
        disposition = "retain_unknown"

        if exists:
            effective_kind = "create" if indexed is None else ("rename_to" if observed_kind == "rename_to" else "modify")
            if indexed is None:
                identity_status = "verified_new_file" if baseline_current else "unindexed_file_with_unavailable_baseline"
                disposition = "retain_verified_change" if baseline_current else "retain_unknown"
            elif baseline_current and str(indexed.get("hash") or ""):
                try:
                    live_hash, read_status = _live_atlas_text_identity(resolved)
                except (OSError, UnicodeError) as exc:
                    live_hash, read_status = "", f"identity_read_failed:{type(exc).__name__}"
                if not live_hash:
                    identity_status = read_status
                    disposition = "retain_unknown"
                elif live_hash == str(indexed.get("hash") or ""):
                    identity_status = "matches_canonical_atlas"
                    disposition = "omit_unchanged"
                else:
                    identity_status = "differs_from_canonical_atlas"
                    disposition = "retain_verified_change"
            else:
                identity_status = (
                    "canonical_atlas_baseline_unavailable"
                    if not baseline_current
                    else "canonical_atlas_file_hash_unavailable"
                )
                disposition = "retain_unknown"
        elif indexed is not None:
            effective_kind = "rename_from" if observed_kind == "rename_from" else "delete"
            identity_status = "verified_missing_indexed_file" if baseline_current else "indexed_file_missing_untrusted_baseline"
            disposition = "retain_verified_change" if baseline_current else "retain_unknown"
        else:
            effective_kind = "unknown" if baseline_current else "delete"
            identity_status = "absent_and_not_indexed" if baseline_current else "absent_path_with_unavailable_baseline"
            disposition = "omit_transient_absence" if baseline_current else "retain_unknown"

        row = {
            "path": path_text,
            "event_kind": effective_kind,
            "observed_event_kind": observed_kind,
            "related_path": str(raw.get("related_path") or ""),
            "previously_indexed": indexed is not None,
            "target_ref": str((indexed or {}).get("target_ref") or ""),
            "content_identity_status": identity_status,
            "disposition": disposition,
            "raw_event_count": int(raw.get("raw_event_count") or 1),
            "first_observed_at": str(raw.get("first_observed_at") or ""),
            "last_observed_at": str(raw.get("last_observed_at") or ""),
        }
        classified.append(row)
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
        if disposition == "omit_unchanged":
            omitted_unchanged.append(path_text)
        elif disposition == "omit_transient_absence":
            omitted_transient.append(path_text)
        else:
            if disposition == "retain_unknown":
                unknown_paths.append(path_text)
            if effective_kind in {"delete", "rename_from"}:
                retained_tombstones.append(path_text)
            else:
                retained_existing.append(path_text)

    candidate_paths = [row["path"] for row in classified]
    retained_paths = retained_tombstones + retained_existing
    guard = policy.get("amplification_guard") if isinstance(policy.get("amplification_guard"), dict) else {}
    threshold = max(1, int(guard.get("candidate_path_threshold") or 1))
    confirmation_required = len(classified) >= threshold and bool(unknown_paths)
    if confirmation_required:
        decision_status = "operator_confirmation_required"
        selected_existing: list[str] = []
        selected_tombstones: list[str] = []
        held_files = retained_paths
        decision_reason = "large_event_scope_contains_unresolved_content_identity"
    else:
        selected_existing = retained_existing
        selected_tombstones = retained_tombstones
        held_files = []
        if retained_paths:
            decision_status = (
                "accepted_verified_bulk"
                if len(classified) >= threshold and len(retained_paths) == len(classified)
                else "accepted_after_content_identity_filter"
                if len(classified) >= threshold
                else "accepted"
            )
            decision_reason = "all_actionable_paths_retained"
        else:
            decision_status = "no_content_change"
            decision_reason = "all_events_proved_unchanged_or_transient"

    selected_files = selected_tombstones + selected_existing
    omitted_existing_count = sum(
        1 for row in classified if row["path"] not in selected_files and row["event_kind"] not in {"delete", "rename_from"}
    )
    omitted_tombstone_count = sum(
        1 for row in classified if row["path"] not in selected_files and row["event_kind"] in {"delete", "rename_from"}
    )
    scope_hash = _scope_sha256(candidate_paths)
    event_ring = [dict(row) for row in provenance.get("event_ring", []) if isinstance(row, dict)]
    raw_event_count = max(int(provenance.get("raw_event_count") or 0), len(classified))
    event_provenance = {
        "observer_session_id": str(provenance.get("observer_session_id") or ""),
        "observer_started_at": str(provenance.get("observer_started_at") or ""),
        "pulse_sequence": int(provenance.get("pulse_sequence") or 0),
        "batch_id": hashlib.sha256(
            f"{provenance.get('observer_session_id')}:{provenance.get('pulse_sequence')}:{scope_hash}".encode("utf-8")
        ).hexdigest()[:16],
        "seconds_since_observer_start": round(float(provenance.get("seconds_since_observer_start") or 0.0), 3),
        "cold_start": bool(provenance.get("cold_start")),
        "raw_event_count": raw_event_count,
        "deduplicated_path_count": len(classified),
        "duplicate_event_count": max(0, raw_event_count - len(classified)),
        "event_ring": event_ring,
        "event_ring_omitted_count": max(0, raw_event_count - len(event_ring)),
        "baseline": dict(baseline),
        "disposition_counts": dispositions,
    }
    return {
        "mode": "filesystem_events",
        "selected_files": selected_files,
        "selected_existing_files": selected_existing,
        "selected_tombstones": selected_tombstones,
        "held_files": held_files,
        "change_events": classified,
        "candidate_count": len(classified),
        "omitted_count": len(classified) - len(selected_files),
        "omitted_existing_count": omitted_existing_count,
        "omitted_tombstone_count": omitted_tombstone_count,
        "omitted_unchanged_count": len(omitted_unchanged),
        "omitted_transient_count": len(omitted_transient),
        "tombstone_coverage_complete": omitted_tombstone_count == 0,
        "filesystem_event_provenance": event_provenance,
        "scope_decision": {
            "status": decision_status,
            "reason": decision_reason,
            "candidate_path_threshold": threshold,
            "threshold_basis": str(guard.get("threshold_basis") or ""),
            "candidate_path_count": len(classified),
            "verified_change_count": dispositions.get("retain_verified_change", 0),
            "unknown_path_count": len(unknown_paths),
            "unchanged_path_count": len(omitted_unchanged),
            "transient_absence_count": len(omitted_transient),
            "selected_path_count": len(selected_files),
            "held_path_count": len(held_files),
            "candidate_scope_sha256": scope_hash,
            "silent_scope_truncation": False,
        },
    }

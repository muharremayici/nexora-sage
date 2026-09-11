from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.release_identity import current_release_version


REGISTRY_PATH = CONFIG_DIR / "react_corpus_report_coverage_registry.json"
WORK_ITEM_REGISTRY_PATH = CONFIG_DIR / "sage_work_item_registry.json"
RELEASE_CLAIM_PROFILES_PATH = CONFIG_DIR / "release_claim_profiles.json"
VALIDATION_RAW = RAW_DIR / "react_corpus_report_coverage_validation.json"
VALIDATION_REPORT = REPORTS_DIR / "react_corpus_report_coverage_validation.md"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_report_set_receipt(
    root: Path,
    *,
    excluded_relative_paths: set[str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"React Corpus report root is not a directory: {root}")

    exclusions = {
        str(value).replace("\\", "/").strip("/")
        for value in (excluded_relative_paths or set())
        if str(value).strip()
    }
    rows: list[tuple[str, int, str]] = []
    extension_counts: Counter[str] = Counter()
    extension_bytes: Counter[str] = Counter()
    layer_counts: Counter[str] = Counter()
    layer_bytes: Counter[str] = Counter()
    content_hashes: Counter[str] = Counter()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in exclusions:
            continue
        byte_count = path.stat().st_size
        sha256 = _file_sha256(path)
        rows.append((relative, byte_count, sha256))
        extension = path.suffix.lower()
        layer = relative.split("/", 1)[0] if "/" in relative else "root"
        extension_counts[extension] += 1
        extension_bytes[extension] += byte_count
        layer_counts[layer] += 1
        layer_bytes[layer] += byte_count
        content_hashes[sha256] += 1

    rows.sort(key=lambda row: row[0])
    canonical = "".join(f"{path}\t{size}\t{sha256}\n" for path, size, sha256 in rows)
    return {
        "set_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "file_count": len(rows),
        "total_bytes": sum(row[1] for row in rows),
        "unique_content_hashes": len(content_hashes),
        "duplicate_hash_groups": sum(1 for count in content_hashes.values() if count > 1),
        "artifact_layers": {
            layer: {"file_count": layer_counts[layer], "total_bytes": layer_bytes[layer]}
            for layer in sorted(layer_counts)
        },
        "extension_counts": {
            extension: {"file_count": extension_counts[extension], "total_bytes": extension_bytes[extension]}
            for extension in sorted(extension_counts)
        },
    }


def validate_registry_payload(
    registry: dict[str, Any],
    *,
    known_work_items: set[str],
    claim_profile_planned_work: set[str],
    work_item_statuses: dict[str, str] | None = None,
    required_release_gate_work_items: set[str] | None = None,
) -> list[dict[str, Any]]:
    snapshot = registry.get("evidence_snapshot") if isinstance(registry.get("evidence_snapshot"), dict) else {}
    layers = snapshot.get("artifact_layers") if isinstance(snapshot.get("artifact_layers"), list) else []
    extensions = snapshot.get("extension_counts") if isinstance(snapshot.get("extension_counts"), list) else []
    policy = registry.get("disposition_policy") if isinstance(registry.get("disposition_policy"), dict) else {}
    dispositions = registry.get("family_dispositions") if isinstance(registry.get("family_dispositions"), list) else []
    completion = registry.get("completion_boundary") if isinstance(registry.get("completion_boundary"), dict) else {}
    derived_evidence = registry.get("post_snapshot_derived_evidence") if isinstance(registry.get("post_snapshot_derived_evidence"), dict) else {}
    derived_rows = derived_evidence.get("entries") if isinstance(derived_evidence.get("entries"), list) else []
    valid_derived_rows = [row for row in derived_rows if isinstance(row, dict)]
    derived_paths = [str(row.get("relative_path") or "") for row in valid_derived_rows]
    derived_contract_valid = bool(valid_derived_rows) and len(valid_derived_rows) == len(derived_rows) and all(
        path
        and not Path(path).is_absolute()
        and ":" not in path
        and ".." not in Path(path).parts
        and len(str(row.get("sha256") or "")) == 64
        for path, row in zip(derived_paths, valid_derived_rows)
    )
    allowed = {str(value) for value in policy.get("allowed_dispositions", [])}
    ids = [str(row.get("id") or "") for row in dispositions if isinstance(row, dict)]
    unknown_owners = sorted(
        {
            str(owner)
            for row in dispositions
            if isinstance(row, dict)
            for owner in row.get("owners", [])
            if str(owner) not in known_work_items
        }
    )
    unsafe_surfaces = sorted(
        str(surface)
        for row in dispositions
        if isinstance(row, dict)
        for surface in row.get("evidence_surfaces", [])
        if Path(str(surface)).is_absolute() or ":" in str(surface)
    )
    candidate_without_intake_owner = sorted(
        str(row.get("id") or "")
        for row in dispositions
        if isinstance(row, dict)
        and row.get("disposition") == "bounded_v1_0_5_candidate"
        and "react_corpus_report_coverage_and_disposition_contract" not in row.get("owners", [])
    )
    bounded_candidates = sorted(
        str(row.get("id") or "")
        for row in dispositions
        if isinstance(row, dict) and row.get("disposition") == "bounded_v1_0_5_candidate"
    )
    statuses = work_item_statuses or {}
    required_release_gates = sorted(required_release_gate_work_items or set())
    declared_release_gates = sorted(
        str(value)
        for value in completion.get("release_gate_work_items", [])
        if str(value or "").strip()
    )
    release_gate_states = {
        work_item_id: statuses.get(work_item_id)
        for work_item_id in required_release_gates
    }
    expected_release_ready = (
        completion.get("semantic_family_clusters_undispositioned") == 0
        and not bounded_candidates
        and bool(required_release_gates)
        and all(status == "ready_for_delivery" for status in release_gate_states.values())
    )
    checks = [
        _check("registry_kind", registry.get("meta", {}).get("kind") == "nexora.react_corpus_report_coverage_registry", registry.get("meta", {}).get("kind")),
        _check("snapshot_identity", len(str(snapshot.get("set_sha256") or "")) == 64, snapshot.get("set_sha256")),
        _check("layer_file_count", sum(int(row.get("file_count") or 0) for row in layers if isinstance(row, dict)) == snapshot.get("file_count"), len(layers)),
        _check("layer_byte_count", sum(int(row.get("total_bytes") or 0) for row in layers if isinstance(row, dict)) == snapshot.get("total_bytes"), snapshot.get("total_bytes")),
        _check("extension_file_count", sum(int(row.get("file_count") or 0) for row in extensions if isinstance(row, dict)) == snapshot.get("file_count"), extensions),
        _check("extension_byte_count", sum(int(row.get("total_bytes") or 0) for row in extensions if isinstance(row, dict)) == snapshot.get("total_bytes"), snapshot.get("total_bytes")),
        _check("read_receipt_complete", snapshot.get("read_receipt", {}).get("all_file_bytes_hashed") is True and snapshot.get("read_receipt", {}).get("files_without_inventory_classification") == 0, snapshot.get("read_receipt")),
        _check(
            "post_snapshot_derived_evidence_is_hash_bound",
            derived_contract_valid and len(derived_paths) == len(set(derived_paths)),
            {"paths": derived_paths, "boundary": derived_evidence.get("boundary")},
        ),
        _check("family_ids_unique_and_nonempty", bool(ids) and len(ids) == len(set(ids)) and all(ids), ids),
        _check("dispositions_allowed", not [row.get("id") for row in dispositions if isinstance(row, dict) and row.get("disposition") not in allowed], sorted(allowed)),
        _check("owners_resolve", not unknown_owners, unknown_owners),
        _check("evidence_surfaces_path_neutral", not unsafe_surfaces, unsafe_surfaces),
        _check("bounded_candidates_owned_by_intake", not candidate_without_intake_owner, candidate_without_intake_owner),
        _check("no_undispositioned_cluster", completion.get("semantic_family_clusters_undispositioned") == 0, completion),
        _check(
            "release_gate_matches_central_current_release_blockers",
            declared_release_gates == required_release_gates,
            {"declared": declared_release_gates, "required": required_release_gates},
        ),
        _check(
            "release_readiness_is_derived_from_dispositions_and_owner_state",
            completion.get("release_ready") is expected_release_ready,
            {
                "declared": completion.get("release_ready"),
                "expected": expected_release_ready,
                "bounded_candidates": bounded_candidates,
                "release_gate_states": release_gate_states,
            },
        ),
        _check("coverage_work_is_claim_planned", "react_corpus_report_coverage_and_disposition_contract" in claim_profile_planned_work, sorted(claim_profile_planned_work)),
    ]
    return checks


def _expected_receipt(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "set_sha256": snapshot.get("set_sha256"),
        "file_count": snapshot.get("file_count"),
        "total_bytes": snapshot.get("total_bytes"),
        "unique_content_hashes": snapshot.get("unique_content_hashes"),
        "duplicate_hash_groups": snapshot.get("duplicate_hash_groups"),
        "artifact_layers": {
            str(row.get("id")): {"file_count": row.get("file_count"), "total_bytes": row.get("total_bytes")}
            for row in snapshot.get("artifact_layers", [])
            if isinstance(row, dict)
        },
        "extension_counts": {
            str(row.get("extension")): {"file_count": row.get("file_count"), "total_bytes": row.get("total_bytes")}
            for row in snapshot.get("extension_counts", [])
            if isinstance(row, dict)
        },
    }


def _render(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# React Corpus Report Coverage Validation",
        "",
        f"- status: `{summary['status']}`",
        f"- checks: `{summary['passed_checks']}/{summary['total_checks']}`",
        f"- external_evidence_checked: `{summary['external_evidence_checked']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload["checks"]:
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:900]
        lines.append(f"| `{check['name']}` | `{'PASS' if check['passed'] else 'FAIL'}` | `{details}` |")
    return "\n".join(lines) + "\n"


def run(evidence_root: Path | None = None) -> dict[str, Any]:
    registry = load_json_object_strict(REGISTRY_PATH, label="React Corpus report coverage registry")
    work_registry = load_json_object_strict(WORK_ITEM_REGISTRY_PATH, label="SAGE work item registry")
    claim_profiles = load_json_object_strict(RELEASE_CLAIM_PROFILES_PATH, label="release claim profiles")
    known_work_items = {str(row.get("id")) for row in work_registry.get("work_items", []) if isinstance(row, dict) and row.get("id")}
    work_item_statuses = {
        str(row.get("id")): str(row.get("status") or "")
        for row in work_registry.get("work_items", [])
        if isinstance(row, dict) and row.get("id")
    }
    required_release_gate_work_items = {
        str(row.get("id"))
        for row in work_registry.get("work_items", [])
        if isinstance(row, dict)
        and row.get("id") != "react_corpus_report_coverage_and_disposition_contract"
        and row.get("target_release") == current_release_version()
        and row.get("release_blocking") is True
    }
    active_profile_id = str(claim_profiles.get("active_profile_id") or "")
    profile = next(
        (
            row
            for row in claim_profiles.get("profiles", [])
            if isinstance(row, dict)
            and str(row.get("id") or "") == active_profile_id
            and str(row.get("target_release") or "") == current_release_version()
        ),
        {},
    )
    checks = validate_registry_payload(
        registry,
        known_work_items=known_work_items,
        claim_profile_planned_work={str(value) for value in profile.get("planned_work", [])},
        work_item_statuses=work_item_statuses,
        required_release_gate_work_items=required_release_gate_work_items,
    )
    external_checked = evidence_root is not None
    if evidence_root is not None:
        derived_rows = registry.get("post_snapshot_derived_evidence", {}).get("entries", [])
        excluded_paths = {
            str(row.get("relative_path") or "").replace("\\", "/").strip("/")
            for row in derived_rows
            if isinstance(row, dict) and str(row.get("relative_path") or "").strip()
        }
        observed = build_report_set_receipt(
            evidence_root,
            excluded_relative_paths=excluded_paths,
        )
        expected = _expected_receipt(registry["evidence_snapshot"])
        checks.append(_check("external_report_set_identity", observed == expected, {"expected": expected, "observed": observed}))
        derived_mismatches = []
        evidence_root_resolved = evidence_root.resolve()
        for row in derived_rows:
            if not isinstance(row, dict):
                continue
            relative = str(row.get("relative_path") or "").replace("\\", "/").strip("/")
            candidate = (evidence_root_resolved / relative).resolve()
            try:
                candidate.relative_to(evidence_root_resolved)
            except ValueError:
                derived_mismatches.append({"relative_path": relative, "reason": "path_escape"})
                continue
            observed_sha256 = _file_sha256(candidate) if candidate.is_file() else None
            if observed_sha256 != row.get("sha256"):
                derived_mismatches.append(
                    {
                        "relative_path": relative,
                        "expected_sha256": row.get("sha256"),
                        "observed_sha256": observed_sha256,
                    }
                )
        checks.append(
            _check(
                "post_snapshot_derived_evidence_identity",
                not derived_mismatches,
                derived_mismatches,
            )
        )
    else:
        checks.append(_check("external_report_set_identity_not_requested", True, "Pass --evidence-root to re-read and verify the preserved external report set."))

    passed = sum(1 for check in checks if check["passed"])
    payload = {
        "summary": {
            "status": "PASS" if passed == len(checks) else "FAIL",
            "passed_checks": passed,
            "total_checks": len(checks),
            "external_evidence_checked": external_checked,
        },
        "checks": checks,
    }
    save_json_atomic(VALIDATION_RAW, payload)
    save_text_atomic(VALIDATION_REPORT, _render(payload))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the bounded React Corpus report coverage registry.")
    parser.add_argument("--evidence-root", type=Path, help="Optional preserved _reports directory to re-read and hash.")
    args = parser.parse_args()
    payload = run(args.evidence_root)
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

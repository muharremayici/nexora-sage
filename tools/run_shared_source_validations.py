from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.python_source_index import PythonSourceIndex
from tools import validate_hardcoded_decision_inventory as hardcoded_inventory
from tools import validate_large_artifact_sqlite_first_access as large_artifact_access


RECEIPT_PATH = RAW_DIR / "shared_python_source_validation_receipt.json"
NO_AUTHORITY = "execution_evidence_only_no_release_or_publication_authority"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _consumer_row(
    *,
    consumer_id: str,
    artifact_path: Path,
    status: str,
) -> dict[str, Any]:
    return {
        "id": consumer_id,
        "status": status,
        "artifact": artifact_path.relative_to(ROOT).as_posix(),
        "artifact_sha256": _sha256_file(artifact_path),
    }


def _overall_status(
    *,
    source_once_pass: bool,
    exact_consumer_union_pass: bool,
    consumers_pass: bool,
) -> str:
    return (
        "PASS"
        if source_once_pass and exact_consumer_union_pass and consumers_pass
        else "FAIL"
    )


def run() -> dict[str, Any]:
    started = time.perf_counter()
    hardcoded_files = hardcoded_inventory._python_files()
    large_artifact_files = large_artifact_access._python_files()
    source_files = sorted(
        set(hardcoded_files) | set(large_artifact_files),
        key=lambda path: path.resolve().as_posix().casefold(),
    )
    source_index = PythonSourceIndex.build(ROOT, source_files)

    hardcoded_payload = hardcoded_inventory.build_validation(source_index=source_index)
    save_json_atomic(hardcoded_inventory.RAW_OUTPUT_PATH, hardcoded_payload)
    hardcoded_inventory.write_report(hardcoded_payload)

    large_artifact_payload = large_artifact_access.validate(source_index=source_index)
    consumers = [
        _consumer_row(
            consumer_id="hardcoded_decision_inventory",
            artifact_path=hardcoded_inventory.RAW_OUTPUT_PATH,
            status=str(hardcoded_payload.get("status") or "FAIL"),
        ),
        _consumer_row(
            consumer_id="large_artifact_sqlite_first_access",
            artifact_path=large_artifact_access.OUTPUT_JSON,
            status=str(large_artifact_payload.get("summary", {}).get("status") or "FAIL"),
        ),
    ]
    consumer_source_sets = {
        "hardcoded_decision_inventory": [
            path.resolve().relative_to(ROOT.resolve()).as_posix()
            for path in hardcoded_files
        ],
        "large_artifact_sqlite_first_access": [
            path.resolve().relative_to(ROOT.resolve()).as_posix()
            for path in large_artifact_files
        ],
    }
    manifest_paths = {record.relative_path for record in source_index.records}
    consumer_source_union = {
        path
        for paths in consumer_source_sets.values()
        for path in paths
    }
    source_once_pass = (
        source_index.read_count == len(source_files)
        and source_index.parse_count == len(source_files)
    )
    exact_consumer_union_pass = (
        set(consumer_source_sets) == {
            "hardcoded_decision_inventory",
            "large_artifact_sqlite_first_access",
        }
        and all(consumer_source_sets.values())
        and all(
            len(paths) == len(set(paths))
            for paths in consumer_source_sets.values()
        )
        and consumer_source_union == manifest_paths
    )
    consumers_pass = all(row["status"] == "PASS" for row in consumers)
    status = _overall_status(
        source_once_pass=source_once_pass,
        exact_consumer_union_pass=exact_consumer_union_pass,
        consumers_pass=consumers_pass,
    )
    receipt = {
        "meta": {
            "kind": "shared_python_source_validation_receipt",
            "version": "v1",
            "generated_at": _utc_now(),
            "authority": NO_AUTHORITY,
        },
        "status": status,
        "execution": {
            "duration_seconds": round(time.perf_counter() - started, 3),
            "mode": "single_read_single_parse_shared_python_source_index",
        },
        "source_index": source_index.manifest(),
        "consumer_source_sets": consumer_source_sets,
        "consumers": consumers,
        "checks": [
            {
                "name": "source_files_are_read_and_parsed_once",
                "passed": source_once_pass,
                "details": {
                    "source_files": len(source_files),
                    "read_count": source_index.read_count,
                    "parse_count": source_index.parse_count,
                },
            },
            {
                "name": "consumer_source_sets_cover_exact_index",
                "passed": exact_consumer_union_pass,
                "details": {
                    "manifest_files": len(manifest_paths),
                    "consumer_union_files": len(consumer_source_union),
                },
            },
            {
                "name": "all_shared_source_consumers_pass",
                "passed": consumers_pass,
                "details": {
                    row["id"]: row["status"]
                    for row in consumers
                },
            },
        ],
    }
    save_json_atomic(RECEIPT_PATH, receipt)
    return receipt


def main() -> int:
    try:
        receipt = run()
    except Exception as exc:
        failure = {
            "meta": {
                "kind": "shared_python_source_validation_receipt",
                "version": "v1",
                "generated_at": _utc_now(),
                "authority": NO_AUTHORITY,
            },
            "status": "FAIL",
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "source_index": {},
            "consumers": [],
            "checks": [],
        }
        save_json_atomic(RECEIPT_PATH, failure)
        raise
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "source_files": receipt["source_index"]["source_files"],
                "read_count": receipt["source_index"]["read_count"],
                "parse_count": receipt["source_index"]["parse_count"],
                "consumers": {
                    row["id"]: row["status"]
                    for row in receipt["consumers"]
                },
            },
            ensure_ascii=False,
        )
    )
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

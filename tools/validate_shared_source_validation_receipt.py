from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR
from tools.core.json_io import load_json_file


RECEIPT_PATH = RAW_DIR / "shared_python_source_validation_receipt.json"
NO_AUTHORITY = "execution_evidence_only_no_release_or_publication_authority"
CONSUMER_CONTRACTS = {
    "hardcoded_decision_inventory": {
        "artifact": "output/.raw/hardcoded_decision_inventory_validation.json",
        "status_path": ("status",),
    },
    "large_artifact_sqlite_first_access": {
        "artifact": "output/.raw/large_artifact_sqlite_first_access_validation.json",
        "status_path": ("summary", "status"),
    },
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_sha256(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _nested_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def validate_consumer(consumer_id: str) -> dict[str, Any]:
    if consumer_id not in CONSUMER_CONTRACTS:
        raise ValueError(f"Unknown shared-source consumer: {consumer_id}")
    receipt = load_json_file(RECEIPT_PATH, {})
    source_index = receipt.get("source_index") if isinstance(receipt.get("source_index"), dict) else {}
    files = source_index.get("files") if isinstance(source_index.get("files"), list) else []
    manifest_paths = {
        str(row.get("path") or "")
        for row in files
        if isinstance(row, dict) and row.get("path")
    }
    consumer_source_sets = (
        receipt.get("consumer_source_sets")
        if isinstance(receipt.get("consumer_source_sets"), dict)
        else {}
    )
    consumer_source_union = {
        str(path)
        for paths in consumer_source_sets.values()
        if isinstance(paths, list)
        for path in paths
        if isinstance(path, str) and path
    }
    consumer_list = [
        row
        for row in receipt.get("consumers", [])
        if isinstance(row, dict)
    ]
    consumer_rows = {
        str(row.get("id") or ""): row
        for row in consumer_list
    }
    consumer = consumer_rows.get(consumer_id, {})
    artifact_text = str(consumer.get("artifact") or "")
    expected_artifact_text = str(CONSUMER_CONTRACTS[consumer_id]["artifact"])
    artifact_path = ROOT / expected_artifact_text
    artifact_payload = load_json_file(artifact_path, {}) if artifact_path.is_file() else {}
    checks = [
        {
            "name": "receipt_contract_is_current_and_non_authoritative",
            "passed": (
                receipt.get("meta", {}).get("kind") == "shared_python_source_validation_receipt"
                and receipt.get("meta", {}).get("version") == "v1"
                and receipt.get("meta", {}).get("authority") == NO_AUTHORITY
                and receipt.get("status") == "PASS"
            ),
        },
        {
            "name": "source_index_manifest_is_content_bound",
            "passed": (
                bool(files)
                and len(manifest_paths) == len(files)
                and source_index.get("content_sha256") == _manifest_sha256(files)
                and source_index.get("source_files") == len(files)
                and source_index.get("read_count") == len(files)
                and source_index.get("parse_count") == len(files)
            ),
        },
        {
            "name": "consumer_rows_are_exact_and_unique",
            "passed": (
                len(consumer_list) == len(consumer_rows) == len(CONSUMER_CONTRACTS)
                and set(consumer_rows) == set(CONSUMER_CONTRACTS)
            ),
        },
        {
            "name": "consumer_source_sets_cover_exact_index",
            "passed": (
                set(consumer_source_sets) == set(CONSUMER_CONTRACTS)
                and all(
                    isinstance(paths, list) and bool(paths)
                    for paths in consumer_source_sets.values()
                )
                and all(
                    len(paths) == len(set(paths))
                    for paths in consumer_source_sets.values()
                    if isinstance(paths, list)
                )
                and consumer_source_union == manifest_paths
            ),
        },
        {
            "name": "consumer_artifact_matches_producer_receipt",
            "passed": (
                consumer.get("status") == "PASS"
                and artifact_text == expected_artifact_text
                and artifact_path.is_file()
                and consumer.get("artifact_sha256") == _sha256_file(artifact_path)
                and _nested_value(
                    artifact_payload,
                    CONSUMER_CONTRACTS[consumer_id]["status_path"],
                ) == "PASS"
            ),
        },
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    return {
        "status": status,
        "consumer": consumer_id,
        "receipt": RECEIPT_PATH.relative_to(ROOT).as_posix(),
        "artifact": artifact_text or None,
        "checks": checks,
        "authority": NO_AUTHORITY,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer", required=True, choices=sorted(CONSUMER_CONTRACTS))
    args = parser.parse_args()
    result = validate_consumer(args.consumer)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

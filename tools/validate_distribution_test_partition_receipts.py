"""Validate and combine isolated distribution-test family receipts.

The family runners remain the owners of pytest execution. This validator is a
small, fail-closed join: it proves that one current PASS receipt exists for
every family and that their ordered paths are the exact selected profile union.
It grants no release or publication authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_distribution_tests as distribution_tests


RECEIPT_SCHEMA = "nexora_sage_distribution_test_partition_receipt_v1"
RECEIPT_AUTHORITY = "test_evidence_only_no_release_or_publication_authority"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_receipt(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Shard receipt must be an object: {path}")
    return payload


def _validated_shard_row(
    family: str,
    expected_tests: list[str],
    payload: Mapping[str, Any],
    *,
    selected_profile: str,
    partition_contract_sha256: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema") != RECEIPT_SCHEMA:
        errors.append("schema")
    if payload.get("authority") != RECEIPT_AUTHORITY:
        errors.append("authority")
    if payload.get("status") != "PASS":
        errors.append("status")
    if payload.get("profile") != selected_profile:
        errors.append("profile")
    if payload.get("collect_only") is not False:
        errors.append("collect_only")
    if payload.get("partition_contract_sha256") != partition_contract_sha256:
        errors.append("partition_contract_sha256")
    if payload.get("exact_profile_union") is not False:
        errors.append("exact_profile_union")
    if payload.get("selected_test_count") != len(expected_tests):
        errors.append("selected_test_count")
    expected_union_sha256 = distribution_tests._canonical_sha256(expected_tests)
    if payload.get("selected_test_union_sha256") != expected_union_sha256:
        errors.append("selected_test_union_sha256")

    shards = payload.get("shards")
    if not isinstance(shards, list) or len(shards) != 1 or not isinstance(shards[0], Mapping):
        errors.append("single_shard")
        shard: Mapping[str, Any] = {}
    else:
        shard = shards[0]
    if shard.get("id") != family:
        errors.append("shard_id")
    if shard.get("status") != "PASS" or shard.get("returncode") != 0:
        errors.append("shard_execution")
    if shard.get("test_count") != len(expected_tests):
        errors.append("shard_test_count")
    if shard.get("test_paths") != expected_tests:
        errors.append("shard_test_paths")
    if shard.get("test_paths_sha256") != expected_union_sha256:
        errors.append("shard_test_paths_sha256")
    if errors:
        raise ValueError(f"Shard receipt mismatch for {family}: {','.join(sorted(errors))}")
    return {
        "id": family,
        "status": "PASS",
        "returncode": 0,
        "test_count": len(expected_tests),
        "test_paths_sha256": expected_union_sha256,
        "test_paths": expected_tests,
    }


def combine_partition_receipts(
    profile_payload: Mapping[str, Any],
    profile_name: str,
    shard_receipts: Mapping[str, Mapping[str, Any]],
    *,
    source_receipts: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    partitions, _, selected_profile = distribution_tests._resolve_test_partitions_from_payload(
        dict(profile_payload), profile_name
    )
    expected_families = list(partitions)
    if list(shard_receipts) != expected_families:
        raise ValueError(
            "Shard receipt order/set mismatch: "
            f"expected={expected_families}:actual={list(shard_receipts)}"
        )
    partition_sha256 = distribution_tests._canonical_sha256(profile_payload["partition"])
    shard_rows = [
        _validated_shard_row(
            family,
            partitions[family],
            shard_receipts[family],
            selected_profile=selected_profile,
            partition_contract_sha256=partition_sha256,
        )
        for family in expected_families
    ]
    selected_union = [path for family in expected_families for path in partitions[family]]
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "authority": RECEIPT_AUTHORITY,
        "profile": selected_profile,
        "collect_only": False,
        "partition_contract_sha256": partition_sha256,
        "selected_test_union_sha256": distribution_tests._canonical_sha256(selected_union),
        "selected_test_count": len(selected_union),
        "exact_profile_union": True,
        "shards": shard_rows,
    }
    if source_receipts is not None:
        receipt["source_receipts"] = [
            {
                "id": family,
                "path": source_receipts[family]["path"],
                "sha256": source_receipts[family]["sha256"],
            }
            for family in expected_families
        ]
    return receipt


def _parse_shard_receipts(values: list[str]) -> dict[str, Path]:
    rows: dict[str, Path] = {}
    raw_root = (ROOT / "output" / ".raw").resolve()
    for value in values:
        family, separator, raw_path = value.partition("=")
        if not separator or not family or not raw_path or family in rows:
            raise ValueError(f"Invalid or duplicate --shard-receipt: {value}")
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        try:
            path.relative_to(raw_root)
        except ValueError as exc:
            raise ValueError(f"Shard receipt escapes output/.raw: {value}") from exc
        if not path.is_file():
            raise ValueError(f"Shard receipt is missing: {path}")
        rows[family] = path
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="auto")
    parser.add_argument("--shard-receipt", action="append", default=[])
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()

    output_path = Path(args.receipt)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    try:
        paths = _parse_shard_receipts(args.shard_receipt)
        payloads = {family: _load_receipt(path) for family, path in paths.items()}
        source_rows = {
            family: {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(path),
            }
            for family, path in paths.items()
        }
        receipt = combine_partition_receipts(
            distribution_tests._load_profile(),
            args.profile,
            payloads,
            source_receipts=source_rows,
        )
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "FAIL",
            "authority": RECEIPT_AUTHORITY,
            "profile": args.profile,
            "collect_only": False,
            "exact_profile_union": False,
            "errors": [str(exc)],
            "shards": [],
        }
    distribution_tests._write_json_atomic(output_path, receipt)
    print(
        f"[distribution-test-receipts] status={receipt['status']} "
        f"shards={len(receipt['shards'])} tests={receipt.get('selected_test_count', 0)}",
        flush=True,
    )
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

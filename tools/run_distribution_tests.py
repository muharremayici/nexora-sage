from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "distribution_test_profiles.json"
PUBLIC_MANIFEST = ROOT / "PUBLIC_DISTRIBUTION_MANIFEST.json"
KNOWN_CLASSIFICATIONS = {"public_target_repository", "private_maintainer"}
KNOWN_PROFILES = {"canonical_all", "public_target_repository"}


def _load_profile() -> dict[str, Any]:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Distribution test profile must be a JSON object")
    return payload


def _canonical_classification(payload: dict[str, Any]) -> tuple[dict[str, str], str]:
    classification = payload.get("classification")
    if not isinstance(classification, dict):
        raise ValueError("Canonical distribution test profile is missing classification")
    test_glob = str(classification.get("test_glob") or "")
    default = str(classification.get("default") or "")
    overrides = classification.get("overrides")
    if not test_glob or default != "unclassified" or not isinstance(overrides, dict):
        raise ValueError("Canonical distribution test classification is invalid")

    discovered = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.glob(test_glob)
        if path.is_file()
    }
    if set(overrides) != KNOWN_CLASSIFICATIONS:
        raise ValueError("Canonical distribution test overrides must declare both authority classes")
    classified = {path: default for path in discovered}
    explicitly_classified: set[str] = set()
    for class_name, paths in overrides.items():
        if not isinstance(paths, list):
            raise ValueError(f"Unknown or invalid test classification: {class_name}")
        for raw_path in paths:
            path = str(raw_path)
            if path in explicitly_classified:
                raise ValueError(f"Test has multiple explicit classifications: {path}")
            if path not in discovered:
                raise ValueError(f"Classified test does not exist: {path}")
            explicitly_classified.add(path)
            classified[path] = class_name
    unclassified = sorted(path for path, class_name in classified.items() if class_name == "unclassified")
    if unclassified:
        raise ValueError(f"Tests require explicit authority classification: {unclassified}")
    _validate_partition_inventory(payload, sorted(discovered))
    return classified, test_glob


def validate_canonical_distribution_test_inventory() -> dict[str, str]:
    """Fail closed unless every canonical test has one authority classification."""
    classified, _ = _canonical_classification(_load_profile())
    return classified


def _test_token(path: str) -> str:
    name = Path(path).name
    if not name.startswith("test_") or not name.endswith(".py"):
        raise ValueError(f"Test path cannot be partitioned: {path}")
    token = name[len("test_") : -len(".py")].split("_", 1)[0]
    if not token or not token.replace("-", "").isalnum() or token.lower() != token:
        raise ValueError(f"Test path has an invalid partition token: {path}")
    return token


def _partition_contract(payload: dict[str, Any]) -> tuple[list[str], dict[str, str], dict[str, set[str]]]:
    partition = payload.get("partition")
    if not isinstance(partition, dict):
        raise ValueError("Distribution test profile is missing partition")
    if (
        partition.get("version") != "1.0"
        or partition.get("method") != "filename_first_token"
        or partition.get("unknown_token") != "fail_closed"
    ):
        raise ValueError("Distribution test partition contract is invalid")
    execution_order = partition.get("execution_order")
    families = partition.get("families")
    if (
        not isinstance(execution_order, list)
        or not execution_order
        or len(set(map(str, execution_order))) != len(execution_order)
        or not isinstance(families, dict)
        or set(map(str, execution_order)) != set(map(str, families))
    ):
        raise ValueError("Distribution test partition order is not an exact family set")

    token_owner: dict[str, str] = {}
    family_profiles: dict[str, set[str]] = {}
    for raw_family in execution_order:
        family = str(raw_family)
        row = families.get(family)
        if not isinstance(row, dict):
            raise ValueError(f"Distribution test partition family is invalid: {family}")
        profiles = row.get("profiles")
        tokens = row.get("tokens")
        if (
            not isinstance(profiles, list)
            or not profiles
            or len(set(map(str, profiles))) != len(profiles)
            or not set(map(str, profiles)) <= KNOWN_PROFILES
            or not isinstance(tokens, list)
            or not tokens
            or len(set(map(str, tokens))) != len(tokens)
        ):
            raise ValueError(f"Distribution test partition family is incomplete: {family}")
        family_profiles[family] = set(map(str, profiles))
        for raw_token in tokens:
            token = str(raw_token)
            if not token or token.lower() != token or not token.replace("-", "").isalnum():
                raise ValueError(f"Distribution test partition token is invalid: {token}")
            if token in token_owner:
                raise ValueError(
                    f"Distribution test partition token has multiple owners: {token}"
                )
            token_owner[token] = family
    return list(map(str, execution_order)), token_owner, family_profiles


def _validate_partition_inventory(payload: dict[str, Any], test_paths: list[str]) -> None:
    _, token_owner, _ = _partition_contract(payload)
    observed = {_test_token(path) for path in test_paths}
    declared = set(token_owner)
    if observed != declared:
        missing = sorted(observed - declared)
        stale = sorted(declared - observed)
        raise ValueError(
            "Distribution test partition token inventory mismatch: "
            f"unowned={missing}:stale={stale}"
        )


def _partition_selected_tests(
    payload: dict[str, Any],
    test_paths: list[str],
    profile_name: str,
) -> dict[str, list[str]]:
    execution_order, token_owner, family_profiles = _partition_contract(payload)
    partitions = {family: [] for family in execution_order}
    for path in sorted(test_paths):
        token = _test_token(path)
        family = token_owner.get(token)
        if family is None:
            raise ValueError(f"Test partition token is unowned: {token}:{path}")
        if profile_name not in family_profiles[family]:
            raise ValueError(
                f"Test partition family is not applicable to profile: {family}:{profile_name}"
            )
        partitions[family].append(path)
    for family in execution_order:
        applicable = profile_name in family_profiles[family]
        if applicable != bool(partitions[family]):
            raise ValueError(
                f"Test partition profile applicability mismatch: {family}:{profile_name}"
            )
    flattened = [path for family in execution_order for path in partitions[family]]
    if len(flattened) != len(set(flattened)) or sorted(flattened) != sorted(test_paths):
        raise ValueError("Distribution test partitions are not an exact test union")
    return partitions


def _projected_public_tests(payload: dict[str, Any]) -> tuple[list[str], str]:
    projection = payload.get("public_projection")
    if not isinstance(projection, dict):
        raise ValueError("Public distribution test profile is missing public_projection")
    paths = projection.get("included_test_paths")
    target_fixture = str(projection.get("target_fixture") or "")
    if not isinstance(paths, list) or not paths or not target_fixture:
        raise ValueError("Public distribution test projection is incomplete")
    normalized = sorted({str(path) for path in paths})
    if len(normalized) != len(paths):
        raise ValueError("Public distribution test projection contains duplicates")
    actual = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tools" / "tests").glob("test_*.py")
        if path.is_file()
    )
    if actual != normalized:
        raise ValueError("Public distribution test inventory does not match shipped tests")
    _validate_partition_inventory(payload, normalized)
    return normalized, target_fixture


def _resolve_tests_from_payload(
    payload: dict[str, Any], profile_name: str
) -> tuple[list[str], str | None]:
    if "public_projection" in payload:
        if profile_name not in {"auto", "public_target_repository"}:
            raise ValueError("A public distribution cannot run private maintainer tests")
        return _projected_public_tests(payload)

    classified, _ = _canonical_classification(payload)
    profiles = payload.get("profiles")
    selected = "canonical_all" if profile_name == "auto" else profile_name
    if not isinstance(profiles, dict) or selected not in profiles:
        raise ValueError(f"Unknown distribution test profile: {selected}")
    row = profiles[selected]
    if not isinstance(row, dict):
        raise ValueError(f"Invalid distribution test profile: {selected}")
    classes = row.get("classifications")
    if not isinstance(classes, list) or not classes or not set(classes) <= KNOWN_CLASSIFICATIONS:
        raise ValueError(f"Invalid classifications for distribution test profile: {selected}")
    tests = sorted(path for path, class_name in classified.items() if class_name in classes)
    target_fixture = str(row.get("target_fixture") or "") or None
    return tests, target_fixture


def resolve_tests(profile_name: str) -> tuple[list[str], str | None]:
    return _resolve_tests_from_payload(_load_profile(), profile_name)


def _resolve_test_partitions_from_payload(
    payload: dict[str, Any], profile_name: str
) -> tuple[dict[str, list[str]], str | None, str]:
    tests, target_fixture = _resolve_tests_from_payload(payload, profile_name)
    selected_profile = (
        "public_target_repository"
        if "public_projection" in payload
        else ("canonical_all" if profile_name == "auto" else profile_name)
    )
    return (
        _partition_selected_tests(payload, tests, selected_profile),
        target_fixture,
        selected_profile,
    )


def resolve_test_partitions(profile_name: str) -> tuple[dict[str, list[str]], str | None, str]:
    payload = _load_profile()
    return _resolve_test_partitions_from_payload(payload, profile_name)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _run_pytest(
    tests: list[str],
    *,
    target_fixture: str | None,
    collect_only: bool,
    verbose: bool,
    basetemp: Path | None,
) -> int:
    env = os.environ.copy()
    temporary_target: tempfile.TemporaryDirectory[str] | None = None
    temporary_pytest: tempfile.TemporaryDirectory[str] | None = None
    try:
        if target_fixture:
            resolved_fixture = (ROOT / target_fixture).resolve()
            if not resolved_fixture.is_dir():
                raise ValueError(f"target fixture is missing: {target_fixture}")
            try:
                resolved_fixture.relative_to(ROOT)
            except ValueError as exc:
                raise ValueError("target fixture escapes the distribution root") from exc
            temporary_target = tempfile.TemporaryDirectory(
                prefix="nexora-sage-test-target-",
                ignore_cleanup_errors=True,
            )
            external_target = Path(temporary_target.name) / "repository"
            shutil.copytree(resolved_fixture, external_target)
            env["CODEMAPS_TARGET_ROOT"] = str(external_target)

        if basetemp is None:
            temporary_pytest = tempfile.TemporaryDirectory(
                prefix="nexora-sage-pytest-",
                ignore_cleanup_errors=True,
            )
            resolved_basetemp = Path(temporary_pytest.name)
        else:
            resolved_basetemp = basetemp.resolve()
            resolved_basetemp.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-B", "-m", "pytest", *tests]
        command.extend(
            [
                "-vv" if verbose else "-q",
                "--basetemp",
                str(resolved_basetemp),
                "-p",
                "no:cacheprovider",
            ]
        )
        if collect_only:
            command.append("--collect-only")
        return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    finally:
        if temporary_pytest is not None:
            temporary_pytest.cleanup()
        if temporary_target is not None:
            temporary_target.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the authority-scoped Nexora SAGE test profile.")
    parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "canonical_all", "public_target_repository"],
    )
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all-shards", action="store_true")
    mode.add_argument("--shard")
    parser.add_argument("--receipt")
    parser.add_argument("--basetemp-root")
    args = parser.parse_args()

    try:
        if args.receipt and not (args.all_shards or args.shard):
            raise ValueError("--receipt requires --all-shards or --shard")
        if args.basetemp_root and not (args.all_shards or args.shard):
            raise ValueError("--basetemp-root requires --all-shards or --shard")
        profile_payload = _load_profile()
        partitions: dict[str, list[str]] | None = None
        selected_profile = ""
        if args.all_shards or args.shard:
            partitions, target_fixture, selected_profile = _resolve_test_partitions_from_payload(
                profile_payload, args.profile
            )
            if args.shard and args.shard not in partitions:
                raise ValueError(f"Unknown distribution test shard: {args.shard}")
            tests = [path for shard_tests in partitions.values() for path in shard_tests]
        else:
            tests, target_fixture = _resolve_tests_from_payload(profile_payload, args.profile)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[distribution-tests] FAIL: {exc}", file=sys.stderr)
        return 2

    try:
        print(
            f"[distribution-tests] profile={args.profile} tests={len(tests)} "
            f"public_distribution={PUBLIC_MANIFEST.is_file()} collect_only={args.collect_only} "
            f"partitioned={bool(partitions)}",
            flush=True,
        )
        if partitions is None:
            return _run_pytest(
                tests,
                target_fixture=target_fixture,
                collect_only=args.collect_only,
                verbose=args.verbose,
                basetemp=None,
            )

        selected_families = [args.shard] if args.shard else list(partitions)
        shard_results: list[dict[str, Any]] = []
        basetemp_root = Path(args.basetemp_root) if args.basetemp_root else None
        for family in selected_families:
            shard_tests = partitions[family]
            print(
                f"[distribution-tests] shard={family} tests={len(shard_tests)}",
                flush=True,
            )
            returncode = _run_pytest(
                shard_tests,
                target_fixture=target_fixture,
                collect_only=args.collect_only,
                verbose=args.verbose,
                basetemp=(basetemp_root / family if basetemp_root else None),
            )
            shard_results.append(
                {
                    "id": family,
                    "status": "PASS" if returncode == 0 else "FAIL",
                    "returncode": returncode,
                    "test_count": len(shard_tests),
                    "test_paths_sha256": _canonical_sha256(shard_tests),
                    "test_paths": shard_tests,
                }
            )

        status = "PASS" if all(row["returncode"] == 0 for row in shard_results) else "FAIL"
        receipt = {
            "schema": "nexora_sage_distribution_test_partition_receipt_v1",
            "status": status,
            "authority": "test_evidence_only_no_release_or_publication_authority",
            "profile": selected_profile,
            "collect_only": bool(args.collect_only),
            "partition_contract_sha256": _canonical_sha256(profile_payload["partition"]),
            "selected_test_union_sha256": _canonical_sha256(
                [path for family in selected_families for path in partitions[family]]
            ),
            "selected_test_count": sum(len(partitions[family]) for family in selected_families),
            "exact_profile_union": not bool(args.shard),
            "shards": shard_results,
        }
        if args.receipt:
            receipt_path = Path(args.receipt)
            if not receipt_path.is_absolute():
                receipt_path = ROOT / receipt_path
            _write_json_atomic(receipt_path, receipt)
        print(
            f"[distribution-tests] shard_union={status} shards={len(shard_results)} "
            f"tests={receipt['selected_test_count']}",
            flush=True,
        )
        return 0 if status == "PASS" else 1
    except (OSError, ValueError) as exc:
        print(f"[distribution-tests] FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

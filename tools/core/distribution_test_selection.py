"""One dependency-light distribution inventory, partition and payload-contract owner.

Consumes caller-supplied payloads and roots. It never imports the test CLI, pytest
plugin, proof/reuse engine or source recipe; it does not launch or admit tests.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

KNOWN_CLASSIFICATIONS = {"public_target_repository", "private_maintainer"}
KNOWN_PROFILES = {"canonical_all", "public_target_repository"}
_STARTUP_PROTOCOLS = ("pytest_loaded_startup_metadata_v1", "pytest_loaded_startup_metadata_v2")
_STARTUP_COMPONENT_HASHES = ("plugin_registration_sequence_sha256",
                             "plugin_module_type_sequence_sha256",
                             "plugin_module_type_multiset_sha256")


OWNED_PROBE_FAILURE_PROTOCOL = "owned_runtime_probe_failure_v1"


def validate_owned_probe_failure_policy(policy: object) -> None:
    """Validate the optional closed diagnostic capability; never admit evidence."""
    if (type(policy) is not dict or len(policy) != 4 or any(type(key) is not str for key in policy)
            or set(policy) != {"protocol", "producer_paths", "maximum_detail_bytes", "maximum_properties"}
            or type(policy["protocol"]) is not str or policy["protocol"] != OWNED_PROBE_FAILURE_PROTOCOL
            or type(policy["maximum_detail_bytes"]) is not int
            or not 256 <= policy["maximum_detail_bytes"] <= 1024
            or type(policy["maximum_properties"]) is not int
            or not 1 <= policy["maximum_properties"] <= 32):
        raise ValueError("invalid_owned_probe_failure_policy")
    paths = policy["producer_paths"]
    if (type(paths) is not list or not 1 <= len(paths) <= 8
            or any(type(path) is not str or len(path) > 256
                   or re.fullmatch(r"tools/tests/test_[a-z0-9_]+\.py", path) is None for path in paths)
            or len(set(paths)) != len(paths)):
        raise ValueError("invalid_owned_probe_failure_policy")


def validate_execution_contract(payload: dict) -> dict:
    contract = payload.get("test_execution")
    if not isinstance(contract, dict) or contract.get("version") != "1.0":
        raise ValueError("test_execution_contract_required")
    signing = contract.get("synthetic_signing")
    diagnostics = contract.get("diagnostics")
    early = contract.get("early_checks")
    if (not isinstance(signing, dict)
            or signing.get("environment_keys") != ["NEXORA_HITL_SECRET", "CODEMAPS_HITL_SECRET"]
            or not isinstance(signing.get("public_test_value"), str)
            or not signing["public_test_value"].startswith("sage-synthetic-")
            or not isinstance(signing.get("test_paths"), list) or not signing["test_paths"]
            or not isinstance(diagnostics, dict)
            or diagnostics.get("schema") != "nexora_distribution_pytest_failure_journal_v1"
            or type(diagnostics.get("maximum_failure_events")) is not int
            or not 1 <= diagnostics["maximum_failure_events"] <= 128
            or type(diagnostics.get("maximum_line_bytes")) is not int
            or not 4096 <= diagnostics["maximum_line_bytes"] <= 1048576
            or not isinstance(early, list)):
        raise ValueError("invalid_test_execution_contract")
    private = payload.get("classification", {}).get("overrides", {}).get("private_maintainer", [])
    if len(signing["test_paths"]) != len(set(signing["test_paths"])):
        raise ValueError("duplicate_test_launch_prerequisite")
    for path in signing["test_paths"]:
        if path not in private:
            raise ValueError("synthetic_signing_requires_private_test_authority")
    inventory = set().union(*payload.get("classification", {}).get("overrides", {}).values())
    for row in early:
        if (not isinstance(row, dict) or not isinstance(row.get("changed_paths"), list)
                or not row["changed_paths"] or not isinstance(row.get("test_paths"), list)
                or not row["test_paths"] or not set(row["test_paths"]) <= inventory):
            raise ValueError("invalid_early_consumer_check")
        if "development_test_nodes" in row:
            nodes = row["development_test_nodes"]
            if (not isinstance(nodes, list) or not nodes or any(not isinstance(n, str) for n in nodes)
                    or len(nodes) != len(set(nodes))):
                raise ValueError("invalid_development_consumer_nodes")
            for node in nodes:
                path, separator, function = node.partition("::")
                if (separator != "::" or path not in row["test_paths"]
                        or not re.fullmatch(r"test_[A-Za-z0-9_]{1,160}", function)):
                    raise ValueError("development_consumer_node_outside_reviewed_owner")
    startup = diagnostics.get('startup_observation')
    if startup is not None:
        if (not isinstance(startup, dict)
                or set(startup) != {'protocol', 'maximum_plugins', 'maximum_metadata_bytes', 'environment_keys', 'ini_keys'}
                or startup['protocol'] not in _STARTUP_PROTOCOLS
                or type(startup['maximum_plugins']) is not int or not 1 <= startup['maximum_plugins'] <= diagnostics['maximum_line_bytes']
                or type(startup['maximum_metadata_bytes']) is not int or not 1 <= startup['maximum_metadata_bytes'] <= diagnostics['maximum_line_bytes']
                or not isinstance(startup['environment_keys'], list) or len(startup['environment_keys']) > 64
                or any(not isinstance(key, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', key)
                       for key in startup['environment_keys'])
                or len(set(startup['environment_keys'])) != len(startup['environment_keys'])):
            raise ValueError('invalid_pytest_startup_observation_policy')
        keys = startup['ini_keys']
        if (not isinstance(keys, list) or not keys or len(keys) > 64
                or any(not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,95}', key) for key in keys)
                or len(set(keys)) != len(keys)):
            raise ValueError('invalid_pytest_startup_observation_policy')
    if "owned_probe_failure_observation" in diagnostics:
        probe = diagnostics["owned_probe_failure_observation"]
        validate_owned_probe_failure_policy(probe)
        if (probe["maximum_detail_bytes"] > diagnostics["maximum_line_bytes"]
                or not set(probe["producer_paths"]) <= inventory):
            raise ValueError("invalid_owned_probe_failure_policy")
    return contract


def _canonical_classification(payload: dict[str, Any], *, root: Path) -> tuple[dict[str, str], str]:
    validate_execution_contract(payload)
    classification = payload.get("classification")
    if not isinstance(classification, dict):
        raise ValueError("Canonical distribution test profile is missing classification")
    test_glob = str(classification.get("test_glob") or "")
    default = str(classification.get("default") or "")
    overrides = classification.get("overrides")
    if not test_glob or default != "unclassified" or not isinstance(overrides, dict):
        raise ValueError("Canonical distribution test classification is invalid")

    selected_root = root
    discovered = {
        path.relative_to(selected_root).as_posix()
        for path in selected_root.glob(test_glob)
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
    return {
        family: partitions[family]
        for family in execution_order
        if profile_name in family_profiles[family]
    }


def _projected_public_tests(payload: dict[str, Any], *, root: Path) -> tuple[list[str], str]:
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
        path.relative_to(root).as_posix()
        for path in (root / "tools" / "tests").glob("test_*.py")
        if path.is_file()
    )
    if actual != normalized:
        raise ValueError("Public distribution test inventory does not match shipped tests")
    _validate_partition_inventory(payload, normalized)
    return normalized, target_fixture


def _resolve_tests_from_payload(
    payload: dict[str, Any], profile_name: str, *, root: Path, alternate_root: bool = False
) -> tuple[list[str], str | None]:
    if "public_projection" in payload:
        if alternate_root:
            raise ValueError("Alternate-root selection is canonical-only")
        if profile_name not in {"auto", "public_target_repository"}:
            raise ValueError("A public distribution cannot run private maintainer tests")
        return _projected_public_tests(payload, root=root)

    classified, _ = _canonical_classification(payload, root=root)
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


def _resolve_test_partitions_from_payload(
    payload: dict[str, Any], profile_name: str, *, root: Path, alternate_root: bool = False
) -> tuple[dict[str, list[str]], str | None, str]:
    tests, target_fixture = _resolve_tests_from_payload(payload, profile_name, root=root, alternate_root=alternate_root)
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

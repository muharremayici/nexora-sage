from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def validate_third_party_distribution(root: Path) -> dict[str, Any]:
    contract = _load_object(root / "config" / "third_party_distribution_contract.json")
    rows: list[dict[str, Any]] = []
    for dependency in contract.get("vendored_dependencies", []):
        if not isinstance(dependency, dict):
            continue
        lockfile = root / str(dependency.get("lockfile_path") or "")
        runtime_package = root / str(dependency.get("runtime_package_path") or "")
        lock = _load_object(lockfile)
        runtime = _load_object(runtime_package)
        package_key = str(dependency.get("lockfile_package_key") or "")
        locked = lock.get("packages", {}).get(package_key, {})
        if not isinstance(locked, dict):
            locked = {}
        notice_rows = []
        for notice in dependency.get("required_notice_files", []):
            if not isinstance(notice, dict):
                continue
            notice_path = root / str(notice.get("path") or "")
            notice_rows.append(
                {
                    "path": notice.get("path"),
                    "exists": notice_path.is_file(),
                    "hash_matches": _sha256(notice_path) == notice.get("sha256"),
                }
            )
        documentation_path = root / str(dependency.get("human_documentation") or "")
        documentation = (
            documentation_path.read_text(encoding="utf-8")
            if documentation_path.is_file()
            else ""
        )
        documentation_tokens = {
            str(dependency.get("locked_version") or ""),
            str(dependency.get("license_expression") or ""),
            str(dependency.get("lockfile_sha256") or "").upper(),
            *{
                str(notice.get("sha256") or "").upper()
                for notice in dependency.get("required_notice_files", [])
                if isinstance(notice, dict)
            },
        }
        row = {
            "id": dependency.get("id"),
            "lockfile_hash_matches": _sha256(lockfile)
            == dependency.get("lockfile_sha256"),
            "lockfile_version_matches": locked.get("version")
            == dependency.get("locked_version"),
            "lockfile_license_matches": locked.get("license")
            == dependency.get("license_expression"),
            "runtime_version_matches": runtime.get("version")
            == dependency.get("locked_version"),
            "runtime_license_matches": runtime.get("license")
            == dependency.get("license_expression"),
            "notice_files": notice_rows,
            "documentation_matches": bool(documentation_tokens)
            and all(token in documentation for token in documentation_tokens),
        }
        row["passed"] = all(
            row[key]
            for key in (
                "lockfile_hash_matches",
                "lockfile_version_matches",
                "lockfile_license_matches",
                "runtime_version_matches",
                "runtime_license_matches",
                "documentation_matches",
            )
        ) and bool(notice_rows) and all(
            notice["exists"] and notice["hash_matches"] for notice in notice_rows
        )
        rows.append(row)

    non_vendored = contract.get("non_vendored_dependency_manifests", [])
    non_vendored_valid = bool(non_vendored) and all(
        isinstance(row, dict)
        and (root / str(row.get("path") or "")).is_file()
        and row.get("installation_model")
        == "resolved_by_user_environment_package_manager"
        and row.get("license_boundary")
        == "dependency_packages_are_not_relicensed_by_sage"
        for row in non_vendored
    )
    contract_valid = (
        contract.get("_meta", {}).get("kind")
        == "sage.third_party_distribution_contract"
        and contract.get("distribution", {}).get(
            "third_party_terms_remain_authoritative"
        )
        is True
        and contract.get("distribution", {}).get(
            "sage_license_does_not_relicense_third_party_code"
        )
        is True
        and contract.get("distribution", {}).get(
            "public_source_checkout_includes_runtime_directory"
        )
        is False
        and contract.get("distribution", {}).get(
            "installed_runtime_validation_required_for_release_proof"
        )
        is True
    )
    return {
        "contract_valid": contract_valid,
        "vendored_dependencies": rows,
        "non_vendored_dependency_manifests_valid": non_vendored_valid,
        "passed": contract_valid
        and bool(rows)
        and all(row["passed"] for row in rows)
        and non_vendored_valid,
    }

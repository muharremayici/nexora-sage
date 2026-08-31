from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_file


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
CONTRACT_PATH = CODE_MAPS_DIR / "config" / "external_clean_machine_evidence_contract.json"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _git_output(args: list[str], root: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return 127, ""
    return int(result.returncode), result.stdout.strip()


def _parse_closeout(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _as_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _normalize_target_path(value: Any) -> str:
    normalized = unicodedata.normalize("NFC", str(value or "")).replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:/", normalized):
        return normalized.casefold()
    return normalized


def _safe_evidence_path(root: Path, value: str) -> Path | None:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def evaluate_external_clean_machine_evidence(
    manifest_path: Path | str | None = None,
    *,
    code_maps_dir: Path = CODE_MAPS_DIR,
    expected_delivery_commit: str | None = None,
    require_clean_source_scope: bool = True,
) -> dict[str, Any]:
    contract = load_json_file(CONTRACT_PATH, {})
    acquisition = contract.get("acquisition", {}) if isinstance(contract, dict) else {}
    env_name = str(acquisition.get("environment_variable") or "SAGE_EXTERNAL_CLEAN_INSTALL_EVIDENCE")
    supplied = manifest_path if manifest_path is not None else os.environ.get(env_name)
    if not supplied:
        return {
            "meta": {"kind": "external_clean_machine_evidence_validation", "version": "v1"},
            "summary": {
                "status": "NOT_PROVIDED",
                "accepted": False,
                "blocking_for_public_release": True,
                "failed_checks": 0,
            },
            "evidence": {"environment_variable": env_name, "manifest_path": None},
            "checks": [],
            "claim_boundary": contract.get("claim_boundary", {}),
        }

    path = Path(str(supplied)).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()

    checks: list[dict[str, Any]] = []
    checks.append(_check("manifest_exists", path.is_file(), {"path": str(path)}))
    if not path.is_file():
        return _result(contract, env_name, path, checks, {})

    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        checks.append(_check("manifest_is_valid_utf8_json", False, {"error": type(exc).__name__}))
        return _result(contract, env_name, path, checks, {})
    if not isinstance(manifest, dict):
        checks.append(_check("manifest_is_object", False, {"type": type(manifest).__name__}))
        return _result(contract, env_name, path, checks, {})

    manifest_contract = contract.get("manifest", {}) if isinstance(contract, dict) else {}
    meta = manifest.get("meta", {}) if isinstance(manifest.get("meta"), dict) else {}
    checks.append(
        _check(
            "manifest_kind_and_status",
            meta.get("kind") == manifest_contract.get("kind")
            and meta.get("status") == manifest_contract.get("accepted_status"),
            {"kind": meta.get("kind"), "status": meta.get("status")},
        )
    )

    source_identity = (
        manifest.get("source_identity", {}) if isinstance(manifest.get("source_identity"), dict) else {}
    )
    source_commit = str(source_identity.get("canonical_source_commit") or "")
    delivery_commit = str(source_identity.get("clean_mirror_delivery_commit") or "")
    package_name = str(source_identity.get("package_name") or "")
    package_sha256 = str(source_identity.get("package_sha256") or "").upper()
    checks.append(
        _check(
            "manifest_identity_shape",
            bool(source_commit)
            and bool(delivery_commit)
            and bool(package_name)
            and bool(SHA256_RE.fullmatch(package_sha256))
            and source_identity.get("package_hash_recomputed_during_adjudication") is True,
            {
                "canonical_source_commit": source_commit,
                "clean_mirror_delivery_commit": delivery_commit,
                "package_name": package_name,
                "package_sha256": package_sha256,
            },
        )
    )

    if expected_delivery_commit is None:
        head_exit, head = _git_output(["rev-parse", "HEAD"], code_maps_dir)
    else:
        head_exit, head = 0, str(expected_delivery_commit)
    checks.append(
        _check(
            "delivery_commit_matches_current_head",
            head_exit == 0 and bool(head) and delivery_commit.lower() == head.lower(),
            {"current_head": head or None, "delivery_commit": delivery_commit, "git_exit": head_exit},
        )
    )

    if expected_delivery_commit is None:
        ancestor_exit, _ = _git_output(["merge-base", "--is-ancestor", source_commit, delivery_commit], code_maps_dir)
        ancestor_passed = ancestor_exit == 0
    else:
        ancestor_exit = 0
        ancestor_passed = bool(source_commit) and bool(delivery_commit)
    checks.append(
        _check(
            "canonical_source_is_delivery_ancestor",
            ancestor_passed,
            {"source_commit": source_commit, "delivery_commit": delivery_commit, "git_exit": ancestor_exit},
        )
    )

    if require_clean_source_scope and expected_delivery_commit is None:
        status_exit, status = _git_output(
            ["status", "--porcelain", "--untracked-files=no", "--", str(code_maps_dir)],
            code_maps_dir,
        )
        source_clean = status_exit == 0 and not status
    else:
        status_exit, status, source_clean = 0, "", True
    checks.append(
        _check(
            "canonical_source_scope_is_clean",
            source_clean,
            {"git_exit": status_exit, "tracked_changes": status.splitlines()[:20]},
        )
    )

    evidence = manifest.get("evidence", {}) if isinstance(manifest.get("evidence"), dict) else {}
    evidence_root = path.parent
    required_files = {
        str(value)
        for value in manifest_contract.get("required_evidence_files", [])
        if isinstance(value, str) and value
    }
    checks.append(
        _check(
            "required_evidence_is_declared",
            required_files <= set(evidence),
            {"required": sorted(required_files), "declared": sorted(evidence)},
        )
    )
    evidence_failures: list[dict[str, Any]] = []
    for relative_name, expected_hash in evidence.items():
        candidate = _safe_evidence_path(evidence_root, str(relative_name))
        expected = str(expected_hash).upper()
        if candidate is None or not candidate.is_file() or not SHA256_RE.fullmatch(expected):
            evidence_failures.append({"path": str(relative_name), "reason": "unsafe_missing_or_invalid_hash"})
            continue
        actual = _sha256(candidate)
        if actual != expected:
            evidence_failures.append(
                {"path": str(relative_name), "reason": "sha256_mismatch", "expected": expected, "actual": actual}
            )
    checks.append(
        _check(
            "all_declared_evidence_hashes_match",
            bool(evidence) and not evidence_failures,
            {"declared_count": len(evidence), "failures": evidence_failures},
        )
    )

    closeout_path = _safe_evidence_path(evidence_root, "closeout.txt")
    try:
        closeout = _parse_closeout(closeout_path) if closeout_path and closeout_path.is_file() else {}
        closeout_read_error = None
    except (OSError, UnicodeError) as exc:
        closeout = {}
        closeout_read_error = type(exc).__name__
    required_closeout = (
        contract.get("required_closeout_values", {})
        if isinstance(contract.get("required_closeout_values"), dict)
        else {}
    )
    closeout_mismatches = {
        key: {"expected": str(expected), "actual": closeout.get(key)}
        for key, expected in required_closeout.items()
        if closeout.get(key) != str(expected)
    }
    identity_matches = (
        closeout.get("SAGE_CANONICAL_SOURCE_COMMIT", "").lower() == source_commit.lower()
        and closeout.get("SAGE_MIRROR_DELIVERY_COMMIT", "").lower() == delivery_commit.lower()
        and closeout.get("PACKAGE_SHA256", "").upper() == package_sha256
    )
    checks.append(
        _check(
            "closeout_terminal_verdict_and_identity_match",
            not closeout_mismatches and identity_matches,
            {
                "mismatches": closeout_mismatches,
                "identity_matches": identity_matches,
                "read_error": closeout_read_error,
            },
        )
    )

    checksum_path = _safe_evidence_path(evidence_root, "package.sha256.txt")
    try:
        checksum_text = (
            checksum_path.read_text(encoding="ascii", errors="strict").strip()
            if checksum_path and checksum_path.is_file()
            else ""
        )
        checksum_read_error = None
    except (OSError, UnicodeError) as exc:
        checksum_text = ""
        checksum_read_error = type(exc).__name__
    checks.append(
        _check(
            "package_checksum_receipt_matches_manifest",
            checksum_text == f"{package_sha256}  {package_name}",
            {
                "checksum_receipt": checksum_text,
                "expected_package_name": package_name,
                "read_error": checksum_read_error,
            },
        )
    )

    proof_path = _safe_evidence_path(evidence_root, "installation_proof.json")
    proof = load_json_file(proof_path, {}) if proof_path and proof_path.is_file() else {}
    proof_summary = proof.get("summary", {}) if isinstance(proof, dict) and isinstance(proof.get("summary"), dict) else {}
    proof_target = proof.get("target_scope", {}) if isinstance(proof, dict) and isinstance(proof.get("target_scope"), dict) else {}
    manifest_target = manifest.get("target", {}) if isinstance(manifest.get("target"), dict) else {}
    expected_target = _normalize_target_path(manifest_target.get("path"))
    actual_target = _normalize_target_path(proof_target.get("target_root"))
    proof_steps = proof.get("steps", []) if isinstance(proof, dict) and isinstance(proof.get("steps"), list) else []
    proof_passed = (
        proof_summary.get("status") == "PASS"
        and _as_int(proof_summary.get("passed"), 0) == _as_int(proof_summary.get("steps"), -1)
        and _as_int(proof_summary.get("steps"), 0) > 0
        and bool(proof_steps)
        and all(isinstance(row, dict) and row.get("passed") is True for row in proof_steps)
        and actual_target == expected_target
        and str(proof_target.get("projects") or "") == str(manifest_target.get("requested_projects") or "")
    )
    checks.append(
        _check(
            "installation_proof_is_structured_pass_for_manifest_target",
            proof_passed,
            {
                "summary": proof_summary,
                "actual_target": actual_target,
                "expected_target": expected_target,
                "actual_projects": proof_target.get("projects"),
                "expected_projects": manifest_target.get("requested_projects"),
            },
        )
    )
    return _result(contract, env_name, path, checks, manifest)


def _result(
    contract: dict[str, Any],
    env_name: str,
    path: Path,
    checks: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    failed = [row for row in checks if not row.get("passed")]
    accepted = bool(checks) and not failed
    return {
        "meta": {"kind": "external_clean_machine_evidence_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if accepted else "FAIL",
            "accepted": accepted,
            "blocking_for_public_release": not accepted,
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
        },
        "evidence": {
            "environment_variable": env_name,
            "manifest_path": str(path),
            "manifest_sha256": _sha256(path) if path.is_file() else None,
            "canonical_source_commit": (
                manifest.get("source_identity", {}).get("canonical_source_commit")
                if isinstance(manifest.get("source_identity"), dict)
                else None
            ),
            "clean_mirror_delivery_commit": (
                manifest.get("source_identity", {}).get("clean_mirror_delivery_commit")
                if isinstance(manifest.get("source_identity"), dict)
                else None
            ),
            "package_sha256": (
                manifest.get("source_identity", {}).get("package_sha256")
                if isinstance(manifest.get("source_identity"), dict)
                else None
            ),
        },
        "checks": checks,
        "claim_boundary": contract.get("claim_boundary", {}),
    }

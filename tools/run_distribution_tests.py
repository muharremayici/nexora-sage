from __future__ import annotations

import argparse
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
    return classified, test_glob


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
    return normalized, target_fixture


def resolve_tests(profile_name: str) -> tuple[list[str], str | None]:
    payload = _load_profile()
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the authority-scoped Nexora SAGE test profile.")
    parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "canonical_all", "public_target_repository"],
    )
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    try:
        tests, target_fixture = resolve_tests(args.profile)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[distribution-tests] FAIL: {exc}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    temporary_target: tempfile.TemporaryDirectory[str] | None = None
    temporary_pytest: tempfile.TemporaryDirectory[str] | None = None
    try:
        if target_fixture:
            resolved_fixture = (ROOT / target_fixture).resolve()
            if not resolved_fixture.is_dir():
                print(f"[distribution-tests] FAIL: target fixture is missing: {target_fixture}", file=sys.stderr)
                return 2
            try:
                resolved_fixture.relative_to(ROOT)
            except ValueError:
                print("[distribution-tests] FAIL: target fixture escapes the distribution root", file=sys.stderr)
                return 2
            temporary_target = tempfile.TemporaryDirectory(prefix="nexora-sage-test-target-")
            external_target = Path(temporary_target.name) / "repository"
            shutil.copytree(resolved_fixture, external_target)
            env["CODEMAPS_TARGET_ROOT"] = str(external_target)

        temporary_pytest = tempfile.TemporaryDirectory(prefix="nexora-sage-pytest-")
        command = [sys.executable, "-B", "-m", "pytest", *tests]
        command.extend(
            [
                "-vv" if args.verbose else "-q",
                "--basetemp",
                temporary_pytest.name,
                "-p",
                "no:cacheprovider",
            ]
        )
        if args.collect_only:
            command.append("--collect-only")
        print(
            f"[distribution-tests] profile={args.profile} tests={len(tests)} "
            f"public_distribution={PUBLIC_MANIFEST.is_file()} collect_only={args.collect_only}",
            flush=True,
        )
        return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    finally:
        if temporary_pytest is not None:
            temporary_pytest.cleanup()
        if temporary_target is not None:
            temporary_target.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

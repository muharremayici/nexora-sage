from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.validate_clean_distribution import (
    FORBIDDEN_GLOBS,
    FORBIDDEN_RECURSIVE_NAMES,
    FORBIDDEN_RECURSIVE_SUFFIXES,
    FORBIDDEN_ROOT_NAMES,
    DEFAULT_ROOT,
)
from tools.core.distribution_policy import clean_mirror_forbidden_relative_paths, is_clean_install_root

ROOT = DEFAULT_ROOT


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _inside_root(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def assert_clean_install_root(root: Path) -> Path:
    resolved = root.resolve()
    if not is_clean_install_root(resolved):
        raise RuntimeError(
            "Refusing to clean runtime artifacts outside an existing clean-install mirror. "
            "Use tools/sync_clean_distribution.py --destination <clean-install-dir> first, "
            "then run this cleaner from that clean-install directory."
        )
    return resolved


def _remove(root: Path, path: Path, *, dry_run: bool) -> str:
    if not _inside_root(root, path):
        raise RuntimeError(f"Refusing to clean outside distribution root: {path}")
    rel = _rel(root, path)
    if dry_run:
        return rel
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return rel


def _record_removal(
    root: Path,
    path: Path,
    *,
    dry_run: bool,
    removed: list[str],
    failed: list[dict[str, str]],
) -> None:
    try:
        removed.append(_remove(root, path, dry_run=dry_run))
    except OSError as exc:
        failed.append(
            {
                "path": _rel(root, path),
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )


def clean_distribution_artifacts(*, root: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    target_root = assert_clean_install_root(root or ROOT)
    removed: list[str] = []
    failed: list[dict[str, str]] = []

    for name in sorted(FORBIDDEN_ROOT_NAMES):
        path = target_root / name
        if path.exists():
            _record_removal(target_root, path, dry_run=dry_run, removed=removed, failed=failed)

    for relative in sorted(clean_mirror_forbidden_relative_paths()):
        path = target_root / relative
        if path.exists():
            _record_removal(target_root, path, dry_run=dry_run, removed=removed, failed=failed)

    for name in sorted(FORBIDDEN_RECURSIVE_NAMES):
        for path in sorted(target_root.rglob(name), key=lambda item: len(item.parts), reverse=True):
            if path.exists():
                _record_removal(target_root, path, dry_run=dry_run, removed=removed, failed=failed)

    for suffix in sorted(FORBIDDEN_RECURSIVE_SUFFIXES):
        for path in sorted(
            (item for item in target_root.rglob("*") if item.is_dir() and item.name.endswith(suffix)),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            if path.exists():
                _record_removal(target_root, path, dry_run=dry_run, removed=removed, failed=failed)

    for pattern in FORBIDDEN_GLOBS:
        for path in sorted(target_root.glob(pattern)):
            if path.exists():
                _record_removal(target_root, path, dry_run=dry_run, removed=removed, failed=failed)

    return {
        "meta": {"kind": "clean_distribution_artifacts", "version": "v1", "root": str(target_root)},
        "summary": {
            "status": "FAIL" if failed else "PASS",
            "dry_run": bool(dry_run),
            "removed_count": len(removed),
            "failed_count": len(failed),
        },
        "removed": removed,
        "failed_removals": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove generated/runtime artifacts from a clean Nexora SAGE distribution copy.")
    parser.add_argument("--dry-run", action="store_true", help="List artifacts that would be removed without deleting them.")
    args = parser.parse_args()
    payload = clean_distribution_artifacts(dry_run=args.dry_run)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

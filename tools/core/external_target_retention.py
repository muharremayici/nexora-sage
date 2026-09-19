from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from tools.core.advisory_file_lock import AdvisoryFileLock
from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR
from tools.core.external_target_generation import external_target_output_slug
from tools.core.unmanaged_atomic_io import native_filesystem_path


EXTERNAL_TARGETS_DIR = CODE_MAPS_DIR / "output" / "external_targets"
RETENTION_POLICY_PATH = CONFIG_DIR / "runtime_output_retention_policy.json"
RETENTION_TARGET_ID = "external_target_generated_fixtures"
GENERATION_INDEX_RETENTION_TARGET_ID = "external_target_generation_history_index"
FIXTURE_LEASE_DIR_NAME = ".generated_fixture_leases"


def _load_retention_target(target_id: str = RETENTION_TARGET_ID) -> dict[str, Any]:
    try:
        payload = json.loads(RETENTION_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    targets = payload.get("retention_targets") if isinstance(payload, dict) else []
    for target in targets if isinstance(targets, list) else []:
        if isinstance(target, dict) and target.get("id") == target_id:
            return target
    return {}


def generated_fixture_prefixes() -> tuple[str, ...]:
    target = _load_retention_target()
    prefixes = target.get("generated_fixture_prefixes") if isinstance(target, dict) else []
    return tuple(str(prefix) for prefix in prefixes if isinstance(prefix, str) and prefix)


def default_keep_per_prefix() -> int:
    target = _load_retention_target()
    try:
        return max(0, int(target.get("default_keep_per_prefix")))
    except (TypeError, ValueError, AttributeError):
        return 0


def generation_history_limits() -> tuple[int, int]:
    target = _load_retention_target(GENERATION_INDEX_RETENTION_TARGET_ID)
    try:
        max_scan = int(target.get("max_manifest_scan_per_target"))
        max_history = int(target.get("max_history_entries_per_target"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("External target generation history policy is incomplete") from exc
    if max_history < 4 or max_scan < max_history:
        raise ValueError("External target generation history bounds are invalid")
    return max_scan, max_history


GENERATED_FIXTURE_PREFIXES = generated_fixture_prefixes()
DEFAULT_KEEP_PER_PREFIX = default_keep_per_prefix()


def _fixture_prefix(name: str) -> str:
    for prefix in GENERATED_FIXTURE_PREFIXES:
        if name.startswith(prefix):
            return prefix
    return ""


def _fixture_lease_path(fixture_name: str, base_dir: Path = EXTERNAL_TARGETS_DIR) -> Path:
    return Path(base_dir) / FIXTURE_LEASE_DIR_NAME / f"{fixture_name}.lock"


def acquire_generated_fixture_lease(
    target_root: str | Path,
    *,
    base_dir: Path = EXTERNAL_TARGETS_DIR,
) -> AdvisoryFileLock:
    """Protect one generated fixture output for its complete producer/consumer lifetime."""

    fixture_name = external_target_output_slug(str(Path(target_root).resolve()))
    if not _fixture_prefix(fixture_name):
        raise ValueError("Generated fixture lease requires a governed fixture prefix")
    lease = AdvisoryFileLock(_fixture_lease_path(fixture_name, base_dir))
    if not lease.acquire():
        raise RuntimeError(f"Generated fixture lease is already held: {fixture_name}")
    return lease


@contextmanager
def generated_fixture_lease(
    target_root: str | Path,
    *,
    base_dir: Path = EXTERNAL_TARGETS_DIR,
) -> Iterator[AdvisoryFileLock]:
    """Hold a generated-fixture lease and release it on every exit path."""

    lease = acquire_generated_fixture_lease(target_root, base_dir=base_dir)
    try:
        yield lease
    finally:
        lease.release()


def external_target_retention_inventory(base_dir: Path = EXTERNAL_TARGETS_DIR) -> dict[str, Any]:
    generated_by_prefix = {prefix: 0 for prefix in GENERATED_FIXTURE_PREFIXES}
    user_scoped = 0
    total = 0
    if base_dir.exists():
        for item in base_dir.iterdir():
            if not item.is_dir():
                continue
            if item.name == FIXTURE_LEASE_DIR_NAME:
                continue
            total += 1
            prefix = _fixture_prefix(item.name)
            if prefix:
                generated_by_prefix[prefix] += 1
            else:
                user_scoped += 1
    return {
        "base_dir": str(base_dir),
        "total_dirs": total,
        "generated_fixture_dirs": sum(generated_by_prefix.values()),
        "user_scoped_dirs": user_scoped,
        "generated_by_prefix": generated_by_prefix,
        "policy": {
            "generated_fixture_prefixes": list(GENERATED_FIXTURE_PREFIXES),
            "default_keep_per_prefix": DEFAULT_KEEP_PER_PREFIX,
            "user_scoped_outputs_pruned": False,
        },
    }


def prune_generated_external_target_fixtures(
    *,
    base_dir: Path = EXTERNAL_TARGETS_DIR,
    keep_per_prefix: int = DEFAULT_KEEP_PER_PREFIX,
    dry_run: bool = False,
) -> dict[str, Any]:
    keep_per_prefix = max(0, int(keep_per_prefix))
    before = external_target_retention_inventory(base_dir)
    candidates_by_prefix: dict[str, list[Path]] = {prefix: [] for prefix in GENERATED_FIXTURE_PREFIXES}
    if base_dir.exists():
        for item in base_dir.iterdir():
            if not item.is_dir():
                continue
            prefix = _fixture_prefix(item.name)
            if prefix:
                candidates_by_prefix[prefix].append(item)

    selected: list[Path] = []
    for prefix, candidates in candidates_by_prefix.items():
        ordered = sorted(
            candidates,
            key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
            reverse=True,
        )
        selected.extend(ordered[keep_per_prefix:])

    removed: list[str] = []
    protected_by_active_lease: list[str] = []
    failed: list[dict[str, str]] = []
    for path in selected:
        lease = AdvisoryFileLock(_fixture_lease_path(path.name, base_dir))
        if not lease.acquire():
            protected_by_active_lease.append(str(path))
            continue
        try:
            removed.append(str(path))
            if not dry_run:
                try:
                    shutil.rmtree(native_filesystem_path(path))
                except OSError as exc:
                    failed.append({"path": str(path), "error": str(exc)})
        finally:
            lease.release()

    after = external_target_retention_inventory(base_dir)
    return {
        "before": before,
        "after": after,
        "keep_per_prefix": keep_per_prefix,
        "dry_run": dry_run,
        "retention_candidates": len(selected),
        "selected_for_removal": len(selected) - len(protected_by_active_lease),
        "protected_by_active_lease": protected_by_active_lease,
        "removed": removed,
        "failed": failed,
        "status": "PASS" if not failed else "ATTENTION",
    }

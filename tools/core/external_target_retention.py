from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR


EXTERNAL_TARGETS_DIR = CODE_MAPS_DIR / "output" / "external_targets"
RETENTION_POLICY_PATH = CONFIG_DIR / "runtime_output_retention_policy.json"
RETENTION_TARGET_ID = "external_target_generated_fixtures"


def _load_retention_target() -> dict[str, Any]:
    try:
        payload = json.loads(RETENTION_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    targets = payload.get("retention_targets") if isinstance(payload, dict) else []
    for target in targets if isinstance(targets, list) else []:
        if isinstance(target, dict) and target.get("id") == RETENTION_TARGET_ID:
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


GENERATED_FIXTURE_PREFIXES = generated_fixture_prefixes()
DEFAULT_KEEP_PER_PREFIX = default_keep_per_prefix()


def _fixture_prefix(name: str) -> str:
    for prefix in GENERATED_FIXTURE_PREFIXES:
        if name.startswith(prefix):
            return prefix
    return ""


def external_target_retention_inventory(base_dir: Path = EXTERNAL_TARGETS_DIR) -> dict[str, Any]:
    generated_by_prefix = {prefix: 0 for prefix in GENERATED_FIXTURE_PREFIXES}
    user_scoped = 0
    total = 0
    if base_dir.exists():
        for item in base_dir.iterdir():
            if not item.is_dir():
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
    failed: list[dict[str, str]] = []
    for path in selected:
        removed.append(str(path))
        if dry_run:
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            failed.append({"path": str(path), "error": str(exc)})

    after = external_target_retention_inventory(base_dir)
    return {
        "before": before,
        "after": after,
        "keep_per_prefix": keep_per_prefix,
        "dry_run": dry_run,
        "selected_for_removal": len(selected),
        "removed": removed,
        "failed": failed,
        "status": "PASS" if not failed else "ATTENTION",
    }

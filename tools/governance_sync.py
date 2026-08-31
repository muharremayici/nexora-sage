import argparse
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Ensure the Nexora SAGE root is in sys.path
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import (
    CODE_MAPS_DIR,
    CONFIG_DIR,
    DISCOVERY_FILE as DISCOVERY_PATH,
    OVERRIDES_FILE as OVERRIDES_PATH,
    DOCTRINE_FILE as DOCTRINE_PATH,
    save_json_atomic,
)
from tools.core.overrides_validator import overrides_match_discovery


def load_json(path: Path):
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def save_json(path: Path, data):
    save_json_atomic(path, data)


def _canonical_index(overrides: dict):
    index = {}
    for alias, payload in (overrides.get("variation_aliases") or {}).items():
        if not isinstance(payload, dict):
            continue
        discovery_key = payload.get("discovery_key")
        if discovery_key:
            index[discovery_key] = alias
    return index


def _has_doctrine_hint(discovery_key: str, doctrine_hints: dict, canonical_by_discovery: dict) -> bool:
    if discovery_key in doctrine_hints:
        return True
    canonical_name = canonical_by_discovery.get(discovery_key)
    return bool(canonical_name and canonical_name in doctrine_hints)


def _has_generic_doctrine_support(doctrine: dict) -> bool:
    return bool(doctrine.get("generic_path_hints"))


def _workspace_scoped_override_seed(discovery: dict, overrides: dict) -> dict:
    seeded = dict(overrides or {})
    meta = seeded.setdefault("_meta", {})
    meta.setdefault("kind", "codemaps.overrides")
    meta["owner"] = "Nexora SAGE governance"
    meta["source"] = "codemaps.discovery.json"
    meta["generated_by"] = "tools/governance_sync.py"
    meta["contract_version"] = "config-provenance-v1"
    meta["last_validated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seeded["workspace_root"] = discovery.get("workspace_root", seeded.get("workspace_root", ".."))
    seeded.setdefault("use_sqlite", bool(discovery.get("use_sqlite", True)))
    seeded["source_extensions"] = discovery.get("source_extensions", seeded.get("source_extensions", [".ts", ".tsx", ".js", ".jsx"]))
    seeded["skip_dirs"] = discovery.get("skip_dirs", seeded.get("skip_dirs", []))
    seeded["project_roles"] = {
        key: value
        for key, value in (discovery.get("project_roles", {}) or {}).items()
        if isinstance(value, str)
    }
    seeded["variations"] = {
        key: value
        for key, value in (discovery.get("variations", {}) or {}).items()
        if isinstance(value, str)
    }
    seeded["variation_aliases"] = {}
    seeded["architecture"] = dict(discovery.get("architecture", {}) or {})
    seeded["environment"] = dict(discovery.get("environment", {}) or {})
    seeded["plugins"] = sorted(set(discovery.get("plugins", []) or []))
    return seeded


def _overrides_match_workspace(discovery: dict, overrides: dict) -> bool:
    return overrides_match_discovery(discovery, overrides)


def sync_governance(dry_run=True):
    print(f"[SYNC] Governance alignment {'(dry run)' if dry_run else '(apply)'}")

    discovery = load_json(DISCOVERY_PATH)
    overrides = load_json(OVERRIDES_PATH)
    doctrine = load_json(DOCTRINE_PATH)

    if not discovery:
        print("[FAIL] Discovery data not found. Run `python sage.py refresh` or `python sage.py init` first.")
        return

    updates = []
    warnings = []

    discovery_variations = discovery.get("variations", {}) or {}
    if not _overrides_match_workspace(discovery, overrides):
        updates.append("Overrides do not match this workspace; reseeding workspace-scoped sections from discovery.")
        if not dry_run:
            overrides = _workspace_scoped_override_seed(discovery, overrides)

    override_variations = overrides.setdefault("variations", {})
    alias_map = overrides.setdefault("variation_aliases", {})
    canonical_by_discovery = _canonical_index(overrides)
    doctrine_hints = doctrine.get("project_path_hints", {}) or {}

    for discovery_key, rel_path in discovery_variations.items():
        if discovery_key == "MAIN":
            if override_variations.get("MAIN") != rel_path:
                updates.append(f"Override variation MAIN -> {rel_path}")
                if not dry_run:
                    override_variations["MAIN"] = rel_path
            continue

        if discovery_key not in canonical_by_discovery:
            updates.append(f"Seed variation alias proposal for {discovery_key}")
            if not dry_run:
                alias_map[discovery_key] = {
                    "discovery_key": discovery_key,
                    "path": rel_path,
                    "enabled": True,
                }

        if not _has_doctrine_hint(discovery_key, doctrine_hints, canonical_by_discovery) and not _has_generic_doctrine_support(doctrine):
            warnings.append(f"Missing doctrine project_path_hints entry for {discovery_key}")

    discovered_plugins = set(discovery.get("plugins", []) or [])
    override_plugins = set(overrides.get("plugins", []) or [])
    new_plugins = sorted(discovered_plugins - override_plugins)
    if new_plugins:
        updates.append(f"Discovery sees plugins absent from overrides: {new_plugins}")
        if not dry_run:
            overrides["plugins"] = sorted(override_plugins | set(new_plugins))

    if not dry_run:
        meta = overrides.setdefault("_meta", {})
        meta.setdefault("kind", "codemaps.overrides")
        meta["owner"] = "Nexora SAGE governance"
        meta["source"] = "codemaps.discovery.json"
        meta["generated_by"] = "tools/governance_sync.py"
        meta["contract_version"] = "config-provenance-v1"
        meta["last_validated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if not updates and not warnings:
        print("[OK] Discovery, overrides, and doctrine are aligned.")
        return

    print("\n--- GOVERNANCE ALIGNMENT REPORT ---")
    for update in updates:
        print(f"[PATCH] {update}")
    for warning in warnings:
        print(f"[WARN] {warning}")

    if dry_run:
        print("\n[INFO] Dry run only. No files modified.")
        return

    save_json(OVERRIDES_PATH, overrides)
    print(f"\n[OK] Overrides updated: {OVERRIDES_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync discovery proposals into human-governed overrides.")
    parser.add_argument("--apply", action="store_true", help="Write suggested seeds into codemaps.overrides.json")
    args = parser.parse_args()
    sync_governance(dry_run=not args.apply)

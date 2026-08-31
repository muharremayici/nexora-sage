import argparse
import json
import os
import stat
import shutil
import sys
import time
from pathlib import Path

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CODE_MAPS_DIR = Path(_ROOT)

from tools.core.config import (
    CONFIG_DIR,
    OVERRIDES_FILE,
    DYNAMIC_CONFIG,
)
from tools.core.external_target_retention import prune_generated_external_target_fixtures


def _sample_remaining(path: Path, limit: int = 8) -> list[str]:
    if not path.exists():
        return []
    remaining: list[str] = []
    try:
        for item in path.rglob("*"):
            remaining.append(str(item.relative_to(path)))
            if len(remaining) >= limit:
                break
    except Exception:
        remaining.append(str(path))
    return remaining


def _manual_prune(path: Path) -> None:
    if not path.exists():
        return
    for root, dirs, files in os.walk(path, topdown=False):
        root_path = Path(root)
        for file_name in files:
            target = root_path / file_name
            try:
                os.chmod(target, stat.S_IWRITE)
                target.unlink()
            except Exception:
                pass
        for dir_name in dirs:
            target = root_path / dir_name
            try:
                os.chmod(target, stat.S_IWRITE)
                target.rmdir()
            except Exception:
                pass
    try:
        os.chmod(path, stat.S_IWRITE)
        path.rmdir()
    except Exception:
        pass


def _is_nonblocking_test_tmp_residue(path: Path, remaining: list[str]) -> bool:
    if path.name != "output" or not remaining:
        return False
    return all(item == ".test_tmp" or item.startswith(".test_tmp\\") or item.startswith(".test_tmp/") for item in remaining)


def _rmtree_force(path: Path, retries: int = 3) -> tuple[bool, list[str]]:
    def _on_rm_error(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    for attempt in range(retries):
        try:
            shutil.rmtree(path, onerror=_on_rm_error, ignore_errors=False)
        except Exception:
            pass
        _manual_prune(path)
        if not path.exists():
            return True, []
        time.sleep(0.2 * (attempt + 1))

    return False, _sample_remaining(path)


def _required_fixture_ids() -> set[str]:
    matrix_path = CONFIG_DIR / "react_fixture_matrix.json"
    if not matrix_path.exists():
        return set()
    try:
        payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    except Exception:
        return set()

    fixtures = payload.get("fixtures", []) if isinstance(payload, dict) else []
    required: set[str] = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        if not bool(fixture.get("enabled", True)):
            continue
        if not bool(fixture.get("required", False)):
            continue
        fixture_id = str(fixture.get("id") or "").strip()
        if fixture_id:
            required.add(fixture_id)
    return required


def _preserve_fixture_dir(output_dir: Path, keep_ids: set[str] | None = None) -> Path | None:
    fixtures_dir = output_dir / ".fixtures"
    if not fixtures_dir.exists() or not fixtures_dir.is_dir():
        return None

    staging_dir = CODE_MAPS_DIR / ".purge_preserve_fixtures"
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)

    if keep_ids is None:
        shutil.copytree(fixtures_dir, staging_dir)
        return staging_dir

    staging_dir.mkdir(parents=True, exist_ok=True)
    for fixture_id in keep_ids:
        src = fixtures_dir / fixture_id
        if src.exists() and src.is_dir():
            shutil.copytree(src, staging_dir / fixture_id)
    return staging_dir


def _restore_fixture_dir(output_dir: Path, staging_dir: Path | None) -> None:
    if not staging_dir or not staging_dir.exists():
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    target_dir = output_dir / ".fixtures"
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    shutil.copytree(staging_dir, target_dir)
    shutil.rmtree(staging_dir, ignore_errors=True)


def _prune_stale_project_truth_dirs() -> list[str]:
    projects_dir = CONFIG_DIR / "golden" / "projects"
    if not projects_dir.exists() or not projects_dir.is_dir():
        return []

    active_projects = {
        str(key).strip()
        for key in ((DYNAMIC_CONFIG.get("variations", {}) or {}).keys())
        if str(key).strip()
    }
    removed: list[str] = []
    for project_dir in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
        if not project_dir.is_dir():
            continue
        if project_dir.name in active_projects:
            continue
        try:
            _rmtree_force(project_dir)
            if not project_dir.exists():
                removed.append(str(project_dir))
        except Exception:
            continue
    return removed


def purge_system(mode="standard"):
    print(f"=== SOVEREIGN SYSTEM PURGE | Mode: {mode.upper()} ===")
    if mode == "external-targets":
        result = prune_generated_external_target_fixtures(keep_per_prefix=0)
        print(
            "\n[OK] Generated external target fixture outputs pruned. "
            f"removed={result.get('selected_for_removal')} user_scoped_preserved={result.get('after', {}).get('user_scoped_dirs')}"
        )
        if result.get("failed"):
            print("[WARN] Some generated external target fixture outputs could not be removed:")
            for item in result["failed"][:20]:
                print(f"  - {item.get('path')}: {item.get('error')}")
        return
    
    # 1. Standard Targets (Transient Data)
    transient_targets = [
        CODE_MAPS_DIR / "output",
        CODE_MAPS_DIR / "__pycache__",
        CODE_MAPS_DIR / ".pytest_cache",
        CODE_MAPS_DIR / ".pipeline_run.lock",
    ]
    
    # 2. Deep Targets (Re-discovery required)
    discovery_targets = [
        CONFIG_DIR / "codemaps.discovery.json",
        CONFIG_DIR / "codemaps.config.json",
    ]
    
    # 3. Identity Targets (User governance - EXTREME)
    identity_targets = [
        OVERRIDES_FILE,
    ]

    # Execute Purge
    to_delete = transient_targets
    if mode in ["deep", "total"]:
        to_delete.extend(discovery_targets)
    if mode == "total":
        to_delete.extend(identity_targets)

    preserve_fixture_ids: set[str] | None = None
    if mode == "standard":
        preserve_fixture_ids = None
    elif mode in {"deep", "total"}:
        preserve_fixture_ids = _required_fixture_ids()

    output_dir = CODE_MAPS_DIR / "output"
    preserved_fixtures: Path | None = None

    residual_errors = []
    for target in to_delete:
        if not target.exists():
            continue
            
        try:
            if target.is_dir():
                print(f"  [RMDIR] {target.name}")
                if target == output_dir:
                    preserved_fixtures = _preserve_fixture_dir(output_dir, preserve_fixture_ids)
                removed, remaining = _rmtree_force(target)
                if not removed:
                    detail = f"; remaining: {', '.join(remaining)}" if remaining else ""
                    if _is_nonblocking_test_tmp_residue(target, remaining):
                        print(f"  [WARN] Non-blocking inaccessible test temp residue retained: {target}{detail}")
                        print("  [HINT] This is usually a Windows ACL/sandbox-owner residue; it is ignored by release gates.")
                    else:
                        residual_errors.append(f"{target} (directory not fully removed{detail})")
            else:
                print(f"  [UNLINK] {target.name}")
                target.unlink(missing_ok=True)
                if target.exists():
                    residual_errors.append(f"{target} (file still exists)")
        except Exception as e:
            print(f"  [ERROR] Failed to delete {target.name}: {e}")
            residual_errors.append(f"{target} ({e})")

    _restore_fixture_dir(output_dir, preserved_fixtures)

    # Doc maintenance
    docs_dir = CODE_MAPS_DIR / "docs"
    if docs_dir.exists():
        for f in docs_dir.glob("MANUAL_TRUTH_SAMPLING_*.md"):
            try: f.unlink()
            except (PermissionError, OSError): pass

    if residual_errors:
        print("\n[WARN] Purge completed with residual artifacts:")
        for err in residual_errors[:20]:
            print(f"  - {err}")
        if len(residual_errors) > 20:
            print(f"  - ... and {len(residual_errors) - 20} more")
    else:
        print("\n[OK] Purge sequence completed.")

    if mode in {"deep", "total"}:
        removed_truth_dirs = _prune_stale_project_truth_dirs()
        if removed_truth_dirs:
            print(f"[TRUTH] Pruned stale project truth dirs: {len(removed_truth_dirs)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nexora SAGE Sovereign System Purge Tool")
    parser.add_argument("--mode", choices=["standard", "deep", "total", "external-targets"], default="standard", 
                        help="standard: clean outputs/cache. deep: +discovery/config. total: +overrides. external-targets: clean only external target run outputs.")
    parser.add_argument("--confirm", action="store_true", help="Confirm total purge without prompt.")
    args = parser.parse_args()

    if args.mode == "total" and not args.confirm:
        resp = input("WARNING: 'total' mode will delete your manual overrides and policy files. Continue? (y/N): ")
        if resp.lower() != 'y':
            print("Abort.")
            sys.exit(0)

    purge_system(mode=args.mode)

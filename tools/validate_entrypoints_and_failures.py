from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.json_io import load_json_strict, load_text_file
from tools.core.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    RAW_DIR,
    REPORTS_DIR,
    _target_output_slug,
    save_json_atomic,
    save_text_atomic,
)
from tools.core.operational_limits import (
    entrypoint_failure_dead_code_drill_timeout_seconds,
    entrypoint_failure_default_timeout_seconds,
    pipeline_step_heartbeat_seconds,
)
from tools.core.subprocess_telemetry import run_observed_subprocess


ENTRYPOINT_HEARTBEAT_SECONDS = pipeline_step_heartbeat_seconds()
STANDALONE_INVOCATION_CONTRACT = CONFIG_DIR / "standalone_tool_invocation_contract.json"
PUBLIC_DISTRIBUTION_MANIFEST = CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json"


def _log(message: str) -> None:
    print(f"[entrypoint-failure-validation] {message}", flush=True)


def _run(
    command: list[str],
    cwd: Path,
    env_extra: dict[str, str] | None = None,
    *,
    timeout: int | None = None,
    label: str = "subprocess",
) -> tuple[Any, float]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc, duration = run_observed_subprocess(
        command,
        cwd=cwd,
        env=env,
        label=label,
        timeout=timeout or entrypoint_failure_default_timeout_seconds(),
        heartbeat_seconds=ENTRYPOINT_HEARTBEAT_SECONDS,
        log=_log,
    )
    return proc, duration


def discovery_entrypoint_environment(
    *,
    code_maps_dir: Path = CODE_MAPS_DIR,
    config_file: Path = CONFIG_FILE,
    public_manifest: Path = PUBLIC_DISTRIBUTION_MANIFEST,
) -> dict[str, str]:
    """Recover explicit target authority for public entrypoint validation."""

    if not public_manifest.is_file():
        return {}
    try:
        config = load_json_strict(config_file)
    except Exception:
        return {}
    if not isinstance(config, dict):
        return {}
    workspace_root = str(config.get("workspace_root") or "").strip()
    if not workspace_root:
        return {}
    target_root = (code_maps_dir / workspace_root).resolve()
    if not target_root.is_dir():
        return {}
    return {"CODEMAPS_TARGET_ROOT": str(target_root)}


def _load_subprocess_json_output(
    path: Path,
    proc: Any,
    *,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    diagnostic = {
        "label": label,
        "path": str(path),
        "output_exists": path.exists(),
        "returncode": int(getattr(proc, "returncode", -1)),
        "stdout": str(getattr(proc, "stdout", "") or "")[-4000:],
        "stderr": str(getattr(proc, "stderr", "") or "")[-4000:],
        "read_error": "",
    }
    if not diagnostic["output_exists"]:
        diagnostic["read_error"] = "expected_output_missing"
        return {}, diagnostic
    try:
        payload = load_json_strict(path)
    except Exception as exc:
        diagnostic["read_error"] = f"{type(exc).__name__}: {exc}"
        return {}, diagnostic
    if not isinstance(payload, dict):
        diagnostic["read_error"] = "expected_object_payload"
        return {}, diagnostic
    return payload, diagnostic


def _content_hashes(paths: dict[str, Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, path in paths.items():
        if not path.exists() or not path.is_file():
            hashes[name] = "missing"
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[name] = digest.hexdigest()
    return hashes


def _canonical_state_fingerprints() -> dict[str, dict[str, Any] | str]:
    database_path = RAW_DIR / "codemaps.db"
    names = ("atlas", "health_score", "quality_gate")
    if not database_path.exists():
        return {name: "missing_database" for name in names}
    fingerprints: dict[str, dict[str, Any] | str] = {}
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for name in names:
            row = connection.execute(
                "SELECT payload_sha, source_mtime, updated_at FROM state_payloads WHERE name = ?;",
                (name,),
            ).fetchone()
            fingerprints[name] = dict(row) if row is not None else "missing_payload"
    return fingerprints


def _isolated_output_dir(target_root: Path) -> Path:
    return CODE_MAPS_DIR / "output" / "external_targets" / _target_output_slug(str(target_root))


def _cleanup_isolated_output(path: Path) -> None:
    expected_parent = (CODE_MAPS_DIR / "output" / "external_targets").resolve()
    resolved = path.resolve()
    if resolved.parent != expected_parent or not resolved.name.startswith("sage_entrypoint_failure_drill_"):
        raise ValueError(f"Refusing to clean unexpected failure-drill output path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _cleanup_stale_isolated_outputs() -> list[str]:
    parent = CODE_MAPS_DIR / "output" / "external_targets"
    if not parent.exists():
        return []
    stale_after_seconds = (
        (2 * entrypoint_failure_dead_code_drill_timeout_seconds())
        + entrypoint_failure_default_timeout_seconds()
        + ENTRYPOINT_HEARTBEAT_SECONDS
    )
    now = time.time()
    cleaned: list[str] = []
    for path in sorted(parent.glob("sage_entrypoint_failure_drill_*")):
        mtimes = [path.stat().st_mtime]
        mtimes.extend(child.stat().st_mtime for child in path.rglob("*"))
        if now - max(mtimes) <= stale_after_seconds:
            continue
        _cleanup_isolated_output(path)
        cleaned.append(path.name)
    return cleaned


def _engine_direct_entrypoint_inventory() -> dict[str, Any]:
    contract = load_json_strict(STANDALONE_INVOCATION_CONTRACT)
    scope = contract.get("scope") if isinstance(contract.get("scope"), dict) else {}
    modes = contract.get("invocation_modes") if isinstance(contract.get("invocation_modes"), dict) else {}
    module_only_mode = modes.get("module_only") if isinstance(modes.get("module_only"), dict) else {}
    engine_glob = str(scope.get("engine_glob") or "")
    entrypoint_marker = str(scope.get("entrypoint_marker") or "")
    package_markers = [str(value) for value in scope.get("package_import_markers", []) if str(value)]
    bootstrap_marker = str(scope.get("repo_root_bootstrap_marker") or "")
    canonical_template = str(module_only_mode.get("canonical_command_template") or "")
    if not engine_glob or not entrypoint_marker or not package_markers or not bootstrap_marker or "{module}" not in canonical_template:
        raise ValueError("standalone invocation contract is incomplete")
    module_only: list[dict[str, Any]] = []
    direct_file_supported: list[str] = []
    for path in sorted(CODE_MAPS_DIR.glob(engine_glob)):
        text = load_text_file(path)
        if entrypoint_marker not in text:
            continue
        imports_tools_package = any(marker in text for marker in package_markers)
        bootstraps_repo_root = bootstrap_marker in text
        if imports_tools_package and not bootstraps_repo_root:
            module_only.append(
                {
                    "file": path.relative_to(CODE_MAPS_DIR).as_posix(),
                    "canonical_invocation": canonical_template.format(module=path.stem),
                    "direct_file_invocation": module_only_mode.get("direct_file_invocation"),
                }
            )
        elif imports_tools_package:
            direct_file_supported.append(path.relative_to(CODE_MAPS_DIR).as_posix())
    return {
        "module_only_count": len(module_only),
        "module_only_entrypoints": module_only,
        "direct_file_supported_count": len(direct_file_supported),
        "direct_file_supported_entrypoints": direct_file_supported,
        "contract": str(STANDALONE_INVOCATION_CONTRACT.relative_to(CODE_MAPS_DIR)),
        "policy": contract.get("policy"),
    }


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    canonical_inputs = {
        "runtime_config": CONFIG_FILE,
        "atlas_shadow": RAW_DIR / "atlas.json",
        "health_score_shadow": RAW_DIR / "health_score.json",
        "quality_gate_shadow": RAW_DIR / "quality_gate.json",
    }
    canonical_hashes_before = _content_hashes(canonical_inputs)
    canonical_state_before = _canonical_state_fingerprints()

    _log("START discovery_entrypoint")
    discovery_preview_path = RAW_DIR / "discovery.entrypoint.validation.json"
    discovery_proc, discovery_seconds = _run(
        [
            sys.executable,
            str(CODE_MAPS_DIR / "tools" / "orchestrators" / "discovery.py"),
            "--profile",
            "entrypoint-smoke",
            "--output",
            str(discovery_preview_path),
        ],
        CODE_MAPS_DIR,
        env_extra=discovery_entrypoint_environment(),
        label="discovery_entrypoint",
    )

    discovery, discovery_diagnostic = _load_subprocess_json_output(
        discovery_preview_path,
        discovery_proc,
        label="discovery_entrypoint",
    )
    discovery_kind = discovery.get("_meta", {}).get("kind")
    discovery_passed = discovery_proc.returncode == 0 and discovery_kind == "codemaps.discovery"
    _log(f"{'PASS' if discovery_passed else 'FAIL'} discovery_entrypoint runtime_seconds={discovery_seconds}")
    checks.append(
        {
            "name": "discovery_entrypoint",
            "passed": discovery_passed,
            "details": {
                "returncode": discovery_proc.returncode,
                "kind": discovery_kind,
                "profile": discovery.get("_meta", {}).get("profile"),
                "runtime_seconds": discovery_seconds,
                "diagnostic": discovery_diagnostic,
            },
        }
    )

    _log("START config_compiler_entrypoint")
    preview_path = RAW_DIR / "codemaps.config.compiler.preview.validation.json"
    compile_proc, compile_seconds = _run(
        [
            sys.executable,
            str(CODE_MAPS_DIR / "tools" / "config_compiler.py"),
            "--output",
            str(preview_path),
        ],
        CODE_MAPS_DIR,
        label="config_compiler_entrypoint",
    )
    preview, preview_diagnostic = _load_subprocess_json_output(
        preview_path,
        compile_proc,
        label="config_compiler_entrypoint",
    )
    live_config_diagnostic = {"path": str(CONFIG_FILE), "read_error": ""}
    try:
        live_config_payload = load_json_strict(CONFIG_FILE)
    except Exception as exc:
        live_config_payload = {}
        live_config_diagnostic["read_error"] = f"{type(exc).__name__}: {exc}"
    live_config = live_config_payload if isinstance(live_config_payload, dict) else {}
    if not isinstance(live_config_payload, dict):
        live_config_diagnostic["read_error"] = "expected_object_payload"
    config_compiler_passed = (
        compile_proc.returncode == 0
        and preview.get("_meta", {}).get("kind") == "codemaps.config"
        and set((preview.get("variations") or {}).keys()) == set((live_config.get("variations") or {}).keys())
    )
    _log(f"{'PASS' if config_compiler_passed else 'FAIL'} config_compiler_entrypoint runtime_seconds={compile_seconds}")
    checks.append(
        {
            "name": "config_compiler_entrypoint",
            "passed": config_compiler_passed,
            "details": {
                "returncode": compile_proc.returncode,
                "preview_kind": preview.get("_meta", {}).get("kind"),
                "preview_projects": sorted((preview.get("variations") or {}).keys()),
                "runtime_seconds": compile_seconds,
                "preview_diagnostic": preview_diagnostic,
                "live_config_diagnostic": live_config_diagnostic,
            },
        }
    )

    _log("START pipeline_list_steps_entrypoint")
    list_steps_proc, list_steps_seconds = _run(
        [
            sys.executable,
            str(CODE_MAPS_DIR / "tools" / "orchestrators" / "orchestrator.py"),
            "--list-steps",
        ],
        CODE_MAPS_DIR,
        label="pipeline_list_steps_entrypoint",
    )
    list_steps_passed = list_steps_proc.returncode == 0 and "qualitygates" in (list_steps_proc.stdout or "").lower()
    _log(f"{'PASS' if list_steps_passed else 'FAIL'} pipeline_list_steps_entrypoint runtime_seconds={list_steps_seconds}")
    checks.append(
        {
            "name": "pipeline_list_steps_entrypoint",
            "passed": list_steps_passed,
            "details": {
                "returncode": list_steps_proc.returncode,
                "runtime_seconds": list_steps_seconds,
            },
        }
    )

    invocation_inventory = _engine_direct_entrypoint_inventory()
    checks.append(
        {
            "name": "engine_entrypoint_invocation_policy_declared",
            "passed": bool(invocation_inventory.get("contract")),
            "details": invocation_inventory,
        }
    )

    with tempfile.TemporaryDirectory(prefix="sage_entrypoint_config_drill_") as temp_config_dir:
        isolated_config = Path(temp_config_dir) / "codemaps.config.json"
        _log("START config_missing_autocompiles_gracefully")
        inline = (
            "from pathlib import Path; "
            "import tools.core.config as cfg; "
            f"cfg.CONFIG_FILE = Path({str(isolated_config)!r}); "
            "data = cfg.load_runtime_config(); "
            "print(data.get('_meta', {}).get('kind')); "
            "print(cfg.CONFIG_FILE.exists())"
        )
        autocompile_proc, autocompile_seconds = _run(
            [sys.executable, "-c", inline],
            CODE_MAPS_DIR,
            label="config_missing_autocompiles_gracefully",
        )
        config_recreated = isolated_config.exists()
        recreated_kind = None
        if config_recreated:
            recreated_kind = load_json_strict(isolated_config).get("_meta", {}).get("kind")
        autocompile_passed = autocompile_proc.returncode == 0 and config_recreated and recreated_kind == "codemaps.config"
        _log(f"{'PASS' if autocompile_passed else 'FAIL'} config_missing_autocompiles_gracefully runtime_seconds={autocompile_seconds}")
        checks.append(
            {
                "name": "config_missing_autocompiles_gracefully",
                "passed": autocompile_passed,
                "details": {
                    "returncode": autocompile_proc.returncode,
                    "config_recreated": config_recreated,
                    "recreated_kind": recreated_kind,
                    "runtime_seconds": autocompile_seconds,
                },
            }
        )

    stale_isolated_outputs = _cleanup_stale_isolated_outputs()
    with tempfile.TemporaryDirectory(prefix="sage_entrypoint_failure_drill_") as temp_target_dir:
        target_root = Path(temp_target_dir)
        (target_root / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
        isolated_output = _isolated_output_dir(target_root)
        isolated_raw = isolated_output / ".raw"
        isolated_env = {
            "CODEMAPS_TARGET_ROOT": str(target_root),
            "CODEMAPS_EXPECTED_FAILURE_DRILL": "1",
        }
        try:
            _log("START isolated_failure_drill_atlas_seed")
            atlas_seed_proc, atlas_seed_seconds = _run(
                [sys.executable, "-m", "tools.engines.generate_atlas"],
                CODE_MAPS_DIR,
                env_extra=isolated_env,
                timeout=entrypoint_failure_dead_code_drill_timeout_seconds(),
                label="isolated_failure_drill_atlas_seed",
            )
            isolated_atlas = isolated_raw / "atlas.json"
            isolated_db = isolated_raw / "codemaps.db"
            atlas_seeded = (
                atlas_seed_proc.returncode == 0
                and isolated_atlas.exists()
                and isolated_db.exists()
            )
            _log(
                f"{'PASS' if atlas_seeded else 'FAIL'} isolated_failure_drill_atlas_seed "
                f"runtime_seconds={atlas_seed_seconds}"
            )

            _log("START dead_code_missing_atlas_shadow_uses_sqlite_or_fails_gracefully")
            if isolated_atlas.exists():
                isolated_atlas.unlink()
            dead_code_proc, dead_code_seconds = _run(
                [sys.executable, "-m", "tools.engines.dead_code_detector"],
                CODE_MAPS_DIR,
                env_extra=isolated_env,
                timeout=entrypoint_failure_dead_code_drill_timeout_seconds(),
                label="dead_code_missing_atlas_shadow_uses_sqlite_or_fails_gracefully",
            )
            combined = "\n".join([dead_code_proc.stdout or "", dead_code_proc.stderr or ""])
            sqlite_primary_used = atlas_seeded and dead_code_proc.returncode == 0
            failure_details = {
                "isolated_namespace": True,
                "atlas_seed_returncode": atlas_seed_proc.returncode,
                "atlas_seed_runtime_seconds": atlas_seed_seconds,
                "returncode": dead_code_proc.returncode,
                "message_present": "atlas.json not found" in combined.lower(),
                "sqlite_primary_used": sqlite_primary_used,
                "runtime_seconds": dead_code_seconds,
            }
            dead_code_passed = atlas_seeded and (sqlite_primary_used or failure_details["message_present"])
            _log(
                f"{'PASS' if dead_code_passed else 'FAIL'} "
                "dead_code_missing_atlas_shadow_uses_sqlite_or_fails_gracefully "
                f"runtime_seconds={dead_code_seconds}"
            )
            checks.append(
                {
                    "name": "dead_code_missing_atlas_shadow_uses_sqlite_or_fails_gracefully",
                    "passed": dead_code_passed,
                    "details": failure_details,
                }
            )

            _log("START quality_gate_missing_health_stays_controlled")
            quality_gate_proc, quality_gate_seconds = _run(
                [sys.executable, "-m", "tools.engines.quality_gate"],
                CODE_MAPS_DIR,
                env_extra=isolated_env,
                label="quality_gate_missing_health_stays_controlled",
            )
            quality_gate_payload, quality_gate_diagnostic = _load_subprocess_json_output(
                isolated_raw / "quality_gate.json",
                quality_gate_proc,
                label="quality_gate_missing_health_stays_controlled",
            )
            quality_gate_passed = (
                atlas_seeded
                and quality_gate_proc.returncode == 0
                and not quality_gate_diagnostic["read_error"]
                and quality_gate_payload.get("passed") is False
                and bool(quality_gate_payload.get("checks"))
                and any(check.get("name") == "min_health_score" for check in quality_gate_payload.get("checks", []))
            )
            _log(f"{'PASS' if quality_gate_passed else 'FAIL'} quality_gate_missing_health_stays_controlled runtime_seconds={quality_gate_seconds}")
            checks.append(
                {
                    "name": "quality_gate_missing_health_stays_controlled",
                    "passed": quality_gate_passed,
                    "details": {
                        "isolated_namespace": True,
                        "returncode": quality_gate_proc.returncode,
                        "passed": quality_gate_payload.get("passed"),
                        "runtime_seconds": quality_gate_seconds,
                        "output_diagnostic": quality_gate_diagnostic,
                    },
                }
            )
        finally:
            _cleanup_isolated_output(isolated_output)

    canonical_hashes_after = _content_hashes(canonical_inputs)
    canonical_state_after = _canonical_state_fingerprints()
    checks.append(
        {
            "name": "failure_drills_preserve_canonical_inputs",
            "passed": (
                canonical_hashes_before == canonical_hashes_after
                and canonical_state_before == canonical_state_after
            ),
            "details": {
                "json_hashes_before": canonical_hashes_before,
                "json_hashes_after": canonical_hashes_after,
                "sqlite_state_before": canonical_state_before,
                "sqlite_state_after": canonical_state_after,
                "isolated_namespace": True,
                "stale_isolated_outputs_cleaned": stale_isolated_outputs,
            },
        }
    )

    payload = {
        "meta": {
            "kind": "entrypoint_failure_validation",
            "version": "v1",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    return payload


def main() -> int:
    _log("START run_validation")
    payload = run_validation()
    out_path = RAW_DIR / "entrypoint_failure_validation.json"
    save_json_atomic(out_path, payload)
    lines = [
        "# Entrypoint Failure Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(
            f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | "
            f"`{json.dumps(check.get('details', {}), ensure_ascii=False)}` |"
        )
    save_text_atomic(REPORTS_DIR / "entrypoint_failure_validation.md", "\n".join(lines) + "\n")
    _log(
        "DONE "
        f"checks={payload['summary']['passed_checks']}/{payload['summary']['total_checks']} "
        f"failed={payload['summary']['failed_checks']}"
    )
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

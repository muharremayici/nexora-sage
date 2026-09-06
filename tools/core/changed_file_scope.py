from datetime import datetime, timezone
from typing import Any

from tools.core.artifact_freshness_contract import artifact_state_meta
from tools.core.config import RAW_DIR, ROOT
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.operational_limits import quant_git_status_timeout_seconds
from tools.core.path_identity import strip_current_directory_prefix
from tools.core.subprocess_telemetry import run_observed_subprocess


def _repo_relative(path_str: str) -> str:
    value = str(path_str or "").strip().replace("\\", "/")
    if " -> " in value:
        value = value.split(" -> ", 1)[1]
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return strip_current_directory_prefix(value)


def change_scope_evidence(status: str, source: str, files: list[str], **details: Any) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "shape_status": "valid" if status == "available" else "not_evaluated",
        "payload_bytes": sum(len(path.encode("utf-8")) for path in files),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **details,
    }


def get_git_changed_files() -> tuple[list[str], dict[str, Any]]:
    """Return Git change scope without collapsing failed probes into an empty scope."""
    changed: set[str] = set()
    timeout_seconds = quant_git_status_timeout_seconds()
    probe_stats: list[str] = []
    probe_rows: list[dict[str, Any]] = []
    commands = (
        ("unstaged", "quant_git_diff_unstaged", ["git", "diff", "--name-only"]),
        ("cached", "quant_git_diff_cached", ["git", "diff", "--cached", "--name-only"]),
        ("status", "quant_git_status_porcelain", ["git", "status", "--porcelain"]),
    )
    try:
        for name, telemetry_label, command in commands:
            result, duration = run_observed_subprocess(
                command,
                cwd=ROOT,
                label=telemetry_label,
                timeout=timeout_seconds,
                heartbeat_seconds=5,
                log=logger.info,
            )
            probe_stats.append(f"{name} rc={result.returncode} {duration:.3f}s")
            probe_rows.append({"name": name, "returncode": result.returncode, "stderr": result.stderr.strip()})
            if result.returncode != 0:
                continue
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                value = line
                if name == "status":
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) != 2:
                        continue
                    value = parts[1]
                changed.add(_repo_relative(value))
    except Exception as exc:
        logger.warning("Git changed-file resolver unavailable: %s", exc)
        return [], change_scope_evidence(
            "unavailable",
            "git_changed_file_probes",
            [],
            reason="git_probe_exception",
            error=str(exc),
            probes=probe_rows,
        )

    failed_probes = [row for row in probe_rows if row["returncode"] != 0]
    logger.info(
        "Quant git changed-file probes completed: %s; changed_files=%d",
        "; ".join(probe_stats),
        len(changed),
    )
    if failed_probes:
        logger.warning("Quant Git change scope is unavailable: %s", failed_probes)
        return [], change_scope_evidence(
            "unavailable",
            "git_changed_file_probes",
            [],
            reason="one_or_more_git_probes_failed",
            probes=probe_rows,
        )
    files = sorted(changed)
    return files, change_scope_evidence("available", "git_changed_file_probes", files, probes=probe_rows)


def get_changed_files_from_watchdog() -> tuple[list[str], dict[str, Any]]:
    """Return the latest watchdog scope with explicit availability evidence."""
    watchdog_path = RAW_DIR / "watchdog_session.json"
    artifact_meta = artifact_state_meta(RAW_DIR, "watchdog_session")
    if not artifact_meta.get("exists"):
        return [], change_scope_evidence(
            "unavailable",
            "watchdog_session.json",
            [],
            reason="watchdog_session_missing",
            truth_source=artifact_meta.get("source"),
        )
    try:
        data = load_json_file(watchdog_path, {})
        files = data.get("changed_files", [])
        if not isinstance(files, list):
            raise ValueError("watchdog changed_files must be a list")
        normalized = [_repo_relative(path) for path in files]
        return normalized, change_scope_evidence(
            "available",
            "watchdog_session.json",
            normalized,
            updated_at=artifact_meta.get("updated_at")
            or datetime.fromtimestamp(float(artifact_meta.get("mtime") or 0.0), timezone.utc).isoformat(),
            truth_source=artifact_meta.get("source"),
        )
    except Exception as exc:
        logger.warning("Failed to load watchdog_session.json: %s", exc)
        return [], change_scope_evidence(
            "unavailable",
            "watchdog_session.json",
            [],
            reason="watchdog_session_invalid",
            error=str(exc),
        )

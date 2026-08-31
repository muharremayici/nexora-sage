from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import REPORTS_DIR
from tools.core.json_io import load_json_file


def list_report_names(pattern: str) -> list[str]:
    return sorted(path.name for path in REPORTS_DIR.glob(pattern) if path.is_file())


def load_oracle_reports() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for name in list_report_names("oracle_*.json"):
        payload = load_json_file(REPORTS_DIR / name, {})
        if isinstance(payload, dict):
            reports.append(payload)
    return reports


def report_path(name: str) -> Path:
    return REPORTS_DIR / name

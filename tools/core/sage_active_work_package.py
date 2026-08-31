"""Read the narrow, session-independent SAGE developer work-package ledger."""

from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


LEDGER_PATH = CONFIG_DIR / "sage_active_work_package.json"


def load_active_work_package_ledger() -> dict[str, Any]:
    return load_json_object_strict(LEDGER_PATH, label="SAGE active work package ledger")


def active_work_package() -> dict[str, Any]:
    ledger = load_active_work_package_ledger()
    package = ledger.get("active_package")
    if not isinstance(package, dict):
        raise ValueError("SAGE active work package ledger must contain active_package")
    return package

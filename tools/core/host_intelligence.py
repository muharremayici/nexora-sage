from __future__ import annotations

from typing import Dict

from tools.core.artifact_contracts import HOST_INTELLIGENCE_JSON_PATH
from tools.core.json_io import load_json_file

HOST_INTELLIGENCE_PATH = HOST_INTELLIGENCE_JSON_PATH


def load_host_intelligence() -> Dict:
    if not HOST_INTELLIGENCE_PATH.exists():
        return {}
    try:
        return load_json_file(HOST_INTELLIGENCE_PATH, {})
    except Exception:
        return {}


def get_host_studios() -> Dict:
    data = load_host_intelligence()
    studios = data.get("studios", {})
    return studios if isinstance(studios, dict) else {}

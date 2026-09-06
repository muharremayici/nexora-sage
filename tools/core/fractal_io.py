from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.json_io import load_raw_artifact_path


def load_fractal_map_data(raw_dir: Path | None = None, default: Any | None = None) -> Any:
    """Load Fractal Map from SQLite first for the managed SAGE artifact root."""
    fallback = {} if default is None else default
    target_raw_dir = raw_dir or RAW_DIR
    if target_raw_dir == RAW_DIR:
        from tools.core.artifact_store import STORE

        return STORE.load_raw("fractal_map", fallback)
    return load_raw_artifact_path(target_raw_dir / "fractal_map.json", fallback)


def save_fractal_map_data(payload: Any, raw_dir: Path | None = None) -> Path:
    """Persist Fractal Map through ArtifactStore while retaining its JSON shadow."""
    target_raw_dir = raw_dir or RAW_DIR
    path = target_raw_dir / "fractal_map.json"
    if target_raw_dir == RAW_DIR:
        from tools.core.artifact_store import STORE

        STORE.save_raw("fractal_map", payload)
    else:
        save_json_atomic(path, payload)
    return path

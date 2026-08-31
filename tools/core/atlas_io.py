from __future__ import annotations

from typing import Any, Dict
from pathlib import Path

from tools.core.config import RAW_DIR
from tools.core.json_io import load_raw_artifact_path


def load_atlas_data(raw_dir: Path | None = None) -> Dict:
    """Load Atlas through the SQLite-first artifact store when possible.

    For the default SAGE analysis root, Atlas is a managed raw artifact and the
    store reads SQLite first, using JSON only as the shadow/self-heal fallback.
    External target artifacts use their isolated codemaps.db when present and
    retain the adjacent JSON artifact only as the observable shadow fallback.
    """

    target_raw_dir = raw_dir or RAW_DIR
    if target_raw_dir == RAW_DIR:
        from tools.core.artifact_store import STORE

        data = STORE.load_raw("atlas", {})
    else:
        data = load_raw_artifact_path(target_raw_dir / "atlas.json", {})
    return data if isinstance(data, dict) else {}


def resolve_atlas_data(
    atlas: dict[str, Any] | None = None,
    raw_dir: Path | None = None,
) -> tuple[Dict, str]:
    """Resolve a caller-provided runtime Atlas or fall back to SQLite-first storage.

    A provided payload is only an intra-process transport optimization. Durable
    authority remains the Atlas artifact already committed through ArtifactStore.
    """

    if isinstance(atlas, dict):
        return atlas, "provided_runtime_payload"
    return load_atlas_data(raw_dir), "sqlite_first_artifact_store"

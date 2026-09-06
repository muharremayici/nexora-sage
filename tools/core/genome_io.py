from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import RAW_DIR
from tools.core.json_io import load_raw_artifact_path


def load_genome_data(raw_dir: Path | None = None, default: Any | None = None) -> Any:
    """Load Genome through the SQLite-first artifact store when possible.

    The default SAGE analysis root treats Genome as a managed raw artifact, so
    SQLite is consulted first and JSON is only the shadow/self-heal fallback.
    External target artifacts use their isolated codemaps.db when present and
    retain the adjacent JSON artifact only as the observable shadow fallback.
    """

    fallback = {} if default is None else default
    target_raw_dir = raw_dir or RAW_DIR
    if target_raw_dir == RAW_DIR:
        from tools.core.artifact_store import STORE

        return STORE.load_raw("genome", fallback)
    return load_raw_artifact_path(target_raw_dir / "genome.json", fallback)

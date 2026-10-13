from __future__ import annotations

from typing import Any, Dict
from pathlib import Path
from dataclasses import dataclass
import hashlib
import json

from tools.core.config import RAW_DIR
from tools.core.json_io import load_raw_artifact_path
from tools.core.json_syntax import loads_json_strict, JSONSelectionLimitError
from tools.core.unmanaged_atomic_io import native_filesystem_path


@dataclass(frozen=True)
class AtlasRead:
    """Request-bound content provenance, not producer or runtime attestation.

    The data belongs to this read. Do not recertify mutable cached dictionaries
    using a separately read header, or reuse this across requests as freshness.
    """
    data: Dict
    identity: str | None
    source: str
    file_metadata_limit_bytes: int = 0

    @property
    def primary_verified(self) -> bool:
        return self.source in {'sqlite_primary', 'json_primary'} and bool(self.identity)

    def file_metadata(self, project: str, rel_path: str) -> dict | None:
        if not self.primary_verified:
            return None
        project_data = self.data.get(project)
        files = project_data.get('files') if isinstance(project_data, dict) else None
        value = files.get(rel_path) if isinstance(files, dict) else None
        if isinstance(value, dict) and (self.file_metadata_limit_bytes < 1 or len(json.dumps(
                value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > self.file_metadata_limit_bytes):
            raise JSONSelectionLimitError('Atlas file metadata exceeds the current primary read budget.')
        return value if isinstance(value, dict) else None


def load_atlas_data_with_identity(raw_dir: Path | None = None) -> AtlasRead:
    """Bind content/backend/identity in one read, keeping recovery unverified.

    Default backend choice is owned by STORE. External targets retain their
    existing isolated SQLite precedence; without that store the configured
    JSON backend is primary, otherwise JSON is only compatibility orientation.
    A corrupt primary never promotes a shadow into source evidence.
    """
    from tools.core.artifact_store import ArtifactStore, STORE, _record_atlas_primary_read_failure

    target_raw_dir = Path(raw_dir or RAW_DIR)
    store = STORE if target_raw_dir.resolve() == RAW_DIR.resolve() else ArtifactStore(raw_dir=target_raw_dir)
    sqlite_selected = store.use_sqlite if store is STORE else store.db_manager.db_path.is_file() or store.use_sqlite
    try:
        if sqlite_selected:
            verified = store.load_sqlite_atlas_data_with_identity()
            if verified is not None:
                payload, identity = verified
                return AtlasRead(payload, identity, 'sqlite_primary', store.atlas_staging_file_aggregate_limit_bytes)
            # Preserve legacy/missing-primary orientation without granting it
            # an identity or causing default STORE's recovery/self-heal write.
            if store is STORE:
                return AtlasRead({}, None, 'unavailable')
            return AtlasRead(load_atlas_data(target_raw_dir), None, 'unverified_orientation')
        path = target_raw_dir / 'atlas.json'
        content = Path(native_filesystem_path(path)).read_bytes()
        payload = loads_json_strict(content.decode('utf-8-sig'))
        if not isinstance(payload, dict):
            raise ValueError('Primary Atlas JSON root must be an object.')
        return AtlasRead(payload, 'json:' + hashlib.sha256(content).hexdigest(), 'json_primary',
                         store.atlas_staging_file_aggregate_limit_bytes)
    except Exception as exc:
        _record_atlas_primary_read_failure(target_raw_dir, exc)
        return AtlasRead({}, None, 'unavailable')


def load_atlas_file_metadata(raw_dir: Path, project: str, rel_path: str) -> dict | None:
    """Select exact primary metadata, respecting the default backend choice.

    SQLite selection retains bounded selected-object memory. Explicit default
    JSON-primary mode must not borrow a residual trace database as its backend.
    """
    from tools.core.artifact_store import ArtifactStore, STORE

    store = STORE if Path(raw_dir).resolve() == RAW_DIR.resolve() else ArtifactStore(raw_dir=raw_dir)
    if not store.use_sqlite and (store is STORE or not store.db_manager.db_path.is_file()):
        return load_atlas_data_with_identity(raw_dir).file_metadata(project, rel_path)
    return store.load_sqlite_atlas_file_metadata(project, rel_path)


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

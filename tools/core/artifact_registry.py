"""Strict, reloadable access to the canonical declarative artifact registry."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from collections.abc import Iterator, Mapping
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached


REGISTRY_PATH = CONFIG_DIR / "artifact_registry.json"


def _relative_posix_path(value: Any, field: str, artifact_id: str) -> str:
    path = str(value or "").strip()
    if not path or "\\" in path or path.startswith("/") or ".." in Path(path).parts:
        raise ValueError(f"artifact registry {artifact_id}.{field} must be a relative POSIX path")
    return path


def _normalize_artifact_registry(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("meta", {}).get("kind") != "artifact_registry":
        raise ValueError("artifact registry has unexpected meta.kind")
    rows = payload.get("artifacts")
    if not isinstance(rows, list) or not rows:
        raise ValueError("artifact registry artifacts must be a non-empty list")
    row_contract = payload.get("row_contract")
    path_contract = payload.get("path_contract")
    if not isinstance(row_contract, dict) or not isinstance(path_contract, dict):
        raise ValueError("artifact registry is missing row_contract or path_contract")
    required_fields = {str(value) for value in row_contract.get("required_fields", []) if str(value)}
    allowed_availability = {str(value) for value in row_contract.get("availability_values", []) if str(value)}
    storage_roots = path_contract.get("storage_roots")
    if not required_fields or not allowed_availability or not isinstance(storage_roots, dict) or not storage_roots:
        raise ValueError("artifact registry row/path contract must declare non-empty required fields, availability values, and storage roots")
    normalized_storage_roots = {
        str(storage_class): _relative_posix_path(root, "storage_roots", str(storage_class)) + ("" if str(root).endswith("/") else "/")
        for storage_class, root in storage_roots.items()
    }

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    normalized: list[dict[str, str]] = []
    for raw in rows:
        if not isinstance(raw, dict) or not required_fields <= set(raw):
            raise ValueError("artifact registry row is missing required fields")
        artifact_id = str(raw["id"] or "").strip()
        if not artifact_id or artifact_id in seen_ids:
            raise ValueError(f"artifact registry has invalid or duplicate id: {artifact_id!r}")
        schema = _relative_posix_path(raw["schema"], "schema", artifact_id)
        path = _relative_posix_path(raw["path"], "path", artifact_id)
        availability = str(raw["availability"] or "").strip()
        storage_class = str(raw["storage_class"] or "").strip()
        if availability not in allowed_availability:
            raise ValueError(f"artifact registry {artifact_id} has invalid availability: {availability!r}")
        if storage_class not in normalized_storage_roots:
            raise ValueError(f"artifact registry {artifact_id} has invalid storage class: {storage_class!r}")
        if path in seen_paths:
            raise ValueError(f"artifact registry has duplicate artifact path: {path}")
        expected_root = normalized_storage_roots[storage_class]
        if not path.startswith(expected_root):
            raise ValueError(f"artifact {artifact_id} with storage class {storage_class} must be rooted in {expected_root}")
        if not (CODE_MAPS_DIR / schema).is_file():
            raise FileNotFoundError(f"artifact registry schema does not exist: {schema}")
        seen_ids.add(artifact_id)
        seen_paths.add(path)
        normalized.append(
            {
                "id": artifact_id,
                "schema": schema,
                "path": path,
                "availability": availability,
                "storage_class": storage_class,
            }
        )
    return {**payload, "artifacts": normalized}


def load_artifact_registry() -> dict[str, Any]:
    """Return registry data, refreshing when its machine-readable source changes."""
    return load_json_object_strict_cached(
        REGISTRY_PATH,
        label="artifact registry",
        normalizer=_normalize_artifact_registry,
    )


def artifact_metadata() -> dict[str, dict[str, str]]:
    return {row["id"]: dict(row) for row in load_artifact_registry()["artifacts"]}


def artifact_schema_paths() -> dict[str, Path]:
    return {artifact_id: CODE_MAPS_DIR / row["schema"] for artifact_id, row in artifact_metadata().items()}


def artifact_paths() -> dict[str, Path]:
    return {artifact_id: CODE_MAPS_DIR / row["path"] for artifact_id, row in artifact_metadata().items()}


def artifact_path_for_storage_root(storage_root: Path, artifact_id: str) -> Path:
    """Project a registered artifact path beneath an equivalent storage root."""
    registry = load_artifact_registry()
    metadata = {row["id"]: row for row in registry["artifacts"]}
    if artifact_id not in metadata:
        raise KeyError(f"artifact is not registered: {artifact_id}")
    row = metadata[artifact_id]
    declared_root = registry["path_contract"]["storage_roots"][row["storage_class"]]
    relative_path = PurePosixPath(row["path"]).relative_to(PurePosixPath(declared_root))
    return Path(storage_root).joinpath(*relative_path.parts)


def mandatory_artifact_ids() -> set[str]:
    return {artifact_id for artifact_id, row in artifact_metadata().items() if row["availability"] == "mandatory"}


class _ArtifactRegistryView(Mapping[str, Any]):
    """Mapping facade that never retains a stale registry decision after mtime changes."""

    def __init__(self, field: str) -> None:
        self._field = field

    def _current(self) -> dict[str, Any]:
        if self._field == "metadata":
            return artifact_metadata()
        if self._field == "schema":
            return artifact_schema_paths()
        return artifact_paths()

    def __getitem__(self, key: str) -> Any:
        return self._current()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._current())

    def __len__(self) -> int:
        return len(self._current())


# Shared views keep existing consumers stable while the JSON registry owns the data.
ARTIFACT_METADATA: Mapping[str, dict[str, str]] = _ArtifactRegistryView("metadata")
ARTIFACT_SCHEMAS: Mapping[str, Path] = _ArtifactRegistryView("schema")
ARTIFACT_PATHS: Mapping[str, Path] = _ArtifactRegistryView("path")

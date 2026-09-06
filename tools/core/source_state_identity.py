"""Deterministic identity for the governed SAGE source tree."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR
from tools.core.source_layer_classifier import classify_source_layer, is_ignored


def iter_governed_source_files(
    root: Path = CODE_MAPS_DIR,
    *,
    include_generated_runtime: bool = False,
) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or is_ignored(path, root=root):
            continue
        layer, _ = classify_source_layer(path, root=root)
        if not include_generated_runtime and layer == "generated_runtime_artifact":
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def source_state_identity(root: Path = CODE_MAPS_DIR) -> dict[str, Any]:
    digest = hashlib.sha256()
    files = iter_governed_source_files(root)
    total_bytes = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                total_bytes += len(chunk)
                digest.update(chunk)
        digest.update(b"\0")
    return {
        "algorithm": "sha256_path_and_content_v1",
        "fingerprint": digest.hexdigest(),
        "file_count": len(files),
        "total_bytes": total_bytes,
    }

"""Exact-text identity shared by snapshot producers and consumers."""
from __future__ import annotations

import hashlib
from pathlib import Path

from tools.core.strict_contract_cache import load_json_object_strict_cached


POLICY_PATH = Path(__file__).resolve().parents[2] / "config/source_snapshot_store_policy.json"


def snapshot_hash_algorithms() -> dict[str, str]:
    policy = load_json_object_strict_cached(POLICY_PATH, label="source snapshot identity")
    identity = policy.get("content_identity")
    algorithms = identity.get("digest_algorithms") if isinstance(identity, dict) else None
    if not isinstance(algorithms, dict) or not algorithms:
        raise ValueError("Missing source snapshot digest algorithms")
    for width, algorithm in algorithms.items():
        if not isinstance(algorithm, str) or str(hashlib.new(algorithm).digest_size * 2) != width:
            raise ValueError("Invalid source snapshot digest algorithm")
    return algorithms


def source_text_hash(content: str, reference: str, *, algorithms: dict[str, str] | None = None) -> str | None:
    """Preserve BOM/CRLF; unknown identities never fall back to timestamps."""
    if not isinstance(content, str) or not isinstance(reference, str) or not reference:
        return None
    if any(char not in "0123456789abcdef" for char in reference):
        return None
    allowed = snapshot_hash_algorithms() if algorithms is None else algorithms
    algorithm = allowed.get(str(len(reference)))
    if not algorithm:
        return None
    return hashlib.new(algorithm, content.encode("utf-8")).hexdigest()


def snapshot_content_status(content: str, content_hash: str, atlas_hash: str, *,
                            status: str = "ok", algorithms: dict[str, str] | None = None) -> str:
    if status != "ok":
        return status
    actual = source_text_hash(content, atlas_hash, algorithms=algorithms)
    if actual is None:
        return "identity_unavailable"
    if content_hash != atlas_hash:
        return "atlas_mismatch"
    return "ok" if actual == content_hash else "content_mismatch"

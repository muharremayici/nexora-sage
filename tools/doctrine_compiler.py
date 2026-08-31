from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CODE_MAPS_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = CODE_MAPS_DIR / "config"
DOCTRINES_DIR = CONFIG_DIR / "doctrines"
MANIFEST_PATH = DOCTRINES_DIR / "manifest.json"
COMPILED_DOCTRINE_PATH = CONFIG_DIR / "architecture_doctrine.json"


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def doctrine_source_fingerprint(manifest_path: Path = MANIFEST_PATH) -> str:
    """Fingerprint manifest and pack semantics without host-specific byte drift."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Doctrine manifest is not an object: {manifest_path}")

    digest = hashlib.sha256()
    digest.update(b"manifest\0")
    digest.update(_canonical_json_bytes(manifest))
    active = manifest.get("active_doctrines")
    if not isinstance(active, list) or not active:
        raise ValueError("Doctrine manifest must declare non-empty active_doctrines")
    for entry in active:
        if not isinstance(entry, dict) or not entry.get("path"):
            raise ValueError(f"Doctrine pack entry missing path: {entry}")
        rel_path = str(entry["path"]).replace("\\", "/")
        pack_path = manifest_path.parent / rel_path
        digest.update(b"pack\0")
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        if not pack_path.exists():
            if entry.get("required", True):
                raise FileNotFoundError(f"Required doctrine pack is missing: {pack_path}")
            digest.update(b"optional-missing")
            continue
        pack_payload = json.loads(pack_path.read_text(encoding="utf-8-sig"))
        digest.update(_canonical_json_bytes(pack_payload))
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def _manifest_reference(manifest_path: Path) -> str:
    try:
        return str(manifest_path.relative_to(CODE_MAPS_DIR).as_posix())
    except ValueError:
        return str(manifest_path.resolve().as_posix())


def _deep_merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, dict):
        return deepcopy(override)
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _pack_payload(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Doctrine pack is not an object: {path}")
    meta = payload.get("_meta")
    if not isinstance(meta, dict) or meta.get("kind") != "nexora.doctrine_pack":
        raise ValueError(f"Doctrine pack missing _meta.kind=nexora.doctrine_pack: {path}")
    return {key: value for key, value in payload.items() if key != "_meta"}


def _contains_contract_key(value: Any, contract_key: str) -> bool:
    if isinstance(value, dict):
        return contract_key in value or any(
            _contains_contract_key(nested, contract_key) for nested in value.values()
        )
    if isinstance(value, list):
        return any(_contains_contract_key(nested, contract_key) for nested in value)
    return False


def _validate_exclusive_contract_owners(
    manifest: dict[str, Any],
    pack_payloads: list[tuple[str, dict[str, Any]]],
) -> None:
    validation_contract = manifest.get("validation_contract", {})
    owners = (
        validation_contract.get("exclusive_contract_owners", {})
        if isinstance(validation_contract, dict)
        else {}
    )
    if not isinstance(owners, dict):
        raise ValueError("Doctrine validation_contract.exclusive_contract_owners must be an object")

    known_pack_ids = {pack_id for pack_id, _payload in pack_payloads}
    for contract_key, owner_id in owners.items():
        if (
            not isinstance(contract_key, str)
            or not contract_key
            or not isinstance(owner_id, str)
            or not owner_id
        ):
            raise ValueError("Doctrine exclusive contract owner entries must map non-empty strings")
        if owner_id not in known_pack_ids:
            raise ValueError(f"Doctrine exclusive contract owner is not active: {contract_key} -> {owner_id}")
        declaring_packs = [
            pack_id
            for pack_id, payload in pack_payloads
            if _contains_contract_key(payload, contract_key)
        ]
        if declaring_packs != [owner_id]:
            raise ValueError(
                f"Doctrine contract ownership violation for {contract_key}: "
                f"expected only {owner_id}, found {declaring_packs}"
            )


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"Doctrine manifest is not an object: {manifest_path}")
    meta = manifest.get("_meta")
    if not isinstance(meta, dict) or meta.get("kind") != "nexora.doctrine_manifest":
        raise ValueError("Doctrine manifest must declare _meta.kind=nexora.doctrine_manifest")
    active = manifest.get("active_doctrines")
    if not isinstance(active, list) or not active:
        raise ValueError("Doctrine manifest must declare non-empty active_doctrines")
    return manifest


def compile_doctrine_registry(manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    compiled: dict[str, Any] = {
        "_meta": {
            "kind": "architecture_doctrine",
            "version": "1.1",
            "description": "Compiled Nexora SAGE architecture doctrine from modular doctrine packs",
            "compiled": True,
            "compiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "compiled_by": "tools/doctrine_compiler.py",
            "compiled_from_manifest": _manifest_reference(manifest_path),
            "compiled_pack_count": 0,
            "compiled_pack_ids": [],
            "source_fingerprint": doctrine_source_fingerprint(manifest_path),
        }
    }

    pack_payloads: list[tuple[str, dict[str, Any]]] = []
    for entry in manifest.get("active_doctrines", []):
        if not isinstance(entry, dict):
            raise ValueError("Doctrine manifest active_doctrines entries must be objects")
        rel_path = entry.get("path")
        pack_id = entry.get("id") or rel_path
        if not rel_path:
            raise ValueError(f"Doctrine pack entry missing path: {entry}")
        pack_path = (manifest_path.parent / str(rel_path)).resolve()
        if not pack_path.exists():
            if entry.get("required", True):
                raise FileNotFoundError(f"Required doctrine pack is missing: {pack_path}")
            continue
        pack_payloads.append((str(pack_id), _pack_payload(pack_path)))

    _validate_exclusive_contract_owners(manifest, pack_payloads)
    pack_ids: list[str] = []
    for pack_id, payload in pack_payloads:
        compiled = _deep_merge(compiled, payload)
        pack_ids.append(pack_id)

    compiled["_meta"]["compiled_pack_count"] = len(pack_ids)
    compiled["_meta"]["compiled_pack_ids"] = pack_ids
    return compiled


def doctrine_without_compile_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(payload)
    meta = cleaned.get("_meta")
    if isinstance(meta, dict):
        for key in (
            "description",
            "compiled",
            "compiled_at",
            "compiled_by",
            "compiled_from_manifest",
            "compiled_pack_count",
            "compiled_pack_ids",
            "source_fingerprint",
        ):
            meta.pop(key, None)
    return cleaned


def manifest_newer_than_output(manifest_path: Path = MANIFEST_PATH, output_path: Path = COMPILED_DOCTRINE_PATH) -> bool:
    if not manifest_path.exists():
        return False
    if not output_path.exists():
        return True
    try:
        output = _load_json(output_path)
        output_meta = output.get("_meta") if isinstance(output, dict) else None
        compiled_fingerprint = (
            str(output_meta.get("source_fingerprint") or "")
            if isinstance(output_meta, dict)
            else ""
        )
        return not compiled_fingerprint or compiled_fingerprint != doctrine_source_fingerprint(manifest_path)
    except Exception:
        return True


def write_compiled_doctrine(output_path: Path = COMPILED_DOCTRINE_PATH, manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    compiled = compile_doctrine_registry(manifest_path)
    _save_json(output_path, compiled)
    return compiled


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile modular Nexora SAGE doctrine packs into architecture_doctrine.json.")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Doctrine manifest path.")
    parser.add_argument("--output", default=str(COMPILED_DOCTRINE_PATH), help="Compiled doctrine output path.")
    parser.add_argument("--check", action="store_true", help="Compile without writing and verify manifest/pack validity.")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    output_path = Path(args.output)
    compiled = compile_doctrine_registry(manifest_path)
    if not args.check:
        _save_json(output_path, compiled)
    print(
        json.dumps(
            {
                "status": "PASS",
                "packs": compiled.get("_meta", {}).get("compiled_pack_count"),
                "output": str(output_path),
                "written": not args.check,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_file


PRINCIPLES_DIR = CONFIG_DIR / "principles"
PRINCIPLE_MANIFEST_FILE = PRINCIPLES_DIR / "manifest.json"


def load_principle_manifest(path: Path = PRINCIPLE_MANIFEST_FILE) -> dict[str, Any]:
    payload = load_json_file(path, {})
    if not isinstance(payload, dict):
        return {}
    return payload


def principle_contract(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = manifest if isinstance(manifest, dict) else load_principle_manifest()
    contract = manifest.get("contract", {}) if isinstance(manifest, dict) else {}
    return contract if isinstance(contract, dict) else {}


def required_principle_fields(manifest: dict[str, Any] | None = None) -> set[str]:
    fields = principle_contract(manifest).get("required_principle_fields", [])
    if not isinstance(fields, list):
        return set()
    return {str(field) for field in fields if isinstance(field, str) and field.strip()}


def principle_pack_paths(manifest: dict[str, Any] | None = None) -> list[Path]:
    manifest = manifest if isinstance(manifest, dict) else load_principle_manifest()
    paths: list[Path] = []
    for entry in manifest.get("active_principles", []) or []:
        if isinstance(entry, dict) and entry.get("path"):
            paths.append(PRINCIPLES_DIR / str(entry["path"]))
    return paths


def load_principle_packs(manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    for path in principle_pack_paths(manifest):
        payload = load_json_file(path, {})
        if isinstance(payload, dict):
            packs.append(payload)
    return packs


def iter_principles(packs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pack in packs if isinstance(packs, list) else load_principle_packs():
        meta = pack.get("_meta", {}) if isinstance(pack, dict) else {}
        namespace = str(meta.get("namespace") or "")
        for item in pack.get("principles", []) or []:
            if isinstance(item, dict):
                row = dict(item)
                row["namespace"] = namespace
                rows.append(row)
    return rows


def principles_for_context(context: str, *, limit: int = 4) -> list[dict[str, Any]]:
    target = str(context or "").strip()
    if not target:
        return []
    matches: list[dict[str, Any]] = []
    for principle in iter_principles():
        applies_to = {str(item) for item in principle.get("applies_to", []) or []}
        if target in applies_to or "agent_directive" in applies_to:
            matches.append(compact_principle(principle))
    return matches[: max(1, int(limit or 4))]


def compact_principle(principle: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": principle.get("id"),
        "label": principle.get("label"),
        "rationale": principle.get("rationale"),
        "directive_hint": principle.get("directive_hint"),
        "enforcement": principle.get("enforcement"),
    }


def validate_principle_pack(
    pack: dict[str, Any],
    *,
    path: Path | None = None,
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    required_fields = required_principle_fields(manifest)
    if not required_fields:
        errors.append("missing_manifest_contract:required_principle_fields")
    meta = pack.get("_meta") if isinstance(pack, dict) else None
    if not isinstance(meta, dict) or meta.get("kind") != "nexora.principle_pack":
        errors.append("missing_meta_kind:nexora.principle_pack")
    if not isinstance(meta, dict) or not str(meta.get("namespace") or "").strip():
        errors.append("missing_meta_namespace")
    if isinstance(meta, dict) and meta.get("status") != "advisory":
        errors.append("principle_pack_status_must_be_advisory")
    principles = pack.get("principles") if isinstance(pack, dict) else None
    if not isinstance(principles, list) or not principles:
        errors.append("principles_must_be_non_empty_list")
        return errors
    ids: list[str] = []
    for item in principles:
        if not isinstance(item, dict):
            errors.append("principle_must_be_object")
            continue
        ids.append(str(item.get("id") or ""))
        missing = sorted(field for field in required_fields if field not in item)
        if missing:
            errors.append(f"{item.get('id') or '<missing-id>'}:missing_fields:{','.join(missing)}")
        if item.get("enforcement") != "advisory":
            errors.append(f"{item.get('id') or '<missing-id>'}:enforcement_must_be_advisory")
        applies_to = item.get("applies_to")
        if not isinstance(applies_to, list) or not all(isinstance(value, str) and value.strip() for value in applies_to):
            errors.append(f"{item.get('id') or '<missing-id>'}:invalid_applies_to")
    duplicates = sorted(item for item in set(ids) if item and ids.count(item) > 1)
    if duplicates:
        errors.append(f"duplicate_principle_ids:{','.join(duplicates)}")
    return errors


def build_principle_pack_summary(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = manifest if isinstance(manifest, dict) else load_principle_manifest()
    packs = load_principle_packs(manifest)
    principles = iter_principles(packs)
    context_counts: Counter[str] = Counter()
    for principle in principles:
        for context in principle.get("applies_to", []) or []:
            context_counts[str(context)] += 1
    return {
        "manifest": str(PRINCIPLE_MANIFEST_FILE.relative_to(CONFIG_DIR.parent).as_posix()),
        "packs": len(packs),
        "principles": len(principles),
        "contexts": dict(sorted(context_counts.items())),
        "contract": manifest.get("contract", {}) if isinstance(manifest, dict) else {},
    }


def render_principle_pack_report(validation: dict[str, Any]) -> str:
    summary = validation.get("summary", {})
    lines = [
        "# Principle Pack Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- packs: `{summary.get('packs')}`",
        f"- principles: `{summary.get('principles')}`",
        f"- quality_gate_effect: `{summary.get('contract', {}).get('quality_gate_effect')}`",
        f"- doctrine_compilation: `{summary.get('contract', {}).get('doctrine_compilation')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in validation.get("checks", []):
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | {escaped_details} |")
    return "\n".join(lines) + "\n"

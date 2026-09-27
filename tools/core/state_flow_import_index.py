"""Snapshot-local importer acceleration for focused State Flow queries.

This index narrows which recorded files to inspect. It never substitutes for
the caller's import, parser, module-identity or live-source checks.
"""

import hashlib
import json

INDEX_KIND = "nexora.atlas.resolved_module_importers"
INDEX_VERSION = "v1"


def import_index_file_identity_row(rel: str, record: dict) -> bytes | None:
    """Encode one canonical path, source and captured resolution-input identity."""
    if not isinstance(rel, str) or not rel or not isinstance(record, dict):
        return None
    workspace_rel = record.get("workspace_rel") or rel
    source_hash = record.get("hash")
    auxiliary_identity = record.get("auxiliary_input_identity", "")
    if (not isinstance(workspace_rel, str) or not workspace_rel
            or not isinstance(source_hash, str) or not source_hash
            or not isinstance(auxiliary_identity, str)):
        return None
    return (json.dumps((rel, workspace_rel, source_hash, auxiliary_identity),
                       ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")


def build_resolved_module_importer_index(files: dict) -> dict | None:
    """Index only resolved sources that name a file or workspace alias here."""
    if not isinstance(files, dict):
        return None
    aliases = set()
    file_identities = []
    for rel, record in files.items():
        identity_row = import_index_file_identity_row(rel, record)
        if (not isinstance(rel, str) or not rel or not isinstance(record, dict)
                or not isinstance(record.get("import_records"), list)
                or identity_row is None):
            return None
        file_identities.append((rel, identity_row))
        workspace_rel = record.get("workspace_rel") or rel
        if not isinstance(workspace_rel, str) or not workspace_rel:
            return None
        aliases.update((rel, workspace_rel))

    by_source = {}
    for rel, record in files.items():
        for entry in record["import_records"]:
            if not isinstance(entry, dict):
                return None
            source = entry.get("source")
            if not isinstance(source, str) or source not in aliases:
                continue
            importers = by_source.setdefault(source, [])
            if not importers or importers[-1] != rel:
                importers.append(rel)
    for importers in by_source.values():
        importers.sort()
    file_identity = hashlib.sha256()
    for _, identity_row in sorted(file_identities):
        file_identity.update(identity_row)
    return {
        "kind": INDEX_KIND, "version": INDEX_VERSION, "status": "complete",
        "scope": "same_project_recorded_resolved_import_sources_only",
        "indexed_file_count": len(files),
        "file_identity_sha256": file_identity.hexdigest(), "by_source": by_source,
    }


def select_resolved_module_importers(project_data: dict, files: dict,
                                     target: dict, file_order: dict,
                                     file_identity_sha256: str | None) -> list | None:
    """Return ordered candidates, or None when an old/stale index needs fallback."""
    index = project_data.get("resolved_module_importer_index")
    if (not isinstance(index, dict) or index.get("kind") != INDEX_KIND
            or index.get("version") != INDEX_VERSION or index.get("status") != "complete"
            or index.get("scope") != "same_project_recorded_resolved_import_sources_only"
            or type(index.get("indexed_file_count")) is not int
            or index["indexed_file_count"] != len(files)
            or not file_identity_sha256
            or index.get("file_identity_sha256") != file_identity_sha256
            or not isinstance(index.get("by_source"), dict)):
        return None
    prefix, separator, rel = target.get("atlas_ref", "").partition("::")
    if not separator or not prefix or rel not in files:
        return None
    aliases = {rel, target.get("target_file")}
    selected = set()
    for alias in aliases:
        rows = index["by_source"].get(alias, [])
        if not isinstance(rows, list):
            return None
        for importer in rows:
            if not isinstance(importer, str) or importer not in file_order or importer not in files:
                return None
            selected.add(importer)
    return [(importer, files[importer]) for importer in sorted(selected, key=file_order.__getitem__)]

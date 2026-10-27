"""Bounded input and directory identities captured by Atlas, not a rescan."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from tools.core.json_io import load_json_object_strict_cached


POLICY_PATH = Path(__file__).resolve().parents[2] / "config/analysis_snapshot_lineage_contract.json"


def resolution_input_policy() -> dict[str, Any]:
    policy = load_json_object_strict_cached(POLICY_PATH, label="Analysis snapshot lineage")
    settings = policy["artifacts"]["ts_diagnostics"]["atlas_resolution_inputs"]
    for key in ("max_directories", "max_entries", "max_name_bytes"):
        if type(settings.get(key)) is not int or settings[key] < 1:
            raise ValueError(f"Invalid Atlas TypeScript resolution input budget: {key}")
    return settings


def valid_directory_name(value: Any) -> bool:
    return (isinstance(value, str) and bool(value) and value not in {".", ".."}
            and not any(char in value for char in ("/", "\\", ":", "\0")))


class ResolutionInputCapture:
    """Reuse complete os.walk listings; a filtered file inventory proves no absence."""

    def __init__(self, project_root: Path, *, surgical: bool = False):
        self.policy = resolution_input_policy()
        self.root = Path(project_root).resolve()
        self.payload: dict[str, Any] = {
            "version": "v1", "project_root": self.root.as_posix(),
            "scope": "captured_directory_names_only",
            "path_semantics": "windows_directory_names" if os.name == "nt" else "posix_directory_names",
            "status": "unavailable_surgical_scan" if surgical else "captured",
            "observed_directories": 0, "omitted_directories": 0, "walk_errors": 0,
            "entry_count": 0, "name_bytes": 0, "directories": {},
        }

    def record_walk_error(self, _error: OSError) -> None:
        self.payload["walk_errors"] += 1
        self.payload["status"] = "partial"

    def observe_directory(self, directory: str, dirs: list[str], files: list[str]) -> None:
        result = self.payload
        if result["status"] == "unavailable_surgical_scan":
            return
        result["observed_directories"] += 1
        try:
            candidate = Path(directory).absolute()
            relative = candidate.relative_to(self.root).as_posix()
            # Aliased directories are not canonical inventory authority. Parent
            # listings retain the link name, so its children cannot appear absent.
            if candidate.resolve() != candidate or ".." in candidate.parts:
                raise ValueError("Noncanonical directory")
            names = sorted(set(dirs + files))
            if any(not valid_directory_name(name) for name in names):
                raise ValueError("Invalid directory entry")
            name_bytes = sum(len(name.encode("utf-8")) for name in names)
            if (len(result["directories"]) >= self.policy["max_directories"]
                    or result["entry_count"] + len(names) > self.policy["max_entries"]
                    or result["name_bytes"] + name_bytes > self.policy["max_name_bytes"]):
                raise ValueError("Resolution inventory budget exceeded")
        except (OSError, ValueError, UnicodeError):
            result["omitted_directories"] += 1
            result["status"] = "partial"
            return
        result["directories"][relative] = names
        result["entry_count"] += len(names)
        result["name_bytes"] += name_bytes


def auxiliary_input_policy() -> dict[str, Any]:
    policy = load_json_object_strict_cached(POLICY_PATH, label="Analysis snapshot lineage")
    settings = policy["artifacts"]["ts_diagnostics"]["atlas_auxiliary_inputs"]
    suffixes = settings.get("file_suffixes")
    if not isinstance(suffixes, list) or not suffixes or any(
        not isinstance(value, str) or not value.startswith(".") for value in suffixes
    ):
        raise ValueError("Invalid Atlas TypeScript auxiliary input suffixes")
    for key in ("max_files", "max_file_bytes", "max_total_bytes"):
        if type(settings.get(key)) is not int or settings[key] < 1:
            raise ValueError(f"Invalid Atlas TypeScript auxiliary input budget: {key}")
    return settings


def capture_auxiliary_inputs(
    project_root: Path, walk_items: Iterable[tuple[str, str, str]], *, surgical: bool = False,
) -> dict[str, Any]:
    """Capture one bounded read per selected file. Never assert filesystem absence.

    A surgical walk cannot refresh the project-wide auxiliary inventory. Discard
    its previous authority instead of relabeling old inputs with a new snapshot.
    """
    policy = auxiliary_input_policy()
    root = Path(project_root).resolve()
    result: dict[str, Any] = {
        "version": "v1", "project_root": root.as_posix(),
        "scope": "owned_walk_positive_inputs_only", "negative_resolution_authority": False,
        "status": "unavailable_surgical_scan" if surgical else "captured",
        "selected_files": 0, "omitted_files": 0, "bytes_read": 0, "files": {},
    }
    if surgical:
        return result
    entries = result["files"]
    candidates = (row for row in walk_items if row[2].lower().endswith(tuple(policy["file_suffixes"])))
    for full_path, _name, relative in sorted(candidates, key=lambda row: row[2]):
        result["selected_files"] += 1
        if len(entries) >= policy["max_files"]:
            result["omitted_files"] += 1
            continue
        parts = PurePosixPath(relative).parts
        if (not relative or "\\" in relative or ":" in relative or relative.startswith("/")
                or ".." in parts or str(PurePosixPath(relative)) != relative or relative in entries):
            result["omitted_files"] += 1
            continue
        entry: dict[str, Any] = {"status": "unavailable"}
        entries[relative] = entry
        try:
            source = Path(full_path).resolve()
            canonical_file_path = source.as_posix() == (root / relative).as_posix()
            if source != (root / relative).resolve() or not source.is_relative_to(root):
                entry["status"] = "outside_project"
                continue
            remaining = policy["max_total_bytes"] - result["bytes_read"]
            if remaining <= 0:
                entry["status"] = "total_byte_budget_exceeded"
                continue
            # Bounded read also handles a file growing after enumeration/stat.
            limit = min(policy["max_file_bytes"], remaining)
            with source.open("rb") as handle:
                data = handle.read(limit + 1)
            result["bytes_read"] += len(data)
            if len(data) > limit:
                entry["status"] = "byte_budget_exceeded"
                continue
            # TypeScript sys.readFile strips UTF-8 BOM, but preserves line endings.
            # Other encodings stay unavailable rather than guessing host semantics.
            text = data.decode("utf-8-sig", errors="strict")
            entry.update(status="ok", size_bytes=len(data),
                         raw_sha256=hashlib.sha256(data).hexdigest(),
                         host_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                         canonical_file_path=canonical_file_path)
        except UnicodeError:
            entry["status"] = "unsupported_encoding"
        except (OSError, ValueError):
            entry["status"] = "unreadable"
    if result["omitted_files"] or any(row["status"] != "ok" for row in entries.values()):
        result["status"] = "partial"
    return result


def auxiliary_context_identity(inventory: dict[str, Any]) -> str:
    """Only a complete ingestion inventory may authorize context-dependent reuse."""
    if (not isinstance(inventory, dict) or inventory.get("version") != "v1"
            or inventory.get("status") != "captured"
            or inventory.get("scope") != "owned_walk_positive_inputs_only"
            or inventory.get("negative_resolution_authority") is not False):
        return ""
    from tools.core.atlas_integrity import payload_sha256

    return payload_sha256(inventory)


def declaration_contract_policy() -> tuple[dict[str, Any], str]:
    """One central syntax/admission budget, independent of runtime consumers."""
    raw = POLICY_PATH.read_bytes()
    settings = json.loads(raw)["artifacts"]["ts_diagnostics"]["atlas_auxiliary_inputs"]["declaration_contract_evidence"]
    if settings.get("version") != "v1-top-level-declaration-headers":
        raise ValueError("Invalid declaration syntax version")
    for key in ("max_files", "max_captured_bytes", "max_records", "max_statements", "max_names", "max_name_bytes"):
        if type(settings.get(key)) is not int or settings[key] < 1:
            raise ValueError(f"Invalid declaration syntax budget: {key}")
    for key in ("supported_suffixes", "statement_kinds", "parser_reason_codes"):
        value = settings.get(key)
        if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f"Invalid declaration syntax vocabulary: {key}")
    return settings, hashlib.sha256(raw).hexdigest()


def _declaration_selection(project_root: Path, inventory: dict[str, Any]):
    policy, digest = declaration_contract_policy()
    root = Path(project_root).resolve()
    if (not isinstance(inventory, dict) or inventory.get("version") != "v1"
            or inventory.get("project_root") != root.as_posix()
            or inventory.get("scope") != "owned_walk_positive_inputs_only"
            or inventory.get("negative_resolution_authority") is not False
            or inventory.get("status") not in {"captured", "partial"}
            or not isinstance(inventory.get("files"), dict)
            or len(inventory["files"]) > auxiliary_input_policy()["max_files"]):
        return policy, digest, [], None, "auxiliary_scope_unavailable"
    names = sorted(name for name in inventory["files"] if isinstance(name, str)
                   and name.lower().endswith(tuple(policy["supported_suffixes"])))
    selected, total = [], 0
    for name in names[:policy["max_files"]]:
        entry = inventory["files"][name]
        reason = None
        parts = PurePosixPath(name).parts
        path = root / name
        if (not parts or name.startswith("/") or "\\" in name or ":" in name or ".." in parts
                or PurePosixPath(name).as_posix() != name):
            reason = "invalid_auxiliary_path"
        elif (not isinstance(entry, dict) or entry.get("status") != "ok"
                or entry.get("canonical_file_path") is not True
                or type(entry.get("size_bytes")) is not int or entry["size_bytes"] < 0
                or entry["size_bytes"] > auxiliary_input_policy()["max_file_bytes"]
                or not isinstance(entry.get("raw_sha256"), str) or len(entry["raw_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in entry["raw_sha256"])):
            reason = "auxiliary_file_unavailable"
        elif total + entry["size_bytes"] > policy["max_captured_bytes"]:
            reason = "declaration_byte_budget_exceeded"
        else:
            try:
                if path.resolve() != path or not path.is_relative_to(root):
                    reason = "noncanonical_current_path"
            except (OSError, ValueError):
                reason = "noncanonical_current_path"
            if reason is None:
                total += entry["size_bytes"]
        selected.append((name, entry, reason))
    omitted = len(names) - len(selected)
    if inventory.get("omitted_files"):
        omitted = None  # Unseen auxiliary entries may include more declarations.
    return policy, digest, selected, omitted, None


def declaration_input_paths(project_root: Path, inventory: dict[str, Any]) -> list[str]:
    """Select only captured canonical inputs, never enumerate another tree."""
    _, _, selected, _, _ = _declaration_selection(project_root, inventory)
    return [(Path(project_root).resolve() / name).as_posix() for name, _, reason in selected if reason is None]


def declaration_file_shape_valid(value: Any, policy: dict, digest: str) -> bool:
    """Shared filesystem-free syntax shape; not capture or producer authority."""
    counters = ("record_count", "omitted_statements", "omitted_name_nodes", "uninspected_module_bodies")
    fields = {"version", "coverage", "status", "policy_sha256", "parser_kind", "parser_version",
              "parser_input_sha256", "size_bytes", "offset_unit", "records", "reasons", "runtime_execution", *counters}
    if not isinstance(value, dict) or set(value) != fields:
        return False
    records, reasons, size = value["records"], value["reasons"], value["size_bytes"]
    identity = value["parser_input_sha256"]
    try:
        if isinstance(value["parser_version"], str):
            value["parser_version"].encode("utf-8")
    except UnicodeError:
        return False
    if not (value["version"] == policy["version"] and value["coverage"] == "finite_top_level_headers_only"
            and value["policy_sha256"] == digest and value["parser_kind"] == "typescript_compiler_api"
            and isinstance(value["parser_version"], str) and 0 < len(value["parser_version"]) <= 128
            and isinstance(identity, str) and len(identity) == 64 and all(c in "0123456789abcdef" for c in identity)
            and type(size) is int and size >= 0 and value["offset_unit"] == "utf8_bytes"
            and value["runtime_execution"] == "not_established" and value["status"] in ("observed", "partial")
            and isinstance(records, list) and len(records) <= policy["max_records"]
            and all(type(value[key]) is int and value[key] >= 0 for key in counters)
            and value["record_count"] == len(records) and isinstance(reasons, dict)
            and all(key in policy["parser_reason_codes"] and type(count) is int and count > 0 for key, count in reasons.items())):
        return False

    def bounded_text(text):
        try:
            return isinstance(text, str) and len(text.encode("utf-8")) <= policy["max_name_bytes"]
        except UnicodeError:
            return False

    row_fields = {"syntax_kind", "names", "module_specifier", "export_modifier", "declare_modifier",
                  "default_modifier", "global_augmentation", "start_byte", "end_byte"}
    previous_end = 0
    for row in records:
        if not isinstance(row, dict) or set(row) != row_fields:
            return False
        names, module = row["names"], row["module_specifier"]
        start, end = row["start_byte"], row["end_byte"]
        if (row["syntax_kind"] not in policy["statement_kinds"]
                or not isinstance(names, list) or len(names) > policy["max_names"] or any(not bounded_text(item) for item in names)
                or not (module is None or bounded_text(module))
                or any(type(row[key]) is not bool for key in ("export_modifier", "declare_modifier", "default_modifier", "global_augmentation"))
                or type(start) is not int or type(end) is not int or not previous_end <= start < end <= size):
            return False
        previous_end = end
    omissions = bool(reasons) or any(value[key] for key in counters[1:])
    return (value["status"] == "observed") != bool(omissions)


def build_declaration_contract_evidence(
    project_root: Path, inventory: dict[str, Any], parser_results: dict[str, Any],
) -> dict[str, Any]:
    """Admit native syntax against exact auxiliary bytes, outside runtime graphs.

    This is input/transport binding, not hostile-producer attestation or atomic
    link confinement. The native parser hashes the actual bounded read; a race
    changing those bytes makes admission fail instead of borrowing the capture.
    """
    policy, digest, selected, omitted, error = _declaration_selection(project_root, inventory)
    result = {"version": policy["version"], "coverage": "finite_top_level_headers_only",
        "project_root": Path(project_root).resolve().as_posix(), "policy_sha256": digest,
        "status": "unavailable" if error else "observed", "reason": error or "",
        "selected_files": len(selected), "omitted_files": omitted, "files": {},
        "runtime_execution": "not_established"}
    if error:
        return result

    for name, entry, reason in selected:
        value = None
        if reason is None:
            rows = parser_results.get((Path(project_root).resolve() / name).as_posix()) if isinstance(parser_results, dict) else None
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
                value = rows[0].get("declarationContractEvidence")
            if not isinstance(value, dict):
                reason = "parser_evidence_unavailable"
        if reason is None:
            counters = ("record_count", "omitted_statements", "omitted_name_nodes", "uninspected_module_bodies")
            if (not declaration_file_shape_valid(value, policy, digest)
                    or value["parser_input_sha256"] != entry["raw_sha256"] or value["size_bytes"] != entry["size_bytes"]):
                reason = "parser_identity_or_shape_mismatch"
        if reason is not None:
            result["files"][name] = {"status": "unavailable", "reason": reason}
        else:
            # Copy only admitted fixed fields; no implementation/symbol carrier.
            result["files"][name] = {key: value[key] for key in (
                "version", "coverage", "status", "policy_sha256", "parser_kind", "parser_version",
                "parser_input_sha256", "size_bytes", "offset_unit", "records", *counters, "reasons", "runtime_execution")}
        if result["files"][name]["status"] != "observed":
            result["status"] = "partial"
    if omitted is None or omitted or inventory.get("status") == "partial":
        result["status"] = "partial"
    return result

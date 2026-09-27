"""Bounded input and directory identities captured by Atlas, not a rescan."""

from __future__ import annotations

import hashlib
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

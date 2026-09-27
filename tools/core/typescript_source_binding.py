"""Verify collector-owned checked text before issuing Atlas lineage.

Syntax observations cover listed files, not project type safety. Semantic
collection records bounded compiler/config/declaration observations, not snapshot
authority, and remains unavailable to snapshot-based scoring until bound.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import hashlib
import json
import ntpath
import os
import re
from typing import Any

from tools.core.atlas_typescript_inputs import (
    POLICY_PATH as INPUT_POLICY_PATH, auxiliary_input_policy, resolution_input_policy, valid_directory_name,
)
from tools.core.json_io import load_json_object_strict, load_json_object_strict_cached
from tools.core.source_snapshot_integrity import snapshot_hash_algorithms


def _atlas_source_hash_matches(captured: Any, observed: Any) -> bool:
    """Compare actual parsed text using the centrally allowed Atlas digest width."""
    if not isinstance(captured, str) or not isinstance(observed, dict):
        return False
    algorithm = snapshot_hash_algorithms().get(str(len(captured)))
    value = observed.get(algorithm) if algorithm in {"md5", "sha256"} else None
    return bool(isinstance(value, str) and re.fullmatch(r"[0-9a-f]+", captured)
                and isinstance(value, str) and re.fullmatch(r"[0-9a-f]+", value)
                and len(value) == len(captured) and value == captured)


def _valid_atlas_source_hash(captured: Any) -> bool:
    if not isinstance(captured, str):
        return False
    algorithm = snapshot_hash_algorithms().get(str(len(captured)))
    return bool(algorithm in {"md5", "sha256"} and re.fullmatch(r"[0-9a-f]+", captured))


def capture_compiler_library_inputs(code_maps_dir: Path, run_id: str) -> dict[str, Any]:
    """Capture producer-owned installation inputs, never collector-selected files.

    This is a pre-invocation content image, not an atomic snapshot or an
    attestation of the JavaScript/Node code subsequently executed.
    """
    result: dict[str, Any] = {
        "version": "v1", "authority": "producer_preinvocation_installation_capture",
        "collector_run_id": run_id, "status": "unavailable", "files": {},
        "scope": "positive_compiler_library_reads_only",
        "loaded_code_attested": False, "snapshot_bound": False,
    }
    try:
        base = Path(code_maps_dir).resolve(strict=True)
        contract = load_json_object_strict(base / "config/analysis_snapshot_lineage_contract.json")
        policy = contract["artifacts"]["ts_diagnostics"]["compiler_library_inputs"]
        if not isinstance(policy, dict):
            raise ValueError("invalid_compiler_library_budget")
        for key in ("max_directory_entries", "max_files", "max_file_bytes", "max_module_bytes", "max_total_bytes"):
            if type(policy.get(key)) is not int or policy[key] < 1:
                raise ValueError("invalid_compiler_library_budget")
        provenance = load_json_object_strict(base / "config/third_party_distribution_contract.json")
        owners = [row for row in provenance["vendored_dependencies"]
                  if isinstance(row, dict) and row.get("id") == "typescript"]
        if len(owners) != 1 or not run_id:
            raise ValueError("compiler_installation_owner_unavailable")
        owner = owners[0]

        def owned_path(relative: str) -> Path:
            if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                    or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts):
                raise ValueError("unsafe_compiler_installation_path")
            return base / relative

        total_bytes = 0

        def read_bytes(candidate: Path, limit: int) -> bytes:
            nonlocal total_bytes
            # Installed package roots may be package-manager links. Once that
            # root is fixed, aliased entries cannot silently change its scope.
            if candidate.resolve(strict=True) != candidate.absolute():
                raise ValueError("aliased_compiler_input")
            if not candidate.is_file():
                raise ValueError("non_file_compiler_input")
            with candidate.open("rb") as stream:
                raw = stream.read(min(limit, policy["max_total_bytes"] - total_bytes) + 1)
            total_bytes += len(raw)
            if len(raw) > limit or total_bytes > policy["max_total_bytes"]:
                raise ValueError("compiler_input_budget_exceeded")
            return raw

        manifest_raw = read_bytes(owned_path(owner["manifest_path"]), policy["max_file_bytes"])
        lock_raw = read_bytes(owned_path(owner["lockfile_path"]), policy["max_file_bytes"])
        if hashlib.sha256(lock_raw).hexdigest() != owner["lockfile_sha256"]:
            raise ValueError("compiler_installation_lock_mismatch")
        marker = owned_path(owner["runtime_package_path"])
        package_root = marker.parent.resolve(strict=True)
        package_raw = read_bytes(package_root / marker.name, policy["max_file_bytes"])
        package = json.loads(package_raw.decode("utf-8-sig"))
        manifest = json.loads(manifest_raw.decode("utf-8-sig"))
        version = owner["locked_version"]
        if (not isinstance(package, dict) or not isinstance(manifest, dict)
                or not isinstance(manifest.get("dependencies"), dict)
                or package.get("name") != "typescript" or package.get("version") != version
                or manifest.get("dependencies", {}).get("typescript") != version):
            raise ValueError("compiler_installation_version_mismatch")
        main = package.get("main")
        if (not isinstance(main, str) or not main or "\\" in main or ":" in main
                or PurePosixPath(main).is_absolute() or ".." in PurePosixPath(main).parts):
            raise ValueError("unsafe_compiler_entry")
        module = package_root / main
        module_raw = read_bytes(module, policy["max_module_bytes"])
        library_root = module.parent
        names = []
        with os.scandir(library_root) as entries:
            for count, entry in enumerate(entries, 1):
                if count > policy["max_directory_entries"]:
                    raise ValueError("compiler_directory_budget_exceeded")
                if entry.name.startswith("lib") and entry.name.endswith(".d.ts"):
                    names.append(entry.name)
                    if len(names) > policy["max_files"]:
                        raise ValueError("compiler_library_budget_exceeded")
        if not names:
            raise ValueError("compiler_libraries_unavailable")
        files = {}
        for name in sorted(names):
            raw = read_bytes(library_root / name, policy["max_file_bytes"])
            text = raw.decode("utf-8-sig")  # Preserve newlines; TS strips one UTF-8 BOM.
            files[name] = {"sha256": hashlib.sha256(raw).hexdigest(),
                           "host_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        result.update(
            status="captured", library_root=library_root.as_posix(),
            installed_module=module.as_posix(), installed_module_sha256=hashlib.sha256(module_raw).hexdigest(),
            compiler_version=version, files=files,
            manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            lockfile_sha256=hashlib.sha256(lock_raw).hexdigest(),
            package_sha256=hashlib.sha256(package_raw).hexdigest(),
        )
        result["inventory_sha256"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        result["error"] = str(exc) or type(exc).__name__
    return result


def reconcile_compiler_library_inputs(payload: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    """Compare actual host reads with the separately held pre-invocation image.

    The caller passes its own inventory, not a child payload's authority claim.
    No path reported by the child is opened or resolved here.
    """
    result: dict[str, Any] = {
        "version": "v1", "status": "BLOCKED", "scope": "positive_compiler_library_reads_only",
        "producer": "tools.engines.react_frontier_intelligence",
        "collector_run_id": inventory.get("collector_run_id"),
        "inventory_sha256": inventory.get("inventory_sha256"),
        "inventory": inventory,
        "loaded_code_attested": False, "snapshot_bound": False, "projects": {},
    }
    if inventory.get("status") != "captured":
        result["errors"] = ["compiler_library_inventory_unavailable"]
        return result
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if (not isinstance(meta, dict) or not meta.get("collector_run_id")
            or meta["collector_run_id"] != inventory.get("collector_run_id")
            or payload.get("collector_status") != "OK"):
        result["errors"] = ["compiler_library_invocation_mismatch"]
        return result
    projects = payload.get("projects")
    if not isinstance(projects, dict) or not projects:
        result["errors"] = ["compiler_library_project_scope_unavailable"]
        return result
    errors: set[str] = set()
    root = Path(inventory["library_root"])
    for project, data in projects.items():
        context = data.get("semantic_context") if isinstance(data, dict) else None
        if (not isinstance(context, dict) or data.get("mode") != "semantic" or data.get("status") != "OK"
                or context.get("observations_complete") is not True):
            errors.add(f"{project}:compiler_library_observations_unavailable")
            continue
        compiler = context.get("compiler")
        if (not isinstance(compiler, dict)
                or compiler.get("module_hash_basis") != "installed_entry_bytes_read_before_analysis"
                or compiler.get("version") != inventory["compiler_version"]
                or any(compiler.get(key) != inventory[key] for key in
                       ("library_root", "installed_module", "installed_module_sha256"))):
            errors.add(f"{project}:compiler_installation_identity_mismatch")
            continue
        rows = context.get("observations")
        if not isinstance(rows, list):
            errors.add(f"{project}:compiler_library_observations_unavailable")
            continue
        matched: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                errors.add(f"{project}:compiler_library_observations_unavailable")
                continue
            candidate = row.get("path")
            in_root = isinstance(candidate, str) and Path(candidate).is_relative_to(root)
            if row.get("operation") != "readFile" or not (in_root or row.get("input_scope") == "compiler_library"):
                continue
            relative = Path(candidate).relative_to(root).as_posix() if in_root else ""
            expected = inventory["files"].get(relative)
            value = row.get("value")
            if (not in_root or not expected or ".." in Path(candidate).parts
                    or row.get("input_scope") != "compiler_library" or row.get("stage") != "compiler"
                    or row.get("allowed") is not True or row.get("access_status") != "present"
                    or row.get("error_code") or row.get("filtered_entries", 0) != 0
                    or not isinstance(value, dict) or value.get("text_sha256") != expected["host_text_sha256"]):
                errors.add(f"{project}:compiler_library_read_unbound")
            else:
                matched.add(relative)
        # An empty trace is not a positive library-read proof (e.g. noLib).
        if not matched:
            errors.add(f"{project}:compiler_library_reads_unavailable")
        result["projects"][project] = {"matched_files": sorted(matched)}
    result["errors"] = sorted(errors)
    result["status"] = "MATCHED_POSITIVE_READS" if not errors else "BLOCKED"
    return result


def _reserved_component(name: str) -> bool:
    if hasattr(ntpath, "isreserved"):
        return ntpath.isreserved(name)
    return PureWindowsPath(name).is_reserved()  # Python 3.11/3.12 compatibility.


def _resolution_snapshot(context: Any, project_atlas: Any) -> dict[str, Any] | None:
    """Validate the shared captured namespace before any lookup comparison."""
    if not isinstance(context, dict) or not isinstance(project_atlas, dict):
        return None
    inventory = project_atlas.get("typescript_resolution_inputs")
    metadata = project_atlas.get("project")
    if (not isinstance(inventory, dict) or not isinstance(metadata, dict)
            or inventory.get("version") != "v1"
            or inventory.get("scope") != "captured_directory_names_only"
            or inventory.get("path_semantics") not in {"windows_directory_names", "posix_directory_names"}
            or inventory.get("status") not in {"captured", "partial"}):
        return None
    roots = [metadata.get("root"), context.get("project_root"), inventory.get("project_root")]
    if any(not isinstance(value, str) or not value or not Path(value).is_absolute()
           or ".." in Path(value).parts for value in roots):
        return None
    root = Path(roots[0])
    if any(Path(value) != root for value in roots):
        return None
    directories = inventory.get("directories")
    policy = resolution_input_policy()
    if not isinstance(directories, dict) or len(directories) > policy["max_directories"]:
        return None
    members: dict[str, set[str]] = {}
    ambiguous_names: set[str] = set()
    entry_count = name_bytes = 0
    for relative, names in directories.items():
        if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                or relative.startswith("/") or ".." in PurePosixPath(relative).parts
                or str(PurePosixPath(relative)) != relative
                or not isinstance(names, list) or any(not valid_directory_name(name) for name in names)):
            return None
        entry_count += len(names)
        try:
            name_bytes += sum(len(name.encode("utf-8")) for name in names)
        except UnicodeError:
            return None
        if entry_count > policy["max_entries"] or name_bytes > policy["max_name_bytes"]:
            return None
        # Conservative on case-sensitive hosts too: a differently-cased existing
        # name prevents an absence claim rather than guessing filesystem policy.
        members[relative] = {name.casefold() for name in names}
        if any(not name.isascii() for name in names):
            ambiguous_names.add(relative)
    # Enumeration is not atomic. A later source/auxiliary read or child listing
    # may prove a name present that its earlier parent listing omitted. Reject
    # that internally inconsistent image instead of manufacturing absence.
    source_files = project_atlas.get("files", {})
    auxiliary = project_atlas.get("typescript_auxiliary_inputs", {})
    auxiliary_files = auxiliary.get("files", {}) if isinstance(auxiliary, dict) else None
    if not isinstance(source_files, dict) or not isinstance(auxiliary_files, dict):
        return None
    file_names = set(source_files) | set(auxiliary_files)
    if file_names.intersection(directories):
        return None  # An image cannot prove the same path both file and directory.
    for relative in (*directories, *source_files, *auxiliary_files):
        if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                or relative.startswith("/") or ".." in PurePosixPath(relative).parts
                or str(PurePosixPath(relative)) != relative):
            return None
        parent = PurePosixPath(".")
        for part in PurePosixPath(relative).parts:
            if parent.as_posix() in file_names:
                return None  # A captured file cannot also contain descendants.
            names = members.get(parent.as_posix())
            if names is None:
                break
            if part.casefold() not in names:
                return None
            parent /= part
    return {"root": root, "directories": directories, "members": members,
            "exact_members": {relative: set(names) for relative, names in directories.items()},
            "ambiguous_names": ambiguous_names, "path_semantics": inventory["path_semantics"]}


def _literal_lookup_parts(candidate: Any, root: Path) -> tuple[str, ...] | None:
    """Conservative lexical matching only; never resolve a live target path."""
    if (not isinstance(candidate, str) or not Path(candidate).is_absolute()
            or ".." in Path(candidate).parts):
        return None
    try:
        parts = Path(candidate).relative_to(root).parts
    except ValueError:
        return None
    if any(not valid_directory_name(part) or not part.isascii()
           or part.rstrip(" .") != part or _reserved_component(part)
           or any(ord(char) < 32 or char in '<>"|?*' for char in part) for part in parts):
        return None
    return parts


def _captured_file_content(relative: str, project_atlas: dict[str, Any], root: Path) -> bool:
    """A listed name is not file authority; require captured content identity."""
    source = project_atlas.get("files", {}).get(relative)
    if isinstance(source, dict) and _valid_atlas_source_hash(source.get("hash")):
        return True
    auxiliary = project_atlas.get("typescript_auxiliary_inputs", {})
    entry = auxiliary.get("files", {}).get(relative)
    return bool(auxiliary.get("version") == "v1" and auxiliary.get("status") in {"captured", "partial"}
                and auxiliary.get("scope") == "owned_walk_positive_inputs_only"
                and auxiliary.get("negative_resolution_authority") is False
                and auxiliary.get("project_root") == root.as_posix()
                and isinstance(entry, dict) and entry.get("status") == "ok"
                and isinstance(entry.get("host_text_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", entry["host_text_sha256"]))


def _captured_canonical_auxiliary_file(relative: str, project_atlas: dict[str, Any], root: Path) -> bool:
    """Use the existing auxiliary read's resolved path, never a later lookup."""
    auxiliary = project_atlas.get("typescript_auxiliary_inputs", {})
    if not isinstance(auxiliary, dict):
        return False
    files = auxiliary.get("files")
    entry = files.get(relative) if isinstance(files, dict) else None
    return bool(auxiliary.get("version") == "v1" and auxiliary.get("status") in {"captured", "partial"}
                and auxiliary.get("scope") == "owned_walk_positive_inputs_only"
                and auxiliary.get("negative_resolution_authority") is False
                and auxiliary.get("project_root") == root.as_posix()
                and isinstance(entry, dict) and entry.get("status") == "ok"
                and entry.get("canonical_file_path") is True
                and isinstance(entry.get("host_text_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", entry["host_text_sha256"]))


def _captured_canonical_source_file(relative: str, project_atlas: dict[str, Any]) -> bool:
    """Require the owned Atlas walk's source-path bit and captured text identity."""
    source = project_atlas.get("files", {}).get(relative)
    return bool(isinstance(source, dict)
                and source.get("language") in {"typescript", "javascript"}
                and source.get("canonical_file_path") is True
                and _valid_atlas_source_hash(source.get("hash")))


def _captured_directory_kinds(
    relative: str, snapshot: dict[str, Any], project_atlas: dict[str, Any],
) -> dict[str, Any] | None:
    """Project complete immediate kinds from existing captured authority only."""
    directories, root = snapshot["directories"], snapshot["root"]
    names = directories.get(relative)
    if names is None or len(names) != len({name.casefold() for name in names}):
        return None
    children, files = [], []
    for name in names:
        child = (PurePosixPath(relative) / name).as_posix()
        if _literal_lookup_parts((root / child).as_posix(), root) is None:
            return None
        if child in directories:
            children.append(name)
        elif not _captured_file_content(child, project_atlas, root):
            return None  # Excluded/aliased/unreadable name is not a known file.
        else:
            files.append(name)
    # TS returns sorted immediate basenames. ASCII-only names make Python's
    # ordering identical to its UTF-16 sort, without a second glob engine.
    def digest(entries):
        encoded = json.dumps(sorted(entries), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return {"entries": len(entries), "sha256": hashlib.sha256(encoded).hexdigest()}
    return {"entries": len(names), "files": digest(files), "directories": digest(children), "unsupported_entries": 0}


def _captured_child_directories(
    relative: str, snapshot: dict[str, Any], project_atlas: dict[str, Any],
) -> dict[str, Any] | None:
    kinds = _captured_directory_kinds(relative, snapshot, project_atlas)
    return kinds["directories"] if kinds is not None else None


def _canonical_captured_directory(candidate: Any, snapshot: dict[str, Any]) -> str | None:
    parts = _literal_lookup_parts(candidate, snapshot["root"])
    if parts is None or "." not in snapshot["directories"]:
        return None
    parent = PurePosixPath(".")
    for part in parts:
        if part not in snapshot["exact_members"].get(parent.as_posix(), set()):
            return None
        parent /= part
    relative = parent.as_posix()
    return relative if relative in snapshot["directories"] else None


def _recursive_directory_inputs_match(row, snapshot, project_atlas, projections) -> bool:
    """Compare observed glob inputs, not a second execution of glob semantics."""
    trace = row.get("recursive_inputs")
    if (not isinstance(trace, dict) or trace.get("version") != "v1"
            or trace.get("scope") != "actual_read_directory_calls" or trace.get("complete") is not True
            or type(trace.get("omitted_observations")) is not int or trace["omitted_observations"] != 0):
        return False
    inputs = trace.get("observations")
    if (not isinstance(inputs, list) or not inputs or type(trace.get("observed_calls")) is not int
            or trace["observed_calls"] != len(inputs)):
        return False
    value = row.get("value")
    if (not isinstance(value, dict) or type(value.get("entries")) is not int or value["entries"] < 0
            or not isinstance(value.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
        return False
    enumerated, resolved = set(), set()
    for observation in inputs:
        if not isinstance(observation, dict):
            return False
        relative = _canonical_captured_directory(observation.get("path"), snapshot)
        if relative is None:
            return False
        operation, value = observation.get("operation"), observation.get("value")
        if operation == "readdirSync":
            if relative not in projections:
                projections[relative] = _captured_directory_kinds(relative, snapshot, project_atlas)
            expected = projections[relative]
            if (expected is None or not isinstance(value, dict)
                    or any(type(value.get(key)) is not int for key in ("entries", "unsupported_entries"))
                    or any(not isinstance(value.get(key), dict) or type(value[key].get("entries")) is not int
                           for key in ("files", "directories"))
                    or value != expected):
                return False
            enumerated.add(relative)
        elif operation in {"realpathSync", "realpathSync.native"}:
            if _canonical_captured_directory(value, snapshot) != relative:
                return False
            resolved.add(relative)
        else:
            return False  # Link-following stat, legacy entries and other I/O are unknown.
    query_root = _canonical_captured_directory(row.get("path"), snapshot)
    return query_root in enumerated and enumerated == resolved


def semantic_positive_resolution_errors(context: Any, project_atlas: Any) -> list[str]:
    """Match positive existence and canonical-directory identity, not live state.

    Listed names alone do not prove entry type. File presence requires captured
    source/auxiliary content; directory presence requires its own canonical walk
    record. Lists require every immediate kind. Recursive queries additionally
    require complete actual enumeration/realpath inputs, not result digests alone.
    This compares input identities; it does not independently replay the compiler
    or establish native-project semantics, link binding or atomic stability.
    """
    unknown = ["positive_resolution_not_snapshot_bound"]
    observations = context.get("observations") if isinstance(context, dict) else None
    if (not isinstance(observations, list) or any(not isinstance(row, dict) for row in observations)
            or context.get("observations_complete") is False):
        return unknown
    operations = {"fileExists", "directoryExists", "getDirectories", "readDirectory", "realpath"}
    rows = [row for row in observations if isinstance(row, dict)
            and row.get("operation") in operations and row.get("access_status") != "missing"]
    if not rows:
        return []
    snapshot = _resolution_snapshot(context, project_atlas)
    if snapshot is None:
        return unknown
    root, directories = snapshot["root"], snapshot["directories"]
    exact_members = snapshot["exact_members"]
    empty_args = hashlib.sha256(b"[]").hexdigest()
    directory_results: dict[str, dict[str, Any] | None] = {}
    recursive_projections: dict[str, dict[str, Any] | None] = {}
    if any(row.get("operation") == "readDirectory" for row in rows):
        policy = load_json_object_strict_cached(INPUT_POLICY_PATH, label="Analysis snapshot lineage")
        limit = policy["artifacts"]["ts_diagnostics"]["semantic_context"]["max_observations"]
        observed_limit = context.get("observation_limit")
        if (type(limit) is not int or type(observed_limit) is not int
                or not 0 < observed_limit <= limit or context.get("observations_complete") is not True):
            return unknown
        units = len(observations)
        for row in observations:
            trace = row.get("recursive_inputs")
            if trace is not None:
                if not isinstance(trace, dict) or not isinstance(trace.get("observations"), list):
                    return unknown
                units += len(trace["observations"])
            if units > observed_limit:
                return unknown
    for row in rows:
        if (row.get("allowed") is not True or row.get("input_scope") != "workspace"
                or row.get("stage") not in {"config", "compiler"}
                or row.get("access_status") != "present"
                or row.get("error_code") or row.get("filtered_entries", 0) != 0
                or row.get("io_failure") is not None):
            return unknown
        if row.get("operation") == "readDirectory":
            digest = row.get("arguments_sha256")
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or not _recursive_directory_inputs_match(row, snapshot, project_atlas, recursive_projections)):
                return unknown
            continue
        if row.get("arguments_sha256") != empty_args:
            return unknown
        parts = _literal_lookup_parts(row.get("path"), root)
        if parts is None or "." not in directories:
            return unknown
        parent = PurePosixPath(".")
        for part in parts:
            # Exact spelling and captured canonical ancestors, not casefold or
            # display-name aliases, establish this restricted identity.
            if part not in exact_members.get(parent.as_posix(), set()):
                return unknown
            parent /= part
        relative = parent.as_posix()
        operation, value = row["operation"], row.get("value")
        if operation == "directoryExists" and value is True and relative in directories:
            continue
        if operation == "getDirectories" and isinstance(value, dict):
            if relative not in directory_results:
                directory_results[relative] = _captured_child_directories(relative, snapshot, project_atlas)
            expected = directory_results[relative]
            if (expected is not None and type(value.get("entries")) is int
                    and value.get("entries") == expected["entries"]
                    and value.get("sha256") == expected["sha256"]):
                continue
        if operation == "realpath" and relative in directories:
            resolved_parts = _literal_lookup_parts(value, root)
            if resolved_parts == parts:
                continue  # Agreement with captured canonical directory, not an I/O success claim.
        if operation == "realpath" and relative not in directories:
            resolved_parts = _literal_lookup_parts(value, root)
            if resolved_parts == parts and (
                _captured_canonical_auxiliary_file(relative, project_atlas, root)
                or _captured_canonical_source_file(relative, project_atlas)
            ):
                continue  # Captured literal file identity, not atomic link stability.
        if operation == "fileExists" and value is True and relative not in directories:
            if _captured_file_content(relative, project_atlas, root):
                continue
        return unknown
    return []


def semantic_negative_resolution_errors(context: Any, project_atlas: Any) -> list[str]:
    """Reconcile missing probes with enumeration-time names, never live paths."""
    unknown = ["negative_resolution_not_snapshot_bound"]
    observations = context.get("observations") if isinstance(context, dict) else None
    if not isinstance(observations, list) or any(not isinstance(row, dict) for row in observations):
        return unknown
    missing = [row for row in observations if isinstance(row, dict) and row.get("access_status") == "missing"]
    if not missing:
        return []
    snapshot = _resolution_snapshot(context, project_atlas)
    if snapshot is None:
        return unknown
    root, members = snapshot["root"], snapshot["members"]
    ambiguous_names = snapshot["ambiguous_names"]
    for row in missing:
        operation, value, candidate = row.get("operation"), row.get("value"), row.get("path")
        if (row.get("allowed") is not True or row.get("input_scope") != "workspace"
                or row.get("error_code") or row.get("filtered_entries", 0) != 0
                or not ((operation in {"fileExists", "directoryExists"} and value is False)
                        or (operation == "readFile" and value is None))
                or not isinstance(candidate, str) or not Path(candidate).is_absolute()
                or ".." in Path(candidate).parts):
            return unknown
        parts = _literal_lookup_parts(candidate, root)
        if parts is None:
            return unknown
        parent = PurePosixPath(".")
        for part in parts:
            names = members.get(parent.as_posix())
            if names is None:
                return unknown  # Excluded, unreadable, aliased or omitted directory.
            if part.casefold() not in names:
                if parent.as_posix() in ambiguous_names:
                    return unknown  # Filesystem Unicode equivalence is not captured.
                pieces = part.split(".")
                if (snapshot["path_semantics"] == "windows_directory_names" and names
                        and len(pieces) <= 2 and len(pieces[0]) <= 8
                        and (len(pieces) == 1 or len(pieces[1]) <= 3)):
                    return unknown  # A directory listing does not enumerate 8.3 aliases.
                break  # This exact directory listing proves the next name absent.
            parent /= part
        else:
            return unknown  # Every queried component existed in the Atlas listing.
    return []


def semantic_auxiliary_input_errors(context: Any, project_atlas: Any) -> list[str]:
    """Compare positive host reads with ingestion identity, never infer absence."""
    if not isinstance(context, dict) or not isinstance(project_atlas, dict):
        return ["auxiliary_input_inventory_unavailable"]
    inventory = project_atlas.get("typescript_auxiliary_inputs")
    metadata = project_atlas.get("project") or {}
    root = metadata.get("root") if isinstance(metadata, dict) else None
    if not root or not isinstance(inventory, dict) or inventory.get("version") != "v1":
        return ["auxiliary_input_inventory_unavailable"]
    roots = [root, context.get("project_root"), inventory.get("project_root")]
    if any(not isinstance(value, str) or not value or not Path(value).is_absolute()
           or ".." in Path(value).parts for value in roots):
        return ["auxiliary_input_root_mismatch"]
    root = Path(root)
    if any(root != Path(value) for value in roots):
        return ["auxiliary_input_root_mismatch"]
    if (inventory.get("status") not in {"captured", "partial"}
            or inventory.get("scope") != "owned_walk_positive_inputs_only"
            or inventory.get("negative_resolution_authority") is not False):
        return ["auxiliary_input_inventory_unavailable"]
    observations = context.get("observations")
    entries = inventory.get("files")
    if not isinstance(observations, list) or not isinstance(entries, dict):
        return ["auxiliary_input_inventory_unavailable"]
    suffixes = tuple(auxiliary_input_policy()["file_suffixes"])
    errors: set[str] = set()
    for row in observations:
        if not isinstance(row, dict) or row.get("operation") != "readFile":
            continue
        value = row.get("value")
        candidate = row.get("path")
        if not isinstance(value, dict) or not isinstance(candidate, str) or not candidate.lower().endswith(suffixes):
            continue
        if row.get("input_scope") == "compiler_library":
            # A collector scope tag cannot authorize itself. Keep toolchain
            # observations separate from project inputs and explicitly unbound.
            errors.add("compiler_library_not_snapshot_bound")
            continue
        # Lexical normalization only: evidence consumers must not consult live
        # realpaths, which may have changed since the actual compiler read.
        candidate_path = Path(candidate)
        if not candidate_path.is_absolute() or ".." in candidate_path.parts:
            errors.add("unsafe_auxiliary_input_path")
            continue
        try:
            relative = candidate_path.relative_to(root).as_posix()
        except ValueError:
            errors.add("auxiliary_input_outside_project")
            continue  # Includes compiler libraries; their authority is separate.
        entry = entries.get(relative)
        digest = value.get("text_sha256")
        if (row.get("allowed") is not True or not isinstance(entry, dict)
                or entry.get("status") != "ok" or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or entry.get("host_text_sha256") != digest):
            errors.add(f"auxiliary_input_unbound:{relative}")
    return sorted(errors)


def semantic_workspace_read_errors(context: Any, project_atlas: Any) -> list[str]:
    """Bind positive compiler/config host text reads to captured Atlas content.

    This is input correspondence only. It does not prove file realpaths,
    compiler option semantics, project references or one atomic filesystem
    image, and cannot by itself authorize a semantic lineage receipt.
    """
    unknown = ["workspace_read_inventory_unavailable"]
    if not isinstance(context, dict) or not isinstance(project_atlas, dict):
        return unknown
    observations = context.get("observations")
    metadata = project_atlas.get("project")
    root_text = metadata.get("root") if isinstance(metadata, dict) else None
    observed_root = context.get("project_root")
    if (context.get("observations_complete") is not True
            or not isinstance(observations, list)
            or any(not isinstance(row, dict) for row in observations)
            or not isinstance(root_text, str) or not root_text
            or not Path(root_text).is_absolute() or ".." in Path(root_text).parts
            or not isinstance(observed_root, str) or not observed_root
            or Path(observed_root) != Path(root_text)):
        return unknown
    sources = project_atlas.get("files")
    auxiliary = project_atlas.get("typescript_auxiliary_inputs")
    auxiliary_files = auxiliary.get("files") if isinstance(auxiliary, dict) else {}
    if not isinstance(sources, dict) or not isinstance(auxiliary_files, dict):
        return unknown
    root = Path(root_text)
    errors: set[str] = set()
    positive_reads = 0
    for row in observations:
        if row.get("operation") != "readFile" or row.get("access_status") == "missing":
            continue
        if row.get("input_scope") == "compiler_library":
            continue  # Separately reconciled with the producer-owned installation image.
        positive_reads += 1
        parts = _literal_lookup_parts(row.get("path"), root)
        if parts is None or not parts:
            errors.add("workspace_read_path_unbound")
            continue
        relative = PurePosixPath(*parts).as_posix()
        value = row.get("value")
        digest = value.get("text_sha256") if isinstance(value, dict) else None
        if (row.get("input_scope") != "workspace" or row.get("allowed") is not True
                or row.get("stage") not in {"config", "compiler"}
                or row.get("access_status") != "present" or row.get("error_code")
                or row.get("filtered_entries", 0) != 0 or row.get("io_failure") is not None
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            errors.add(f"workspace_read_unbound:{relative}")
            continue
        source = sources.get(relative)
        captured = source.get("hash") if isinstance(source, dict) else None
        entry = auxiliary_files.get(relative)
        if entry is not None:
            if (auxiliary.get("version") != "v1"
                    or auxiliary.get("status") not in {"captured", "partial"}
                    or auxiliary.get("scope") != "owned_walk_positive_inputs_only"
                    or auxiliary.get("negative_resolution_authority") is not False
                    or auxiliary.get("project_root") != root.as_posix()
                    or not isinstance(entry, dict) or entry.get("status") != "ok"):
                errors.add(f"workspace_read_unbound:{relative}")
                continue
            auxiliary_digest = entry.get("host_text_sha256")
            if (not isinstance(auxiliary_digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", auxiliary_digest)
                    or auxiliary_digest != digest):
                errors.add(f"workspace_read_content_mismatch:{relative}")
        if source is not None:
            if not _atlas_source_hash_matches(captured, {
                "md5": value.get("text_md5"), "sha256": digest,
            }):
                errors.add(f"workspace_read_content_mismatch:{relative}")
        elif entry is None:
            errors.add(f"workspace_read_unbound:{relative}")
    if not positive_reads:
        errors.add("workspace_positive_reads_unavailable")
    return sorted(errors)


def semantic_config_root_read_errors(data: Any, project_atlas: Any) -> list[str]:
    """Require the contract-selected root config's actual read and Atlas text.

    This checks one input observation, not the complete extends graph, parsed
    option semantics or the native compiler profile.
    """
    unknown = ["semantic_config_root_read_unbound"]
    if not isinstance(data, dict) or not isinstance(project_atlas, dict):
        return unknown
    metadata = project_atlas.get("project")
    root_text = metadata.get("root") if isinstance(metadata, dict) else None
    relative = data.get("tsconfig")
    context = data.get("semantic_context")
    try:
        policy = load_json_object_strict_cached(INPUT_POLICY_PATH, label="Analysis snapshot lineage")
        root_config_file = policy["artifacts"]["ts_diagnostics"]["root_config_filename"]
    except (OSError, ValueError, KeyError, TypeError):
        return unknown
    if (not isinstance(root_text, str) or not Path(root_text).is_absolute()
            or not isinstance(relative, str) or not relative
            or not isinstance(root_config_file, str) or relative != root_config_file
            or relative.startswith("/") or "\\" in relative or ":" in relative
            or "/" in relative
            or ".." in PurePosixPath(relative).parts
            or str(PurePosixPath(relative)) != relative
            or PurePosixPath(relative).suffix.lower() not in {".json", ".jsonc"}
            or not isinstance(context, dict)
            or context.get("observations_complete") is not True
            or not isinstance(context.get("observations"), list)
            or any(not isinstance(row, dict) for row in context["observations"])
            or not isinstance(data.get("project_root"), str)
            or Path(data["project_root"]) != Path(root_text)
            or not isinstance(context.get("project_root"), str)
            or Path(context["project_root"]) != Path(root_text)):
        return unknown
    root = Path(root_text)
    expected = (root / PurePosixPath(relative)).as_posix()
    if _literal_lookup_parts(expected, root) != PurePosixPath(relative).parts:
        return unknown
    reads = [row for row in context["observations"]
             if row.get("stage") == "config" and row.get("operation") == "readFile"
             and row.get("path") == expected]
    if len(reads) != 1:
        return unknown
    scoped = {"project_root": root_text, "observations_complete": True, "observations": reads}
    if (semantic_auxiliary_input_errors(scoped, project_atlas)
            or semantic_workspace_read_errors(scoped, project_atlas)):
        return unknown
    return []


def semantic_diagnostic_source_errors(data: Any, project_atlas: Any) -> list[str]:
    """Compare emitted diagnostic and selected-root SourceFile text to Atlas.

    Cross-check the selected-root context digest against that same manifest;
    neither producer field alone can establish an independent config snapshot.
    The collector's SourceFile text and positive host reads are distinct inputs.
    Even a match here cannot establish the whole Program or one atomic image.
    """
    unavailable = ["semantic_checked_manifest_unavailable"]
    if not isinstance(data, dict) or not isinstance(project_atlas, dict):
        return unavailable
    manifest = data.get("checked_source_manifest")
    metadata = project_atlas.get("project")
    root = metadata.get("root") if isinstance(metadata, dict) else None
    observed_root = data.get("project_root")
    files = manifest.get("files") if isinstance(manifest, dict) else None
    selected_roots = manifest.get("selected_root_files") if isinstance(manifest, dict) else None
    diagnostics = data.get("diagnostics")
    summary = data.get("summary")
    atlas_files = project_atlas.get("files")
    if (data.get("status") != "OK" or data.get("mode") != "semantic"
            or not isinstance(manifest, dict) or manifest.get("version") != "v1"
            or manifest.get("hash_basis") != "sha256_utf8_source_text"
            or manifest.get("scope") != "semantic_context_not_snapshot_bound"
            or not isinstance(files, list) or not isinstance(selected_roots, list)
            or not isinstance(diagnostics, list) or not isinstance(summary, dict)
            or any(not isinstance(name, str) for name in selected_roots)
            or len(selected_roots) != len(set(selected_roots))
            or summary.get("root_files_checked") != len(selected_roots)
            or not isinstance(atlas_files, dict)
            or not isinstance(root, str) or not root or not Path(root).is_absolute()
            or ".." in Path(root).parts
            or not isinstance(observed_root, str) or Path(observed_root) != Path(root)
            or type(manifest.get("source_files_total")) is not int
            or type(manifest.get("omitted_files")) is not int
            or manifest["omitted_files"] < 0
            or manifest["source_files_total"] != len(files) + manifest["omitted_files"]
            or any(not isinstance(row, dict) for row in files + diagnostics)
            or any(not isinstance(row.get("file"), str) for row in diagnostics)):
        return unavailable
    relevant = {name: "root" for name in selected_roots}
    relevant.update({row["file"]: "diagnostic" for row in diagnostics if row.get("file")})
    errors: set[str] = set()
    context = data.get("semantic_context")
    if not isinstance(context, dict):
        errors.add("semantic_selected_roots_context_unavailable")
    else:
        selected_count = context.get("selected_roots")
        configured_count = context.get("configured_roots")
        selected_digest = context.get("selected_roots_sha256")
        configured_digest = context.get("configured_roots_sha256")
        if (type(selected_count) is not int or type(configured_count) is not int
                or selected_count < 0 or configured_count < 0
                or not isinstance(selected_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", selected_digest)
                or not isinstance(configured_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", configured_digest)):
            errors.add("semantic_selected_roots_context_unavailable")
        elif all(name and "\\" not in name and ":" not in name and not name.startswith("/")
                 and ".." not in PurePosixPath(name).parts
                 and str(PurePosixPath(name)) == name for name in selected_roots):
            absolute_roots = [(Path(root) / PurePosixPath(name)).as_posix() for name in selected_roots]
            digest = hashlib.sha256(json.dumps(
                absolute_roots, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            if (selected_count != len(selected_roots) or configured_count < selected_count
                    or selected_digest != digest):
                errors.add("semantic_selected_roots_context_mismatch")
            if configured_count == selected_count and configured_digest != selected_digest:
                errors.add("semantic_configured_roots_context_mismatch")
    if not relevant:
        return sorted(errors)  # Empty inventory still cannot authorize lineage.
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in files:
        name = row.get("file")
        if isinstance(name, str) and name in relevant:
            indexed.setdefault(name, []).append(row)
    for name, kind in relevant.items():
        if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                or name.startswith("/") or ".." in PurePosixPath(name).parts
                or str(PurePosixPath(name)) != name):
            errors.add(f"semantic_{kind}_file_path_unbound")
            continue
        matches = indexed.get(name, [])
        if len(matches) != 1:
            errors.add(f"semantic_{kind}_manifest_{'ambiguous' if matches else 'missing'}:{name}")
            continue
        atlas_row = atlas_files.get(name)
        atlas_digest = atlas_row.get("hash") if isinstance(atlas_row, dict) else None
        if not _valid_atlas_source_hash(atlas_digest):
            errors.add(f"semantic_{kind}_source_unbound:{name}")
        elif not _atlas_source_hash_matches(atlas_digest, matches[0]):
            errors.add(f"semantic_{kind}_source_mismatch:{name}")
    return sorted(errors)


def checked_source_errors(payload: Any, atlas: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict) or payload.get("collector_status") != "OK":
        return ["typescript_collector_unavailable"]
    if not isinstance(atlas, dict):
        return ["typescript_atlas_unavailable"]
    projects = payload.get("projects")
    meta = payload.get("meta")
    scope = meta.get("execution_scope") if isinstance(meta, dict) else None
    analyzed = scope.get("analyzed_projects") if isinstance(scope, dict) else None
    requested = scope.get("requested_projects") if isinstance(scope, dict) else None
    if (
        not isinstance(projects, dict) or not projects
        or not isinstance(analyzed, list) or not all(isinstance(p, str) for p in analyzed)
        or set(analyzed) != set(projects) or len(analyzed) != len(projects)
        or not isinstance(requested, list) or not all(isinstance(p, str) for p in requested)
        or {p.upper() for p in requested} != {p.upper() for p in projects}
        or scope.get("unavailable_requested_projects")
    ):
        return ["typescript_project_scope_incomplete"]
    errors: list[str] = []
    for project, data in projects.items():
        prefix = f"typescript:{project}:"
        if not isinstance(data, dict):
            errors.append(prefix + "invalid_project")
            continue
        manifest = data.get("checked_source_manifest")
        if data.get("mode") == "semantic":
            errors.append(prefix + "semantic_context_not_snapshot_bound")
            context = data.get("semantic_context")
            if not isinstance(context, dict) or context.get("observations_complete") is not True:
                errors.append(prefix + "semantic_context_observation_incomplete")
            # These producer observations can prove a profile difference, never
            # prove that the native options/references match an Atlas snapshot.
            options_hash = context.get("requested_options_sha256") if isinstance(context, dict) else None
            effective_options = context.get("effective_options") if isinstance(context, dict) else None
            overrides = context.get("overridden_options") if isinstance(context, dict) else None
            if (not isinstance(options_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", options_hash)
                    or not isinstance(effective_options, dict)
                    or not isinstance(overrides, list)
                    or any(not isinstance(option, str) or not option for option in overrides)
                    or overrides != sorted(set(overrides))):
                errors.append(prefix + "semantic_compiler_profile_unavailable")
            elif overrides:
                errors.append(prefix + "semantic_compiler_profile_overridden")
            reference_count = context.get("project_references") if isinstance(context, dict) else None
            reference_hash = context.get("project_references_sha256") if isinstance(context, dict) else None
            if (type(reference_count) is not int or reference_count < 0
                    or not isinstance(reference_hash, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", reference_hash)):
                errors.append(prefix + "semantic_project_references_unavailable")
            elif reference_count:
                errors.append(prefix + "semantic_project_references_unloaded")
            elif reference_hash != hashlib.sha256(b"[]").hexdigest():
                errors.append(prefix + "semantic_project_references_context_mismatch")
            observations = context.get("observations") if isinstance(context, dict) else None
            for row in observations if isinstance(observations, list) else []:
                if not isinstance(row, dict):
                    continue
                if row.get("operation") == "readFile" and row.get("input_scope") == "compiler_library":
                    errors.append(prefix + "compiler_library_not_snapshot_bound")
                status = row.get("access_status")
                if status in {"unavailable", "changed_during_probe"}:
                    errors.append(prefix + "semantic_host_access_unavailable")
                elif status == "outside_boundary":
                    errors.append(prefix + "semantic_host_boundary_denied")
            errors.extend(prefix + issue for issue in semantic_negative_resolution_errors(context, atlas.get(project)))
            errors.extend(prefix + issue for issue in semantic_positive_resolution_errors(context, atlas.get(project)))
            errors.extend(prefix + issue for issue in semantic_auxiliary_input_errors(context, atlas.get(project)))
            errors.extend(prefix + issue for issue in semantic_workspace_read_errors(context, atlas.get(project)))
            errors.extend(prefix + issue for issue in semantic_config_root_read_errors(data, atlas.get(project)))
            errors.extend(prefix + issue for issue in semantic_diagnostic_source_errors(data, atlas.get(project)))
            continue  # Complete observations cannot authorize their own snapshot binding.
        if (
            data.get("status") != "OK_SYNTAX_ONLY" or data.get("mode") != "syntax"
            or not isinstance(manifest, dict)
            or manifest.get("version") != "v1"
            or manifest.get("hash_basis") != "sha256_utf8_source_text"
            or manifest.get("scope") != "checked_syntax_files_only"
            or manifest.get("complete") is not True
        ):
            errors.append(prefix + "checked_scope_unavailable")
            continue
        project_atlas = atlas.get(project)
        project_atlas = project_atlas if isinstance(project_atlas, dict) else {}
        project_meta = project_atlas.get("project")
        root = project_meta.get("root") if isinstance(project_meta, dict) else None
        observed_root = data.get("project_root")
        root_matches = False
        if isinstance(root, str) and root and isinstance(observed_root, str) and observed_root:
            try:
                root_matches = Path(root).resolve() == Path(observed_root).resolve()
            except (OSError, ValueError):
                errors.append(prefix + "project_root_unavailable")
        if not root_matches:
            errors.append(prefix + "project_root_mismatch")
        files = manifest.get("files")
        summary = data.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        if (
            not isinstance(files, list) or not files
            or summary.get("root_files_checked") != len(files)
            or summary.get("root_files_total") != len(files)
        ):
            errors.append(prefix + "checked_inventory_incomplete")
            continue
        seen: set[str] = set()
        atlas_files = project_atlas.get("files")
        atlas_files = atlas_files if isinstance(atlas_files, dict) else {}
        for row in files:
            if not isinstance(row, dict):
                errors.append(prefix + "invalid_checked_file")
                continue
            file = str(row.get("file") or "")
            parts = PurePosixPath(file).parts
            if (
                not file or "\\" in file or ":" in file
                or file.startswith("/") or ".." in parts
                or str(PurePosixPath(file)) != file or file in seen
            ):
                errors.append(prefix + "unsafe_or_duplicate_checked_file")
                continue
            seen.add(file)
            entry = atlas_files.get(file) or {}
            if (
                not isinstance(entry, dict)
                or not _atlas_source_hash_matches(entry.get("hash"), row)
            ):
                errors.append(prefix + f"source_content_mismatch:{file}")
        diagnostics = data.get("diagnostics")
        if not isinstance(diagnostics, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("file"), str) or row["file"] not in seen
            for row in diagnostics
        ):
            errors.append(prefix + "diagnostic_outside_checked_inventory")
    return sorted(set(errors))

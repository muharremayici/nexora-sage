"""Bounded existing-log identity ingestion; no target command is executed."""
from __future__ import annotations

import hashlib
import io
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.core.artifact_validator import validate_against_schema, ensure_valid_payload
from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR
from tools.core.json_syntax import loads_json_strict
from tools.core.strict_contract_cache import load_json_object_strict_cached
from tools.core.target_repository_trust import classify_target_path, target_trust_projection

CONTRACT_PATH = CONFIG_DIR / "native_run_log_ingestion_contract.json"


class _InputError(ValueError):
    """Stable, content-free error code; never echo untrusted input text."""


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _identity(value: Any) -> str:
    from tools.core.atlas_integrity import payload_sha256
    return payload_sha256(value)


def _contained(root: Path, relative: str) -> Path:
    # Reject absolute paths, parent traversal and Windows ADS, on all hosts.
    normalized = relative.replace("\\", "/")
    if (normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/")
            or any(ord(char) < 32 for char in normalized)):
        raise _InputError("path_not_relative_or_contained")
    try:
        result = classify_target_path(root, normalized)
    except (ValueError, OSError, RuntimeError) as exc:
        raise _InputError("path_not_relative_or_contained") from exc
    if not result["contained"]:
        raise _InputError("path_not_relative_or_contained")
    return Path(result["resolved_path"])


def _read(path: Path, limit: int, budget: list[int]) -> bytes:
    # A bounded read also bounds a concurrently grown ordinary file.
    if not path.is_file():
        raise _InputError("file_unavailable")
    try:
        with path.open("rb") as stream:
            content = stream.read(min(limit, budget[0]) + 1)
    except OSError as exc:
        raise _InputError("file_unavailable") from exc
    if len(content) > limit or len(content) > budget[0]:
        raise _InputError("byte_budget_exceeded")
    budget[0] -= len(content)
    return content


def _contract() -> dict[str, Any]:
    contract = load_json_object_strict_cached(CONTRACT_PATH, label="native log ingestion contract")
    limits = contract["limits"]
    keys = ("manifest_bytes", "stream_bytes", "input_bytes", "total_bytes", "input_files", "argv_items")
    if contract["meta"]["version"] != "v1" or any(type(limits[k]) is not int or limits[k] <= 0 for k in keys):
        raise ValueError("Invalid native log ingestion contract")
    normalization = contract["normalization"]
    if any(type(value) is not int or value <= 0 for value in normalization["limits"].values()):
        raise ValueError("Invalid native normalization budget")
    return contract


def _mixed_import_fields(line: str) -> tuple[str, str, str] | None:
    # Match only the reporter's finite grammar, not arbitrary warning/error words.
    if not line.startswith("(!) "):
        return None
    body, ending = line[4:], ", dynamic import will not move module into another chunk."
    if not body.endswith(ending) or any(ord(char) < 32 for char in body):
        return None
    module, dynamic_separator, rest = body[:-len(ending)].partition(" is dynamically imported by ")
    dynamic, static_separator, static = rest.partition(" but also statically imported by ")
    if dynamic_separator and static_separator and module.strip() and dynamic.strip() and static.strip():
        return module, dynamic, static
    return None


def _source_identifier_key(identifier: str) -> str | None:
    # String-only comparison: never resolve, open or follow a reported log path.
    if "?" in identifier or "#" in identifier:
        return None
    return os.path.normcase(identifier).replace("\\", "/") if os.name == "nt" else identifier


def _source_correspondence(
    line: str, fields: tuple[str, str, str], receipt: dict[str, Any], declared_sources: dict[str, int],
) -> dict[str, Any]:
    result = {"status": "AMBIGUOUS_FORMAT", "roles": None,
              "input_observation_sha256": receipt["identities"]["input_observation_sha256"]}
    # Reporter identifiers and joined lists are unescaped. Never guess a split.
    if any(line.count(separator) != 1 for separator in
           (" is dynamically imported by ", " but also statically imported by ")):
        return result
    roles = {}
    for role, identifier in zip(("module", "dynamic_importers", "static_importers"), fields):
        if role != "module" and ", " in identifier:
            roles[role] = {"status": "AMBIGUOUS_LIST", "source_input_index": None}
            continue
        index = declared_sources.get(_source_identifier_key(identifier))
        roles[role] = {"status": "DECLARED_SOURCE_INPUT" if index is not None else "UNRESOLVED_IDENTIFIER",
                       "source_input_index": index}
    result.update(status="ASSESSED", roles=roles)
    return result


def _observe_native_warning(
    receipt: dict[str, Any], captured: dict[str, bytes], tool: dict[str, str] | None,
    requested: str, policy: dict[str, Any], declared_sources: dict[str, int],
) -> dict[str, Any]:
    """Observe one declared warning family from the exact already hashed bytes."""
    selected = policy["formats"].get(requested)
    result = {
        "format_id": requested if selected else None, "status": "UNAVAILABLE",
        "reason_code": "input_not_ingested", "coverage": "single_warning_family_only",
        "parser_contract_sha256": _identity(policy),
        "run_sha256": receipt["identities"]["run_sha256"],
        "streams_sha256": receipt["identities"]["streams_sha256"],
        "items": [], "unmatched_nonempty_lines": None, "performance_impact": "not_measured",
    }
    if receipt["summary"]["status"] != "INGESTED":
        return result
    if not selected or tool != selected["tool"] or selected["parser"] != "vite_mixed_import_console_v1":
        result.update(status="UNSUPPORTED_FORMAT", reason_code="format_or_tool_not_supported")
        return result
    limits = policy["limits"]
    items: dict[str, dict[str, Any]] = {}
    unmatched = total_lines = occurrences = 0
    sgr = re.compile(r"\x1b\[[0-9;]*m")
    try:
        # Decode both complete captures before emitting any observation.
        decoded = {stream: content.decode("utf-8") for stream, content in captured.items()}
    except UnicodeError:
        result.update(status="UNREADABLE_FORMAT", reason_code="invalid_utf8")
        return result
    for stream, text in decoded.items():
        for line_number, raw_line in enumerate(io.StringIO(text, newline=None), 1):
            total_lines += 1
            if total_lines > limits["lines"] or len(raw_line) > limits["line_characters"]:
                result.update(status="LIMIT_EXCEEDED", reason_code="normalization_budget_exceeded")
                return result
            line = sgr.sub("", raw_line.rstrip("\r\n"))
            fields = _mixed_import_fields(line)
            if fields is None:
                unmatched += bool(line.strip())
                continue
            occurrences += 1
            if occurrences > limits["occurrences"]:
                result.update(status="LIMIT_EXCEEDED", reason_code="normalization_budget_exceeded")
                return result
            digest = _digest(line.encode("utf-8"))
            item = items.setdefault(digest, {
                "code": selected["code"], "reported_severity": selected["reported_severity"],
                "message_sha256": digest, "occurrences": [],
                "source_correspondence": _source_correspondence(line, fields, receipt, declared_sources),
            })
            item["occurrences"].append({"stream": stream, "line": line_number})
    result.update(status="OBSERVED" if items else "NO_SUPPORTED_OBSERVATIONS",
                  reason_code="supported_family_observed" if items else "supported_family_not_seen",
                  items=list(items.values()), unmatched_nonempty_lines=unmatched)
    return result


def ingest_native_run_logs(
    *, target_root: Path, bundle_root: Path, manifest_path: str,
    trust_class: str | None = None, diagnostic_format: str | None = None,
    atlas: dict[str, Any] | None = None, atlas_commit: dict[str, Any] | None = None,
    atlas_project: str | None = None,
) -> dict[str, Any]:
    """Hash existing files and report declared identities, not executed proof."""
    contract = _contract()
    limits = contract["limits"]
    target = Path(target_root).resolve()
    bundle = Path(bundle_root).resolve()
    empty_stream = {"sha256": None, "declared_sha256": None, "byte_count": None, "status": "UNAVAILABLE"}
    receipt = {
        "meta": {"kind": "native_run_log_receipt", "version": "v1"},
        "summary": {"status": "REJECTED", "reason_codes": [], "reported_exit_code": None,
                    "reported_process_outcome": "unavailable", "stream_integrity": "UNAVAILABLE",
                    "current_input_correspondence": "UNAVAILABLE"},
        "identities": {key: None for key in ("manifest_sha256", "run_sha256", "target_root_sha256",
                                            "command_sha256", "tool_sha256", "input_observation_sha256", "streams_sha256")},
        "streams": {"stdout": dict(empty_stream), "stderr": dict(empty_stream)},
        "inputs": {"source": [], "config": []},
        "authority": dict(contract["authority"]),
        "claim_boundary": contract["claim_boundary"],
    }
    summary = receipt["summary"]
    budget = [limits["total_bytes"]]
    captured: dict[str, bytes] = {}
    declared_sources: dict[str, int] = {}
    source_bytes: dict[int, bytes] = {}
    tool = None
    try:
        admission = target_trust_projection(trust_class)["analysis_admission"]
        if admission not in ("allowed", "allowed_with_explicit_boundaries"):
            raise _InputError("trust_class_not_admitted")
        if not target.is_dir() or not bundle.is_dir():
            raise _InputError("root_unavailable")
        manifest_bytes = _read(_contained(bundle, manifest_path), limits["manifest_bytes"], budget)
        try:
            manifest = loads_json_strict(manifest_bytes.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise _InputError("manifest_invalid") from exc
        if validate_against_schema(CODE_MAPS_DIR / contract["manifest_schema"], "native_run_manifest", manifest):
            raise _InputError("manifest_invalid")
        tool = manifest["tool"]
        try:
            declared_root = Path(manifest["target_root"])
            if not declared_root.is_absolute() or declared_root.resolve() != target:
                raise _InputError("target_identity_mismatch")
        except (ValueError, OSError, RuntimeError) as exc:
            raise _InputError("target_identity_mismatch") from exc
        cwd = _contained(target, manifest["cwd"])
        if not cwd.is_dir():
            raise _InputError("cwd_unavailable")
        try:
            start, end = [datetime.fromisoformat(manifest[key].replace("Z", "+00:00"))
                          for key in ("started_at", "finished_at")]
            if start.tzinfo is None or end.tzinfo is None or end < start:
                raise ValueError("invalid time range")
        except ValueError as exc:
            raise _InputError("reported_time_range_invalid") from exc
        if len(manifest["argv"]) > limits["argv_items"]:
            raise _InputError("argv_budget_exceeded")
        if len(manifest["source_inputs"]) + len(manifest["config_inputs"]) > limits["input_files"]:
            raise _InputError("input_file_budget_exceeded")
        # Resolve every declared path before reading any streams/target inputs.
        stream_paths = {key: _contained(bundle, manifest[key]["path"]) for key in ("stdout", "stderr")}
        if stream_paths["stdout"] == stream_paths["stderr"]:
            raise _InputError("stream_identity_collision")
        inputs = [(kind, row, _contained(target, row["path"]))
                  for kind in ("source", "config") for row in manifest[kind + "_inputs"]]
        if len({path for _, _, path in inputs}) != len(inputs):
            raise _InputError("input_identity_collision")
        root_hash = _digest(os.path.normcase(str(target)).encode("utf-8"))
        identities = receipt["identities"]
        identities.update({
            "manifest_sha256": _digest(manifest_bytes),
            "target_root_sha256": root_hash,
            "tool_sha256": _identity(manifest["tool"]),
            "command_sha256": _identity({"argv": manifest["argv"], "cwd": os.path.normcase(str(cwd))}),
            "run_sha256": _identity({"manifest_sha256": _digest(manifest_bytes), "target_root_sha256": root_hash}),
        })
        exit_code = manifest["exit_code"]
        summary["reported_exit_code"] = exit_code
        summary["reported_process_outcome"] = ("not_reported" if exit_code is None else
                                               "reported_exit_zero" if exit_code == 0 else "reported_exit_nonzero")
        for key, path in stream_paths.items():
            content = _read(path, limits["stream_bytes"], budget)
            if diagnostic_format is not None:
                captured[key] = content
            observed = _digest(content)
            declared = manifest[key]["sha256"]
            receipt["streams"][key] = {"sha256": observed, "declared_sha256": declared,
                                      "byte_count": len(content), "status": "MATCH" if observed == declared else "MISMATCH"}
        summary["stream_integrity"] = ("MATCH" if all(row["status"] == "MATCH" for row in receipt["streams"].values())
                                       else "MISMATCH")
        if summary["stream_integrity"] != "MATCH":
            raise _InputError("stream_hash_mismatch")
        for kind, row, path in inputs:
            try:
                content = _read(path, limits["input_bytes"], budget)
                observed = _digest(content)
                if kind == "source" and atlas_project is not None:
                    source_bytes[len(receipt["inputs"]["source"])] = content
                status = "MATCH" if observed == row["sha256"] else "MISMATCH"
            except _InputError as exc:
                if str(exc) != "file_unavailable":
                    raise
                observed, status = None, "UNAVAILABLE"
            receipt["inputs"][kind].append({"path": row["path"], "declared_sha256": row["sha256"],
                                           "current_sha256": observed, "status": status})
            if kind == "source" and diagnostic_format is not None:
                key = _source_identifier_key(str(path))
                if key is not None:
                    declared_sources[key] = len(receipt["inputs"]["source"]) - 1
        rows = receipt["inputs"]["source"] + receipt["inputs"]["config"]
        if any(row["status"] == "MISMATCH" for row in rows):
            correspondence = "MISMATCH"
        elif not receipt["inputs"]["source"] or not receipt["inputs"]["config"] or any(row["status"] != "MATCH" for row in rows):
            correspondence = "INCOMPLETE"
        else:
            correspondence = "MATCH"
        summary["current_input_correspondence"] = correspondence
        identities["input_observation_sha256"] = _identity(receipt["inputs"])
        identities["streams_sha256"] = _identity(receipt["streams"])
        summary["status"] = "INGESTED"
    except _InputError as exc:
        summary["reason_codes"].append(str(exc))
    if diagnostic_format is not None:
        observation = _observe_native_warning(receipt, captured, tool, diagnostic_format,
                                              contract["normalization"], declared_sources)
        receipt["diagnostic_observations"] = observation
        if observation["status"] in ("OBSERVED", "NO_SUPPORTED_OBSERVATIONS"):
            receipt["authority"]["diagnostics"] = "bounded_family_observations_only"
    if atlas_project is not None:
        receipt["atlas_source_correspondence"] = _atlas_source_correspondence(
            receipt, source_bytes, atlas if isinstance(atlas, dict) else {},
            atlas_commit if isinstance(atlas_commit, dict) else {}, atlas_project)
    ensure_valid_payload(contract["artifact_id"], receipt)
    return receipt


def native_source_errors(receipt: dict[str, Any], atlas: dict[str, Any]) -> list[str]:
    """Recheck recorded source correspondence without reopening target files."""
    from tools.core.source_snapshot_integrity import source_text_hash
    from tools.core.artifact_validator import validate_payload
    if validate_payload(_contract()["artifact_id"], receipt):
        return ["native_receipt_invalid"]
    errors = []
    result = receipt.get("atlas_source_correspondence", {})
    inputs = receipt.get("inputs", {}).get("source", [])
    project = atlas.get(result.get("project"), {})
    if not isinstance(project, dict):
        return ["atlas_project_unavailable"]
    metadata = project.get("project", {})
    root = metadata.get("root") if isinstance(metadata, dict) else None
    # Recorded lexical identity only; never follow a snapshot-selected path.
    root_valid = (isinstance(root, str) and Path(root).is_absolute()
                  and ".." not in root.replace("\\", "/").split("/"))
    root_hash = _digest(os.path.normcase(str(Path(root))).encode("utf-8")) if root_valid else None
    if not root_hash or root_hash != receipt.get("identities", {}).get("target_root_sha256"):
        errors.append("atlas_project_root_mismatch_or_unavailable")
    if (receipt.get("summary", {}).get("status") != "INGESTED" or not inputs
            or result.get("scope") != "analysis_time_declared_source_text_only"
            or result.get("input_observation_sha256") != _identity(receipt.get("inputs"))):
        errors.append("native_source_observation_unavailable")
    rows = result.get("sources", [])
    if not isinstance(rows, list) or len(rows) != len(inputs):
        return errors + ["native_source_inventory_mismatch"]
    for index, (row, source) in enumerate(zip(rows, inputs)):
        relative = source.get("path", "").replace("\\", "/")
        files = project.get("files", {})
        file = files.get(relative, {}) if isinstance(files, dict) else {}
        reference = file.get("hash") if isinstance(file, dict) else None
        valid_hash = isinstance(reference, str) and source_text_hash("", reference) is not None
        if (row.get("source_input_index") != index or row.get("file") != relative
                or not valid_hash or row.get("atlas_hash") != reference
                or row.get("observed_text_hash") != reference or row.get("status") != "MATCH"
                or source.get("status") != "MATCH" or not source.get("current_sha256")
                or source.get("current_sha256") != source.get("declared_sha256")):
            errors.append("declared_source_not_atlas_matched")
    return sorted(set(errors))


def _atlas_source_correspondence(receipt, captured, atlas, commit, project):
    from tools.core.atlas_integrity import validate_atlas_commit
    from tools.core.source_snapshot_integrity import source_text_hash
    result = {"scope": "analysis_time_declared_source_text_only", "status": "UNAVAILABLE",
              "project": project, "atlas_snapshot_id": None,
              "input_observation_sha256": receipt["identities"]["input_observation_sha256"],
              "reason_codes": [], "sources": []}
    project_data = atlas.get(project, {})
    files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
    if not isinstance(files, dict):
        files = {}
    for index, source in enumerate(receipt["inputs"]["source"]):
        relative = source["path"].replace("\\", "/")
        file = files.get(relative, {})
        reference = file.get("hash") if isinstance(file, dict) else None
        observed = None
        try:
            observed = source_text_hash(captured[index].decode("utf-8"), reference)
        except (KeyError, UnicodeError):
            result["reason_codes"].append("declared_source_text_unavailable")
        status = ("UNAVAILABLE" if observed is None else
                  "MATCH" if observed == reference and source["status"] == "MATCH" else "MISMATCH")
        result["sources"].append({"source_input_index": index, "file": relative,
                                  "atlas_hash": reference if isinstance(reference, str) else None,
                                  "observed_text_hash": observed, "status": status})
    receipt["atlas_source_correspondence"] = result
    checks = validate_atlas_commit(atlas, commit) if atlas and commit else []
    errors = native_source_errors(receipt, atlas) + result["reason_codes"]
    if not checks or not all(row["passed"] for row in checks):
        errors.append("atlas_commit_invalid_or_unavailable")
    else:
        result["atlas_snapshot_id"] = commit["snapshot_id"]
    result["reason_codes"] = sorted(set(errors))
    if not errors:
        result["status"] = "MATCH"
    elif any(row["status"] == "MISMATCH" for row in result["sources"]):
        result["status"] = "MISMATCH"
    return result

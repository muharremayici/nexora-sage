"""Bounded local patch acquisition; transport facts are not apply authority."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
import stat
from typing import Any

from tools.core.json_io import load_json_object_strict_cached
from tools.core.source_snapshot_reader import read_source_snapshot_binding
from tools.core.target_repository_trust import classify_target_path
from tools.core.unmanaged_atomic_io import native_filesystem_path


class PatchInputError(ValueError):
    def __init__(self, reason: str, *, stage: str = "acquisition", receipt: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage
        self.receipt = receipt or {}


def patch_input_policy() -> dict[str, Any]:
    contract = load_json_object_strict_cached(
        Path(__file__).resolve().parents[2] / "config/agent_surface_contract.json",
        label="Agent patch input",
    )
    policy = contract.get("patch_input", {})
    if (not isinstance(policy, dict) or policy.get("encoding") != "utf-8"
            or policy.get("digest") != "sha256"
            or type(policy.get("version")) is not int or policy["version"] != 1
            or policy.get("mode") != "workspace_file"
            or not isinstance(policy.get("required_fields"), list)
            or not policy["required_fields"]):
        raise PatchInputError("patch_input_policy_unavailable")
    for key in ("maximum_patch_bytes", "maximum_source_bytes", "maximum_snapshot_id_characters"):
        if type(policy.get(key)) is not int or policy[key] <= 0:
            raise PatchInputError("patch_input_policy_unavailable")
    return policy


def _relative_file(value: Any) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\x00" in value or value.startswith("/")
            or any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in value.split("/"))
            or re.search(r'[<>"|?*\x00-\x1f]', value)
            or getattr(ntpath, "isreserved", lambda path: PureWindowsPath(path).is_reserved())(value)):
        raise PatchInputError("patch_input_path_refused")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise PatchInputError("patch_input_path_refused") from None
    return value


def _identity(value: os.stat_result) -> tuple:
    # Windows path-stat and handle-stat can expose different ctime meanings.
    # Compare ctime only within the same API below; keep file ID/size/mtime
    # cross-API, and require the caller's exact byte hash independently.
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _regular_unlinked(value: os.stat_result) -> bool:
    return (stat.S_ISREG(value.st_mode) and value.st_nlink == 1
            and not (getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def _read_confined(root: Path, relative: str, limit: int) -> bytes:
    """Reject observed links/instability, without claiming hostile-FS atomicity."""
    relative = _relative_file(relative)
    candidate = root / relative
    try:
        boundary = classify_target_path(root, candidate)
        if not boundary["contained"] or boundary["link_path"]:
            raise PatchInputError("patch_input_path_refused")
        current = root
        for part in relative.split("/"):
            current = current / part
            info = Path(native_filesystem_path(current)).lstat()
            if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise PatchInputError("patch_input_path_refused")
        before = Path(native_filesystem_path(candidate)).lstat()
        if not _regular_unlinked(before):
            raise PatchInputError("patch_input_not_regular_unlinked_file")
        if before.st_size > limit:
            raise PatchInputError("patch_input_over_budget")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(native_filesystem_path(candidate), flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not _regular_unlinked(opened) or _identity(opened) != _identity(before):
                raise PatchInputError("patch_input_unstable_file")
            data = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
        final = Path(native_filesystem_path(candidate)).lstat()
        boundary = classify_target_path(root, candidate)
        if (len(data) > limit or len(data) != before.st_size
                or _identity(before) != _identity(after) or _identity(after) != _identity(final)
                or before.st_ctime_ns != final.st_ctime_ns or opened.st_ctime_ns != after.st_ctime_ns
                or not boundary["contained"] or boundary["link_path"]):
            raise PatchInputError("patch_input_unstable_file")
        return data
    except (OSError, RuntimeError):
        raise PatchInputError("patch_input_file_unavailable") from None


def _strict_text(raw: bytes, *, patch: bool) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise PatchInputError("patch_input_invalid_utf8") from None
    if "\x00" in text or (patch and text.startswith("\ufeff")):
        raise PatchInputError("patch_input_unsupported_encoding")
    return text


def _single_target_diff(text: str, target: str) -> None:
    # Only recognize structural CRLF here; the original text/bytes are retained.
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    i = 0
    if lines and lines[0].startswith("diff --git "):
        if lines[0] != f"diff --git a/{target} b/{target}":
            raise PatchInputError("patch_input_unsupported_diff_scope")
        i += 1
    if i < len(lines) and lines[i].startswith("index "):
        if not re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100(?:644|755))?", lines[i]):
            raise PatchInputError("patch_input_unsupported_diff_scope")
        i += 1
    if lines[i:i + 2] != [f"--- a/{target}", f"+++ b/{target}"]:
        raise PatchInputError("patch_input_unsupported_diff_scope")
    body = lines[i + 2:]
    hunk = r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?:.*)?"
    if not body or not re.fullmatch(hunk, body[0]):
        raise PatchInputError("patch_input_unsupported_diff_scope")
    for line in body:
        if re.fullmatch(hunk, line) or line == "\\ No newline at end of file":
            continue
        if not line or line[0] not in " +-" or line.startswith(("--- ", "+++ ")):
            raise PatchInputError("patch_input_unsupported_diff_scope")


@dataclass(frozen=True)
class PreparedPatchInput:
    text: str
    source_text: str
    target_file: str
    project: str
    atlas_path: str
    source_snapshot_hash: str
    analysis_snapshot_id: str
    source_sha256: str
    receipt: dict[str, Any]


def prepare_patch_input(envelope: Any, inline: str, *, root: Path, raw_dir: Path,
                        target: dict[str, Any], snapshot_id: str) -> PreparedPatchInput:
    policy = patch_input_policy()
    if (not isinstance(envelope, dict) or set(envelope) != set(policy["required_fields"])
            or inline != "" or type(inline) is not str
            or type(envelope.get("version")) is not int or envelope["version"] != policy["version"]
            or envelope.get("mode") != policy["mode"]):
        raise PatchInputError("patch_input_invalid_envelope")
    count = envelope["byte_count"]
    if type(count) is not int or not 0 < count <= policy["maximum_patch_bytes"]:
        raise PatchInputError("patch_input_invalid_byte_count")
    for key in ("sha256", "source_sha256"):
        if not isinstance(envelope[key], str) or not re.fullmatch("[0-9a-f]{64}", envelope[key]):
            raise PatchInputError("patch_input_invalid_digest")
    requested_snapshot = envelope["analysis_snapshot_id"]
    if (not isinstance(requested_snapshot, str) or not requested_snapshot
            or len(requested_snapshot) > policy["maximum_snapshot_id_characters"]):
        raise PatchInputError("patch_input_invalid_snapshot")
    raw = _read_confined(root, envelope["path"], policy["maximum_patch_bytes"])
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) != count or digest != envelope["sha256"]:
        raise PatchInputError("patch_input_byte_identity_mismatch")
    text = _strict_text(raw, patch=True)
    try:
        if (not all(target.get(k) is True for k in ("inside_root", "exists", "indexed"))
                or target.get("source_snapshot_status") != "ok" or target.get("drift_check_status") != "match"
                or requested_snapshot != snapshot_id):
            raise PatchInputError("patch_input_source_not_current")
        relative = _relative_file(target.get("target_file"))
        source = _read_confined(root, relative, policy["maximum_source_bytes"])
        if hashlib.sha256(source).hexdigest() != envelope["source_sha256"]:
            raise PatchInputError("patch_input_source_identity_mismatch")
        source_text = _strict_text(source, patch=False)
        node = str(target.get("resolved_node") or "").split("::", 1)
        if len(node) != 2 or not all(node):
            raise PatchInputError("patch_input_snapshot_unavailable")
        binding = read_source_snapshot_binding(raw_dir, node[0], node[1], expected_hash=target.get("source_snapshot_hash"))
        if binding["status"] != "ok" or binding["content"] != source_text:
            raise PatchInputError("patch_input_snapshot_unavailable")
    except PatchInputError as exc:
        raise PatchInputError(exc.reason, stage="source_binding", receipt={
            "mode": policy["mode"], "byte_count": len(raw), "sha256": digest,
            "expected_byte_count": count, "expected_sha256": envelope["sha256"],
            "source_binding": "REFUSED",
        }) from None
    _single_target_diff(text, relative)
    receipt = {"mode": policy["mode"], "status": "RECEIVED", "byte_count": len(raw), "sha256": digest,
               "expected_byte_count": count, "expected_sha256": envelope["sha256"], "encoding": policy["encoding"],
               "source_sha256": envelope["source_sha256"], "source_snapshot_hash": binding["source_hash"],
               "analysis_snapshot_id": snapshot_id, "source_binding": "CURRENT_AT_RECEIPT",
               "newlines": {"crlf": raw.count(b"\r\n"), "lf_only": raw.count(b"\n") - raw.count(b"\r\n"), "cr_only": raw.count(b"\r") - raw.count(b"\r\n"), "final_lf": raw.endswith(b"\n")},
               "governance_projection": "logical_lines_not_byte_exact_result", "mutation_performed": False,
               "claim_boundary": policy["claim_boundary"]}
    return PreparedPatchInput(text, source_text, relative, node[0], node[1], binding["source_hash"], snapshot_id, envelope["source_sha256"], receipt)


def check_patch_input_continuity(prepared: PreparedPatchInput, *, root: Path, raw_dir: Path, snapshot_id: str) -> None:
    try:
        if snapshot_id != prepared.analysis_snapshot_id:
            raise PatchInputError("patch_input_snapshot_changed")
        raw = _read_confined(root, prepared.target_file, patch_input_policy()["maximum_source_bytes"])
        if hashlib.sha256(raw).hexdigest() != prepared.source_sha256:
            raise PatchInputError("patch_input_source_changed")
        binding = read_source_snapshot_binding(raw_dir, prepared.project, prepared.atlas_path, expected_hash=prepared.source_snapshot_hash)
        if binding["status"] != "ok" or binding["content"] != prepared.source_text:
            raise PatchInputError("patch_input_snapshot_changed")
    except PatchInputError as exc:
        raise PatchInputError(exc.reason, stage="source_continuity") from None

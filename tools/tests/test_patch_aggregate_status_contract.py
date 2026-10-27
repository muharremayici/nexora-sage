import json
import subprocess
import hashlib
import sqlite3
import os
import asyncio
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import pytest

from tools.mcp import server
from tools.core import patch_input as acquisition


@pytest.fixture
def byte_patch(tmp_path):
    root = tmp_path / "çalışma e\u0301 😀"
    (root / "src").mkdir(parents=True)
    relative = "src/öğe.ts"
    original = b"export const value = 1;\n"
    (root / relative).write_bytes(original)
    raw_dir = tmp_path / "selected_primary"
    raw_dir.mkdir()
    digest = hashlib.md5(original).hexdigest()
    with sqlite3.connect(raw_dir / "codemaps.db") as conn:
        conn.execute("CREATE TABLE files(file_id INTEGER, project_key TEXT, rel_path TEXT, hash TEXT)")
        conn.execute("CREATE TABLE source_snapshots(file_id INTEGER, project_key TEXT, rel_path TEXT, content TEXT, content_hash TEXT, status TEXT)")
        conn.execute("INSERT INTO files VALUES(1, 'MAIN', ?, ?)", (relative, digest))
        conn.execute("INSERT INTO source_snapshots VALUES(1, 'MAIN', ?, ?, ?, 'ok')", (relative, original.decode(), digest))
    payload = (f"--- a/{relative}\n+++ b/{relative}\n@@ -1 +1 @@\n-export const value = 1;\n+export const value = 2;\n").encode()
    (root / "change.diff").write_bytes(payload)
    envelope = {"version": 1, "mode": "workspace_file", "path": "change.diff", "byte_count": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(), "source_sha256": hashlib.sha256(original).hexdigest(), "analysis_snapshot_id": "snapshot-file-v1"}
    target = {"target_file": relative, "target_ref": f"MAIN::{relative}", "resolved_node": f"MAIN::{relative}",
              "inside_root": True, "exists": True, "indexed": True, "source_snapshot_status": "ok",
              "source_snapshot_hash": digest, "drift_check_status": "match"}
    return root, raw_dir, envelope, target, original


def _prepare(case, *, envelope=None, inline=""):
    root, raw_dir, default, target, _ = case
    return acquisition.prepare_patch_input(default if envelope is None else envelope, inline, root=root,
                                           raw_dir=raw_dir, target=target, snapshot_id="snapshot-file-v1")


def _payload(case, raw):
    root, _, envelope, _, _ = case
    (root / "change.diff").write_bytes(raw)
    envelope.update(byte_count=len(raw), sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("final_newline", [True, False])
def test_byte_patch_large_unicode_exact_receipt(byte_patch, newline, final_newline):
    relative = byte_patch[3]["target_file"]
    old = "export const value = 1;"
    value = "İstanbul e\u0301 é 😀 " * 3500
    raw = (f"--- a/{relative}\n+++ b/{relative}\n@@ -1 +1 @@\n-{old}\n+//{value}\n").encode().replace(b"\n", newline)
    if not final_newline:
        raw = raw[:-len(newline)]
    assert len(raw) > 32768 and len(raw.decode()) > 32767
    _payload(byte_patch, raw)
    result = _prepare(byte_patch)
    assert result.text.encode() == raw
    assert result.receipt["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result.receipt["byte_count"] == len(raw)
    assert result.receipt["newlines"]["final_lf"] is final_newline
    assert result.source_snapshot_hash == byte_patch[3]["source_snapshot_hash"]
    assert result.source_sha256 != result.source_snapshot_hash
    acquisition.check_patch_input_continuity(result, root=byte_patch[0], raw_dir=byte_patch[1], snapshot_id=result.analysis_snapshot_id)
    assert (byte_patch[0] / relative).read_bytes() == byte_patch[4]


@pytest.mark.parametrize("key,value", [("version", True), ("version", 2), ("mode", "url"), ("byte_count", True), ("byte_count", 0), ("byte_count", -1), ("byte_count", 1.5), ("byte_count", 2000001), ("sha256", "a" * 63), ("sha256", "A" * 64), ("source_sha256", None), ("analysis_snapshot_id", ""), ("analysis_snapshot_id", "x" * 257), ("extra", "ignored?")])
def test_byte_patch_envelope_refusals(byte_patch, key, value):
    envelope = {**byte_patch[2], key: value}
    with pytest.raises(acquisition.PatchInputError):
        _prepare(byte_patch, envelope=envelope)


@pytest.mark.parametrize("value", [[], "file", {}, None])
def test_byte_patch_wrong_envelope_type_or_missing_fields(byte_patch, value):
    with pytest.raises(acquisition.PatchInputError):
        acquisition.prepare_patch_input(value, "", root=byte_patch[0], raw_dir=byte_patch[1], target=byte_patch[3], snapshot_id=byte_patch[2]["analysis_snapshot_id"])


@pytest.mark.parametrize("inline", ["nonempty", " ", None, 0])
def test_byte_patch_refuses_ambiguous_inline_mode(byte_patch, inline):
    with pytest.raises(acquisition.PatchInputError):
        _prepare(byte_patch, inline=inline)


@pytest.mark.parametrize("path", ["../change.diff", "/change.diff", "C:change.diff", "C:/change.diff", "\\\\host\\share", "a\\b", "file:stream", "a/../change.diff", "./change.diff", "change.diff.", "change.diff ", "CON", "NUL.txt", "?", "a\x00b", "bad\ud800", "a//b", ""])
def test_byte_patch_path_refusals(byte_patch, path):
    with pytest.raises(acquisition.PatchInputError):
        _prepare(byte_patch, envelope={**byte_patch[2], "path": path})


@pytest.mark.parametrize("bad", [b"\xff", b"\xef\xbb\xbfhello", b"a\x00b", b"\xff\xfea\x00"])
def test_byte_patch_encoding_refuses_without_echo(byte_patch, bad):
    _payload(byte_patch, bad)
    with pytest.raises(acquisition.PatchInputError) as exc:
        _prepare(byte_patch)
    assert "hello" not in str(exc.value) and "\\xff" not in str(exc.value)


def test_byte_patch_tampered_count_hash_and_missing_file(byte_patch):
    for changes in ({"sha256": "0" * 64}, {"byte_count": 1}, {"path": "missing.diff"}, {"path": "src"}):
        with pytest.raises(acquisition.PatchInputError):
            _prepare(byte_patch, envelope={**byte_patch[2], **changes})


@pytest.mark.parametrize("change", ["source", "snapshot", "sqlite", "foreign_project", "unindexed", "outside", "source_hash", "invalid_source_utf8", "oversize"])
def test_byte_patch_source_binding_refusals(byte_patch, change):
    root, raw_dir, envelope, target, _ = byte_patch
    if change == "source":
        (root / target["target_file"]).write_bytes(b"changed")
    elif change == "invalid_source_utf8":
        raw = b"\xffsecret"
        (root / target["target_file"]).write_bytes(raw)
        envelope["source_sha256"] = hashlib.sha256(raw).hexdigest()
    elif change == "oversize":
        (root / target["target_file"]).write_bytes(b"x" * 2000001)
    elif change == "snapshot":
        envelope["analysis_snapshot_id"] = "foreign"
    elif change == "sqlite":
        with sqlite3.connect(raw_dir / "codemaps.db") as conn:
            conn.execute("DELETE FROM source_snapshots")
    elif change == "foreign_project":
        target["resolved_node"] = "OTHER::" + target["target_file"]
    elif change == "unindexed":
        target["indexed"] = False
    elif change == "outside":
        target["inside_root"] = False
    elif change == "source_hash":
        envelope["source_sha256"] = "0" * 64
    with pytest.raises(acquisition.PatchInputError) as exc:
        _prepare(byte_patch)
    assert exc.value.stage == "source_binding"
    assert exc.value.receipt["sha256"] == envelope["sha256"]


@pytest.mark.parametrize("form", ["second", "rename", "binary", "headerless", "replacement", "other", "mode", "quoted", "header_only"])
def test_byte_patch_rejects_unsupported_scope(byte_patch, form):
    raw = (byte_patch[0] / "change.diff").read_bytes()
    if form == "second": raw += raw
    elif form == "rename": raw = b"rename from old\nrename to new\n" + raw
    elif form == "binary": raw = b"GIT binary patch\n" + raw
    elif form == "headerless": raw = b"@@ -1 +1 @@\n-old\n+new\n"
    elif form == "replacement": raw = b"export const value = 2;\n"
    elif form == "other": raw = raw.replace("öğe".encode(), b"other")
    elif form == "mode": raw = b"old mode 100644\nnew mode 100755\n" + raw
    elif form == "quoted": raw = raw.replace(b"--- a/", b'--- "a/')
    elif form == "header_only": raw = raw.split(b"@@")[0]
    _payload(byte_patch, raw)
    with pytest.raises(acquisition.PatchInputError, match="unsupported_diff_scope"):
        _prepare(byte_patch)


@pytest.mark.parametrize("kind", ["source", "snapshot", "sqlite"])
def test_byte_patch_final_continuity_refuses(byte_patch, kind):
    prepared = _prepare(byte_patch)
    if kind == "source":
        (byte_patch[0] / prepared.target_file).write_bytes(b"changed")
    if kind == "sqlite":
        with sqlite3.connect(byte_patch[1] / "codemaps.db") as conn:
            conn.execute("UPDATE source_snapshots SET content='changed'")
    with pytest.raises(acquisition.PatchInputError) as exc:
        acquisition.check_patch_input_continuity(prepared, root=byte_patch[0], raw_dir=byte_patch[1], snapshot_id="other" if kind == "snapshot" else prepared.analysis_snapshot_id)
    assert exc.value.stage == "source_continuity"


def _mcp_fixture(case, *, oracle_status="PASS", governance=None):
    stack = ExitStack()
    root, raw_dir, envelope, target, _ = case
    stack.enter_context(patch.object(server, "ANALYZED_REPOSITORY_ROOT", root))
    stack.enter_context(patch.object(server, "_raw_dir_for_target", return_value=raw_dir))
    stack.enter_context(patch.object(server, "_target_path_status", return_value=target))
    stack.enter_context(patch.object(server, "_analysis_snapshot_id", return_value=envelope["analysis_snapshot_id"]))
    stack.enter_context(patch.object(server, "_audit_violation_work_items_from_sqlite", return_value=([], 0, True)))
    stack.enter_context(patch.object(server, "resolve_execution_identity", return_value={"system_scope": "target_repository", "subject_root": str(root)}))
    stack.enter_context(patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **kw: result))
    stack.enter_context(patch("tools.core.seal_impact_guard.mutating_agent_surface_block", return_value={"blocked": False}))
    engine = stack.enter_context(patch("tools.engines.mcp_governance_engine.validate_proposed_patch", return_value=governance or {"status": "PASS", "violations": []}))
    oracle = stack.enter_context(patch.object(server, "_check_exact_patch_applicability", return_value={"status": oracle_status, "mutation_performed": False}))
    return stack, engine, oracle


@pytest.mark.parametrize("oracle_status", ["PASS", "FAIL", "UNKNOWN"])
def test_byte_patch_mcp_preserves_exact_payload_and_no_apply_authority(byte_patch, oracle_status):
    stack, engine, oracle = _mcp_fixture(byte_patch, oracle_status=oracle_status)
    with stack:
        result = json.loads(server.validate_patch(byte_patch[3]["target_ref"], "", format="json", patch_input=byte_patch[2]))
    engine.assert_called_once_with(byte_patch[3]["target_file"], (byte_patch[0] / "change.diff").read_bytes().decode(), workspace_root=byte_patch[0], _existing_source=byte_patch[4].decode())
    assert oracle.call_args.args[0].encode() == (byte_patch[0] / "change.diff").read_bytes()
    assert result["patch_input_receipt"]["source_binding"] == "CURRENT_AT_RETURN_CHECK"
    assert result["patch_applicability"]["status"] == oracle_status
    assert result["safe_to_apply"] is False
    assert result["target_native_policy_validation"]["status"] == "NOT_RUN"
    assert result["status"] != "PASS"


def test_byte_patch_mcp_refusal_does_not_run_validation_or_echo_payload(byte_patch):
    stack, engine, oracle = _mcp_fixture(byte_patch)
    envelope = {**byte_patch[2], "sha256": "0" * 64}
    with stack:
        result = json.loads(server.validate_patch(byte_patch[3]["target_file"], "", format="json", patch_input=envelope))
        brief = server.validate_patch(byte_patch[3]["target_file"], "", patch_input=envelope)
    engine.assert_not_called()
    oracle.assert_not_called()
    assert result["governance_status"] == result["patch_applicability"]["status"] == "NOT_RUN"
    assert "repair_patch_input_before_validation" in brief
    assert "export const" not in json.dumps(result)


def test_byte_patch_mcp_continuity_failure_retains_executed_governance(byte_patch):
    stack, engine, _ = _mcp_fixture(byte_patch)
    def evaluated(*args, **kwargs):
        (byte_patch[0] / byte_patch[3]["target_file"]).write_bytes(b"changed during governance")
        return {"status": "PASS", "violations": []}
    engine.side_effect = evaluated
    with stack:
        result = json.loads(server.validate_patch(byte_patch[3]["target_file"], "", format="json", patch_input=byte_patch[2]))
    assert result["status"] == "INVALID_CONTEXT"
    assert result["governance_status"] == "PASS"
    assert result["patch_input_receipt"]["source_binding"] == "REFUSED_AT_RETURN"
    assert result["safe_to_apply"] is False


def test_byte_patch_real_git_oracle_is_non_mutating(byte_patch):
    prepared = _prepare(byte_patch)
    result = server._check_exact_patch_applicability(prepared.text, byte_patch[0])
    assert result["status"] == "PASS", result
    assert result["mutation_performed"] is False
    assert (byte_patch[0] / prepared.target_file).read_bytes() == byte_patch[4]


def test_byte_patch_mcp_schema_accepts_optional_envelope():
    tools = asyncio.run(server.mcp.list_tools())
    tool = next(t for t in tools if t.name == "validate_patch")
    assert "patch_input" in tool.inputSchema["properties"]
    assert "patch_input" not in tool.inputSchema.get("required", [])
    assert "patch_content" in tool.inputSchema["required"]


@pytest.mark.parametrize("fault", [None, "mtime", "ctime", "inode", "size"])
def test_byte_patch_handle_stability_keeps_same_api_ctime_checks(byte_patch, fault):
    from types import SimpleNamespace
    original = os.fstat
    count = 0
    def observed(fd):
        nonlocal count
        actual = original(fd)
        fields = {name: getattr(actual, name, 0) for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink", "st_mode", "st_file_attributes")}
        # A consistent handle-only ctime offset is not a file mutation.
        fields["st_ctime_ns"] += 100
        count += 1
        if count == 2 and fault:
            fields[{"mtime": "st_mtime_ns", "ctime": "st_ctime_ns", "inode": "st_ino", "size": "st_size"}[fault]] += 1
        return SimpleNamespace(**fields)
    with patch.object(acquisition.os, "fstat", side_effect=observed):
        if fault:
            with pytest.raises(acquisition.PatchInputError, match="unstable"):
                _prepare(byte_patch)
        else:
            assert _prepare(byte_patch).receipt["status"] == "RECEIVED"


def test_byte_patch_refuses_hardlinked_payload(byte_patch):
    os.link(byte_patch[0] / "change.diff", byte_patch[0] / "second.diff")
    with pytest.raises(acquisition.PatchInputError, match="not_regular_unlinked"):
        _prepare(byte_patch)


def test_byte_patch_refuses_observed_link_before_read(byte_patch):
    with patch.object(acquisition, "classify_target_path", return_value={"contained": True, "link_path": True}):
        with pytest.raises(acquisition.PatchInputError, match="path_refused"):
            _prepare(byte_patch)


def test_byte_patch_budget_is_policy_owned_and_checked_before_read(byte_patch):
    policy = {**acquisition.patch_input_policy(), "maximum_patch_bytes": 8}
    with patch.object(acquisition, "patch_input_policy", return_value=policy):
        with pytest.raises(acquisition.PatchInputError, match="invalid_byte_count"):
            _prepare(byte_patch)
        with pytest.raises(acquisition.PatchInputError, match="over_budget"):
            _prepare(byte_patch, envelope={**byte_patch[2], "byte_count": 8})


def test_byte_patch_actual_tool_manager_validates_new_optional_input(byte_patch):
    stack, engine, _ = _mcp_fixture(byte_patch)
    with stack:
        result = asyncio.run(server.mcp._tool_manager.call_tool("validate_patch", {
            "target_file": byte_patch[3]["target_file"], "patch_content": "", "format": "json", "patch_input": byte_patch[2],
        }))
    assert engine.call_count == 1
    assert json.loads(result)["patch_input_receipt"]["status"] == "RECEIVED"


@pytest.mark.parametrize("guard", ["seal", "lease", "governance"])
def test_byte_patch_cannot_bypass_existing_authority_or_governance(byte_patch, guard):
    stack, _, _ = _mcp_fixture(byte_patch, governance={"status": "FAIL", "violations": [{"rule": "existing_governance"}]} if guard == "governance" else None)
    if guard == "seal":
        stack.enter_context(patch("tools.core.seal_impact_guard.mutating_agent_surface_block", return_value={"blocked": True, "message": "Retained human seal"}))
    if guard == "lease":
        stack.enter_context(patch("tools.core.target_write_lease.inspect_target_write_lease", return_value={"lease": {}}))
    with stack:
        result = json.loads(server.validate_patch(byte_patch[3]["target_file"], "", format="json", actor_id="foreign" if guard == "lease" else "", patch_input=byte_patch[2]))
    assert result["safe_to_apply"] is False
    assert result["governance_status"] == "FAIL"
    assert result["patch_input_receipt"]["status"] == "RECEIVED"


def test_byte_patch_engine_uses_captured_source_instead_of_live_reopen(byte_patch):
    from tools.engines.mcp_governance_engine import validate_proposed_patch
    root, _, _, target, original = byte_patch
    (root / target["target_file"]).write_bytes(b"changed after capture")
    result = validate_proposed_patch(target["target_file"], original.decode(), workspace_root=root, _existing_source=original.decode())
    assert result["status"] == "NO_OP"
    # This unit proves input selection only; MCP continuity must reject the drift.
    with pytest.raises(ValueError, match="Invalid internal captured source"):
        validate_proposed_patch(target["target_file"], original.decode(), workspace_root=root, _existing_source=1)


def test_byte_patch_powershell_binary_metadata_matches_python(byte_patch):
    import base64
    import shutil
    shell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is not installed; no cross-client proof claimed")
    path = str(byte_patch[0] / "change.diff").replace("'", "''")
    script = "$b=[IO.File]::ReadAllBytes('" + path + "'); $s=[Security.Cryptography.SHA256]::Create(); @{byte_count=$b.Length; sha256=([BitConverter]::ToString($s.ComputeHash($b))).Replace('-','').ToLowerInvariant()} | ConvertTo-Json -Compress"
    # EncodedCommand carries only this small control script, never patch text.
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")], capture_output=True, timeout=30)
    assert result.returncode == 0
    metadata = json.loads(result.stdout.decode("utf-8-sig"))
    assert metadata == {k: byte_patch[2][k] for k in ("byte_count", "sha256")}


@pytest.mark.parametrize("key,value", [("version", 2), ("version", True), ("mode", "remote"), ("encoding", "utf-16"), ("digest", "md5"), ("maximum_patch_bytes", 0), ("maximum_source_bytes", 1.5)])
def test_byte_patch_unknown_policy_fails_closed(key, value):
    policy = {**acquisition.patch_input_policy(), key: value}
    with patch.object(acquisition, "load_json_object_strict_cached", return_value={"patch_input": policy}):
        with pytest.raises(acquisition.PatchInputError, match="policy_unavailable"):
            acquisition.patch_input_policy()


def test_byte_patch_guidance_limits_match_central_owner():
    from tools.core.config import CODE_MAPS_DIR
    policy = acquisition.patch_input_policy()
    guide = (CODE_MAPS_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert f"{policy['maximum_patch_bytes']:,} bytes each for patch and source" in guide
    assert policy["maximum_source_bytes"] == policy["maximum_patch_bytes"]
    assert "patch_input" in guide and "safe_to_apply" in guide


def test_byte_patch_real_governance_and_git_in_bounded_mcp_fixture(byte_patch):
    from tools.engines import mcp_governance_engine as engine_module
    real_engine = engine_module.validate_proposed_patch
    real_oracle = server._check_exact_patch_applicability
    stack, _, _ = _mcp_fixture(byte_patch)
    stack.enter_context(patch.object(engine_module, "validate_proposed_patch", wraps=real_engine))
    stack.enter_context(patch.object(engine_module, "RAW_DIR", byte_patch[0] / "engine_temporary"))
    stack.enter_context(patch.object(server, "_check_exact_patch_applicability", wraps=real_oracle))
    with stack:
        result = json.loads(server.validate_patch(byte_patch[3]["target_file"], "", format="json", patch_input=byte_patch[2]))
    assert result["governance_status"] == result["patch_applicability"]["status"] == "PASS", result
    assert result["patch_input_receipt"]["sha256"] == byte_patch[2]["sha256"]
    assert result["status"] == "INCOMPLETE_EVIDENCE" and result["safe_to_apply"] is False
    assert (byte_patch[0] / byte_patch[3]["target_file"]).read_bytes() == byte_patch[4]


def test_patch_applicability_timeout_remains_unknown_and_does_not_prescribe_payload_repair(tmp_path: Path) -> None:
    timed_out = subprocess.CompletedProcess(
        ["git", "apply", "--check"],
        returncode=124,
        stdout="",
        stderr="Timed out after 30.0s",
    )
    with patch.object(server, "run_observed_subprocess", return_value=(timed_out, 30.0)):
        applicability = server._check_exact_patch_applicability(
            "@@ -1,1 +1,1 @@\n-old\n+new\n",
            tmp_path,
        )

    assert applicability["status"] == "UNKNOWN"
    assert applicability["timed_out"] is True
    assert applicability["reason"] == "patch_applicability_oracle_timeout"

    brief = server._render_patch_validation_brief(
        {
            "status": "PASS",
            "violations": [],
            "target_exists": True,
            "target_indexed": True,
            "target_file": "src/example.ts",
            "target_ref": "MAIN::src/example.ts",
            "patch_applicability": applicability,
            "target_native_policy_validation": {"status": "NOT_RUN"},
        },
        "src/example.ts",
    )

    assert 'next_action: "retry_bounded_applicability_or_request_review"' in brief
    assert 'status: "INCOMPLETE_EVIDENCE"' in brief
    assert "repair_exact_patch_payload_before_apply" not in brief
    assert "incomplete evidence" in brief.lower()
    assert "safe_to_apply: false" in brief


def test_patch_validation_never_exposes_overall_pass_when_applicability_is_unknown(tmp_path: Path) -> None:
    with (
        patch.object(
            server,
            "_target_path_status",
            return_value={
                "target_ref": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "exists": True,
                "indexed": True,
                "inside_root": True,
            },
        ),
        patch.object(server, "_analysis_snapshot_id", return_value="snapshot-20"),
        patch.object(server, "_operator_privileged_projection_allowed", return_value=False),
        patch.object(
            server,
            "resolve_execution_identity",
            return_value={"system_scope": "target_repository", "subject_root": str(tmp_path)},
        ),
        patch(
            "tools.engines.mcp_governance_engine.validate_proposed_patch",
            return_value={
                "status": "PASS",
                "safe_to_apply": True,
                "violations": [],
                "existing_violation_count": 0,
                "resolved_violation_count": 0,
            },
        ),
        patch(
            "tools.core.seal_impact_guard.mutating_agent_surface_block",
            return_value={"blocked": False},
        ),
        patch.object(
            server,
            "_check_exact_patch_applicability",
            return_value={
                "status": "UNKNOWN",
                "oracle": "git_apply_check",
                "returncode": 124,
                "timed_out": True,
                "mutation_performed": False,
                "reason": "patch_applicability_oracle_timeout",
            },
        ),
    ):
        payload = server.validate_patch(
            "src/example.ts",
            "@@ -1,1 +1,1 @@\n-old\n+new",
            format="json",
        )

    parsed = json.loads(payload)
    assert parsed["status"] == "INCOMPLETE_EVIDENCE"
    assert parsed["overall_status"] == "INCOMPLETE_EVIDENCE"
    assert parsed["governance_status"] == "PASS"
    assert parsed["governance_validation_passed"] is True
    assert parsed["safe_to_apply"] is False
    assert parsed["application_readiness"] == "INCOMPLETE_APPLICABILITY_EVIDENCE"

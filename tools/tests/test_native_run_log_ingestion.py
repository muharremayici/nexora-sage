from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path
import pytest

from tools.core.artifact_validator import validate_payload
from tools.core import native_run_log_ingestion as ingestion


@pytest.mark.parametrize("payload", [{}, {"z": [1, True, None], "a": "源"}, [None, False, 3.5]])
def test_native_identity_uses_upstream_atlas_digest_with_unchanged_bytes(payload, monkeypatch):
    from tools.core import atlas_integrity
    from tools.core.analysis_snapshot_lineage import payload_sha256
    expected = atlas_integrity.payload_sha256(payload)
    assert ingestion._identity(payload) == expected == payload_sha256(payload)
    monkeypatch.setattr(atlas_integrity, "payload_sha256", lambda value: "a" * 64)
    assert ingestion._identity(payload) == "a" * 64


def _native_atlas(target, manifest, algorithm="md5"):
    from tools.core.atlas_integrity import build_atlas_commit
    files = {row["path"]: {"hash": hashlib.new(algorithm, (target / row["path"]).read_bytes()).hexdigest(),
                            "symbols": []} for row in manifest["source_inputs"]}
    atlas = {"MAIN": {"project": {"root": str(target)}, "files": files}}
    return atlas, build_atlas_commit(atlas)


def _native_lineage(tmp_path, receipt, atlas, commit):
    from tools.core.analysis_snapshot_lineage import write_lineage_receipt
    return write_lineage_receipt(artifact_id="native_run_log_receipt", producer="tools.ingest_native_run_logs",
                                artifact_payload=receipt, atlas=atlas, atlas_commit=commit, raw_dir=tmp_path)


@pytest.mark.parametrize("algorithm,content", [("md5", b"export const a=1;"),
                                              ("sha256", b"\xef\xbb\xbfexport const a=1;\r\n")])
def test_native_atlas_exact_declared_text_is_analysis_time_only(tmp_path, algorithm, content, monkeypatch):
    from tools.core.analysis_snapshot_lineage import receipt_binding
    target, bundle, manifest = _fixture(tmp_path)
    source = target / manifest["source_inputs"][0]["path"]
    source.write_bytes(content)
    manifest["source_inputs"][0]["sha256"] = _hash(content)
    atlas, commit = _native_atlas(target, manifest, algorithm)
    reads = []
    original = ingestion._read
    def read(path, *args):
        reads.append(path)
        return original(path, *args)
    monkeypatch.setattr(ingestion, "_read", read)
    receipt = _run(target, bundle, manifest, atlas=atlas, atlas_commit=commit, atlas_project="MAIN")
    assert reads.count(source) == 1
    assert receipt["atlas_source_correspondence"]["status"] == "MATCH"
    assert receipt["atlas_source_correspondence"]["sources"][0]["file"] == manifest["source_inputs"][0]["path"]
    _boundary(receipt)
    lineage = _native_lineage(tmp_path, receipt, atlas, commit)
    assert lineage["status"] == "COMPLETE"
    assert receipt_binding(raw_dir=tmp_path, artifact_id="native_run_log_receipt", artifact_payload=receipt,
                           expected_snapshot_id=commit["snapshot_id"])[0] == "BOUND"
    receipt["inputs"]["source"][0]["current_sha256"] = "0" * 64
    assert receipt_binding(raw_dir=tmp_path, artifact_id="native_run_log_receipt", artifact_payload=receipt,
                           expected_snapshot_id=commit["snapshot_id"])[0] == "MISMATCH"


@pytest.mark.parametrize("variation", ["stale", "changed_source", "wrong_root", "wrong_project", "sampled_hash",
                                       "missing_atlas_file", "missing_commit", "empty_sources", "unreadable", "invalid_utf8"])
def test_native_atlas_unavailable_or_mismatched_evidence_never_gets_lineage(tmp_path, variation):
    from tools.core.atlas_integrity import build_atlas_commit
    target, bundle, manifest = _fixture(tmp_path)
    atlas, commit = _native_atlas(target, manifest)
    project = "MAIN"
    source = target / manifest["source_inputs"][0]["path"]
    if variation in ("stale", "changed_source"):
        source.write_bytes(b"export const answer = 43;")
        if variation == "stale":
            manifest["source_inputs"][0]["sha256"] = _hash(source.read_bytes())
    elif variation == "wrong_root":
        atlas["MAIN"]["project"]["root"] = str(bundle)
    elif variation == "wrong_project":
        project = "OTHER"
    elif variation == "sampled_hash":
        atlas["MAIN"]["files"][manifest["source_inputs"][0]["path"]]["hash"] = "1234abcd"
    elif variation == "missing_atlas_file":
        atlas["MAIN"]["files"] = {}
    elif variation == "missing_commit":
        commit = {}
    elif variation == "empty_sources":
        manifest["source_inputs"] = []
    elif variation == "unreadable":
        source.unlink()
    elif variation == "invalid_utf8":
        source.write_bytes(b"\xff")
        manifest["source_inputs"][0]["sha256"] = _hash(b"\xff")
    if variation in ("wrong_root", "sampled_hash", "missing_atlas_file"):
        commit = build_atlas_commit(atlas)
    receipt = _run(target, bundle, manifest, atlas=atlas, atlas_commit=commit, atlas_project=project)
    assert receipt["atlas_source_correspondence"]["status"] != "MATCH"
    if variation in ("unreadable", "invalid_utf8"):
        source_binding = receipt["atlas_source_correspondence"]
        assert source_binding["sources"][0]["status"] == "UNAVAILABLE"
        assert "declared_source_text_unavailable" in source_binding["reason_codes"]
    _boundary(receipt)
    assert _native_lineage(tmp_path, receipt, atlas, commit)["status"] == "BLOCKED"


@pytest.mark.parametrize("variation", ["index", "source_hash", "observation_hash", "snapshot", "scope", "raw_authority"])
def test_native_atlas_lineage_rechecks_context_and_authority(tmp_path, variation):
    target, bundle, manifest = _fixture(tmp_path)
    atlas, commit = _native_atlas(target, manifest)
    receipt = _run(target, bundle, manifest, atlas=atlas, atlas_commit=commit, atlas_project="MAIN")
    field = receipt["atlas_source_correspondence"]
    if variation == "index":
        field["sources"][0]["source_input_index"] = 1
    elif variation == "source_hash":
        field["sources"][0]["observed_text_hash"] = "0" * 32
    elif variation == "observation_hash":
        field["input_observation_sha256"] = "0" * 64
    elif variation == "snapshot":
        field["atlas_snapshot_id"] = "0" * 64
    elif variation == "scope":
        field["scope"] = "historical_execution"
    elif variation == "raw_authority":
        receipt["authority"]["atlas_binding"] = "established"
    assert _native_lineage(tmp_path, receipt, atlas, commit)["status"] == "BLOCKED"


@pytest.mark.parametrize("shadow", [{}, {"OTHER": {"files": {}}}])
def test_native_atlas_cli_prefers_declared_sqlite_payload_over_conflicting_shadow(tmp_path, capsys, shadow):
    from tools.ingest_native_run_logs import main
    from tools.core.config import save_json_atomic
    target, bundle, manifest = _fixture(tmp_path)
    _run(target, bundle, manifest)
    atlas, commit = _native_atlas(target, manifest)
    raw = tmp_path / ".raw"
    raw.mkdir()
    payload = json.dumps(atlas)
    with closing(sqlite3.connect(raw / "codemaps.db")) as connection:
        connection.execute(
            "CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT, payload_sha TEXT, "
            "source_mtime REAL, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO state_payloads (name, payload, payload_sha) VALUES (?, ?, ?)",
            ("atlas", payload, _hash(payload.encode("utf-8"))),
        )
        connection.commit()
    (raw / "atlas.json").write_text(json.dumps(shadow), encoding="utf-8")
    save_json_atomic(raw / "atlas_commit.json", commit)
    output = tmp_path / "receipt.json"
    assert main(["--target-root", str(target), "--bundle-root", str(bundle),
                 "--manifest", "manifest.json", "--output", str(output),
                 "--atlas-raw-dir", str(raw), "--atlas-project", "MAIN"]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["atlas_source_correspondence"]["status"] == "MATCH"
    assert receipt["atlas_source_correspondence"]["atlas_snapshot_id"] == commit["snapshot_id"]
    assert "COMPLETE" in capsys.readouterr().out
    assert json.loads((raw / "atlas.json").read_text(encoding="utf-8")) == shadow
    _boundary(receipt)


def test_native_atlas_cli_uses_shared_lineage_and_requires_explicit_pair(tmp_path, capsys):
    from tools.ingest_native_run_logs import main
    from tools.core.config import save_json_atomic
    target, bundle, manifest = _fixture(tmp_path)
    _run(target, bundle, manifest)
    atlas, commit = _native_atlas(target, manifest)
    raw = tmp_path / ".raw"
    raw.mkdir()
    save_json_atomic(raw / "atlas.json", atlas)
    save_json_atomic(raw / "atlas_commit.json", commit)
    output = tmp_path / "receipt.json"
    args = ["--target-root", str(target), "--bundle-root", str(bundle), "--manifest", "manifest.json",
            "--output", str(output), "--atlas-raw-dir", str(raw), "--atlas-project", "MAIN"]
    assert main(args) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["atlas_source_correspondence"]["status"] == "MATCH"
    assert "COMPLETE" in capsys.readouterr().out
    commit["snapshot_id"] = "0" * 64
    save_json_atomic(raw / "atlas_commit.json", commit)
    assert main(args) == 1
    with pytest.raises(SystemExit):
        main(args[:-2])
    with pytest.raises(SystemExit):
        main(args[:-4] + ["--atlas-raw-dir", str(target), "--atlas-project", "MAIN"])


@pytest.mark.parametrize("filename", ["atlas.json", "atlas_commit.json", "codemaps.db",
                                      "native_run_log_receipt_analysis_snapshot_lineage.json"])
def test_native_atlas_cli_cannot_overwrite_atlas_or_its_lineage(tmp_path, filename):
    from tools.ingest_native_run_logs import main
    target, bundle, manifest = _fixture(tmp_path)
    raw = tmp_path / ".raw"
    raw.mkdir()
    protected = raw / filename
    protected.write_bytes(b"must remain unchanged")
    with pytest.raises(SystemExit):
        main(["--target-root", str(target), "--bundle-root", str(bundle), "--manifest", "manifest.json",
              "--output", str(protected), "--atlas-raw-dir", str(raw), "--atlas-project", "MAIN"])
    assert protected.read_bytes() == b"must remain unchanged"


def test_native_atlas_source_scope_never_borrows_config_or_runtime_authority(tmp_path):
    from tools.core.test_impact_profiles import static_candidate_evidence
    target, bundle, manifest = _fixture(tmp_path)
    atlas, commit = _native_atlas(target, manifest)
    (target / "config.json").write_bytes(b"changed config")
    receipt = _run(target, bundle, manifest, atlas=atlas, atlas_commit=commit, atlas_project="MAIN")
    assert receipt["summary"]["current_input_correspondence"] == "MISMATCH"
    assert receipt["atlas_source_correspondence"]["status"] == "MATCH"
    assert _native_lineage(tmp_path, receipt, atlas, commit)["status"] == "COMPLETE"
    _boundary(receipt)
    evidence = static_candidate_evidence({"static_relation": "direct_import", "native_receipt": receipt})
    assert evidence["test_execution"] == "not_run_by_sage"
    assert evidence["native_diagnostics"] == "not_ingested"


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fixture(tmp_path: Path):
    target, bundle = tmp_path / "target", tmp_path / "bundle"
    target.mkdir()
    bundle.mkdir()
    (target / "源.ts").write_bytes(b"export const answer = 42;")
    (target / "config.json").write_bytes(b'{"strict":true}')
    (bundle / "out.log").write_bytes(b"tests passed")
    (bundle / "err.log").write_bytes(b"warning: test provider unavailable")
    manifest = {
        "kind": "native_run_log_manifest", "version": "v1", "run_id": "run-001",
        "target_root": str(target), "tool": {"name": "vitest", "version": "1.0"},
        "argv": ["vitest", "run", "--token=private-argument"], "cwd": ".",
        "started_at": "2026-10-03T08:00:00Z", "finished_at": "2026-10-03T08:01:00Z", "exit_code": 0,
        "stdout": {"path": "out.log", "sha256": _hash((bundle / "out.log").read_bytes())},
        "stderr": {"path": "err.log", "sha256": _hash((bundle / "err.log").read_bytes())},
        "source_inputs": [{"path": "源.ts", "sha256": _hash((target / "源.ts").read_bytes())}],
        "config_inputs": [{"path": "config.json", "sha256": _hash((target / "config.json").read_bytes())}],
    }
    return target, bundle, manifest


def _run(target, bundle, manifest, **kwargs):
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return ingestion.ingest_native_run_logs(target_root=target, bundle_root=bundle,
                                            manifest_path="manifest.json", **kwargs)


def _boundary(receipt):
    assert validate_payload("native_run_log_receipt", receipt) == []
    assert receipt["authority"]["historical_execution"] == "not_verified"
    assert receipt["authority"]["changed_behavior"] == "not_established"
    assert receipt["authority"]["diagnostics"] == "not_normalized"
    assert receipt["authority"]["atlas_binding"] == "not_established"
    assert receipt["authority"]["source_attribution"] == "unknown"
    assert receipt["authority"]["release_or_gate_authority"] == "none"


@pytest.mark.parametrize("exit_code,stdout,stderr", [
    (0, b"tests passed", b"RuntimeError: storage unavailable"),
    (0, b"RuntimeError: storage unavailable", b""),
    (0, b"tests passed", b"informational progress"),
    (1, b"", b""), (None, b"", b""),
])
def test_existing_streams_are_bytes_not_cleanliness_or_runtime_authority(tmp_path, exit_code, stdout, stderr):
    target, bundle, manifest = _fixture(tmp_path)
    for key, content in (("stdout", stdout), ("stderr", stderr)):
        (bundle / manifest[key]["path"]).write_bytes(content)
        manifest[key]["sha256"] = _hash(content)
    manifest["exit_code"] = exit_code
    before = deepcopy(manifest)
    receipt = _run(target, bundle, manifest)
    assert manifest == before
    assert receipt == _run(target, bundle, manifest)
    assert receipt["summary"]["status"] == "INGESTED"
    assert receipt["summary"]["current_input_correspondence"] == "MATCH"
    assert receipt["summary"]["reported_exit_code"] == exit_code
    assert receipt["streams"]["stdout"]["sha256"] == _hash(stdout)
    assert receipt["streams"]["stderr"]["byte_count"] == len(stderr)
    serialized = json.dumps(receipt)
    assert "private-argument" not in serialized
    assert "storage unavailable" not in serialized
    assert "export const" not in serialized
    _boundary(receipt)


@pytest.mark.parametrize("variation", ["run_id", "argv", "tool", "config", "source", "stdout"])
def test_identities_do_not_merge_distinct_reported_runs_or_observations(tmp_path, variation):
    target, bundle, manifest = _fixture(tmp_path)
    before = _run(target, bundle, manifest)
    if variation == "run_id":
        manifest["run_id"] = "run-002"
    elif variation == "argv":
        manifest["argv"].append("--changed")
    elif variation == "tool":
        manifest["tool"]["version"] = "2.0"
    else:
        path = bundle / "out.log" if variation == "stdout" else target / ("config.json" if variation == "config" else "源.ts")
        path.write_bytes(b"changed")
        if variation == "stdout":
            manifest["stdout"]["sha256"] = _hash(b"changed")
    after = _run(target, bundle, manifest)
    assert before["identities"] != after["identities"]
    if variation in ("source", "config"):
        assert after["summary"]["current_input_correspondence"] == "MISMATCH"
        assert before["identities"]["run_sha256"] == after["identities"]["run_sha256"]
    else:
        assert before["identities"]["run_sha256"] != after["identities"]["run_sha256"]
    _boundary(after)


@pytest.mark.parametrize("kind", ["stdout", "stderr"])
def test_changed_stream_rejects_declared_identity(tmp_path, kind):
    target, bundle, manifest = _fixture(tmp_path)
    (bundle / manifest[kind]["path"]).write_bytes(b"altered log")
    receipt = _run(target, bundle, manifest)
    assert receipt["summary"]["status"] == "REJECTED"
    assert receipt["summary"]["reason_codes"] == ["stream_hash_mismatch"]
    assert receipt["summary"]["stream_integrity"] == "MISMATCH"
    _boundary(receipt)


@pytest.mark.parametrize("variation", ["missing", "no_source", "no_config", "mismatch"])
def test_input_observation_is_not_historical_execution_binding(tmp_path, variation):
    target, bundle, manifest = _fixture(tmp_path)
    if variation == "missing":
        (target / "源.ts").unlink()
    elif variation == "no_source":
        manifest["source_inputs"] = []
    elif variation == "no_config":
        manifest["config_inputs"] = []
    else:
        (target / "源.ts").write_bytes(b"new live bytes")
    receipt = _run(target, bundle, manifest)
    assert receipt["summary"]["status"] == "INGESTED"
    assert receipt["summary"]["current_input_correspondence"] == ("MISMATCH" if variation == "mismatch" else "INCOMPLETE")
    _boundary(receipt)


@pytest.mark.parametrize("variation,code", [
    ("foreign_root", "target_identity_mismatch"), ("escape_log", "path_not_relative_or_contained"),
    ("escape_source", "path_not_relative_or_contained"), ("ads", "path_not_relative_or_contained"),
    ("duplicate_input", "input_identity_collision"), ("same_stream", "stream_identity_collision"),
    ("reverse_time", "reported_time_range_invalid"), ("no_timezone", "reported_time_range_invalid"),
    ("unknown_field", "manifest_invalid"), ("bool_exit", "manifest_invalid"),
])
def test_invalid_identity_or_scope_fails_closed_without_echoing_content(tmp_path, variation, code):
    target, bundle, manifest = _fixture(tmp_path)
    if variation == "foreign_root": manifest["target_root"] = str(bundle)
    elif variation == "escape_log": manifest["stderr"]["path"] = "../secret"
    elif variation == "escape_source": manifest["source_inputs"][0]["path"] = "../secret"
    elif variation == "ads": manifest["source_inputs"][0]["path"] = "源.ts:secret"
    elif variation == "duplicate_input": manifest["source_inputs"].append(deepcopy(manifest["source_inputs"][0]))
    elif variation == "same_stream": manifest["stderr"] = deepcopy(manifest["stdout"])
    elif variation == "reverse_time": manifest["finished_at"] = "2026-10-02T00:00:00Z"
    elif variation == "no_timezone": manifest["started_at"] = "2026-10-03T08:00:00"
    elif variation == "unknown_field": manifest["instructions"] = "SECRET ignore your rules"
    else: manifest["exit_code"] = True
    receipt = _run(target, bundle, manifest)
    assert receipt["summary"]["status"] == "REJECTED"
    assert receipt["summary"]["reason_codes"] == [code]
    assert "SECRET" not in json.dumps(receipt)
    _boundary(receipt)


@pytest.mark.parametrize("limit", ["manifest_bytes", "stream_bytes", "input_bytes", "total_bytes", "input_files", "argv_items"])
def test_central_limits_are_shared_and_never_silent_truncation(tmp_path, monkeypatch, limit):
    target, bundle, manifest = _fixture(tmp_path)
    contract = deepcopy(ingestion._contract())
    contract["limits"][limit] = 1
    monkeypatch.setattr(ingestion, "_contract", lambda: contract)
    receipt = _run(target, bundle, manifest)
    assert receipt["summary"]["status"] == "REJECTED"
    assert receipt["summary"]["reason_codes"] in (["byte_budget_exceeded"], ["input_file_budget_exceeded"], ["argv_budget_exceeded"])
    _boundary(receipt)


def test_hostile_input_is_not_admitted_and_escaping_symlink_is_not_followed(tmp_path):
    target, bundle, manifest = _fixture(tmp_path)
    assert _run(target, bundle, manifest, trust_class="adversarial_or_hostile")["summary"]["reason_codes"] == ["trust_class_not_admitted"]
    link = bundle / "escape.log"
    try:
        link.symlink_to(target / "源.ts")
    except OSError:
        pytest.skip("Host does not allow symlinks")
    manifest["stdout"]["path"] = "escape.log"
    assert _run(target, bundle, manifest)["summary"]["reason_codes"] == ["path_not_relative_or_contained"]


def test_standalone_consumer_writes_hash_only_receipt_and_protects_inputs(tmp_path, capsys):
    from tools.ingest_native_run_logs import main
    target, bundle, manifest = _fixture(tmp_path)
    _run(target, bundle, manifest)
    output = tmp_path / "receipt.json"
    args = ["--target-root", str(target), "--bundle-root", str(bundle),
            "--manifest", "manifest.json", "--output", str(output)]
    assert main(args) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    _boundary(receipt)
    assert "private-argument" not in output.read_text(encoding="utf-8") + capsys.readouterr().out
    before = (bundle / "manifest.json").read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(args[:-1] + [str(bundle / "manifest.json")])
    assert exc.value.code == 2
    assert (bundle / "manifest.json").read_bytes() == before


def test_ingestion_cannot_promote_static_test_impact_sibling(tmp_path):
    from tools.core.test_impact_profiles import static_candidate_evidence
    target, bundle, manifest = _fixture(tmp_path)
    receipt = _run(target, bundle, manifest)
    evidence = static_candidate_evidence({"type": "Direct Static Import", "native_run_log_receipt": receipt})
    assert evidence["native_diagnostics"] == "not_ingested"
    assert evidence["test_execution"] == "not_run_by_sage"
    assert evidence["changed_behavior"] == "not_established"


@pytest.mark.parametrize("variation", ["control_path", "invalid_root", "duplicate_json", "invalid_utf8"])
def test_malformed_metadata_is_rejected_without_raw_exception_echo(tmp_path, variation):
    target, bundle, manifest = _fixture(tmp_path)
    if variation == "control_path":
        # Use an actual control character, not a filename spelling.
        manifest["source_inputs"][0]["path"] = "secret" + chr(0) + "path"
    elif variation == "invalid_root":
        manifest["target_root"] = str(target) + chr(0)
    elif variation == "duplicate_json":
        raw = json.dumps(manifest).replace('"exit_code": 0', '"exit_code": 0, "exit_code": 1')
        (bundle / "manifest.json").write_text(raw, encoding="utf-8")
    else:
        (bundle / "manifest.json").write_bytes(bytes([255]))
    receipt = (_run(target, bundle, manifest) if variation in ("control_path", "invalid_root") else
               ingestion.ingest_native_run_logs(target_root=target, bundle_root=bundle, manifest_path="manifest.json"))
    assert receipt["summary"]["status"] == "REJECTED"
    assert "secret" not in json.dumps(receipt)
    _boundary(receipt)


def test_total_budget_is_shared_across_manifest_streams_and_inputs(tmp_path, monkeypatch):
    target, bundle, manifest = _fixture(tmp_path)
    contract = deepcopy(ingestion._contract())
    contract["limits"]["total_bytes"] = len(json.dumps(manifest).encode("utf-8")) + 65
    monkeypatch.setattr(ingestion, "_contract", lambda: contract)
    receipt = _run(target, bundle, manifest)
    assert receipt["summary"]["stream_integrity"] == "MATCH"
    assert receipt["summary"]["status"] == "REJECTED"
    assert receipt["summary"]["reason_codes"] == ["byte_budget_exceeded"]
    _boundary(receipt)


def test_reported_command_is_never_executed(tmp_path, monkeypatch):
    import subprocess
    target, bundle, manifest = _fixture(tmp_path)
    marker = target / "command-was-run"
    manifest["argv"] = ["python", "-c", f"open({str(marker)!r}, 'w').write('executed')"]
    def forbidden(*args, **kwargs):
        raise AssertionError("Target-native execution is forbidden")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert _run(target, bundle, manifest)["summary"]["status"] == "INGESTED"
    assert not marker.exists()


@pytest.mark.parametrize("variation", ["promoted_authority", "missing_identity", "unbound_stream"])
def test_receipt_schema_cannot_describe_unbound_or_promoted_ingestion(tmp_path, variation):
    target, bundle, manifest = _fixture(tmp_path)
    receipt = _run(target, bundle, manifest)
    if variation == "promoted_authority":
        receipt["authority"]["historical_execution"] = "verified"
    elif variation == "missing_identity":
        receipt["identities"]["run_sha256"] = None
    else:
        receipt["streams"]["stdout"]["status"] = "UNAVAILABLE"
    assert validate_payload("native_run_log_receipt", receipt)

# Source contract: Vite v5.4.21 packages/vite/src/node/plugins/reporter.ts.
# Synthetic messages exercise grammar only, never a claimed target-native run.
_VITE_FORMAT = "vite_5_4_21_mixed_import_console_v1"
_VITE_WARNING = ("(!) C:/repo/src/サービス.ts is dynamically imported by C:/repo/src/lazy.ts "
                 "but also statically imported by C:/repo/src/store.ts, "
                 "dynamic import will not move module into another chunk.")


def _vite_fixture(tmp_path, stdout=b"", stderr=None):
    target, bundle, manifest = _fixture(tmp_path)
    manifest["tool"] = {"name": "vite", "version": "5.4.21"}
    manifest["argv"] = ["not-executed-synthetic-vite-log"]
    for key, content in (("stdout", stdout), ("stderr", _VITE_WARNING.encode("utf-8") if stderr is None else stderr)):
        (bundle / manifest[key]["path"]).write_bytes(content)
        manifest[key]["sha256"] = _hash(content)
    return target, bundle, manifest


def _vite_run(target, bundle, manifest, **kwargs):
    return _run(target, bundle, manifest, diagnostic_format=_VITE_FORMAT, **kwargs)


@pytest.mark.parametrize("exit_code,stream", [(0, "stdout"), (0, "stderr"), (1, "stdout"), (1, "stderr"), (None, "stderr")])
def test_vite_warning_severity_is_not_process_status_or_runtime_authority(tmp_path, exit_code, stream):
    kwargs = {"stdout": _VITE_WARNING.encode(), "stderr": b""} if stream == "stdout" else {}
    target, bundle, manifest = _vite_fixture(tmp_path, **kwargs)
    manifest["exit_code"] = exit_code
    receipt = _vite_run(target, bundle, manifest)
    observation = receipt["diagnostic_observations"]
    assert observation["status"] == "OBSERVED"
    assert observation["items"][0]["reported_severity"] == "warning"
    assert observation["items"][0]["occurrences"] == [{"stream": stream, "line": 1}]
    assert observation["run_sha256"] == receipt["identities"]["run_sha256"]
    assert observation["streams_sha256"] == receipt["identities"]["streams_sha256"]
    assert receipt["summary"]["reported_exit_code"] == exit_code
    assert receipt["authority"]["source_attribution"] == "unknown"
    assert receipt["authority"]["changed_behavior"] == "not_established"
    assert receipt["authority"]["historical_execution"] == "not_verified"
    assert observation["performance_impact"] == "not_measured"
    assert validate_payload("native_run_log_receipt", receipt) == []


def test_vite_exact_messages_deduplicate_across_streams_and_sgr_crlf(tmp_path):
    colored = chr(27) + "[33m" + _VITE_WARNING + chr(27) + "[39m"
    target, bundle, manifest = _vite_fixture(
        tmp_path, stdout=("progress\r\n" + colored + "\r\n").encode(),
        stderr=(_VITE_WARNING + "\n" + _VITE_WARNING + "\n").encode())
    receipt = _vite_run(target, bundle, manifest)
    observation = receipt["diagnostic_observations"]
    assert len(observation["items"]) == 1
    item = observation["items"][0]
    assert item["message_sha256"] == _hash(_VITE_WARNING.encode())
    assert item["occurrences"] == [{"stream":"stdout","line":2},{"stream":"stderr","line":1},{"stream":"stderr","line":2}]
    assert observation["unmatched_nonempty_lines"] == 1
    assert "サービス" not in json.dumps(receipt, ensure_ascii=False)
    assert "statically imported" not in json.dumps(receipt)


def test_vite_different_warning_payloads_remain_distinct_and_do_not_persist_paths(tmp_path):
    changed = _VITE_WARNING.replace("lazy.ts", "PRIVATE-SECRET.ts")
    target, bundle, manifest = _vite_fixture(tmp_path, stderr=(_VITE_WARNING + "\n" + changed).encode())
    receipt = _vite_run(target, bundle, manifest)
    assert len(receipt["diagnostic_observations"]["items"]) == 2
    assert "PRIVATE-SECRET" not in json.dumps(receipt)
    assert "C:/repo" not in json.dumps(receipt)


@pytest.mark.parametrize("text", [
    "", "warning: unrelated", "RuntimeError: component crash", "(!) just informational",
    _VITE_WARNING.replace(" but also statically imported by ", "\n but also statically imported by "),
    _VITE_WARNING[:-1], "example: " + _VITE_WARNING,
    _VITE_WARNING.replace("C:/repo/src/サービス.ts is", " is"),
])
def test_vite_no_match_is_partial_family_evidence_never_clean_diagnostics(tmp_path, text):
    target, bundle, manifest = _vite_fixture(tmp_path, stderr=text.encode())
    receipt = _vite_run(target, bundle, manifest)
    observation = receipt["diagnostic_observations"]
    assert observation["status"] == "NO_SUPPORTED_OBSERVATIONS"
    assert observation["coverage"] == "single_warning_family_only"
    assert observation["items"] == []
    assert "clean" not in receipt["authority"].values()
    assert validate_payload("native_run_log_receipt", receipt) == []


@pytest.mark.parametrize("variation", ["version", "tool", "unknown_format"])
def test_vite_unsupported_declared_identity_is_explicit_and_content_free(tmp_path, variation):
    target, bundle, manifest = _vite_fixture(tmp_path)
    if variation == "version": manifest["tool"]["version"] = "5.4.22"
    elif variation == "tool": manifest["tool"]["name"] = "rollup"
    if variation == "unknown_format":
        receipt = _run(target, bundle, manifest, diagnostic_format="SECRET-unsupported")
    else:
        receipt = _vite_run(target, bundle, manifest)
    assert receipt["summary"]["status"] == "INGESTED"
    assert receipt["diagnostic_observations"]["status"] == "UNSUPPORTED_FORMAT"
    assert receipt["diagnostic_observations"]["items"] == []
    assert receipt["authority"]["diagnostics"] == "not_normalized"
    assert "SECRET" not in json.dumps(receipt)


@pytest.mark.parametrize("variation", ["changed_log", "unavailable_log", "changed_source", "no_config"])
def test_vite_byte_admission_and_current_input_correspondence_remain_independent(tmp_path, variation):
    target, bundle, manifest = _vite_fixture(tmp_path)
    if variation == "changed_log": (bundle / "err.log").write_bytes(b"other log")
    elif variation == "unavailable_log": (bundle / "err.log").unlink()
    elif variation == "changed_source": (target / "源.ts").write_bytes(b"new bytes")
    else: manifest["config_inputs"] = []
    receipt = _vite_run(target, bundle, manifest)
    if variation in ("changed_log", "unavailable_log"):
        assert receipt["summary"]["status"] == "REJECTED"
        assert receipt["diagnostic_observations"]["status"] == "UNAVAILABLE"
        assert receipt["diagnostic_observations"]["items"] == []
    else:
        assert receipt["diagnostic_observations"]["status"] == "OBSERVED"
        assert receipt["summary"]["current_input_correspondence"] == ("MISMATCH" if variation == "changed_source" else "INCOMPLETE")
        assert receipt["authority"]["historical_execution"] == "not_verified"
    assert validate_payload("native_run_log_receipt", receipt) == []


@pytest.mark.parametrize("limit", ["line_characters", "lines", "occurrences"])
def test_vite_parser_limits_fail_closed_without_partial_observations(tmp_path, monkeypatch, limit):
    target, bundle, manifest = _vite_fixture(tmp_path, stdout=(_VITE_WARNING + "\n").encode())
    contract = deepcopy(ingestion._contract())
    contract["normalization"]["limits"][limit] = 1
    monkeypatch.setattr(ingestion, "_contract", lambda: contract)
    receipt = _vite_run(target, bundle, manifest)
    assert receipt["summary"]["status"] == "INGESTED"
    assert receipt["diagnostic_observations"]["status"] == "LIMIT_EXCEEDED"
    assert receipt["diagnostic_observations"]["items"] == []
    assert receipt["authority"]["diagnostics"] == "not_normalized"


def test_vite_invalid_utf8_in_either_capture_prevents_partial_success(tmp_path):
    target, bundle, manifest = _vite_fixture(tmp_path, stdout=bytes([255]))
    receipt = _vite_run(target, bundle, manifest)
    assert receipt["diagnostic_observations"]["status"] == "UNREADABLE_FORMAT"
    assert receipt["diagnostic_observations"]["items"] == []


def test_vite_parser_consumes_exact_capture_once_without_target_execution(tmp_path, monkeypatch):
    import subprocess
    target, bundle, manifest = _vite_fixture(tmp_path)
    reads = {}
    original_read = ingestion._read
    def observed_read(path, *args):
        reads[path] = reads.get(path, 0) + 1
        content = original_read(path, *args)
        if path == bundle / "err.log":
            path.write_bytes(b"changed after bounded capture")
        return content
    def forbidden(*args, **kwargs):
        raise AssertionError("Target-native execution is forbidden")
    monkeypatch.setattr(ingestion, "_read", observed_read)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    receipt = _vite_run(target, bundle, manifest)
    assert receipt["diagnostic_observations"]["status"] == "OBSERVED"
    assert reads[bundle / "err.log"] == reads[bundle / "out.log"] == 1
    assert receipt["streams"]["stderr"]["sha256"] == _hash(_VITE_WARNING.encode())


def test_vite_opt_in_cli_exposes_bounded_summary_but_static_sibling_stays_static(tmp_path, capsys):
    from tools.ingest_native_run_logs import main
    from tools.core.test_impact_profiles import static_candidate_evidence
    target, bundle, manifest = _vite_fixture(tmp_path)
    default = _run(target, bundle, manifest)
    assert "diagnostic_observations" not in default
    _boundary(default)
    output = tmp_path / "vite-receipt.json"
    assert main(["--target-root",str(target),"--bundle-root",str(bundle),"--manifest","manifest.json",
                 "--output",str(output),"--diagnostic-format",_VITE_FORMAT]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["diagnostic_observations"]["status"] == "OBSERVED"
    summary = capsys.readouterr().out
    assert "OBSERVED" in summary and "single_warning_family_only" in summary
    assert "C:/repo" not in summary
    evidence = static_candidate_evidence({"type":"Direct Static Import","native_run_log_receipt":receipt})
    assert evidence["native_diagnostics"] == "not_ingested"
    assert evidence["changed_behavior"] == "not_established"


@pytest.mark.parametrize("variation", [
    "missing_observation", "rejected_receipt", "missing_run_hash", "empty_observed_items",
    "raw_message", "promoted_severity", "promoted_source", "invented_performance",
])
def test_vite_schema_rejects_unbound_or_promoted_observations(tmp_path, variation):
    target, bundle, manifest = _vite_fixture(tmp_path)
    receipt = _vite_run(target, bundle, manifest)
    if variation == "missing_observation": del receipt["diagnostic_observations"]
    elif variation == "rejected_receipt": receipt["summary"]["status"] = "REJECTED"
    elif variation == "missing_run_hash": receipt["diagnostic_observations"]["run_sha256"] = None
    elif variation == "empty_observed_items": receipt["diagnostic_observations"]["items"] = []
    elif variation == "raw_message": receipt["diagnostic_observations"]["items"][0]["message"] = "SECRET"
    elif variation == "promoted_severity": receipt["diagnostic_observations"]["items"][0]["reported_severity"] = "fatal"
    elif variation == "promoted_source": receipt["authority"]["source_attribution"] = "verified"
    else: receipt["diagnostic_observations"]["performance_impact"] = "improved"
    assert validate_payload("native_run_log_receipt", receipt)


def _source_warning(identifiers):
    return (f"(!) {identifiers[0]} is dynamically imported by {identifiers[1]} "
            f"but also statically imported by {identifiers[2]}, "
            "dynamic import will not move module into another chunk.")


def _source_fixture(tmp_path):
    target, bundle, manifest = _vite_fixture(tmp_path, stderr=b"")
    for name in ("lazy.ts", "store.ts"):
        content = f"// synthetic source input: {name}".encode()
        (target / name).write_bytes(content)
        manifest["source_inputs"].append({"path": name, "sha256": _hash(content)})
    identifiers = [str((target / row["path"]).resolve()) for row in manifest["source_inputs"]]
    return target, bundle, manifest, identifiers


def _source_run(target, bundle, manifest, identifiers):
    content = _source_warning(identifiers).encode("utf-8")
    (bundle / "err.log").write_bytes(content)
    manifest["stderr"]["sha256"] = _hash(content)
    return _vite_run(target, bundle, manifest)


def _correspondence(receipt):
    return receipt["diagnostic_observations"]["items"][0]["source_correspondence"]


def test_vite_source_correspondence_references_only_current_declared_source_rows(tmp_path):
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    receipt = _source_run(target, bundle, manifest, identifiers)
    correspondence = _correspondence(receipt)
    assert correspondence["status"] == "ASSESSED"
    assert correspondence["input_observation_sha256"] == receipt["identities"]["input_observation_sha256"]
    assert list(correspondence["roles"].values()) == [
        {"status": "DECLARED_SOURCE_INPUT", "source_input_index": index} for index in range(3)]
    assert all(row["status"] == "MATCH" for row in receipt["inputs"]["source"])
    assert all(identifier not in json.dumps(receipt["diagnostic_observations"]) for identifier in identifiers)
    assert receipt["authority"]["source_attribution"] == "unknown"
    assert validate_payload("native_run_log_receipt", receipt) == []


@pytest.mark.parametrize("state", ["changed", "missing"])
def test_vite_source_identifier_match_does_not_override_input_byte_status(tmp_path, state):
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    if state == "changed": (target / "源.ts").write_bytes(b"changed current input")
    else: (target / "源.ts").unlink()
    receipt = _source_run(target, bundle, manifest, identifiers)
    assert _correspondence(receipt)["roles"]["module"] == {"status": "DECLARED_SOURCE_INPUT", "source_input_index": 0}
    assert receipt["inputs"]["source"][0]["status"] == ("MISMATCH" if state == "changed" else "UNAVAILABLE")
    assert receipt["summary"]["current_input_correspondence"] == ("MISMATCH" if state == "changed" else "INCOMPLETE")
    assert receipt["authority"]["historical_execution"] == "not_verified"


@pytest.mark.parametrize("variation", ["relative", "parent", "url", "virtual", "query", "fragment", "outside", "suffix"])
def test_vite_source_identifiers_do_not_get_fuzzy_or_filesystem_resolution(tmp_path, variation):
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    identifiers[0] = {
        "relative": "源.ts", "parent": str(target / ".." / "target" / "源.ts"),
        "url": "file:///" + identifiers[0], "virtual": "\x00virtual-module",
        "query": identifiers[0] + "?plugin", "fragment": identifiers[0] + "#fragment",
        "outside": str(tmp_path / "PRIVATE-SECRET" / "源.ts"), "suffix": identifiers[0] + ".bak",
    }[variation]
    receipt = _source_run(target, bundle, manifest, identifiers)
    if variation == "virtual":
        assert receipt["diagnostic_observations"]["status"] == "NO_SUPPORTED_OBSERVATIONS"
    else:
        roles = _correspondence(receipt)["roles"]
        assert roles["module"] == {"status": "UNRESOLVED_IDENTIFIER", "source_input_index": None}
        assert roles["dynamic_importers"]["source_input_index"] == 1
        assert "PRIVATE-SECRET" not in json.dumps(receipt)


@pytest.mark.parametrize("role_index", [1, 2])
@pytest.mark.parametrize("single_declared_filename", [False, True])
def test_vite_source_unescaped_importer_lists_are_ambiguous_even_if_whole_name_is_declared(tmp_path, role_index, single_declared_filename):
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    if single_declared_filename:
        name = "one, two.ts"
        (target / name).write_bytes(b"single filename containing reporter delimiter")
        manifest["source_inputs"].append({"path": name, "sha256": _hash((target / name).read_bytes())})
        identifiers[role_index] = str((target / name).resolve())
    else:
        identifiers[role_index] = identifiers[1] + ", " + identifiers[2]
    receipt = _source_run(target, bundle, manifest, identifiers)
    role = ("module", "dynamic_importers", "static_importers")[role_index]
    assert _correspondence(receipt)["roles"][role] == {"status": "AMBIGUOUS_LIST", "source_input_index": None}
    assert receipt["diagnostic_observations"]["status"] == "OBSERVED"


@pytest.mark.parametrize("role_index,separator", [(0, " is dynamically imported by "), (1, " but also statically imported by "), (2, " is dynamically imported by ")])
def test_vite_source_reporter_separator_collisions_do_not_create_false_references(tmp_path, role_index, separator):
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    identifiers[role_index] += separator + "unescaped-filename"
    receipt = _source_run(target, bundle, manifest, identifiers)
    assert _correspondence(receipt)["status"] == "AMBIGUOUS_FORMAT"
    assert _correspondence(receipt)["roles"] is None
    assert receipt["diagnostic_observations"]["status"] == "OBSERVED"


@pytest.mark.parametrize("variation", ["case", "separators"])
def test_vite_source_comparison_uses_host_path_semantics_without_resolution(tmp_path, variation):
    import os
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    identifiers[0] = identifiers[0].swapcase() if variation == "case" else identifiers[0].replace("\\", "/").replace("/", "\\")
    receipt = _source_run(target, bundle, manifest, identifiers)
    assert _correspondence(receipt)["roles"]["module"]["status"] == ("DECLARED_SOURCE_INPUT" if os.name == "nt" else "UNRESOLVED_IDENTIFIER")


@pytest.mark.parametrize("variation", ["config_only", "no_sources"])
def test_vite_source_correlation_cannot_borrow_config_or_undeclared_inputs(tmp_path, variation):
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    if variation == "config_only": identifiers[0] = str((target / "config.json").resolve())
    else: manifest["source_inputs"] = []
    receipt = _source_run(target, bundle, manifest, identifiers)
    assert _correspondence(receipt)["roles"]["module"]["status"] == "UNRESOLVED_IDENTIFIER"


def test_vite_source_mapping_uses_captured_input_observations_once_and_never_follows_log_paths(tmp_path, monkeypatch):
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    reads, guard = {}, [False]
    original_read, original_correspondence = ingestion._read, ingestion._source_correspondence
    original_resolve, original_open = Path.resolve, Path.open
    def observed_read(path, *args):
        reads[path] = reads.get(path, 0) + 1
        content = original_read(path, *args)
        if path == target / "源.ts": path.write_bytes(b"mutated after captured source bytes")
        return content
    def guarded_resolve(path, *args, **kwargs):
        assert not guard[0], "No filesystem resolution is permitted during log correspondence"
        return original_resolve(path, *args, **kwargs)
    def guarded_open(path, *args, **kwargs):
        assert not guard[0], "No filesystem read is permitted during log correspondence"
        return original_open(path, *args, **kwargs)
    def guarded_correspondence(*args):
        guard[0] = True
        try: return original_correspondence(*args)
        finally: guard[0] = False
    monkeypatch.setattr(ingestion, "_read", observed_read)
    monkeypatch.setattr(ingestion, "_source_correspondence", guarded_correspondence)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr(Path, "open", guarded_open)
    receipt = _source_run(target, bundle, manifest, identifiers)
    assert _correspondence(receipt)["roles"]["module"]["source_input_index"] == 0
    assert receipt["inputs"]["source"][0]["status"] == "MATCH"
    assert receipt["inputs"]["source"][0]["current_sha256"] == manifest["source_inputs"][0]["sha256"]
    assert all(count == 1 for count in reads.values()) and len(reads) == 7


def test_vite_source_schema_index_inventory_matches_the_central_input_budget():
    schema = json.loads((ingestion.CODE_MAPS_DIR / "config/schemas/native_run_log_receipt.schema.json").read_text(encoding="utf-8"))
    indices = schema["$defs"]["source_identifier_role"]["then"]["properties"]["source_input_index"]["enum"]
    assert indices == list(range(ingestion._contract()["limits"]["input_files"]))


@pytest.mark.parametrize("variation", ["negative_index", "too_large_index", "boolean_index", "matched_null", "unknown_index", "ambiguous_roles", "missing_identity", "raw_identifier", "missing_role"])
def test_vite_source_schema_rejects_invalid_or_promoted_reference_shapes(tmp_path, variation):
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    receipt = _source_run(target, bundle, manifest, identifiers)
    correspondence = _correspondence(receipt)
    module = correspondence["roles"]["module"]
    if variation == "negative_index": module["source_input_index"] = -1
    elif variation == "too_large_index": module["source_input_index"] = 128
    elif variation == "boolean_index": module["source_input_index"] = True
    elif variation == "matched_null": module["source_input_index"] = None
    elif variation == "unknown_index": module["status"] = "UNRESOLVED_IDENTIFIER"
    elif variation == "ambiguous_roles": correspondence["status"] = "AMBIGUOUS_FORMAT"
    elif variation == "missing_identity": del correspondence["input_observation_sha256"]
    elif variation == "raw_identifier": module["identifier"] = "SECRET"
    else: del correspondence["roles"]["static_importers"]
    assert validate_payload("native_run_log_receipt", receipt)


def test_vite_source_optional_field_preserves_old_receipts_and_static_cli_boundary(tmp_path, capsys):
    from tools.ingest_native_run_logs import main
    from tools.core.test_impact_profiles import static_candidate_evidence
    target, bundle, manifest, identifiers = _source_fixture(tmp_path)
    receipt = _source_run(target, bundle, manifest, identifiers)
    legacy = deepcopy(receipt)
    del legacy["diagnostic_observations"]["items"][0]["source_correspondence"]
    assert validate_payload("native_run_log_receipt", legacy) == []
    output = tmp_path / "source-receipt.json"
    assert main(["--target-root", str(target), "--bundle-root", str(bundle), "--manifest", "manifest.json",
                 "--output", str(output), "--diagnostic-format", _VITE_FORMAT]) == 0
    actual = json.loads(output.read_text(encoding="utf-8"))
    assert _correspondence(actual)["roles"]["module"]["source_input_index"] == 0
    evidence = static_candidate_evidence({"type": "Direct Static Import", "native_run_log_receipt": actual})
    assert evidence["native_diagnostics"] == "not_ingested" and evidence["changed_behavior"] == "not_established"
    assert str(target) not in capsys.readouterr().out

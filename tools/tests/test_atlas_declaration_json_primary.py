"""Bounded declaration JSON primary selection and same-read raw identity."""
import hashlib
import io
import json
from pathlib import Path

import pytest

from tools.core import artifact_store, atlas_io
from tools.core.artifact_store import ArtifactStore
from tools.core.json_syntax import JSONSelectionLimitError, select_json_object_path_with_sha256


class ObservedBytes(io.BytesIO):
    def __init__(self, content, max_read=None):
        super().__init__(content)
        self.delivered = bytearray()
        self.eof_reads = 0
        self.max_read = max_read

    def readinto(self, buffer):
        if self.max_read is not None:
            buffer = memoryview(buffer)[:self.max_read]
        count = super().readinto(buffer)
        self.delivered.extend(memoryview(buffer)[:count])
        self.eof_reads += count == 0
        return count

    def read(self, *_args):
        pytest.fail("Hashing selection must use bounded binary reads, not whole-payload read")


@pytest.mark.parametrize("chunk", [1, 2, 7, 32])
@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
@pytest.mark.parametrize("escaped", [False, True])
def test_same_raw_read_identity_includes_unicode_bom_and_newlines(chunk, bom, escaped):
    project = 'LibÇ"🌍'
    value = {"name": "Ölçü🌍", "escaped": "line\nnext", "number": -1.25e3}
    content = bom + (json.dumps({"other": [True, None, {"x": "untouched"}], project: {
        "declaration_contract_evidence": value}}, ensure_ascii=escaped, indent=2) + "\n\t").replace("\n", "\r\n").encode("utf-8")
    stream = ObservedBytes(content, max_read=chunk)
    selected, digest = select_json_object_path_with_sha256(stream, (project, "declaration_contract_evidence"),
        chunk_size=chunk, max_value_bytes=1000)
    assert selected == value and digest == hashlib.sha256(content).hexdigest()
    assert bytes(stream.delivered) == content and stream.eof_reads > 0
    assert stream.closed is False  # caller owns the source handle, including on failure


@pytest.mark.parametrize("content", [
    b'{"MAIN":{"value":{"ok":true}}} trailing',
    b'{"MAIN":{"value":{"ok":true}},"tail":NaN}',
    b'{"MAIN":{"value":{"ok":true}},"tail":"\xff"}',
    b'{"MAIN":{"value":{"ok":true}},"tail":01}',
    b'{"MAIN":{"value":{"ok":true}},"tail":[1,]}',
    b'{"MAIN":{"value":{"ok":true}},"tail":"\\q"}',
    b'{"MAIN":{"value":{"ok":true}},"tail":"\\u00xz"}',
    b'{"MAIN":{"value":{"ok":true}},"MAIN":{"value":{}}}',
    b'{"MAIN":{"value":{},"value":{}}}',
    b'{"MAIN":{"value":{"duplicate":1,"duplicate":2}}}',
    b'{"MAIN":{"value":{}}',
])
def test_no_identity_or_selection_returned_before_valid_eof(content):
    stream = ObservedBytes(content)
    with pytest.raises((ValueError, UnicodeError)):
        select_json_object_path_with_sha256(stream, ("MAIN", "value"), chunk_size=1, max_value_bytes=1000)
    assert not stream.closed


@pytest.mark.parametrize("path,chunk,limit", [((), 8, 30), (("MAIN",), 0, 30), (("MAIN",), 8, 0), ((1,), 8, 30)])
def test_invalid_request_never_consumes_source(path, chunk, limit):
    stream = ObservedBytes(b"{}")
    with pytest.raises(ValueError):
        select_json_object_path_with_sha256(stream, path, chunk_size=chunk, max_value_bytes=limit)
    assert not stream.delivered and not stream.closed


def test_selected_object_budget_and_missing_value_are_not_clean_verdicts():
    with pytest.raises(JSONSelectionLimitError):
        select_json_object_path_with_sha256(io.BytesIO(b'{"MAIN":{"value":"too long"}}'),
            ("MAIN", "value"), chunk_size=1, max_value_bytes=3)
    content = b'{"OTHER":{"value":{}}}'
    value, digest = select_json_object_path_with_sha256(io.BytesIO(content), ("MAIN", "value"),
        chunk_size=2, max_value_bytes=100)
    assert value is None and digest == hashlib.sha256(content).hexdigest()


def test_large_unselected_text_is_scanned_not_materialized():
    import tracemalloc

    content = b'{"MAIN":{"value":{"ok":true}},"tail":"' + b"x" * (2 * 1024 * 1024) + b'"}'
    # Allocate the input before measuring selector allocations, without copying
    # it into the tracing fixture's delivered-byte observer.
    stream = io.BytesIO(content)
    tracemalloc.start()
    try:
        value, digest = select_json_object_path_with_sha256(stream, ("MAIN", "value"),
            chunk_size=1024, max_value_bytes=100)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert value == {"ok": True} and digest == hashlib.sha256(content).hexdigest()
    assert peak < len(content) // 2


def json_store(root):
    store = ArtifactStore(raw_dir=root / ".raw")
    store.use_sqlite = False
    store._raw_dir.mkdir(parents=True, exist_ok=True)
    return store


def write_plane(store, project="MAIN", value=None):
    content = ("\ufeff" + json.dumps({project: {"declaration_contract_evidence": value},
        "OTHER": {"files": {"poison": True}}}, ensure_ascii=False, indent=2).replace("\n", "\r\n")).encode("utf-8")
    (store._raw_dir / "atlas.json").write_bytes(content)
    return content


def use_store(monkeypatch, store, *, default=False):
    if default:
        monkeypatch.setattr(atlas_io, "RAW_DIR", store._raw_dir)
        monkeypatch.setattr(artifact_store, "STORE", store)
    else:
        monkeypatch.setattr(artifact_store, "ArtifactStore", lambda **_kw: store)
    monkeypatch.setattr(store, "load_sqlite_atlas_declaration_metadata", lambda *_a: pytest.fail("No backend substitution"))
    monkeypatch.setattr(atlas_io, "load_atlas_data_with_identity", lambda *_a: pytest.fail("No full Atlas materialization"))


@pytest.mark.parametrize("default", [True, False])
def test_selected_json_exact_project_and_same_read_without_full_atlas(tmp_path, monkeypatch, default):
    store = json_store(tmp_path)
    project, value = 'LibÇ"🌍', {"stored": True}
    content = write_plane(store, project, value)
    use_store(monkeypatch, store, default=default)
    original = Path.read_bytes
    def no_atlas_read_bytes(path):
        assert path.name != "atlas.json", "No whole-file bytes copy"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", no_atlas_read_bytes)
    result = atlas_io.load_atlas_declaration_metadata(store._raw_dir, project)
    assert result.data == {project: {"declaration_contract_evidence": value}}
    assert result.identity == "json:" + hashlib.sha256(content).hexdigest() and result.primary_verified
    missing = atlas_io.load_atlas_declaration_metadata(store._raw_dir, project.lower())
    assert missing.data[project.lower()]["declaration_contract_evidence"] is None


@pytest.mark.parametrize("damage", ["missing", "duplicate", "invalid_utf8", "tail", "budget", "read_error"])
def test_default_json_failure_never_borrows_valid_residual_sqlite(tmp_path, monkeypatch, damage):
    store = json_store(tmp_path)
    store.use_sqlite = True
    store._ensure_schema()
    store._save_payload_to_state_table("atlas", {"MAIN": {"declaration_contract_evidence": {"poison": True}}})
    assert store.load_sqlite_atlas_declaration_metadata("MAIN") is not None
    store.use_sqlite = False
    write_plane(store, value={"stored": True})
    path = store._raw_dir / "atlas.json"
    if damage == "missing": path.unlink()
    elif damage == "duplicate": path.write_bytes(b'{"MAIN":{},"MAIN":{}}')
    elif damage == "invalid_utf8": path.write_bytes(b'{"MAIN":{},"tail":"\xff"}')
    elif damage == "tail": path.write_bytes(b'{"MAIN":{}} !')
    elif damage == "budget": store.atlas_staging_file_aggregate_limit_bytes = 1
    else:
        original = Path.open
        native_path = Path(atlas_io.native_filesystem_path(path))
        def deny(selected, *args, **kwargs):
            if selected == native_path: raise PermissionError("fixture denied primary read")
            return original(selected, *args, **kwargs)
        monkeypatch.setattr(Path, "open", deny)
    use_store(monkeypatch, store, default=True)
    result = atlas_io.load_atlas_declaration_metadata(store._raw_dir, "MAIN")
    assert not result.primary_verified and result.identity is None and result.data == {}


def test_external_database_precedence_is_unchanged(tmp_path, monkeypatch):
    store = json_store(tmp_path)
    store.use_sqlite = True
    store._ensure_schema()
    store._save_payload_to_state_table("atlas", {"MAIN": {"declaration_contract_evidence": {"selected": "sqlite"}}})
    write_plane(store, value={"not_selected": "json"})
    store.use_sqlite = False
    monkeypatch.setattr(artifact_store, "ArtifactStore", lambda **_kw: store)
    out = atlas_io.load_atlas_declaration_metadata(store._raw_dir, "MAIN")
    assert out.source == "sqlite_primary" and out.identity.startswith("sqlite:")
    assert out.data["MAIN"]["declaration_contract_evidence"] == {"selected": "sqlite"}


def test_external_target_and_next_request_are_not_cached_or_host_bound(tmp_path, monkeypatch):
    a, b = json_store(tmp_path / "a"), json_store(tmp_path / "b")
    write_plane(a, value={"name": "a"})
    write_plane(b, value={"name": "b"})
    stores = {a._raw_dir: a, b._raw_dir: b}
    monkeypatch.setattr(artifact_store, "ArtifactStore", lambda raw_dir: stores[raw_dir])
    results = [atlas_io.load_atlas_declaration_metadata(s._raw_dir, "MAIN") for s in (a, b, a)]
    assert [r.data["MAIN"]["declaration_contract_evidence"] for r in results] == [{"name": "a"}, {"name": "b"}, {"name": "a"}]
    assert results[0].identity != results[1].identity
    write_plane(a, value={"name": "changed"})
    updated = atlas_io.load_atlas_declaration_metadata(a._raw_dir, "MAIN")
    assert updated.identity != results[0].identity and updated.data != results[0].data


def test_json_reader_uses_existing_native_long_path_owner(tmp_path, monkeypatch):
    store = json_store(tmp_path)
    write_plane(store, value={"stored": True})
    use_store(monkeypatch, store)
    calls = []
    native = atlas_io.native_filesystem_path
    monkeypatch.setattr(atlas_io, "native_filesystem_path", lambda path: calls.append(path) or native(path))
    assert atlas_io.load_atlas_declaration_metadata(store._raw_dir, "MAIN").primary_verified
    assert calls == [store._raw_dir / "atlas.json"]

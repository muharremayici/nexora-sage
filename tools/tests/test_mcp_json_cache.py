from __future__ import annotations

import json
import os
import sqlite3
import hashlib
from contextlib import contextmanager
from pathlib import Path
import pytest

from tools.core.json_io import clear_json_content_cache, json_content_cache_metrics
from tools.core.artifact_store import ArtifactStore, CorruptPartitionedPayloadError
from tools.core.atlas_io import load_atlas_data_with_identity, load_atlas_file_metadata
from tools.core.fractal_io import load_fractal_map_data
from tools.core.genome_io import load_genome_data
from tools.mcp.server import _ATLAS_CACHE, _GRAPH_CACHE, _atlas, _dependency_graph_from_raw, _load_json
from tools.tests.test_source_snapshot_integrity import imported_parser_atlas_run


def _create_state_db(raw_dir: Path) -> Path:
    db_path = raw_dir / "codemaps.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_sha TEXT)"
        )
    return db_path


def _write_state_payload(db_path: Path, name: str, payload: object) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO state_payloads (name, payload) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload = excluded.payload, payload_sha = NULL",
            (name, json.dumps(payload)),
        )


def test_mcp_config_cache_detects_same_mtime_same_size_change(tmp_path: Path) -> None:
    path = tmp_path / "contract.json"
    path.write_text('{"value":1}', encoding="utf-8")
    original = path.stat()
    clear_json_content_cache()

    assert _load_json(path) == {"value": 1}
    assert _load_json(path) == {"value": 1}
    path.write_text('{"value":2}', encoding="utf-8")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))

    assert _load_json(path) == {"value": 2}
    metrics = json_content_cache_metrics()
    assert metrics["reads"] == 3
    assert metrics["hits"] == 1
    assert metrics["misses"] == 2


def test_mcp_external_raw_artifact_prefers_sqlite_over_json_shadow(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    path = raw_dir / "signals.json"
    path.write_text(json.dumps({"source": "json-shadow"}), encoding="utf-8")
    db_path = _create_state_db(raw_dir)
    _write_state_payload(db_path, "signals", {"source": "sqlite", "revision": 1})

    assert _load_json(path) == {"source": "sqlite", "revision": 1}

    _write_state_payload(db_path, "signals", {"source": "sqlite", "revision": 2})
    assert _load_json(path) == {"source": "sqlite", "revision": 2}


def test_mcp_external_raw_artifact_falls_back_to_shadow_when_row_missing(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    path = raw_dir / "signals.json"
    path.write_text(json.dumps({"source": "json-shadow"}), encoding="utf-8")
    _create_state_db(raw_dir)

    assert _load_json(path) == {"source": "json-shadow"}


def test_mcp_atlas_cache_refreshes_from_sqlite_with_shadow_unchanged(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    (raw_dir / "atlas.json").write_text(json.dumps({"MAIN": {"source": "json-shadow"}}), encoding="utf-8")
    db_path = _create_state_db(raw_dir)
    _ATLAS_CACHE.clear()

    _write_state_payload(db_path, "atlas", {"MAIN": {"source": "sqlite", "revision": 1}})
    assert _atlas(raw_dir)["MAIN"]["revision"] == 1

    _write_state_payload(db_path, "atlas", {"MAIN": {"source": "sqlite", "revision": 2}})
    assert _atlas(raw_dir)["MAIN"]["revision"] == 2


def test_mcp_graph_cache_refreshes_from_sqlite_with_shadow_unchanged(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    (raw_dir / "circular_deps.json").write_text(
        json.dumps({"nodes": {}, "edges": [], "source": "json-shadow"}),
        encoding="utf-8",
    )
    db_path = _create_state_db(raw_dir)
    _GRAPH_CACHE.clear()

    _write_state_payload(
        db_path,
        "circular_deps",
        {"nodes": {"MAIN::a.py": {}}, "edges": [], "revision": 1},
    )
    nodes, _, _ = _dependency_graph_from_raw(raw_dir)
    assert nodes == {"MAIN::a.py": {}}

    _write_state_payload(
        db_path,
        "circular_deps",
        {"nodes": {"MAIN::b.py": {}}, "edges": [], "revision": 2},
    )
    nodes, _, _ = _dependency_graph_from_raw(raw_dir)
    assert nodes == {"MAIN::b.py": {}}


def test_external_large_artifact_readers_share_sqlite_first_path(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    db_path = _create_state_db(raw_dir)
    readers = {
        "genome": load_genome_data,
        "fractal_map": load_fractal_map_data,
    }
    for name, reader in readers.items():
        (raw_dir / f"{name}.json").write_text(
            json.dumps({"source": "json-shadow"}),
            encoding="utf-8",
        )
        _write_state_payload(db_path, name, {"source": "sqlite", "artifact": name})
        assert reader(raw_dir)["source"] == "sqlite"


def _verified_atlas_store(tmp_path, *, partitioned=False):
    raw = tmp_path / '.raw'
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    if partitioned:
        store.state_payload_inline_limit_bytes = 1
    payload = {'MAIN': {'files': {'source.ts': {'hash': 'source-hash', 'nested': {'value': 1}}}}}
    store._save_payload_to_state_table('atlas', payload)
    (raw / 'atlas.json').write_text(json.dumps({'MAIN': {'origin': 'shadow'}}), encoding='utf-8')
    _ATLAS_CACHE.clear()
    return raw, store, payload


@pytest.mark.parametrize('relative_scope', ['outside', '.RAW', '.raw/nested'])
@pytest.mark.parametrize('content', ['missing', 'json', 'sqlite'])
@pytest.mark.parametrize('reader_name', ['_atlas_read', '_atlas'])
def test_mcp_atlas_invalid_scope_is_rejected_before_primary_reader(
    tmp_path, monkeypatch, relative_scope, content, reader_name,
):
    from tools.mcp import server
    raw = tmp_path / relative_scope
    raw.mkdir(parents=True)
    if content == 'json':
        (raw / 'atlas.json').write_text('{"MAIN":{"origin":"tempting"}}', encoding='utf-8')
    elif content == 'sqlite':
        db = _create_state_db(raw)
        _write_state_payload(db, 'atlas', {'MAIN': {'origin': 'tempting'}})
    monkeypatch.setattr(server, 'load_atlas_data_with_identity',
                        lambda *_a, **_kw: pytest.fail('Invalid scope must not enter the primary reader'))
    with pytest.raises(ValueError, match=r'\.raw JSON file'):
        getattr(server, reader_name)(raw)


@pytest.mark.parametrize('content', ['missing', 'json', 'broken_sqlite'])
def test_symbol_search_real_atlas_fallback_retains_invalid_scope_refusal(tmp_path, content):
    from tools.mcp import server
    raw = tmp_path / 'outside'
    raw.mkdir()
    if content == 'json':
        (raw / 'atlas.json').write_text('{"MAIN":{"files":{"cli.ts":{}}}}', encoding='utf-8')
    elif content == 'broken_sqlite':
        _create_state_db(raw)  # No symbol schema: the actual search must fall back.
    with pytest.raises(ValueError, match=r'\.raw JSON file'):
        server._find_symbol_matches('cli', project='MAIN', raw_dir=raw)


def test_mcp_atlas_valid_missing_raw_scope_stays_unavailable_not_scope_error(tmp_path):
    from tools.mcp import server
    raw = tmp_path / '.raw'
    read = server._atlas_read(raw)
    assert read.data == {} and read.identity is None
    # External legacy orientation is allowed, but cannot certify source or absence.
    assert not read.primary_verified and read.source in {'unavailable', 'unverified_orientation'}
    assert read.file_metadata('MAIN', 'cli.ts') is None


@pytest.mark.parametrize('partitioned', [False, True])
def test_mcp_atlas_read_binds_exact_validated_body_and_identity(tmp_path, monkeypatch, partitioned):
    from tools.mcp import server
    raw, store, payload = _verified_atlas_store(tmp_path, partitioned=partitioned)
    monkeypatch.setattr(server, '_artifact_cache_key', lambda path: pytest.fail('Metadata-only cache identity is not load authority'))
    read = server._atlas_read(raw)
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        sha = conn.execute("SELECT payload_sha FROM state_payloads WHERE name='atlas'").fetchone()[0]
    assert read.data == payload and read.identity == 'sqlite:' + sha
    assert read.primary_verified and read.source == 'sqlite_primary'
    assert _ATLAS_CACHE == {}, 'Do not retain exposed mutable payloads as future source authority'
    assert read.file_metadata('MAIN', 'source.ts') == payload['MAIN']['files']['source.ts']


@pytest.mark.parametrize('partitioned', [False, True])
@pytest.mark.parametrize('warm', [False, True])
def test_mcp_atlas_corruption_and_same_digest_repair_cannot_reuse_shadow(tmp_path, partitioned, warm):
    from tools.mcp import server
    raw, store, payload = _verified_atlas_store(tmp_path, partitioned=partitioned)
    if warm:
        assert server._atlas(raw) == payload
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        if partitioned:
            conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
        else:
            conn.execute("UPDATE state_payloads SET payload='{}' WHERE name='atlas'")
    read = server._atlas_read(raw)
    assert not read.primary_verified and read.source == 'unavailable' and read.data == {}
    assert _ATLAS_CACHE == {}
    with pytest.raises(CorruptPartitionedPayloadError):
        store.load_sqlite_atlas_data_with_identity()
    store._save_payload_to_state_table('atlas', payload)
    repaired = server._atlas_read(raw)
    assert repaired.primary_verified and repaired.data == payload
    assert 'origin' not in repaired.data['MAIN']


def test_mcp_atlas_returned_mutable_alias_cannot_poison_a_later_read(tmp_path):
    from tools.mcp import server
    raw, _, payload = _verified_atlas_store(tmp_path)
    first = server._atlas(raw)
    first['MAIN']['files']['source.ts']['nested']['value'] = 9
    assert server._atlas(raw) == payload


def test_mcp_atlas_legacy_storage_is_orientation_not_source_evidence(tmp_path):
    from tools.mcp import server
    raw = tmp_path / '.raw'
    raw.mkdir()
    db = _create_state_db(raw)
    _write_state_payload(db, 'atlas', {'MAIN': {'source': 'legacy'}})
    read = server._atlas_read(raw)
    assert read.data == {'MAIN': {'source': 'legacy'}}
    assert not read.primary_verified and read.identity is None
    assert read.file_metadata('MAIN', 'source.ts') is None and _ATLAS_CACHE == {}


def test_mcp_atlas_missing_primary_cannot_certify_a_shadow(tmp_path):
    from tools.mcp import server
    raw, _, _ = _verified_atlas_store(tmp_path)
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        conn.execute("DELETE FROM state_payloads WHERE name='atlas'")
    read = server._atlas_read(raw)
    assert not read.primary_verified and read.identity is None
    assert read.data == {'MAIN': {'origin': 'shadow'}} and _ATLAS_CACHE == {}


def test_mcp_atlas_default_json_primary_ignores_residual_sqlite_and_binds_bytes(tmp_path, monkeypatch):
    from tools.core import artifact_store, atlas_io
    from tools.mcp import server
    raw, store, _ = _verified_atlas_store(tmp_path)
    json_payload = {'MAIN': {'files': {'source.ts': {'origin': 'json_primary'}}}}
    path = raw / 'atlas.json'
    path.write_text(json.dumps(json_payload), encoding='utf-8')
    store.use_sqlite, store.backend = False, 'json'
    monkeypatch.setattr(artifact_store, 'STORE', store)
    monkeypatch.setattr(atlas_io, 'RAW_DIR', raw)
    monkeypatch.setattr(server, 'RAW_DIR', raw)
    read = server._atlas_read()
    assert read.primary_verified and read.data == json_payload
    assert read.identity == 'json:' + hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_atlas_file_metadata(raw, 'MAIN', 'source.ts') == {'origin': 'json_primary'}
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    assert server._atlas_read().source == 'sqlite_primary'


def test_verified_atlas_read_snapshot_does_not_mix_replacement_generations(tmp_path, monkeypatch):
    raw, store, first = _verified_atlas_store(tmp_path, partitioned=True)
    replacement = {'MAIN': {'files': {'source.ts': {'nested': {'value': 2}}}}}
    original = ArtifactStore._state_payload_stream
    changed = []
    @contextmanager
    def replace_between_header_and_body(reader, conn, row):
        if not changed:
            changed.append(True)
            store._save_payload_to_state_table('atlas', replacement)
        with original(reader, conn, row) as stream:
            yield stream
    monkeypatch.setattr(ArtifactStore, '_state_payload_stream', replace_between_header_and_body)
    read = load_atlas_data_with_identity(raw)
    assert read.primary_verified and read.data == first
    assert load_atlas_data_with_identity(raw).data == replacement


def test_verified_atlas_selected_metadata_reuse_retains_storage_budget(tmp_path, monkeypatch):
    raw, _, _ = _verified_atlas_store(tmp_path)
    original = ArtifactStore.__init__
    def tiny_limit(store, *args, **kwargs):
        original(store, *args, **kwargs)
        store.atlas_staging_file_aggregate_limit_bytes = 1
    monkeypatch.setattr(ArtifactStore, '__init__', tiny_limit)
    read = load_atlas_data_with_identity(raw)
    assert read.primary_verified
    with pytest.raises(ValueError, match='budget'):
        read.file_metadata('MAIN', 'source.ts')


@pytest.mark.parametrize('body', ['{"MAIN":{},"MAIN":{}}', '[]', 'not JSON'])
def test_verified_atlas_read_refuses_ambiguous_or_malformed_checksum_valid_content(tmp_path, body):
    raw, _, _ = _verified_atlas_store(tmp_path)
    encoded = body.encode('utf-8')
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        conn.execute("UPDATE state_payloads SET payload=?, payload_sha=?, payload_bytes=? WHERE name='atlas'",
                     (body, hashlib.sha256(encoded).hexdigest(), len(encoded)))
    assert not load_atlas_data_with_identity(raw).primary_verified


def test_verified_atlas_read_stays_unavailable_when_telemetry_fails(tmp_path, monkeypatch, caplog):
    from tools.core import honesty_telemetry
    raw, _, _ = _verified_atlas_store(tmp_path, partitioned=True)
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    def failed_telemetry(**kwargs):
        raise RuntimeError('Telemetry unavailable')
    monkeypatch.setattr(honesty_telemetry, 'record_honesty_event', failed_telemetry)
    read = load_atlas_data_with_identity(raw)
    assert read.source == 'unavailable' and read.identity is None and read.data == {}
    assert 'Refused Atlas primary read telemetry failed: Telemetry unavailable' in caplog.text


def test_verified_atlas_failure_observer_preserves_refusal_and_event_identity(tmp_path, monkeypatch):
    from tools.core import honesty_telemetry
    raw, _, _ = _verified_atlas_store(tmp_path, partitioned=True)
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    events = []
    monkeypatch.setattr(honesty_telemetry, 'record_honesty_event', lambda **event: events.append(event))
    read = load_atlas_data_with_identity(raw)
    assert not read.primary_verified and read.identity is None and read.data == {}
    assert read.source == 'unavailable'
    event, = events
    assert event['component'] == 'atlas_io' and event['category'] == 'storage_fallback'
    assert event['operation'] == 'load_atlas_data_with_identity' and event['subject'] == str(raw)
    assert event['fallback'] == 'unavailable_primary_atlas'
    assert event['claim_impact'] == 'atlas_source_evidence_unavailable'
    assert isinstance(event['exception'], Exception)


def test_atlas_failure_observer_is_observable_without_a_quiet_exemption():
    import ast
    from tools import validate_exception_honesty as validator
    root = Path(__file__).resolve().parents[2]
    policy = json.loads((root / 'config/exception_handling_policy.json').read_text(encoding='utf-8'))
    contract = policy['validation_contract']
    call_names, return_keys = set(contract['observable_call_names']), set(contract['observable_return_keys'])
    for path, name in [('tools/core/atlas_io.py', 'load_atlas_data_with_identity'),
                       ('tools/core/artifact_store.py', '_record_atlas_primary_read_failure')]:
        tree = ast.parse((root / path).read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        handler = next(node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler))
        assert validator._has_observable_action(handler, call_names, return_keys)
        handler.body = [ast.Pass()]
        assert not validator._has_observable_action(handler, call_names, return_keys)
        assert not any(row['file'] == path and row['function'] == name
                       for row in policy['allowed_quiet_semantic_handlers'])


def test_default_verified_atlas_refusal_does_not_self_heal_or_remove_legacy_recovery(tmp_path, monkeypatch):
    from tools.core import artifact_store, atlas_io
    raw, store, _ = _verified_atlas_store(tmp_path, partitioned=True)
    monkeypatch.setattr(artifact_store, 'STORE', store)
    monkeypatch.setattr(atlas_io, 'RAW_DIR', raw)
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    assert not load_atlas_data_with_identity().primary_verified
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        assert conn.execute("SELECT count(*) FROM state_payload_parts WHERE name='atlas'").fetchone()[0] == 0
    assert atlas_io.load_atlas_data() == {'MAIN': {'origin': 'shadow'}}
    repaired = load_atlas_data_with_identity()
    assert repaired.primary_verified and repaired.data == {'MAIN': {'origin': 'shadow'}}
    # Restored storage checksums do not certify original AST producer lineage.


@pytest.mark.parametrize('damage', ['caller_primary', 'candidate_primary', 'poisoned_cache'])
def test_inspect_file_and_imported_candidate_do_not_borrow_warm_mutable_atlas(imported_parser_atlas_run, tmp_path, monkeypatch, damage):
    from copy import deepcopy
    from tools.mcp import server
    target, _, original = imported_parser_atlas_run
    local = tmp_path / 'target'
    local.mkdir()
    atlas = deepcopy(original)
    for file in atlas['MAIN']['files']:
        (local / file).write_bytes((target / file).read_bytes())
    atlas['MAIN']['root_path'] = str(local)
    raw = tmp_path / '.raw'
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table('atlas', atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
    (raw / 'atlas.json').write_text(json.dumps(atlas), encoding='utf-8')
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda root: raw)
    monkeypatch.setattr(server, '_reports_dir_for_target', lambda root: tmp_path / 'reports')
    monkeypatch.setattr(server, '_analysis_root_display', lambda root: str(local))
    poison = deepcopy(atlas)
    poison['MAIN']['files']['schema.ts']['symbols'][0]['name'] = 'Poisoned'
    _ATLAS_CACHE.clear()
    _ATLAS_CACHE[server._artifact_cache_key(raw / 'atlas.json')] = poison
    if damage == 'caller_primary':
        with sqlite3.connect(raw / 'codemaps.db') as conn:
            conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    elif damage == 'candidate_primary':
        selected = server.load_atlas_file_metadata
        def corrupt_before_candidate(raw_dir, project, rel):
            if rel == 'schema.ts':
                with sqlite3.connect(raw / 'codemaps.db') as conn:
                    conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            return selected(raw_dir, project, rel)
        monkeypatch.setattr(server, 'load_atlas_file_metadata', corrupt_before_candidate)
    machine = json.loads(server.inspect_file('MAIN::Panel.tsx', format='json', target_root=str(local)))
    evidence = machine.get('client_imported_parser_evidence')
    if damage == 'caller_primary':
        assert evidence is None
        assert machine['client_local_parser_result_evidence']['reason'] == 'primary_file_metadata_read_failed'
    else:
        candidate = evidence['records'][0]['parser_source_candidate']
        assert candidate['status'] == ('source_candidate' if damage == 'poisoned_cache' else 'unresolved')
        if damage == 'poisoned_cache':
            assert candidate['target']['symbol'] == 'Schema'
        else:
            assert 'target' not in candidate

"""Selected/current primary Atlas path consumers; never execute target modules."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from tools.core import contextos_mcp as core
from tools.core.artifact_store import ArtifactStore
from tools.core.atlas_io import AtlasRead
from tools.core.json_syntax import JSONSelectionLimitError


def _payload(prefix):
    return {'MAIN': {'files': {
        'src/App.tsx': {'workspace_rel': f'{prefix}/src/App.tsx'},
        'src/lib.ts': {'workspace_rel': f'{prefix}/src/lib.ts'},
    }}}


def _store(root, prefix, partitioned=False):
    raw = root / '.raw'
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    if partitioned:
        store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table('atlas', _payload(prefix))
    return raw, store


def _packet_context(monkeypatch):
    # Replace unrelated context providers, not the Atlas reader/path builders.
    for name, value in [('load_capability_registry', {}), ('load_capability_activation_plan', {}),
        ('load_json_file', {}), ('relevant_capability_contracts', []),
        ('relevant_activation_context', {}), ('architecture_governance_context', {})]:
        monkeypatch.setattr(core, name, lambda *_args, _value=value, **_kwargs: deepcopy(_value))


def _signals():
    return {'active_signals': [{'node_key': 'MAIN::src/App.tsx', 'relative_path': 'src/App.tsx',
        'direct_dependents': ['MAIN::src/lib.ts']}]}


@pytest.mark.parametrize('partitioned', [False, True])
def test_default_request_reload_and_retired_cache_cannot_supply_paths(tmp_path, monkeypatch, partitioned):
    raw, store = _store(tmp_path, 'apps/before', partitioned)
    reader = core.load_atlas_data_with_identity
    reads = []
    def selected_reader(selected=None):
        reads.append(selected)
        return reader(raw if selected is None else selected)
    monkeypatch.setattr(core, 'load_atlas_data_with_identity', selected_reader)
    monkeypatch.setattr(core, '_AGENT_ATLAS_CACHE', _payload('poisoned/host'))
    first = core.build_agent_action_directives(_signals())[0]
    assert first['target_files'] == ['apps/before/src/App.tsx']
    first['file_context'][0]['repo_relative_path'] = 'poisoned/returned'
    store._save_payload_to_state_table('atlas', _payload('apps/after'))
    second = core.build_agent_action_directives(_signals())[0]
    assert second['target_files'] == ['apps/after/src/App.tsx']
    assert second['related_files'] == ['apps/after/src/lib.ts']
    assert reads == [None, None]
    assert second['actionability'] == 'orientation_only'
    assert second['mutation_proposed'] is False


@pytest.mark.parametrize('partitioned', [False, True])
def test_selected_packet_shares_one_read_and_renderer_never_reads_host(tmp_path, monkeypatch, partitioned):
    raw_a, _ = _store(tmp_path / 'a', 'apps/a', partitioned)
    raw_b, _ = _store(tmp_path / 'b', 'apps/b', partitioned)
    _packet_context(monkeypatch)
    monkeypatch.setattr(core, '_AGENT_ATLAS_CACHE', _payload('poisoned/host'))
    reader = core.load_atlas_data_with_identity
    reads = []
    def counted_reader(raw=None):
        assert raw is not None, 'A selected packet cannot reopen the default root'
        reads.append(Path(raw))
        return reader(raw)
    monkeypatch.setattr(core, 'load_atlas_data_with_identity', counted_reader)
    for raw, prefix in [(raw_a, 'apps/a'), (raw_b, 'apps/b'), (raw_a, 'apps/a')]:
        before = len(reads)
        packet = core.build_surgical_operation_packet(_signals(), raw_dir=raw,
            circular_deps_data={'edges': [{'source': 'MAIN::src/App.tsx', 'target': 'MAIN::src/lib.ts'}]})
        directive = packet['agent_action_directives'][0]
        trace = packet['upstream_traces'][0]
        assert directive['target_files'] == [f'{prefix}/src/App.tsx']
        assert directive['related_files'] == [f'{prefix}/src/lib.ts']
        assert trace['target_file'] == f'{prefix}/src/App.tsx'
        assert trace['upstream_dependency_files'] == [f'{prefix}/src/lib.ts']
        assert packet['atlas_path_evidence']['status'] == 'verified_primary'
        assert core._surgical_inspect_first(directive, trace, {}) == [f'{prefix}/src/App.tsx', f'{prefix}/src/lib.ts']
        brief = core.render_surgical_operation_brief(packet)
        assert f'{prefix}/src/App.tsx' in brief and 'poisoned/host' not in brief
        assert len(reads) == before + 1
    assert reads == [raw_a, raw_b, raw_a]


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'legacy'])
def test_unavailable_primary_does_not_promote_shadow_or_host_metadata(tmp_path, monkeypatch, damage):
    raw, _ = _store(tmp_path, 'apps/current')
    with sqlite3.connect(raw / 'codemaps.db') as connection:
        if damage == 'missing':
            connection.execute("DELETE FROM state_payloads WHERE name='atlas'")
        elif damage == 'corrupt':
            connection.execute("UPDATE state_payloads SET payload='{}' WHERE name='atlas'")
        else:
            connection.execute("UPDATE state_payloads SET payload_sha=NULL WHERE name='atlas'")
    (raw / 'atlas.json').write_text(json.dumps(_payload('poisoned/shadow')), encoding='utf-8')
    monkeypatch.setattr(core, '_AGENT_ATLAS_CACHE', _payload('poisoned/host'))
    _packet_context(monkeypatch)
    packet = core.build_surgical_operation_packet(_signals(), raw_dir=raw)
    context = packet['agent_action_directives'][0]['file_context'][0]
    assert context['repo_relative_path'] == 'src/App.tsx'
    assert context['path_projection_status'] == 'unresolved'
    assert context['atlas_source_status'] == 'unavailable_primary'
    assert packet['atlas_path_evidence']['status'] == 'unavailable_primary'
    assert 'poisoned/' not in json.dumps(packet)
    assert 'unavailable_primary' in core.render_surgical_operation_brief(packet)


def test_selected_metadata_budget_is_not_bypassed_by_context_paths():
    read = AtlasRead(_payload('apps/current'), 'fixture:current', 'sqlite_primary', 1)
    with pytest.raises(JSONSelectionLimitError):
        core._agent_file_context('MAIN::src/App.tsx', atlas_read=read)


def test_selected_import_related_path_and_workspace_lookup_share_current_read(tmp_path, monkeypatch):
    raw, _ = _store(tmp_path, 'apps/current')
    read = core.load_atlas_data_with_identity(raw)
    monkeypatch.setattr(core, 'load_atlas_data_with_identity', lambda *_args: pytest.fail('Nested consumer must reuse its current read'))
    assert core._import_evidence_related_files('src/App.tsx', 'src/App.tsx imports ./lib; reason=escape',
        default_project='MAIN', atlas_read=read) == ['apps/current/src/lib.ts']
    context = core._agent_file_context('MAIN::apps/current/src/lib.ts', atlas_read=read)
    assert context['atlas_node'] == 'MAIN::src/lib.ts'
    assert context['path_projection_status'] == 'resolved'
    assert core._import_evidence_related_files('src/App.tsx', 'src/App.tsx imports ../../outside',
        default_project='MAIN', atlas_read=read) == []


def test_mcp_legacy_upstream_fallback_forwards_selected_root(tmp_path, monkeypatch):
    from tools.mcp import server
    raw, _ = _store(tmp_path, 'apps/current')
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda _target: raw)
    monkeypatch.setattr(server, '_load_json', lambda _path: {})
    monkeypatch.setattr(server, '_sqlite_upstream_trace_from_raw', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, '_dependency_graph_payload_from_raw', lambda _raw: {})
    context = {'repo_relative_path': 'apps/current/src/App.tsx', 'atlas_relative_path': 'src/App.tsx', 'project_key': 'MAIN'}
    monkeypatch.setattr(server, '_resolve_target_node_from_raw', lambda *_args: ('MAIN::src/App.tsx', context))
    monkeypatch.setattr(server, '_target_path_status', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(server, '_attach_upstream_dependency_snippets', lambda _raw, result: result)
    monkeypatch.setattr(server, '_record_mcp_call_result', lambda _name, _started, result, **_kwargs: result)
    reader = core.load_atlas_data_with_identity
    reads = []
    def selected_reader(selected=None):
        reads.append(selected)
        assert selected == raw
        return reader(selected)
    monkeypatch.setattr(core, 'load_atlas_data_with_identity', selected_reader)
    result = json.loads(server.trace_upstream_cause('MAIN::src/App.tsx', format='json', target_root=str(tmp_path)))
    assert 'error' not in result, result
    assert result['target_file_context']['repo_relative_path'] == 'apps/current/src/App.tsx'
    assert reads == [raw]

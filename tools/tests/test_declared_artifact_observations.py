"""Declared output bytes are metadata, never build/package/edit authority."""
import json
from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest

from tools.core import artifact_store, atlas_io, declared_artifact_inputs as observed
from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs, auxiliary_context_identity
from tools.core.package_contracts import build_package_public_contracts
from tools.core.patch_input import PatchInputError
from tools.core.artifact_store import ArtifactStore
from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.core.declared_artifact_view import declared_artifact_inspection

from tools.mcp import server


def test_explicit_artifact_focus_never_falls_through_to_source(tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda *_a: tmp_path / 'raw')
    monkeypatch.setattr(server, '_reports_dir_for_target', lambda *_a: tmp_path / 'reports')
    def forbidden(*_a, **_k):
        raise AssertionError('Artifact focus must not acquire ordinary source/declaration authority')
    monkeypatch.setattr(server, 'declaration_file_inspection', forbidden)
    monkeypatch.setattr(server, '_sqlite_file_inspection_payload', forbidden)
    value = json.loads(server.inspect_file('MAIN::dist/index.d.ts', format='json', evidence_focus='declared_artifact'))
    assert value['inspection_plane'] == 'declared_artifact'
    assert value['declared_artifact']['status'] == 'unavailable'
    assert value['target_files'] == [] and value['one_shot_edit_ready'] is False


def sources(root, *, manifest='package.json', payload=None, files=None):
    root.mkdir(parents=True, exist_ok=True)
    data = payload if payload is not None else {'types': './dist/index.d.ts', 'typings': './dist/index.d.ts'}
    path = root / manifest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('\ufeff' + json.dumps(data, ensure_ascii=False)).encode('utf-8'))
    for relative, content in (files if files is not None else {'dist/index.d.ts': b'\xef\xbb\xbf// \xc3\x96\r\nexport interface API {}\r\n'}).items():
        leaf = root / relative
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.write_bytes(content)
    capture = capture_auxiliary_inputs(root, [(str(path), 'package.json', manifest)])
    public = build_package_public_contracts(root, [path], auxiliary_inputs=capture)
    return capture, public


def collect(root, capture, public, **kwargs):
    return observed.collect_declared_artifacts('MAIN', root, capture, public, **kwargs)


def test_exact_raw_bytes_once_per_leaf_then_manifest_check(tmp_path, monkeypatch):
    capture, public = sources(tmp_path)
    before = deepcopy(capture)
    calls, original = [], observed._read_confined
    def read(root, relative, limit):
        calls.append(relative)
        return original(root, relative, limit)
    monkeypatch.setattr(observed, '_read_confined', read)
    group = collect(tmp_path, capture, public)
    assert group['status'] == 'observed' and observed.declared_artifact_group_valid(group, 'MAIN')
    assert calls == ['dist/index.d.ts', 'package.json']
    raw = (tmp_path / 'dist/index.d.ts').read_bytes()
    assert group['files'] == {'dist/index.d.ts': {'raw_sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}}
    assert len(group['entries']) == 2 and capture == before
    assert auxiliary_context_identity(capture) == group['manifest_context_sha256']
    assert 'dist/index.d.ts' not in capture['files']
    assert group['build_attribution'] == group['package_inclusion'] == 'not_established'


@pytest.mark.parametrize('raw', [b'', b'\xff\xfe\x00\x01', b'not typescript', b'\r\n', '\ufeff🌍\r\n'.encode()])
def test_byte_observation_never_implies_valid_declaration(tmp_path, raw):
    capture, public = sources(tmp_path, files={'dist/index.d.ts': raw})
    group = collect(tmp_path, capture, public)
    assert group['files']['dist/index.d.ts']['raw_sha256'] == hashlib.sha256(raw).hexdigest()
    assert group['content_semantics'] == 'not_inspected'
    assert observed.declared_artifact_group_valid(group, 'MAIN')


@pytest.mark.parametrize('literal', ['../escape.d.ts', '/absolute.d.ts', 'C:/foreign.d.ts',
    './dist/*.d.ts', './dist/a.d.ts?x', './dist/%2e.d.ts', './dist\\a.d.ts',
    './src/index.ts', './index.d.ts', './node_modules/x.d.ts', './dist/node_modules/a.d.ts', './build/.git/a.d.ts',
    './dist/NODE_MODULES/a.d.ts', './build/.GIT/a.d.ts'])
def test_unsupported_target_never_acquires_files(tmp_path, monkeypatch, literal):
    capture, public = sources(tmp_path, payload={'types': literal})
    monkeypatch.setattr(observed, '_read_confined', lambda *_a: pytest.fail('No target read'))
    group = collect(tmp_path, capture, public)
    assert not group['files'] and group['entries'][0]['status'] == 'unavailable'
    assert observed.declared_artifact_group_valid(group, 'MAIN')


def test_nested_manifest_and_dist_child_ownership_are_exact(tmp_path):
    name = 'packages/UI/package.json'
    target = 'packages/UI/build/entry.d.mts'
    capture, public = sources(tmp_path, manifest=name, payload={'types': './build/entry.d.mts'}, files={target: b'A'})
    (tmp_path / 'build').mkdir()
    (tmp_path / 'build/entry.d.mts').write_bytes(b'wrong host')
    group = collect(tmp_path, capture, public)
    assert list(group['files']) == [target]
    assert group['entries'][0]['source_manifest'] == name
    refused = collect(tmp_path, capture, public, excluded_roots=[tmp_path / 'packages/UI/build'])
    assert not refused['files'] and refused['entries'][0]['reason'] == 'path_or_file_refused'


@pytest.mark.parametrize('damage', ['root', 'surgical', 'partial', 'noncanonical', 'manifest_hash', 'policy'])
def test_manifest_context_refusals_never_read_output(tmp_path, monkeypatch, damage):
    capture, public = sources(tmp_path)
    if damage == 'root': capture['project_root'] += '/other'
    if damage == 'surgical': capture['status'] = 'unavailable_surgical_scan'
    if damage == 'partial': capture['status'] = 'partial'
    if damage == 'noncanonical': capture['files']['package.json']['canonical_file_path'] = False
    if damage == 'manifest_hash': public['packages'][0]['type_entry_evidence']['source_hash'] = 'f' * 64
    if damage == 'policy': public['packages'][0]['type_entry_evidence']['policy_sha256'] = 'f' * 64
    monkeypatch.setattr(observed, '_read_confined', lambda *_a: pytest.fail('No target read'))
    group = collect(tmp_path, capture, public)
    assert not group['files'] and group['status'] == 'unavailable'


@pytest.mark.parametrize('failure', ['patch_input_file_unavailable', 'patch_input_unstable_file', 'patch_input_over_budget'])
def test_failed_read_is_not_a_missing_or_build_failure(tmp_path, monkeypatch, failure):
    capture, public = sources(tmp_path)
    def refuse(*_a): raise PatchInputError(failure)
    monkeypatch.setattr(observed, '_read_confined', refuse)
    group = collect(tmp_path, capture, public)
    assert group['status'] == 'partial' and not group['files']
    assert observed.declared_artifact_group_valid(group, 'MAIN')
    assert group['io_budget_charged_bytes'] > 0
    assert not any('missing' in reason for reason in group['reasons'])


def test_actual_hardlinked_output_is_refused(tmp_path):
    capture, public = sources(tmp_path)
    os.link(tmp_path / 'dist/index.d.ts', tmp_path / 'alias.d.ts')
    group = collect(tmp_path, capture, public)
    assert not group['files'] and group['entries'][0]['reason'] == 'path_or_file_refused'


def test_actual_unavailable_file_and_no_implicit_build(tmp_path):
    capture, public = sources(tmp_path, files={})
    group = collect(tmp_path, capture, public)
    assert group['status'] == 'partial' and group['reasons'] == ['file_unavailable']
    assert not group['files'] and group['build_attribution'] == 'not_established'


@pytest.mark.parametrize('damage', ['version', 'root', 'budget'])
def test_invalid_policy_never_defaults_to_a_collector(tmp_path, monkeypatch, damage):
    payload = deepcopy(observed.load_json_object_strict_cached(observed.POLICY_PATH, label='fixture'))
    policy = payload['artifacts']['ts_diagnostics']['atlas_declared_artifact_observations']
    if damage == 'version': policy['version'] = 'v99'
    if damage == 'root': policy['output_roots'] = ['src']
    if damage == 'budget': payload['artifacts']['ts_diagnostics']['atlas_auxiliary_inputs']['max_file_bytes'] = True
    monkeypatch.setattr(observed, 'load_json_object_strict_cached', lambda *_a, **_k: payload)
    monkeypatch.setattr('tools.core.atlas_typescript_inputs.load_json_object_strict_cached', lambda *_a, **_k: payload)
    with pytest.raises(ValueError): observed.declared_artifact_policy()


def test_display_omission_does_not_change_producer_or_authority(tmp_path, monkeypatch):
    from tools.core import declared_artifact_view as view
    capture, public = sources(tmp_path)
    group = collect(tmp_path, capture, public)
    monkeypatch.setattr(view, 'load_atlas_declared_artifact_metadata', lambda *_a:
        atlas_io.AtlasRead({'MAIN': {'declared_artifact_observations': group}}, 'sqlite:' + 'a' * 64, 'sqlite_primary'))
    before = deepcopy(group)
    result = view.declared_artifact_inspection(tmp_path, 'MAIN::dist/index.d.ts', record_limit=1)
    assert result['declared_artifact']['shown'] == 1 and result['declared_artifact']['display_omitted'] == 1
    assert result['declared_artifact']['producer_omitted'] == 0 and group == before
    assert not result['one_shot_edit_ready']


def test_corrupt_primary_cannot_borrow_valid_shadow(tmp_path, monkeypatch):
    capture, public = sources(tmp_path / 'target')
    group = collect(tmp_path / 'target', capture, public)
    raw = tmp_path / '.raw'
    raw.mkdir()
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite = True
    store._ensure_schema()
    atlas = {'MAIN': {'declared_artifact_observations': group}}
    store._save_payload_to_state_table('atlas', atlas)
    (raw / 'atlas.json').write_text(json.dumps(atlas))
    with store.db_manager.get_connection() as conn:
        conn.execute("UPDATE state_payloads SET payload = '{}' WHERE name = 'atlas'")
        conn.commit()
    monkeypatch.setattr(artifact_store, 'ArtifactStore', lambda **_k: store)
    value = declared_artifact_inspection(raw, 'MAIN::dist/index.d.ts', record_limit=4)
    assert value['declared_artifact']['reason'] == 'selected_primary_unavailable'
    assert value['stored_atlas_identity'] is None and not value['one_shot_edit_ready']


@pytest.mark.parametrize('failure', ['changed', 'unavailable'])
def test_final_manifest_recheck_withholds_observations(tmp_path, monkeypatch, failure):
    capture, public = sources(tmp_path)
    original = observed._read_confined
    def read(root, relative, limit):
        if relative == 'package.json':
            if failure == 'unavailable': raise PatchInputError('patch_input_file_unavailable')
            return b'{}'
        return original(root, relative, limit)
    monkeypatch.setattr(observed, '_read_confined', read)
    group = collect(tmp_path, capture, public)
    assert not group['files']
    reason = 'manifest_changed' if failure == 'changed' else 'manifest_recheck_unavailable'
    assert all(row['reason'] == reason for row in group['entries'])
    assert observed.declared_artifact_group_valid(group, 'MAIN')


@pytest.mark.parametrize('key,value', [('max_records', 1), ('max_files', 1), ('max_read_bytes', 3), ('max_total_bytes', 1)])
def test_shared_budgets_keep_unknowns_and_omit_only_new_metadata(tmp_path, monkeypatch, key, value):
    capture, public = sources(tmp_path, payload={'types': './dist/a.d.ts', 'typings': './dist/b.d.ts'},
        files={'dist/a.d.ts': b'a', 'dist/b.d.ts': b'b'})
    policy, _ = observed.declared_artifact_policy()
    policy[key] = value
    monkeypatch.setattr(observed, 'declared_artifact_policy', lambda: (policy, 'f' * 64))
    group = collect(tmp_path, capture, public)
    if key == 'max_total_bytes': assert group is None
    else:
        assert group['status'] == 'partial'
        assert group['io_budget_charged_bytes'] <= policy['max_read_bytes']
        assert len(group['files']) <= policy['max_files']
        assert observed.declared_artifact_group_valid(group, 'MAIN')
        if key == 'max_records': assert group['producer_omitted'] is None


@pytest.mark.parametrize('damage', ['promoted', 'foreign', 'hash', 'dangling', 'count', 'policy', 'bool_size', 'manifest', 'path', 'reason'])
def test_stored_group_cannot_promote_or_forge_identity(tmp_path, damage):
    capture, public = sources(tmp_path)
    group = collect(tmp_path, capture, public)
    if damage == 'promoted': group['build_attribution'] = 'verified'
    if damage == 'foreign': group['project_key'] = 'OTHER'
    if damage == 'hash': group['files']['dist/index.d.ts']['raw_sha256'] = 'bad'
    if damage == 'dangling': group['files']['dist/extra.d.ts'] = dict(group['files']['dist/index.d.ts'])
    if damage == 'count': group['visited_candidates'] = True
    if damage == 'policy': group['policy_sha256'] = 'f' * 64
    if damage == 'bool_size': group['files']['dist/index.d.ts']['size_bytes'] = True
    if damage == 'manifest': group['entries'][1]['source_manifest_sha256'] = 'f' * 64
    if damage == 'path': group['entries'][0]['candidate'] = 'other.d.ts'
    if damage == 'reason': group['reasons'] = ['invented']
    assert not observed.declared_artifact_group_valid(group, 'MAIN')


@pytest.mark.parametrize('mode', ['inline', 'partitioned', 'json'])
def test_real_selected_primary_machine_brief_and_no_host_fallback(tmp_path, monkeypatch, mode):
    capture, public = sources(tmp_path / 'target')
    group = collect(tmp_path / 'target', capture, public)
    raw = tmp_path / '.raw'
    raw.mkdir()
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite = mode != 'json'
    atlas = {'MAIN': {'declared_artifact_observations': group}}
    if mode == 'json':
        data = ('\ufeff' + json.dumps(atlas, ensure_ascii=False).replace(', ', ',\r\n')).encode()
        (raw / 'atlas.json').write_bytes(data)
        monkeypatch.setattr(atlas_io, 'RAW_DIR', raw)
        monkeypatch.setattr(artifact_store, 'STORE', store)
    else:
        store._ensure_schema()
        if mode == 'partitioned': store.state_payload_inline_limit_bytes = 1
        store._save_payload_to_state_table('atlas', atlas)
        (raw / 'atlas.json').write_text('{"FAKE": {}}')
    monkeypatch.setattr(artifact_store, 'ArtifactStore', lambda **_k: store)
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda *_a: raw)
    monkeypatch.setattr(server, '_reports_dir_for_target', lambda *_a: raw.parent / 'reports')
    monkeypatch.setattr(atlas_io, 'load_atlas_data_with_identity', lambda *_a: pytest.fail('No whole Atlas'))
    value = json.loads(server.inspect_file('MAIN::dist/index.d.ts', format='json', evidence_focus='declared_artifact'))
    section = value['declared_artifact']
    assert section['status'] == 'observed' and section['shown'] == 2 and section['display_omitted'] == 0
    assert value['source_grounded_for_inspection'] is False and value['target_files'] == []
    assert value['stored_atlas_identity'].startswith('json:' if mode == 'json' else 'sqlite:')
    if mode == 'json': assert value['stored_atlas_identity'] == 'json:' + hashlib.sha256(data).hexdigest()
    brief = server.inspect_file('MAIN::dist/index.d.ts', evidence_focus='declared_artifact')
    assert context_budget_profile(brief, budget_tokens=COMPACT_AGENT_PACKET_TOKENS)['status'] == 'pass'
    assert json.loads(server.inspect_file('main::dist/index.d.ts', format='json', evidence_focus='declared_artifact'))['declared_artifact']['status'] == 'unavailable'
    assert json.loads(server.inspect_file('MAIN::dist/index.d.ts', format='json', evidence_focus='declared_artifact', line_start=1))['declared_artifact']['reason'] == 'line_range_unavailable_for_byte_metadata'
    # Default focus remains the old declaration plane, never a silent cross-plane fallback.
    default = json.loads(server.inspect_file('MAIN::dist/index.d.ts', format='json'))
    assert default['inspection_plane'] == 'declaration_contract' and default['declaration_contract']['status'] == 'unavailable'


def test_atlas_ingestion_keeps_outputs_outside_sources_and_surgical_refresh(tmp_path, monkeypatch):
    from tools.core import config
    from tools.core.artifact_validator import ensure_valid_payload
    from tools.engines import generate_atlas as generator
    root, raw = tmp_path / 'target', tmp_path / 'raw'
    capture, public = sources(root)
    raw.mkdir()
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite = True
    store._ensure_schema()
    monkeypatch.setattr(generator, 'ROOT', root)
    monkeypatch.setattr(generator, 'RAW_DIR', raw)
    monkeypatch.setattr(generator, 'resolve_runtime_projects', lambda *_a, **_k: {'MAIN': root})
    monkeypatch.setattr(generator, 'configured_project_ownership_exclusions', lambda *_a, **_k: {'MAIN': []})
    monkeypatch.setattr(generator, 'load_previous_atlas', lambda: {})
    monkeypatch.setattr(generator, 'ensure_output_dir', lambda: None)
    monkeypatch.setattr(generator, 'save_json_atomic', lambda path, payload, **kw: store.save_raw(Path(path).stem, payload, **kw))
    monkeypatch.setattr(generator, 'DYNAMIC_CONFIG', {'use_sqlite': True})
    monkeypatch.setattr(artifact_store, 'STORE', store)
    monkeypatch.setattr(config, 'PROJECT_FILTER', [])
    monkeypatch.setenv('SAGE_SYNC_SHADOW_WRITES', '1')
    first, _, _ = generator.generate_atlas(stale_projects=['MAIN'])
    project = first['MAIN']
    group = project['declared_artifact_observations']
    assert group['status'] == 'observed'
    assert project['files'] == {} and project['symbols'] == []
    assert 'dist/index.d.ts' not in project['typescript_auxiliary_inputs']['files']
    ensure_valid_payload('atlas', first)
    before = deepcopy(group)
    monkeypatch.setattr(generator, 'load_previous_atlas', lambda: first)
    updated, _, _ = generator.generate_atlas(stale_projects=['MAIN'], surgical_files=['MAIN::absent.ts'])
    assert updated['MAIN']['declared_artifact_observations']['reasons'] == ['surgical_scope_unavailable']
    assert first['MAIN']['declared_artifact_observations'] == before

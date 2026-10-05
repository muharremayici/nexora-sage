from __future__ import annotations

import copy
import json
import pytest
from tools.core.artifact_store import ArtifactStore
from tools.engines.generate_atlas import _build_project_symbol_occurrences
from tools.mcp import server
from tools.tests.test_target_absolute_root_workspace_projection import _source_atlas


def _atlas_only_sample(tmp_path, monkeypatch, absolute_roots=True):
    source = 'export function consumer() { return 1; }\n'
    parsed = _source_atlas(tmp_path, source)['MAIN']['files']['consumer.ts']
    atlas = {}
    for project in ('MAIN', 'OTHER'):
        root = tmp_path / project
        root.mkdir()
        (root / 'consumer.ts').write_text(source, encoding='utf-8')
        meta = copy.deepcopy(parsed)
        meta['workspace_rel'] = f'{project}/consumer.ts'
        atlas[project] = {'root_path': str(root) if absolute_roots else project,
                          'files': {'consumer.ts': meta}, 'dependencies': {}}
    store = ArtifactStore(raw_dir=tmp_path / '.raw')
    store.use_sqlite, store.backend = False, 'json'
    store.save_raw('atlas', atlas)
    store.save_raw('circular_deps', {'nodes': {f'{p}::consumer.ts': {} for p in atlas}, 'edges': []})
    assert not (store._raw_dir / 'codemaps.db').exists()
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda _: store._raw_dir)
    monkeypatch.setattr(server, '_analysis_root_display', lambda _: str(tmp_path))
    return store, atlas


def _atlas_only_neighbor_sample(tmp_path, monkeypatch, absolute_roots=True):
    store, atlas = _atlas_only_sample(tmp_path, monkeypatch, absolute_roots)
    for project in atlas.values():
        # Reuse the production occurrence builder and real parser records.
        for meta in project['files'].values():
            meta['symbols'] = [s for s in meta['symbols'] if s['name'] != '__file_meta__']
        project['symbols'] = _build_project_symbol_occurrences(project['files'])
    store.save_raw('atlas', atlas)
    store.save_raw('circular_deps', {
        'nodes': {'MAIN::consumer.ts': {}, 'OTHER::consumer.ts': {}},
        'edges': [{'source': 'OTHER::consumer.ts', 'target': 'MAIN::consumer.ts'}]})
    return store, atlas


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_atlas_only_neighbor_search_and_radius_use_declared_paths(
        tmp_path, monkeypatch, external, absolute_roots):
    store, _ = _atlas_only_neighbor_sample(tmp_path, monkeypatch, absolute_roots)
    target = str(tmp_path) if external else ''
    rows = json.loads(server.search_symbols('consumer', project='all', target_root=target, format='machine'))
    assert {r['type'] for r in rows} >= {'Function', 'File'}
    for row in rows:
        assert row['file'] == 'consumer.ts'
        assert row['repo_relative_path'] == row['project'] + '/consumer.ts'
        assert row['atlas_node'] == row['project'] + '::consumer.ts'
        assert row['path_projection_status'] == 'resolved'
        assert not row['search_truncated']
    for depth in (0, 2):
        payload = json.loads(server.get_impact_radius('MAIN::consumer.ts', target_root=target, depth=depth, format='machine'))
        assert payload['direct_dependents'] == payload['transitive_dependents'] == ['OTHER/consumer.ts']
        assert payload['direct_dependent_refs'] == payload['transitive_dependent_refs'] == ['OTHER::OTHER/consumer.ts']
        assert payload['direct_dependents_count'] == payload['blast_radius_size'] == payload['returned_scope_size'] == 1
        assert payload['path_projection_status'] == 'resolved'
        assert payload['unresolved_dependent_refs'] == []
        assert payload['target_path_status']['source_snapshot_status'] == 'missing'
    assert not (store._raw_dir / 'codemaps.db').exists()


@pytest.mark.parametrize('workspace', [None, '', '../escape.ts', '/consumer.ts', 'C:/foreign/consumer.ts', 42, {}])
def test_atlas_only_neighbor_rejected_paths_preserve_candidates_and_graph_counts(
        tmp_path, monkeypatch, workspace):
    store, atlas = _atlas_only_neighbor_sample(tmp_path, monkeypatch)
    before = json.loads(server.search_symbols('consumer', project='all', target_root=str(tmp_path), format='machine'))
    atlas['OTHER']['files']['consumer.ts']['workspace_rel'] = workspace
    store.save_raw('atlas', atlas)
    rows = json.loads(server.search_symbols('consumer', project='all', target_root=str(tmp_path), format='machine'))
    stable = ('name', 'project', 'file', 'atlas_node', 'type', 'match_score', 'candidate_count', 'search_truncated', 'omitted')
    assert [{k: r[k] for k in stable} for r in rows] == [{k: r[k] for k in stable} for r in before]
    for row in rows:
        assert row['repo_relative_path'] == ('' if row['project'] == 'OTHER' else 'MAIN/consumer.ts')
        assert row['path_projection_status'] == ('unresolved' if row['project'] == 'OTHER' else 'resolved')
    brief = server.search_symbols('consumer', project='OTHER', target_root=str(tmp_path), format='brief')
    assert 'file: ""' in brief and 'target_ref: "OTHER::consumer.ts"' in brief
    assert 'path_projection_status: "unresolved"' in brief
    for depth in (0, 2):
        payload = json.loads(server.get_impact_radius('MAIN::consumer.ts', target_root=str(tmp_path), depth=depth, format='machine'))
        assert payload['direct_dependents'] == payload['direct_dependent_refs'] == []
        assert payload['transitive_dependents'] == payload['transitive_dependent_refs'] == []
        assert payload['direct_dependents_count'] == payload['blast_radius_size'] == payload['returned_scope_size'] == 1
        assert payload['direct_dependents_omitted'] == payload['transitive_dependents_omitted'] == 0
        assert payload['unresolved_dependent_refs'] == ['OTHER::consumer.ts']
        assert payload['path_projection_status'] == 'partial'
        brief = server.get_impact_radius('MAIN::consumer.ts', target_root=str(tmp_path), depth=depth, format='brief')
        assert 'unresolved_dependent_refs: ["OTHER::consumer.ts"]' in brief
        assert 'OTHER/consumer.ts' not in brief
    assert not (store._raw_dir / 'codemaps.db').exists()


@pytest.mark.parametrize('absolute_roots', [False, True])
def test_atlas_only_neighbor_omitted_workspace_has_only_relative_root_compatibility(
        tmp_path, monkeypatch, absolute_roots):
    store, atlas = _atlas_only_neighbor_sample(tmp_path, monkeypatch, absolute_roots)
    atlas['OTHER']['files']['consumer.ts'].pop('workspace_rel')
    store.save_raw('atlas', atlas)
    rows = json.loads(server.search_symbols('consumer', project='OTHER', target_root=str(tmp_path), format='machine'))
    expected = '' if absolute_roots else 'OTHER/consumer.ts'
    assert rows and all(r['repo_relative_path'] == expected for r in rows)
    radius = json.loads(server.get_impact_radius('MAIN::consumer.ts', target_root=str(tmp_path), format='machine'))
    assert radius['direct_dependents'] == ([] if absolute_roots else [expected])
    assert radius['blast_radius_size'] == radius['direct_dependents_count'] == 1
    assert radius['unresolved_dependent_refs'] == (['OTHER::consumer.ts'] if absolute_roots else [])


@pytest.mark.parametrize('state', ['unknown_file', 'ambiguous_alias', 'unique_alias'])
def test_atlas_only_neighbor_symbol_file_identity_cannot_borrow_a_basename(
        tmp_path, monkeypatch, state):
    store, atlas = _atlas_only_neighbor_sample(tmp_path, monkeypatch)
    occurrence = next(r for r in atlas['OTHER']['symbols'] if r['name'] == 'consumer')
    occurrence['file'] = 'unknown.ts' if state == 'unknown_file' else 'OTHER/consumer.ts'
    if state == 'ambiguous_alias':
        atlas['OTHER']['files']['duplicate.ts'] = copy.deepcopy(atlas['OTHER']['files']['consumer.ts'])
    store.save_raw('atlas', atlas)
    rows = json.loads(server.search_symbols('consumer', project='OTHER', target_root=str(tmp_path), format='machine'))
    row = next(r for r in rows if r['type'] == 'Function')
    expected = 'OTHER/consumer.ts' if state == 'unique_alias' else ''
    assert row['repo_relative_path'] == expected
    assert row['atlas_node'] == 'OTHER::' + ('consumer.ts' if state == 'unique_alias' else occurrence['file'])
    assert row['path_projection_status'] == ('resolved' if expected else 'unresolved')
    assert row['candidate_count'] >= 2


def test_atlas_only_neighbor_unknown_graph_nodes_keep_refs_not_openable_paths(tmp_path, monkeypatch):
    store, atlas = _atlas_only_neighbor_sample(tmp_path, monkeypatch)
    atlas['LAST'] = copy.deepcopy(atlas['OTHER'])
    atlas['LAST']['files']['consumer.ts']['workspace_rel'] = 'LAST/consumer.ts'
    store.save_raw('atlas', atlas)
    unknown = ['GHOST::consumer.ts', 'MAIN::missing.ts', 'consumer.ts']
    store.save_raw('circular_deps', {
        'nodes': {n: {} for n in ['MAIN::consumer.ts', 'OTHER::consumer.ts', 'LAST::consumer.ts', *unknown]},
        'edges': [{'source': n, 'target': 'MAIN::consumer.ts'} for n in ['OTHER::consumer.ts', *unknown]]
                 + [{'source': 'LAST::consumer.ts', 'target': 'OTHER::consumer.ts'}]})
    for depth, count in ((1, 4), (2, 5), (0, 5)):
        radius = json.loads(server.get_impact_radius('MAIN::consumer.ts', target_root=str(tmp_path), depth=depth, format='machine'))
        assert radius['direct_dependents_count'] == 4
        assert radius['blast_radius_size'] == radius['returned_scope_size'] == count
        assert radius['direct_dependents'] == ['OTHER/consumer.ts']
        assert radius['direct_dependent_refs'] == ['OTHER::OTHER/consumer.ts']
        assert set(radius['transitive_dependents']) == ({'OTHER/consumer.ts'} if depth == 1 else {'OTHER/consumer.ts', 'LAST/consumer.ts'})
        assert set(radius['unresolved_dependent_refs']) == set(unknown)
        assert radius['direct_dependents_omitted'] == radius['transitive_dependents_omitted'] == 0
        assert radius['scope_completeness'] == ('complete_within_loaded_graph' if depth == 0 else 'bounded')
    assert not (store._raw_dir / 'codemaps.db').exists()


def test_atlas_only_neighbor_path_rejection_does_not_change_search_caps(tmp_path, monkeypatch):
    store, atlas = _atlas_only_neighbor_sample(tmp_path, monkeypatch)
    for n in range(4):
        atlas['OTHER']['files'][f'consumer-{n}.ts'] = copy.deepcopy(atlas['OTHER']['files']['consumer.ts'])
        atlas['OTHER']['files'][f'consumer-{n}.ts']['workspace_rel'] = f'OTHER/consumer-{n}.ts'
    atlas['OTHER']['symbols'] = _build_project_symbol_occurrences(atlas['OTHER']['files'])
    store.save_raw('atlas', atlas)
    policy = {**server._symbol_search_policy(), 'max_candidate_rows_per_source': 2}
    monkeypatch.setattr(server, '_symbol_search_policy', lambda: policy)
    before = json.loads(server.search_symbols('consumer', project='all', target_root=str(tmp_path), format='machine'))
    for project in atlas.values():
        for meta in project['files'].values():
            meta['workspace_rel'] = '../not-a-file.ts'
    store.save_raw('atlas', atlas)
    after = json.loads(server.search_symbols('consumer', project='all', target_root=str(tmp_path), format='machine'))
    assert len(before) == len(after) == 4
    stable = ('name', 'project', 'file', 'atlas_node', 'match_score', 'candidate_count', 'candidate_count_semantics', 'search_truncated')
    assert [{k: r[k] for k in stable} for r in before] == [{k: r[k] for k in stable} for r in after]
    assert all(r['search_truncated'] and r['candidate_count_semantics'] == 'lower_bound' for r in after)
    assert all(r['repo_relative_path'] == '' and r['path_projection_status'] == 'unresolved' for r in after)


@pytest.mark.parametrize('project', ['MAIN', 'OTHER'])
@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_atlas_only_target_path_uses_owned_project_without_snapshot_claim(
        tmp_path, monkeypatch, project, external, absolute_roots):
    store, _ = _atlas_only_sample(tmp_path, monkeypatch, absolute_roots)
    target_root = str(tmp_path) if external else ''
    for ref in (f'{project}::consumer.ts', f'{project}::{project}/consumer.ts'):
        payload = json.loads(server.trace_upstream_cause(ref, target_root=target_root, format='machine'))
        status = payload['target_path_status']
        assert payload['target'] == f'{project}::consumer.ts'
        assert payload['target_file'] == f'{project}/consumer.ts'
        assert status['target_ref'] == f'{project}::{project}/consumer.ts'
        assert status['target_file'] == f'{project}/consumer.ts'
        assert status['exists'] and status['inside_root'] and status['indexed']
        assert status['source_snapshot_status'] == 'missing'
        assert status['drift_check_status'] == 'not_available'
        assert status.get('target_source_snippets', []) == []
    assert not (store._raw_dir / 'codemaps.db').exists()


@pytest.mark.parametrize('workspace', [None, '', '../escape.ts', '/consumer.ts', 'C:/foreign/consumer.ts', 42, {}])
def test_atlas_only_target_path_rejects_malformed_workspace_without_borrowing_live_file(
        tmp_path, monkeypatch, workspace):
    store, atlas = _atlas_only_sample(tmp_path, monkeypatch)
    atlas['MAIN']['files']['consumer.ts']['workspace_rel'] = workspace
    store.save_raw('atlas', atlas)
    # The parser fixture left a real root-level consumer.ts: it is a decoy,
    # not permission to discard the MAIN file's missing/rejected workspace path.
    payload = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=str(tmp_path), format='machine'))
    status = payload['target_path_status']
    assert payload['target'] == 'MAIN::consumer.ts'
    assert payload['target_file'] == status['target_file'] == ''
    assert status['indexed'] is False and status['exists'] is False
    assert status['source_snapshot_status'] == 'missing'
    assert status.get('target_source_snippets', []) == []
    assert not (store._raw_dir / 'codemaps.db').exists()


@pytest.mark.parametrize('state', ['unknown_file', 'foreign_project', 'ambiguous_workspace', 'missing_absolute_workspace'])
def test_atlas_only_target_path_unknowns_remain_unopenable(tmp_path, monkeypatch, state):
    store, atlas = _atlas_only_sample(tmp_path, monkeypatch)
    ref = 'MAIN::consumer.ts'
    if state == 'unknown_file':
        ref = 'MAIN::unknown.ts'
        (tmp_path / 'unknown.ts').write_text('export const decoy = 1;\n', encoding='utf-8')
    elif state == 'foreign_project':
        ref = 'GHOST::consumer.ts'
    elif state == 'ambiguous_workspace':
        atlas['MAIN']['files']['duplicate.ts'] = copy.deepcopy(atlas['MAIN']['files']['consumer.ts'])
        ref = 'MAIN::MAIN/consumer.ts'
    else:
        atlas['MAIN']['files']['consumer.ts'].pop('workspace_rel')
    store.save_raw('atlas', atlas)
    payload = json.loads(server.trace_upstream_cause(ref, target_root=str(tmp_path), format='machine'))
    assert payload['target_file'] == ''
    assert payload['target_path_status']['indexed'] is False
    assert payload['target_path_status']['exists'] is False


def test_atlas_only_target_path_legacy_relative_root_uses_declared_root(tmp_path, monkeypatch):
    store, atlas = _atlas_only_sample(tmp_path, monkeypatch, False)
    atlas['OTHER']['files']['consumer.ts'].pop('workspace_rel')
    store.save_raw('atlas', atlas)
    for ref in ('OTHER::consumer.ts', 'OTHER::OTHER/consumer.ts', 'OTHER/consumer.ts'):
        payload = json.loads(server.trace_upstream_cause(ref, target_root=str(tmp_path), format='machine'))
        assert payload['target'] == 'OTHER::consumer.ts'
        assert payload['target_file'] == 'OTHER/consumer.ts'
        assert payload['target_path_status']['indexed'] is True
        assert payload['target_path_status']['source_snapshot_status'] == 'missing'


@pytest.mark.parametrize('state', ['host_exact', 'unique_workspace', 'ambiguous_workspace'])
def test_atlas_only_target_path_unqualified_selection_is_not_first_alias(tmp_path, monkeypatch, state):
    store, atlas = _atlas_only_sample(tmp_path, monkeypatch)
    ref = 'consumer.ts' if state == 'host_exact' else 'OTHER/consumer.ts'
    if state == 'ambiguous_workspace':
        atlas['MAIN']['files']['consumer.ts']['workspace_rel'] = 'OTHER/consumer.ts'
        store.save_raw('atlas', atlas)
    payload = json.loads(server.trace_upstream_cause(ref, target_root=str(tmp_path), format='machine'))
    status = payload['target_path_status']
    if state == 'ambiguous_workspace':
        assert payload['target_file'] == status['target_file'] == ''
        assert not status['indexed'] and not status['exists']
    else:
        project = 'MAIN' if state == 'host_exact' else 'OTHER'
        assert payload['target'] == f'{project}::consumer.ts'
        assert payload['target_file'] == status['target_file'] == f'{project}/consumer.ts'
        assert status['indexed'] and status['exists']


@pytest.mark.parametrize('state', ['unique_fold', 'exact_collision', 'ambiguous_fold'])
def test_atlas_only_target_path_project_case_selection_is_unambiguous(tmp_path, monkeypatch, state):
    store, atlas = _atlas_only_sample(tmp_path, monkeypatch)
    if state != 'unique_fold':
        atlas['other'] = copy.deepcopy(atlas['MAIN'])
        store.save_raw('atlas', atlas)
    project = 'OTHER' if state == 'exact_collision' else 'Other'
    payload = json.loads(server.trace_upstream_cause(f'{project}::consumer.ts', target_root=str(tmp_path), format='machine'))
    status = payload['target_path_status']
    if state == 'ambiguous_fold':
        assert payload['target_file'] == status['target_file'] == ''
        assert not status['indexed'] and not status['exists']
    else:
        assert payload['target'] == 'OTHER::consumer.ts'
        assert payload['target_file'] == status['target_file'] == 'OTHER/consumer.ts'
        assert status['indexed'] and status['exists']


@pytest.mark.parametrize('state', ['valid', 'malformed', 'foreign_project', 'unknown_file'])
@pytest.mark.parametrize('graph_has_target', [False, True])
def test_atlas_only_target_path_radius_and_briefs_do_not_restore_rejected_paths(
        tmp_path, monkeypatch, state, graph_has_target):
    store, atlas = _atlas_only_sample(tmp_path, monkeypatch)
    ref = 'OTHER::consumer.ts'
    if state == 'malformed':
        atlas['OTHER']['files']['consumer.ts']['workspace_rel'] = '../escape.ts'
        store.save_raw('atlas', atlas)
    elif state == 'foreign_project':
        ref = 'GHOST::consumer.ts'
    elif state == 'unknown_file':
        ref = 'OTHER::unknown.ts'
        (tmp_path / 'unknown.ts').write_text('export const decoy = 1;\n', encoding='utf-8')
    store.save_raw('circular_deps', {'nodes': {ref: {}} if graph_has_target else {}, 'edges': []})
    payload = json.loads(server.get_impact_radius(ref, target_root=str(tmp_path), format='machine', depth=0))
    status = payload['target_path_status']
    expected = 'OTHER/consumer.ts' if state == 'valid' else ''
    assert payload['target'] == ref
    assert payload['target_file'] == status['target_file'] == expected
    assert status['indexed'] is (state == 'valid')
    assert status['source_snapshot_status'] == 'missing'
    assert payload['blast_radius_size'] == payload['direct_dependents_count'] == 0
    for consumer in (server.get_impact_radius, server.trace_upstream_cause):
        brief = consumer(ref, target_root=str(tmp_path), format='brief')
        assert f'target_file: {json.dumps(expected)}' in brief
        assert 'source_snapshot_status: "missing"' in brief
    assert not (store._raw_dir / 'codemaps.db').exists()

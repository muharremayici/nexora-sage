"""Physical config roots are not public search/radius/upstream/finding paths."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from tools.core import artifact_store, config, projects_registry
from tools.core.artifact_store import ArtifactStore
from tools.mcp import server
from tools.tests.test_symbol_qualified_import_call_search import _source_atlas


def _sample(tmp_path, monkeypatch, absolute_roots, *, source=None):
    source = source if source is not None else '''import * as Sentry from "@sentry/react";
import { createStore } from "zustand/vanilla";
export class Worker { run() { return 1; } }
export const store = createStore((set) => ({update() { set({value: 1}); }}));
export function report() { Sentry.captureException(new Error('probe')); }
'''
    # Real parser evidence supplies every search lane; no target code executes.
    parsed = _source_atlas(tmp_path, source)['MAIN']['files']['consumer.ts']
    hints = {}
    atlas = {}
    for project in ('MAIN', 'OTHER'):
        root = tmp_path / project
        root.mkdir()
        (root / 'consumer.ts').write_text(source, encoding='utf-8', newline='')
        (root / 'leaf.ts').write_text('export const leaf = 1;\n', encoding='utf-8', newline='')
        hints[project] = str(root) if absolute_roots else project
        meta = copy.deepcopy(parsed)
        meta.update(workspace_rel=f'{project}/consumer.ts', repo_relative_path=f'{project}/consumer.ts')
        atlas[project] = {'files': {'consumer.ts': meta, 'leaf.ts': {
            'hash': hashlib.sha256(b'export const leaf = 1;\n').hexdigest(),
            'language': 'typescript', 'symbols': [], 'workspace_rel': f'{project}/leaf.ts'}}}
    monkeypatch.setattr(projects_registry, 'PROJECTS', {key: [value] for key, value in hints.items()})
    monkeypatch.setattr(config, 'PROJECT_FILTER', set())
    assert projects_registry.resolve_runtime_projects(tmp_path) == {
        key: tmp_path / key for key in hints}
    monkeypatch.setattr(artifact_store, 'ROOT', tmp_path)
    monkeypatch.setattr(artifact_store, 'DYNAMIC_CONFIG', {**artifact_store.DYNAMIC_CONFIG, 'variations': hints})
    store = ArtifactStore(raw_dir=tmp_path / '.raw')
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    atlas['MAIN']['dependencies'] = {'consumer.ts': ['leaf.ts']}
    atlas['OTHER']['dependencies'] = {'consumer.ts': ['MAIN::consumer.ts']}
    store.save_raw('atlas', atlas)
    store.save_raw('circular_deps', {
        'nodes': {f'{key}::{rel}': {} for key, project in atlas.items() for rel in project['files']},
        'edges': [{'source': 'MAIN::consumer.ts', 'target': 'MAIN::leaf.ts'},
                  {'source': 'OTHER::consumer.ts', 'target': 'MAIN::consumer.ts'}]})
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda _: store._raw_dir)
    monkeypatch.setattr(server, '_analysis_root_display', lambda _: str(tmp_path))
    return store, atlas


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_public_search_all_lanes_use_declared_workspace_paths(tmp_path, monkeypatch, external, absolute_roots):
    _sample(tmp_path, monkeypatch, absolute_roots)
    target_root = str(tmp_path) if external else ''
    rows = json.loads(server.search_symbols('consumer.ts', project='all', target_root=target_root, format='machine'))
    assert {row['type'] for row in rows} >= {
        'File', 'Class', 'ClassMethod', 'StoreActionCandidate', 'ImportBindingCandidate', 'QualifiedImportCallCandidate'}
    for row in rows:
        assert row['file'] == 'consumer.ts'
        assert row['repo_relative_path'] == f"{row['project']}/consumer.ts"
        assert row['atlas_node'] == f"{row['project']}::consumer.ts"
        assert row['search_truncated'] is False
    for project in ('MAIN', 'OTHER'):
        brief = server.search_symbols('consumer.ts', project=project, target_root=target_root, format='brief')
        assert f'file: "{project}/consumer.ts"' in brief
        assert str(tmp_path).replace('\\', '/') not in brief.split('matches:')[-1]
    assert server._find_symbol_matches('OTHER/consumer', project='all', raw_dir=tmp_path / '.raw') == []
    inspected = json.loads(server.inspect_file('OTHER::OTHER/consumer.ts', target_root=target_root, format='machine'))
    assert inspected['target_file_context'][0]['atlas_node'] == 'OTHER::consumer.ts'


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_public_radius_preserves_graph_identity_with_workspace_refs(tmp_path, monkeypatch, external, absolute_roots):
    _sample(tmp_path, monkeypatch, absolute_roots)
    target_root = str(tmp_path) if external else ''
    payload = json.loads(server.get_impact_radius('MAIN::leaf.ts', target_root=target_root, format='machine'))
    assert payload['dependency_graph_source'] == 'sqlite_dependencies'
    assert payload['target'] == 'MAIN::leaf.ts'
    assert payload['target_file'] == 'MAIN/leaf.ts'
    assert payload['direct_dependents'] == ['MAIN/consumer.ts']
    assert payload['transitive_dependents'] == ['MAIN/consumer.ts', 'OTHER/consumer.ts']
    assert payload['direct_dependent_refs'] == ['MAIN::MAIN/consumer.ts']
    assert payload['transitive_dependent_refs'] == ['MAIN::MAIN/consumer.ts', 'OTHER::OTHER/consumer.ts']
    assert payload['blast_radius_size'] == 2 and payload['direct_dependents_count'] == 1
    assert payload['transitive_dependent_depths'] == {'MAIN/consumer.ts': 1, 'OTHER/consumer.ts': 2}
    assert payload['scope_completeness'] == 'bounded'
    assert payload['path_projection_status'] == 'resolved'
    assert payload['unresolved_dependent_refs'] == []
    public = json.loads(server.get_impact_radius('MAIN::MAIN/leaf.ts', target_root=target_root, format='machine'))
    assert public['transitive_dependent_refs'] == payload['transitive_dependent_refs']
    brief = server.get_impact_radius('MAIN::leaf.ts', target_root=target_root, format='brief')
    assert str(tmp_path).replace('\\', '/') not in brief.split('direct_dependents:')[-1]


@pytest.mark.parametrize('workspace', [None, '', '../escape.ts', '/foreign/consumer.ts', 'C:/foreign/consumer.ts', 42])
def test_unresolved_workspace_keeps_candidates_and_graph_counts_without_guessing(tmp_path, monkeypatch, workspace):
    store, atlas = _sample(tmp_path, monkeypatch, True)
    atlas['MAIN']['files']['consumer.ts']['workspace_rel'] = workspace
    store.save_raw('atlas', atlas)
    original_atlas = server._atlas

    def owned_atlas(*, raw_dir=None):
        assert raw_dir == store._raw_dir, 'must not borrow host or another target Atlas'
        return original_atlas(raw_dir=raw_dir)

    monkeypatch.setattr(server, '_atlas', owned_atlas)
    rows = json.loads(server.search_symbols('consumer.ts', project='all', target_root=str(tmp_path), format='machine'))
    assert {row['type'] for row in rows if row['project'] == 'MAIN'} >= {
        'File', 'ClassMethod', 'StoreActionCandidate', 'ImportBindingCandidate', 'QualifiedImportCallCandidate'}
    for row in rows:
        if row['project'] == 'MAIN':
            assert row['repo_relative_path'] == ''
            assert row['path_projection_status'] == 'unresolved'
            assert row['atlas_node'] == 'MAIN::consumer.ts'
        else:
            assert row['repo_relative_path'] == 'OTHER/consumer.ts'
    brief = server.search_symbols('consumer.ts', target_root=str(tmp_path), format='brief')
    assert 'file: ""' in brief and 'target_ref: "MAIN::consumer.ts"' in brief
    assert 'path_projection_status: "unresolved"' in brief
    assert 'file: "consumer.ts"' not in brief
    payload = json.loads(server.get_impact_radius('MAIN::leaf.ts', target_root=str(tmp_path), format='machine'))
    assert payload['blast_radius_size'] == 2 and payload['direct_dependents_count'] == 1
    assert payload['direct_dependents'] == payload['direct_dependent_refs'] == []
    assert payload['direct_dependents_omitted'] == 1
    assert payload['transitive_dependents'] == ['OTHER/consumer.ts']
    assert payload['transitive_dependent_refs'] == ['OTHER::OTHER/consumer.ts']
    assert payload['transitive_dependents_omitted'] == 1
    assert payload['transitive_dependent_depths'] == {'OTHER/consumer.ts': 2}
    assert payload['path_projection_status'] == 'partial'
    assert payload['unresolved_dependent_refs'] == ['MAIN::consumer.ts']
    radius_brief = server.get_impact_radius('MAIN::leaf.ts', target_root=str(tmp_path), format='brief')
    assert 'path_projection_status: "partial"' in radius_brief
    assert 'unresolved_dependent_refs: ["MAIN::consumer.ts"]' in radius_brief
    assert 'target_ref: "OTHER::OTHER/consumer.ts"' in radius_brief


def test_relative_root_projection_does_not_need_atlas_or_live_walk(monkeypatch):
    monkeypatch.setattr(server, '_atlas', lambda **_: pytest.fail('relative path needs no Atlas load'))
    from pathlib import Path
    monkeypatch.setattr(Path, 'iterdir', lambda *_: pytest.fail('projection must not walk source'))
    assert server._symbol_search_repo_relative('packages/companion', 'src/file.ts') == 'packages/companion/src/file.ts'
    assert server._symbol_search_repo_relative('.', 'src/file.ts') == 'src/file.ts'


def test_absolute_display_projection_does_not_change_source_caps_or_rank(tmp_path, monkeypatch):
    store, _ = _sample(tmp_path, monkeypatch, True)
    policy = {**server._symbol_search_policy(), 'max_candidate_rows_per_source': 1, 'machine_max_visible_items': 1}
    monkeypatch.setattr(server, '_symbol_search_policy', lambda: policy)
    absolute = json.loads(server.search_symbols('consumer.ts', project='all', target_root=str(tmp_path), format='machine'))
    with store.db_manager.get_connection() as conn:
        conn.execute('UPDATE projects SET path = project_key')
        conn.commit()
    relative = json.loads(server.search_symbols('consumer.ts', project='all', target_root=str(tmp_path), format='machine'))
    assert absolute == relative
    assert absolute[0]['search_truncated'] is True
    assert absolute[0]['candidate_count_semantics'] == 'lower_bound'
    assert absolute[0]['shown'] == 1 and absolute[0]['omitted'] > 0


def _upstream_sample(tmp_path, monkeypatch, absolute_roots):
    source = '''import { leaf } from "./leaf";
import { remote } from "../OTHER/remote";
export const combined = leaf + remote;
'''
    store, atlas = _sample(tmp_path, monkeypatch, absolute_roots, source=source)
    remote_source = 'export const remote = 2;\n'
    (tmp_path / 'OTHER' / 'remote.ts').write_text(remote_source, encoding='utf-8', newline='')
    atlas['OTHER']['files']['remote.ts'] = {
        'hash': hashlib.sha256(remote_source.encode()).hexdigest(),
        'language': 'typescript', 'symbols': [], 'workspace_rel': 'OTHER/remote.ts'}
    atlas['MAIN']['dependencies']['consumer.ts'] = ['leaf.ts', 'OTHER::remote.ts']
    # Declared resolver identities, with real parser spans and normal SQLite writes.
    # This does not claim whole Atlas acquisition resolves the cross-project import.
    atlas['MAIN']['files']['consumer.ts']['import_records'] = [
        {'source': 'leaf.ts', 'raw_source': './leaf', 'name': 'leaf', 'kind': 'named', 'scope': 'top_level'},
        {'source': 'OTHER::remote.ts', 'raw_source': '../OTHER/remote', 'name': 'remote', 'kind': 'named', 'scope': 'top_level'}]
    store.save_raw('atlas', atlas)
    assert server._source_snapshot_content_for_ref(store._raw_dir, 'MAIN::consumer.ts') == source
    return store, atlas


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_public_upstream_workspace_paths_refs_and_import_snapshots(tmp_path, monkeypatch, external, absolute_roots):
    store, _ = _upstream_sample(tmp_path, monkeypatch, absolute_roots)
    target_root = str(tmp_path) if external else ''
    payload = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=target_root, format='machine'))
    assert payload['target'] == 'MAIN::consumer.ts'
    assert payload['dependency_graph_source'] == 'sqlite_dependencies'
    assert payload['upstream_dependency_files'] == ['MAIN/leaf.ts', 'OTHER/remote.ts']
    assert payload['upstream_dependencies'] == ['MAIN::MAIN/leaf.ts', 'OTHER::OTHER/remote.ts']
    assert payload['direct_dependent_files'] == ['OTHER/consumer.ts']
    assert payload['direct_dependents'] == ['OTHER::OTHER/consumer.ts']
    assert [(row['file'], row['target_ref'], row['import_specifier']) for row in payload['upstream_dependency_evidence']] == [
        ('MAIN/leaf.ts', 'MAIN::MAIN/leaf.ts', './leaf'),
        ('OTHER/remote.ts', 'OTHER::OTHER/remote.ts', '../OTHER/remote')]
    assert payload['upstream_dependency_evidence'][1]['source_snippets'][0]['matched_line'] == 2
    brief = server.trace_upstream_cause('MAIN::consumer.ts', target_root=target_root, format='brief')
    assert 'target_ref: "OTHER::OTHER/remote.ts"' in brief
    assert 'target_ref: "OTHER::OTHER/consumer.ts"' in brief
    assert str(tmp_path).replace('\\', '/') not in brief.split('upstream_candidates:')[-1]
    inspected = json.loads(server.inspect_file('OTHER::OTHER/remote.ts', target_root=target_root, format='machine'))
    assert inspected['target_file_context'][0]['atlas_node'] == 'OTHER::remote.ts'
    # Changing live source does not replace the captured import line.
    (tmp_path / 'MAIN' / 'consumer.ts').write_text('export const changed = 3;\n', encoding='utf-8')
    repeated = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=target_root, format='machine'))
    assert repeated['upstream_dependency_evidence'] == payload['upstream_dependency_evidence']
    with store.db_manager.get_connection() as conn:
        conn.execute("UPDATE source_snapshots SET content='tampered' WHERE project_key='MAIN' AND rel_path='consumer.ts'")
        conn.commit()
    tampered = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=target_root, format='machine'))
    assert tampered['upstream_dependency_evidence'] == []


@pytest.mark.parametrize('workspace', [None, '', '../escape.ts', '/foreign/leaf.ts', 'C:/foreign/leaf.ts', 42])
def test_public_upstream_unknown_metadata_preserves_graph_and_valid_cross_project_ref(tmp_path, monkeypatch, workspace):
    store, atlas = _upstream_sample(tmp_path, monkeypatch, True)
    atlas['MAIN']['files']['leaf.ts']['workspace_rel'] = workspace
    atlas['OTHER']['files']['consumer.ts']['workspace_rel'] = workspace
    store.save_raw('atlas', atlas)
    original_atlas = server._atlas

    def owned_atlas(*, raw_dir=None):
        assert raw_dir == store._raw_dir
        return original_atlas(raw_dir=raw_dir)

    monkeypatch.setattr(server, '_atlas', owned_atlas)
    payload = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=str(tmp_path), format='machine'))
    assert payload['upstream_dependencies'] == ['MAIN::leaf.ts', 'OTHER::OTHER/remote.ts']
    assert payload['upstream_dependency_files'] == ['OTHER/remote.ts']
    assert payload['upstream_dependency_count'] == 2
    assert payload['direct_dependents_count'] == 1
    assert payload['path_projection_status'] == 'partial'
    assert payload['unresolved_upstream_refs'] == ['MAIN::leaf.ts']
    assert payload['direct_dependents'] == ['OTHER::consumer.ts']
    assert payload['direct_dependent_files'] == []
    assert payload['unresolved_dependent_refs'] == ['OTHER::consumer.ts']
    unresolved, valid = payload['upstream_dependency_context']
    assert unresolved['repo_relative_path'] == '' and unresolved['path_projection_status'] == 'unresolved'
    assert unresolved['atlas_node'] == unresolved['target_ref'] == 'MAIN::leaf.ts'
    assert valid['target_ref'] == 'OTHER::OTHER/remote.ts'
    brief = server.trace_upstream_cause('MAIN::consumer.ts', target_root=str(tmp_path), format='brief')
    assert 'upstream_dependency_count: 2' in brief
    assert 'upstream_candidates_omitted: 1' in brief
    assert 'unresolved_upstream_refs: ["MAIN::leaf.ts"]' in brief
    assert 'unresolved_dependent_refs: ["OTHER::consumer.ts"]' in brief
    assert 'target_ref: "OTHER::OTHER/remote.ts"' in brief
    assert 'file: "leaf.ts"' not in brief


def test_upstream_legacy_brief_keeps_explicit_project_refs_and_default_project():
    brief = server._render_upstream_trace_brief({
        'target': 'MAIN::src/a.ts', 'upstream_dependency_files': ['src/b.ts', 'OTHER::lib/c.ts'],
        'direct_dependents': ['OTHER::src/d.ts']})
    assert 'target_ref: "MAIN::src/b.ts"' in brief
    assert 'target_ref: "OTHER::lib/c.ts"' in brief
    assert 'target_ref: "OTHER::src/d.ts"' in brief


def test_upstream_projection_keeps_existing_sql_bound_order_without_source_walk(tmp_path, monkeypatch):
    store, atlas = _upstream_sample(tmp_path, monkeypatch, True)
    extras = [f'extra-{index:03}.ts' for index in range(108)]
    for name in extras:
        atlas['MAIN']['files'][name] = {'hash': 'a' * 64, 'symbols': [], 'workspace_rel': f'MAIN/{name}'}
    atlas['MAIN']['dependencies']['consumer.ts'].extend(extras)
    store.save_raw('atlas', atlas)
    from pathlib import Path
    monkeypatch.setattr(Path, 'iterdir', lambda *_: pytest.fail('upstream projection must not walk source'))
    payload = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=str(tmp_path), format='machine'))
    assert payload['scope_completeness'] == 'bounded'
    assert payload['dependency_count_semantics'] == 'returned_indexed_rows'
    assert payload['upstream_dependency_count'] == 100
    assert payload['upstream_dependency_files'] == [f'MAIN/{name}' for name in extras[:100]]
    assert payload['upstream_dependencies'] == [f'MAIN::MAIN/{name}' for name in extras[:100]]
    brief = server.trace_upstream_cause('MAIN::consumer.ts', target_root=str(tmp_path), format='brief')
    assert 'upstream_candidates_shown: 4' in brief and 'upstream_candidates_omitted: 96' in brief


def _finding_sample(tmp_path, monkeypatch, absolute_roots, *, workspace='MAIN/consumer.ts',
                    source=None, main_detail='MAIN finding fixture'):
    from tools.core.atlas_integrity import build_atlas_commit
    store, atlas = _sample(tmp_path, monkeypatch, absolute_roots, source=source)
    atlas['MAIN']['files']['consumer.ts']['workspace_rel'] = workspace
    store.save_raw('atlas', atlas)
    commit = build_atlas_commit(atlas)
    store.save_raw('atlas_commit', commit)
    # Explicit fixture scope and Audit inputs, not a live full-repository audit.
    scope_authority = {
        'contract': 'repository_analysis_scope_authority_v1',
        'scope_authority_id': 'fixture-finding-path', 'evidence_status': 'BOUNDED_PROJECT_SELECTION'}
    store.save_raw('analysis_scope_authority', {'scope_authority': scope_authority})
    audit_scope = {'scope_kind': 'full_repository', 'full_repository_claim': True,
                   'atlas_project_count': 2, 'audited_projects': ['MAIN', 'OTHER'],
                   'scope_authority': scope_authority}
    store.save_raw('audit_report', {
        'meta': {'kind': 'audit_report', 'version': 'v15-atlas-pure'},
        'artifact_identity': {'status': 'BOUND', 'atlas_snapshot_id': commit['snapshot_id']},
        'audit_scope': audit_scope, 'summary': {'audit_scope': audit_scope},
        'violations': [
            {'project': 'MAIN', 'file': 'consumer.ts', 'rule': 'monolithic_function',
             'severity': 'critical', 'detail': main_detail},
            {'project': 'OTHER', 'file': 'consumer.ts', 'rule': 'monolithic_function',
             'severity': 'warning', 'detail': 'OTHER finding fixture'}]})
    assert server._audit_findings_generation_projection(store._raw_dir, project='MAIN')['status'] == 'PASS'
    return store, atlas


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_public_module_finding_paths_preserve_owner_and_rule(tmp_path, monkeypatch, external, absolute_roots):
    store, _ = _finding_sample(tmp_path, monkeypatch, absolute_roots)
    target = str(tmp_path) if external else ''
    payload = json.loads(server.check_module_integrity('consumer.ts', target_root=target, format='machine'))
    assert payload['status'] == 'impure' and payload['source'] == 'sqlite_findings'
    assert payload['violation_count'] == (2 if external else 1)
    guidance = server._rule_guidance('monolithic_function')
    for row in payload['reasoning_trace']:
        project = 'MAIN' if row['evidence'] == 'MAIN finding fixture' else 'OTHER'
        path = f'{project}/consumer.ts'
        assert row['file'] == row['target_file'] == path
        assert row['target_ref'] == f'{project}::{path}'
        assert row['inspect_first'] == [path]
        assert row['target_status'] == {'exists': True, 'inside_root': True, 'indexed': True}
        assert row['rule'] == 'monolithic_function' and row['priority'] == guidance['priority']
        assert row['remediation_action'] == guidance['recommended_action']
        inspected = json.loads(server.inspect_file(row['target_ref'], target_root=target, format='machine'))
        assert inspected['target_file_context'][0]['atlas_node'] == f'{project}::consumer.ts'
    brief = server.check_module_integrity('consumer.ts', target_root=target, format='brief')
    assert 'target_ref: "MAIN::MAIN/consumer.ts"' in brief
    assert str(tmp_path).replace('\\', '/') not in brief.split('items:')[-1]
    if external:
        assert 'target_ref: "OTHER::OTHER/consumer.ts"' in brief
    with store.db_manager.get_connection() as conn:
        assert [row[0] for row in conn.execute('SELECT severity FROM findings ORDER BY finding_id')] == ['critical', 'warning']


@pytest.mark.parametrize('workspace', [None, '', '../escape.ts', '/foreign/consumer.ts', 'C:/foreign/consumer.ts', 42])
def test_public_module_finding_unknown_path_keeps_evidence_and_count(tmp_path, monkeypatch, workspace):
    store, _ = _finding_sample(tmp_path, monkeypatch, True, workspace=workspace)
    original_atlas = server._atlas

    def owned_atlas(*, raw_dir=None):
        assert raw_dir == store._raw_dir
        return original_atlas(raw_dir=raw_dir)

    monkeypatch.setattr(server, '_atlas', owned_atlas)
    payload = json.loads(server.check_module_integrity('consumer.ts', target_root=str(tmp_path), format='machine'))
    assert payload['status'] == 'impure' and payload['violation_count'] == 2
    unresolved, valid = payload['reasoning_trace']
    assert unresolved['file'] == unresolved['target_file'] == ''
    assert unresolved['atlas_node'] == unresolved['target_ref'] == 'MAIN::consumer.ts'
    assert unresolved['path_projection_status'] == 'unresolved'
    assert unresolved['target_status'] == {} and unresolved['inspect_first'] == []
    assert unresolved['evidence'] == 'MAIN finding fixture' and unresolved['rule'] == 'monolithic_function'
    assert valid['file'] == 'OTHER/consumer.ts' and valid['target_ref'] == 'OTHER::OTHER/consumer.ts'
    brief = server.check_module_integrity('consumer.ts', target_root=str(tmp_path), format='brief')
    assert 'path_projection_status: "unresolved"' in brief
    assert 'target_ref: "MAIN::consumer.ts"' in brief and 'inspect_first: []' in brief
    assert 'MAIN finding fixture' in brief and 'file: "consumer.ts"' not in brief


def test_module_finding_projection_preserves_literal_filter_scope_order_and_cap(tmp_path, monkeypatch):
    store, _ = _finding_sample(tmp_path, monkeypatch, True)
    from pathlib import Path
    monkeypatch.setattr(Path, 'iterdir', lambda *_: pytest.fail('finding projection must not walk source'))
    for query, count in [('consumer.ts', 2), ('consumer%ts', 0), ('consumer_ts', 0)]:
        payload = json.loads(server.check_module_integrity(query, target_root=str(tmp_path), format='machine', max_items=1))
        assert payload['violation_count'] == count
        assert len(payload['reasoning_trace']) == min(count, 1)
        if count:
            assert payload['reasoning_trace'][0]['evidence'] == 'MAIN finding fixture'
    default = json.loads(server.check_module_integrity('consumer.ts', format='machine', max_items=1))
    assert default['violation_count'] == 1
    assert default['reasoning_trace'][0]['target_ref'] == 'MAIN::MAIN/consumer.ts'


def test_public_module_finding_projection_cannot_bypass_stale_generation(tmp_path, monkeypatch):
    store, _ = _finding_sample(tmp_path, monkeypatch, True)
    commit = store.load_raw('atlas_commit', {})
    commit['snapshot_id'] = 'fixture-new-snapshot-with-no-audit'
    store.save_raw('atlas_commit', commit)
    monkeypatch.setattr(server, '_module_integrity_items_from_sqlite', lambda *args, **kwargs: pytest.fail('stale findings cannot reach projection'))
    payload = json.loads(server.check_module_integrity('consumer.ts', target_root=str(tmp_path), format='machine'))
    assert payload['status'] == 'INVALID_CONTEXT'
    assert server._audit_findings_generation_projection(store._raw_dir, project='MAIN')['snapshot_match'] is False
    assert any(row['name'] == 'sqlite_findings:generation_bound_to_current_snapshot'
               for row in payload['artifact_trust']['failures'])

@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_public_queue_paths_preserve_owner_identity_and_authority(tmp_path, monkeypatch, external, absolute_roots):
    store, _ = _finding_sample(tmp_path, monkeypatch, absolute_roots)
    original, total, ok = server._audit_violation_work_items_from_sqlite(
        store._raw_dir, page=1, page_size=10, project='all')
    assert ok and total == 2
    by_id = {row['id']: row for row in original}
    target = str(tmp_path) if external else ''
    payload = json.loads(server.get_violation_work_queue(project='all', target_root=target, format='machine'))
    assert payload['status'] == 'debt_present' and payload['total_violations'] == 2
    for row in payload['items']:
        project = row['target_project']
        path = f'{project}/consumer.ts'
        assert row['target_file'] == path and row['target_ref'] == f'{project}::{path}'
        assert row['atlas_node'] == f'{project}::consumer.ts'
        assert row['path_projection_status'] == 'resolved' and row['inspect_first'] == [path]
        status = row['source_grounding']
        assert all(status[key] is True for key in ('exists', 'inside_root', 'indexed'))
        assert status['target_project'] == project
        for key in ('rule', 'priority', 'evidence', 'recommended_action', 'human_approval_required',
                    'approval_decision_source', 'approval_reason', 'actionability', 'mutation_authority'):
            assert row[key] == by_id[row['id']][key]
        inspected = json.loads(server.inspect_file(row['target_ref'], target_root=target, format='machine'))
        assert inspected['target_file_context'][0]['atlas_node'] == row['atlas_node']
    brief = server.get_violation_work_queue(project='all', target_root=target, format='brief')
    assert 'target_ref: "OTHER::OTHER/consumer.ts"' in brief
    assert 'path_projection_status: "resolved"' in brief


@pytest.mark.parametrize('workspace', [None, '', '../escape.ts', '/foreign/consumer.ts', 'C:/foreign/consumer.ts', 42])
def test_public_queue_unknown_path_retains_finding_without_openable_guess(tmp_path, monkeypatch, workspace):
    store, _ = _finding_sample(tmp_path, monkeypatch, True, workspace=workspace)
    # A tempting basename file must never become the unresolved MAIN finding's target.
    (tmp_path / 'consumer.ts').write_text('export const unrelated = 1;\n', encoding='utf-8')
    original, total, ok = server._audit_violation_work_items_from_sqlite(
        store._raw_dir, page=1, page_size=10, project='all')
    assert ok and total == 2
    payload = json.loads(server.get_violation_work_queue(project='all', target_root=str(tmp_path), format='machine'))
    assert payload['status'] == 'debt_present' and payload['total_violations'] == 2
    unresolved, valid = payload['items']
    assert unresolved['target_file'] == '' and unresolved['inspect_first'] == []
    assert unresolved['target_ref'] == unresolved['atlas_node'] == 'MAIN::consumer.ts'
    assert unresolved['path_projection_status'] == 'unresolved'
    assert not unresolved['source_grounding'].get('target_source_snippets')
    assert not unresolved['source_grounding'].get('evidence_source_snippets')
    assert unresolved['source_grounding']['target_file'] == ''
    assert unresolved['source_grounding']['indexed'] is False
    for key in ('id', 'rule', 'priority', 'evidence', 'recommended_action',
                'human_approval_required', 'approval_reason', 'actionability', 'mutation_authority'):
        assert unresolved[key] == original[0][key]
    assert valid['target_file'] == 'OTHER/consumer.ts' and valid['inspect_first'] == ['OTHER/consumer.ts']
    brief = server.get_violation_work_queue(project='all', target_root=str(tmp_path), format='brief')
    assert 'path_projection_status: "unresolved"' in brief and 'inspect_first: []' in brief
    assert 'target_ref: "MAIN::consumer.ts"' in brief and 'MAIN finding fixture' in brief


def test_public_queue_missing_live_file_keeps_finding_but_no_inspect_path(tmp_path, monkeypatch):
    _finding_sample(tmp_path, monkeypatch, True)
    (tmp_path / 'MAIN' / 'consumer.ts').unlink()
    payload = json.loads(server.get_violation_work_queue(format='machine'))
    row = payload['items'][0]
    assert payload['total_violations'] == 1 and row['evidence'] == 'MAIN finding fixture'
    assert row['target_file'] == '' and row['inspect_first'] == []
    assert row['target_ref'] == row['atlas_node'] == 'MAIN::consumer.ts'
    assert row['path_projection_status'] == 'unresolved'
    assert row['source_grounding']['exists'] is False
    assert not row['source_grounding'].get('evidence_source_snippets')


def test_queue_unindexed_or_unknown_project_cannot_borrow_other_file(tmp_path, monkeypatch):
    store, _ = _finding_sample(tmp_path, monkeypatch, True)
    rows = server._resolve_work_item_paths_for_agent(store._raw_dir, [
        {'id': 'missing', 'target_project': 'MAIN', 'target_file': 'missing.ts',
         'target_ref': 'MAIN::missing.ts', 'inspect_first': ['missing.ts'], 'evidence': 'missing'},
        {'id': 'unknown', 'target_project': 'ABSENT', 'target_file': 'consumer.ts',
         'target_ref': 'ABSENT::consumer.ts', 'inspect_first': ['consumer.ts'], 'evidence': 'unknown'}])
    assert [row['id'] for row in rows] == ['missing', 'unknown']
    for row in rows:
        assert row['path_projection_status'] == 'unresolved'
        assert row['target_file'] == '' and row['inspect_first'] == []
        assert row['target_ref'] == row['atlas_node']
        assert row['source_grounding']['indexed'] is False


def test_public_queue_projection_preserves_filters_page_counts_and_no_walk(tmp_path, monkeypatch):
    _finding_sample(tmp_path, monkeypatch, True)
    from pathlib import Path
    monkeypatch.setattr(Path, 'iterdir', lambda *_: pytest.fail('queue projection must not walk source'))
    first = json.loads(server.get_violation_work_queue(project='all', page_size=1, format='machine'))
    second = json.loads(server.get_violation_work_queue(project='all', page=2, page_size=1, format='machine'))
    assert first['total_violations'] == second['total_violations'] == 2
    assert [first['items'][0]['target_project'], second['items'][0]['target_project']] == ['MAIN', 'OTHER']
    assert first['items'][0]['id'] != second['items'][0]['id']
    default = json.loads(server.get_violation_work_queue(format='machine'))
    assert default['total_violations'] == 1 and default['items'][0]['target_project'] == 'MAIN'
    for rule in ('monolithic_function%', 'monolithic_function_'):
        assert json.loads(server.get_violation_work_queue(rule=rule, format='machine'))['total_violations'] == 0
    warning = json.loads(server.get_violation_work_queue(project='all', severity='warning', format='machine'))
    assert warning['total_violations'] == 1 and warning['items'][0]['target_project'] == 'OTHER'



@pytest.mark.parametrize('lane', ['verified', 'live_drift', 'tampered_snapshot'])
def test_public_queue_import_paths_and_snippets_require_verified_captured_source(tmp_path, monkeypatch, lane):
    source = 'import { leaf } from "./leaf";\nexport const value = leaf;\n'
    detail = 'consumer.ts imports ./leaf; ownership_root=fixture reason=fixture'
    store, _ = _finding_sample(tmp_path, monkeypatch, True, source=source, main_detail=detail)
    if lane == 'live_drift':
        (tmp_path / 'MAIN' / 'consumer.ts').write_text('export const changed = 2;\n', encoding='utf-8')
    elif lane == 'tampered_snapshot':
        with store.db_manager.get_connection() as conn:
            conn.execute("UPDATE source_snapshots SET content='tampered' WHERE project_key='MAIN' AND rel_path='consumer.ts'")
            conn.commit()
    payload = json.loads(server.get_violation_work_queue(format='machine'))
    row = payload['items'][0]
    assert row['target_file'] == 'MAIN/consumer.ts'
    assert row['inspect_first'] == ['MAIN/consumer.ts', 'MAIN/leaf.ts']
    assert row['evidence'] == detail and row['human_approval_required'] is True
    snippets = row['source_grounding'].get('evidence_source_snippets', [])
    if lane == 'verified':
        assert snippets and snippets[0]['matched_line'] == 1
        assert 'import { leaf }' in snippets[0]['code']
    else:
        assert snippets == []


@pytest.mark.parametrize('failed_field', ['exists', 'inside_root', 'indexed'])
def test_queue_rejected_status_is_retained_without_openable_path(tmp_path, monkeypatch, failed_field):
    store, _ = _finding_sample(tmp_path, monkeypatch, True)
    original_status = server._target_path_status

    def rejected(*args, **kwargs):
        status = original_status(*args, **kwargs)
        status.update({failed_field: False, 'source_snapshot_status': 'boundary_rejected'})
        return status

    monkeypatch.setattr(server, '_target_path_status', rejected)
    original = {'id': 'rejected', 'target_project': 'MAIN', 'target_file': 'consumer.ts',
                'target_ref': 'MAIN::consumer.ts', 'inspect_first': ['consumer.ts'], 'evidence': 'retained'}
    row = server._resolve_work_item_paths_for_agent(store._raw_dir, [original])[0]
    assert row['id'] == 'rejected' and row['evidence'] == 'retained'
    assert row['target_file'] == '' and row['inspect_first'] == []
    assert row['path_projection_status'] == 'unresolved'
    assert row['source_grounding'][failed_field] is False
    assert row['source_grounding']['source_snapshot_status'] == 'boundary_rejected'
    assert row['source_grounding']['target_source_snippets'] == []
    assert original['target_file'] == 'consumer.ts'


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_public_qualified_module_selector_preserves_exact_project_findings(tmp_path, monkeypatch, external, absolute_roots):
    _finding_sample(tmp_path, monkeypatch, absolute_roots)
    target = str(tmp_path) if external else ''
    for project in ('MAIN', 'OTHER'):
        canonical = json.loads(server.check_module_integrity(f'{project}::consumer.ts', target_root=target, format='machine'))
        workspace = json.loads(server.check_module_integrity(f'{project}::{project}/consumer.ts', target_root=target, format='machine'))
        assert canonical == workspace
        assert canonical['status'] == 'impure' and canonical['violation_count'] == 1
        row = canonical['reasoning_trace'][0]
        assert row['evidence'] == f'{project} finding fixture'
        assert row['atlas_node'] == f'{project}::consumer.ts'
        assert row['target_ref'] == f'{project}::{project}/consumer.ts'
        brief = server.check_module_integrity(row['target_ref'], target_root=target, format='brief')
        assert 'status: "impure"' in brief and row['evidence'] in brief
    unqualified = json.loads(server.check_module_integrity('consumer.ts', target_root=target, format='machine'))
    assert unqualified['violation_count'] == (2 if external else 1)


@pytest.mark.parametrize('workspace', [None, '', '../escape.ts', '/foreign/consumer.ts', 'C:/foreign/consumer.ts', 42])
def test_public_qualified_selector_keeps_unresolved_canonical_findings(tmp_path, monkeypatch, workspace):
    _finding_sample(tmp_path, monkeypatch, True, workspace=workspace)
    payload = json.loads(server.check_module_integrity('MAIN::consumer.ts', target_root=str(tmp_path), format='machine'))
    assert payload['status'] == 'impure' and payload['violation_count'] == 1
    row = payload['reasoning_trace'][0]
    assert row['target_ref'] == row['atlas_node'] == 'MAIN::consumer.ts'
    assert row['target_file'] == '' and row['inspect_first'] == []
    assert row['path_projection_status'] == 'unresolved'
    assert row['evidence'] == 'MAIN finding fixture'


def test_public_qualified_selector_rejects_guesses_and_literal_wildcards(tmp_path, monkeypatch):
    _finding_sample(tmp_path, monkeypatch, True)
    for query in ('MAIN::consumer', 'MAIN::MAIN/consumer', 'MAIN::consumer%ts',
                  'MAIN::consumer_ts', 'MAIN::OTHER/consumer.ts', 'OTHER::MAIN/consumer.ts',
                  'MAIN::../consumer.ts', 'MAIN::/consumer.ts'):
        payload = json.loads(server.check_module_integrity(query, target_root=str(tmp_path), format='machine'))
        assert payload['violation_count'] == 0 and payload['reasoning_trace'] == []
    # Project generation authority remains exact; the selector must not
    # normalize a project past the public trust gate.
    unknown_project = json.loads(server.check_module_integrity('main::MAIN/consumer.ts', format='machine'))
    assert unknown_project['status'] == 'INVALID_CONTEXT'


@pytest.mark.parametrize('exact_key', [True, False])
def test_qualified_selector_preserves_exact_key_and_rejects_ambiguous_alias(tmp_path, monkeypatch, exact_key):
    from tools.core.atlas_integrity import build_atlas_commit
    store, atlas = _finding_sample(tmp_path, monkeypatch, True)
    root = tmp_path / 'MAIN' / 'MAIN'
    root.mkdir()
    source = (tmp_path / 'MAIN' / 'consumer.ts').read_text(encoding='utf-8')
    (root / 'consumer.ts').write_text(source, encoding='utf-8')
    key = 'MAIN/consumer.ts' if exact_key else 'alternate.ts'
    atlas['MAIN']['files'][key] = copy.deepcopy(atlas['MAIN']['files']['consumer.ts'])
    atlas['MAIN']['files'][key]['workspace_rel'] = 'MAIN/MAIN/consumer.ts' if exact_key else 'MAIN/consumer.ts'
    atlas['MAIN']['files'][key]['hash'] = hashlib.sha256((root / 'consumer.ts').read_bytes()).hexdigest()
    store.save_raw('atlas', atlas)
    commit = build_atlas_commit(atlas)
    store.save_raw('atlas_commit', commit)
    # Rebind declared scope after changing the Atlas, preserving the real
    # freshness gate rather than weakening it for this collision fixture.
    store.save_raw('analysis_scope_authority', store.load_raw('analysis_scope_authority', {}))
    audit = store.load_raw('audit_report', {})
    audit['artifact_identity']['atlas_snapshot_id'] = commit['snapshot_id']
    audit['violations'].append({'project': 'MAIN', 'file': key,
        'rule': 'monolithic_function', 'severity': 'warning', 'detail': 'exact canonical precedence fixture'})
    store.save_raw('audit_report', audit)
    payload = json.loads(server.check_module_integrity('MAIN::MAIN/consumer.ts', format='machine'))
    assert payload['violation_count'] == int(exact_key), payload
    if exact_key:
        assert payload['reasoning_trace'][0]['atlas_node'] == 'MAIN::MAIN/consumer.ts'
        assert payload['reasoning_trace'][0]['evidence'] == 'exact canonical precedence fixture'
    else:
        assert payload['reasoning_trace'] == []
        canonical = json.loads(server.check_module_integrity('MAIN::consumer.ts', format='machine'))
        assert canonical['violation_count'] == 1


def test_public_qualified_selector_cannot_bypass_stale_generation(tmp_path, monkeypatch):
    store, _ = _finding_sample(tmp_path, monkeypatch, True)
    commit = store.load_raw('atlas_commit', {})
    commit['snapshot_id'] = 'qualified-fixture-new-snapshot-with-no-audit'
    store.save_raw('atlas_commit', commit)
    monkeypatch.setattr(server, '_module_integrity_items_from_sqlite',
                        lambda *args, **kwargs: pytest.fail('stale findings cannot reach selector'))
    for query in ('MAIN::consumer.ts', 'MAIN::MAIN/consumer.ts'):
        payload = json.loads(server.check_module_integrity(query, format='machine'))
        assert payload['status'] == 'INVALID_CONTEXT'
        assert any(row['name'] == 'sqlite_findings:generation_bound_to_current_snapshot'
                   for row in payload['artifact_trust']['failures'])

def _import_transfer_sample(tmp_path, monkeypatch, absolute_roots, raw_source='@local/leaf', *, source=None, kind='named'):
    source = source if source is not None else f'''// import {{ decoy }} from "leaf.ts";
const misleading = "{raw_source}";
import {{ leaf as renamed }} from "{raw_source}";
export const value = renamed;
'''
    store, atlas = _sample(tmp_path, monkeypatch, absolute_roots, source=source)
    # The parser binding is real; this fixture declares the Atlas resolver's
    # module identity. Normal ArtifactStore writes are never rewritten by SQL.
    for project in ('MAIN', 'OTHER'):
        atlas[project]['files']['consumer.ts']['import_records'] = [
            {'source': 'leaf.ts', 'raw_source': raw_source, 'name': 'leaf', 'kind': kind, 'scope': 'top_level'}]
    store.save_raw('atlas', atlas)
    return store, atlas


@pytest.mark.parametrize('raw_source', ['./leaf', '@local/leaf'])
@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_upstream_import_transfer_uses_raw_spelling_and_parser_span(tmp_path, monkeypatch, absolute_roots, external, raw_source):
    store, _ = _import_transfer_sample(tmp_path, monkeypatch, absolute_roots, raw_source)
    target = str(tmp_path) if external else ''
    with store.db_manager.get_connection() as conn:
        before = [tuple(row) for row in conn.execute('SELECT source_file_id, target_file_id, import_specifier FROM dependencies ORDER BY dep_id')]
    assert before[0][2] == 'leaf.ts'
    payload = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=target, format='machine'))
    assert payload['upstream_dependency_count'] == 1
    assert payload['upstream_dependency_files'] == ['MAIN/leaf.ts']
    row = payload['upstream_dependency_evidence'][0]
    assert row['import_specifier'] == raw_source
    assert row['target_ref'] == 'MAIN::MAIN/leaf.ts'
    snippet = row['source_snippets'][0]
    assert snippet['matched_line'] == 3 and snippet['source_lines'] == 'L3-L3'
    assert 'import { leaf as renamed }' in snippet['code'] and 'decoy' not in snippet['code']
    brief = server.trace_upstream_cause('MAIN::MAIN/consumer.ts', target_root=target, format='brief')
    assert f'import_specifier: "{raw_source}"' in brief
    with store.db_manager.get_connection() as conn:
        assert before == [tuple(row) for row in conn.execute('SELECT source_file_id, target_file_id, import_specifier FROM dependencies ORDER BY dep_id')]


@pytest.mark.parametrize('state', ['missing', 'conflicting', 'other_project', 'type_only', 'parser_unavailable',
                                 'stale_metadata', 'tampered_snapshot', 'wrong_kind', 'wrong_scope', 'bad_span', 'boolean_span'])
def test_upstream_import_transfer_unknown_keeps_graph_without_snippet_guess(tmp_path, monkeypatch, state):
    store, atlas = _import_transfer_sample(tmp_path, monkeypatch, True)
    meta = atlas['MAIN']['files']['consumer.ts']
    if state == 'missing':
        meta.pop('import_records')
    elif state == 'conflicting':
        meta['import_records'].append({**meta['import_records'][0], 'source': 'OTHER::consumer.ts'})
    elif state == 'other_project':
        meta['import_records'][0]['source'] = 'OTHER::leaf.ts'
    elif state == 'type_only':
        meta['direct_import_binding_evidence']['records'][0]['typeOnly'] = True
    elif state == 'parser_unavailable':
        meta['direct_import_binding_evidence']['status'] = 'unavailable'
    elif state == 'stale_metadata':
        meta['hash'] = 'foreign-source-hash'
    elif state == 'wrong_kind':
        meta['import_records'][0]['kind'] = 'namespace'
    elif state == 'wrong_scope':
        meta['direct_import_binding_evidence']['records'][0]['bindingScope'] = 'local'
    elif state == 'bad_span':
        meta['direct_import_binding_evidence']['records'][0]['endLine'] = 1000
    elif state == 'boolean_span':
        meta['direct_import_binding_evidence']['records'][0]['line'] = True
    store.save_raw('atlas', atlas)
    if state == 'tampered_snapshot':
        with store.db_manager.get_connection() as conn:
            conn.execute("UPDATE source_snapshots SET content=content || '// tampered' WHERE project_key='MAIN' AND rel_path='consumer.ts'")
            conn.commit()
    payload = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=str(tmp_path), format='machine'))
    assert payload['upstream_dependency_count'] == 1
    assert payload['upstream_dependencies'] == ['MAIN::MAIN/leaf.ts']
    assert payload['upstream_dependency_evidence'] == []
    assert payload['upstream_dependency_evidence_omitted'] == 1


@pytest.mark.parametrize('source,kind,line,token', [
    ('import leaf from "./leaf";\n', 'default', 1, 'import leaf'),
    ('import * as leaf from "./leaf";\n', 'namespace', 1, 'import * as leaf'),
    ('import {\n  leaf as renamed,\n} from "./leaf";\n', 'named', 2, 'leaf as renamed'),
])
def test_upstream_import_transfer_retains_actual_binding_span(tmp_path, monkeypatch, source, kind, line, token):
    _import_transfer_sample(tmp_path, monkeypatch, True, './leaf', source=source, kind=kind)
    payload = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=str(tmp_path), format='machine'))
    row = payload['upstream_dependency_evidence'][0]
    assert row['import_specifier'] == './leaf'
    assert row['source_snippets'][0]['source_lines'] == f'L{line}-L{line}'
    assert token in row['source_snippets'][0]['code']


@pytest.mark.parametrize('source,kind', [
    ('import "./leaf";\n', 'side_effect'),
    ('export { leaf } from "./leaf";\n', 'reexport'),
    ('export async function load() { return import("./leaf"); }\n', 'dynamic'),
    ('import type { leaf } from "./leaf";\n', 'type'),
])
def test_upstream_import_transfer_unsupported_syntax_remains_graph_only(tmp_path, monkeypatch, source, kind):
    _import_transfer_sample(tmp_path, monkeypatch, True, './leaf', source=source, kind=kind)
    payload = json.loads(server.trace_upstream_cause('MAIN::consumer.ts', target_root=str(tmp_path), format='machine'))
    assert payload['upstream_dependency_count'] == 1
    assert payload['upstream_dependency_evidence'] == []
    assert payload['upstream_dependency_evidence_omitted'] == 1


def test_public_queue_projection_cannot_bypass_stale_generation(tmp_path, monkeypatch):
    store, _ = _finding_sample(tmp_path, monkeypatch, True)
    commit = store.load_raw('atlas_commit', {})
    commit['snapshot_id'] = 'queue-fixture-new-snapshot-with-no-audit'
    store.save_raw('atlas_commit', commit)
    monkeypatch.setattr(server, '_resolve_work_item_paths_for_agent',
                        lambda *args, **kwargs: pytest.fail('stale findings cannot reach path projection'))
    payload = json.loads(server.get_violation_work_queue(format='machine'))
    assert payload['status'] == 'INVALID_CONTEXT'
    assert any(row['name'] == 'sqlite_findings:generation_bound_to_current_snapshot'
               for row in payload['artifact_trust']['failures'])


@pytest.mark.parametrize('tool', ['upstream', 'radius', 'inspect'])
@pytest.mark.parametrize('project', ['MAIN', 'OTHER'])
@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('absolute_roots', [False, True])
def test_target_grounding_transfer_public_uses_exact_snapshot_context(
        tmp_path, monkeypatch, tool, project, external, absolute_roots):
    _, atlas = _sample(tmp_path, monkeypatch, absolute_roots)
    target_root = str(tmp_path) if external else ''
    consumer = {'upstream': server.trace_upstream_cause, 'radius': server.get_impact_radius,
                'inspect': server.inspect_file}[tool]
    for target in (f'{project}::consumer.ts', f'{project}::{project}/consumer.ts'):
        payload = json.loads(consumer(target, target_root=target_root, format='machine'))
        status = payload['target_path_status']
        assert status['target_ref'] == f'{project}::{project}/consumer.ts'
        assert status['target_file'] == f'{project}/consumer.ts'
        assert status['exists'] and status['inside_root'] and status['indexed']
        assert status['source_snapshot_status'] == 'ok'
        assert status['drift_check_status'] == 'match'
        assert status['source_snapshot_hash'] == atlas[project]['files']['consumer.ts']['hash']
        assert status['target_source_snippets']
        assert all(row['snippet_status'] == 'included' for row in status['target_source_snippets'])
    brief = consumer(f'{project}::consumer.ts', target_root=target_root, format='brief')
    assert 'source_snapshot_status: "ok"' in brief and 'drift_check_status: "match"' in brief


def test_target_grounding_transfer_retains_context_from_qualified_index_lookup(tmp_path, monkeypatch):
    _, atlas = _sample(tmp_path, monkeypatch, True)
    # First unqualified lookup misses; the existing qualified lookup resolves MAIN.
    status = server._target_path_status(tmp_path / '.raw', 'MAIN/consumer.ts', str(tmp_path))
    assert status['resolved_node'] == 'MAIN::consumer.ts'
    assert status['target_ref'] == 'MAIN::MAIN/consumer.ts'
    assert status['indexed'] and status['exists'] and status['inside_root']
    assert status['source_snapshot_status'] == 'ok' and status['drift_check_status'] == 'match'
    assert status['source_snapshot_hash'] == atlas['MAIN']['files']['consumer.ts']['hash']


@pytest.mark.parametrize('tool', ['upstream', 'radius', 'inspect'])
@pytest.mark.parametrize('external', [False, True])
def test_compact_grounding_pairs_real_parser_span_and_snippet(tmp_path, monkeypatch, tool, external):
    _sample(tmp_path, monkeypatch, True)
    target_root = str(tmp_path) if external else ''
    consumer = {'upstream': server.trace_upstream_cause, 'radius': server.get_impact_radius,
                'inspect': server.inspect_file}[tool]
    payload = json.loads(consumer('OTHER::consumer.ts', target_root=target_root, format='machine'))
    status = payload['target_path_status']
    before = copy.deepcopy(status)
    assert status['target_spans'][0]['start_line'] == 0
    assert status['target_source_snippets'][0]['source_lines'] == 'L3-L3'
    compact = server._bounded_source_grounding_for_agent(status, max_spans=1)
    assert compact['target_spans'][0]['symbol'] == compact['target_source_snippets'][0]['symbol'] == 'Worker'
    assert compact['target_spans'][0]['source_lines'] == 'L3-L3'
    assert compact['target_ref'] == 'OTHER::OTHER/consumer.ts'
    assert compact['target_spans_shown'] == compact['target_source_snippets_shown'] == 1
    assert compact['target_spans_omitted'] == len(status['target_spans']) - 1
    assert compact['target_source_snippets_omitted'] == len(status['target_source_snippets']) - 1
    brief = consumer('OTHER::consumer.ts', target_root=target_root, format='brief')
    # inspect_file owns a separate inspection brief; verify its unchanged authority.
    grounding = ('\n'.join(server._source_grounding_yaml_lines(status, max_spans=1))
                 if tool == 'inspect' else brief.split('source_grounding:', 1)[1])
    assert 'start_line: 3' in grounding and 'source_lines: "L3-L3"' in grounding
    assert 'symbol: "__file_meta__"' not in grounding
    if tool == 'inspect':
        assert payload['one_shot_edit_ready'] is False and 'one_shot_edit_ready: false' in brief
    assert status == before  # Compact selection never rewrites full machine evidence.
    assert json.loads(consumer('OTHER::consumer.ts', target_root=target_root,
                               format='machine'))['target_path_status'] == before


@pytest.mark.parametrize('state,snapshot,drift', [
    ('missing_snapshot', 'missing', 'not_available'),
    ('tampered_snapshot', 'content_mismatch', 'not_available'),
    ('live_drift', 'ok', 'mismatch'),
    ('missing_file', 'ok', 'target_missing'),
    ('unindexed', 'missing', 'not_available'),
    ('wrong_project', 'missing', 'not_available'),
    ('foreign_project', 'missing', 'not_available'),
    ('malformed_workspace', 'missing', 'not_available'),
    ('ambiguous_workspace', 'missing', 'not_available'),
    ('outside_root', 'boundary_rejected', 'not_available'),
])
def test_target_grounding_transfer_preserves_unknown_drift_and_boundary(
        tmp_path, monkeypatch, state, snapshot, drift):
    store, atlas = _sample(tmp_path, monkeypatch, True)
    target = 'MAIN::consumer.ts'
    if state in {'missing_snapshot', 'tampered_snapshot'}:
        with store.db_manager.get_connection() as conn:
            if state == 'missing_snapshot':
                conn.execute("DELETE FROM source_snapshots WHERE project_key='MAIN' AND rel_path='consumer.ts'")
            else:
                conn.execute("UPDATE source_snapshots SET content=content || '// tampered' WHERE project_key='MAIN' AND rel_path='consumer.ts'")
            conn.commit()
    elif state == 'live_drift':
        (tmp_path / 'MAIN' / 'consumer.ts').write_text('export const changed = 1;\n', encoding='utf-8')
    elif state == 'missing_file':
        (tmp_path / 'MAIN' / 'consumer.ts').unlink()
    elif state == 'unindexed':
        (tmp_path / 'MAIN' / 'unindexed.ts').write_text('export const unknown = 1;\n', encoding='utf-8')
        target = 'MAIN::MAIN/unindexed.ts'
    elif state == 'wrong_project':
        target = 'MAIN::OTHER/consumer.ts'
    elif state == 'foreign_project':
        target = 'GHOST::consumer.ts'
    elif state == 'malformed_workspace':
        atlas['MAIN']['files']['consumer.ts']['workspace_rel'] = '../escape.ts'
        store.save_raw('atlas', atlas)
    elif state == 'ambiguous_workspace':
        atlas['MAIN']['files']['duplicate.ts'] = copy.deepcopy(atlas['MAIN']['files']['consumer.ts'])
        store.save_raw('atlas', atlas)
        target = 'MAIN::MAIN/consumer.ts'
    elif state == 'outside_root':
        target = 'MAIN::../escape.ts'
    status = server._target_path_status(store._raw_dir, target, str(tmp_path))
    assert status['source_snapshot_status'] == snapshot
    assert status['drift_check_status'] == drift
    assert status.get('target_source_snippets', []) == []
    if state in {'unindexed', 'wrong_project', 'foreign_project', 'malformed_workspace',
                 'ambiguous_workspace', 'outside_root'}:
        assert status['indexed'] is False
        assert status['source_snapshot_hash'] == ''
    if state == 'outside_root':
        assert status['inside_root'] is False and status['exists'] is False

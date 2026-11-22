"""Recorded Class getter/setter search, never accessor evaluation."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.mcp import server
from tools.tests.test_symbol_class_method_search import _atlas, _class, _method, _search, store


def test_real_parser_recorded_getter_setter_pair_reaches_public_search(monkeypatch, store, tmp_path):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    root = Path(__file__).resolve().parents[2]
    source = ('export class Worker {\n'
              '  get value() { throw new Error("Never execute getter"); }\n'
              '  set value(next: number) { throw new Error("Never execute setter"); }\n'
              '  static get size() { return 1; }\n'
              '  run() {}\n}\n')
    path = tmp_path / 'worker.ts'
    path.write_text(source, encoding='utf-8', newline='')
    parsed = subprocess.run([shutil.which('node'), str(root / 'tools/engines/ast_sequencer.cjs'), str(path)],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert parsed.returncode == 0, parsed.stderr
    symbols = _normalize_polyglot_symbols(json.loads(parsed.stdout), source, language='typescript')
    parent = next(row for row in symbols if row['type'] == 'Class')
    expected = [row for row in parent['member_details'] if row['name'] == 'value']
    assert [row['kind'] for row in expected] == ['getter', 'setter']
    store._save_atlas_to_sqlite(_atlas(symbols))
    output = _search(monkeypatch, store, 'Worker.value')
    assert output.startswith('['), 'Recorded accessor syntax must reach indexed public search: ' + output
    rows = json.loads(output)
    assert [row['type'] for row in rows] == ['ClassGetter', 'ClassSetter']
    assert [row['member_kind'] for row in rows] == ['getter', 'setter']
    assert [(row['line'], row['end_line']) for row in rows] == [(row['line'], row['end_line']) for row in expected]
    assert all(row['atlas_node'] == 'MAIN::worker.ts' for row in rows)
    assert all(row['class_accessor_search']['status'] == 'available' for row in rows)
    assert all(row['class_accessor_search']['source_tree_complete'] is False for row in rows)
    assert all('char' not in row and 'dependencies' not in row for row in rows)
    assert [row['name'] for row in server._find_symbol_matches('Worker.value', 'MAIN', store._raw_dir)] == []


def accessor(name='value', kind='getter', line=2, end_line=3, static=False):
    return {**_method(name, line=line, end_line=end_line, static=static), 'kind': kind}


def test_pair_collisions_static_unicode_and_project_identity(monkeypatch, store):
    atlas = _atlas([_class(members=[accessor(), accessor(kind='setter', line=4, end_line=4),
                                  accessor('計算', line=5, end_line=5, static=True), _method()]),
                    _class('Other', [accessor(line=8, end_line=8)])])
    atlas['COMPANION'] = deepcopy(atlas['MAIN'])
    atlas['COMPANION']['root_path'] = 'packages/companion'
    store._save_atlas_to_sqlite(atlas)
    rows = json.loads(_search(monkeypatch, store, 'value', project='all'))
    assert len(rows) == 6 and len({(r['atlas_node'], r['name'], r['line'], r['member_kind']) for r in rows}) == 6
    assert {r['type'] for r in rows} == {'ClassGetter', 'ClassSetter'}
    assert all(r['class_method_search']['status'] == 'available' for r in rows)
    companion = json.loads(_search(monkeypatch, store, '計算', project='companion'))
    assert companion[0]['name'] == 'Worker.計算' and companion[0]['is_static'] is True
    assert companion[0]['repo_relative_path'] == 'packages/companion/worker.ts'
    assert [r['name'] for r in server._find_class_method_search_matches('run', 'MAIN', store._raw_dir)[0]] == ['Worker.run']


@pytest.mark.parametrize('change', [{'name': '#private'}, {'name': '"quoted"'}, {'name': '[key]'},
    {'line': True}, {'line': 0}, {'line': '2'}, {'end_line': 99}, {'end_line': 1}, {'static': 1}])
def test_malformed_accessor_lowers_only_its_own_coverage(monkeypatch, store, change):
    atlas = _atlas([_class(members=[_method(), accessor('valid'), {**accessor('bad'), **change}])])
    store._save_atlas_to_sqlite(atlas)
    rows, coverage = server._find_class_accessor_search_matches('', 'MAIN', store._raw_dir)
    assert [r['name'] for r in rows] == ['Worker.valid']
    assert coverage['status'] == 'partial'
    assert server._find_class_method_search_matches('run', 'MAIN', store._raw_dir)[1]['status'] == 'available'


def test_non_class_global_property_and_missing_evidence_never_gain_accessor_authority(store):
    atlas = _atlas([_class(members=[accessor(), {'name': 'property', 'kind': 'property'}]),
                    {**_class('Face', [accessor()]), 'type': 'Interface'},
                    {'name': 'store', 'type': 'Variable', 'line': 1, 'end_line': 6,
                     'initializer_member_evidence': {'members': [accessor()]}}])
    atlas['MAIN']['symbols'] = [{**_class('Global', [accessor()]), 'file': 'worker.ts'}]
    atlas['MAIN']['files']['legacy.ts'] = {'symbols': [{'name': 'Legacy', 'type': 'Class', 'line': 1, 'end_line': 2}]}
    store._save_atlas_to_sqlite(atlas)
    rows, coverage = server._find_class_accessor_search_matches('', 'MAIN', store._raw_dir)
    assert [r['name'] for r in rows] == ['Worker.value']
    assert coverage['status'] == 'partial' and coverage['partial_files'] == 1
    with store.db_manager.get_connection() as conn:
        assert not list(conn.execute("SELECT name FROM symbols WHERE name = 'Worker.value'"))


def state(store):
    with store.db_manager.get_connection() as conn:
        return {t: [tuple(row) for row in conn.execute('SELECT * FROM ' + t + ' ORDER BY 1')]
            for t in ('files', 'symbols', 'symbol_search_members', 'symbol_search_accessors', 'source_snapshots')}


@pytest.mark.parametrize('scoped', [False, True])
def test_accessor_projection_rollback_keeps_parent_method_and_snapshot_atomic(monkeypatch, store, scoped):
    from tools.core import artifact_store
    atlas = _atlas([_class(members=[_method(), accessor()])])
    store._save_atlas_to_sqlite(atlas)
    before = state(store)
    original = store._project_class_accessor_search
    def fail(*args):
        original(*args)
        raise RuntimeError('injected accessor projection failure')
    monkeypatch.setattr(store, '_project_class_accessor_search', fail)
    monkeypatch.setattr(artifact_store, '_source_snapshot_projection_scope', lambda: {'MAIN': {'worker.ts'}} if scoped else None)
    with pytest.raises(RuntimeError, match='injected accessor'):
        store._save_atlas_to_sqlite(_atlas([_class(members=[accessor('changed')])]))
    assert state(store) == before


def test_scoped_replacement_deletion_and_full_rebuild_preserve_unrelated_accessors(monkeypatch, store):
    from tools.core import artifact_store
    atlas = _atlas([_class(members=[accessor()])])
    atlas['MAIN']['files']['other.ts'] = deepcopy(atlas['MAIN']['files']['worker.ts'])
    store._save_atlas_to_sqlite(atlas)
    with store.db_manager.get_connection() as conn:
        other = conn.execute("SELECT file_id FROM files WHERE rel_path='other.ts'").fetchone()[0]
        original = [tuple(r) for r in conn.execute('SELECT * FROM symbol_search_accessors WHERE file_id=?', (other,))]
    monkeypatch.setattr(artifact_store, '_source_snapshot_projection_scope', lambda: {'MAIN': {'worker.ts'}})
    atlas['MAIN']['files']['worker.ts']['symbols'] = [_class(members=[accessor('changed')])]
    assert store._save_atlas_to_sqlite(atlas)['atlas_relational_mode'] == 'scoped'
    with store.db_manager.get_connection() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM symbol_search_accessors WHERE file_id=?', (other,))] == original
    assert server._find_class_accessor_search_matches('changed', 'MAIN', store._raw_dir)[0][0]['file'] == 'worker.ts'
    del atlas['MAIN']['files']['worker.ts']
    store._save_atlas_to_sqlite(atlas)
    assert server._find_class_accessor_search_matches('changed', 'MAIN', store._raw_dir)[0] == []
    monkeypatch.setattr(artifact_store, '_source_snapshot_projection_scope', lambda: None)
    atlas['MAIN']['files'] = {}
    store._save_atlas_to_sqlite(atlas)
    assert state(store)['symbol_search_accessors'] == []


@pytest.mark.parametrize('drop_column', [False, True])
def test_legacy_query_is_read_only_and_empty_recreated_table_clears_coverage(monkeypatch, store, drop_column):
    store._save_atlas_to_sqlite(_atlas([_class(members=[_method(), accessor()])]))
    with store.db_manager.get_connection() as conn:
        conn.execute('DROP TABLE symbol_search_accessors')
        if drop_column: conn.execute('ALTER TABLE files DROP COLUMN class_accessor_search_status')
    before = store.db_manager.db_path.read_bytes()
    monkeypatch.setattr(server, '_atlas', lambda **kwargs: pytest.fail('No Atlas materialization in a derived query'))
    rows, coverage = server._find_class_accessor_search_matches('value', 'MAIN', store._raw_dir)
    assert rows == [] and coverage['status'] == 'unavailable'
    assert store.db_manager.db_path.read_bytes() == before
    assert server._find_class_method_search_matches('run', 'MAIN', store._raw_dir)[0]
    store.db_manager.initialize_schema()  # normal owner, not a query-side migration
    rows, coverage = server._find_class_accessor_search_matches('value', 'MAIN', store._raw_dir)
    assert rows == [] and coverage['status'] == 'partial' and coverage['unprojected_files'] == 1


@pytest.mark.parametrize('column,value', [('accessor_kind', 'method'), ('line', 0), ('line', 'bad'),
    ('is_static', 2), ('member_name', '#private'), ('name', 'Foreign.value'), ('declaring_line', 99)])
def test_corrupt_accessor_rows_are_not_presented_as_valid_syntax(store, column, value):
    store._save_atlas_to_sqlite(_atlas([_class(members=[_method(), accessor()])]))
    with store.db_manager.get_connection() as conn:
        conn.execute('UPDATE symbol_search_accessors SET ' + column + ' = ?', (value,))
    rows, coverage = server._find_class_accessor_search_matches('value', 'MAIN', store._raw_dir)
    assert rows == [] and coverage['status'] == 'unavailable'
    assert coverage['reason'] == 'invalid_recorded_accessor_projection'
    assert server._find_class_method_search_matches('run', 'MAIN', store._raw_dir)[0]


def test_accessor_caps_literal_query_ranking_and_brief_do_not_invent_columns(monkeypatch, store):
    store._save_atlas_to_sqlite(_atlas([_class(members=[accessor(f'value_{i}', line=i + 2, end_line=i + 2)
                                                     for i in range(15)])]))
    text = _search(monkeypatch, store, 'value', format='brief')
    assert 'candidate_count: 15' in text and 'shown: 10' in text and 'omitted: 5' in text
    assert 'class_accessor_search:' in text and 'member_kind: "getter"' in text
    assert '    char:' not in text and '    dependencies:' not in text
    assert 'not evaluated values' in text and 'not evidence of absence' in text
    policy = {**server._symbol_search_policy(), 'max_candidate_rows_per_source': 1}
    monkeypatch.setattr(server, '_symbol_search_policy', lambda: policy)
    rows = json.loads(_search(monkeypatch, store, 'value_14'))
    assert rows[0]['member_name'] == 'value_14' and rows[0]['match_score'] == 0
    assert server._find_class_accessor_search_matches('%', 'MAIN', store._raw_dir)[0] == []
    assert server._find_class_accessor_search_matches('_', 'MAIN', store._raw_dir)[0][0]['search_truncated'] is True


@pytest.mark.parametrize('external', [False, True])
def test_real_default_and_external_namespace_routing_never_borrows_global_index(monkeypatch, tmp_path, external):
    from tools.core.artifact_store import ArtifactStore
    policy_source = server.BASE_DIR / 'config/agent_surface_contract.json'
    installation_config = tmp_path / 'installation' / 'config'
    installation_config.mkdir(parents=True)
    shutil.copy(policy_source, installation_config / 'agent_surface_contract.json')
    (installation_config / 'schemas').mkdir()
    shutil.copy(server.BASE_DIR / 'config/schemas/class_accessor_search.schema.json',
        installation_config / 'schemas/class_accessor_search.schema.json')
    monkeypatch.setattr(server, 'BASE_DIR', tmp_path / 'installation')
    monkeypatch.setattr(server, 'RAW_DIR', tmp_path / 'default' / '.raw')
    target = tmp_path / 'target'
    target.mkdir()
    raw = server._raw_dir_for_target(str(target) if external else '')
    isolated = ArtifactStore(raw_dir=raw)
    isolated.use_sqlite, isolated.backend = True, 'hybrid_sqlite'
    isolated._ensure_schema()
    atlas = _atlas([_class(members=[accessor(), accessor(kind='setter', line=4, end_line=4)])])
    atlas['MAIN']['root_path'] = str(target)
    atlas['MAIN']['files']['worker.ts']['workspace_rel'] = 'src/worker.ts'
    isolated.save_raw('atlas', atlas)
    result = server.search_symbols('Worker.value', target_root=str(target) if external else '', format='machine')
    rows = json.loads(result)
    assert [r['type'] for r in rows] == ['ClassGetter', 'ClassSetter']
    assert {r['repo_relative_path'] for r in rows} == {'src/worker.ts'}
    assert all(r['atlas_node'] == 'MAIN::worker.ts' for r in rows)
    if external:
        missing = tmp_path / 'other-target'
        missing.mkdir()
        result = server.search_symbols('Worker.value', target_root=str(missing), format='machine')
        assert 'No symbols found' in result and 'missing_sqlite_index' in result


@pytest.mark.parametrize('variation', ['missing', 'unknown_kind', 'wrong_type', 'wrong_version'])
def test_policy_is_schema_bound_and_unknown_values_cannot_authorize_accessors(tmp_path, monkeypatch, variation):
    policy = deepcopy(server._class_accessor_search_policy())
    if variation == 'unknown_kind': policy['kind_types']['property'] = 'Property'
    if variation == 'wrong_type': policy['kind_types']['getter'] = 'ClassMethod'
    if variation == 'wrong_version': policy['version'] = 'v999'
    payload = {'symbol_search_policy': {} if variation == 'missing' else {'class_accessor_search': policy}}
    monkeypatch.setattr(server, '_agent_surface_contract', lambda: payload)
    with pytest.raises((KeyError, ValueError)):
        server._class_accessor_search_policy()
    rows, coverage = server._find_class_accessor_search_matches('value', 'MAIN', tmp_path)
    assert rows == [] and coverage['reason'] == 'invalid_accessor_policy'


def test_same_line_pair_retains_two_identities_and_unknown_coverage_never_means_absence(monkeypatch, store):
    store._save_atlas_to_sqlite(_atlas([_class(members=[accessor(line=2, end_line=2),
        accessor(kind='setter', line=2, end_line=2)])]))
    rows = json.loads(_search(monkeypatch, store, 'Worker.value'))
    assert [(r['type'], r['line']) for r in rows] == [('ClassGetter', 2), ('ClassSetter', 2)]
    with store.db_manager.get_connection() as conn:
        conn.execute('UPDATE files SET class_accessor_search_status=NULL')
    text = _search(monkeypatch, store, 'unknown', format='brief')
    assert '"status": "partial"' in text and 'not evidence of absence' in text


@pytest.mark.parametrize('name', ['v\\u0061lue', 'a\u200cb', 'a\u200db'])
def test_accessor_identifier_subset_does_not_gain_escaped_or_join_control_names(store, name):
    store._save_atlas_to_sqlite(_atlas([_class(members=[accessor(name)])]))
    rows, coverage = server._find_class_accessor_search_matches('', 'MAIN', store._raw_dir)
    assert rows == [] and coverage['status'] == 'partial'

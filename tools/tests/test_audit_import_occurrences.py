"""Captured AST import navigation, never current edit or native/runtime authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from tools.core import audit_finding_generation as audit_context, contextos_mcp
from tools.core import import_occurrence_evidence as locations
from tools.core.artifact_validator import _validate, ensure_valid_payload
from tools.core.atlas_io import load_atlas_data_with_identity, load_atlas_file_metadata
from tools.core.polyglot_imports import extract_typescript_import_evidence
from tools.core.source_snapshot_reader import read_source_snapshot_binding
from tools.mcp import server
from tools.tests.test_audit_import_subject_evidence import _fixture, CONTENT, MODULE_CONTENT

ROOT = Path(__file__).resolve().parents[2]
UNICODE = '\ufeff// İstanbul 🔎\r\n' + CONTENT.replace('// İstanbul 🔎\n', '').replace('\n', '\r\n')
SOURCES = {
    'panel.ts': CONTENT, 'panel.js': CONTENT, 'unicode.ts': UNICODE,
    'module.ts': MODULE_CONTENT,
    'aliases.ts': "import{View as A, View as B}from'@/widgets/view';\nimport*as Theme from'@/widgets/theme'\n",
    'excluded.ts': "import type { T } from './types';\nimport { type T2, View } from './mixed';\n"
        "const later = import('./dynamic');\nconst req = require('./required');\nexport { V } from './exported';\n"
        "import { Escaped } from './esc\\u0061ped';\nimport { Json } from './json' with {type: 'json'};\n",
    'kinds.ts': "import Default, {View} from './same';\nimport * as Theme from './same';\nimport './same';\n",
    'empty.ts': 'export const value = 1;\n',
    'broken.ts': "import { Broken from './bad';\n",
}


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    directory = tmp_path_factory.mktemp('audit-occurrence-native')
    paths = []
    for name, content in SOURCES.items():
        path = directory / name
        path.write_bytes(content.encode())
        paths.append(path)
    limit = locations.occurrence_policy(audit_context.audit_import_subject_contract())['max_file_rows']
    large = directory / 'budget.ts'
    large.write_bytes(('\n'.join(f"import {{ View as V{i} }} from '@/widgets/view';" for i in range(limit + 3))).encode())
    paths.append(large)
    result = subprocess.run(['node', str(ROOT / 'tools/engines/ast_sequencer.cjs'), '--batch-json', *map(str, paths)],
        capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    return {path.name: {'entries': parsed[str(path)], 'content': path.read_bytes().decode()}
            for path in paths}


def normalize(data, root=None):
    return locations.normalize_import_occurrences(data['entries'], data['content'],
        root_policy=root or audit_context.audit_import_subject_contract(),
        atlas_source_hash=hashlib.sha256(data['content'].encode()).hexdigest(), parser_status='observed')


def file_meta(data, stored):
    return {'hash': hashlib.sha256(data['content'].encode()).hexdigest(),
        'ast_contract_version': locations.AST_CONTRACT_VERSION, 'parser_evidence': {'status': 'observed'},
        'import_records': [{**r, 'raw_source': r['source']} for r in extract_typescript_import_evidence(data['entries'])['records']]}


def verify(data, stored, root=None, meta=None):
    return locations.validate_stored_occurrences(stored, data['content'],
        root_policy=root or audit_context.audit_import_subject_contract(), file_data=meta or file_meta(data, stored))


@pytest.mark.parametrize('name', ['panel.ts', 'panel.js', 'unicode.ts', 'aliases.ts'])
def test_native_duplicate_bindings_and_exact_snapshot_spans(native, name):
    data = native[name]
    stored = normalize(data)
    assert stored['status'] == 'observed'
    assert verify(data, stored) == stored
    raw = data['content'].encode()
    for row in stored['rows']:
        statement, module = row['statement'], row['module']
        assert raw[statement['start']:statement['end']].startswith(b'import')
        assert raw[module['start']:module['end']].decode()[1:-1] == row['record']['source']
    if name != 'aliases.ts':
        assert len(stored['rows']) == 6
        assert len(extract_typescript_import_evidence(data['entries'])['records']) == 5
        repeats = [r for r in stored['rows'] if r['record']['name'] == 'View']
        assert len(repeats) == 2 and repeats[0]['statement'] != repeats[1]['statement']
    else:
        assert len(stored['rows']) == 3
        a, b = stored['rows'][:2]
        assert a['statement'] == b['statement'] and a['binding'] != b['binding']


def test_native_exclusions_and_budget_never_change_semantic_records(native):
    excluded = normalize(native['excluded.ts'])
    assert excluded['status'] == 'partial' and excluded['omitted'] == 2
    assert [r['record']['source'] for r in excluded['rows']] == ['./mixed']
    records = extract_typescript_import_evidence(native['excluded.ts']['entries'])['records']
    assert {'type', 'dynamic', 'require', 'reexport'} <= {r['kind'] for r in records}
    budget = normalize(native['budget.ts'])
    policy = locations.occurrence_policy(audit_context.audit_import_subject_contract())
    assert len(budget['rows']) == policy['max_file_rows'] and budget['omitted'] == 3
    assert verify(native['budget.ts'], budget)['status'] == 'partial'
    assert len(extract_typescript_import_evidence(native['budget.ts']['entries'])['records']) == 1
    assert normalize(native['empty.ts'])['rows'] == []
    assert normalize(native['broken.ts'])['status'] == 'unavailable'


@pytest.mark.parametrize('damage', ['version', 'source', 'policy', 'parser', 'line', 'surrogate', 'literal',
    'statement', 'binding', 'name', 'duplicate', 'reverse', 'bool', 'extra', 'count', 'rows', 'missing_role'])
def test_native_corruption_refuses_atomically(native, damage):
    data = deepcopy(native['unicode.ts'])
    raw = next(r for r in data['entries'] if r.get('name') == '__file_meta__')['moduleImportOccurrences']
    if damage in ('version', 'parser'): raw['version' if damage == 'version' else 'parser_kind'] = 'other'
    elif damage in ('source', 'policy'): raw['source_hash' if damage == 'source' else 'policy_sha256'] = 'f' * 64
    elif damage == 'line': raw['rows'][0]['module']['line'] += 1
    elif damage == 'surrogate': raw['rows'][0]['statement']['start'] = 14
    elif damage == 'literal': raw['rows'][0]['record']['source'] = '@/widgets/other'
    elif damage == 'statement': raw['rows'][0]['statement'] = deepcopy(raw['rows'][0]['binding'])
    elif damage == 'binding': raw['rows'][0]['binding'] = deepcopy(raw['rows'][0]['module'])
    elif damage == 'name': raw['rows'][0]['record']['name'] = 'Style'
    elif damage == 'duplicate': raw['rows'].append(deepcopy(raw['rows'][0]))
    elif damage == 'reverse': raw['rows'].reverse()
    elif damage == 'bool': raw['rows'][0]['module']['line'] = True
    elif damage == 'extra': raw['rows'][0]['runtime'] = 'executed'
    elif damage == 'count': raw['omitted'] = True
    elif damage == 'rows': raw['rows'] = {}
    else: del raw['rows'][0]['name']
    result = normalize(data)
    assert result['status'] == 'unavailable' and result['rows'] == []


@pytest.mark.parametrize('damage', ['utf8_boundary', 'line', 'source', 'atlas', 'policy', 'ast', 'record', 'bool', 'extra', 'omitted'])
def test_stored_coordinate_and_primary_refusals(native, damage):
    data = native['unicode.ts']; stored = normalize(data); meta = file_meta(data, stored)
    if damage == 'utf8_boundary': stored['rows'][0]['statement']['start'] = 1
    elif damage == 'line': stored['rows'][0]['binding']['end_line'] += 1
    elif damage in ('source', 'atlas', 'policy'): stored[{'source': 'source_hash', 'atlas': 'atlas_source_hash', 'policy': 'policy_sha256'}[damage]] = 'f' * 64
    elif damage == 'ast': meta['ast_contract_version'] = 'old'
    elif damage == 'record': meta['import_records'] = []
    elif damage == 'bool': stored['rows'][0]['statement']['line'] = True
    elif damage == 'extra': stored['mutation_authority'] = 'approved'
    else: stored['omitted'] = 1
    with pytest.raises(ValueError): verify(data, stored, meta=meta)


def test_optional_policy_limits_and_unavailable_schema(native):
    root = deepcopy(audit_context.audit_import_subject_contract())
    root['occurrences']['max_source_bytes'] = 1
    assert normalize(native['panel.ts'], root)['status'] == 'unavailable'
    root['occurrences']['max_source_bytes'] = True
    assert normalize(native['panel.ts'], root)['status'] == 'unavailable'
    schema = json.loads((ROOT / 'config/schemas/atlas.schema.json').read_text())
    for value in [normalize(native['panel.ts']), normalize(native['broken.ts'])]:
        errors = []
        _validate(value, schema['$defs']['import_occurrence_evidence'], schema, [], errors)
        assert not errors
    wrong = normalize(native['panel.ts']); wrong['rows'][0]['statement']['start'] = '0'
    errors = []
    _validate(wrong, schema['$defs']['import_occurrence_evidence'], schema, [], errors)
    assert errors


@pytest.mark.parametrize('kind', ['default', 'namespace', 'side_effect'])
def test_primary_record_kind_cannot_borrow_another_binding_span(native, kind):
    data = native['kinds.ts']; stored = normalize(data)
    named = next(r for r in stored['rows'] if r['record']['kind'] == 'named')
    target = next(r for r in stored['rows'] if r['record']['kind'] == kind)
    named['record'] = deepcopy(target['record'])
    if kind == 'side_effect':
        named['binding'] = deepcopy(named['module']); named['name'] = deepcopy(named['module'])
    with pytest.raises(ValueError): verify(data, stored)


@pytest.fixture(params=['panel.ts', 'panel.js', 'module.ts'])
def captured(tmp_path, monkeypatch, native, request):
    data = native[request.param]
    is_module = request.param == 'module.ts'
    if is_module:
        from tools.engines import audit
        monkeypatch.setattr(audit, 'get_module_root_name', lambda: 'lifecycle-modules')
    rel = 'lifecycle-modules/a/Module.ts' if is_module else 'domain/Panel.' + request.param.rsplit('.', 1)[1]
    return _fixture(tmp_path, extract_typescript_import_evidence(data['entries']), monkeypatch,
        rel_path=rel, content=data['content'], profile='FSD_STANDARD' if is_module else 'HEXAGONAL_PURE',
        occurrence_evidence=normalize(data))


def project(data, row, **kwargs):
    return audit_context.project_audit_import_context(row, raw_dir=data['raw'], project='MAIN', rel_path=data['rel_path'], **kwargs)


def test_actual_audit_three_agent_consumers_preserve_findings_and_repeats(captured):
    data = captured
    rows = [r for r in data['report']['violations'] if r.get('finding_evidence', {}).get('status') == 'bound_record_set']
    queue, _, _ = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=50, project='MAIN')
    integrity, _, _ = server._module_integrity_items_from_sqlite(data['raw'], 'MAIN::' + data['rel_path'], max_items=50, target_root=str(data['root']))
    directives = contextos_mcp.build_agent_action_directives(audit_report={'violations': rows}, raw_dir=data['raw'],
        _atlas_read=load_atlas_data_with_identity(data['raw']), max_items=50)
    expected = {r['finding_evidence']['binding_id']: r['finding_evidence'] for r in rows}
    assert rows
    for items in (queue, integrity, directives):
        assert {r.get('finding_evidence', {}).get('binding_id') for r in items if 'finding_evidence' in r} == set(expected)
        for item in items:
            if 'finding_evidence' not in item: continue
            base, view = item['finding_evidence'], item['import_occurrence_evidence']
            assert base == expected[base['binding_id']]
            assert view['status'] == 'bound_occurrences'
            assert view['atlas_snapshot_id'] == data['commit']['snapshot_id']
            assert view['mutation_authority'] == 'none' and view['live_source_currentness'] == 'not_evaluated'
            assert all(s['source_range'] is None and s['occurrence_binding'] == 'not_recorded' for s in base['subjects'])
    assert load_atlas_file_metadata(data['raw'], 'MAIN', data['rel_path'])['import_occurrence_evidence'] == data['meta']['import_occurrence_evidence']


def test_one_captured_read_and_one_validation_per_file_and_live_drift_not_promoted(captured):
    data = captured; memo = {}
    rows = [r for r in data['report']['violations'] if r.get('finding_evidence', {}).get('status') == 'bound_record_set']
    data['source'].write_bytes(b'// changed live source, captured navigation remains historical\n')
    with patch('tools.core.source_snapshot_reader.read_source_snapshot_binding', wraps=read_source_snapshot_binding) as reader, \
            patch.object(locations, 'validate_stored_occurrences', wraps=locations.validate_stored_occurrences) as validator:
        for row in rows:
            value = project(data, row, cache=memo)
            assert value['import_occurrence_evidence']['status'] == 'bound_occurrences'
            assert value['import_occurrence_evidence']['live_source_currentness'] == 'not_evaluated'
        assert reader.call_count == validator.call_count == 1


@pytest.mark.parametrize('damage', ['legacy', 'span', 'policy', 'snapshot', 'base'])
def test_bad_optional_metadata_never_hides_base_finding(captured, damage):
    data = captured
    row = next(r for r in data['report']['violations'] if r.get('finding_evidence', {}).get('status') == 'bound_record_set')
    meta = deepcopy(data['meta'])
    if damage == 'legacy': del meta['import_occurrence_evidence']
    elif damage == 'span': meta['import_occurrence_evidence']['rows'][0]['module']['start'] += 1
    elif damage == 'policy': meta['import_occurrence_evidence']['policy_sha256'] = 'f' * 64
    elif damage == 'base': row = {**row, 'finding_evidence': {**row['finding_evidence'], 'binding_id': 'f' * 64}}
    with patch('tools.core.atlas_io.load_atlas_file_metadata', return_value=meta):
        if damage == 'snapshot':
            with patch('tools.core.source_snapshot_reader.read_source_snapshot_binding', return_value={'status': 'missing'}):
                value = project(data, row)
        else: value = project(data, row)
    assert value['import_occurrence_evidence']['status'] == 'unavailable'
    assert value['import_occurrence_evidence']['rows'] == []
    if damage not in ('snapshot', 'base'): assert value['finding_evidence'] == row['finding_evidence']
    else: assert value['finding_evidence']['status'] == 'unavailable'


def test_projection_discloses_independent_budgets_and_missing_subjects(native):
    stored = normalize(native['panel.ts'])
    policy = locations.occurrence_policy(audit_context.audit_import_subject_contract())
    subjects = [{'id': str(i), 'record': {**r, 'raw_source': r['source']}}
                for i, r in enumerate(extract_typescript_import_evidence(native['panel.ts']['entries'])['records'])]
    base = {'subjects': subjects, 'omitted_subject_count': 2, 'binding_id': 'b', 'project_key': 'MAIN',
        'atlas_rel_path': 'Panel.ts', 'atlas_snapshot_id': 's', 'source_hash': stored['atlas_source_hash']}
    view = locations.project_import_occurrences(base, stored, {**policy, 'max_projection_rows': 1})
    assert view['status'] == 'partial' and view['display_omitted'] == 5 and view['subject_omitted'] == 2
    stored['rows'] = stored['rows'][1:]; stored['omitted'] = 3
    view = locations.project_import_occurrences(base, stored, policy)
    assert view['producer_omitted'] == 3 and view['status'] == 'partial'
    stored['rows'] = []
    view = locations.project_import_occurrences(base, stored, policy)
    assert view['status'] == 'unavailable' and view['subjects_without_locations']


def test_ambiguous_retained_subjects_cannot_borrow_one_location(native):
    stored = normalize(native['panel.ts'])
    record = {'raw_source': '@/widgets/view', 'source': 'widgets/view.ts',
        'name': 'View', 'kind': 'named', 'scope': 'top_level'}
    base = {'subjects': [{'id': 'a', 'record': record},
        {'id': 'b', 'record': {**record, 'source': 'another/view.ts'}}]}
    with pytest.raises(ValueError, match='ambiguous_subject_record_identity'):
        locations.project_import_occurrences(base, stored,
            locations.occurrence_policy(audit_context.audit_import_subject_contract()))


def test_real_atlas_generation_and_sqlite_preserve_optional_import_metadata(tmp_path, monkeypatch):
    from tools.core import artifact_store, config
    from tools.core.artifact_store import ArtifactStore
    from tools.engines import generate_atlas as generator
    root, raw = tmp_path / 'target', tmp_path / 'raw'
    root.mkdir(); raw.mkdir()
    (root / 'package.json').write_text('{"name":"fixture","type":"module"}', encoding='utf-8')
    (root / 'Panel.ts').write_bytes(CONTENT.encode())
    store = ArtifactStore(raw_dir=raw); store.use_sqlite = True; store._ensure_schema()
    monkeypatch.setattr(generator, 'ROOT', root); monkeypatch.setattr(generator, 'RAW_DIR', raw)
    monkeypatch.setattr(generator, 'resolve_runtime_projects', lambda *_a, **_k: {'MAIN': root})
    monkeypatch.setattr(generator, 'configured_project_ownership_exclusions', lambda *_a, **_k: {'MAIN': []})
    monkeypatch.setattr(generator, 'load_previous_atlas', lambda: {})
    monkeypatch.setattr(generator, 'ensure_output_dir', lambda: None)
    monkeypatch.setattr(generator, 'save_json_atomic', lambda path, payload, **kw: store.save_raw(Path(path).stem, payload, **kw))
    monkeypatch.setattr(generator, 'DYNAMIC_CONFIG', {'use_sqlite': True})
    monkeypatch.setattr(artifact_store, 'DYNAMIC_CONFIG', {'project_path_hints': {'MAIN': str(root)}})
    monkeypatch.setattr(artifact_store, 'STORE', store); monkeypatch.setattr(config, 'PROJECT_FILTER', [])
    atlas, _, _ = generator.generate_atlas(stale_projects=['MAIN'])
    ensure_valid_payload('atlas', atlas)
    meta = atlas['MAIN']['files']['Panel.ts']
    assert len(meta['import_records']) == 5 and len(meta['import_occurrence_evidence']['rows']) == 6
    assert meta['import_occurrence_evidence']['status'] == 'observed'
    assert load_atlas_file_metadata(raw, 'MAIN', 'Panel.ts')['import_occurrence_evidence'] == meta['import_occurrence_evidence']
    snapshot = read_source_snapshot_binding(raw, 'MAIN', 'Panel.ts', expected_hash=meta['hash'])
    assert snapshot['status'] == 'ok' and snapshot['content'] == CONTENT
    locations.validate_stored_occurrences(meta['import_occurrence_evidence'], snapshot['content'],
        root_policy=audit_context.audit_import_subject_contract(), file_data=meta)
    assert atlas['MAIN']['dependencies']['Panel.ts'] == meta['internal_deps']

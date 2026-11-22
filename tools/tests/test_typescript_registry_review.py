"""One finite native batch and isolated primary/snapshot controls; not runtime proof."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tools.core import declarative_registry_review as review
from tools.core import typescript_registry_review as adapter
from tools.core import typescript_registry_contract as contract_guard
from tools.core.artifact_store import ArtifactStore
from tools.core.atlas_io import load_atlas_data
from tools.core.source_coordinates import _utf8_span_validator
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.engines import generate_atlas as producer

ROOT = Path(__file__).resolve().parents[2]
LANE = 'literal_registry_evidence'
CASES = {
    'plain': "export const genres = { fiction: { id: 'fiction' }, bad: { id: 'wrong' }, separator: { id: '' } };",
    'typed': "export const genres: Record<string, Row> = { /*a*/ fiction: {id: 'fiction', note: unknown()}, };",
    'unicode': "\ufeff// 🚀\r\nexport const genres = { 'öykü/1': { id: 'other' }, '': { id: '' } };\r\nthrow new Error('never execute');",
    'empty': 'const genres = {};',
    'opaque': "const genres = {a: {id: getId()}, b: {}, c: {id: 3}, d: {id: `d`}, e: {id: 'e' as string}};",
    'mutated': "const genres = {a: {id: 'a'}}; genres.a.id = 'changed';",
    'spread': "const genres = {a: {id: 'a'}, ...other};",
    'row_spread': "const genres = {a: {id: 'a', ...other}};",
    'computed': "const genres = {[key]: {id: 'a'}};",
    'row_computed': "const genres = {a: {[key]: 'a'}};",
    'getter': "const genres = {get a() {return {id:'a'}}};",
    'row_getter': "const genres = {a: {get id() {return 'a'}}};",
    'duplicate': "const genres = {a: {id:'a'}, 'a': {id:'other'}};",
    'field_duplicate': "const genres = {a: {id:'a', 'id':'other'}};",
    'escape': "const genres = {'\\u0061': {id:'a'}};",
    'escaped_value': "const genres = {a: {id:'\\u0061'}};",
    'proto': "const genres = {__proto__: {id:'__proto__'}};",
    'proto_value': "const genres = {a: {id:'__proto__'}};",
    'wrapped': "const genres = {a: {id:'a'}} as const;",
    'satisfies': "const genres = {a: {id:'a'}} satisfies Map;",
    'let': "let genres = {a: {id:'a'}};",
    'nested': "function build() {const genres = {a: {id:'a'}};}",
    'multiple': "const genres = {a: {id:'a'}}, extra = 1;",
    'syntax': "const genres = { a: {id: 'a'};",
    'source_budget': '//' + 'x' * 1048576 + "\nconst genres = {};",
    'node_budget': 'void 0;\n' * 4000,
    'table_budget': '\n'.join(f'const table{i} = {{}};' for i in range(17)),
    'row_budget': 'const genres = {' + ','.join(f"k{i}: {{id:'k{i}'}}" for i in range(257)) + '};',
    'field_budget': 'const genres = {a: {' + ','.join(f"f{i}: 'x'" for i in range(33)) + '}};',
    'storage_budget': 'const genres = {' + ','.join('k%d: {' % i + ','.join("f%d: '%s'" % (j, 'x'*128) for j in range(10)) + '}' for i in range(80)) + '};',
    'display': 'const genres = {' + ','.join(f"k{i}: {{id:'wrong'}}" for i in range(55)) + '};',
}


def _write(path, value):
    path = Path(native_filesystem_path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8'))


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    target = tmp_path_factory.mktemp('ts-registry-native')
    paths = []
    for name, text in CASES.items():
        path = target / (name + '.ts')
        _write(path, text.encode('utf-8'))
        paths.append(str(path))
    run = subprocess.run([shutil.which('node'), str(ROOT/'tools/engines/ast_sequencer.cjs'), '--batch-json', *paths],
                         cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    return {name: payload[str(target/(name+'.ts'))] for name in CASES}


def _lane(native, name):
    text, context = CASES[name], {}
    producer._normalize_polyglot_symbols(native[name], text, 'typescript', file_evidence_context=context)
    return producer._literal_registry_evidence(native[name], text, context)


@pytest.mark.parametrize('name', ['plain', 'typed', 'unicode', 'empty', 'opaque', 'mutated', 'escaped_value', 'proto_value'])
def test_literal_source_normalization_and_schema(native, name):
    import jsonschema
    lane = _lane(native, name)
    assert lane['status'] == 'observed', lane
    policy, digest, width = contract_guard.registry_source_policy()
    valid = _utf8_span_validator(CASES[name], list(contract_guard.registry_validation_spans(lane, policy['limits'])))
    assert contract_guard.registry_lane_problem(lane, valid, identity=hashlib.sha256(CASES[name].encode('utf-8')).hexdigest(),
        policy=policy, policy_hash=digest, name_limit=width, ast_version=producer.AST_CONTRACT_VERSION) is None
    schema = json.loads((ROOT/'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator({'$ref':'#/$defs/literal_registry_evidence', '$defs':schema['$defs']}).validate(lane)
    assert lane['records'][0]['name'] == 'genres'


@pytest.mark.parametrize('name', ['spread', 'row_spread', 'computed', 'row_computed', 'getter', 'row_getter', 'duplicate', 'field_duplicate', 'escape', 'proto', 'field_budget'])
def test_unsupported_properties_withhold_entire_table(native, name):
    lane = _lane(native, name)
    assert lane['status'] == 'observed'
    assert lane['records'][0]['status'] == 'unsupported' and lane['records'][0]['rows'] == []


@pytest.mark.parametrize('name', ['wrapped', 'satisfies', 'let', 'nested', 'multiple'])
def test_non_direct_const_table_is_not_discovered(native, name):
    lane = _lane(native, name)
    assert lane['status'] == 'observed' and lane['records'] == []


@pytest.mark.parametrize('name,reason', [('syntax','parse_diagnostics'), ('source_budget','source_byte_budget_exceeded'),
    ('node_budget','node_budget_exceeded'), ('table_budget','table_budget_exceeded'), ('row_budget','row_budget_exceeded'), ('storage_budget','stored_byte_budget_exceeded')])
def test_native_budget_refusal_is_never_partial_compliance(native, name, reason):
    raw = next(r for r in native[name] if r['name'] == '__file_meta__')['literalRegistryEvidence']
    assert raw['status'] == 'unavailable' and raw['reason'] == reason and raw['records'] == []
    assert _lane(native, name)['status'] == 'unavailable'


def test_one_existing_coordinate_batch_transports_registry_spans(native, monkeypatch):
    original, calls = producer._utf16_offsets_to_utf8_byte_offsets, []
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(producer, '_utf16_offsets_to_utf8_byte_offsets', counted)
    lane = _lane(native, 'unicode')
    assert len(calls) == 1
    span = lane['records'][0]['rows'][0]['spans']['key']
    assert CASES['unicode'].encode('utf-8')[span['start']:span['end']].decode('utf-8') == "'öykü/1'"


@pytest.mark.parametrize('damage', ['hidden_row', 'hidden_field', 'omitted_row', 'omitted_field', 'extra_key', 'hash', 'policy', 'version', 'span', 'bool_offset', 'malformed_kind', 'malformed_status', 'count'])
def test_all_rows_source_tokens_and_exact_header_are_validated_before_selection(native, damage):
    raw = deepcopy(native['plain'])
    lane = next(r for r in raw if r['name'] == '__file_meta__')['literalRegistryEvidence']
    table = lane['records'][0]
    if damage == 'hidden_row': table['rows'][-1]['key'] = 'foreign'
    elif damage == 'hidden_field': table['rows'][-1]['fields'][0]['value'] = 'foreign'
    elif damage == 'omitted_row': table['rows'].pop()
    elif damage == 'omitted_field': table['rows'][0]['fields'].clear()
    elif damage == 'extra_key': lane['runtime_proof'] = 'yes'
    elif damage == 'hash': lane['source_hash'] = '0'*64
    elif damage == 'policy': lane['policy_sha256'] = '0'*64
    elif damage == 'version': lane['version'] = 'legacy'
    elif damage == 'span': table['rows'][-1]['fields'][0]['spans']['value']['end'] -= 1
    elif damage == 'bool_offset': table['spans']['binding']['start'] = True
    elif damage == 'malformed_kind': table['rows'][0]['fields'][0]['key_kind'] = []
    elif damage == 'malformed_status': table['status'] = {}
    else: lane['record_count'] = True
    assert producer._literal_registry_evidence(raw, CASES['plain'])['status'] == 'unavailable'


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    target = tmp_path_factory.mktemp('ts-registry-generated')
    _write(target/'genres.ts', CASES['unicode'].encode('utf-8'))
    _declare(target)  # Cache comparison must not add a new auxiliary JSON input later.
    env = dict(os.environ, CODEMAPS_TARGET_ROOT=str(target))
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT','CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256', 'CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE','CODEMAPS_TARGET_PROJECTS'):
        env.pop(key, None)
    run = subprocess.run([sys.executable, '-B', '-m', 'tools.engines.generate_atlas'], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    raw = adapter._target_raw_dir(target)
    atlas = load_atlas_data(raw)
    assert atlas['MAIN']['files']['genres.ts'][LANE]['status'] == 'observed'
    return target, raw, atlas


def _declare(root, *, text=None, exceptions=None):
    if text is not None: _write(root/'genres.ts', text.encode('utf-8'))
    value = {'version':'v1-typescript-literal-key-id',
        'source':{'path':'genres.ts', 'sha256':hashlib.sha256(Path(native_filesystem_path(root/'genres.ts')).read_bytes()).hexdigest(),
                  'project':'MAIN', 'atlas_path':'genres.ts', 'symbol':'genres'},
        'id_field':'id', 'exceptions':exceptions or []}
    _write(root/'contract.json', value)
    return value


def _run(root):
    return review.review_declarative_registry(root, 'contract.json')


def test_actual_generation_selected_sqlite_snapshot_and_existing_cli(generated, monkeypatch, capsys):
    from tools import inspect_target
    target, raw, _atlas = generated
    _declare(target)
    before = (target/'genres.ts').read_bytes()
    result = _run(target)
    assert result['status'] == 'review_required', result
    assert result['counts'] == {'matched':1, 'mismatched':1, 'excepted':0, 'unresolved':0}
    assert result['atlas_binding'] == 'verified_selected_file_and_snapshot'
    assert result['initializer_value_semantics'] == 'not_runtime_state'
    assert result['source_binding'] == 'pinned_bytes_rechecked'
    assert result['coverage'] == 'all_rows_of_selected_literal_initializer_only'
    row = result['rows'][0]
    assert 'pointer' not in row and row['key'] == 'öykü/1'
    span = row['source_span']
    assert before[span['start']:span['end']] == b"'other'"
    assert row['source_followup'] == {'tool':'inspect_file', 'file_path':'MAIN::genres.ts', 'line_start':2, 'line_end':2}
    assert (raw/'codemaps.db').is_file()
    monkeypatch.setattr(inspect_target, 'write_inspection', lambda *a: pytest.fail('No registry artifact writer'))
    monkeypatch.setattr(sys, 'argv', ['inspect_target','--registry-contract','contract.json','--target-root',str(target)])
    assert inspect_target.main() == 1
    assert json.loads(capsys.readouterr().out) == result
    assert (target/'genres.ts').read_bytes() == before


def _primary(tmp_path, monkeypatch, generated, *, text=None, lane=None, damage='none', partitioned=False, long_path=False):
    target, _, original = generated
    local = tmp_path/'target'
    if long_path: local = local/('deep-'*22)/('nested-'*15)
    _declare(local, text=text or CASES['unicode'])
    atlas = deepcopy(original)
    atlas['MAIN']['root_path'] = str(local)
    info = atlas['MAIN']['files']['genres.ts']
    if lane is not None:
        info[LANE] = deepcopy(lane)
        info['hash'] = producer._atlas_text_hash(text)
    if damage == 'missing_lane': info.pop(LANE)
    elif damage == 'ast': info['ast_contract_version'] = 'old'
    elif damage == 'path': info['repo_relative_path'] = 'foreign.ts'
    elif damage == 'project': info['project_key'] = 'other'
    elif damage == 'parser': info['parser_evidence'] = []
    elif damage == 'reported': info['parser_evidence']['reported_by_adapter'] = 1
    elif damage == 'diagnostic': info['parser_evidence']['parser_diagnostic_count'] = False
    elif damage == 'missing_hash': info.pop('hash')
    elif damage == 'policy': info[LANE]['policy_sha256'] = '0'*64
    elif damage == 'hidden': info[LANE]['records'][0]['rows'][-1]['key'] = 'foreign'
    raw = tmp_path/'.raw'
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table('atlas', atlas)
    store._save_atlas_to_sqlite(atlas)
    _write(raw/'atlas.json', original)  # A valid shadow must not rescue damaged primary.
    with sqlite3.connect(raw/'codemaps.db') as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == 'missing_snapshot': conn.execute('DELETE FROM source_snapshots')
        elif damage == 'bad_snapshot': conn.execute("UPDATE source_snapshots SET content=content || '//changed'")
        elif damage == 'corrupt_primary':
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
    if damage == 'raw_drift': _write(local/'genres.ts', b'const genres = {};')
    monkeypatch.setattr(adapter, '_target_raw_dir', lambda *a: raw)
    return local


@pytest.mark.parametrize('partitioned', [False, True])
@pytest.mark.parametrize('damage', ['none','missing_lane','ast','path','project','parser','reported','diagnostic','missing_hash','policy','hidden','missing_snapshot','bad_snapshot','corrupt_primary','raw_drift'])
def test_primary_snapshot_live_identity_refusals(generated, tmp_path, monkeypatch, damage, partitioned):
    root = _primary(tmp_path, monkeypatch, generated, damage=damage, partitioned=partitioned, long_path=damage=='none')
    result = _run(root)
    assert result['status'] == ('review_required' if damage == 'none' else 'unavailable'), result
    if damage != 'none': assert result['rows'] == [] and result['source_binding'] == 'unavailable'


@pytest.mark.parametrize('name,status', [('plain','review_required'), ('typed','checked_within_declared_scope'),
    ('empty','empty_declared_registry'), ('opaque','incomplete_evidence'), ('mutated','checked_within_declared_scope'),
    ('spread','unavailable'), ('let','unavailable'), ('escaped_value','incomplete_evidence')])
def test_common_comparison_and_initializer_only_semantics(native, generated, tmp_path, monkeypatch, name, status):
    root = _primary(tmp_path, monkeypatch, generated, text=CASES[name], lane=_lane(native, name))
    result = _run(root)
    assert result['status'] == status, result
    assert result['runtime_proof'] == 'not_performed' and result['mutation_proposed'] is False


@pytest.mark.parametrize('kind', ['exact','stale','missing','redundant'])
def test_explicit_exception_semantics_shared_with_json(generated, tmp_path, monkeypatch, kind):
    root = _primary(tmp_path, monkeypatch, generated)
    exception = {'key':'öykü/1', 'expected_id':'other', 'reason':'Target declared placeholder'}
    if kind == 'stale': exception['expected_id'] = 'changed'
    elif kind == 'missing': exception['key'] = 'missing'
    elif kind == 'redundant': exception.update(key='', expected_id='')
    _declare(root, exceptions=[exception])
    result = _run(root)
    assert result['status'] == ('checked_within_declared_scope' if kind == 'exact' else 'unavailable'), result
    if kind == 'exact': assert result['counts']['excepted'] == 1 and result['rows'][0]['reason'] == exception['reason']


def test_primary_rechecked_after_comparison_and_target_isolation(generated, tmp_path, monkeypatch):
    root = _primary(tmp_path, monkeypatch, generated)
    original, reads = adapter.load_atlas_file_metadata, []
    def drifting(*args):
        value = original(*args)
        reads.append(1)
        if len(reads) == 2: value['hash'] = 'changed'
        return value
    monkeypatch.setattr(adapter, 'load_atlas_file_metadata', drifting)
    assert _run(root)['reason'] == 'typescript_primary_or_snapshot_changed_during_review'
    monkeypatch.undo()
    other = tmp_path/'other'
    _declare(other, text=CASES['unicode'])
    assert _run(other)['status'] == 'unavailable'


def test_policy_corruption_stays_unavailable_without_json_regression(generated, tmp_path, monkeypatch):
    root = _primary(tmp_path, monkeypatch, generated)
    path = tmp_path/'policy.json'
    policy = deepcopy(review._policy())
    policy['typescript'] = []
    _write(path, policy)
    monkeypatch.setattr(review, 'POLICY_PATH', path)
    assert _run(root)['status'] == 'unavailable'
    monkeypatch.setattr(contract_guard, 'POLICY_PATH', path)
    with pytest.raises(ValueError, match='policy_invalid'): contract_guard.registry_source_policy()


def test_cache_and_staging_identity_preserve_lane_and_reject_changed_policy(generated, tmp_path, monkeypatch):
    target, raw, atlas = generated
    info = atlas['MAIN']['files']['genres.ts']
    assert producer.file_contract_is_current(info)
    env = dict(os.environ, CODEMAPS_TARGET_ROOT=str(target))
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT','CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256','CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE','CODEMAPS_TARGET_PROJECTS'):
        env.pop(key, None)
    run = subprocess.run([sys.executable, '-B', '-m', 'tools.engines.generate_atlas'], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    after = load_atlas_data(raw)
    assert after == atlas, [(key, after['MAIN'].get(key), atlas['MAIN'].get(key)) for key in after['MAIN'] if after['MAIN'].get(key) != atlas['MAIN'].get(key)]
    original = producer.atlas_staging_producer_contract()
    policy = tmp_path/'policy.json'
    _write(policy, review._policy())
    monkeypatch.setattr(contract_guard, 'POLICY_PATH', policy)
    assert not producer.file_contract_is_current(info)  # New raw policy bytes, same parsed meaning.
    real_read = Path.read_bytes
    def changed(path):
        data = real_read(path)
        return data + b' ' if path == contract_guard.POLICY_PATH else data
    # The actual producer path list binds its source policy as well as the helper.
    monkeypatch.setattr(contract_guard, 'POLICY_PATH', ROOT/'config/declarative_registry_review_contract.json')
    monkeypatch.setattr(Path, 'read_bytes', changed)
    assert producer.atlas_staging_producer_contract() != original


def test_display_omissions_never_hide_unvalidated_rows(native, generated, tmp_path, monkeypatch):
    lane = _lane(native, 'display')
    root = _primary(tmp_path, monkeypatch, generated, text=CASES['display'], lane=lane)
    value = _run(root)
    assert value['status'] == 'review_required', value.get('reason')
    assert value['counts']['mismatched'] == 55 and value['shown'] == 50 and value['display_omitted'] == 5
    assert len(json.dumps(value, ensure_ascii=False).encode('utf-8')) <= review._policy()['limits']['output_bytes']
    lane['records'][0]['rows'][-1]['fields'][0]['value'] = 'foreign'
    root = _primary(tmp_path/'damaged', monkeypatch, generated, text=CASES['display'], lane=lane)
    assert _run(root)['status'] == 'unavailable'


@pytest.mark.parametrize('damage', ['project','atlas_path','symbol','unknown_version','unknown_source_key'])
def test_exact_target_declaration_selectors_cannot_borrow_or_infer_evidence(generated, tmp_path, monkeypatch, damage):
    root = _primary(tmp_path, monkeypatch, generated)
    declaration = _declare(root)
    if damage in {'project','symbol'}: declaration['source'][damage] = 'foreign'
    elif damage == 'atlas_path': declaration['source'][damage] = '../genres.ts'
    elif damage == 'unknown_version': declaration['version'] = 'v2'
    else: declaration['source']['raw_dir'] = str(tmp_path/'.raw')
    _write(root/'contract.json', declaration)
    assert _run(root)['status'] == 'unavailable'

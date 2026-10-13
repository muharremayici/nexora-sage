"""Const property alias call sites are source associations, never runtime proof."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tools.engines.generate_atlas import AST_CONTRACT_VERSION, _normalize_polyglot_symbols
from tools.tests.test_state_flow_call_result_property_binding_contract import _native

ROOT = Path(__file__).resolve().parents[2]
KEY = 'call_result_alias_invocation_evidence'
FOCUS = 'call_result_alias_invocations'
BINDING = 'call_result_property_binding_evidence'
PLAIN = 'const { refetch } = useUser();'
CASES = {
    'plain': PLAIN + ' refetch();',
    'renamed': 'const { refetch: run } = useUser(); run();',
    'import_alias': 'const { refetch: run } = getUser(); run();',
    'unicode': 'const { refetch: çağrı } = useUser(); çağrı();',
    'repeated': PLAIN + ' refetch(); refetch(1);',
    'multiple': 'const { refetch, save: run } = useUser(); refetch(); run();',
    'no_invocation': PLAIN,
    'shadow': PLAIN + ' function child(refetch: any) { refetch(); }',
    'nested': PLAIN + ' later(() => { refetch(); });',
    'branch': PLAIN + ' if (ready) { refetch(); }',
    'before': 'refetch(); ' + PLAIN,
    'optional': PLAIN + ' refetch?.();',
    'generic': PLAIN + ' refetch<any>();',
    'spread': PLAIN + ' refetch(...args);',
    'returned': PLAIN + ' return refetch();',
    'void': PLAIN + ' void refetch();',
    'parenthesized': PLAIN + ' (refetch());',
    'callee_wrapped': PLAIN + ' (refetch)();',
    'block': PLAIN + ' { refetch(); }',
    'binding_write': PLAIN + ' refetch = other; refetch();',
    'producer_write': PLAIN + ' useUser = other; refetch();',
    'duplicate': PLAIN + ' const refetch = other; refetch();',
    'let': PLAIN.replace('const', 'let') + ' refetch();',
    'rest': 'const { refetch, ...rest } = useUser(); refetch();',
    'default': 'const { refetch = other } = useUser(); refetch();',
    'type_only': PLAIN.replace('useUser()', 'typeUser()') + ' refetch();',
    'namespace': PLAIN.replace('useUser()', 'Hooks.useUser()') + ' refetch();',
    'escaped_call': PLAIN + r' ref\u0065tch();',
    'different_binding': PLAIN + ' const run = other; run();',
    'deferred_on_success': 'const { refetch: refetchUser } = useUser(); useMutation({ onSuccess: (...args) => { refetchUser(); onSuccess?.(...args); } });',
}
POSITIVES = {'plain', 'renamed', 'import_alias', 'unicode', 'repeated', 'multiple', 'arrow'}


@pytest.fixture(scope='module')
def invocation_inputs(tmp_path_factory):
    owners = [f'export function Case_{name}() {{ {body} }}' for name, body in CASES.items()]
    owners += [f'export const Case_arrow = () => {{ {CASES["plain"]} }};',
               f'export function Case_parameter_collision(refetch: any) {{ {CASES["plain"]} }}',
               f'export async function Case_async() {{ {CASES["plain"]} }}',
               f'export function* Case_generator() {{ {CASES["plain"]} }}',
               'export function olderLanes() { const first = () => {}; const second = () => { first(); }; const cb = useCallback(() => {}, []); return { cb }; }',
               'export function jsxLane() { const done = () => {}; return <Child onDone={done} />; }',
               "import { useUser, useUser as getUser } from './users';",
               "import type { useUser as typeUser } from './users'; import * as Hooks from './users';",
               "import { useCallback } from 'react'; import { Child } from './child';",
               "throw new Error('Source must never execute');"]
    source = '\ufeff// \U0001f680 CRLF identity\r\n' + '\r\n'.join(owners)
    path = tmp_path_factory.mktemp('alias-invocation') / 'Cases.tsx'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    rows = {row['name']: row for row in _normalize_polyglot_symbols(raw, source, 'typescript')}
    return source, raw, rows


@pytest.mark.parametrize('name', sorted(POSITIVES))
def test_native_positive(invocation_inputs, name):
    source, _, rows = invocation_inputs
    lane = rows['Case_' + name][KEY]
    assert lane['status'] == 'observed' and lane['omitted'] == 0
    assert len(lane['records']) == (2 if name in {'repeated', 'multiple'} else 1)
    assert lane['source_hash'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    assert lane['ast_contract_version'] == AST_CONTRACT_VERSION
    for row in lane['records']:
        assert len(row['spans']) == 17 and 'target' not in row and 'calls' not in row
        for role, text in (('invoked_identifier', row['local_name']), ('local_binding', row['local_name']),
                           ('property_name', row['property_name']), ('callee', row['localName'])):
            span = row['spans'][role]
            assert source.encode('utf-8')[span['start']:span['end']].decode('utf-8') == text
        assert row['spans']['declaration']['end'] < row['spans']['invocation_statement']['start']
    assert lane['runtime_execution'] == 'not_established'


@pytest.mark.parametrize('name', sorted(set(CASES) - POSITIVES) + ['parameter_collision', 'async', 'generator'])
def test_native_negative(invocation_inputs, name):
    assert invocation_inputs[2]['Case_' + name][KEY]['records'] == []


@pytest.mark.parametrize('damage', ['hash', 'version', 'authority', 'offset', 'bool_span', 'lines',
                                  'missing_role', 'extra_role', 'order', 'duplicate', 'status', 'omitted',
                                  'parser', 'imports', 'token', 'binding_role', 'before_binding', 'extra_field'])
def test_normalizer_damage(invocation_inputs, damage):
    source, original, _ = invocation_inputs
    raw = deepcopy(original)
    owner = next(row for row in raw if row['name'] == 'Case_plain')
    lane = owner[KEY]
    row = lane['records'][0]
    if damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'version': lane['version'] = 'old'
    elif damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'offset': lane['offset_unit'] = 'utf8_bytes'
    elif damage == 'bool_span': row['spans']['invoked_identifier']['start'] = True
    elif damage == 'lines': row['spans']['invoked_identifier']['line'] += 1
    elif damage == 'missing_role': row['spans'].pop('invocation')
    elif damage == 'extra_role': row['spans']['other'] = row['spans']['callee']
    elif damage == 'order': row['spans']['invocation_statement'] = row['spans']['local_binding']
    elif damage == 'duplicate': lane['records'].append(deepcopy(row))
    elif damage == 'status': lane['status'] = []
    elif damage == 'omitted': lane['omitted'] = -1
    elif damage == 'parser': owner['parserKind'] = 'fallback'
    elif damage == 'imports': next(row for row in raw if row['name'] == '__file_meta__')['directImportBindings'] = []
    elif damage == 'token': row['local_name'] = 'other'
    elif damage == 'binding_role': row['spans'].pop('pattern')
    elif damage == 'before_binding': row['spans']['declaration'] = row['spans']['invocation_statement']
    elif damage == 'extra_field': row['callability'] = 'proven'
    result = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == 'Case_plain')[KEY]
    assert result['status'] == 'unavailable' and result['records'] == []


def test_shared_offset_pass_and_prior_lanes(invocation_inputs, monkeypatch):
    from tools.engines import generate_atlas
    source, raw, _ = invocation_inputs
    original, calls = generate_atlas._utf16_offsets_to_utf8_byte_offsets, []
    def track(content, offsets):
        calls.append(1)
        return original(content, offsets)
    monkeypatch.setattr(generate_atlas, '_utf16_offsets_to_utf8_byte_offsets', track)
    rows = {row['name']: row for row in generate_atlas._normalize_polyglot_symbols(raw, source, 'typescript')}
    assert len(calls) == 1 and rows['Case_unicode'][KEY]['records']
    assert len(rows['Case_plain'][BINDING]['records'][0]['spans']) == 14
    assert len(rows['olderLanes']['local_arrow_sibling_call_evidence']['records']) == 1
    assert len(rows['olderLanes']['returned_local_callback_evidence']['records']) == 1
    assert len(rows['jsxLane']['jsx_prop_callback_evidence']['records']) == 1
    assert all(call['localName'] != 'refetch' for call in rows['Case_plain']['import_call_evidence']['calls'])


@pytest.mark.parametrize('damage', ['none', 'missing', 'hash', 'ast', 'parser', 'authority', 'bool_span',
                                  'outer', 'duplicate', 'omitted', 'incomplete', 'budget', 'roles', 'extra_field'])
def test_focus_integrity(invocation_inputs, damage):
    from tools.core.state_flow import _focus_call_result_alias_invocations
    source, _, rows = invocation_inputs
    row = deepcopy(rows['Case_plain'])
    record = {'hash': hashlib.sha256(source.encode('utf-8')).hexdigest(), 'size': len(source.encode('utf-8')),
              'loc': len(source.splitlines()), 'parser_evidence': {'status': 'observed'}, 'ast_contract_version': AST_CONTRACT_VERSION}
    lane = row[KEY]
    if damage == 'missing': row.pop(KEY)
    elif damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'ast': record['ast_contract_version'] = 'old'
    elif damage == 'parser': record['parser_evidence']['status'] = 'degraded'
    elif damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'bool_span': lane['records'][0]['spans']['invocation']['end'] = True
    elif damage == 'outer': row['start'] += 1
    elif damage == 'duplicate': lane['records'].append(deepcopy(lane['records'][0]))
    elif damage == 'omitted': lane['omitted'] = -1
    elif damage == 'incomplete': lane.update(status='incomplete_scan', omitted=1)
    elif damage == 'roles': lane['records'][0]['spans'].pop('invocation')
    elif damage == 'extra_field': lane['callability'] = 'proven'
    result = _focus_call_result_alias_invocations(record, row, 3, 0 if damage == 'budget' else 64)
    if damage == 'none': assert result['status'] == 'observed' and result['returned'] == 1
    else: assert result['status'] in {'unavailable', 'incomplete_scan'} and result['items'] == []
    for field in ('value_transfer', 'runtime_execution', 'callability', 'callback_activation', 'schema_validity'):
        assert result[field] == 'not_established'


@pytest.mark.parametrize('damage', ['none', 'authority', 'extra', 'role', 'bool_span', 'kind'])
def test_schema_guard(invocation_inputs, tmp_path, damage):
    from tools.core.artifact_validator import validate_against_schema
    schema = json.loads((ROOT / 'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    path = tmp_path / 'schema.json'
    path.write_text(json.dumps({'$defs': schema['$defs'], '$ref': '#/$defs/' + KEY}), encoding='utf-8')
    lane = deepcopy(invocation_inputs[2]['Case_plain'][KEY])
    if damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'extra': lane['callability'] = 'proven'
    elif damage == 'role': lane['records'][0]['spans'].pop('invocation')
    elif damage == 'bool_span': lane['records'][0]['spans']['invoked_identifier']['start'] = True
    elif damage == 'kind': lane['records'][0]['kind'] = 'namespace'
    assert (not validate_against_schema(path, 'invocation', lane)) == (damage == 'none')


@pytest.mark.parametrize('case', ['row_cap', 'scan_cap', 'parse', 'ambiguous'])
def test_native_failure_and_caps(tmp_path, case):
    body = PLAIN + (' refetch();' * 65 if case == 'row_cap' else ' refetch();')
    if case == 'scan_cap': body = 'const padding = [' + ','.join('1' for _ in range(10001)) + ']; ' + body
    elif case == 'parse': body += ' const broken = ;'
    source = "import { useUser } from './users'; "
    if case == 'ambiguous': source += "import { useUser } from './another'; "
    source += 'export function owner() { ' + body + ' }'
    path = tmp_path / 'Caps.ts'
    path.write_text(source, encoding='utf-8')
    lane = next(row for row in _native(path) if row['name'] == 'owner')[KEY]
    if case == 'row_cap': assert lane['status'] == 'incomplete_scan' and len(lane['records']) == 64 and lane['omitted'] == 1
    else: assert lane['records'] == [] and (case == 'ambiguous' or lane['status'] == 'unavailable')


@pytest.fixture(scope='module')
def invocation_atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp('invocation-atlas')
    source = """import { useCallback } from 'react';
import { useUser } from './users';
export function owner() {
  const { refetch: refetchUser, save } = useUser();
  refetchUser();
  save(1);
  const cb = useCallback(() => {}, []);
  return { cb };
}
throw new Error('Target source must never execute');
"""
    (target / 'Panel.ts').write_bytes(('\ufeff// \U0001f680\r\n' + source.replace('\n', '\r\n')).encode('utf-8'))
    env = dict(os.environ)
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT', 'CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256',
                'CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE', 'CODEMAPS_TARGET_PROJECTS'): env.pop(key, None)
    env['CODEMAPS_TARGET_ROOT'] = str(target)
    run = subprocess.run([sys.executable, '-B', '-m', 'tools.engines.generate_atlas'], cwd=ROOT,
                         env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    raw = ROOT / 'output/external_targets' / external_target_output_slug(str(target)) / '.raw'
    return target, raw, load_atlas_data(raw)


def test_real_sqlite_mcp_cold_warm_and_item_cap(invocation_atlas_run, record_property):
    from tools.mcp import server
    target, _, atlas = invocation_atlas_run
    owner = next(row for row in atlas['MAIN']['files']['Panel.ts']['symbols'] if row['name'] == 'owner')
    assert len(owner[KEY]['records']) == 2 and len(owner[BINDING]['records']) == 2
    server._ATLAS_CACHE.clear()
    cold = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    warm = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    assert cold == warm
    record_property('observed_focused_state_flow', json.dumps(cold, ensure_ascii=False))
    lane = cold['symbol_context'][FOCUS]
    assert lane['status'] == 'observed' and lane['returned'] == lane['omitted'] == 1
    assert lane['source_binding'] == 'snapshot_and_live_match' and lane['items'][0]['local_name'] == 'refetchUser'
    assert 'target' not in lane['items'][0]
    assert cold['symbol_context']['call_result_property_bindings']['returned'] == 1
    assert FOCUS in server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), max_items=1)


def test_packet_omission_preserves_prior_lanes(invocation_atlas_run, monkeypatch):
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    target, _, _ = invocation_atlas_run
    baseline = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json'))
    # Settlement order defines the predecessor packet. Future additive lanes
    # cannot grant this lane extra room merely by adding empty metadata.
    keys = list(baseline['symbol_context'])
    for key in keys[keys.index(FOCUS):]:
        baseline['symbol_context'].pop(key)
    budget = context_budget_profile(json.dumps(baseline, ensure_ascii=False))['estimated_tokens']
    monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', budget)
    limited = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json'))
    assert FOCUS not in limited['symbol_context']
    assert limited['symbol_context'] == baseline['symbol_context']
    assert context_budget_profile(json.dumps(limited, ensure_ascii=False), budget_tokens=budget)['status'] == 'pass'
    assert 'alias' in limited['next_action'] and 'absence' in limited['next_action']


@pytest.mark.parametrize('damage', ['none', 'legacy', 'source_drift', 'corrupt_primary', 'poison_cache', 'scan_budget'])
def test_real_primary_source_refusal(invocation_atlas_run, tmp_path, monkeypatch, damage):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    target, _, original = invocation_atlas_run
    local = tmp_path / 'target'
    local.mkdir()
    (local / 'Panel.ts').write_bytes((target / 'Panel.ts').read_bytes())
    atlas = deepcopy(original)
    atlas['MAIN']['root_path'] = str(local)
    if damage == 'legacy':
        for row in atlas['MAIN']['files']['Panel.ts']['symbols']: row.pop(KEY, None)
    raw = tmp_path / '.raw'
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table('atlas', atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / 'codemaps.db') as connection:
        connection.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == 'corrupt_primary': connection.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    (raw / 'atlas.json').write_text(json.dumps(original), encoding='utf-8')
    if damage == 'source_drift': (local / 'Panel.ts').write_bytes((local / 'Panel.ts').read_bytes() + b'\n// drift')
    if damage == 'scan_budget':
        policy = dict(server._state_flow_brief_policy())
        policy['focus_scan_limit'] = 8
        monkeypatch.setattr(server, '_state_flow_brief_policy', lambda: policy)
    server._ATLAS_CACHE.clear()
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda root: raw)
    monkeypatch.setattr(server, '_analysis_root_display', lambda root: str(local))
    if damage == 'poison_cache':
        cached = server._atlas_read(raw).data
        next(row for row in cached['MAIN']['files']['Panel.ts']['symbols'] if row['name'] == 'owner')[KEY]['records'][0]['local_name'] = 'poison'
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(local), format='json'))
    lane = result.get('symbol_context', {}).get(FOCUS)
    if damage in {'none', 'poison_cache'}:
        assert lane['status'] == 'observed' and lane['returned'] == 2 and lane['items'][0]['local_name'] == 'refetchUser'
    elif damage == 'legacy': assert lane is None
    else: assert lane is None or (lane['status'] in {'unavailable', 'incomplete_scan'} and lane['returned'] == 0)
    if damage == 'scan_budget': assert result['visited_records'] <= 8

"""Nested alias source context is not callback registration or execution proof."""
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
from tools.tests.test_state_flow_returned_local_callback_contract import _packet_at_context_budget_check

ROOT = Path(__file__).resolve().parents[2]
KEY = 'call_result_nested_callback_alias_invocation_evidence'
FOCUS = 'call_result_nested_callback_alias_invocations'
BINDING = 'call_result_property_binding_evidence'
IMMEDIATE = 'call_result_alias_invocation_evidence'
PLAIN = 'const { refetch: run } = useUser();'
CALL = 'consume({ done: () => { run(); } });'
CASES = {
    'plain': PLAIN + CALL,
    'renamed': 'const { save: anything } = getUser(); register({ complete: () => { anything(1); } });',
    'unicode': 'const { refetch: çağrı } = useUser(); consume({ tamam: () => { çağrı(); } });',
    'rest_callback': PLAIN + 'return useMutation({ onSuccess: (...args) => { run(); onSuccess?.(...args); } });',
    'spread_object': PLAIN + 'consume({ done: () => { run(); }, ...restConfig });',
    'duplicate_key': PLAIN + 'consume({ done: () => { run(); }, done: other });',
    'quoted_duplicate': PLAIN + 'consume({ done: () => { run(); }, "done": other });',
    'computed_context': PLAIN + 'consume({ done: () => { run(); }, [key]: other });',
    'repeated': PLAIN + 'consume({ done: () => { run(); run(1); } });',
    'multiple': 'const { refetch: run, save } = useUser(); consume({ done: () => { run(); save(); } });',
    'none': PLAIN,
    'before': CALL + PLAIN,
    'parameter_shadow': PLAIN + 'consume({ done: (run: any) => { run(); } });',
    'local_shadow': PLAIN + 'consume({ done: () => { const run = other; run(); } });',
    'nested_function': PLAIN + 'consume({ done: () => { function child() { run(); } } });',
    'nested_config': PLAIN + 'consume({ options: { done: () => { run(); } } });',
    'branch_owner': PLAIN + 'if (ready) { ' + CALL + ' }',
    'branch_callback': PLAIN + 'consume({ done: () => { if (ready) run(); } });',
    'async_callback': PLAIN + 'consume({ done: async () => { run(); } });',
    'concise': PLAIN + 'consume({ done: () => run() });',
    'method': PLAIN + 'consume({ done() { run(); } });',
    'function': PLAIN + 'consume({ done: function() { run(); } });',
    'quoted_callback': PLAIN + 'consume({ "done": () => { run(); } });',
    'computed_callback': PLAIN + 'consume({ [key]: () => { run(); } });',
    'optional_container': PLAIN + CALL.replace('consume(', 'consume?.('),
    'generic_container': PLAIN + CALL.replace('consume(', 'consume<any>('),
    'member_container': PLAIN + CALL.replace('consume(', 'api.consume('),
    'two_arguments': PLAIN + CALL.replace('consume({', 'consume(1, {'),
    'wrapped_object': PLAIN + 'consume(({ done: () => { run(); } }));',
    'assigned_container': PLAIN + 'const value = ' + CALL,
    'nonterminal_return': PLAIN + 'return ' + CALL + 'extra();',
    'optional_invocation': PLAIN + CALL.replace('run();', 'run?.();'),
    'generic_invocation': PLAIN + CALL.replace('run();', 'run<any>();'),
    'spread_invocation': PLAIN + CALL.replace('run();', 'run(...args);'),
    'wrapped_invocation': PLAIN + CALL.replace('run();', '(run)();'),
    'returned_invocation': PLAIN + CALL.replace('run();', 'return run();'),
    'write_callback': PLAIN + CALL.replace('run();', 'run = other; run();'),
    'write_later': PLAIN + CALL + 'run = other;',
    'write_sibling': PLAIN + CALL + 'later(() => { run = other; });',
    'producer_write': PLAIN + CALL + 'useUser = other;',
    'duplicate_binding': PLAIN + 'const run = other;' + CALL,
    'let': PLAIN.replace('const', 'let') + CALL,
    'rest_binding': 'const { refetch: run, ...rest } = useUser();' + CALL,
    'default_binding': 'const { refetch: run = other } = useUser();' + CALL,
    'type_only': PLAIN.replace('useUser()', 'typeUser()') + CALL,
    'escaped_invocation': PLAIN + CALL.replace('run();', r'r\u0075n();'),
}
POSITIVES = {'plain', 'renamed', 'unicode', 'rest_callback', 'spread_object', 'duplicate_key',
             'quoted_duplicate', 'computed_context', 'repeated', 'multiple', 'arrow'}


@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    source = '\ufeff// \U0001f680 CRLF\r\n' + '\r\n'.join([
        "import { useUser, useUser as getUser } from './users';",
        "import type { useUser as typeUser } from './users';",
        "import { useMutation } from '@tanstack/react-query';",
        *[f'export function Case_{name}() {{ {body} }}' for name, body in CASES.items()],
        f'export const Case_arrow = () => {{ {CASES["plain"]} }};',
        f'export async function Case_async_owner() {{ {CASES["plain"]} }}',
        f'export function* Case_generator_owner() {{ {CASES["plain"]} }}',
        'export function prior() { const { refetch: run } = useUser(); run(); }',
        "throw new Error('Target source must never execute');",
    ])
    path = tmp_path_factory.mktemp('nested-source') / 'Cases.tsx'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    rows = {row['name']: row for row in _normalize_polyglot_symbols(raw, source, 'typescript')}
    return source, raw, rows


@pytest.mark.parametrize('name', sorted(POSITIVES))
def test_native_positive(inputs, name):
    source, _, rows = inputs
    lane = rows['Case_' + name][KEY]
    assert lane['status'] == 'observed' and lane['omitted'] == 0
    assert len(lane['records']) == (2 if name in {'repeated', 'multiple'} else 1)
    assert lane['source_hash'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    assert lane['ast_contract_version'] == AST_CONTRACT_VERSION and lane['runtime_execution'] == 'not_established'
    for row in lane['records']:
        assert len(row['spans']) == 25 and 'target' not in row and 'calls' not in row
        for role, text in (('invoked_identifier', row['local_name']), ('local_binding', row['local_name']),
                           ('callback_property_name', row['callback_property_name']),
                           ('container_callee', row['container_callee_spelling'])):
            span = row['spans'][role]
            assert source.encode('utf-8')[span['start']:span['end']].decode('utf-8') == text
        assert row['object_spread_present'] == (name == 'spread_object')
        assert row['duplicate_callback_property_spelling'] == (name in {'duplicate_key', 'quoted_duplicate'})
        assert row['spans']['declaration']['end'] < row['spans']['container_statement']['start']


@pytest.mark.parametrize('name', sorted(set(CASES) - POSITIVES) + ['async_owner', 'generator_owner'])
def test_native_negative(inputs, name):
    assert inputs[2]['Case_' + name][KEY]['records'] == []


@pytest.mark.parametrize('damage', ['hash', 'version', 'authority', 'offset', 'bool_span', 'lines',
                                  'missing_role', 'extra_role', 'order', 'duplicate', 'status', 'omitted',
                                  'parser', 'imports', 'token', 'before_binding', 'extra_field', 'flag_type',
                                  'callback_token', 'callee_token', 'callback_containment'])
def test_normalizer_damage(inputs, damage):
    source, original, _ = inputs
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
    elif damage == 'missing_role': row['spans'].pop('callback')
    elif damage == 'extra_role': row['spans']['other'] = row['spans']['callee']
    elif damage == 'order': row['spans']['container_statement'] = row['spans']['local_binding']
    elif damage == 'duplicate': lane['records'].append(deepcopy(row))
    elif damage == 'status': lane['status'] = []
    elif damage == 'omitted': lane['omitted'] = -1
    elif damage == 'parser': owner['parserKind'] = 'fallback'
    elif damage == 'imports': next(row for row in raw if row['name'] == '__file_meta__')['directImportBindings'] = []
    elif damage == 'token': row['local_name'] = 'other'
    elif damage == 'before_binding': row['spans']['declaration'] = row['spans']['container_statement']
    elif damage == 'extra_field': row['callback_registration'] = 'proven'
    elif damage == 'flag_type': row['object_spread_present'] = 1
    elif damage == 'callback_token': row['callback_property_name'] = 'other'
    elif damage == 'callee_token': row['container_callee_spelling'] = 'other'
    elif damage == 'callback_containment': row['spans']['callback_body'] = row['spans']['body']
    result = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == 'Case_plain')[KEY]
    assert result['status'] == 'unavailable' and result['records'] == []


def test_one_offset_pass_and_prior_lanes(inputs, monkeypatch):
    from tools.engines import generate_atlas
    source, raw, _ = inputs
    original, calls = generate_atlas._utf16_offsets_to_utf8_byte_offsets, []
    def track(content, offsets):
        calls.append(1)
        return original(content, offsets)
    monkeypatch.setattr(generate_atlas, '_utf16_offsets_to_utf8_byte_offsets', track)
    rows = {row['name']: row for row in generate_atlas._normalize_polyglot_symbols(raw, source, 'typescript')}
    assert len(calls) == 1 and rows['Case_unicode'][KEY]['records']
    assert len(rows['Case_plain'][BINDING]['records'][0]['spans']) == 14
    assert len(rows['prior'][IMMEDIATE]['records'][0]['spans']) == 17
    assert rows['Case_plain'][IMMEDIATE]['records'] == []
    assert all(call['localName'] != 'run' for call in rows['Case_plain']['import_call_evidence']['calls'])


@pytest.mark.parametrize('damage', ['none', 'missing', 'hash', 'ast', 'parser', 'authority', 'bool_span',
                                  'outer', 'duplicate', 'omitted', 'incomplete', 'budget', 'roles', 'extra_field', 'flag_type'])
def test_focus_integrity(inputs, damage):
    from tools.core.state_flow import _focus_call_result_alias_invocations
    source, _, rows = inputs
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
    elif damage == 'roles': lane['records'][0]['spans'].pop('callback')
    elif damage == 'extra_field': lane['callability'] = 'proven'
    elif damage == 'flag_type': lane['records'][0]['duplicate_callback_property_spelling'] = 0
    result = _focus_call_result_alias_invocations(record, row, 3, 0 if damage == 'budget' else 64, _nested=True)
    if damage == 'none': assert result['status'] == 'observed' and result['returned'] == 1
    else: assert result['status'] in {'unavailable', 'incomplete_scan'} and result['items'] == []
    for field in ('value_transfer', 'runtime_execution', 'callability', 'callback_activation', 'schema_validity',
                  'callback_registration', 'effective_object_property', 'container_callee_identity'):
        assert result[field] == 'not_established'


@pytest.mark.parametrize('damage', ['none', 'authority', 'extra', 'role', 'bool_span', 'kind', 'flag_type'])
def test_schema_guard(inputs, tmp_path, damage):
    from tools.core.artifact_validator import validate_against_schema
    schema = json.loads((ROOT / 'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    path = tmp_path / 'schema.json'
    path.write_text(json.dumps({'$defs': schema['$defs'], '$ref': '#/$defs/' + KEY}), encoding='utf-8')
    lane = deepcopy(inputs[2]['Case_plain'][KEY])
    if damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'extra': lane['callability'] = 'proven'
    elif damage == 'role': lane['records'][0]['spans'].pop('callback')
    elif damage == 'bool_span': lane['records'][0]['spans']['invoked_identifier']['start'] = True
    elif damage == 'kind': lane['records'][0]['kind'] = 'namespace'
    elif damage == 'flag_type': lane['records'][0]['object_spread_present'] = 1
    assert (not validate_against_schema(path, 'nested', lane)) == (damage == 'none')


@pytest.mark.parametrize('case', ['row_cap', 'scan_cap', 'parse', 'ambiguous'])
def test_native_failure_and_caps(tmp_path, case):
    body = PLAIN + 'consume({ done: () => { ' + 'run();' * 65 + ' } });'
    if case == 'scan_cap': body = 'const pad = [' + ','.join('1' for _ in range(10001)) + '];' + body
    elif case == 'parse': body += 'const broken = ;'
    source = "import { useUser } from './users'; "
    if case == 'ambiguous': source += "import { useUser } from './another'; "
    path = tmp_path / 'Caps.ts'
    path.write_text(source + 'export function owner() { ' + body + ' }', encoding='utf-8')
    lane = next(row for row in _native(path) if row['name'] == 'owner')[KEY]
    if case == 'row_cap': assert lane['status'] == 'incomplete_scan' and len(lane['records']) == 64 and lane['omitted'] == 1
    else: assert lane['records'] == [] and (case == 'ambiguous' or lane['status'] == 'unavailable')


@pytest.fixture(scope='module')
def atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp('nested-atlas')
    source = """import { useUser } from './users';
import { useCallback } from 'react';
export function owner() {
  const { refetch: refetchUser, save } = useUser();
  refetchUser();
  const cb = useCallback(() => {}, []);
  consume({ complete: () => { refetchUser(); save(1); }, ...restConfig });
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


def test_real_sqlite_mcp_cold_warm_and_item_cap(atlas_run, record_property):
    from tools.mcp import server
    target, _, atlas = atlas_run
    owner = next(row for row in atlas['MAIN']['files']['Panel.ts']['symbols'] if row['name'] == 'owner')
    assert len(owner[KEY]['records']) == 2 and len(owner[BINDING]['records']) == 2
    assert len(owner[IMMEDIATE]['records']) == 1
    server._ATLAS_CACHE.clear()
    cold = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    warm = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    assert cold == warm
    record_property('observed_focused_state_flow', json.dumps(cold, ensure_ascii=False))
    lane = cold['symbol_context'][FOCUS]
    assert lane['status'] == 'observed' and lane['returned'] == lane['omitted'] == 1
    assert lane['source_binding'] == 'snapshot_and_live_match' and lane['items'][0]['local_name'] == 'refetchUser'
    assert lane['items'][0]['object_spread_present'] and 'target' not in lane['items'][0]
    assert cold['symbol_context']['call_result_alias_invocations']['returned'] == 1
    assert FOCUS in server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), max_items=1)


def test_packet_omission_preserves_prior_lanes(atlas_run, monkeypatch):
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    target, _, _ = atlas_run
    baseline = _packet_at_context_budget_check(
        lambda: server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json'),
        FOCUS, monkeypatch)
    budget = context_budget_profile(json.dumps(baseline, ensure_ascii=False))['estimated_tokens']
    monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', budget)
    limited = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json'))
    assert FOCUS not in limited['symbol_context']
    assert limited['symbol_context'] == baseline['symbol_context']
    assert context_budget_profile(json.dumps(limited, ensure_ascii=False), budget_tokens=budget)['status'] == 'pass'
    action = limited['next_action'].lower()
    assert 'nested alias' in action or 'nested' in action.replace('/', ' ').replace(';', ' ').split()
    assert 'absence' in action


@pytest.mark.parametrize('damage', ['none', 'legacy', 'source_drift', 'corrupt_primary', 'poison_cache', 'scan_budget'])
def test_real_primary_source_refusal(atlas_run, tmp_path, monkeypatch, damage):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    target, _, original = atlas_run
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

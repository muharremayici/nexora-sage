"""Parameter callback source association, never a runtime or absence verdict."""
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

KEY = 'parameter_config_callback_forwarding_evidence'
FOCUS = 'parameter_config_callback_forwardings'
ROOT = Path(__file__).resolve().parents[2]
BIND = 'const { done: run, ...rest } = config || {};'
CALL = 'consume({ complete: (...args) => { run?.(...args); }, ...rest });'
CASES = {
    'plain': BIND + CALL,
    'direct': BIND.replace('config || {}', 'config') + CALL,
    'nullish': BIND.replace('||', '??') + CALL,
    'no_rest': BIND.replace(', ...rest', '') + CALL,
    'shorthand': BIND.replace('done: run', 'run') + CALL,
    'unicode': (BIND + CALL).replace('run', 'çağrı').replace('args', 'girdi'),
    'repeated': BIND + CALL.replace('run?.(...args);', 'run?.(...args); run?.(...args);'),
    'duplicate_context': BIND + CALL.replace(', ...rest', ', complete: other'),
    'let': BIND.replace('const', 'let') + CALL,
    'default_binding': BIND.replace('done: run', 'done: run = other') + CALL,
    'quoted': BIND.replace('done:', '"done":') + CALL,
    'computed': BIND.replace('done:', '[key]:') + CALL,
    'nested_pattern': BIND.replace('done: run', 'child: { done: run }') + CALL,
    'duplicate_key': BIND.replace('done: run', 'done: run, done: other') + CALL,
    'duplicate_local': BIND + 'const run = other;' + CALL,
    'parameter_collision': BIND + 'const config = other;' + CALL,
    'binding_write': BIND + CALL + 'run = other;',
    'parameter_write': BIND + CALL + 'config = other;',
    'args_write': BIND + CALL.replace('run?.(...args);', 'args = other; run?.(...args);'),
    'args_shadow': BIND + CALL.replace('run?.(...args);', 'const args = other; run?.(...args);'),
    'callback_shadow': BIND + CALL.replace('run?.(...args);', 'const run = other; run?.(...args);'),
    'nonempty_fallback': BIND.replace('|| {}', '|| {done: other}') + CALL,
    'indirect': 'const local = config;' + BIND.replace('config || {}', 'local || {}') + CALL,
    'before': CALL + BIND,
    'plain_call': BIND + CALL.replace('run?.(', 'run('),
    'generic': BIND + CALL.replace('run?.(', 'run?.<any>('),
    'other_args': BIND + CALL.replace('...args);', '...outer);'),
    'mixed_args': BIND + CALL.replace('...args);', '1, ...args);'),
    'array_args': BIND + CALL.replace('run?.(...args);', 'run?.(args);'),
    'optional_container': BIND + CALL.replace('consume(', 'consume?.('),
    'property_container': BIND + CALL.replace('consume(', 'api.consume('),
    'async_callback': BIND + CALL.replace('complete: (', 'complete: async ('),
    'function_callback': BIND + CALL.replace('complete: (...args) =>', 'complete: function(...args)'),
    'concise': BIND + 'consume({ complete: (...args) => run?.(...args) });',
    'branch': BIND + CALL.replace('run?.(...args);', 'if (ready) run?.(...args);'),
    'deeper': BIND + CALL.replace('run?.(...args);', 'later(() => run?.(...args));'),
    'restless_callback': BIND + CALL.replace('(...args) =>', '(args) =>'),
    'extra_param': BIND + CALL.replace('(...args) =>', '(first, ...args) =>'),
    'escaped': BIND + CALL.replace('run?.', r'r\u0075n?.'),
}
POSITIVE = {'plain', 'direct', 'nullish', 'no_rest', 'shorthand', 'unicode', 'repeated',
            'duplicate_context', 'arrow', 'parameter_pattern', 'parameter_rename', 'parameter_default'}


@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    source = '\ufeff// \U0001f680 CRLF\r\n' + '\r\n'.join([
        *[f'export function Case_{name}(config: any) {{ {body} }}' for name, body in CASES.items()],
        f'export const Case_arrow = (config: any) => {{ {CASES["plain"]} }};',
        f'export function Case_parameter_pattern({{config}}: any = {{}}) {{ {CASES["plain"]} }}',
        f'export function Case_parameter_rename({{options: config}}: any) {{ {CASES["plain"]} }}',
        f'export function Case_parameter_default(config: any = {{}}) {{ {CASES["plain"]} }}',
        f'export function Case_parameter_bad_default(config: any = other) {{ {CASES["plain"]} }}',
        f'export function Case_parameter_nested({{child: {{config}}}}: any) {{ {CASES["plain"]} }}',
        f'export async function Case_async_owner(config: any) {{ {CASES["plain"]} }}',
        f'export function* Case_generator_owner(config: any) {{ {CASES["plain"]} }}',
        "throw new Error('TARGET_SOURCE_MUST_NEVER_EXECUTE');",
    ])
    path = tmp_path_factory.mktemp('parameter-source') / 'Cases.tsx'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    rows = {row['name']: row for row in _normalize_polyglot_symbols(raw, source, 'typescript')}
    return source, raw, rows


@pytest.mark.parametrize('name', sorted(POSITIVE))
def test_native_positive(inputs, name):
    source, _, rows = inputs
    lane = rows['Case_' + name][KEY]
    assert lane['status'] == 'observed' and lane['omitted'] == 0
    assert len(lane['records']) == (2 if name == 'repeated' else 1)
    assert lane['source_hash'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    assert lane['ast_contract_version'] == AST_CONTRACT_VERSION
    assert lane['runtime_execution'] == 'not_established'
    for row in lane['records']:
        assert len(row['spans']) == 27 and row['optional_call'] is True
        assert not {'source', 'importedName', 'target', 'calls'} & row.keys()
        assert row['fallback_operator'] == ('none' if name == 'direct' else '??' if name == 'nullish' else '||')
        assert row['object_rest_present'] == (name != 'no_rest')
        assert row['parameter_default_empty_object'] == (name in {'parameter_pattern', 'parameter_default'})
        for role, field in [('local_binding', 'local_name'), ('invoked_identifier', 'local_name'),
                            ('parameter_input', 'parameter_name'), ('parameter_name', 'parameter_name'),
                            ('rest_binding', 'rest_name'), ('spread_identifier', 'rest_name')]:
            span = row['spans'][role]
            assert source.encode('utf-8')[span['start']:span['end']].decode('utf-8') == row[field]


@pytest.mark.parametrize('name', sorted(set(CASES) - POSITIVE)
                         + ['parameter_bad_default', 'parameter_nested', 'async_owner', 'generator_owner'])
def test_native_negative(inputs, name):
    assert inputs[2]['Case_' + name][KEY]['records'] == []


@pytest.mark.parametrize('damage', ['hash', 'version', 'authority', 'offset', 'bool_span', 'lines',
    'missing_role', 'extra_role', 'order', 'duplicate', 'status', 'omitted', 'parser', 'token',
    'extra_field', 'flag_type', 'binding_kind', 'fallback', 'optional_false', 'rest_token'])
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
    elif damage == 'bool_span': row['spans']['invocation']['start'] = True
    elif damage == 'lines': row['spans']['invocation']['line'] += 1
    elif damage == 'missing_role': row['spans'].pop('parameter')
    elif damage == 'extra_role': row['spans']['other'] = row['spans']['parameter']
    elif damage == 'order': row['spans']['container_statement'] = row['spans']['declaration']
    elif damage == 'duplicate': lane['records'].append(deepcopy(row))
    elif damage == 'status': lane['status'] = []
    elif damage == 'omitted': lane['omitted'] = -1
    elif damage == 'parser': owner['parserKind'] = 'fallback'
    elif damage == 'token': row['parameter_name'] = 'other'
    elif damage == 'extra_field': row['callability'] = 'proven'
    elif damage == 'flag_type': row['object_rest_present'] = 1
    elif damage == 'binding_kind': row['parameter_binding_kind'] = 'imported'
    elif damage == 'fallback': row['fallback_operator'] = 'none'
    elif damage == 'optional_false': row['optional_call'] = False
    elif damage == 'rest_token': row['rest_name'] = 'outer'
    result = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == 'Case_plain')[KEY]
    assert result['status'] == 'unavailable' and result['records'] == []


def test_one_batched_offset_conversion(inputs, monkeypatch):
    from tools.engines import generate_atlas
    source, raw, _ = inputs
    original, calls = generate_atlas._utf16_offsets_to_utf8_byte_offsets, []
    def track(content, offsets):
        calls.append(1)
        return original(content, offsets)
    monkeypatch.setattr(generate_atlas, '_utf16_offsets_to_utf8_byte_offsets', track)
    rows = {row['name']: row for row in generate_atlas._normalize_polyglot_symbols(raw, source, 'typescript')}
    assert len(calls) == 1 and rows['Case_unicode'][KEY]['records']


@pytest.mark.parametrize('damage', ['none', 'missing', 'hash', 'ast', 'parser', 'authority', 'bool_span',
    'outer', 'duplicate', 'omitted', 'incomplete', 'budget', 'roles', 'extra_field', 'flag_type',
    'optional', 'binding_kind', 'fallback', 'limit'])
def test_focus_integrity(inputs, damage):
    from tools.core.state_flow import _focus_parameter_config_callbacks
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
    elif damage == 'roles': lane['records'][0]['spans'].pop('rest_parameter')
    elif damage == 'extra_field': lane['callability'] = 'proven'
    elif damage == 'flag_type': lane['records'][0]['object_rest_present'] = 0
    elif damage == 'optional': lane['records'][0]['optional_call'] = False
    elif damage == 'binding_kind': lane['records'][0]['parameter_binding_kind'] = 'imported'
    elif damage == 'fallback': lane['records'][0]['fallback_operator'] = 'none'
    result = _focus_parameter_config_callbacks(record, row, 0 if damage == 'limit' else 3,
                                               0 if damage == 'budget' else 64)
    if damage == 'none': assert result['status'] == 'observed' and result['returned'] == 1
    elif damage == 'limit': assert result['status'] == 'observed' and result['returned'] == 0 and result['omitted'] == 1
    else: assert result['status'] in {'unavailable', 'incomplete_scan'} and result['items'] == []
    for field in ('value_transfer', 'runtime_execution', 'callability', 'callback_activation', 'schema_validity',
                  'callback_registration', 'effective_object_property', 'container_callee_identity'):
        assert result[field] == 'not_established'


@pytest.mark.parametrize('damage', ['none', 'authority', 'extra', 'role', 'bool_span', 'binding_kind',
                                   'flag_type', 'optional', 'fallback'])
def test_schema_guard(inputs, tmp_path, damage):
    from tools.core.artifact_validator import validate_against_schema
    schema = json.loads((ROOT / 'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    path = tmp_path / 'schema.json'
    path.write_text(json.dumps({'$defs': schema['$defs'], '$ref': '#/$defs/' + KEY}), encoding='utf-8')
    lane = deepcopy(inputs[2]['Case_plain'][KEY])
    if damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'extra': lane['callability'] = 'proven'
    elif damage == 'role': lane['records'][0]['spans'].pop('rest_parameter')
    elif damage == 'bool_span': lane['records'][0]['spans']['invoked_identifier']['start'] = True
    elif damage == 'binding_kind': lane['records'][0]['parameter_binding_kind'] = 'imported'
    elif damage == 'flag_type': lane['records'][0]['object_rest_present'] = 1
    elif damage == 'optional': lane['records'][0]['optional_call'] = False
    elif damage == 'fallback': lane['records'][0]['fallback_operator'] = '&&'
    assert (not validate_against_schema(path, 'parameter_callback', lane)) == (damage == 'none')


@pytest.mark.parametrize('case', ['row_cap', 'scan_cap', 'parse'])
def test_native_failure_and_caps(tmp_path, case):
    body = BIND + 'consume({ done: (...args) => { ' + 'run?.(...args);' * 65 + ' } });'
    if case == 'scan_cap': body = 'const pad = [' + ','.join('1' for _ in range(10001)) + '];' + body
    elif case == 'parse': body += 'const broken = ;'
    path = tmp_path / 'Caps.ts'
    path.write_text('export function owner(config: any) { ' + body + ' }', encoding='utf-8')
    lane = next(row for row in _native(path) if row['name'] == 'owner')[KEY]
    if case == 'row_cap': assert lane['status'] == 'incomplete_scan' and len(lane['records']) == 64 and lane['omitted'] == 1
    else: assert lane['records'] == [] and lane['status'] == 'unavailable'


@pytest.fixture(scope='module')
def atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp('parameter-atlas')
    source = """import { useUser } from './users';
import { useCallback } from 'react';
export function owner(config: any) {
  const { done: run, ...rest } = config || {};
  const { refetch: refetchUser } = useUser();
  refetchUser();
  const cb = useCallback(() => {}, []);
  consume({ complete: (...args) => { run?.(...args); run?.(...args); refetchUser(); }, ...rest });
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
    assert len(owner[KEY]['records']) == 2
    assert len(owner['call_result_nested_callback_alias_invocation_evidence']['records']) == 1
    server._ATLAS_CACHE.clear()
    cold = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    warm = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    assert cold == warm
    record_property('observed_focused_state_flow', json.dumps(cold, ensure_ascii=False))
    lane = cold['symbol_context'][FOCUS]
    assert lane['status'] == 'observed' and lane['returned'] == lane['omitted'] == 1
    assert lane['source_binding'] == 'snapshot_and_live_match' and lane['items'][0]['local_name'] == 'run'
    assert lane['items'][0]['optional_call'] and lane['items'][0]['object_rest_present']
    assert not {'target', 'importedName', 'source'} & lane['items'][0].keys()
    assert cold['symbol_context']['call_result_alias_invocations']['returned'] == 1
    assert cold['symbol_context']['call_result_nested_callback_alias_invocations']['returned'] == 1
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
    assert 'parameter callback' in action or 'param' in action.replace('/', ' ').replace(';', ' ').split()
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
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(local), format='json', max_items=1))
    lane = result.get('symbol_context', {}).get(FOCUS)
    if damage in {'none', 'poison_cache'}:
        assert lane['status'] == 'observed' and lane['returned'] == lane['omitted'] == 1 and lane['items'][0]['local_name'] == 'run'
        assert lane['source_binding'] == 'snapshot_and_live_match'
    elif damage == 'legacy': assert lane is None
    else: assert lane is None or (lane['status'] in {'unavailable', 'incomplete_scan'} and lane['returned'] == 0)
    if damage == 'scan_budget': assert result['visited_records'] <= 8

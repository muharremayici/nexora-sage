"""Direct optional source association, never callability or execution proof."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tools.engines.generate_atlas import AST_CONTRACT_VERSION, _normalize_polyglot_symbols
from tools.tests.test_state_flow_call_result_property_binding_contract import _native
from tools.tests.test_state_flow_returned_local_callback_contract import _packet_at_context_budget_check

KEY = 'call_result_optional_alias_invocation_evidence'
FOCUS = 'call_result_optional_alias_invocations'
ROOT = Path(__file__).resolve().parents[2]
BIND = 'const { task: run } = useJobs();'
CALL = 'run?.();'
CASES = {
    'plain': BIND + CALL,
    'shorthand': BIND.replace('task: run', 'run') + CALL,
    'unicode': (BIND + CALL).replace('run', 'çağrı'),
    'repeated': BIND + CALL * 2,
    'arguments': BIND + 'run?.(value, other());',
    'trivia': BIND + 'run /* marker */ ?. /* arguments */ ();',
    'mixed': BIND + 'run(); run?.();',
    'let': BIND.replace('const', 'let') + CALL,
    'pattern_rest': BIND.replace('task: run', 'task: run, ...rest') + CALL,
    'pattern_default': BIND.replace('task: run', 'task: run = other') + CALL,
    'quoted': BIND.replace('task:', '"task":') + CALL,
    'computed': BIND.replace('task:', '[key]:') + CALL,
    'nested_pattern': BIND.replace('task: run', 'inner: { task: run }') + CALL,
    'duplicate_key': BIND.replace('task: run', 'task: run, task: other') + CALL,
    'duplicate_local': BIND + 'const run = other;' + CALL,
    'binding_write': BIND + CALL + 'run = other;',
    'import_write': BIND + CALL + 'useJobs = other;',
    'parameter_origin': BIND.replace('useJobs()', 'config') + CALL,
    'optional_producer': BIND.replace('useJobs()', 'useJobs?.()') + CALL,
    'generic_producer': BIND.replace('useJobs()', 'useJobs<any>()') + CALL,
    'local_producer': 'const useJobs = other;' + BIND + CALL,
    'before': CALL + BIND,
    'branch': BIND + 'if (ready) { run?.(); }',
    'nested': BIND + 'consume({complete: () => { run?.(); }});',
    'returned': BIND + 'return run?.();',
    'wrapped': BIND + '(run)?.();',
    'member': BIND + 'run.method?.();',
    'generic': BIND + 'run?.<any>();',
    'spread': BIND + 'run?.(...args);',
    'escaped': BIND + r'r\u0075n?.();',
    'ordinary': BIND + 'run();',
    'older_nested': BIND + 'consume({complete: () => { run(); }});',
    'older_rest': BIND + 'consume({complete: (...args) => { run?.(...args); }});',
}
POSITIVE = {'plain', 'shorthand', 'unicode', 'repeated', 'arguments', 'trivia',
            'mixed', 'arrow', 'renamed_import'}


@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    source = '\ufeff// \U0001f680 CRLF\r\nimport { produce as useJobs, produce as jobs } from "./producer";\r\n'
    source += '\r\n'.join([
        *[f'export function Case_{name}(config: any) {{ {body} }}' for name, body in CASES.items()],
        f'export const Case_arrow = () => {{ {CASES["plain"]} }};',
        f'export function Case_renamed_import() {{ {BIND.replace("useJobs", "jobs")} {CALL} }}',
        f'export async function Case_async_owner() {{ {CASES["plain"]} }}',
        f'export function* Case_generator_owner() {{ {CASES["plain"]} }}',
        f'export function Case_parameter_collision(run: any) {{ {CASES["plain"]} }}',
        "throw new Error('TARGET_SOURCE_MUST_NEVER_EXECUTE');",
    ])
    path = tmp_path_factory.mktemp('direct-optional-source') / 'Cases.tsx'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    return source, raw, {row['name']: row for row in _normalize_polyglot_symbols(raw, source, 'typescript')}


@pytest.mark.parametrize('name', sorted(POSITIVE))
def test_native_positive(inputs, name):
    source, _, rows = inputs
    lane = rows['Case_' + name][KEY]
    assert lane['status'] == 'observed' and lane['omitted'] == 0
    assert lane['source_hash'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    assert lane['ast_contract_version'] == AST_CONTRACT_VERSION
    assert lane['runtime_execution'] == 'not_established'
    assert len(lane['records']) == (2 if name == 'repeated' else 1)
    for row in lane['records']:
        assert len(row['spans']) == 18 and row['optional_call'] is True
        assert row['source'] == './producer' and row['importedName'] == 'produce'
        assert not {'parameter_name', 'target', 'callability', 'calls'} & row.keys()
        for role, text in [('local_binding', row['local_name']), ('invoked_identifier', row['local_name']),
                           ('callee', row['localName']), ('imported_binding', row['importedName']),
                           ('optional_operator', '?.')]:
            span = row['spans'][role]
            assert source.encode('utf-8')[span['start']:span['end']].decode('utf-8') == text


@pytest.mark.parametrize('name', sorted(set(CASES) - POSITIVE) +
                         ['async_owner', 'generator_owner', 'parameter_collision'])
def test_native_negative(inputs, name):
    assert inputs[2]['Case_' + name][KEY]['records'] == []


@pytest.mark.parametrize('damage', ['hash', 'version', 'authority', 'offset', 'bool_span', 'lines',
    'missing_role', 'extra_role', 'order', 'duplicate', 'status', 'omitted', 'parser', 'token',
    'extra_field', 'optional_false', 'optional_type', 'marker_width', 'marker_token', 'import_identity'])
def test_normalizer_damage(inputs, damage):
    source, original, _ = inputs
    raw = deepcopy(original)
    owner = next(row for row in raw if row['name'] == 'Case_plain')
    lane, row = owner[KEY], owner[KEY]['records'][0]
    if damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'version': lane['version'] = 'old'
    elif damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'offset': lane['offset_unit'] = 'utf8_bytes'
    elif damage == 'bool_span': row['spans']['invocation']['start'] = True
    elif damage == 'lines': row['spans']['invocation']['line'] += 1
    elif damage == 'missing_role': row['spans'].pop('optional_operator')
    elif damage == 'extra_role': row['spans']['other'] = row['spans']['optional_operator']
    elif damage == 'order': row['spans']['optional_operator'] = row['spans']['invoked_identifier']
    elif damage == 'duplicate': lane['records'].append(deepcopy(row))
    elif damage == 'status': lane['status'] = []
    elif damage == 'omitted': lane['omitted'] = -1
    elif damage == 'parser': owner['parserKind'] = 'fallback'
    elif damage == 'token': row['local_name'] = 'other'
    elif damage == 'extra_field': row['callability'] = 'proven'
    elif damage == 'optional_false': row['optional_call'] = False
    elif damage == 'optional_type': row['optional_call'] = 1
    elif damage == 'marker_width': row['spans']['optional_operator']['end'] += 1
    elif damage == 'marker_token':
        row['spans']['optional_operator']['start'] += 2
        row['spans']['optional_operator']['end'] += 2
    elif damage == 'import_identity': row['source'] = './unbound'
    lane = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript')
                if row['name'] == 'Case_plain')[KEY]
    assert lane['status'] == 'unavailable' and lane['records'] == []


def test_one_batched_offset_conversion_and_preserved_lanes(inputs, monkeypatch):
    from tools.engines import generate_atlas
    source, raw, _ = inputs
    original, visits = generate_atlas._utf16_offsets_to_utf8_byte_offsets, []
    def track(content, offsets):
        visits.append(1)
        return original(content, offsets)
    monkeypatch.setattr(generate_atlas, '_utf16_offsets_to_utf8_byte_offsets', track)
    rows = {row['name']: row for row in generate_atlas._normalize_polyglot_symbols(raw, source, 'typescript')}
    assert len(visits) == 1 and rows['Case_unicode'][KEY]['records']
    for name, key, roles in [('mixed', 'call_result_alias_invocation_evidence', 17),
                            ('older_nested', 'call_result_nested_callback_alias_invocation_evidence', 25),
                            ('older_rest', 'call_result_optional_rest_alias_invocation_evidence', 29)]:
        lane = rows['Case_' + name][key]
        assert len(lane['records']) == 1 and len(lane['records'][0]['spans']) == roles
    legacy = deepcopy(raw)
    for row in legacy: row.pop(KEY, None)
    assert all(KEY not in row for row in generate_atlas._normalize_polyglot_symbols(legacy, source, 'typescript'))


@pytest.mark.parametrize('damage', ['type_only', 'namespace', 'parse_error', 'record_cap', 'binding_cap', 'scan_cap'])
def test_native_failure_and_capacity_controls(tmp_path, damage):
    body, declaration = BIND + CALL, 'import { produce as useJobs } from "./producer";'
    if damage == 'type_only': declaration = declaration.replace('import {', 'import type {')
    elif damage == 'namespace': declaration = 'import * as useJobs from "./producer";'
    elif damage == 'parse_error': body += ' const broken = ;'
    elif damage == 'record_cap': body = BIND + CALL * 65
    elif damage == 'binding_cap': body = 'const {' + ','.join(f'k{i}: v{i}' for i in range(65)) + '} = useJobs();'
    elif damage == 'scan_cap': body = BIND + ';' * 10001 + CALL
    source = declaration + '\nexport function Owner() {' + body + '}'
    path = tmp_path / 'Capacity.tsx'
    path.write_bytes(source.encode('utf-8'))
    lane = next(row for row in _normalize_polyglot_symbols(_native(path), source, 'typescript') if row['name'] == 'Owner')[KEY]
    if damage == 'record_cap':
        assert lane['status'] == 'incomplete_scan' and len(lane['records']) == 64 and lane['omitted'] == 1
    elif damage == 'binding_cap':
        assert lane['status'] == 'incomplete_scan' and lane['records'] == [] and lane['omitted'] == 1
    else:
        assert lane['records'] == []
        assert lane['status'] == ('unavailable' if damage in {'parse_error', 'scan_cap'} else 'observed')


@pytest.mark.parametrize('damage', ['none', 'missing', 'hash', 'ast', 'parser', 'authority', 'bool_span',
    'outer', 'duplicate', 'omitted', 'incomplete', 'budget', 'roles', 'extra_field', 'optional',
    'kind', 'limit', 'marker_width'])
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
    elif damage == 'roles': lane['records'][0]['spans'].pop('optional_operator')
    elif damage == 'extra_field': lane['callability'] = 'proven'
    elif damage == 'optional': lane['records'][0]['optional_call'] = False
    elif damage == 'kind': lane['records'][0]['kind'] = 'namespace'
    elif damage == 'marker_width': lane['records'][0]['spans']['optional_operator']['end'] += 1
    result = _focus_call_result_alias_invocations(record, row, 0 if damage == 'limit' else 3,
                                                 0 if damage == 'budget' else 64, _optional=True)
    if damage == 'none': assert result['status'] == 'observed' and result['returned'] == 1
    elif damage == 'limit': assert result['status'] == 'observed' and result['returned'] == 0 and result['omitted'] == 1
    else: assert result['status'] in {'unavailable', 'incomplete_scan'} and result['items'] == []
    assert result['relation'] == 'const_call_result_property_alias_to_immediate_optional_identifier_invocation_source_association'
    for field in ('value_transfer', 'runtime_execution', 'callability', 'callback_activation', 'schema_validity'):
        assert result[field] == 'not_established'


@pytest.mark.parametrize('damage', ['none', 'authority', 'extra', 'role', 'bool_span', 'kind', 'optional', 'extra_role'])
def test_schema_guard(inputs, tmp_path, damage):
    from tools.core.artifact_validator import validate_against_schema
    schema = json.loads((ROOT / 'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    path = tmp_path / 'schema.json'
    path.write_text(json.dumps({'$defs': schema['$defs'], '$ref': '#/$defs/' + KEY}), encoding='utf-8')
    lane = deepcopy(inputs[2]['Case_plain'][KEY])
    if damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'extra': lane['callability'] = 'proven'
    elif damage == 'role': lane['records'][0]['spans'].pop('optional_operator')
    elif damage == 'bool_span': lane['records'][0]['spans']['invoked_identifier']['start'] = True
    elif damage == 'kind': lane['records'][0]['kind'] = 'parameter'
    elif damage == 'optional': lane['records'][0]['optional_call'] = False
    elif damage == 'extra_role': lane['records'][0]['spans']['runtime_value'] = lane['records'][0]['spans']['optional_operator']
    assert (not validate_against_schema(path, 'direct_optional_alias', lane)) == (damage == 'none')


@pytest.fixture(scope='module')
def atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp('direct-optional-atlas')
    source = """import { useJobs } from './jobs';
export function owner(config: any) {
  const { task: run } = useJobs();
  run(); run?.(); run?.(1);
  consume({ complete: () => { run(); } });
  consume({ complete: (...args) => { run?.(...args); }, ...config });
}
export function parameterOwner(config: any) {
  const { task: run } = config || {};
  consume({ complete: (...args) => { run?.(...args); } });
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
    server._ATLAS_CACHE.clear()
    cold = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    warm = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    assert cold == warm
    record_property('observed_focused_state_flow', json.dumps(cold, ensure_ascii=False))
    lane = cold['symbol_context'][FOCUS]
    assert lane['status'] == 'observed' and lane['returned'] == lane['omitted'] == 1
    assert lane['source_binding'] == 'snapshot_and_live_match' and lane['items'][0]['local_name'] == 'run'
    assert len(lane['items'][0]['spans']) == 18 and lane['items'][0]['optional_call'] is True
    for key in ('call_result_property_bindings', 'call_result_alias_invocations',
                'call_result_nested_callback_alias_invocations', 'call_result_optional_rest_alias_invocations'):
        assert cold['symbol_context'][key]['returned'] == 1
    assert FOCUS in server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), max_items=1)
    parameter = json.loads(server.get_state_flow(file='Panel.ts', symbol='parameterOwner', target_root=str(target), format='json', max_items=1))
    assert parameter['symbol_context']['parameter_config_callback_forwardings']['returned'] == 1
    assert parameter['symbol_context'][FOCUS]['returned'] == 0


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
    assert 'direct optional' in action or 'opt' in action.replace('/', ' ').replace(';', ' ').split()
    assert 'absence' in action


@pytest.mark.parametrize('mode', ['direct', 'deferred', 'legacy'])
def test_focus_selected_projection(atlas_run, mode):
    from tools.core.state_flow import project_state_flow_focus
    atlas = deepcopy(atlas_run[2])
    owner = next(row for row in atlas['MAIN']['files']['Panel.ts']['symbols'] if row['name'] == 'owner')
    if mode == 'legacy': owner.pop(KEY)
    deferred = {}
    result = project_state_flow_focus(atlas, project='MAIN', file='Panel.ts', symbol='owner',
        max_items=1, scan_limit=1000,
        **({'_deferred_optional_alias_invocations': deferred} if mode == 'deferred' else {}))
    assert result['status'] == 'selected'
    if mode == 'direct':
        assert result['symbol_context'][FOCUS]['returned'] == result['symbol_context'][FOCUS]['omitted'] == 1
    else:
        assert FOCUS not in result['symbol_context']
        if mode == 'deferred': assert deferred['symbol'] is owner
    assert result['symbol_context']['call_result_alias_invocations']['returned'] == 1


@pytest.mark.parametrize('partitioned', [False, True])
@pytest.mark.parametrize('damage', ['none', 'legacy', 'source_drift', 'corrupt_primary', 'missing_primary',
                                   'poison_cache', 'scan_budget', 'malformed_metadata'])
def test_real_primary_source_refusal(atlas_run, tmp_path, monkeypatch, damage, partitioned):
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
    elif damage == 'malformed_metadata':
        next(row for row in atlas['MAIN']['files']['Panel.ts']['symbols'] if row['name'] == 'owner')[KEY]['records'][0]['optional_call'] = False
    raw = tmp_path / '.raw'
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    store.state_payload_inline_limit_bytes = 1 if partitioned else 10**7
    store._save_payload_to_state_table('atlas', atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / 'codemaps.db') as connection:
        connection.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == 'corrupt_primary':
            if partitioned: connection.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: connection.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
        elif damage == 'missing_primary': connection.execute("DELETE FROM state_payloads WHERE name='atlas'")
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

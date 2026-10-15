"""Finite returned callback source association, never callback/runtime proof."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tools.engines.generate_atlas import AST_CONTRACT_VERSION, _normalize_polyglot_symbols

ROOT = Path(__file__).resolve().parents[2]
KEY = 'returned_local_callback_evidence'
PLAIN = 'const cb = useCallback(() => { return 1; }, []); return { cb };'
CASES = {
    'plain': PLAIN,
    'alias': PLAIN.replace('useCallback(', 'cache('),
    'async_default': 'const other = 1; const cb = useCallback(async (options = {}) => { try { await send(options); } catch (e) { toast(e); } finally { cleanup(); } }, []); return { other, cb };',
    'unicode': PLAIN.replace('cb', 'çağrı'),
    'wrong_package': PLAIN.replace('useCallback(', 'foreign('),
    'type_clause': PLAIN.replace('useCallback(', 'typeCallback('),
    'type_specifier': PLAIN.replace('useCallback(', 'typeSpecifier('),
    'namespace': PLAIN.replace('useCallback(', 'React.useCallback('),
    'default_import': PLAIN.replace('useCallback(', 'DefaultReact('),
    'escaped_module': PLAIN.replace('useCallback(', 'escaped('),
    'local_alias': 'const local = useCallback; ' + PLAIN.replace('useCallback(', 'local('),
    'duplicate': 'const cb = 1; ' + PLAIN,
    'let': PLAIN.replace('const cb', 'let cb'),
    'multi': PLAIN.replace('; return', ', other = 1; return'),
    'optional': PLAIN.replace('useCallback(', 'useCallback?.('),
    'generic': PLAIN.replace('useCallback(', 'useCallback<any>('),
    'function_arg': PLAIN.replace('() =>', 'function()'),
    'expression_arg': PLAIN.replace('() => { return 1; }', '() => 1'),
    'wrong_arity': PLAIN.replace(', []', ''),
    'indirect_deps': 'const deps = []; ' + PLAIN.replace(', []', ', deps'),
    'nested_decl': 'if (true) { const cb = useCallback(() => {}, []); } return { cb };',
    'reassign': PLAIN.replace('; return', '; cb = other; return'),
    'array_write': PLAIN.replace('; return', '; [cb] = others; return'),
    'object_write': PLAIN.replace('; return', '; ({ cb } = other); return'),
    'nested_write': PLAIN.replace('; return', '; function deferred() { cb = other; } return'),
    'default_write': PLAIN.replace('() => { return 1; }', '(value = (cb = other)) => { return value; }'),
    'import_write': PLAIN.replace('; return', '; useCallback = other; return'),
    'renamed': PLAIN.replace('{ cb }', '{ other: cb }'),
    'spread': PLAIN.replace('{ cb }', '{ ...other, cb }'),
    'duplicate_key': PLAIN.replace('{ cb }', '{ cb, cb }'),
    'quoted_key': PLAIN.replace('{ cb }', '{ "cb": cb, cb }'),
    'computed': PLAIN.replace('{ cb }', '{ [other]: 1, cb }'),
    'getter': PLAIN.replace('{ cb }', '{ get other() { return 1; }, cb }'),
    'method': PLAIN.replace('{ cb }', '{ other() {}, cb }'),
    'shorthand_default': PLAIN.replace('{ cb }', '{ cb = other }'),
    'extra_return': PLAIN.replace('return { cb }', 'if (flag) return null; return { cb }'),
    'branch_return': PLAIN.replace('return { cb };', 'if (flag) return { cb };'),
    'deferred_return': PLAIN.replace('return { cb };', 'later(() => { return { cb }; });'),
    'indirect_return': PLAIN.replace('return { cb };', 'const result = { cb }; return result;'),
    'cast_return': PLAIN.replace('return { cb };', 'return ({ cb } as any);'),
    'early_return': 'return { cb }; const cb = useCallback(() => {}, []);',
    'escaped_identifier': PLAIN.replace('cb', r'c\u0062'),
}


def _packet_before_context(packet, key):
    """Ordered older-stage baseline; later additive lanes are not this stage's data."""
    baseline=deepcopy(packet)
    keys=list(baseline['symbol_context'])
    older=keys[:keys.index(key)]
    baseline['symbol_context']={k:baseline['symbol_context'][k] for k in older}
    return baseline


def _native(path):
    node = shutil.which('node')
    assert node, 'Native contract requires the configured Node runtime'
    run = subprocess.run([node, str(ROOT / 'tools/engines/ast_sequencer.cjs'), str(path)],
                         cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=40)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.fixture(scope='module')
def callback_inputs(tmp_path_factory):
    declarations = [f'export function Case_{name}() {{ {body} }}' for name, body in CASES.items()]
    declarations += [f'export const Case_arrow = () => {{ {PLAIN} }};',
        f'export function Case_shadow(useCallback: any) {{ {PLAIN} }}',
        f'export async function Case_async_owner() {{ {PLAIN} }}',
        f'export function* Case_generator() {{ {PLAIN} }}',
        PLAIN.join(['export function Case_late() { ', ' }'])]
    # Imports after owners are legal source bindings; no import-before-owner assumption.
    declarations += ["import { useCallback, useCallback as cache } from 'react';",
        "import { useCallback as foreign } from 'another';",
        "import type { useCallback as typeCallback } from 'react';",
        "import { type useCallback as typeSpecifier } from 'react';",
        "import * as React from 'react'; import DefaultReact from 'react';",
        r"import { useCallback as escaped } from '\u0072eact';"]
    source = '\ufeff// \U0001f680 exact CRLF identity\r\n' + '\r\n'.join(declarations)
    path = tmp_path_factory.mktemp('returned-callback') / 'Cases.ts'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    rows = {row['name']: row for row in _normalize_polyglot_symbols(raw, source, 'typescript')}
    return source, raw, rows


@pytest.mark.parametrize('name', ['plain', 'alias', 'async_default', 'unicode', 'arrow', 'late'])
def test_native_positive(callback_inputs, name):
    source, _, rows = callback_inputs
    lane = rows['Case_' + name][KEY]
    assert lane['status'] == 'observed', lane
    assert lane['omitted'] == 0 and len(lane['records']) == 1
    assert lane['source_hash'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    assert lane['ast_contract_version'] == AST_CONTRACT_VERSION
    row = lane['records'][0]
    assert len(row['spans']) == 18 and row['property_name'] == row['callback_name']
    for role, text in [('binding', row['callback_name']), ('value', row['callback_name']),
                       ('callee', row['localName']), ('imported_binding', 'useCallback')]:
        span = row['spans'][role]
        assert source.encode('utf-8')[span['start']:span['end']].decode('utf-8') == text
    assert lane['runtime_execution'] == 'not_established'


@pytest.mark.parametrize('name', [name for name in CASES if name not in {'plain', 'alias', 'async_default', 'unicode'}]
                         + ['shadow', 'async_owner', 'generator'])
def test_native_negative(callback_inputs, name):
    lane = callback_inputs[2]['Case_' + name][KEY]
    assert lane['records'] == [], lane
    assert lane['runtime_execution'] == 'not_established'


@pytest.mark.parametrize('damage', ['hash', 'version', 'authority', 'bool_span', 'lines', 'missing_role',
                                  'extra_role', 'order', 'duplicate', 'status_type', 'omitted', 'parser', 'imports', 'token'])
def test_normalizer_damage(callback_inputs, damage):
    source, original, _ = callback_inputs
    raw = deepcopy(original)
    owner = next(row for row in raw if row['name'] == 'Case_plain')
    lane = owner[KEY]
    if damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'version': lane['version'] = 'old'
    elif damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'bool_span': lane['records'][0]['spans']['binding']['start'] = True
    elif damage == 'lines': lane['records'][0]['spans']['binding']['line'] += 1
    elif damage == 'missing_role': lane['records'][0]['spans'].pop('dependencies')
    elif damage == 'extra_role': lane['records'][0]['spans']['unknown'] = deepcopy(lane['records'][0]['spans']['value'])
    elif damage == 'order': lane['records'][0]['spans']['callee'] = deepcopy(lane['records'][0]['spans']['binding'])
    elif damage == 'duplicate': lane['records'].append(deepcopy(lane['records'][0]))
    elif damage == 'status_type': lane['status'] = []
    elif damage == 'omitted': lane['omitted'] = -1
    elif damage == 'parser': owner['parserKind'] = 'fallback'
    elif damage == 'imports': next(row for row in raw if row['name'] == '__file_meta__')['directImportBindings'] = []
    elif damage == 'token': lane['records'][0]['callback_name'] = lane['records'][0]['property_name'] = 'other'
    result = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == 'Case_plain')[KEY]
    assert result['status'] == 'unavailable' and result['records'] == []


def test_one_shared_coordinate_pass(callback_inputs, monkeypatch):
    from tools.engines import generate_atlas
    source, raw, _ = callback_inputs
    original, calls = generate_atlas._utf16_offsets_to_utf8_byte_offsets, []
    def track(content, offsets):
        calls.append(1)
        return original(content, offsets)
    monkeypatch.setattr(generate_atlas, '_utf16_offsets_to_utf8_byte_offsets', track)
    rows = generate_atlas._normalize_polyglot_symbols(raw, source, 'typescript')
    assert len(calls) == 1 and next(row for row in rows if row['name'] == 'Case_unicode')[KEY]['records']


@pytest.mark.parametrize('damage', ['none', 'missing', 'hash', 'ast', 'parser', 'authority', 'bool_span',
                                  'outer', 'duplicate', 'omitted', 'incomplete', 'budget', 'lines', 'roles'])
def test_focused_integrity(callback_inputs, damage):
    from tools.core.state_flow import _focus_returned_local_callbacks
    source, _, original = callback_inputs
    row = deepcopy(original['Case_plain'])
    record = {'hash': hashlib.sha256(source.encode('utf-8')).hexdigest(), 'size': len(source.encode('utf-8')),
              'loc': len(source.splitlines()), 'parser_evidence': {'status': 'observed'},
              'ast_contract_version': AST_CONTRACT_VERSION}
    lane = row[KEY]
    if damage == 'missing': row.pop(KEY)
    elif damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'ast': record['ast_contract_version'] = 'old'
    elif damage == 'parser': record['parser_evidence']['status'] = 'degraded'
    elif damage == 'authority': lane['callback_activation'] = 'proven'
    elif damage == 'bool_span': lane['records'][0]['spans']['owner']['end'] = True
    elif damage == 'outer': row['start'] += 1
    elif damage == 'duplicate': lane['records'].append(deepcopy(lane['records'][0]))
    elif damage == 'omitted': lane['omitted'] = -1
    elif damage == 'incomplete': lane.update(status='incomplete_scan', omitted=1)
    elif damage == 'lines': lane['records'][0]['spans']['binding']['line'] = record['loc']
    elif damage == 'roles': lane['records'][0]['spans'].pop('callee')
    result = _focus_returned_local_callbacks(record, row, 3, 0 if damage == 'budget' else 64)
    if damage == 'none': assert result['status'] == 'observed' and result['returned'] == 1
    else: assert result['status'] in {'unavailable', 'incomplete_scan'} and result['items'] == []
    for field in ('value_transfer', 'runtime_execution', 'callback_activation', 'schema_validity'):
        assert result[field] == 'not_established'


@pytest.mark.parametrize('damage', ['none', 'authority', 'unknown_field', 'missing_role', 'bool_span', 'wrong_import'])
def test_schema_is_an_executable_guard(callback_inputs, tmp_path, damage):
    from tools.core.artifact_validator import validate_against_schema
    schema = json.loads((ROOT / 'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    path = tmp_path / 'schema.json'
    path.write_text(json.dumps({'$defs': schema['$defs'], '$ref': '#/$defs/returned_local_callback_evidence'}), encoding='utf-8')
    lane = deepcopy(callback_inputs[2]['Case_plain'][KEY])
    if damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'unknown_field': lane['schema_validity'] = 'proven'
    elif damage == 'missing_role': lane['records'][0]['spans'].pop('callback_body')
    elif damage == 'bool_span': lane['records'][0]['spans']['binding']['start'] = True
    elif damage == 'wrong_import': lane['records'][0]['importedName'] = 'useMemo'
    errors = validate_against_schema(path, 'callback', lane)
    assert (not errors) == (damage == 'none'), errors


def test_native_scan_and_row_caps(tmp_path):
    declarations = ' '.join(f'const cb{i} = useCallback(() => {{}}, []);' for i in range(65))
    source = "import { useCallback } from 'react'; export function owner() { " + declarations
    source += ' return { ' + ', '.join(f'cb{i}' for i in range(65)) + ' }; }'
    path = tmp_path / 'Caps.ts'
    path.write_text(source, encoding='utf-8')
    raw = _native(path)
    row = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == 'owner')
    assert row[KEY]['status'] == 'incomplete_scan' and len(row[KEY]['records']) == 64 and row[KEY]['omitted'] == 1
    source = "import { useCallback } from 'react'; export function owner() { const padding = [" + ','.join('1' for _ in range(10001))
    source += ']; ' + PLAIN + ' }'
    path.write_text(source, encoding='utf-8')
    owner = next(row for row in _native(path) if row['name'] == 'owner')
    assert owner[KEY]['status'] == 'unavailable' and owner[KEY]['records'] == []


@pytest.fixture(scope='module')
def callback_atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp('returned-cb-atlas')
    source = """import { useCallback } from 'react';
export function owner() {
  const first = useCallback(async (value = {}) => { await send(value); }, []);
  const second = useCallback(() => { return 1; }, []);
  return { first, second };
}
throw new Error('Target source must never execute');
"""
    (target / 'Panel.ts').write_bytes(('\ufeff// \U0001f680\r\n' + source.replace('\n', '\r\n')).encode('utf-8'))
    env = dict(os.environ)
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT', 'CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256',
                'CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE', 'CODEMAPS_TARGET_PROJECTS'):
        env.pop(key, None)
    env['CODEMAPS_TARGET_ROOT'] = str(target)
    run = subprocess.run([sys.executable, '-B', '-m', 'tools.engines.generate_atlas'], cwd=ROOT,
                         env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    raw = ROOT / 'output/external_targets' / external_target_output_slug(str(target)) / '.raw'
    return target, raw, load_atlas_data(raw)


def test_real_sqlite_mcp_and_cap(callback_atlas_run):
    from tools.mcp import server
    target, _, atlas = callback_atlas_run
    owner = next(row for row in atlas['MAIN']['files']['Panel.ts']['symbols'] if row['name'] == 'owner')
    assert len(owner[KEY]['records']) == 2
    server._ATLAS_CACHE.clear()
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    lane = result['symbol_context']['returned_local_callbacks']
    assert lane['status'] == 'observed' and lane['returned'] == lane['omitted'] == 1
    assert lane['source_binding'] == 'snapshot_and_live_match'
    assert lane['items'][0]['callback_name'] == 'first'
    assert 'target' not in lane['items'][0], 'A local callback is not an indexed callee'
    assert 'returned_local_callbacks' in server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), max_items=1)


def test_exact_settled_packet_cap_preserves_prior_lanes(callback_atlas_run, monkeypatch):
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    target, _, _ = callback_atlas_run
    full = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json'))
    baseline = _packet_before_context(full, 'returned_local_callbacks')
    budget = context_budget_profile(json.dumps(baseline, ensure_ascii=False))['estimated_tokens']
    monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', budget)
    limited = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json'))
    assert 'returned_local_callbacks' not in limited['symbol_context']
    assert limited['symbol_context'] == baseline['symbol_context']
    profile = context_budget_profile(json.dumps(limited, ensure_ascii=False), budget_tokens=budget)
    assert profile['status'] == 'pass', (profile, limited['next_action'], baseline['next_action'])
    assert 'returned callback' in limited['next_action'].lower() and 'absence' in limited['next_action']


@pytest.mark.parametrize('damage', ['none', 'legacy', 'source_drift', 'corrupt_primary', 'packet_budget', 'poison_cache', 'scan_budget'])
@pytest.mark.parametrize('target_path_length', ['short', 'long'])
def test_real_primary_source_and_packet_refusal(callback_atlas_run, tmp_path, monkeypatch, damage, target_path_length):
    from tools.core.artifact_store import ArtifactStore
    from tools.core.unmanaged_atomic_io import native_filesystem_path
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    import sqlite3
    target, _, original = callback_atlas_run
    segments = ['long-path-segment'] * 8 if target_path_length == 'long' else []
    local = tmp_path.joinpath(*segments, 'target')
    # Physical I/O uses the shared Windows path owner; Atlas keeps the logical path.
    physical = Path(native_filesystem_path(local))
    physical.mkdir(parents=True)
    (physical / 'Panel.ts').write_bytes((target / 'Panel.ts').read_bytes())
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
        snapshot = connection.execute(
            "SELECT status, content_hash FROM source_snapshots WHERE project_key='MAIN' AND rel_path='Panel.ts'"
        ).fetchone()
        assert snapshot == ('ok', atlas['MAIN']['files']['Panel.ts']['hash'])
        connection.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == 'corrupt_primary': connection.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    # A JSON compatibility shadow cannot rescue or override the actual primary.
    (raw / 'atlas.json').write_text(json.dumps(original), encoding='utf-8')
    if damage == 'source_drift': (physical / 'Panel.ts').write_bytes((physical / 'Panel.ts').read_bytes() + b'\n// drift')
    if damage == 'packet_budget': monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', 2600)
    if damage == 'scan_budget':
        policy = dict(server._state_flow_brief_policy())
        policy['focus_scan_limit'] = 7
        monkeypatch.setattr(server, '_state_flow_brief_policy', lambda: policy)
    server._ATLAS_CACHE.clear()
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda root: raw)
    monkeypatch.setattr(server, '_analysis_root_display', lambda root: str(local))
    if damage == 'poison_cache':
        poisoned = deepcopy(atlas)
        next(row for row in poisoned['MAIN']['files']['Panel.ts']['symbols'] if row['name'] == 'owner')[KEY]['records'][0]['callback_name'] = 'poison'
        # Exercise the real materialized cache route before contaminating its object.
        cached = server._atlas_read(raw).data
        cached.clear()
        cached.update(poisoned)
        server._ATLAS_CACHE[str(raw)] = poisoned
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(local), format='json'))
    lane = result.get('symbol_context', {}).get('returned_local_callbacks')
    if damage in {'none', 'poison_cache'}:
        assert lane['status'] == 'observed' and lane['returned'] == 2
        assert lane['source_binding'] == 'snapshot_and_live_match'
        assert lane['items'][0]['callback_name'] == 'first', 'Old mutable cache is not primary evidence'
    elif damage == 'legacy': assert lane is None
    else:
        assert lane is None or (lane['status'] in {'unavailable', 'incomplete_scan'} and lane['returned'] == 0)
    if damage == 'packet_budget':
        profile = context_budget_profile(json.dumps(result, ensure_ascii=False), budget_tokens=2600)
        assert profile['status'] == 'pass', (profile, result.get('next_action'), list(result))
        disclosure = result['next_action'].casefold()
        assert 'returned callback' in disclosure and 'absence' in disclosure
    if damage == 'scan_budget':
        assert result['visited_records'] <= 7

"""Returned async callback parameter syntax, never callable/value/runtime proof."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.engines import generate_atlas as ga
from tools.core.state_flow import _focus_returned_callback_parameter_optional
from tools.tests.test_state_flow_returned_local_callback_contract import _native

ROOT = Path(__file__).resolve().parents[2]
KEY = 'returned_callback_parameter_optional_call_evidence'
CONTEXT = 'returned_callback_parameter_optional_calls'
PLAIN = 'const cb = useCallback(async (options = {}) => { const { onDone } = options; try { const data = source; await onDone?.(data); } catch (e) { fail(e); } }, []); return { cb };'
CASES = {
    'plain': PLAIN,
    'no_input_default': PLAIN.replace('options = {}', 'options'),
    'nonfirst': PLAIN.replace('options = {}', 'endpoint: string, options = {}'),
    'alias_import': PLAIN.replace('useCallback(', 'cache('),
    'renamed_unicode': PLAIN.replace('onDone }', 'achèvement: çağrı }').replace('onDone?.', 'çağrı?.'),
    'literal_siblings': PLAIN.replace('{ onDone }', '{ method = "GET", count = 1, enabled = true, disabled = false, empty = null, onDone }'),
    'arbitrary': PLAIN.replace('options', 'input').replace('onDone', 'notify').replace('data', 'result').replace('cb', 'perform'),
    'branch_elsewhere': PLAIN.replace('const data = source;', 'if (flag) throw err; const data = source;'),
    'nested_var_independent': PLAIN.replace('try {', 'function later() { var onDone = other; } try {'),
    'selected_default': PLAIN.replace('{ onDone }', '{ onDone = noop }'),
    'literal_selected_default': PLAIN.replace('{ onDone }', '{ onDone = null }'),
    'effectful_default': PLAIN.replace('{ onDone }', '{ method = setup(), onDone }'),
    'template_default': PLAIN.replace('{ onDone }', '{ method = `GET`, onDone }'),
    'unary_default': PLAIN.replace('{ onDone }', '{ count = -1, onDone }'),
    'input_effectful': PLAIN.replace('options = {}', 'options = setup()'),
    'input_nonempty': PLAIN.replace('options = {}', 'options = { onDone: noop }'),
    'other_default': PLAIN.replace('options = {}', 'endpoint = "", options = {}'),
    'input_rest': PLAIN.replace('options = {}', '...options'),
    'other_rest': PLAIN.replace('options = {}', 'options, ...args'),
    'input_pattern': PLAIN.replace('options = {}', '{ options } = {}'),
    'duplicate_parameters': PLAIN.replace('options = {}', 'options, options'),
    'rest_binding': PLAIN.replace('{ onDone }', '{ onDone, ...rest }'),
    'nested_binding': PLAIN.replace('{ onDone }', '{ nested: { onDone } }'),
    'computed_binding': PLAIN.replace('{ onDone }', '{ [key]: onDone }'),
    'quoted_binding': PLAIN.replace('{ onDone }', '{ "onDone": onDone }'),
    'escaped_binding': PLAIN.replace('{ onDone }', r'{ onD\u006fne }'),
    'duplicate_keys': PLAIN.replace('{ onDone }', '{ onDone, onDone: other }'),
    'duplicate_locals': PLAIN.replace('{ onDone }', '{ onDone, other: onDone }'),
    'let_binding': PLAIN.replace('const {', 'let {'),
    'multiple_declarations': PLAIN.replace('= options;', '= options, other = 1;'),
    'indirect_input': PLAIN.replace('= options;', '= options || {};'),
    'shadow_input': PLAIN.replace('const {', 'const options = other; const {'),
    'shadow_input_pattern': PLAIN.replace('const {', 'const { extra: options } = other; const {'),
    'shadow_call': PLAIN.replace('const data = source;', 'const onDone = other; const data = source;'),
    'duplicate_body_binding': PLAIN.replace('try {', 'const onDone = other; try {'),
    'duplicate_body_binding_before': PLAIN.replace('const {', 'const onDone = other; const {'),
    'var_try_binding': PLAIN.replace('const data = source;', 'var onDone = other; const data = source;'),
    'var_nested_binding': PLAIN.replace('const data = source;', 'if (flag) { var onDone = other; } const data = source;'),
    'var_nested_input': PLAIN.replace('const data = source;', 'if (flag) { var options = other; } const data = source;'),
    'input_write': PLAIN.replace('try {', 'options = other; try {'),
    'element_write': PLAIN.replace('try {', 'onDone = other; try {'),
    'nested_write': PLAIN.replace('try {', 'function later() { onDone = other; } try {'),
    'destructuring_write': PLAIN.replace('try {', '({ onDone } = other); try {'),
    'sibling_write': PLAIN.replace('{ onDone }', '{ method = "GET", onDone }').replace('try {', 'method = other; try {'),
    'default_nested_write': PLAIN.replace('try {', 'function later(value = (onDone = other)) {} try {'),
    'nonasync': PLAIN.replace('async ', ''),
    'nonoptional': PLAIN.replace('onDone?.(', 'onDone('),
    'generic': PLAIN.replace('onDone?.(', 'onDone?.<any>('),
    'member': PLAIN.replace('onDone?.(', 'onDone.run?.('),
    'wrapped': PLAIN.replace('onDone?.(', '(onDone)?.('),
    'spread_argument': PLAIN.replace('?.(data)', '?.(...data)'),
    'many_arguments': PLAIN.replace('?.(data)', '?.(data, other)'),
    'literal_argument': PLAIN.replace('?.(data)', '?.(1)'),
    'unawaited': PLAIN.replace('await onDone', 'onDone'),
    'conditional': PLAIN.replace('await onDone?.(data);', 'if (flag) { await onDone?.(data); }'),
    'nested_block': PLAIN.replace('await onDone?.(data);', '{ await onDone?.(data); }'),
    'returned': PLAIN.replace('await onDone?.(data);', 'return await onDone?.(data);'),
    'deferred': PLAIN.replace('await onDone?.(data);', 'later(async () => { await onDone?.(data); });'),
    'nested_try': PLAIN.replace('await onDone?.(data);', 'try { await onDone?.(data); } catch (x) {}'),
    'catch': PLAIN.replace('await onDone?.(data);', '').replace('fail(e);', 'await onDone?.(e);'),
    'finally': PLAIN.replace('await onDone?.(data);', '').replace('catch (e) { fail(e); }', 'finally { await onDone?.(data); }'),
    'outside_try': PLAIN.replace('try { const data = source; await onDone?.(data); } catch (e) { fail(e); }', 'const data = source; await onDone?.(data);'),
    'binding_nested': PLAIN.replace('const { onDone } = options;', '{ const { onDone } = options; }'),
    'binding_after_try': PLAIN.replace('const { onDone } = options; ', '').replace('catch (e) { fail(e); }', 'catch (e) { fail(e); } const { onDone } = options;'),
    'wrong_import': PLAIN.replace('useCallback(', 'foreign('),
    'type_import': PLAIN.replace('useCallback(', 'typeCallback('),
    'namespace': PLAIN.replace('useCallback(', 'React.useCallback('),
    'import_write': PLAIN.replace('const cb', 'useCallback = other; const cb'),
    'indirect_return': PLAIN.replace('return { cb };', 'const result = { cb }; return result;'),
    'branch_return': PLAIN.replace('return { cb };', 'if (flag) return { cb };'),
}
POSITIVES = {'plain', 'no_input_default', 'nonfirst', 'alias_import', 'renamed_unicode', 'literal_siblings', 'arbitrary', 'branch_elsewhere', 'nested_var_independent'}


@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    text = '\ufeff// \U0001f680 coordinates\r\n' + '\r\n'.join(
        [f'export function Case_{name}() {{ {body} }}' for name, body in CASES.items()] + [
            "import { useCallback, useCallback as cache } from 'react';",
            "import { useCallback as foreign } from 'other';",
            "import type { useCallback as typeCallback } from 'react';",
            "import * as React from 'react';",
            "throw new Error('SOURCE MUST NEVER EXECUTE');"])
    path = tmp_path_factory.mktemp('returned-parameter') / 'Cases.tsx'
    path.write_bytes(text.encode('utf-8'))
    native = _native(path)
    normalized = ga._normalize_polyglot_symbols(native, text, 'typescript')
    return text, {r['name']: r for r in native}, {r['name']: r for r in normalized}, native


@pytest.mark.parametrize('name', CASES)
def test_native_finite_admission(inputs, name):
    text, raw, rows, _ = inputs
    lane = rows['Case_' + name][KEY]
    assert lane['runtime_execution'] == 'not_established'
    assert len(lane['records']) == int(name in POSITIVES), (name, lane)
    if name not in POSITIVES: return
    assert lane['status'] == 'observed' and lane['omitted'] == 0
    row = lane['records'][0]
    assert len(row['spans']) == 35 and row['awaited'] is row['optional_call'] is True
    anchor = ga._returned_parameter_anchor(row)
    assert anchor == rows['Case_' + name]['returned_local_callback_evidence']['records'][0]
    data = text.encode('utf-8')
    snippets = {role: data[s['start']:s['end']].decode('utf-8') for role, s in row['spans'].items()}
    assert snippets['optional_operator'] == '?.'
    assert snippets['parameter_name'] == snippets['parameter_input'] == row['parameter_name']
    assert snippets['callback_local_binding'] == snippets['invoked_identifier'] == row['parameter_local_name']
    assert snippets['argument'] == row['argument_name']
    assert snippets['await_expression'].startswith('await ')
    assert row['unselected_literal_default_count'] == (5 if name == 'literal_siblings' else 0)
    assert raw['Case_' + name][KEY]['offset_unit'] == 'utf16_code_units'


def test_single_file_coordinate_pass(inputs, monkeypatch):
    text, _, _, native = inputs
    original = ga._utf16_offsets_to_utf8_byte_offsets
    original_anchor = ga._normalize_returned_local_callbacks
    calls, anchors = [], []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(ga, '_utf16_offsets_to_utf8_byte_offsets', counted)
    def counted_anchor(*args, **kwargs):
        anchors.append(1)
        return original_anchor(*args, **kwargs)
    monkeypatch.setattr(ga, '_normalize_returned_local_callbacks', counted_anchor)
    rows = ga._normalize_polyglot_symbols(native, text, 'typescript')
    assert len(calls) == 1
    assert len(anchors) == sum('returned_local_callback_evidence' in row for row in native)
    assert sum(len(row.get(KEY, {}).get('records', [])) for row in rows) == len(POSITIVES)


@pytest.mark.parametrize('damage', ['hash', 'version', 'runtime', 'flag', 'unknown', 'missing_role', 'wrong_token',
    'wrong_parameter', 'wrong_anchor', 'missing_anchor', 'incomplete_anchor', 'duplicate', 'parser', 'order', 'bool_count'])
def test_normalizer_refuses_corruption(inputs, damage):
    text, raw, _, _ = inputs
    symbol = deepcopy(raw['Case_plain'])
    lane, row = symbol[KEY], symbol[KEY]['records'][0]
    if damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'version': lane['version'] = 'future'
    elif damage == 'runtime': lane['runtime_execution'] = 'confirmed'
    elif damage == 'flag': row['optional_call'] = False
    elif damage == 'unknown': row['runtime_target'] = 'proved'
    elif damage == 'missing_role': row['spans'].pop('parameter_input')
    elif damage == 'wrong_token': row['spans']['optional_operator'] = row['spans']['argument']
    elif damage == 'wrong_parameter': row['parameter_local_name'] = 'wrong'
    elif damage == 'wrong_anchor': symbol['returned_local_callback_evidence']['records'] = []
    elif damage == 'missing_anchor': symbol.pop('returned_local_callback_evidence')
    elif damage == 'incomplete_anchor': symbol['returned_local_callback_evidence'].update(status='incomplete_scan', omitted=1)
    elif damage == 'duplicate': lane['records'].append(deepcopy(row))
    elif damage == 'parser': symbol['parserKind'] = 'regex'
    elif damage == 'order': row['spans']['try_statement'] = row['spans']['binding_statement']
    elif damage == 'bool_count': row['unselected_literal_default_count'] = True
    result = ga._normalize_polyglot_symbols([symbol], text, 'typescript')[0][KEY]
    assert result['status'] == 'unavailable' and result['records'] == [], (damage, result)


def record_for(text):
    return {'hash': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'size': len(text.encode('utf-8')),
        'loc': len(text.splitlines()), 'ast_contract_version': ga.AST_CONTRACT_VERSION,
        'parser_evidence': {'status': 'observed'}}


@pytest.mark.parametrize('damage', ['none', 'legacy', 'ast', 'hash', 'parser', 'flag', 'duplicate', 'anchor', 'budget', 'limit', 'extra', 'role'])
def test_selected_focus_guards(inputs, damage):
    text, _, rows, _ = inputs
    record, row = record_for(text), deepcopy(rows['Case_plain'])
    budget, limit = 500, 5
    if damage == 'legacy': row.pop(KEY)
    elif damage == 'ast': record['ast_contract_version'] = 'old'
    elif damage == 'hash': record['hash'] = '0' * 64
    elif damage == 'parser': record['parser_evidence']['status'] = 'unavailable'
    elif damage == 'flag': row[KEY]['records'][0]['awaited'] = False
    elif damage == 'duplicate': row[KEY]['records'] *= 2
    elif damage == 'anchor': row['returned_local_callback_evidence']['records'] = []
    elif damage == 'budget': budget = 3
    elif damage == 'limit': limit = 0
    elif damage == 'extra': row[KEY]['unknown'] = True
    elif damage == 'role': row[KEY]['records'][0]['spans'].pop('optional_operator')
    result = _focus_returned_callback_parameter_optional(record, row, limit, budget)
    assert result['visited_records'] <= budget
    if damage in {'none', 'limit'}:
        assert result['status'] == 'observed' and result['returned'] == int(damage == 'none')
        assert result['omitted'] == int(damage == 'limit')
    else:
        assert result['status'] in {'unavailable', 'incomplete_scan'} and result['returned'] == 0
    for key in ('runtime_execution', 'value_transfer', 'callability', 'callback_activation', 'schema_validity'):
        assert result[key] == 'not_established'


@pytest.mark.parametrize('damage', ['none', 'runtime', 'extra', 'missing_role', 'bool_span', 'flag', 'context', 'bool_count'])
def test_schema_guards(inputs, tmp_path, damage):
    from tools.core.artifact_validator import validate_against_schema
    schema = json.loads((ROOT / 'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    path = tmp_path / 'schema.json'
    path.write_text(json.dumps({'$defs': schema['$defs'], '$ref': '#/$defs/' + KEY}), encoding='utf-8')
    lane = deepcopy(inputs[2]['Case_plain'][KEY])
    row = lane['records'][0]
    if damage == 'runtime': lane['runtime_execution'] = 'proved'
    elif damage == 'extra': row['callability'] = 'proved'
    elif damage == 'missing_role': row['spans'].pop('argument')
    elif damage == 'bool_span': row['spans']['invocation']['start'] = True
    elif damage == 'flag': row['optional_call'] = False
    elif damage == 'context': row['context_kind'] = 'catch'
    elif damage == 'bool_count': row['unselected_literal_default_count'] = True
    assert (not validate_against_schema(path, 'returned-parameter', lane)) == (damage == 'none')


def test_native_caps_and_partial_anchor_refusal(tmp_path):
    path = tmp_path / 'Caps.ts'
    prefix = "import { useCallback } from 'react'; export function owner() { "
    source = prefix + PLAIN.replace('await onDone?.(data);', 'await onDone?.(data);' * 65) + ' }'
    path.write_text(source, encoding='utf-8')
    row = next(r for r in ga._normalize_polyglot_symbols(_native(path), source, 'typescript') if r['name'] == 'owner')
    assert row[KEY]['status'] == 'incomplete_scan' and len(row[KEY]['records']) == 64 and row[KEY]['omitted'] == 1
    assert _focus_returned_callback_parameter_optional(record_for(source), row, 64, 500)['returned'] == 0
    source = prefix + ' '.join(PLAIN.split('return')[0].replace('cb', f'cb{i}') for i in range(65))
    source += 'return { ' + ', '.join(f'cb{i}' for i in range(65)) + ' }; }'
    path.write_text(source, encoding='utf-8')
    row = next(r for r in _native(path) if r['name'] == 'owner')
    assert row['returned_local_callback_evidence']['status'] == 'incomplete_scan'
    assert row[KEY]['status'] == 'unavailable' and row[KEY]['records'] == []
    source = prefix + 'const padding = [' + ','.join('1' for _ in range(10001)) + ']; ' + PLAIN + ' }'
    path.write_text(source, encoding='utf-8')
    row = next(r for r in _native(path) if r['name'] == 'owner')
    assert row[KEY]['status'] == 'unavailable' and row[KEY]['records'] == []


def test_native_reuses_checker_owner_write_scan_and_anchor_cache():
    import shutil
    # Instrument actual trusted functions, not a reimplementation of the target grammar.
    script = r"""
const fs=require('fs'), path=require('path'), crypto=require('crypto'), vm=require('vm');
const ts=require('module').createRequire(path.resolve('tools/engines/ast_sequencer.cjs'))('typescript');
const source=fs.readFileSync('tools/engines/ast_sequencer.cjs','utf8');
const ctx={ts,path,crypto,counts:{program:0,writes:0}}; vm.createContext(ctx);
for(const name of ['createFileSymbolResolver','createBindingWriteCollector','createReturnedLocalCallbackCollector','createReturnedCallbackParameterOptionalCollector']){
 let start=source.indexOf('function '+name+'('), end=source.indexOf('\nfunction ',start+1);
 if(start<0||end<0)throw Error('actual function missing');
 let body=source.slice(start,end);
 if(name==='createFileSymbolResolver')body=body.replace('checker = ts.createProgram','counts.program++; checker = ts.createProgram');
 if(name==='createBindingWriteCollector')body=body.replace('const written = new Set();','counts.writes++; const written = new Set();');
 vm.runInContext(body,ctx);
}
const sf=ts.createSourceFile('Finite.ts',process.argv[1],ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
const owner=sf.statements.find(ts.isFunctionDeclaration), at=ctx.createFileSymbolResolver(sf), writes=ctx.createBindingWriteCollector(at);
const imports=new Map([['useCallback',{source:'react',localName:'useCallback',importedName:'useCallback',kind:'named',typeOnly:false}]]);
const anchors=ctx.createReturnedLocalCallbackCollector(sf,imports,at,writes);
const collect=ctx.createReturnedCallbackParameterOptionalCollector(sf,at,writes,anchors);
const first=collect(owner); for(let i=0;i<100;i++){if(collect(owner)!==first)throw Error('lost collector cache'); anchors(owner);}
process.stdout.write(JSON.stringify({counts:ctx.counts,records:first.records.length,anchors:anchors.anchors(owner).length}));
"""
    source = "import { useCallback } from 'react'; export function owner() { " + PLAIN + ' }'
    run = subprocess.run([shutil.which('node'), '-e', script, source], cwd=ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=40)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {'counts': {'program': 1, 'writes': 1}, 'records': 1, 'anchors': 1}


@pytest.fixture(scope='module')
def atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp('returned-param-atlas')
    source = "import { useCallback } from 'react'; export function owner() { "
    source += PLAIN.replace('await onDone?.(data);', 'await onDone?.(data); await onDone?.(other);') + ' }'
    source += "\nthrow new Error('TARGET MUST NEVER EXECUTE');\n"
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


def test_real_ingestion_sqlite_mcp_and_item_cap(atlas_run, record_property):
    from tools.mcp import server
    target, raw, atlas = atlas_run
    owner = next(r for r in atlas['MAIN']['files']['Panel.ts']['symbols'] if r['name'] == 'owner')
    assert len(owner[KEY]['records']) == 2
    assert server._atlas_read(raw).primary_verified
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json', max_items=1))
    lane = result['symbol_context'][CONTEXT]
    assert lane['status'] == 'observed' and lane['returned'] == lane['omitted'] == 1
    assert lane['source_binding'] == 'snapshot_and_live_match'
    assert len(lane['items'][0]['spans']) == 35 and 'target' not in lane['items'][0]
    assert result['symbol_context']['returned_local_callbacks']['returned'] == 1
    assert CONTEXT in server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), max_items=1)
    record_property('observed_focused_state_flow', json.dumps(result, ensure_ascii=True))


@pytest.mark.parametrize('damage', ['inline', 'partitioned', 'legacy', 'source_drift', 'corrupt_primary', 'missing_primary', 'poison_cache', 'scan_budget'])
def test_primary_identity_and_failure_boundary(atlas_run, tmp_path, monkeypatch, damage):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    import sqlite3
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
    store.state_payload_inline_limit_bytes = 10**9 if damage == 'inline' else 1
    store._save_payload_to_state_table('atlas', atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / 'codemaps.db') as connection:
        connection.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == 'corrupt_primary': connection.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
        if damage == 'missing_primary': connection.execute("DELETE FROM state_payloads WHERE name='atlas'")
    (raw / 'atlas.json').write_text(json.dumps(original), encoding='utf-8')
    if damage == 'source_drift': (local / 'Panel.ts').write_bytes((local / 'Panel.ts').read_bytes() + b'\n// drift')
    if damage == 'scan_budget':
        policy = dict(server._state_flow_brief_policy())
        policy['focus_scan_limit'] = 7
        monkeypatch.setattr(server, '_state_flow_brief_policy', lambda: policy)
    server._ATLAS_CACHE.clear()
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda root: raw)
    monkeypatch.setattr(server, '_analysis_root_display', lambda root: str(local))
    if damage == 'poison_cache':
        cached = server._atlas_read(raw).data
        next(r for r in cached['MAIN']['files']['Panel.ts']['symbols'] if r['name'] == 'owner')[KEY]['records'][0]['argument_name'] = 'poison'
        server._ATLAS_CACHE[str(raw)] = cached
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(local), format='json'))
    lane = result.get('symbol_context', {}).get(CONTEXT)
    if damage in {'inline', 'partitioned', 'poison_cache'}:
        assert lane['status'] == 'observed' and lane['returned'] == 2
        assert lane['items'][0]['argument_name'] == 'data'
    elif damage == 'legacy': assert lane is None
    else: assert lane is None or (lane['status'] in {'unavailable', 'incomplete_scan'} and lane['returned'] == 0)
    if damage == 'scan_budget': assert result['visited_records'] <= 7


def test_last_lane_packet_budget_preserves_all_old_context(atlas_run, monkeypatch):
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    target, _, _ = atlas_run
    full = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json'))
    baseline = deepcopy(full)
    baseline['symbol_context'].pop(CONTEXT)
    # The later independently versioned context lane is not an old-lane baseline.
    baseline['symbol_context'].pop('returned_callback_parameter_context_calls', None)
    budget = context_budget_profile(json.dumps(baseline, ensure_ascii=False))['estimated_tokens']
    monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', budget)
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(target), format='json'))
    assert CONTEXT not in result['symbol_context']
    assert result['symbol_context'] == baseline['symbol_context']
    assert context_budget_profile(json.dumps(result, ensure_ascii=False), budget_tokens=budget)['status'] == 'pass'
    assert 'returned parameter' in result['next_action'].lower() and 'absence' in result['next_action']

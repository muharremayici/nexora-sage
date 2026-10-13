"""Finite contextual callback source transport; never execute target source."""
from copy import deepcopy
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tools.engines import generate_atlas as ga
from tools.core.state_flow import _focus_returned_callback_parameter_context as focus
from tools.tests.test_state_flow_returned_local_callback_contract import _native
from tools.tests.test_state_flow_returned_callback_parameter_optional_contract import CASES as OLD_CASES, POSITIVES as OLD_POSITIVES, record_for

ROOT = Path(__file__).resolve().parents[2]
KEY = 'returned_callback_parameter_context_call_evidence'
OLD_KEY = 'returned_callback_parameter_optional_call_evidence'
CONTEXT = 'returned_callback_parameter_context_calls'
LAYOUTS = [(kind, branch, awaited) for kind in ga.RETURNED_PARAMETER_CONTEXT_VARIANTS
    for branch in (('then', 'else') if kind.endswith('_if_block') else ('none',))
    for awaited in ((False,) if kind == 'immediate_own_try_block' else (False, True))]


def body_for(kind, branch, awaited):
    call = ('await ' if awaited else '') + 'onDone?.(data);'
    inner = call
    if branch != 'none': inner = 'if (flag) { ' + (call if branch == 'then' else '') + ' } else { ' + (call if branch == 'else' else '') + ' }'
    if kind == 'immediate_callback_if_block': control = inner
    elif 'catch' in kind: control = 'try { fail(); } catch (error) { ' + inner + ' }'
    elif 'finally' in kind: control = 'try { fail(); } finally { ' + inner + ' }'
    else: control = 'try { ' + inner + ' } catch (error) {}'
    return 'const cb = useCallback(async (options = {}) => { const { onDone } = options; ' + control + ' }, []); return { cb };'


PLAIN = body_for('immediate_own_catch_if_block', 'then', False)
CASES = {f'Layout{i}': body_for(*layout) for i,layout in enumerate(LAYOUTS)}
EXPECTED = {f'Layout{i}': layout for i,layout in enumerate(LAYOUTS)}
for name in OLD_POSITIVES:
    CASES['Guard_' + name] = re.sub(r'await ([^\s]+)\?\.\(([^)]*)\);', r'if (flag) { \1?.(\2); }', OLD_CASES[name])
    EXPECTED['Guard_' + name] = ('immediate_own_try_if_block', 'then', False)
for name,body in OLD_CASES.items():
    if name not in OLD_POSITIVES | {'unawaited', 'conditional', 'catch', 'finally'}:
        CASES['Negative_' + name] = (body if name in {'returned', 'outside_try'} else
            body.replace('await onDone?.(data);', 'if (flag) { onDone?.(data); }'))
for name, replacement in {
    'catch_shadow': 'catch (onDone)', 'catch_pattern_shadow': 'catch ({ onDone })',
}.items(): CASES[name] = PLAIN.replace('catch (error)', replacement)
for name,call in {
    'unbraced': 'if (flag) onDone?.(data);',
    'else_if': 'if (flag) {} else if (other) { onDone?.(data); }',
    'nested_if': 'if (flag) { if (other) { onDone?.(data); } }',
    'nested_try': 'try { onDone?.(data); } catch (e) {}',
    'loop': 'while (flag) { onDone?.(data); }',
    'switch': 'switch (flag) { case 1: onDone?.(data); }',
    'block': '{ onDone?.(data); }',
    'deferred': 'later(() => { onDone?.(data); });',
    'returned': 'return onDone?.(data);',
    'short_circuit': 'flag && onDone?.(data);',
    'ternary': 'flag ? onDone?.(data) : other;',
    'branch_shadow': 'if (flag) { const onDone = other; onDone?.(data); }',
    'branch_write': 'if (flag) { onDone = other; onDone?.(data); }',
    'finally_write': 'if (flag) { onDone?.(data); }',
}.items():
    CASES[name] = PLAIN.replace('if (flag) { onDone?.(data); } else {  }', call)
CASES['finally_write'] = CASES['finally_write'].replace('}, []);', 'finally { onDone = other; } }, []);')
CASES['catch_none'] = PLAIN.replace('catch (error)', 'catch')
CASES['catch_unrelated_pattern'] = PLAIN.replace('catch (error)', 'catch ({ message })')
CASES['throw_before'] = PLAIN.replace('if (flag)', 'throw error; if (flag)')
for name in ('catch_none','catch_unrelated_pattern','throw_before'): EXPECTED[name] = ('immediate_own_catch_if_block', 'then', False)
CASES['old_exclusive'] = OLD_CASES['plain']


@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    text = '\ufeff// \U0001f680 native coordinates\r\n' + '\r\n'.join(
        [f'export function Case_{name}() {{ {body} }}' for name,body in CASES.items()] + [
            "import { useCallback, useCallback as cache } from 'react';",
            "import { useCallback as foreign } from 'other';",
            "import type { useCallback as typeCallback } from 'react';",
            "import * as React from 'react';", "throw new Error('TARGET MUST NEVER EXECUTE');"])
    path = tmp_path_factory.mktemp('context-parameter') / 'Cases.tsx'
    path.write_bytes(text.encode('utf-8'))
    native = _native(path)
    rows = ga._normalize_polyglot_symbols(native, text, 'typescript')
    return text, {r['name']: r for r in native}, {r['name']: r for r in rows}, native


@pytest.mark.parametrize('name', CASES)
def test_native_finite_context_and_guards(inputs, name):
    text, raw, rows, _ = inputs
    lane = rows['Case_' + name][KEY]
    assert lane['runtime_execution'] == 'not_established'
    assert len(lane['records']) == int(name in EXPECTED), (name,lane)
    if name not in EXPECTED: return
    row = lane['records'][0]
    assert (row['context_kind'],row['branch_kind'],row['awaited']) == EXPECTED[name]
    assert set(row['spans']) == ga._returned_parameter_context_roles(row)
    assert 34 <= len(row['spans']) <= 40
    assert ('await_expression' in row['spans']) is row['awaited']
    assert ga._returned_parameter_anchor(row) == rows['Case_' + name]['returned_local_callback_evidence']['records'][0]
    data = text.encode('utf-8')
    snippets = {key: data[s['start']:s['end']].decode('utf-8') for key,s in row['spans'].items()}
    assert snippets['optional_operator'] == '?.'
    assert snippets['parameter_name'] == snippets['parameter_input'] == row['parameter_name']
    assert snippets['callback_local_binding'] == snippets['invoked_identifier'] == row['parameter_local_name']
    if row['branch_kind'] != 'none': assert snippets['branch_selector'] == ('if' if row['branch_kind'] == 'then' else 'else')
    assert raw['Case_' + name][KEY]['offset_unit'] == 'utf16_code_units'


def test_old_exclusive_and_single_coordinate_batch(inputs, monkeypatch):
    text, _, rows, native = inputs
    assert rows['Case_old_exclusive'][KEY]['records'] == []
    assert len(rows['Case_old_exclusive'][OLD_KEY]['records']) == 1
    assert rows['Case_Layout0'][OLD_KEY]['records'] == []
    calls, anchors = [], []
    original, original_anchor = ga._utf16_offsets_to_utf8_byte_offsets, ga._normalize_returned_local_callbacks
    def counted(*args,**kwargs): calls.append(1); return original(*args,**kwargs)
    def anchor(*args,**kwargs): anchors.append(1); return original_anchor(*args,**kwargs)
    monkeypatch.setattr(ga, '_utf16_offsets_to_utf8_byte_offsets', counted)
    monkeypatch.setattr(ga, '_normalize_returned_local_callbacks', anchor)
    normalized = ga._normalize_polyglot_symbols(native, text, 'typescript')
    assert len(calls) == 1
    assert len(anchors) == sum('returned_local_callback_evidence' in r for r in native)
    assert sum(len(r.get(KEY,{}).get('records',[])) for r in normalized) == len(EXPECTED)


@pytest.mark.parametrize('damage', ['hash','version','runtime','extra','missing_role','wrong_token','wrong_parameter','anchor','partial_anchor',
    'duplicate','parser','order','bool_count','branch_swap','missing_selector','nested_selector','await_flag','extra_await'])
def test_native_normalizer_corruption(inputs, damage):
    text, raw, _, native = inputs
    symbol = deepcopy(raw['Case_Layout13']) # catch/if then, plain
    lane, row = symbol[KEY], symbol[KEY]['records'][0]
    assert row['branch_kind'] == 'then' and row['awaited'] is False
    if damage == 'hash': lane['source_hash'] = '0'*64
    elif damage == 'version': lane['version'] = ga.RETURNED_PARAMETER_OPTIONAL_VERSION
    elif damage == 'runtime': lane['runtime_execution'] = 'proved'
    elif damage == 'extra': row['callability'] = 'proved'
    elif damage == 'missing_role': row['spans'].pop('argument')
    elif damage == 'wrong_token': row['spans']['optional_operator'] = row['spans']['argument']
    elif damage == 'wrong_parameter': row['parameter_local_name'] = 'wrong'
    elif damage == 'anchor': symbol['returned_local_callback_evidence']['records'] = []
    elif damage == 'partial_anchor': symbol['returned_local_callback_evidence'].update(status='incomplete_scan',omitted=1)
    elif damage == 'duplicate': lane['records'].append(deepcopy(row))
    elif damage == 'parser': symbol['parserKind'] = 'regex'
    elif damage == 'order': row['spans']['try_statement'] = row['spans']['binding_statement']
    elif damage == 'bool_count': row['unselected_literal_default_count'] = True
    elif damage == 'branch_swap': row['branch_kind'] = 'else'
    elif damage == 'missing_selector': row['spans'].pop('branch_selector')
    elif damage == 'nested_selector': row['spans']['branch_selector'] = row['spans']['if_condition']
    elif damage == 'await_flag': row['awaited'] = True
    elif damage == 'extra_await': row['spans']['await_expression'] = row['spans']['invocation']
    meta = next(r for r in native if r['name'] == '__file_meta__')
    result = ga._normalize_polyglot_symbols([meta,symbol],text,'typescript')[1][KEY]
    assert result['status'] == 'unavailable' and result['records'] == [], (damage,result)


@pytest.mark.parametrize('damage', ['none','legacy','ast','hash','parser','duplicate','anchor','budget','limit','extra','role','branch'])
def test_focus_boundaries(inputs, damage):
    record, row = record_for(inputs[0]), deepcopy(inputs[2]['Case_Layout13'])
    budget, limit = 500,5
    if damage == 'legacy': row.pop(KEY)
    elif damage == 'ast': record['ast_contract_version'] = 'old'
    elif damage == 'hash': record['hash'] = '0'*64
    elif damage == 'parser': record['parser_evidence']['status'] = 'unavailable'
    elif damage == 'duplicate': row[KEY]['records'] *= 2
    elif damage == 'anchor': row['returned_local_callback_evidence']['records'] = []
    elif damage == 'budget': budget = 3
    elif damage == 'limit': limit = 0
    elif damage == 'extra': row[KEY]['promoted'] = True
    elif damage == 'role': row[KEY]['records'][0]['spans'].pop('branch_selector')
    elif damage == 'branch': row[KEY]['records'][0]['branch_kind'] = 'else'
    result = focus(record,row,limit,budget)
    assert result['visited_records'] <= budget
    if damage in {'none','limit'}:
        assert result['status'] == 'observed' and result['returned'] == int(damage == 'none')
        assert result['omitted'] == int(damage == 'limit')
    else: assert result['status'] in {'unavailable','incomplete_scan'} and result['returned'] == 0
    for key in ('runtime_execution','value_transfer','callability','callback_activation','schema_validity'): assert result[key] == 'not_established'


@pytest.mark.parametrize('i', range(21))
def test_schema_exact_variant(inputs,tmp_path,i):
    from tools.core.artifact_validator import validate_against_schema
    schema = json.loads((ROOT / 'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    path = tmp_path / 'schema.json'
    path.write_text(json.dumps({'$defs':schema['$defs'],'$ref':'#/$defs/'+KEY}),encoding='utf-8')
    lane = deepcopy(inputs[2][f'Case_Layout{i}'][KEY])
    assert not validate_against_schema(path,'context',lane)
    for role in list(lane['records'][0]['spans']):
        broken = deepcopy(lane); broken['records'][0]['spans'].pop(role)
        assert validate_against_schema(path,'context',broken), role
    broken = deepcopy(lane); broken['records'][0]['spans']['invented'] = broken['records'][0]['spans']['owner']
    assert validate_against_schema(path,'context',broken)
    broken = deepcopy(lane); broken['records'][0]['awaited'] = not broken['records'][0]['awaited']
    assert validate_against_schema(path,'context',broken)


def test_context_cap_does_not_change_old_lane(tmp_path):
    source = "import { useCallback } from 'react'; export function owner() { " + OLD_CASES['plain'].replace(
        'fail(e);', 'onDone?.(data);'*65) + ' }'
    path = tmp_path / 'Caps.ts'; path.write_bytes(source.encode('utf-8'))
    row = next(r for r in ga._normalize_polyglot_symbols(_native(path),source,'typescript') if r['name']=='owner')
    assert row[KEY]['status']=='incomplete_scan' and len(row[KEY]['records'])==64 and row[KEY]['omitted']==1
    assert row[OLD_KEY]['status']=='observed' and len(row[OLD_KEY]['records'])==1
    assert focus(record_for(source),row,64,500)['returned']==0


def test_shared_native_caches_old_settled_first():
    script = r"""
const fs=require('fs'),path=require('path'),crypto=require('crypto'),vm=require('vm');
const ts=require('module').createRequire(path.resolve('tools/engines/ast_sequencer.cjs'))('typescript');
const source=fs.readFileSync('tools/engines/ast_sequencer.cjs','utf8');
const ctx={ts,path,crypto,counts:{program:0,writes:0}};vm.createContext(ctx);
for(const name of ['createFileSymbolResolver','createBindingWriteCollector','createReturnedLocalCallbackCollector','createReturnedCallbackParameterOptionalCollector']){
 let start=source.indexOf('function '+name+'('),end=source.indexOf('\nfunction ',start+1),body=source.slice(start,end);
 if(start<0||end<0)throw Error('missing function');
 if(name==='createFileSymbolResolver')body=body.replace('checker = ts.createProgram','counts.program++; checker = ts.createProgram');
 if(name==='createBindingWriteCollector')body=body.replace('const written = new Set();','counts.writes++; const written = new Set();');
 vm.runInContext(body,ctx);
}
const sf=ts.createSourceFile('Finite.ts',process.argv[1],ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
const owner=sf.statements.find(ts.isFunctionDeclaration),at=ctx.createFileSymbolResolver(sf),writes=ctx.createBindingWriteCollector(at);
const imports=new Map([['useCallback',{source:'react',localName:'useCallback',importedName:'useCallback',kind:'named',typeOnly:false}]]);
const anchors=ctx.createReturnedLocalCallbackCollector(sf,imports,at,writes),collect=ctx.createReturnedCallbackParameterOptionalCollector(sf,at,writes,anchors);
const old=collect(owner),before=JSON.stringify(old),lane=collect.contexts(owner);
for(let i=0;i<100;i++)if(collect(owner)!==old||collect.contexts(owner)!==lane||JSON.stringify(old)!==before)throw Error('cache/old drift');
process.stdout.write(JSON.stringify({counts:ctx.counts,old:old.records.length,context:lane.records.length}));
"""
    source="import { useCallback } from 'react'; export function owner(){ "+OLD_CASES['plain'].replace('fail(e);','if(flag){ onDone?.(data); }')+' }'
    run=subprocess.run([shutil.which('node'),'-e',script,source],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)=={'counts':{'program':1,'writes':1},'old':1,'context':1}


@pytest.fixture(scope='module')
def atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target=tmp_path_factory.mktemp('context-atlas')
    source="import { useCallback } from 'react'; export function owner(){ "+OLD_CASES['plain'].replace('fail(e);','if(flag){ onDone?.(data); onDone?.(other); }')+' }'
    (target/'Panel.ts').write_bytes(('\ufeff// \U0001f680\r\n'+source+"\r\nthrow new Error('TARGET MUST NEVER EXECUTE');\r\n").encode('utf-8'))
    env=dict(os.environ)
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT','CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256','CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE','CODEMAPS_TARGET_PROJECTS'): env.pop(key,None)
    env['CODEMAPS_TARGET_ROOT']=str(target)
    run=subprocess.run([sys.executable,'-B','-m','tools.engines.generate_atlas'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert run.returncode==0,run.stderr or run.stdout
    raw=ROOT/'output/external_targets'/external_target_output_slug(str(target))/'.raw'
    return target,raw,load_atlas_data(raw)


def test_actual_ingestion_mcp_and_item_cap(atlas_run):
    from tools.mcp import server
    target,raw,atlas=atlas_run
    row=next(r for r in atlas['MAIN']['files']['Panel.ts']['symbols'] if r['name']=='owner')
    assert len(row[KEY]['records'])==2 and len(row[OLD_KEY]['records'])==1
    assert server._atlas_read(raw).primary_verified
    result=json.loads(server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(target),format='json',max_items=1))
    lane=result['symbol_context'][CONTEXT]
    assert lane['status']=='observed' and lane['returned']==lane['omitted']==1
    assert lane['source_binding']=='snapshot_and_live_match'
    assert len(lane['items'][0]['spans'])==39
    assert result['symbol_context']['returned_callback_parameter_optional_calls']['returned']==1
    assert CONTEXT in server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(target))


@pytest.mark.parametrize('damage',['inline','partitioned','legacy','source_drift','corrupt_primary','missing_primary','poison_cache','scan_budget'])
def test_primary_guards(atlas_run,tmp_path,monkeypatch,damage):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    target,_,original=atlas_run
    local=tmp_path/'target';local.mkdir();(local/'Panel.ts').write_bytes((target/'Panel.ts').read_bytes())
    atlas=deepcopy(original);atlas['MAIN']['root_path']=str(local)
    if damage=='legacy':
        for row in atlas['MAIN']['files']['Panel.ts']['symbols']: row.pop(KEY,None)
    raw=tmp_path/'.raw';store=ArtifactStore(raw_dir=raw);store.use_sqlite,store.backend=True,'hybrid_sqlite';store._ensure_schema()
    store.state_payload_inline_limit_bytes=10**9 if damage=='inline' else 1
    store._save_payload_to_state_table('atlas',atlas);store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw/'codemaps.db') as connection:
        connection.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage=='corrupt_primary': connection.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
        if damage=='missing_primary': connection.execute("DELETE FROM state_payloads WHERE name='atlas'")
    (raw/'atlas.json').write_text(json.dumps(original),encoding='utf-8')
    if damage=='source_drift': (local/'Panel.ts').write_bytes((local/'Panel.ts').read_bytes()+b'\n// drift')
    if damage=='scan_budget':
        policy=dict(server._state_flow_brief_policy());policy['focus_scan_limit']=7
        monkeypatch.setattr(server,'_state_flow_brief_policy',lambda:policy)
    server._ATLAS_CACHE.clear();monkeypatch.setattr(server,'_raw_dir_for_target',lambda root:raw);monkeypatch.setattr(server,'_analysis_root_display',lambda root:str(local))
    if damage=='poison_cache':
        cached=server._atlas_read(raw).data
        next(r for r in cached['MAIN']['files']['Panel.ts']['symbols'] if r['name']=='owner')[KEY]['records'][0]['argument_name']='poison'
        server._ATLAS_CACHE[str(raw)]=cached
    result=json.loads(server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(local),format='json',max_items=1))
    lane=result.get('symbol_context',{}).get(CONTEXT)
    if damage in {'inline','partitioned','poison_cache'}:
        assert lane['status']=='observed' and lane['returned']==lane['omitted']==1 and lane['items'][0]['argument_name']=='data'
    elif damage=='legacy': assert lane is None
    else: assert lane is None or (lane['status'] in {'unavailable','incomplete_scan'} and lane['returned']==0)
    if damage=='scan_budget': assert result['visited_records']<=7


def test_last_lane_budget_preserves_every_old_lane(atlas_run,monkeypatch):
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    target,_,_=atlas_run
    full=json.loads(server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(target),format='json'))
    baseline=deepcopy(full);baseline['symbol_context'].pop(CONTEXT)
    budget=context_budget_profile(json.dumps(baseline,ensure_ascii=False))['estimated_tokens']
    monkeypatch.setattr(server,'DEFAULT_AGENT_PACKET_BUDGET_TOKENS',budget)
    result=json.loads(server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(target),format='json'))
    assert CONTEXT not in result['symbol_context']
    assert result['symbol_context']==baseline['symbol_context']
    assert context_budget_profile(json.dumps(result,ensure_ascii=False),budget_tokens=budget)['status']=='pass'
    assert 'context' in result['next_action'].lower() and 'absence' in result['next_action']

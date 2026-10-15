"""Finite unbraced callback source transport, never execute analyzed source."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tools.engines import generate_atlas as ga
from tools.core.state_flow import _focus_returned_callback_parameter_unbraced as focus
from tools.core.atlas_ast_contract import RETURNED_PARAMETER_UNBRACED_VARIANTS
from tools.tests.test_state_flow_returned_local_callback_contract import _native
from tools.tests.test_state_flow_returned_callback_parameter_optional_contract import CASES as ORIGINAL, POSITIVES, record_for
from tools.tests.test_state_flow_returned_callback_context_contract import _packet_before_context

ROOT = Path(__file__).resolve().parents[2]
KEY = 'returned_callback_parameter_unbraced_if_call_evidence'
CONTEXT = 'returned_callback_parameter_unbraced_if_calls'
OLD_KEY = 'returned_callback_parameter_optional_call_evidence'
BRACED = 'returned_callback_parameter_context_call_evidence'
LAYOUTS = [(kind, branch, awaited) for kind in RETURNED_PARAMETER_UNBRACED_VARIANTS
    for branch in ('then','else') for awaited in (False,True)]

def body_for(kind, branch, awaited):
    call = ('await ' if awaited else '') + 'onDone?.(data);'
    control = 'if(flag) ' + (call if branch=='then' else '{}')
    if branch=='else': control += ' /* owning else */ else ' + call
    if 'catch' in kind: control='try { fail(); } catch(e) { '+control+' }'
    elif 'finally' in kind: control='try { fail(); } finally { '+control+' }'
    elif 'try' in kind: control='try { '+control+' } catch(e) {}'
    return 'const cb = useCallback(async (options = {}) => { const { onDone } = options; '+control+' }, []); return { cb };'

PLAIN=body_for('immediate_own_catch_unbraced_if','then',False)
CASES={f'Layout{i}':body_for(*layout) for i,layout in enumerate(LAYOUTS)}
EXPECTED={f'Layout{i}':layout for i,layout in enumerate(LAYOUTS)}
# Exercise the original guards on the new syntax, not replay old source tests.
for name,body in ORIGINAL.items():
    if name in {'unawaited','conditional','catch','finally','outside_try','returned'}: continue
    body=re.sub(r'await ([^\s]+)\?\.\(([^)]*)\);',r'if(flag) await \1?.(\2);',body)
    CASES['Guard_'+name]=body
    if name in POSITIVES: EXPECTED['Guard_'+name]=('immediate_own_try_unbraced_if','then',True)
for name,control in {
    'braced':'if(flag) { onDone?.(data); }', 'nested_if':'if(flag) { if(other) onDone?.(data); }',
    'else_if':'if(flag) {} else if(other) onDone?.(data);',
    'chain':'if(flag) onDone?.(data); else if(other) onDone?.(other);',
    'nested_try':'try { if(flag) onDone?.(data); } catch(e) {}',
    'loop':'while(flag) { if(other) onDone?.(data); }', 'deferred':'later(() => { if(flag) onDone?.(data); });',
    'returned':'if(flag) return onDone?.(data);','wrapped':'if(flag) (onDone?.(data));',
    'standalone':'{ if(flag) onDone?.(data); }',
}.items(): CASES[name]=PLAIN.replace('if(flag) onDone?.(data);',control)
CASES['catch_shadow']=PLAIN.replace('catch(e)','catch(onDone)')
CASES['catch_pattern_shadow']=PLAIN.replace('catch(e)','catch({onDone})')
CASES['catch_none']=PLAIN.replace('catch(e)','catch')
CASES['catch_pattern_unrelated']=PLAIN.replace('catch(e)','catch({message})')
for name in ['catch_none','catch_pattern_unrelated']: EXPECTED[name]=('immediate_own_catch_unbraced_if','then',False)
CASES['mixed']=body_for('immediate_callback_unbraced_if','else',False).replace('if(flag) {}','if(flag) { await onDone?.(data); }')
EXPECTED['mixed']=('immediate_callback_unbraced_if','else',False)
CASES['old_only']=ORIGINAL['plain']

@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    text='\ufeff// \U0001f680 exact coordinates\r\n'+'\r\n'.join(
        [f'export function Case_{name}() {{ {body} }}' for name,body in CASES.items()]+[
            "import { useCallback, useCallback as cache } from 'react';",
            "import { useCallback as foreign } from 'other';", "import type { useCallback as typeCallback } from 'react';",
            "import * as React from 'react';", "throw new Error('SOURCE MUST NEVER EXECUTE');"])
    path=tmp_path_factory.mktemp('unbraced-source')/'Cases.tsx';path.write_bytes(text.encode('utf-8'))
    native=_native(path)
    assert next(r for r in native if r['name']=='__file_meta__')['parserDiagnosticCount']==0
    normalized=ga._normalize_polyglot_symbols(native,text,'typescript')
    return text,{r['name']:r for r in native},{r['name']:r for r in normalized},native

@pytest.mark.parametrize('name',CASES)
def test_native_unbraced_and_shared_guards(inputs,name):
    text,raw,rows,_=inputs;lane=rows['Case_'+name][KEY]
    assert lane['runtime_execution']=='not_established' and lane['coverage']=='finite_source_grammar_only'
    assert len(lane['records'])==int(name in EXPECTED),(name,lane)
    if name not in EXPECTED:return
    row=lane['records'][0]
    assert (row['context_kind'],row['branch_kind'],row['awaited'])==EXPECTED[name]
    assert set(row['spans'])==ga._returned_parameter_unbraced_roles(row)
    assert row['spans']['branch_statement']==row['spans']['invocation_statement']
    assert 'branch_body' not in row['spans']
    assert ga._returned_parameter_anchor(row)==rows['Case_'+name]['returned_local_callback_evidence']['records'][0]
    data=text.encode('utf-8');s=row['spans']
    assert data[s['branch_selector']['start']:s['branch_selector']['end']].decode()==('if' if row['branch_kind']=='then' else 'else')
    assert raw['Case_'+name][KEY]['offset_unit']=='utf16_code_units'

def test_one_coordinate_batch_and_mixed_preservation(inputs,monkeypatch):
    text,_,_,native=inputs;calls=[];original=ga._utf16_offsets_to_utf8_byte_offsets
    def counted(*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(ga,'_utf16_offsets_to_utf8_byte_offsets',counted)
    rows={r['name']:r for r in ga._normalize_polyglot_symbols(native,text,'typescript')}
    assert len(calls)==1
    assert len(rows['Case_mixed'][KEY]['records'])==len(rows['Case_mixed'][BRACED]['records'])==1
    assert len(rows['Case_old_only'][OLD_KEY]['records'])==1 and not rows['Case_old_only'][KEY]['records']

@pytest.mark.parametrize('damage',['version','hash','coverage','parser','runtime','extra','duplicate','anchor','missing_role',
    'brace_role','branch','await_flag','wrong_token','wrong_parameter','branch_span','order','bool_count','partial_anchor'])
def test_normalizer_refuses_corruption(inputs,damage):
    text,raw,_,native=inputs;symbol=deepcopy(raw['Case_Layout8']);lane=symbol[KEY];row=lane['records'][0]
    if damage=='version':lane['version']='old'
    elif damage=='hash':lane['source_hash']='0'*64
    elif damage=='coverage':lane['coverage']='complete'
    elif damage=='parser':symbol['parserKind']='regex'
    elif damage=='runtime':lane['runtime_execution']='proved'
    elif damage=='extra':row['activation']='proved'
    elif damage=='duplicate':lane['records']*=2
    elif damage=='anchor':symbol['returned_local_callback_evidence']['records']=[]
    elif damage=='partial_anchor':symbol['returned_local_callback_evidence'].update(status='incomplete_scan',omitted=1)
    elif damage=='missing_role':row['spans'].pop('branch_selector')
    elif damage=='brace_role':row['spans']['branch_body']=row['spans']['branch_statement']
    elif damage=='branch':row['branch_kind']='else'
    elif damage=='await_flag':row['awaited']=True
    elif damage=='wrong_token':row['spans']['optional_operator']=row['spans']['argument']
    elif damage=='wrong_parameter':row['parameter_local_name']='wrong'
    elif damage=='branch_span':row['spans']['branch_statement']=row['spans']['if_statement']
    elif damage=='order':row['spans']['try_statement']=row['spans']['binding_statement']
    elif damage=='bool_count':row['unselected_literal_default_count']=True
    meta=next(r for r in native if r['name']=='__file_meta__')
    result=ga._normalize_polyglot_symbols([meta,symbol],text,'typescript')[1][KEY]
    assert result['status']=='unavailable' and result['records']==[],(damage,result)

def test_braced_source_cannot_impersonate_unbraced(inputs):
    text,raw,_,native=inputs;symbol=deepcopy(raw['Case_braced'])
    row=deepcopy(symbol[BRACED]['records'][0]);row['context_kind']='immediate_own_catch_unbraced_if'
    row['spans'].pop('branch_body');row['spans']['branch_statement']=deepcopy(row['spans']['invocation_statement'])
    symbol[KEY]['records']=[row]
    meta=next(r for r in native if r['name']=='__file_meta__')
    result=ga._normalize_polyglot_symbols([meta,symbol],text,'typescript')[1][KEY]
    assert result['status']=='unavailable' and not result['records']

@pytest.mark.parametrize('damage',['none','legacy','hash','ast','anchor','parser','coverage','duplicate','budget','limit','role','extra'])
def test_focus_identity_and_budget(inputs,damage):
    text,_,rows,_=inputs;record,row=record_for(text),deepcopy(rows['Case_Layout8']);budget,limit=500,5
    if damage=='legacy':row.pop(KEY)
    elif damage=='hash':record['hash']='0'*64
    elif damage=='ast':record['ast_contract_version']='old'
    elif damage=='anchor':row['returned_local_callback_evidence']['records']=[]
    elif damage=='parser':record['parser_evidence']['status']='unavailable'
    elif damage=='coverage':row[KEY]['coverage']='complete'
    elif damage=='duplicate':row[KEY]['records']*=2
    elif damage=='budget':budget=3
    elif damage=='limit':limit=0
    elif damage=='role':row[KEY]['records'][0]['spans'].pop('branch_selector')
    elif damage=='extra':row[KEY]['promoted']=True
    result=focus(record,row,limit,budget)
    assert result['visited_records']<=budget and result['coverage']=='finite_source_grammar_only'
    if damage in {'none','limit'}:assert result['status']=='observed' and result['returned']==int(damage=='none')
    else:assert result['status'] in {'unavailable','incomplete_scan'} and not result['items']
    for key in ['runtime_execution','value_transfer','callability','callback_activation','schema_validity']:assert result[key]=='not_established'

@pytest.mark.parametrize('i',range(16))
def test_schema_exact_variants(inputs,tmp_path,i):
    from tools.core.artifact_validator import validate_against_schema
    schema=json.loads((ROOT/'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    path=tmp_path/'schema.json';path.write_text(json.dumps({'$defs':schema['$defs'],'$ref':'#/$defs/'+KEY}),encoding='utf-8')
    lane=deepcopy(inputs[2][f'Case_Layout{i}'][KEY]);assert not validate_against_schema(path,'unbraced',lane)
    for role in list(lane['records'][0]['spans']):
        broken=deepcopy(lane);broken['records'][0]['spans'].pop(role);assert validate_against_schema(path,'unbraced',broken),role
    for damage in ['extra_role','coverage','await','branch']:
        broken=deepcopy(lane)
        if damage=='extra_role':broken['records'][0]['spans']['branch_body']=broken['records'][0]['spans']['branch_statement']
        elif damage=='coverage':broken.pop('coverage')
        elif damage=='await':broken['records'][0]['awaited']=not broken['records'][0]['awaited']
        else:broken['records'][0]['branch_kind']='none'
        assert validate_against_schema(path,'unbraced',broken),damage

def test_record_cap_keeps_old_lanes(tmp_path):
    source="import { useCallback } from 'react'; export function owner(){ "+ORIGINAL['plain'].replace('fail(e);','if(flag) onDone?.(data);'*65)+' }'
    path=tmp_path/'Caps.ts';path.write_bytes(source.encode('utf-8'))
    row=next(r for r in ga._normalize_polyglot_symbols(_native(path),source,'typescript') if r['name']=='owner')
    assert row[KEY]['status']=='incomplete_scan' and len(row[KEY]['records'])==64 and row[KEY]['omitted']==1
    assert row[OLD_KEY]['status']=='observed' and len(row[OLD_KEY]['records'])==1
    assert not focus(record_for(source),row,64,500)['items']

@pytest.mark.parametrize('fault',[False,True])
def test_single_checker_write_and_isolated_new_failure(fault):
    script=r"""
const fs=require('fs'),path=require('path'),crypto=require('crypto'),vm=require('vm');
const ts=require('module').createRequire(path.resolve('tools/engines/ast_sequencer.cjs'))('typescript');
const source=fs.readFileSync('tools/engines/ast_sequencer.cjs','utf8'),ctx={ts,path,crypto,counts:{program:0,writes:0}};vm.createContext(ctx);
for(const name of ['createFileSymbolResolver','createBindingWriteCollector','createReturnedLocalCallbackCollector','createReturnedCallbackParameterOptionalCollector']){
 let start=source.indexOf('function '+name+'('),end=source.indexOf('\nfunction ',start+1),body=source.slice(start,end);
 if(start<0||end<0)throw Error('missing function');
 if(name==='createFileSymbolResolver')body=body.replace('checker = ts.createProgram','counts.program++; checker = ts.createProgram');
 if(name==='createBindingWriteCollector')body=body.replace('const written = new Set();','counts.writes++; const written = new Set();');
 if(process.argv[2]==='fault')body=body.replace("if (++visited > 10000) throw new Error('unbraced parameter scan cap');","if (++visited > 1) throw new Error('unbraced parameter scan cap');");
 vm.runInContext(body,ctx);
}
const sf=ts.createSourceFile('Finite.ts',process.argv[1],ts.ScriptTarget.Latest,true,ts.ScriptKind.TS),owner=sf.statements.find(ts.isFunctionDeclaration);
const at=ctx.createFileSymbolResolver(sf),writes=ctx.createBindingWriteCollector(at),imports=new Map([['useCallback',{source:'react',localName:'useCallback',importedName:'useCallback',kind:'named',typeOnly:false}]]);
const anchors=ctx.createReturnedLocalCallbackCollector(sf,imports,at,writes),collect=ctx.createReturnedCallbackParameterOptionalCollector(sf,at,writes,anchors);
const old=collect(owner),braced=collect.contexts(owner),before=JSON.stringify([old,braced]),lane=collect.unbraced(owner);
for(let i=0;i<100;i++)if(collect.unbraced(owner)!==lane||JSON.stringify([collect(owner),collect.contexts(owner)])!==before)throw Error('cache/old drift');
process.stdout.write(JSON.stringify({counts:ctx.counts,old:old.records.length,braced:braced.records.length,status:lane.status,new:lane.records.length}));
"""
    source="import { useCallback } from 'react'; export function owner(){ "+ORIGINAL['plain'].replace('fail(e);','if(flag){ onDone?.(data); } if(other) onDone?.(data);')+' }'
    run=subprocess.run([shutil.which('node'),'-e',script,source,'fault' if fault else 'normal'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)=={'counts':{'program':1,'writes':1},'old':1,'braced':1,'status':'unavailable' if fault else 'observed','new':0 if fault else 1}

@pytest.fixture(scope='module')
def atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target=tmp_path_factory.mktemp('unbraced-atlas')
    simple=body_for('immediate_callback_unbraced_if','then',False).replace('if(flag) onDone?.(data);','if(flag) onDone?.(data); if(other) await onDone?.(other);')
    mixed=ORIGINAL['plain'].replace('fail(e);','if(flag){ onDone?.(data); } if(other) onDone?.(data);')
    source="\ufeff// \U0001f680\r\nimport { useCallback } from 'react'; export function owner(){ "+simple+" }\r\nexport function mixed(){ "+mixed+" }\r\nthrow new Error('TARGET MUST NEVER EXECUTE');\r\n"
    (target/'Panel.ts').write_bytes(source.encode('utf-8'))
    env=dict(os.environ)
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT','CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256','CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE','CODEMAPS_TARGET_PROJECTS'):env.pop(key,None)
    env['CODEMAPS_TARGET_ROOT']=str(target)
    run=subprocess.run([sys.executable,'-B','-m','tools.engines.generate_atlas'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert run.returncode==0,run.stderr or run.stdout
    raw=ROOT/'output/external_targets'/external_target_output_slug(str(target))/'.raw'
    return target,raw,load_atlas_data(raw)

def test_real_atlas_mcp_item_cap(atlas_run):
    from tools.mcp import server
    target,raw,atlas=atlas_run;row=next(r for r in atlas['MAIN']['files']['Panel.ts']['symbols'] if r['name']=='owner')
    assert len(row[KEY]['records'])==2
    mixed=next(r for r in atlas['MAIN']['files']['Panel.ts']['symbols'] if r['name']=='mixed')
    assert len(mixed[KEY]['records'])==len(mixed[OLD_KEY]['records'])==len(mixed[BRACED]['records'])==1
    assert server._atlas_read(raw).primary_verified
    result=json.loads(server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(target),format='json',max_items=1))
    lane=result['symbol_context'][CONTEXT]
    assert lane['status']=='observed' and lane['returned']==lane['omitted']==1
    assert lane['coverage']=='finite_source_grammar_only' and lane['source_binding']=='snapshot_and_live_match'
    assert result['symbol_context']['returned_callback_parameter_optional_calls']['returned']==0
    assert result['symbol_context']['returned_callback_parameter_context_calls']['returned']==0
    assert CONTEXT in server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(target))

@pytest.mark.parametrize('damage',['inline','partitioned','legacy','source_drift','corrupt_primary','missing_primary','poison_cache','scan_budget'])
def test_verified_primary_and_source_guards(atlas_run,tmp_path,monkeypatch,damage):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    target,_,original=atlas_run;local=tmp_path/'target';local.mkdir();(local/'Panel.ts').write_bytes((target/'Panel.ts').read_bytes())
    atlas=deepcopy(original);atlas['MAIN']['root_path']=str(local)
    if damage=='legacy':
        for row in atlas['MAIN']['files']['Panel.ts']['symbols']:row.pop(KEY,None)
    raw=tmp_path/'.raw';store=ArtifactStore(raw_dir=raw);store.use_sqlite,store.backend=True,'hybrid_sqlite';store._ensure_schema()
    store.state_payload_inline_limit_bytes=10**9 if damage=='inline' else 1
    store._save_payload_to_state_table('atlas',atlas);store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw/'codemaps.db') as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage=='corrupt_primary':conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
        if damage=='missing_primary':conn.execute("DELETE FROM state_payloads WHERE name='atlas'")
    (raw/'atlas.json').write_text(json.dumps(original),encoding='utf-8')
    if damage=='source_drift':(local/'Panel.ts').write_bytes((local/'Panel.ts').read_bytes()+b'\n// drift')
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
    elif damage=='legacy':assert lane is None
    else:assert lane is None or (lane['status'] in {'unavailable','incomplete_scan'} and not lane['items'])
    if damage=='scan_budget':assert result['visited_records']<=7

def test_new_header_omission_preserves_old_packet(atlas_run,monkeypatch):
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    target,_,_=atlas_run
    full=json.loads(server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(target),format='json'))
    baseline=_packet_before_context(full,CONTEXT)
    budget=context_budget_profile(json.dumps(baseline,ensure_ascii=False))['estimated_tokens']
    monkeypatch.setattr(server,'DEFAULT_AGENT_PACKET_BUDGET_TOKENS',budget)
    result=json.loads(server.get_state_flow(file='Panel.ts',symbol='owner',target_root=str(target),format='json'))
    assert CONTEXT not in result['symbol_context']
    assert {k:result['symbol_context'][k] for k in baseline['symbol_context']}==baseline['symbol_context']
    assert context_budget_profile(json.dumps(result,ensure_ascii=False),budget_tokens=budget)['status']=='pass'
    assert 'absence' in result['next_action']


@pytest.mark.parametrize('target_path_length', ['short', 'long'])
@pytest.mark.parametrize('focus_key,label', [
    ('local_arrow_sibling_calls', 'sibling'),
    ('jsx_prop_callbacks', 'JSX'),
    ('returned_local_callbacks', 'returned callback'),
    ('call_result_property_bindings', 'binding'),
    ('returned_callback_parameter_optional_calls', 'returned parameter'),
    ('returned_callback_parameter_context_calls', 'context'),
    (CONTEXT, 'unbraced'),
])
def test_last_lane_keeps_prior_omission_meanings(atlas_run, tmp_path, monkeypatch,
                                               target_path_length, focus_key, label):
    from tools.core.artifact_store import ArtifactStore
    from tools.core.agent_packet_budget import context_budget_profile
    from tools.core.unmanaged_atomic_io import native_filesystem_path
    from tools.mcp import server
    target, _, original = atlas_run
    local = tmp_path.joinpath(*(['long-path-segment'] * 8 if target_path_length == 'long' else []), 'target')
    Path(native_filesystem_path(local)).mkdir(parents=True)
    Path(native_filesystem_path(local / 'Panel.ts')).write_bytes((target / 'Panel.ts').read_bytes())
    atlas = deepcopy(original)
    atlas['MAIN']['root_path'] = str(local)
    raw = tmp_path / '.raw'
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    store._save_payload_to_state_table('atlas', atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / 'codemaps.db') as connection:
        connection.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
    server._ATLAS_CACHE.clear()
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda root: raw)
    monkeypatch.setattr(server, '_analysis_root_display', lambda root: str(local))
    full = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(local), format='json'))
    assert full['source_binding'] == 'snapshot_and_live_match'
    assert full['symbol_context'][CONTEXT]['returned'] == 2
    baseline = _packet_before_context(full, focus_key)
    budget = context_budget_profile(json.dumps(baseline, ensure_ascii=False))['estimated_tokens']
    monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', budget)
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='owner', target_root=str(local), format='json'))
    assert focus_key not in result['symbol_context']
    assert result['symbol_context'] == baseline['symbol_context']
    assert label in result['next_action'] and 'unbraced' in result['next_action'].lower()
    assert 'absence' in result['next_action']
    assert context_budget_profile(json.dumps(result, ensure_ascii=False), budget_tokens=budget)['status'] == 'pass'

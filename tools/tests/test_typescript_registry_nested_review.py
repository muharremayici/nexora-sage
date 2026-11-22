"""Explicit selected map facts, not inherited/recursive target domain rules."""
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
from tools.core import typescript_registry_contract as guard
from tools.core.artifact_store import ArtifactStore
from tools.core.atlas_io import load_atlas_data
from tools.engines import generate_atlas as producer
from tools.tests.test_typescript_registry_review import _write

ROOT=Path(__file__).resolve().parents[2]
V2='v2-typescript-literal-path-key-id'
BASE="\ufeff// 🚀 source, not runtime\r\nexport const TABLE: Record<string, Row> = {top:{id:'different-root', child:{good:{id:'good'},bad:{id:'other'},separator:{id:'sep',disabled:true}}, sibling:{bad:{id:'foreign'}}}};\r\nthrow new Error('must not execute');"
PREFIX="const TABLE={top:{id:'top',child:"
CASES={'base':BASE,'empty':PREFIX+'{}}};','opaque':PREFIX+"{a:{id:read()},b:{}}}};",
    'spread':PREFIX+"{...foreign,a:{id:'a'}}}};",'computed':PREFIX+"{[key]:{id:'a'}}}};",
    'getter':PREFIX+"{get a(){return {id:'a'}}}}};",'duplicate':PREFIX+"{a:{id:'a'},'a':{id:'b'}}}};",
    'row_spread':PREFIX+"{a:{id:'a',...foreign}}}};",'row_duplicate':PREFIX+"{a:{id:'a',id:'b'}}}};",
    'row_getter':PREFIX+"{a:{get id(){return 'a'}}}}};",'row_wrapped':PREFIX+"{a:({id:'a'} as Row)}}};",
    'escaped':PREFIX+"{'\\u0061':{id:'a'}}}};",'escaped_id':PREFIX+"{a:{id:'\\u0061'}}}};",
    'proto':PREFIX+"{__proto__:{id:'a'}}}};",'array':PREFIX+"[{id:'a'}]}};",
    'scalar':PREFIX+"{a:'a'}}};",'alias':"const rows={a:{id:'a'}}; const TABLE={top:{child:rows}};",
    'depth':"const TABLE={a:{b:{c:{d:{item:{id:'item'}}}}}};",
    'one':"const TABLE={catalog:{a:{id:'a'}}};",
    'literal_keys':"const TABLE={'a.b':{'':{'öykü/1':{id:'other'}}}};",
    'unrelated':BASE.replace("sibling:{bad:{id:'foreign'}}","sibling:{bad:{get id(){return 'foreign'}}}"),
    'objects':PREFIX+'{'+','.join(f"k{i}:{{id:'k{i}'}}" for i in range(256))+'}}};',
    'fields':PREFIX+'{'+','.join(f"f{i}:'x'" for i in range(2050))+'}}};',
    'storage':PREFIX+'{'+','.join('k%d:{'%i+','.join("f%d:'%s'"%(j,'x'*128) for j in range(10))+'}' for i in range(80))+'}}};',
    'display':PREFIX+'{'+','.join(f"k{i}:{{id:'other'}}" for i in range(55))+'}}};'}


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    target=tmp_path_factory.mktemp('registry-member-native')
    paths=[]
    for name,text in CASES.items():
        path=target/(name+'.ts')
        _write(path,text.encode('utf-8'))
        paths.append(str(path))
    run=subprocess.run([shutil.which('node'),str(ROOT/'tools/engines/ast_sequencer.cjs'),'--batch-json',*paths],
        cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert run.returncode==0,run.stderr
    rows=json.loads(run.stdout)
    return {name:rows[str(target/(name+'.ts'))] for name in CASES}


def _normal(raw,text):
    context={}
    producer._normalize_polyglot_symbols(raw,text,'typescript',file_evidence_context=context)
    root=producer._literal_registry_evidence(raw,text,context)
    members=producer._literal_registry_member_evidence(raw,text,context,root_evidence=root)
    return root,members


def _declare(target,text,*,path=('top','child'),exceptions=(),version=V2):
    _write(target/'registry.ts',text.encode('utf-8'))
    source={'path':'registry.ts','sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
        'project':'MAIN','atlas_path':'registry.ts','symbol':'TABLE'}
    if version==V2: source['member_path']=list(path) if isinstance(path,tuple) else path
    contract={'version':version,'source':source,'id_field':'id','exceptions':list(exceptions)}
    _write(target/'contract.json',contract)
    return contract


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    target=tmp_path_factory.mktemp('registry-member-generated')
    _declare(target,BASE)
    env=dict(os.environ,CODEMAPS_TARGET_ROOT=str(target))
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT','CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256','CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE','CODEMAPS_TARGET_PROJECTS'):
        env.pop(key,None)
    run=subprocess.run([sys.executable,'-B','-m','tools.engines.generate_atlas'],cwd=ROOT,env=env,
        capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert run.returncode==0,run.stderr or run.stdout
    raw=adapter._target_raw_dir(target)
    atlas=load_atlas_data(raw)
    assert atlas['MAIN']['files']['registry.ts']['literal_registry_member_evidence']['status']=='observed'
    return target,raw,atlas


def _primary(tmp_path,monkeypatch,generated,native,*,case='base',path=('top','child'),damage=None,partitioned=False):
    _,_,original=generated
    target=tmp_path/'target'
    _declare(target,CASES[case],path=path)
    atlas=deepcopy(original)
    atlas['MAIN']['root_path']=str(target)
    info=atlas['MAIN']['files']['registry.ts']
    root,members=_normal(native[case],CASES[case])
    info.update(hash=producer._atlas_text_hash(CASES[case]),literal_registry_evidence=root,literal_registry_member_evidence=members)
    if damage=='missing': info.pop('literal_registry_member_evidence')
    elif damage=='ast': members['ast_contract_version']='old'
    elif damage=='policy': members['policy_sha256']='0'*64
    elif damage=='hidden': members['records'][-1]['fields'][0]['value']='forged'
    elif damage=='missing_row': members['records']=[r for r in members['records'] if r['member_path']!=['top','child','bad']];members['record_count']=len(members['records'])
    elif damage=='span': members['records'][0]['object_span']['start']+=1
    raw=tmp_path/'.raw'
    store=ArtifactStore(raw_dir=raw)
    store.use_sqlite,store.backend=True,'hybrid_sqlite'
    store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes=1
    store._save_payload_to_state_table('atlas',atlas)
    store._save_atlas_to_sqlite(atlas)
    _write(raw/'atlas.json',original)
    with sqlite3.connect(raw/'codemaps.db') as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage=='snapshot': conn.execute("UPDATE source_snapshots SET content=content || '//drift'")
        elif damage=='primary':
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
    if damage=='live': _write(target/'registry.ts',b'const TABLE={};')
    monkeypatch.setattr(adapter,'_target_raw_dir',lambda *a:raw)
    return target


def _run(target):
    return review.review_declarative_registry(target,'contract.json')


def test_actual_native_primary_snapshot_and_cli_are_path_scoped(generated,monkeypatch,capsys):
    from tools import inspect_target
    target,raw,atlas=generated
    before=(target/'registry.ts').read_bytes()
    value=_run(target)
    assert value['status']=='review_required',value
    assert value['counts']=={'matched':1,'mismatched':2,'excepted':0,'unresolved':0}
    assert value['source_member_path']==['top','child'] and value['coverage']=='all_rows_of_explicit_literal_member_path_only'
    assert all('pointer' not in r and before[r['source_span']['start']:r['source_span']['end']].decode('utf-8') in {"'other'","'sep'"} for r in value['rows'])
    assert value['runtime_proof']=='not_performed' and value['initializer_value_semantics']=='not_runtime_state'
    assert value['mutation_proposed'] is False and value['atlas_binding']=='verified_selected_file_and_snapshot'
    monkeypatch.setattr(sys,'argv',['inspect_target','--registry-contract','contract.json','--target-root',str(target)])
    monkeypatch.setattr(inspect_target,'write_inspection',lambda *a:pytest.fail('No target/artifact writer'))
    assert inspect_target.main()==1
    assert json.loads(capsys.readouterr().out)==value
    assert (target/'registry.ts').read_bytes()==before
    import jsonschema
    schema=json.loads((ROOT/'config/schemas/atlas.schema.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator({'$ref':'#/$defs/literal_registry_member_evidence','$defs':schema['$defs']}).validate(atlas['MAIN']['files']['registry.ts']['literal_registry_member_evidence'])


@pytest.mark.parametrize('path,case,expected',[(('top','child'),'base','review_required'),(('top','sibling'),'base','review_required'),
    (('a','b','c','d'),'depth','checked_within_declared_scope'),(('catalog',),'one','checked_within_declared_scope'),
    (('a.b',''),'literal_keys','review_required'),(('top','child'),'empty','empty_declared_registry'),
    (('top','child'),'opaque','incomplete_evidence'),(('top','child'),'escaped_id','incomplete_evidence'),(('top','child'),'unrelated','review_required')])
def test_only_explicit_path_rows_reach_shared_comparator(native,generated,tmp_path,monkeypatch,path,case,expected):
    target=_primary(tmp_path,monkeypatch,generated,native,case=case,path=path)
    value=_run(target)
    assert value['status']==expected,value
    assert value['source_member_path']==list(path)


@pytest.mark.parametrize('case',['spread','computed','getter','duplicate','row_spread','row_duplicate','row_getter','row_wrapped','escaped','proto','array','scalar','alias'])
def test_unsupported_or_missing_direct_object_link_never_becomes_clean(native,generated,tmp_path,monkeypatch,case):
    target=_primary(tmp_path,monkeypatch,generated,native,case=case)
    value=_run(target)
    assert value['status']=='unavailable' and value['rows']==[],value


@pytest.mark.parametrize('path',[[],None,'top.child',['top',1],['top','__proto__'],['a','b','c','d','item'],['top','*']])
def test_selector_refusal_does_not_discover_a_convenient_map(native,generated,tmp_path,monkeypatch,path):
    target=_primary(tmp_path,monkeypatch,generated,native,path=path)
    assert _run(target)['status']=='unavailable'


@pytest.mark.parametrize('damage',['missing','ast','policy','hidden','missing_row','span','snapshot','primary','live'])
@pytest.mark.parametrize('partitioned',[False,True])
def test_primary_and_path_completeness_refusals(native,generated,tmp_path,monkeypatch,damage,partitioned):
    target=_primary(tmp_path,monkeypatch,generated,native,damage=damage,partitioned=partitioned)
    value=_run(target)
    assert value['status']=='unavailable' and value['rows']==[],value


@pytest.mark.parametrize('case,reason',[('objects','member_object_budget_exceeded'),('fields','member_field_budget_exceeded'),('storage','member_storage_budget_exceeded')])
def test_member_capture_exhaustion_does_not_replace_v1_root_facts(native,case,reason):
    root,members=_normal(native[case],CASES[case])
    assert root['status']=='observed'
    raw=next(r for r in native[case] if r['name']=='__file_meta__')['literalRegistryMemberEvidence']
    assert raw['status']=='unavailable' and raw['reason']==reason and raw['records']==[]
    assert members['status']=='unavailable'


def test_v1_does_not_inherit_nested_policy_or_equality(native,generated,tmp_path,monkeypatch):
    target=_primary(tmp_path,monkeypatch,generated,native)
    _declare(target,BASE,version='v1-typescript-literal-key-id')
    value=_run(target)
    assert value['counts']=={'matched':0,'mismatched':1,'excepted':0,'unresolved':0}
    assert 'source_member_path' not in value and value['rows'][0]['observed_id']=='different-root'
    original=producer.registry_member_policy
    def unavailable(): raise ValueError('Optional policy unavailable')
    monkeypatch.setattr(producer,'registry_member_policy',unavailable)
    root,members=_normal(native['base'],BASE)
    assert root['status']=='observed' and members['status']=='unavailable'
    monkeypatch.setattr(producer,'registry_member_policy',original)


def test_one_coordinate_batch_covers_old_and_new_groups(native,monkeypatch):
    original,calls=producer._utf16_offsets_to_utf8_byte_offsets,[]
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(producer,'_utf16_offsets_to_utf8_byte_offsets',counted)
    root,members=_normal(native['base'],BASE)
    assert root['status']==members['status']=='observed' and len(calls)==1


@pytest.mark.parametrize('kind',['exact','stale','unselected'])
def test_exceptions_are_visible_and_belong_only_to_selected_map(native,generated,tmp_path,monkeypatch,kind):
    target=_primary(tmp_path,monkeypatch,generated,native)
    exception={'key':'separator','expected_id':'sep','reason':'Explicit target-owned disabled separator'}
    if kind=='stale': exception['expected_id']='changed'
    elif kind=='unselected': exception.update(key='top',expected_id='different-root')
    _declare(target,BASE,exceptions=[exception])
    value=_run(target)
    assert value['status']==('review_required' if kind=='exact' else 'unavailable'),value
    if kind=='exact': assert value['counts']['excepted']==1 and value['counts']['mismatched']==1


@pytest.mark.parametrize('damage',['parent_path','duplicate','hidden_field','omitted_field','count','role','literal','extra','root_name'])
def test_all_retained_object_facts_validated_before_selection(native,damage):
    raw=deepcopy(native['base'])
    lane=next(r for r in raw if r['name']=='__file_meta__')['literalRegistryMemberEvidence']
    record=lane['records'][-1]
    if damage=='parent_path': record['member_path'][0]='foreign'
    elif damage=='duplicate': lane['records'].append(deepcopy(record));lane['record_count']+=1
    elif damage=='hidden_field': record['fields'][0]['key']='foreign'
    elif damage=='omitted_field': record['fields'].clear()
    elif damage=='count': lane['record_count']=True
    elif damage=='role': record['object_span']['start']=False
    elif damage=='literal': record['fields'][0]['value']='forged-value'
    elif damage=='extra': lane['runtime_proof']='yes'
    else: record['root_name']='foreign'
    root,members=_normal(raw,BASE)
    assert root['status']=='observed' and members['status']=='unavailable'


@pytest.mark.parametrize('damage',['parser','diagnostic','hash','features'])
def test_member_normalizer_does_not_trust_a_detached_root_projection(native,damage):
    raw=deepcopy(native['base'])
    root,_members=_normal(raw,BASE)
    meta=next(r for r in raw if r['name']=='__file_meta__')
    if damage=='parser': meta['parserStatus']='degraded'
    elif damage=='diagnostic': meta['parserDiagnosticCount']=False
    elif damage=='hash': meta['features']=[f for f in meta['features'] if not f.startswith('Hash:')]
    else: meta['features']=None
    assert producer._literal_registry_member_evidence(raw,BASE,root_evidence=root)['status']=='unavailable'


def test_all_selected_rows_checked_before_display_cap(native,generated,tmp_path,monkeypatch):
    target=_primary(tmp_path,monkeypatch,generated,native,case='display')
    value=_run(target)
    assert value['counts']['mismatched']==55 and value['shown']==50 and value['display_omitted']==5,value
    assert value['source_member_path']==['top','child']
    assert len(json.dumps(value,ensure_ascii=False).encode('utf-8'))<=review._policy()['limits']['output_bytes']
    damaged=deepcopy(native)
    lane=next(r for r in damaged['display'] if r['name']=='__file_meta__')['literalRegistryMemberEvidence']
    lane['records'][-1]['fields'][0]['value']='forged'
    target=_primary(tmp_path/'damaged',monkeypatch,generated,damaged,case='display')
    assert _run(target)['status']=='unavailable'


def test_entire_primary_is_rechecked_after_nested_comparison(native,generated,tmp_path,monkeypatch):
    target=_primary(tmp_path,monkeypatch,generated,native)
    original,reads=adapter.load_atlas_file_metadata,[]
    def drift(*args):
        value=original(*args)
        reads.append(1)
        if len(reads)==2: value['literal_registry_member_evidence']['record_count']=0
        return value
    monkeypatch.setattr(adapter,'load_atlas_file_metadata',drift)
    assert _run(target)['reason']=='typescript_primary_or_snapshot_changed_during_review'


def test_cached_generation_keeps_both_complete_source_groups(generated):
    target,raw,before=generated
    env=dict(os.environ,CODEMAPS_TARGET_ROOT=str(target))
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT','CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256','CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE','CODEMAPS_TARGET_PROJECTS'):
        env.pop(key,None)
    result=subprocess.run([sys.executable,'-B','-m','tools.engines.generate_atlas'],cwd=ROOT,env=env,
        capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout or result.stderr
    assert load_atlas_data(raw)==before

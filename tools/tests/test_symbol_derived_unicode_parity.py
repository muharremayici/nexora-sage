"""Unicode path and project casing parity for recorded derived search syntax."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


def _file() -> dict:
    return {
        "hash": "unicode-derived",
        "language": "typescript",
        "loc": 10,
        "symbols": [
            {"name": "Worker", "type": "Class", "line": 1, "end_line": 5,
             "member_details": [{"name": "run", "kind": "method", "line": 2,
                                 "end_line": 2, "static": False}]},
            {"name": "useStore", "type": "Variable", "line": 1, "end_line": 5,
             "initializer_member_evidence": {
                 "status": "syntax_observed", "runtime_owner_binding": "not_established",
                 "lexical_binding": "unverified", "limitations": [],
                 "members": [{"name": "update", "kind": "method",
                              "attribution": "returned_object_candidate", "line": 2,
                              "end_line": 2, "zustand_setter_call_evidence": {
                                  "status": "observed", "factory_api": "vanilla_store",
                                  "calls": [{"parameter": "set", "line": 2, "end_line": 2,
                                             "optional": False}],
                                  "factory_form": "direct", "middleware_form": "none",
                                  "binding_scope": "single_file_lexical_store_factory_parameter",
                                  "runtime_execution": "not_established"}}]}},
        ],
        "direct_import_binding_evidence": {"status": "observed", "records": [{
            "localName": "Sentry", "importedName": "*", "source": "./lib",
            "kind": "namespace", "typeOnly": False, "line": 1, "endLine": 1,
            "bindingScope": "file_top_level_import_declaration",
        }]},
        "module_root_import_call_evidence": {
            "status": "observed", "binding_scope": "single_file_lexical_import",
            "callsite_scope": "module_root", "runtime_execution": "not_established",
            "omitted": 0, "calls": [{
                "localName": "Sentry", "member": "init", "source": "./lib",
                "importedName": "*", "kind": "namespace", "optional": False,
                "line": 4, "end_line": 4,
            }],
        },
    }


READERS = (
    server._find_class_method_search_matches,
    server._find_store_action_search_matches,
    server._find_direct_import_binding_search_matches,
    server._find_qualified_import_call_search_matches,
)


@pytest.fixture
def raw_dir(tmp_path):
    atlas = {
        "MAIN": {"root_path": ".", "files": {
            "src/\u00c9clair.ts": _file(),
            "src/Equipe.ts": _file(),
            "src/\u0130tem.ts": _file(),
            "src/\u0130tem.ts.backup": _file(),
            "src/\u212aernel.ts": _file(),
        }},
        "\u00c9QUIPE": {"root_path": "companion", "files": {
            "src/\u00c9clair.ts": _file(),
        }},
    }
    store = ArtifactStore(raw_dir=tmp_path)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    return tmp_path


@pytest.mark.parametrize("reader", READERS)
def test_derived_unicode_path_and_project_scope(reader, raw_dir):
    rows, coverage = reader("\u00e9clair", "MAIN", raw_dir)
    assert coverage["status"] == "available"
    assert [row["file"] for row in rows] == ["src/\u00c9clair.ts"]

    scoped, scoped_coverage = reader("\u00e9clair", "\u00e9quipe", raw_dir)
    assert scoped_coverage["status"] == "available"
    assert [(row["project"], row["file"]) for row in scoped] == [
        ("\u00c9QUIPE", "src/\u00c9clair.ts"),
    ]
    unrelated, _ = reader("\u00e9clair", "NONEXISTENT", raw_dir)
    assert unrelated == []


@pytest.mark.parametrize("reader", READERS)
def test_derived_unicode_exact_basename_survives_source_cap(
    reader, raw_dir, monkeypatch,
):
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    rows, coverage = reader("\u00e9clair.ts", "MAIN", raw_dir)
    assert coverage["status"] == "available"
    assert [row["file"] for row in rows] == ["src/\u00c9clair.ts"]
    assert rows[0]["search_truncated"] is False


@pytest.mark.parametrize("reader", READERS)
def test_derived_expanding_lower_and_kelvin_path_rank(reader, raw_dir, monkeypatch):
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    rows, coverage = reader("i\u0307tem.ts", "MAIN", raw_dir)
    assert coverage["status"] == "available"
    assert [(row["file"], row["match_score"]) for row in rows] == [
        ("src/\u0130tem.ts", 0),
    ]
    assert rows[0]["search_truncated"] is True
    kelvin, _ = reader("kernel", "MAIN", raw_dir)
    assert [row["file"] for row in kelvin] == ["src/\u212aernel.ts"]


UNICODE_NAMES = {
    'turkish': ('İşçi', 'çalış', 'İzleme', 'yerelÖğe'),
    'greek': ('Εργάτης', 'τρέξε', 'Πηγή', 'τοπικό'),
    'cjk': ('作業', '更新', '記録', '輸入'),
    'astral': ('𐐀Task', '𐐀run', '𐐀Log', '𐐀alias'),
    'combining': ('Cafe\u0301', 'go\u0301', 'Lib\u0301', 'alias\u0301'),
}


@pytest.fixture(scope='module')
def native_unicode(tmp_path_factory):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    from tools.core.polyglot_imports import extract_direct_import_binding_evidence, extract_module_root_import_call_evidence

    root=Path(__file__).resolve().parents[2]
    target=tmp_path_factory.mktemp('unicode-derived-native')
    sources={}
    for label,(parent,member,receiver,alias) in UNICODE_NAMES.items():
        sources[label]=f'''import {{ create as makeStore }} from "zustand";
import * as {receiver} from "./lib";
import {{ {member} as {alias} }} from "./lib";
export class {parent} {{ static {member}() {{ return 1; }} }}
export const depo{parent} = makeStore((set) => ({{ {member}: () => set({{ready:true}}) }}));
{receiver}.{member}();
'''
    sources['refusals']='''import * as İz from "./lib";
export class İşçi { çalış() {} 'tırnak'() {} [hesap]() {} #gizli() {} }
İz?.opsiyon(); İz["sabitÜye"](); const değişken="dinamik"; İz[değişken]();
'''
    paths=[]
    for label,text in sources.items():
        path=target/(label+'.ts')
        path.write_text(text,encoding='utf-8',newline='')
        paths.append(str(path))
    node=shutil.which('node')
    assert node,'Native TypeScript parser required'
    result=subprocess.run([node,str(root/'tools/engines/ast_sequencer.cjs'),'--batch-json',*paths],
        cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stderr
    parsed=json.loads(result.stdout)
    output={}
    for label,text in sources.items():
        raw=parsed[str(target/(label+'.ts'))]
        output[label]={'hash':hashlib.sha256(text.encode('utf-8')).hexdigest(),'language':'typescript',
            'loc':len(text.splitlines()),'symbols':_normalize_polyglot_symbols(raw,text,'typescript'),
            'direct_import_binding_evidence':extract_direct_import_binding_evidence(raw),
            'module_root_import_call_evidence':extract_module_root_import_call_evidence(raw)}
    return output


def _unicode_index(tmp_path,info):
    store=ArtifactStore(raw_dir=tmp_path)
    store.use_sqlite,store.backend=True,'hybrid_sqlite'
    store._ensure_schema()
    atlas={'MAIN':{'root_path':'.','files':{'source.ts':deepcopy(info)}}}
    store._save_atlas_to_sqlite(atlas)
    return store,atlas


def _unicode_expected(label,index):
    parent,member,receiver,alias=UNICODE_NAMES[label]
    return (f'{parent}.{member}',f'depo{parent}.{member}',alias,f'{receiver}.{member}')[index]


@pytest.mark.parametrize('label',UNICODE_NAMES)
@pytest.mark.parametrize('index',range(4))
def test_recorded_unicode_names_survive_native_index_and_public_search(native_unicode,tmp_path,monkeypatch,label,index):
    store,_=_unicode_index(tmp_path,native_unicode[label])
    expected=_unicode_expected(label,index)
    rows,coverage=READERS[index](expected,'MAIN',tmp_path)
    assert [r['name'] for r in rows]==[expected]
    assert coverage['status']=='available' and coverage['source_tree_complete'] is False
    monkeypatch.setattr(server,'_raw_dir_for_target',lambda _target:tmp_path)
    public=json.loads(server.search_symbols(expected,target_root=str(tmp_path),format='json'))
    kinds=('ClassMethod','StoreActionCandidate','ImportBindingCandidate','QualifiedImportCallCandidate')
    selected=[r for r in public if r['type']==kinds[index]]
    assert len(selected)==1 and selected[0]['name']==expected
    assert selected[0]['atlas_node']=='MAIN::source.ts' and selected[0]['line']>0
    assert all(r.get('runtime_execution','not_established')=='not_established' for r in selected)
    if index==1: assert selected[0]['runtime_owner_binding']=='not_established'
    if index!=2:
        assert server._find_symbol_matches(expected,'MAIN',tmp_path)==[]


def test_unicode_does_not_widen_quoted_computed_private_or_optional_grammar(native_unicode,tmp_path):
    _unicode_index(tmp_path,native_unicode['refusals'])
    methods,coverage=READERS[0]('','MAIN',tmp_path)
    assert [r['name'] for r in methods]==['İşçi.çalış']
    assert coverage['status']=='partial'
    for member in ('opsiyon','sabitÜye','dinamik'):
        assert READERS[3](member,'MAIN',tmp_path)[0]==[]


@pytest.mark.parametrize('index',range(4))
def test_unicode_projection_replacement_keeps_other_project_then_tombstones(native_unicode,tmp_path,monkeypatch,index):
    from tools.core import artifact_store
    store,atlas=_unicode_index(tmp_path,native_unicode['turkish'])
    atlas['OTHER']={'root_path':'companion','files':{'source.ts':deepcopy(native_unicode['turkish'])}}
    store._save_atlas_to_sqlite(atlas)
    old_name=_unicode_expected('turkish',index)
    atlas['MAIN']['files']['source.ts']=deepcopy(native_unicode['cjk'])
    monkeypatch.setattr(artifact_store,'_source_snapshot_projection_scope',lambda:{'MAIN':{'source.ts'}})
    assert store._save_atlas_to_sqlite(atlas)['atlas_relational_mode']=='scoped'
    assert READERS[index](old_name,'MAIN',tmp_path)[0]==[]
    assert [r['name'] for r in READERS[index](old_name,'OTHER',tmp_path)[0]]==[old_name]
    assert [r['name'] for r in READERS[index](_unicode_expected('cjk',index),'MAIN',tmp_path)[0]]==[_unicode_expected('cjk',index)]
    del atlas['MAIN']['files']['source.ts']
    store._save_atlas_to_sqlite(atlas)
    assert READERS[index](_unicode_expected('cjk',index),'MAIN',tmp_path)[0]==[]
    assert len(READERS[index](old_name,'OTHER',tmp_path)[0])==1


@pytest.mark.parametrize('value', ['İşçi','çalış','Εργάτης','更新','𐐀run','Cafe\u0301','$iş','a$b','_','Ｋ'])
def test_conservative_unicode_identifier_subset_accepts_without_rewriting(value):
    from tools.core.search_identifiers import derived_search_identifier_valid
    assert derived_search_identifier_valid(value)


@pytest.mark.parametrize('value', [None,1,True,'','1adı','adı.son','adı-son',"'adı'",'[adı]','#adı',
    '\\u0061','adı\n','ad\x00','🚀','\u0301ad','a\u200cb','a\u200db','a²'])
def test_conservative_unicode_identifier_subset_refuses_wider_or_malformed_spelling(value):
    from tools.core.search_identifiers import derived_search_identifier_valid
    assert not derived_search_identifier_valid(value)


@pytest.mark.parametrize('index',range(4))
@pytest.mark.parametrize('invalid',['çalış.bad','a\u200cb','a\u200db'])
def test_invalid_unicode_sibling_retains_valid_row_and_partial_coverage(native_unicode,tmp_path,index,invalid):
    info=deepcopy(native_unicode['turkish'])
    if index==0:
        owner=next(s for s in info['symbols'] if s['type']=='Class')
        bad={**owner['member_details'][0],'name':invalid}
        owner['member_details'].append(bad)
    elif index==1:
        owner=next(s for s in info['symbols'] if s['name']=='depoİşçi')
        members=owner['initializer_member_evidence']['members']
        members.append({**deepcopy(members[0]),'name':invalid})
    elif index==2:
        records=info['direct_import_binding_evidence']['records']
        records.append({**records[-1],'localName':invalid})
    else:
        calls=info['module_root_import_call_evidence']['calls']
        calls.append({**calls[0],'member':invalid})
    _unicode_index(tmp_path,info)
    expected=_unicode_expected('turkish',index)
    rows,coverage=READERS[index](expected,'MAIN',tmp_path)
    assert [r['name'] for r in rows]==[expected] and coverage['status']=='partial'
    assert READERS[index](invalid,'MAIN',tmp_path)[0]==[]


@pytest.mark.parametrize('names',[('Café','Cafe\u0301'),('K','Ｋ'),('straße','strasse')])
def test_unicode_spellings_are_not_normalized_or_casefolded(tmp_path,names):
    info=_file()
    original=info['symbols'][0]
    info['symbols']=[{**deepcopy(original),'name':name} for name in names]
    _unicode_index(tmp_path,info)
    rows,_=READERS[0]('run','MAIN',tmp_path)
    assert {r['declaring_symbol'] for r in rows}==set(names)
    for name in names:
        exact,_=READERS[0](name+'.run','MAIN',tmp_path)
        assert [r['declaring_symbol'] for r in exact]==[name]


def test_unicode_projection_contract_invalidates_old_file_and_binds_shared_owner(monkeypatch):
    import ast
    from tools.core import atlas_ast_contract as ast_owner, search_identifiers as owner
    from tools.engines import generate_atlas as producer
    info={'ast_contract_version':ast_owner.AST_CONTRACT_VERSION,'language':'typescript','symbols':[],
        'parser_evidence':{'status':'observed','reported_by_adapter':True,'parser_kind':'typescript_compiler_api'},
        'project_key':'MAIN','atlas_rel_path':'source.ts','workspace_rel':'source.ts','repo_relative_path':'source.ts',
        'target_ref':'MAIN::source.ts','module_root_import_call_evidence':{'callsite_scope':'module_root','calls':[],'omitted':0}}
    assert producer.file_contract_is_current(info)
    assert not producer.file_contract_is_current({**info,'ast_contract_version':'v18.65-explicit-registry-member-source'})
    before,native=producer.atlas_staging_producer_contract(),Path.read_bytes
    def changed(path):
        data=native(path)
        return data+b'\n# diagnostic changed shared contract' if path.resolve()==Path(owner.__file__).resolve() else data
    monkeypatch.setattr(Path,'read_bytes',changed)
    assert producer.atlas_staging_producer_contract()!=before
    assert not any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.walk(ast.parse(Path(owner.__file__).read_text(encoding='utf-8'))))

"""Command origin is source observation, never test or shell execution proof."""
from __future__ import annotations

import json
import hashlib
from copy import deepcopy

import pytest

from tools.core.agent_command_contracts import command_contract_for_agent
from tools.core.test_impact_profiles import command_for_test
from tools.mcp import server


def test_generic_package_command_prefix_does_not_attest_a_declared_script():
    command=command_for_test('src/load.test.ts')
    assert command=='pnpm test src/load.test.ts'
    contract=command_contract_for_agent(command)
    assert contract['proof_boundary']=='package_command_origin_unverified'
    assert 'unverified' in contract['agent_note'].lower()


def test_normalization_replaces_borrowed_command_source_claims():
    row={'file':'src/load.test.ts','project':'MAIN','atlas_node':'MAIN::src/load.test.ts',
         'run_command':'pnpm test src/load.test.ts','type':'Direct Static Import',
         'run_command_evidence':{'origin':'declared_package_script','execution':'passed'},
         'package_script_suggestion':{'status':'observed','command':'unsafe borrowed claim'}}
    result=server._normalize_test_impact_payload_for_agent({'impacted_tests':[row],
        'test_command_source_status':'verified','test_command_sources_shown':999,'test_command_sources_omitted':0})
    actual=result['impacted_tests'][0]
    assert actual['run_command_evidence']['origin']=='generic_language_template'
    assert actual['run_command_evidence']['execution']=='not_run_by_sage'
    assert 'package_script_suggestion' not in actual
    assert not any(key.startswith('test_command_source') for key in result)
    assert server._normalize_test_impact_payload_for_agent(result)==result
    assert row['run_command_evidence']['execution']=='passed'


def _manifest(root, directory='.', manager='pnpm@9.0.0', content=None):
    path=root/directory/'package.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=(content if content is not None else json.dumps({'packageManager':manager,
        'scripts':{'test':'DO_NOT_EXECUTE_THIS_SOURCE_DECLARATION'}})).encode('utf-8')
    path.write_bytes(raw)
    return path,raw


def _observe(root, project_root='.', candidate='src/unit.test.ts', atlas_path=None):
    from tools.core.test_command_source import TestCommandSourceObserver
    observer=TestCommandSourceObserver(str(root))
    evidence=observer.observe(project='MAIN',project_root=project_root,candidate_path=candidate,
        atlas_path=atlas_path or candidate)
    return observer,evidence


@pytest.mark.parametrize('manager',['npm','pnpm','yarn','bun'])
@pytest.mark.parametrize('directory',['.','packages/ışık'])
def test_declared_manager_script_and_exact_cwd_are_separate_from_candidate(tmp_path,manager,directory):
    path,raw=_manifest(tmp_path,directory,manager+'@9.0.0')
    candidate='src/unit.test.ts' if directory=='.' else directory+'/src/unit.test.ts'
    observer,evidence=_observe(tmp_path,directory,candidate,'src/unit.test.ts')
    assert observer.settle([evidence])==[evidence]
    assert evidence['status']=='observed_current_source'
    assert evidence['manifest_sha256']==hashlib.sha256(raw).hexdigest()
    assert evidence['manifest']==path.relative_to(tmp_path).as_posix()
    assert evidence['working_directory']==directory
    assert evidence['working_directory_base']=='analysis_root'
    assert evidence['package_manager']==manager
    assert evidence['command'].startswith(manager+' ')
    assert 'unit.test.ts' not in evidence['command']
    if directory!='.':
        assert directory not in evidence['command']
    assert evidence['file_argument_added'] is False and evidence['execution']=='not_run_by_sage'
    assert 'DO_NOT_EXECUTE' not in json.dumps(evidence)
    assert 'shell safety' in evidence['proof_boundary']


@pytest.mark.parametrize('content',[
    '{}','[]','not JSON','{"scripts":{"test":"ok"}}',
    '{"packageManager":"unknown@1","scripts":{"test":"ok"}}',
    '{"packageManager":"pnpm","scripts":{"test":"ok"}}',
    '{"packageManager":"pnpm@1","scripts":{"test":""}}',
    '{"packageManager":"pnpm@1","scripts":{"test":false}}',
    '{"packageManager":"pnpm@1","scripts":{"lint":"ok"}}',
    '{"packageManager":"pnpm@1","scripts":{"test":"one","test":"two"}}',
    '{"packageManager":"pnpm@1","scripts":{"test":"ok"},"other":NaN}',
])
def test_nearest_invalid_package_never_borrows_parent_or_default_manager(tmp_path,content):
    _manifest(tmp_path)
    _manifest(tmp_path,'nested',content=content)
    observer,evidence=_observe(tmp_path,candidate='nested/src/unit.test.ts')
    assert evidence['status']=='unavailable' and 'command' not in evidence
    assert 'package.json' not in observer.sources


@pytest.mark.parametrize('root,candidate,atlas',[
    ('.','../unit.test.ts','../unit.test.ts'),
    ('.','/outside/unit.test.ts','unit.test.ts'),
    ('.','src/../unit.test.ts','src/../unit.test.ts'),
    ('OTHER','MAIN/unit.test.ts','unit.test.ts'),
    ('../outside','unit.test.ts','unit.test.ts'),
    ('.','src/unit.test.ts\n','src/unit.test.ts\n'),
])
def test_unsafe_or_foreign_project_paths_do_not_read_a_manifest(tmp_path,root,candidate,atlas):
    observer,evidence=_observe(tmp_path,root,candidate,atlas)
    assert evidence['status']=='unavailable' and not observer.sources


def test_candidate_project_boundary_and_no_installation_fallback(tmp_path):
    _manifest(tmp_path)
    (tmp_path/'OTHER/src').mkdir(parents=True)
    observer,evidence=_observe(tmp_path,'OTHER','OTHER/src/unit.test.ts','src/unit.test.ts')
    assert evidence['reason']=='no_package_within_candidate_project'
    assert all(path.startswith('OTHER/') for path in observer.sources)


def test_manifest_drift_and_new_closer_manifest_withhold_the_observation(tmp_path):
    path,_=_manifest(tmp_path)
    observer,evidence=_observe(tmp_path)
    path.write_text('{}',encoding='utf-8')
    assert observer.settle([evidence])[0]['reason']=='package_source_changed_during_request'
    _manifest(tmp_path)
    observer,evidence=_observe(tmp_path)
    _manifest(tmp_path,'src',manager='yarn@1')
    assert observer.settle([evidence])[0]['reason']=='package_source_changed_during_request'


def test_same_request_reads_distinct_sources_once_and_rechecks_independently(tmp_path,monkeypatch):
    from tools.core import test_command_source as source
    _manifest(tmp_path)
    original=source._read_confined
    calls=[]
    def capture(root,relative,limit):
        calls.append(relative)
        return original(root,relative,limit)
    monkeypatch.setattr(source,'_read_confined',capture)
    observer,first=_observe(tmp_path)
    second=observer.observe(project='MAIN',project_root='.',candidate_path='src/second.test.ts',atlas_path='src/second.test.ts')
    assert calls==['package.json']
    assert observer.settle([first,second])==[first,second]
    assert calls==['package.json','package.json']


@pytest.mark.parametrize('limit,value',[
    ('max_manifest_bytes',8),('max_ancestor_probes',1),('max_json_nodes',1),('max_json_depth',1)])
def test_source_and_structure_budgets_refuse_without_parent_rescue(tmp_path,monkeypatch,limit,value):
    from tools.core import test_command_source as source
    profile=deepcopy(source.load_test_impact_profiles())
    profile['command_source'][limit]=value
    monkeypatch.setattr(source,'load_test_impact_profiles',lambda:profile)
    _manifest(tmp_path)
    observer,evidence=_observe(tmp_path)
    assert evidence['status']=='unavailable' and 'command' not in evidence


def test_bom_source_hash_is_raw_bytes_and_no_shell_path_interpolation(tmp_path):
    directory='pkg$(untrusted)'
    path,raw=_manifest(tmp_path,directory)
    raw=b'\xef\xbb\xbf'+raw+b'\r\n'
    path.write_bytes(raw)
    observer,evidence=_observe(tmp_path,directory,directory+'/src/unit.test.ts','src/unit.test.ts')
    assert evidence['manifest_sha256']==hashlib.sha256(raw).hexdigest()
    assert evidence['working_directory']==directory and '$(' not in evidence['command']
    assert observer.settle([evidence])==[evidence]


def test_relative_analysis_root_and_other_language_never_borrow_current_process(tmp_path,monkeypatch):
    from tools.core.test_command_source import TestCommandSourceObserver
    observer=TestCommandSourceObserver('.')
    assert observer.observe(project='MAIN',project_root='.',candidate_path='unit.test.ts',atlas_path='unit.test.ts')['status']=='unavailable'
    observer=TestCommandSourceObserver(str(tmp_path))
    monkeypatch.setattr(observer,'_read',lambda *_:pytest.fail('wrong language source read'))
    assert observer.observe(project='MAIN',project_root='.',candidate_path='test_unit.py',atlas_path='test_unit.py')['reason']=='package_test_script_not_applicable_to_language'


@pytest.mark.parametrize('external',[False,True])
def test_public_machine_and_brief_preserve_real_sqlite_candidates_and_script_source(tmp_path,monkeypatch,external):
    from tools.tests.test_target_test_impact_evidence_boundary import _canonical_static_parity_sample
    _manifest(tmp_path,'MAIN','pnpm@9.0.0')
    _manifest(tmp_path,'OTHER','yarn@1.0.0')
    payload,brief=_canonical_static_parity_sample(tmp_path,monkeypatch,external)
    assert len(payload['impacted_tests'])==3
    for row in payload['impacted_tests']:
        evidence=row['package_script_suggestion']
        assert evidence['status']=='observed_current_source'
        assert evidence['project']==row['project'] and evidence['working_directory']==row['project']
        assert row['run_command_evidence']['origin']=='generic_language_template'
        assert row['file'] in row['run_command'] and row['file'] not in evidence['command']
        assert evidence['execution']=='not_run_by_sage' and row['candidate_evidence']['test_execution']=='not_run_by_sage'
    assert 'Command source (not executed)' in brief
    assert 'package_command_origin_unverified' in brief
    assert 'target_repository_script_evidence' not in brief
    before=deepcopy(payload)
    server._render_test_impact_brief(payload)
    assert payload==before


@pytest.mark.parametrize('mutation',['root_missing','foreign_workspace','foreign_node','foreign_project','relative_analysis_root'])
def test_source_attachment_refuses_ambiguous_candidate_metadata(tmp_path,mutation):
    _manifest(tmp_path)
    row={'file':'src/unit.test.ts','project':'MAIN','atlas_node':'MAIN::src/unit.test.ts'}
    atlas={'MAIN':{'root_path':'.','files':{'src/unit.test.ts':{'workspace_rel':'src/unit.test.ts'}}}}
    payload={'analysis_root':str(tmp_path),'impacted_tests':[row]}
    if mutation=='root_missing': atlas['MAIN'].pop('root_path')
    if mutation=='foreign_workspace': atlas['MAIN']['files']['src/unit.test.ts']['workspace_rel']='OTHER/src/unit.test.ts'
    if mutation=='foreign_node': row['atlas_node']='OTHER::src/unit.test.ts'
    if mutation=='foreign_project': row['project']='OTHER'
    if mutation=='relative_analysis_root': payload['analysis_root']='.'
    actual=server._attach_test_command_sources(payload,atlas)
    assert actual['impacted_tests'][0]['package_script_suggestion']['status']=='unavailable'
    assert payload=={'analysis_root':payload['analysis_root'],'impacted_tests':[row]}


def test_new_command_detail_never_evicts_the_existing_brief_rows(tmp_path,monkeypatch):
    from tools.tests.test_target_test_impact_evidence_boundary import _canonical_static_parity_sample
    payload,_=_canonical_static_parity_sample(tmp_path,monkeypatch,True)
    base=server._render_test_impact_brief(payload)
    for row in payload['impacted_tests']:
        row['package_script_suggestion']={'status':'observed_current_source','command':'X'*10000}
    expanded=server._render_test_impact_brief(payload)
    assert expanded.startswith(base.split('\nCommand source')[0].split('\nAdditional command-source')[0])
    assert 'X'*10000 not in expanded
    assert server.context_budget_profile(expanded,budget_tokens=server.BOUNDED_AGENT_PACKET_TOKENS)['status']=='pass'


@pytest.mark.parametrize('boundary',[
    {'contained':False,'link_path':False}, {'contained':True,'link_path':True}])
def test_missing_probe_obeys_existing_central_link_boundary_before_parent(tmp_path,monkeypatch,boundary):
    from tools.core import test_command_source as source
    _manifest(tmp_path)
    monkeypatch.setattr(source,'classify_target_path',lambda *_:boundary)
    monkeypatch.setattr(source,'_read_confined',lambda *_:pytest.fail('unsafe probe read'))
    observer,evidence=_observe(tmp_path)
    assert evidence['status']=='unavailable' and not observer.sources


def test_actual_hardlinked_manifest_is_not_accepted_as_source(tmp_path):
    import os
    path,_=_manifest(tmp_path)
    os.link(path,tmp_path/'duplicate.json')
    observer,evidence=_observe(tmp_path)
    assert evidence['status']=='unavailable' and 'command' not in evidence


def test_policy_drift_invalidates_current_source_suggestion(tmp_path,monkeypatch):
    from tools.core import test_command_source as source
    _manifest(tmp_path)
    observer,evidence=_observe(tmp_path)
    assert evidence['status']=='observed_current_source'
    profile=deepcopy(source.load_test_impact_profiles())
    profile['command_source']['max_candidates']+=1
    monkeypatch.setattr(source,'load_test_impact_profiles',lambda:profile)
    assert observer.settle([evidence])[0]['status']=='unavailable'


@pytest.mark.parametrize('mutation',['missing','bad_version','boolean_limit','unknown_builder'])
def test_invalid_or_unsupported_policy_does_not_create_a_command(tmp_path,monkeypatch,mutation):
    from tools.core import test_command_source as source
    profile=deepcopy(source.load_test_impact_profiles())
    if mutation=='missing': profile.pop('command_source')
    if mutation=='bad_version': profile['command_source']['version']='future'
    if mutation=='boolean_limit': profile['command_source']['max_candidates']=True
    if mutation=='unknown_builder': profile['command_source']['package_managers'].append('custom')
    monkeypatch.setattr(source,'load_test_impact_profiles',lambda:profile)
    if mutation=='unknown_builder':
        _manifest(tmp_path,manager='custom@1')
        _,evidence=_observe(tmp_path)
        assert evidence['reason']=='package_command_builder_unsupported_manager'
    else:
        with pytest.raises(ValueError,match='test_command_source_policy_unavailable'):
            _observe(tmp_path)


def test_unavailable_new_policy_preserves_older_test_candidates(tmp_path,monkeypatch):
    from tools.core import test_command_source as source
    profile=deepcopy(source.load_test_impact_profiles())
    profile.pop('command_source')
    monkeypatch.setattr(source,'load_test_impact_profiles',lambda:profile)
    payload={'analysis_root':str(tmp_path),'impacted_tests':[{'file':'src/unit.test.ts','project':'MAIN'}]}
    actual=server._attach_test_command_sources(payload,{})
    assert actual['impacted_tests']==payload['impacted_tests']
    assert actual['test_command_source_status']=='unavailable_policy_or_root'


def test_attachment_candidate_cap_keeps_other_rows_and_honest_omissions(tmp_path):
    from tools.core.test_command_source import command_source_policy
    limit=command_source_policy()['max_candidates']
    _manifest(tmp_path)
    rows=[{'file':f'src/unit{i}.test.ts','repo_relative_path':f'src/unit{i}.test.ts','project':'MAIN',
           'atlas_node':f'MAIN::src/unit{i}.test.ts'} for i in range(limit+3)]
    atlas={'MAIN':{'root_path':'.','files':{r['file']:{'workspace_rel':r['file']} for r in rows}}}
    actual=server._attach_test_command_sources({'analysis_root':str(tmp_path),'impacted_tests':rows},atlas)
    assert len(actual['impacted_tests'])==len(rows)
    assert actual['test_command_sources_shown']==limit and actual['test_command_sources_omitted']==3
    assert all(r['package_script_suggestion']['status']=='observed_current_source' for r in actual['impacted_tests'][:limit])
    assert all('package_script_suggestion' not in r for r in actual['impacted_tests'][limit:])
    assert all('package_script_suggestion' not in r for r in rows)


def test_command_prefix_classification_sibling_never_attests_manager_or_script():
    for command in ['npm run test','pnpm test one.ts','yarn run test','bun run test']:
        assert command_contract_for_agent(command)['proof_boundary']=='package_command_origin_unverified'


def test_long_path_and_invalid_utf8_manifest_are_explicitly_unavailable(tmp_path):
    path,_=_manifest(tmp_path)
    path.write_bytes(b'\xff')
    _,evidence=_observe(tmp_path)
    assert evidence['status']=='unavailable'
    _,evidence=_observe(tmp_path,candidate='x'*5000+'.test.ts')
    assert evidence['reason']=='candidate_path_over_budget_or_invalid'

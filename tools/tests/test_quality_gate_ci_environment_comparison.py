import io
import json
import zipfile

import pytest

from tools import compare_quality_gate_ci_environment as comparison
from tools import capture_quality_gate_ci_environment as observation
from tools.core.quality_gate_ci import ROOT, build_pr_matrix_receipt, load_ci_execution_policy, validate_workflow_text
from tools.tests.test_quality_gate_ci_environment import collect, _env


def _rehash(payload):
    payload['observation_sha256'] = observation._digest({k:v for k,v in payload.items() if k != 'observation_sha256'})


@pytest.fixture
def evidence(collect):
    pr = {phase: collect(phase=phase) for phase in ('before_suite', 'after_suite')}
    env = {**_env(), 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_RUN_ID': '99', 'GITHUB_JOB': 'post-merge-reference'}
    current = {phase: collect(environ=env, role='reference', phase=phase)
        for phase in ('before_suite', 'after_suite')}
    source = build_pr_matrix_receipt(pull_request_number=1, pull_request_head_sha='d'*40,
        event_sha='a'*40, source_tree_sha='c'*40, repository='example/sage',
        workflow_run_id=42, workflow_run_attempt=2)['source']
    return pr, current, source, env


def _compare(data):
    pr, current, source, env = data
    binding = observation._binding(env, 'reference', load_ci_execution_policy())
    binding['source_tree_sha'] = 'c'*40
    return comparison.compare_pairs(pr, current, pr_source=source,
        reference_binding=binding, policy=load_ci_execution_policy())


def test_matching_metadata_is_not_full_equivalence_or_reuse_authority(evidence):
    result = _compare(evidence)
    assert result['status'] == 'MATCHING_CAPTURED_METADATA'
    assert result['reuse_authorized'] is False
    assert result['environment_equivalence'] == 'not_proven'
    assert result['provenance_status'] == 'supplied_context_bindings_checked_only'
    assert result['difference_count'] == 0


@pytest.mark.parametrize('section,key,value', [
    ('python','version','3.12.9'), ('node','node','20.20.0'),
    ('runner','ImageVersion','new-image'), ('node','typescript','5.9.4')])
def test_before_after_and_cross_job_drift_is_visible(evidence, section, key, value):
    evidence[1]['after_suite'][section][key] = value
    _rehash(evidence[1]['after_suite'])
    result = _compare(evidence)
    assert result['status'] == 'DIFFERENT_CAPTURED_METADATA'
    assert result['difference_count'] > 0 and result['reuse_authorized'] is False


@pytest.mark.parametrize('change', ['missing_key','unknown','digest','wrong_run','wrong_attempt',
    'wrong_repository','wrong_tree','wrong_event','wrong_job','wrong_phase','empty_packages',
    'duplicate_packages','missing_input','extra_input','missing_node','false_complete','reuse_true'])
def test_invalid_incomplete_or_wrong_provenance_is_not_equal(evidence, change):
    payload = evidence[0]['before_suite']
    if change == 'missing_key': del payload['python']['soabi']
    elif change == 'unknown': payload['unknowns'] = ['runner.ImageVersion']
    elif change == 'digest': payload['observation_sha256'] = '0'*64
    elif change.startswith('wrong_') and change not in ('wrong_phase',):
        field, value = {'wrong_run':('workflow_run_id',123), 'wrong_attempt':('workflow_run_attempt',3),
            'wrong_repository':('repository','other/repo'), 'wrong_tree':('source_tree_sha','e'*40),
            'wrong_event':('event_name','push'), 'wrong_job':('job','pr-matrix-receipt')}[change]
        payload['binding'][field] = value
    elif change == 'wrong_phase': payload['phase'] = 'after_suite'
    elif change == 'empty_packages': payload['python']['installed_distributions'] = []
    elif change == 'duplicate_packages': payload['python']['installed_distributions'] *= 2
    elif change == 'missing_input': payload['input_sha256'].pop('requirements.txt')
    elif change == 'extra_input': payload['input_sha256']['private.txt'] = 'e'*64
    elif change == 'missing_node': payload['node'] = None
    elif change == 'false_complete': payload['status'] = 'INCOMPLETE_METADATA_ONLY'
    elif change == 'reuse_true': payload['reuse_authorized'] = True
    if change != 'digest': _rehash(payload)
    with pytest.raises(ValueError): _compare(evidence)


def _archive(pair, *, extra=False, duplicate=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as bundle:
        for phase, name in [('before_suite','before.json'), ('after_suite','after.json')]:
            bundle.writestr(name, json.dumps(pair[phase]))
        if extra: bundle.writestr('../ignored.json','{}')
        if duplicate: bundle.writestr('nested/before.json', '{}')
    return buffer.getvalue()


@pytest.mark.parametrize('extra,duplicate', [(True,False),(False,True)])
def test_archive_requires_exact_two_root_observations(evidence, extra, duplicate):
    with pytest.raises(ValueError): comparison.pair_from_archive(_archive(evidence[0], extra=extra, duplicate=duplicate))


def test_archive_parses_only_bounded_valid_pair(evidence):
    assert comparison.pair_from_archive(_archive(evidence[0])) == evidence[0]


@pytest.mark.parametrize('error',[RuntimeError,NotImplementedError],ids=['encrypted','unsupported-compression'])
def test_unreadable_archive_member_is_normalized_to_unavailable_evidence(evidence,monkeypatch,error):
    data=_archive(evidence[0])
    def unreadable(*_a,**_kw): raise error('sensitive runtime detail')
    monkeypatch.setattr(zipfile.ZipFile,'read',unreadable)
    with pytest.raises(ValueError,match='unsupported'): comparison.pair_from_archive(data)


@pytest.mark.parametrize('data', [b'{"phase":1,"phase":2}', b'[]', b'{'*270000,
    b'{"nested":'*1200+b'0'+b'}'*1200], ids=['duplicate-key','non-object','oversized','excessive-depth'])
def test_duplicate_oversized_or_nested_json_is_not_admitted(data):
    with pytest.raises(ValueError): comparison._json_object(data)


def _fetcher(evidence, *, artifact_change=None):
    pr, _current, source, _env_value = evidence
    receipt = build_pr_matrix_receipt(**{k:source[k] for k in ('pull_request_number',
        'pull_request_head_sha','event_sha','source_tree_sha','repository','workflow_run_id','workflow_run_attempt')})
    memory = io.BytesIO()
    with zipfile.ZipFile(memory,'w') as bundle:
        bundle.writestr('quality-gate-pr-matrix-receipt.json', json.dumps(receipt))
    artifact = {'id':32,'name':'nexora-sage-ci-environment-matrix-3.12-2','expired':False,
        'archive_download_url':'https://api.github.com/repos/example/sage/actions/artifacts/32/zip'}
    artifacts = [dict(artifact, id=31, name='nexora-sage-pr-matrix-receipt',
        archive_download_url='https://api.github.com/repos/example/sage/actions/artifacts/31/zip'), artifact]
    if artifact_change == 'duplicate': artifacts.append(dict(artifact,id=33,expired=True))
    if artifact_change == 'expired': artifact['expired'] = True
    if artifact_change == 'missing': artifacts.pop()
    if artifact_change == 'wrong_url': artifact['archive_download_url']='https://other.example/secret'
    calls=[]
    def fetch(url, **_kw):
        calls.append(url)
        if '/commits/' in url and '/pulls?' in url:
            return [{'number':1,'merged_at':'2026-10-16','merge_commit_sha':'a'*40,
                'base':{'ref':'main'}, 'head':{'sha':'d'*40}}]
        if '/runs?' in url:
            return {'workflow_runs':[{'id':42,'run_attempt':2,'event':'pull_request','status':'completed',
                'conclusion':'success','head_sha':'d'*40,'path':'.github/workflows/quality-gate.yml',
                'repository':{'full_name':'example/sage'}}]}
        if '/artifacts?' in url: return {'total_count':len(artifacts),'artifacts':artifacts}
        if '/git/commits/' in url: return {'sha':'a'*40,'tree':{'sha':'c'*40}}
        raise AssertionError(url)
    def archive(url, **_kw):
        calls.append(url)
        return memory.getvalue() if '/31/' in url else _archive(pr)
    return fetch, archive, calls


def test_real_existing_verifier_chain_is_used_before_comparison(evidence):
    fetch, archive, calls = _fetcher(evidence)
    result = comparison.compare_current_reference(evidence[1], environ=evidence[3],
        fetch_json=fetch, fetch_archive=archive)
    assert result['status'] == 'MATCHING_CAPTURED_METADATA'
    assert result['provenance_status'] == 'verified_pr_receipt_and_artifact_current_reference_binding'
    assert any('/git/commits/' in call for call in calls)
    assert any('/32/zip' in call for call in calls)
    assert result['reuse_authorized'] is False


@pytest.mark.parametrize('change',['duplicate','expired','missing','wrong_url'])
def test_missing_ambiguous_expired_or_untrusted_artifact_is_unavailable(evidence, change):
    fetch, archive, _ = _fetcher(evidence, artifact_change=change)
    result = comparison.compare_current_reference(evidence[1], environ=evidence[3],
        fetch_json=fetch, fetch_archive=archive)
    assert result['status'] == 'UNAVAILABLE_EVIDENCE'
    assert result['reuse_authorized'] is False


def test_api_failure_is_explicit_and_never_a_matching_result(evidence):
    def unavailable(*_a, **_kw): raise ValueError('secret detail must not appear')
    result = comparison.compare_current_reference(evidence[1], environ=evidence[3], fetch_json=unavailable)
    assert result['status'] == 'UNAVAILABLE_EVIDENCE'
    assert 'secret detail' not in json.dumps(result)


def test_reference_cannot_use_a_pr_job_or_wrong_checkout_as_current_provenance(evidence):
    result=comparison.compare_current_reference(evidence[1],environ=_env())
    assert result['status']=='UNAVAILABLE_EVIDENCE'
    assert result['reason']=='unavailable_current_reference_binding'
    env={**evidence[3],'GITHUB_SHA':'e'*40}
    result=comparison.compare_current_reference(evidence[1],environ=env)
    assert result['status']=='UNAVAILABLE_EVIDENCE'


@pytest.mark.parametrize('change', ['remove_command','move_before_after','omit_token','omit_artifact'])
def test_workflow_comparison_wiring_is_validated(change):
    text=(ROOT/'.github/workflows/quality-gate.yml').read_text(encoding='utf-8')
    if change=='remove_command': text=text.replace('python -B tools/compare_quality_gate_ci_environment.py','python -B missing.py')
    elif change=='move_before_after':
        text=text.replace('--role reference --phase after_suite','--role reference --phase before_suite')
    elif change=='omit_token':
        reference=text.index('  post-merge-reference:')
        text=text[:reference]+text[reference:].replace('GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}','OTHER_TOKEN: ignored')
    else: text=text.replace('${{ runner.temp }}/ci-environment-comparison.json','${{ runner.temp }}/missing.json')
    assert not validate_workflow_text(text)['passed']


def test_cli_writes_shadow_status_without_confusing_it_with_test_verdict(evidence, monkeypatch, tmp_path):
    before, after = tmp_path/'before.json', tmp_path/'after.json'
    before.write_text(json.dumps(evidence[1]['before_suite']))
    after.write_text(json.dumps(evidence[1]['after_suite']))
    output=tmp_path/'comparison.json'
    monkeypatch.setattr('sys.argv',['compare','--before',str(before),'--after',str(after),'--output',str(output)])
    monkeypatch.setattr(comparison,'compare_current_reference',lambda *_a,**_kw: {'status':'UNAVAILABLE_EVIDENCE','reuse_authorized':False})
    assert comparison.main() == 0
    assert json.loads(output.read_text())['status'] == 'UNAVAILABLE_EVIDENCE'


def test_workflow_retains_execution_and_separates_comparison_from_legacy_receipt():
    text=(ROOT/'.github/workflows/quality-gate.yml').read_text(encoding='utf-8')
    assert validate_workflow_text(text)['passed']
    assert text.count('python -B tools/compare_quality_gate_ci_environment.py') == 1
    assert text.index('Compare captured environments without authorizing reuse') > text.index('Observe actual reference environment after suite')

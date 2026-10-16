from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools import capture_quality_gate_ci_environment as observer
from tools.core.quality_gate_ci import ROOT, load_ci_execution_policy, validate_workflow_text


def _env():
    return {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'example/sage',
        'GITHUB_SHA': 'a' * 40, 'GITHUB_WORKFLOW_SHA': 'b' * 40,
        'GITHUB_WORKFLOW_REF': 'example/sage/.github/workflows/quality-gate.yml@refs/heads/main',
        'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_EVENT_NAME': 'pull_request',
        'GITHUB_REF': 'refs/pull/1/merge', 'GITHUB_JOB': 'python-runtime-compatibility',
        'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64', 'RUNNER_ENVIRONMENT': 'github-hosted',
        'ImageOS': 'ubuntu24', 'ImageVersion': '20261001.1.0', 'GITHUB_TOKEN': 'DO_NOT_RECORD',
        'HOME': '/private/home/DO_NOT_RECORD'}


@pytest.fixture
def collect(monkeypatch):
    monkeypatch.setattr(observer, 'current_git_tree_sha', lambda _root: 'c' * 40)
    monkeypatch.setattr(observer.platform, 'python_version', lambda: '3.12.8')
    monkeypatch.setattr(observer.metadata, 'distributions', lambda: [
        SimpleNamespace(metadata={'Name': 'pytest'}, version='8.4.2'),
        SimpleNamespace(metadata={'Name': 'Demo_Package'}, version='1.0')])
    monkeypatch.setattr(observer.subprocess, 'run', lambda args, **_k: SimpleNamespace(
        returncode=0, stdout='a' * 40 if args[0] == 'git' else
        json.dumps({'node': '20.19.0', 'modules': '115', 'typescript': '5.9.3'})))
    capture = observer.capture_environment
    return lambda **kw: capture(root=ROOT, environ=kw.pop('environ', _env()),
        role=kw.pop('role', 'matrix'), phase=kw.pop('phase', 'before_suite'),
        expected_python=kw.pop('expected_python', '3.12'), **kw)


def test_observation_is_bound_but_never_reuse_or_test_success_proof(collect):
    result = collect()
    assert result['status'] == 'OBSERVED_METADATA_ONLY'
    assert result['reuse_authorized'] is False
    assert 'matrix_result' not in result and 'success' not in result
    assert result['binding']['event_sha'] == 'a' * 40
    assert result['binding']['source_tree_sha'] == 'c' * 40
    assert result['binding']['workflow_run_attempt'] == 2
    assert result['python']['installed_distributions'][0] == {'name': 'demo-package', 'version': '1.0'}
    assert result['node']['typescript'] == '5.9.3'
    assert 'DO_NOT_RECORD' not in json.dumps(result)
    assert len(result['observation_sha256']) == 64


def test_missing_runner_image_is_explicit_unknown_not_equivalence(collect):
    env = _env()
    del env['ImageVersion']
    result = collect(environ=env)
    assert result['status'] == 'INCOMPLETE_METADATA_ONLY'
    assert 'runner.ImageVersion' in result['unknowns']
    assert result['reuse_authorized'] is False


@pytest.mark.parametrize('update', [
    {'GITHUB_JOB': 'pr-matrix-receipt'}, {'GITHUB_RUN_ID': '0'}, {'GITHUB_RUN_ATTEMPT': 'invalid'},
    {'GITHUB_SHA': 'not-a-sha'}, {'GITHUB_ACTIONS': 'false'},
    {'GITHUB_WORKFLOW_REF': 'example/sage/.github/workflows/other.yml@refs/heads/main'},
])
def test_wrong_job_or_malformed_ci_binding_is_rejected(collect, update):
    with pytest.raises(ValueError): collect(environ={**_env(), **update})


def test_wrong_observed_python_is_not_declared_cell_proof(collect):
    with pytest.raises(ValueError): collect(expected_python='3.13')


def test_reference_has_its_own_event_and_job_binding(collect):
    env = {**_env(), 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_JOB': 'post-merge-reference'}
    result = collect(environ=env, role='reference', phase='after_suite')
    assert result['role'] == 'reference' and result['phase'] == 'after_suite'
    with pytest.raises(ValueError): collect(environ=env, role='matrix')


@pytest.mark.parametrize('node_failure', ['missing', 'timeout', 'bad_json', 'missing_typescript'])
def test_unavailable_node_is_unknown_without_masking_test_success(collect, monkeypatch, node_failure):
    def run(*_a, **_kw):
        if _a[0][0] == 'git': return SimpleNamespace(returncode=0, stdout='a' * 40)
        if node_failure == 'missing': raise OSError('local private path')
        if node_failure == 'timeout': raise observer.subprocess.TimeoutExpired('node', 15)
        return SimpleNamespace(returncode=0, stdout='not json' if node_failure == 'bad_json'
            else json.dumps({'node': '20.19.0', 'modules': '115'}))
    monkeypatch.setattr(observer.subprocess, 'run', run)
    result = collect()
    assert result['status'] == 'INCOMPLETE_METADATA_ONLY'
    assert 'node' in result['unknowns']
    assert 'private path' not in json.dumps(result)


def test_duplicate_normalized_packages_are_not_silently_collapsed(collect, monkeypatch):
    monkeypatch.setattr(observer.metadata, 'distributions', lambda: [
        SimpleNamespace(metadata={'Name': 'Demo_Package'}, version='1.0'),
        SimpleNamespace(metadata={'Name': 'demo-package'}, version='2.0')])
    result = collect()
    assert 'python.installed_distributions' in result['unknowns']
    assert len(result['python']['installed_distributions']) == 2


def test_distribution_scan_failure_is_explicit_unknown(collect, monkeypatch):
    monkeypatch.setattr(observer.metadata, 'distributions', lambda: (_ for _ in ()).throw(OSError('private')))
    result = collect()
    assert 'python.installed_distributions' in result['unknowns']


def test_identity_is_order_independent_and_changes_with_observed_dependency(collect, monkeypatch):
    first = collect()
    monkeypatch.setattr(observer.metadata, 'distributions', lambda: [
        SimpleNamespace(metadata={'Name': 'Demo_Package'}, version='1.0'),
        SimpleNamespace(metadata={'Name': 'pytest'}, version='8.4.2')])
    assert collect()['observation_sha256'] == first['observation_sha256']
    monkeypatch.setattr(observer.metadata, 'distributions', lambda: [
        SimpleNamespace(metadata={'Name': 'pytest'}, version='8.4.3')])
    assert collect()['observation_sha256'] != first['observation_sha256']


def test_workflow_observes_actual_jobs_without_changing_legacy_receipt():
    text = (ROOT / '.github/workflows/quality-gate.yml').read_text(encoding='utf-8')
    assert validate_workflow_text(text)['passed']
    assert text.count('python -B tools/capture_quality_gate_ci_environment.py') == 4
    receipt_job = text.split('  pr-matrix-receipt:')[1].split('  post-merge-reference:')[0]
    assert 'capture_quality_gate_ci_environment' not in receipt_job
    assert 'name: nexora-sage-pr-matrix-receipt' in receipt_job
    assert load_ci_execution_policy()['environment_observation']['reuse_authorized'] is False


def test_cli_writes_only_observation_and_returns_failure_for_invalid_context(collect, monkeypatch, tmp_path):
    monkeypatch.setattr(observer, 'capture_environment', lambda **kw: collect())
    output = tmp_path / 'observation.json'
    monkeypatch.setattr('sys.argv', ['observer', '--output', str(output), '--role', 'matrix',
        '--phase', 'before_suite', '--python-version', '3.12'])
    assert observer.main() == 0
    assert json.loads(output.read_text())['reuse_authorized'] is False
    monkeypatch.setattr(observer, 'capture_environment', lambda **_kw: (_ for _ in ()).throw(ValueError('invalid CI binding')))
    assert observer.main() == 2


@pytest.mark.parametrize('change', ['wrong_phase', 'wrong_runtime', 'missing_upload', 'removed_observation'])
def test_workflow_validator_rejects_observation_drift(change):
    text = (ROOT / '.github/workflows/quality-gate.yml').read_text(encoding='utf-8')
    if change == 'wrong_phase':
        text = text.replace('--role matrix --phase before_suite', '--role matrix --phase after_suite')
    elif change == 'wrong_runtime':
        text = text.replace('--role reference --phase after_suite --python-version "3.12"',
            '--role reference --phase after_suite --python-version "3.13"')
    elif change == 'missing_upload':
        text = text.replace('name: nexora-sage-ci-environment-reference-', 'name: unrelated-')
    else:
        text = text.replace('python -B tools/capture_quality_gate_ci_environment.py', 'python -B missing.py', 1)
    assert not validate_workflow_text(text)['passed']


def test_checked_out_commit_cannot_be_replaced_by_declared_environment(collect):
    with pytest.raises(ValueError, match='Checked-out commit'):
        collect(environ={**_env(), 'GITHUB_SHA': 'd' * 40})


def test_missing_source_input_is_unknown(collect, monkeypatch):
    original = observer.Path.read_bytes
    def read_bytes(path):
        if path.name == 'requirements.txt': raise OSError('private')
        return original(path)
    monkeypatch.setattr(observer.Path, 'read_bytes', read_bytes)
    result = collect()
    assert 'input.requirements.txt' in result['unknowns']
    assert result['input_sha256']['requirements.txt'] is None


@pytest.mark.parametrize('field,value', [('reuse_authorized', True), ('mode', 'reuse'),
    ('runner_metadata_keys', ['GITHUB_TOKEN']), ('input_paths', ['../secret']),
    ('runner_metadata_keys', ['RUNNER_SECRET']), ('input_paths', ['/absolute/file']),
    ('input_paths', ['C:/private']), ('input_paths', ['tools/../private'])])
def test_observation_policy_cannot_become_authority_or_arbitrary_capture(field, value):
    from tools.core.quality_gate_ci import validate_ci_execution_policy
    policy = load_ci_execution_policy()
    policy['environment_observation'][field] = value
    with pytest.raises(ValueError, match='observation'): validate_ci_execution_policy(policy)


def test_observation_field_selection_is_owned_by_contract_not_duplicated_in_validator():
    from tools.core.quality_gate_ci import validate_ci_execution_policy
    policy = load_ci_execution_policy()
    policy['environment_observation']['runner_metadata_keys'] = ['RUNNER_OS']
    policy['environment_observation']['input_paths'] = ['pyproject.toml']
    validate_ci_execution_policy(policy)

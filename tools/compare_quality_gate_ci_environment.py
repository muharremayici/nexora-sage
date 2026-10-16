"""Compare verified CI observations in shadow mode, never authorize test reuse."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import capture_quality_gate_ci_environment as observation
from tools import resolve_quality_gate_ci_tier as admission
from tools.core.quality_gate_ci import load_ci_execution_policy


def _json_object(data: bytes) -> dict:
    if len(data) > 262_144:
        raise ValueError('Observation exceeds bounded size')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    try:
        payload = json.loads(data.decode('utf-8'), object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError('Observation JSON nesting exceeds bounded parsing') from exc
    if not isinstance(payload, dict):
        raise ValueError('Observation must be an object')
    pending, visited = [(payload,0)], 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 20_000:
            raise ValueError('Observation structure exceeds bounded parsing')
        children = value.values() if isinstance(value,dict) else value if isinstance(value,list) else ()
        pending.extend((child,depth+1) for child in children)
    return payload


def pair_from_archive(archive: bytes) -> dict:
    if len(archive) > 2_000_000:
        raise ValueError('Environment archive exceeds bounded size')
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        files = bundle.infolist()
        if len(files) != 2 or {row.filename for row in files} != {'before.json', 'after.json'}:
            raise ValueError('Environment archive must contain exactly two root observations')
        if any(row.is_dir() or row.file_size > 262_144 for row in files):
            raise ValueError('Environment archive contains an invalid entry')
        try:
            return {phase: _json_object(bundle.read(name)) for phase, name in (
                ('before_suite', 'before.json'), ('after_suite', 'after.json'))}
        except (RuntimeError,NotImplementedError) as exc:
            raise ValueError('Environment archive compression or encryption is unsupported') from exc


def _projection(payload: dict, *, role: str, phase: str, expected: dict, policy: dict) -> dict:
    contract = policy['environment_observation']
    if (not isinstance(payload, dict) or payload.get('meta') != {'kind': contract['kind'], 'version': 'v1'}
            or payload.get('role') != role or payload.get('phase') != phase
            or payload.get('status') != 'OBSERVED_METADATA_ONLY' or payload.get('unknowns') != []
            or payload.get('reuse_authorized') is not False):
        raise ValueError('Observation incomplete or outside its declared role/phase')
    core = {k:v for k,v in payload.items() if k != 'observation_sha256'}
    if payload.get('observation_sha256') != observation._digest(core):
        raise ValueError('Observation digest mismatch')
    binding = payload.get('binding')
    if not isinstance(binding, dict) or any(binding.get(k) != v for k,v in expected.items()):
        raise ValueError('Observation source/run context mismatch')
    for name in ('workflow_run_id', 'workflow_run_attempt'):
        if type(binding.get(name)) is not int or binding[name] <= 0:
            raise ValueError('Observation run identity invalid')
    if (not re.fullmatch(r'[0-9a-f]{40}', str(binding.get('workflow_sha', '')))
            or observation._text(binding.get('workflow_ref')) is None
            or not binding['workflow_ref'].startswith(expected['repository'] + '/' + policy['workflow_path'] + '@')
            or payload.get('policy_sha256') != observation._digest(policy)
            or payload.get('suite_command') != policy['commands']['full_suite']):
        raise ValueError('Observation workflow/policy context mismatch')
    python, runner, node, inputs = [payload.get(key) for key in ('python','runner','node','input_sha256')]
    if not all(isinstance(value, dict) for value in (python, runner, node, inputs)):
        raise ValueError('Observation metadata section missing')
    python_fields = ('version','implementation','cache_tag','soabi','system','machine')
    if set(python) != {*python_fields, 'installed_distributions'}:
        raise ValueError('Observation interpreter metadata incomplete')
    if (any(observation._text(python[key]) is None for key in python_fields)
            or '.'.join(python['version'].split('.')[:2]) != policy['reference_python_version']
            or set(runner) != set(contract['runner_metadata_keys'])
            or any(observation._text(value) is None for value in runner.values())
            or set(node) != {'node','modules','typescript'}
            or any(observation._text(value) is None for value in node.values())
            or set(inputs) != set(contract['input_paths'])
            or any(not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in inputs.values())):
        raise ValueError('Observation runtime/input metadata incomplete')
    packages = python['installed_distributions']
    if not isinstance(packages,list) or not 1 <= len(packages) <= 2000:
        raise ValueError('Installed distribution inventory missing or oversized')
    names = set()
    for row in packages:
        if (not isinstance(row,dict) or set(row) != {'name','version'}
                or not isinstance(row['name'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}',row['name'])
                or observation._text(row['version']) is None or row['name'] in names):
            raise ValueError('Installed distribution inventory invalid or ambiguous')
        names.add(row['name'])
    normalized_python = {**python, 'installed_distributions': sorted(packages,key=lambda row:row['name'])}
    return {'python': normalized_python, 'runner':runner, 'node':node, 'input_sha256':inputs}


def _result(status: str, policy: dict) -> dict:
    return {'meta': {'kind': policy['environment_observation']['comparison']['kind'], 'version':'v1'},
        'status': status, 'reuse_authorized': False, 'environment_equivalence': 'not_proven',
        'provenance_status': 'unavailable',
        'limits': ['Captured metadata is not binary/startup-hook integrity or event equivalence.',
            'No test skip, workflow-tier output, publication authority or complete environment-equivalence verdict.']}


def compare_pairs(pr_pair: dict, reference_pair: dict, *, pr_source: dict,
                  reference_binding: dict, policy: dict) -> dict:
    phases = {'before_suite','after_suite'}
    if not isinstance(pr_pair,dict) or not isinstance(reference_pair,dict) or set(pr_pair) != phases or set(reference_pair) != phases:
        raise ValueError('Comparison requires both complete before/after pairs')
    expected_pr = {key:pr_source[key] for key in ('repository','event_sha','source_tree_sha',
        'workflow_run_id','workflow_run_attempt')}
    expected_pr.update(event_name='pull_request', ref=f"refs/pull/{pr_source['pull_request_number']}/merge",
        job='python-runtime-compatibility')
    if expected_pr['source_tree_sha'] != reference_binding.get('source_tree_sha'):
        raise ValueError('PR and reference source trees differ')
    projections = [_projection(pair[phase], role=role, phase=phase, expected=expected, policy=policy)
        for role,pair,expected in (('matrix',pr_pair,expected_pr), ('reference',reference_pair,reference_binding))
        for phase in ('before_suite','after_suite')]
    differences = set()
    for other in projections[1:]:
        for section in projections[0]:
            for key in projections[0][section]:
                if projections[0][section][key] != other[section][key]:
                    differences.add(section + '.' + key)
    result = _result('DIFFERENT_CAPTURED_METADATA' if differences else 'MATCHING_CAPTURED_METADATA',policy)
    result.update(provenance_status='supplied_context_bindings_checked_only',
        difference_count=len(differences), difference_fields=sorted(differences)[:32],
        omitted_difference_count=max(0,len(differences)-32),
        observation_sha256=[pair[phase]['observation_sha256'] for pair in (pr_pair,reference_pair)
            for phase in ('before_suite','after_suite')])
    return result


def _pr_pair(source: dict, *, policy: dict, token: str, fetch_json, fetch_archive) -> tuple[dict,int]:
    base = 'https://api.github.com/repos/' + source['repository']
    inventory = fetch_json(f"{base}/actions/runs/{source['workflow_run_id']}/artifacts?per_page=100",token=token)
    rows = inventory.get('artifacts') if isinstance(inventory,dict) else None
    if (not isinstance(rows,list) or type(inventory.get('total_count')) is not int
            or inventory['total_count'] != len(rows)):
        raise ValueError('Environment artifact inventory incomplete')
    prefix = policy['environment_observation']['artifact_prefix']
    name = f"{prefix}-matrix-{policy['reference_python_version']}-{source['workflow_run_attempt']}"
    matches = [row for row in rows if isinstance(row,dict) and row.get('name') == name]
    if len(matches) != 1:
        raise ValueError('Environment artifact missing or ambiguous')
    artifact = matches[0]
    if (artifact.get('expired') is not False or type(artifact.get('id')) is not int or artifact['id'] <= 0
            or artifact.get('archive_download_url') != f"{base}/actions/artifacts/{artifact['id']}/zip"):
        raise ValueError('Environment artifact unavailable or invalid')
    return pair_from_archive(fetch_archive(artifact['archive_download_url'],token=token)), artifact['id']


def compare_current_reference(reference_pair: dict, *, environ=None, root: Path = ROOT,
        fetch_json=admission._github_json, fetch_archive=admission._download_artifact_archive) -> dict:
    policy = load_ci_execution_policy(root)
    result = _result('UNAVAILABLE_EVIDENCE',policy)
    stage = 'current_reference_binding'
    started = time.monotonic()
    def read_json(url, *, token, timeout_seconds=5.0):
        return fetch_json(url,token=token,timeout_seconds=min(timeout_seconds,5.0))
    def read_archive(url, *, token):
        return fetch_archive(url,token=token,timeout_seconds=5.0)
    try:
        env = os.environ if environ is None else environ
        binding = observation._binding(env,'reference',policy)
        binding['source_tree_sha'] = observation._source_tree(root,binding['event_sha'])
        event = {'ref':binding['ref'], 'after':binding['event_sha'],
            'repository':{'full_name':binding['repository']}}
        token = env.get('GITHUB_TOKEN','')
        if not token:
            raise ValueError('Missing CI evidence access')
        stage = 'pr_provenance'
        print('[ci-environment-comparison] phase=verify_pr_provenance',flush=True)
        pulls = admission._associated_pull_requests(event,token=token,fetch_json=read_json)
        receipt = admission._verified_pr_matrix_receipt(event,pulls,token=token,
            source_tree_sha=binding['source_tree_sha'],policy=policy,
            fetch_json=read_json,fetch_archive=read_archive)
        stage = 'pr_environment_artifact'
        print(f'[ci-environment-comparison] phase=read_environment elapsed_seconds={time.monotonic()-started:.3f}',flush=True)
        pair, artifact_id = _pr_pair(receipt['source'],policy=policy,token=token,
            fetch_json=read_json,fetch_archive=read_archive)
        stage = 'observation_comparison'
        result = compare_pairs(pair,reference_pair,pr_source=receipt['source'],reference_binding=binding,policy=policy)
        result.update(provenance_status='verified_pr_receipt_and_artifact_current_reference_binding',
            pr_receipt_sha256=receipt['receipt_sha256'], pr_environment_artifact_id=artifact_id)
    except (OSError,ValueError,KeyError,UnicodeDecodeError,zipfile.BadZipFile,subprocess.TimeoutExpired):
        result['reason'] = 'unavailable_' + stage
    return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',required=True)
    parser.add_argument('--after',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        pair={}
        for phase,name in (('before_suite',args.before),('after_suite',args.after)):
            with Path(name).open('rb') as handle:
                pair[phase]=_json_object(handle.read(262_145))
        result=compare_current_reference(pair)
    except (OSError,ValueError,UnicodeDecodeError):
        result=_result('UNAVAILABLE_EVIDENCE',load_ci_execution_policy())
        result['reason']='current_observations_missing_or_invalid'
    try:
        output=Path(args.output)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    except OSError:
        print('[ci-environment-comparison] FAIL unable to persist diagnostic',file=sys.stderr)
        return 2
    print(f"[ci-environment-comparison] {result['status']} reuse_authorized=false")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

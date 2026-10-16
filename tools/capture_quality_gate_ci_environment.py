"""Bounded CI metadata observations; never a test receipt or reuse authority."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.quality_gate_ci import current_git_tree_sha, load_ci_execution_policy, supported_python_versions


def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _text(value) -> str | None:
    # Metadata only: do not export paths, arbitrary env values or exception text.
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.+/@:() -]{1,256}', value) else None


def _binding(env: dict[str, str], role: str, policy: dict) -> dict:
    expected_job = {'matrix': 'python-runtime-compatibility', 'reference': 'post-merge-reference'}
    if role not in expected_job or env.get('GITHUB_ACTIONS') != 'true' or env.get('GITHUB_JOB') != expected_job[role]:
        raise ValueError('Environment observation requires its actual governed test job')
    repository = env.get('GITHUB_REPOSITORY', '')
    workflow_ref = env.get('GITHUB_WORKFLOW_REF', '')
    event = env.get('GITHUB_EVENT_NAME')
    ref = env.get('GITHUB_REF', '')
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or not workflow_ref.startswith(repository + '/' + policy['workflow_path'] + '@')
            or _text(workflow_ref) is None or _text(ref) is None
            or event not in ('pull_request', 'push')
            or (event == 'push' and ref != 'refs/heads/' + policy['target_branch'])
            or (event == 'pull_request' and not re.fullmatch(r'refs/pull/[1-9][0-9]*/merge', ref))
            or (role == 'reference' and event != 'push')):
        raise ValueError('Invalid environment observation workflow/event binding')
    result = {'repository': repository, 'workflow_ref': workflow_ref, 'event_name': event,
        'ref': ref, 'job': env['GITHUB_JOB']}
    for name, key in [('GITHUB_SHA', 'event_sha'), ('GITHUB_WORKFLOW_SHA', 'workflow_sha')]:
        value = env.get(name, '')
        if not re.fullmatch(r'[0-9a-f]{40}', value):
            raise ValueError('Invalid environment observation commit binding')
        result[key] = value
    for name, key in [('GITHUB_RUN_ID', 'workflow_run_id'), ('GITHUB_RUN_ATTEMPT', 'workflow_run_attempt')]:
        value = env.get(name, '')
        if not re.fullmatch(r'[1-9][0-9]{0,19}', value):
            raise ValueError('Invalid environment observation run binding')
        result[key] = int(value)
    return result


def _distributions(unknowns: list[str]) -> list[dict]:
    rows = []
    try:
        for dist in metadata.distributions():
            if len(rows) >= 2000:
                raise ValueError('Distribution inventory exceeds bounded observation')
            name, version = dist.metadata.get('Name', ''), dist.version
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name)
                    or _text(version) is None):
                raise ValueError('Invalid distribution metadata')
            rows.append({'name': re.sub(r'[-_.]+', '-', name).lower(), 'version': version})
        if not rows or len({row['name'] for row in rows}) != len(rows):
            unknowns.append('python.installed_distributions')
    except (OSError, ValueError, TypeError, KeyError):
        unknowns.append('python.installed_distributions')
    return sorted(rows, key=lambda row: (row['name'], row['version']))


def _source_tree(root: Path, event_sha: str) -> str:
    proc = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=15, check=False)
    if proc.returncode != 0 or proc.stdout.strip() != event_sha:
        raise ValueError('Checked-out commit does not match the observed CI event')
    return current_git_tree_sha(root)


def _node(root: Path, unknowns: list[str]) -> dict | None:
    # Resolve TypeScript through the actual AST engine anchor, not ambient modules.
    code = ("const {createRequire}=require('node:module');"
        "const r=createRequire(require('node:path').join(process.argv[1],'package.json'));"
        "console.log(JSON.stringify({node:process.versions.node,modules:process.versions.modules,"
        "typescript:r('typescript/package.json').version}));")
    try:
        proc = subprocess.run(['node', '-e', code, str(root / 'tools/engines')],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15, check=False)
        if proc.returncode != 0 or len(proc.stdout) > 4096:
            raise ValueError('Node observation unavailable')
        result = json.loads(proc.stdout)
        if not isinstance(result, dict) or set(result) != {'node', 'modules', 'typescript'}:
            raise ValueError('Node observation incomplete')
        if any(_text(value) is None for value in result.values()):
            raise ValueError('Invalid Node metadata')
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired):
        unknowns.append('node')
        return None


def capture_environment(*, root: Path = ROOT, environ=None, role: str, phase: str,
                        expected_python: str) -> dict:
    policy = load_ci_execution_policy(root)
    contract = policy['environment_observation']
    if phase not in ('before_suite', 'after_suite'):
        raise ValueError('Unknown CI observation phase')
    binding = _binding(os.environ if environ is None else environ, role, policy)
    actual_python = platform.python_version()
    if (expected_python not in supported_python_versions(root)
            or '.'.join(actual_python.split('.')[:2]) != expected_python
            or (role == 'reference' and expected_python != policy['reference_python_version'])):
        raise ValueError('Observed interpreter does not match the governed runtime cell')
    binding['source_tree_sha'] = _source_tree(root, binding['event_sha'])
    unknowns = []
    env = os.environ if environ is None else environ
    runner = {key: _text(env.get(key)) for key in contract['runner_metadata_keys']}
    unknowns.extend('runner.' + key for key, value in runner.items() if value is None)
    inputs = {}
    for name in contract['input_paths']:
        try:
            inputs[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
        except OSError:
            inputs[name] = None
            unknowns.append('input.' + name)
    python = {'version': actual_python, 'implementation': _text(platform.python_implementation()),
        'cache_tag': _text(sys.implementation.cache_tag), 'soabi': _text(sysconfig.get_config_var('SOABI')),
        'system': _text(platform.system()), 'machine': _text(platform.machine()),
        'installed_distributions': _distributions(unknowns)}
    unknowns.extend('python.' + key for key, value in python.items() if value is None)
    node = _node(root, unknowns)
    core = {'meta': {'kind': contract['kind'], 'version': 'v1'}, 'role': role, 'phase': phase,
        'binding': binding, 'policy_sha256': _digest(policy), 'suite_command': policy['commands']['full_suite'],
        'python': python, 'runner': runner, 'node': node, 'input_sha256': inputs,
        'unknowns': sorted(set(unknowns)), 'reuse_authorized': False,
        'status': 'INCOMPLETE_METADATA_ONLY' if unknowns else 'OBSERVED_METADATA_ONLY',
        'limits': ['Not a successful test result or complete environment-equivalence proof.',
            'Installed distribution metadata and input hashes do not prove binary, startup-hook or external-input integrity.',
            'No environment dump, source text, dependency locations or secrets are captured.',
            'No test is removed or skipped; before/after observations do not prove absence of intermediate changes.']}
    return {**core, 'observation_sha256': _digest(core)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--role', required=True, choices=('matrix', 'reference'))
    parser.add_argument('--phase', required=True, choices=('before_suite', 'after_suite'))
    parser.add_argument('--python-version', required=True)
    args = parser.parse_args()
    try:
        payload = capture_environment(role=args.role, phase=args.phase, expected_python=args.python_version)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print('[ci-environment] FAIL invalid or unavailable observation context', file=sys.stderr)
        return 2
    print(f"[ci-environment] {payload['status']} reuse_authorized=false")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

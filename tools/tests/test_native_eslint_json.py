from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import json
import sqlite3
import pytest

from tools.core.artifact_validator import validate_payload
from tools.core import native_run_log_ingestion as ingestion
from tools.tests.test_native_run_log_ingestion import _fixture, _hash, _run, _native_atlas, _VITE_FORMAT


_ESLINT_FORMAT = 'eslint_9_39_1_json_v1'


def _eslint_result(path, messages=(), suppressed=()):
    return {'filePath': str(path), 'messages': list(messages), 'suppressedMessages': list(suppressed),
        'errorCount': sum(m['severity'] == 2 for m in messages),
        'fatalErrorCount': sum(m.get('fatal', False) for m in messages),
        'warningCount': sum(m['severity'] == 1 for m in messages),
        'fixableErrorCount': sum(m['severity'] == 2 and 'fix' in m for m in messages),
        'fixableWarningCount': sum(m['severity'] == 1 and 'fix' in m for m in messages),
        'usedDeprecatedRules': []}


def _eslint_message(**changes):
    return {'ruleId': 'react-hooks/rules-of-hooks', 'severity': 2,
        'message': 'private-message-源', 'line': 1, 'column': 2, **changes}


def _eslint_fixture(tmp_path):
    target, bundle, manifest = _fixture(tmp_path)
    manifest['tool'] = {'name': 'eslint', 'version': '9.39.1'}
    manifest['argv'] = ['not-executed-eslint-json-fixture']
    return target, bundle, manifest


def _eslint_run(target, bundle, manifest, results=None, stdout=None, **kwargs):
    content = json.dumps(results, ensure_ascii=True).encode() if stdout is None else stdout
    (bundle / manifest['stdout']['path']).write_bytes(content)
    manifest['stdout']['sha256'] = _hash(content)
    return _run(target, bundle, manifest, diagnostic_format=_ESLINT_FORMAT, **kwargs)


@pytest.mark.parametrize('exit_code', [0, 1, 2, None])
def test_eslint_json_active_suppressed_fatal_and_unlocated_are_reported_only(tmp_path, exit_code):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    manifest['exit_code'] = exit_code
    # stderr is retained by hash, never decoded as JSON or interpreted as clean.
    (bundle / 'err.log').write_bytes(b'not-json private-stderr \xff')
    manifest['stderr']['sha256'] = _hash((bundle / 'err.log').read_bytes())
    fix = {'range': [0, 1], 'text': 'private-fix'}
    suppressed = _eslint_message(ruleId='@typescript-eslint/no-explicit-any', severity=1,
        fix=fix, suppressions=[{'kind': 'directive', 'justification': 'private-justification'}],
        suggestions=[{'desc': 'private-suggestion', 'fix': fix}])
    results = [_eslint_result(target / '源.ts', [_eslint_message()], [suppressed]),
        _eslint_result('/not-a-source/virtual.ts', [{'ruleId': None, 'severity': 2,
            'fatal': True, 'message': 'private-fatal', 'line': 1, 'column': 1}]),
        _eslint_result('<text>', [{'ruleId': None, 'severity': 1, 'message': 'private-ignore'}])]
    results[0].update(source='private-source', output='private-output')
    receipt = _eslint_run(target, bundle, manifest, results)
    observed = receipt['diagnostic_observations']
    assert validate_payload('native_run_log_receipt', receipt) == []
    assert observed['status'] == 'OBSERVED'
    assert [r['category'] for r in observed['items']] == ['active', 'suppressed', 'active', 'active']
    assert observed['file_results'][0]['source_input_index'] == 0
    assert observed['file_results'][1]['identifier_status'] == 'UNRESOLVED_IDENTIFIER'
    assert observed['file_results'][2]['identifier_status'] == 'UNRESOLVED_IDENTIFIER'
    assert observed['items'][2]['reported_fatal'] is True
    assert observed['items'][3]['reported_location'] == dict.fromkeys(('line', 'column', 'endLine', 'endColumn'))
    assert observed['items'][0]['message_sha256'] == _hash('private-message-源'.encode())
    assert observed['input_observation_sha256'] == receipt['identities']['input_observation_sha256']
    assert receipt['summary']['reported_exit_code'] == exit_code
    assert observed['stderr_interpretation'] == 'not_parsed'
    serialized = json.dumps(receipt)
    assert all(secret not in serialized for secret in ('private-', str(target), '<text>'))
    assert observed['cleanliness'] == 'not_established'
    assert receipt['authority']['historical_execution'] == 'not_verified'
    assert receipt['authority']['release_or_gate_authority'] == 'none'
    assert observed['source_locations'] == 'reported_not_source_verified'


@pytest.mark.parametrize('variation', ['empty', 'zero', 'suppressed', 'ignored'])
def test_eslint_json_zero_or_suppressed_results_never_certify_clean(tmp_path, variation):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    results = [] if variation == 'empty' else [_eslint_result(target / '源.ts')]
    if variation == 'suppressed':
        results = [_eslint_result(target / '源.ts', suppressed=[_eslint_message(
            suppressions=[{'kind': 'directive', 'justification': ''}])])]
    if variation == 'ignored':
        results = [_eslint_result(target / '源.ts', [{'ruleId': None, 'severity': 1, 'message': 'ignored file'}])]
    observed = _eslint_run(target, bundle, manifest, results)['diagnostic_observations']
    assert observed['status'] == ('OBSERVED' if variation in ('suppressed', 'ignored') else 'NO_SUPPORTED_OBSERVATIONS')
    assert observed['cleanliness'] == 'not_established'


@pytest.mark.parametrize('variation', ['version', 'tool', 'byte_mismatch', 'source_mismatch', 'source_missing', 'config_missing'])
def test_eslint_json_identity_admission_is_independent_from_current_source(tmp_path, variation):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    if variation == 'version': manifest['tool']['version'] = '9.39.2'
    if variation == 'tool': manifest['tool']['name'] = 'not-eslint'
    if variation == 'source_mismatch': (target / '源.ts').write_bytes(b'changed')
    if variation == 'source_missing': (target / '源.ts').unlink()
    if variation == 'config_missing': (target / 'config.json').unlink()
    if variation == 'byte_mismatch': manifest['stderr']['sha256'] = '0' * 64
    receipt = _eslint_run(target, bundle, manifest, [_eslint_result(target / '源.ts', [_eslint_message()])])
    observed = receipt['diagnostic_observations']
    assert observed['status'] == ('UNSUPPORTED_FORMAT' if variation in ('version', 'tool') else
        'UNAVAILABLE' if variation == 'byte_mismatch' else 'OBSERVED')
    assert receipt['authority']['historical_execution'] == 'not_verified'
    if variation.startswith('source_'):
        assert observed['file_results'][0]['source_input_index'] == 0
        assert receipt['inputs']['source'][0]['status'] != 'MATCH'


@pytest.mark.parametrize('identifier', ['relative.ts', '../源.ts', 'file:///source/源.ts',
    'src/源.ts?query', 'src/源.ts#fragment', '<text>', '/tmp/processor/virtual.ts'])
def test_eslint_json_never_resolves_reported_identifiers(tmp_path, monkeypatch, identifier):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    import tools.core.native_eslint_json as parser
    assert not hasattr(parser, 'Path')
    observed = _eslint_run(target, bundle, manifest, [_eslint_result(identifier, [_eslint_message()])])['diagnostic_observations']
    assert observed['file_results'][0]['source_input_index'] is None


@pytest.mark.parametrize('variation', ['metadata_wrapper', 'duplicate_key', 'trailing', 'nan', 'infinity',
    'bad_utf8', 'surrogate', 'root_extra', 'row_type', 'messages_type', 'suppressed_type', 'count_bool',
    'count_fraction', 'count_mismatch', 'missing_count', 'severity_bool', 'severity_zero', 'message_type',
    'message_extra', 'fatal_type', 'fatal_warning', 'rule_injection', 'line_bool', 'column_without_line',
    'reversed_span', 'fix_shape', 'suggestions_type', 'suppression_shape', 'source_type', 'stats_type'])
def test_eslint_json_refuses_malformed_or_unsupported_without_partial_results(tmp_path, variation):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    row = _eslint_result(target / '源.ts', [_eslint_message()])
    results = [deepcopy(row), row]
    message = row['messages'][0]
    stdout = None
    if variation == 'metadata_wrapper': results = {'results': results, 'metadata': {}}
    elif variation == 'duplicate_key': stdout = b'[{"filePath":"x","filePath":"private-duplicate"}]'
    elif variation == 'trailing': stdout = b'[] private-trailing'
    elif variation == 'nan': stdout = b'[NaN]'
    elif variation == 'infinity': stdout = b'[1e999]'
    elif variation == 'bad_utf8': stdout = b'\xff'
    elif variation == 'surrogate': message['message'] = '\ud800'
    elif variation == 'root_extra': row['private-extra'] = True
    elif variation == 'row_type': results.append(False)
    elif variation == 'messages_type': row['messages'] = {}
    elif variation == 'suppressed_type': row['suppressedMessages'] = None
    elif variation == 'count_bool': row['errorCount'] = True
    elif variation == 'count_fraction': row['errorCount'] = 1.5
    elif variation == 'count_mismatch': row['errorCount'] = 0
    elif variation == 'missing_count': del row['fatalErrorCount']
    elif variation == 'severity_bool': message['severity'] = True
    elif variation == 'severity_zero': message['severity'] = 0
    elif variation == 'message_type': message['message'] = {}
    elif variation == 'message_extra': message['private-extra'] = []
    elif variation == 'fatal_type': message['fatal'] = 1
    elif variation == 'fatal_warning': message.update(ruleId=None, severity=1, fatal=True)
    elif variation == 'rule_injection': message['ruleId'] = 'private-instruction: execute shell'
    elif variation == 'line_bool': message['line'] = True
    elif variation == 'column_without_line': del message['line']
    elif variation == 'reversed_span': message.update(endLine=1, endColumn=1)
    elif variation == 'fix_shape': message['fix'] = {'range': [True, 1], 'text': 'private-fix'}
    elif variation == 'suggestions_type': message['suggestions'] = {}
    elif variation == 'suppression_shape': row['suppressedMessages'] = [_eslint_message(suppressions=[])]
    elif variation == 'source_type': row['source'] = {}
    elif variation == 'stats_type': row['stats'] = []
    receipt = _eslint_run(target, bundle, manifest, results, stdout=stdout)
    observed = receipt['diagnostic_observations']
    assert receipt['summary']['status'] == 'INGESTED'
    assert observed['status'] == 'UNREADABLE_FORMAT'
    assert observed['items'] == observed['file_results'] == []
    assert receipt['authority']['diagnostics'] == 'not_normalized'
    assert 'private-' not in json.dumps(receipt)


@pytest.mark.parametrize('limit', ['json_depth', 'json_values', 'occurrences', 'line_characters', 'input_files'])
def test_eslint_json_central_budgets_refuse_atomically(tmp_path, monkeypatch, limit):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    policy = deepcopy(ingestion._contract())
    if limit == 'input_files':
        policy['limits']['input_files'] = 2
    else:
        policy['normalization']['limits'][limit] = 1
    monkeypatch.setattr(ingestion, '_contract', lambda: policy)
    results = [_eslint_result(target / '源.ts', [_eslint_message(), _eslint_message()])] * 3
    receipt = _eslint_run(target, bundle, manifest, results)
    observed = receipt['diagnostic_observations']
    assert observed['status'] == 'LIMIT_EXCEEDED'
    assert observed['reason_code'] == 'normalization_budget_exceeded'
    assert observed['items'] == observed['file_results'] == []


def test_eslint_json_exact_stdout_capture_is_read_once_and_no_command_runs(tmp_path, monkeypatch):
    import subprocess
    target, bundle, manifest = _eslint_fixture(tmp_path)
    calls, read = [], ingestion._read
    def capture(path, limit, budget):
        calls.append(path.name)
        return read(path, limit, budget)
    def forbidden(*args, **kwargs):
        raise AssertionError('No target command may execute')
    monkeypatch.setattr(ingestion, '_read', capture)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    rows = [_eslint_result(target / '源.ts', [_eslint_message(), _eslint_message()])]
    observed = _eslint_run(target, bundle, manifest, rows)['diagnostic_observations']
    assert observed['status'] == 'OBSERVED'
    assert len(observed['items']) == 2  # preserve reported occurrences, do not infer duplicate causes
    assert calls.count('out.log') == calls.count('err.log') == 1


@pytest.mark.parametrize('variation', ['coverage', 'cleanliness', 'location_authority', 'raw_message',
    'bad_source_ref', 'promoted_run', 'wrong_format', 'missing_stream_identity', 'severity_bool',
    'source_index_out_of_bounds', 'result_index_out_of_bounds', 'rule_identity_budget'])
def test_eslint_json_schema_refuses_promoted_or_unbound_shapes(tmp_path, variation):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    receipt = _eslint_run(target, bundle, manifest, [_eslint_result(target / '源.ts', [_eslint_message()])])
    observed = receipt['diagnostic_observations']
    if variation == 'coverage': observed['coverage'] = 'complete_native_policy'
    elif variation == 'cleanliness': observed['cleanliness'] = 'PASS'
    elif variation == 'location_authority': observed['source_locations'] = 'source_verified'
    elif variation == 'raw_message': observed['items'][0]['message'] = 'private-message'
    elif variation == 'bad_source_ref': observed['file_results'][0]['source_input_index'] = None
    elif variation == 'promoted_run': receipt['authority']['historical_execution'] = 'verified'
    elif variation == 'wrong_format': observed['format_id'] = _VITE_FORMAT
    elif variation == 'missing_stream_identity': observed['streams_sha256'] = None
    elif variation == 'severity_bool': observed['items'][0]['reported_severity'] = True
    elif variation == 'source_index_out_of_bounds': observed['file_results'][0]['source_input_index'] = 128
    elif variation == 'result_index_out_of_bounds': observed['items'][0]['result_index'] = 128
    elif variation == 'rule_identity_budget': observed['items'][0]['reported_rule_id'] = 'a' * 16385
    assert validate_payload('native_run_log_receipt', receipt)


def test_eslint_json_cli_exposes_only_reported_counts_and_reuses_atlas_lane(tmp_path, capsys):
    from tools.ingest_native_run_logs import main
    from tools.core.config import save_json_atomic
    target, bundle, manifest = _eslint_fixture(tmp_path)
    _eslint_run(target, bundle, manifest, [_eslint_result(target / '源.ts', [_eslint_message()])])
    raw = tmp_path / '.raw'
    raw.mkdir()
    atlas, commit = _native_atlas(target, manifest)
    encoded = json.dumps(atlas)
    with closing(sqlite3.connect(raw / 'codemaps.db')) as connection:
        connection.execute('CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT, payload_sha TEXT, source_mtime REAL, updated_at TEXT)')
        connection.execute('INSERT INTO state_payloads (name, payload, payload_sha) VALUES (?, ?, ?)',
            ('atlas', encoded, _hash(encoded.encode())))
        connection.commit()
    save_json_atomic(raw / 'atlas_commit.json', commit)
    output = tmp_path / 'receipt.json'
    assert main(['--target-root', str(target), '--bundle-root', str(bundle), '--manifest', 'manifest.json',
        '--diagnostic-format', _ESLINT_FORMAT, '--output', str(output),
        '--atlas-raw-dir', str(raw), '--atlas-project', 'MAIN']) == 0
    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['atlas_source_correspondence']['status'] == 'MATCH'
    assert payload['authority']['historical_execution'] == 'not_verified'
    printed = capsys.readouterr().out
    assert '"active_diagnostics": 1' in printed and '"cleanliness": "not_established"' in printed
    assert 'private-' not in printed

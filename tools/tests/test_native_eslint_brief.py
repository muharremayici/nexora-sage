"""Same-invocation display, not a target ESLint execution or new native proof."""
from copy import deepcopy
from contextlib import closing
import json
import sqlite3
import subprocess

import pytest

from tools.core import native_eslint_brief as brief
from tools.core.atlas_integrity import payload_sha256
from tools.core.config import save_json_atomic
from tools.tests.test_native_eslint_json import (
    _ESLINT_FORMAT, _eslint_fixture, _eslint_message, _eslint_result, _eslint_run,
)
from tools.tests.test_native_run_log_ingestion import _hash, _native_atlas


def receipt(tmp_path, results=None):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    rows = results(target) if results else [_eslint_result(target / '源.ts', [_eslint_message()])]
    return _eslint_run(target, bundle, manifest, rows)


def test_brief_preserves_result_category_fatal_and_missing_position_identities(tmp_path):
    payload = receipt(tmp_path, lambda target: [
        _eslint_result(target / '源.ts', [_eslint_message(), _eslint_message(line=3)],
            [_eslint_message(suppressions=[{'kind': 'directive', 'justification': 'private-why'}])]),
        _eslint_result('/private-host/outside.ts', [{'ruleId': None, 'severity': 2,
            'fatal': True, 'message': 'private-parser'}]),
        _eslint_result(target / '源.ts', [{'ruleId': None, 'severity': 1, 'message': 'private-ignore'}]),
    ])
    original = deepcopy(payload)
    text = brief.render_eslint_brief(payload)
    assert payload == original
    assert 'files=3; active=4; suppressed=1; fatal=1' in text
    assert 'result[0] declared_path="\\u6e90.ts" source_now=MATCH' in text
    assert 'result[2] declared_path="\\u6e90.ts" source_now=MATCH' in text
    assert 'active[0]' in text and 'active[1]' in text and 'suppressed[0]' in text
    assert 'rule=null severity=2 fatal=true' in text
    assert 'line=unknown,column=unknown,endLine=unknown,endColumn=unknown' in text
    assert 'line=3,column=2' in text
    assert 'unresolved_identifier_sha256=' in text and 'source_now=unknown' in text
    assert 'private-' not in text and str(tmp_path) not in text
    assert 'reported_not_source_verified' in text and 'Historical execution: not_verified' in text
    assert 'cleanliness: not_established; gate authority: none' in text
    assert 'Shown result files: 3/3' in text and 'Shown diagnostic rows: 5/5' in text


@pytest.mark.parametrize('state', ['MATCH', 'MISMATCH', 'UNAVAILABLE'])
def test_brief_source_now_is_not_historical_execution(tmp_path, state):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    if state == 'MISMATCH': (target / '源.ts').write_text('changed', encoding='utf-8')
    if state == 'UNAVAILABLE': (target / '源.ts').unlink()
    payload = _eslint_run(target, bundle, manifest, [_eslint_result(target / '源.ts', [_eslint_message()])])
    text = brief.render_eslint_brief(payload)
    assert 'source_now=' + state in text and 'Historical execution: not_verified' in text
    assert 'Positions are not source-verified' in text and 'complete inputs' in text


@pytest.mark.parametrize('variation', ['empty', 'zero', 'suppressed', 'invalid_json', 'wrong_tool'])
def test_brief_empty_or_refused_format_never_means_clean(tmp_path, variation):
    target, bundle, manifest = _eslint_fixture(tmp_path)
    rows = [] if variation == 'empty' else [_eslint_result(target / '源.ts')]
    if variation == 'suppressed':
        rows = [_eslint_result(target / '源.ts', suppressed=[_eslint_message(
            suppressions=[{'kind': 'directive', 'justification': ''}])])]
    if variation == 'wrong_tool': manifest['tool']['version'] = '0.0.0'
    payload = _eslint_run(target, bundle, manifest, rows,
        **({'stdout': b'{bad-json'} if variation == 'invalid_json' else {}))
    text = brief.render_eslint_brief(payload)
    assert 'cleanliness: not_established' in text and 'never establishes cleanliness' in text
    assert 'PASS' not in text and 'source-verified' in text
    if variation in ('invalid_json', 'wrong_tool'):
        assert 'result[' not in text and 'Shown diagnostic rows: 0/0' in text


@pytest.mark.parametrize('variation', ['source_index', 'result_index', 'occurrence_result', 'occurrence_index',
    'run', 'streams', 'inputs', 'policy', 'input_digest', 'stream_digest', 'counts', 'suppressed_counts',
    'promoted_run', 'raw_message', 'bool_coordinate'])
def test_brief_withholds_all_detail_on_inconsistent_or_promoted_receipt(tmp_path, variation):
    payload = receipt(tmp_path)
    observed = payload['diagnostic_observations']
    item, file = observed['items'][0], observed['file_results'][0]
    if variation == 'source_index': file['source_input_index'] = 127
    elif variation == 'result_index': file['result_index'] = 1
    elif variation == 'occurrence_result': item['occurrences'][0]['result_index'] = 1
    elif variation == 'occurrence_index': item['occurrences'][0]['message_index'] = 1
    elif variation in ('run', 'streams', 'inputs', 'policy'):
        key = {'run': 'run_sha256', 'streams': 'streams_sha256', 'inputs': 'input_observation_sha256',
               'policy': 'parser_contract_sha256'}[variation]
        observed[key] = '0' * 64
    elif variation == 'input_digest': payload['inputs']['source'][0]['path'] = 'different.ts'
    elif variation == 'stream_digest': payload['streams']['stdout']['byte_count'] += 1
    elif variation == 'counts': file['reported_counts']['errorCount'] += 1
    elif variation == 'suppressed_counts': file['suppressed_count'] += 1
    elif variation == 'promoted_run': payload['authority']['historical_execution'] = 'verified'
    elif variation == 'raw_message': item['message'] = 'private-raw-message'
    elif variation == 'bool_coordinate': item['reported_location']['line'] = True
    text = brief.render_eslint_brief(payload)
    assert 'ESLint brief unavailable:' in text and 'result[' not in text
    assert 'private-' not in text and 'No detail establishes cleanliness' in text


@pytest.mark.parametrize('path', ['/private/host.ts', 'C:/private/host.ts', '../private.ts',
    '\\private\\host.ts', 'src/name\nforged.ts', 'src/name:stream'])
def test_brief_cannot_display_escaping_or_control_injected_paths(tmp_path, path):
    payload = receipt(tmp_path)
    payload['inputs']['source'][0]['path'] = path
    digest = payload_sha256(payload['inputs'])
    payload['identities']['input_observation_sha256'] = digest
    payload['diagnostic_observations']['input_observation_sha256'] = digest
    assert 'invalid_declared_display_path' in brief.render_eslint_brief(payload)


@pytest.mark.parametrize('variation', ['file_cap', 'row_cap', 'character_cap'])
def test_brief_budgets_preserve_whole_rows_and_report_omissions(tmp_path, monkeypatch, variation):
    payload = receipt(tmp_path, lambda target: [_eslint_result(target / '源.ts',
        [_eslint_message(ruleId='a' * (3000 if variation == 'character_cap' else 1)), _eslint_message()]) for _ in range(3)])
    contract = deepcopy(brief._contract())
    limits = contract['brief']['limits']
    if variation == 'file_cap': limits['result_files'] = 1
    if variation == 'row_cap': limits['diagnostic_rows'] = 1
    if variation == 'character_cap': limits['output_characters'] = 2048
    monkeypatch.setattr(brief, '_contract', lambda: contract)
    text = brief.render_eslint_brief(payload)
    assert len(text) <= limits['output_characters']
    assert 'never establishes cleanliness' in text
    assert text.endswith('.\n') and 'omitted diagnostic rows:' in text
    if variation == 'file_cap': assert 'Shown result files: 1/3; omitted result files: 2' in text
    if variation == 'row_cap': assert 'Shown diagnostic rows: 1/6; omitted diagnostic rows: 5' in text
    if variation == 'character_cap':
        assert 'a' * 100 not in text  # no partial identity truncation
        assert 'active[1]' in text


@pytest.mark.parametrize('variation', ['missing', 'unknown_key', 'bool_limit', 'too_small', 'version'])
def test_optional_policy_refusal_does_not_invent_defaults(tmp_path, monkeypatch, variation):
    payload = receipt(tmp_path)
    contract = deepcopy(brief._contract())
    if variation == 'missing': del contract['brief']
    elif variation == 'unknown_key': contract['brief']['limits']['unowned'] = 1
    elif variation == 'bool_limit': contract['brief']['limits']['result_files'] = True
    elif variation == 'too_small': contract['brief']['limits']['output_characters'] = 1
    elif variation == 'version': contract['brief']['version'] = 'v999'
    monkeypatch.setattr(brief, '_contract', lambda: contract)
    text = brief.render_eslint_brief(payload)
    assert 'ESLint brief unavailable:' in text and 'result[' not in text


def test_cli_brief_uses_exact_same_payload_preserves_default_and_never_runs_target(tmp_path, monkeypatch, capsys):
    from tools import ingest_native_run_logs as cli
    from tools.core import native_run_log_ingestion as ingestion
    target, bundle, manifest = _eslint_fixture(tmp_path)
    _eslint_run(target, bundle, manifest, [_eslint_result(target / '源.ts', [_eslint_message()])])
    calls, observed = [], []
    original_read, original_ingest, original_render = ingestion._read, cli.ingest_native_run_logs, brief.render_eslint_brief
    def read(path, *args):
        calls.append(path)
        return original_read(path, *args)
    def ingest(**kwargs):
        value = original_ingest(**kwargs)
        observed.append(value)
        return value
    def render(value):
        assert value is observed[-1]
        return original_render(value)
    def forbidden(*args, **kwargs): raise AssertionError('No target execution')
    monkeypatch.setattr(ingestion, '_read', read)
    monkeypatch.setattr(cli, 'ingest_native_run_logs', ingest)
    monkeypatch.setattr(brief, 'render_eslint_brief', render)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    output = tmp_path / 'receipt.json'
    argv = ['--target-root', str(target), '--bundle-root', str(bundle), '--manifest', 'manifest.json',
            '--diagnostic-format', _ESLINT_FORMAT, '--output', str(output)]
    assert cli.main(argv) == 0
    default_text = capsys.readouterr().out
    assert all(isinstance(json.loads(line), dict) for line in default_text.splitlines())
    assert '"active_diagnostics": 1' in default_text and 'result[' not in default_text
    default_receipt = json.loads(output.read_text(encoding='utf-8'))
    calls.clear()
    assert cli.main(argv + ['--format', 'brief']) == 0
    assert 'result[0]' in capsys.readouterr().out
    assert json.loads(output.read_text(encoding='utf-8')) == default_receipt
    assert calls.count(target / '源.ts') == calls.count(bundle / 'out.log') == calls.count(bundle / 'err.log') == 1


def test_cli_brief_retains_optional_atlas_lane_without_source_replay(tmp_path, capsys):
    from tools.ingest_native_run_logs import main
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
        '--diagnostic-format', _ESLINT_FORMAT, '--output', str(output), '--format', 'brief',
        '--atlas-raw-dir', str(raw), '--atlas-project', 'MAIN']) == 0
    text = capsys.readouterr().out
    assert 'result[0]' in text and '"lineage_status": "COMPLETE"' in text
    assert '"scope": "analysis_time_declared_source_text_only"' in text
    assert 'Historical execution: not_verified' in text


def test_brief_missing_format_or_ingestion_refusal_is_explicit(tmp_path):
    payload = receipt(tmp_path)
    del payload['diagnostic_observations']
    assert 'eslint_format_not_selected' in brief.render_eslint_brief(payload)
    child = tmp_path / 'failed'
    child.mkdir()
    target, bundle, manifest = _eslint_fixture(child)
    # Use the public producer directly, retaining a deliberately bad stream hash.
    manifest['stdout']['sha256'] = '0' * 64
    from tools.tests.test_native_run_log_ingestion import _run
    assert 'input_not_ingested' in brief.render_eslint_brief(_run(target, bundle, manifest, diagnostic_format=_ESLINT_FORMAT))


@pytest.mark.parametrize('path', ['./.github/work.ts', 'src/\u202eforged.ts'])
def test_brief_display_escaping_preserves_dotfile_and_bidi_identity(tmp_path, path):
    payload = receipt(tmp_path)
    payload['inputs']['source'][0]['path'] = path
    digest = payload_sha256(payload['inputs'])
    payload['identities']['input_observation_sha256'] = digest
    payload['diagnostic_observations']['input_observation_sha256'] = digest
    text = brief.render_eslint_brief(payload)
    if path.startswith('./'):
        assert 'declared_path=".github/work.ts"' in text
    else:
        assert '\u202e' not in text and 'declared_path="src/\\u202eforged.ts"' in text


@pytest.mark.parametrize('variation', ['no_format', 'reported_nonzero', 'bad_stream'])
def test_cli_brief_preserves_exit_and_no_format_refusal(tmp_path, capsys, variation):
    from tools.ingest_native_run_logs import main
    from tools.tests.test_native_run_log_ingestion import _run
    target, bundle, manifest = _eslint_fixture(tmp_path)
    _eslint_run(target, bundle, manifest, [_eslint_result(target / '源.ts', [_eslint_message()])])
    if variation == 'reported_nonzero': manifest['exit_code'] = 2
    if variation == 'bad_stream': manifest['stdout']['sha256'] = '0' * 64
    _run(target, bundle, manifest)
    args = ['--target-root', str(target), '--bundle-root', str(bundle), '--manifest', 'manifest.json',
            '--output', str(tmp_path / 'receipt.json'), '--format', 'brief']
    if variation != 'no_format': args += ['--diagnostic-format', _ESLINT_FORMAT]
    assert main(args) == (1 if variation == 'bad_stream' else 0)
    text = capsys.readouterr().out
    if variation == 'reported_nonzero':
        assert 'result[0]' in text and 'Historical execution: not_verified' in text
    else:
        assert 'ESLint brief unavailable:' in text and 'result[' not in text

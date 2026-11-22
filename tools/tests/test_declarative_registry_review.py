"""Explicit domain admission and exact source refusal, not target runtime tests."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tools.core import declarative_registry_review as review
from tools.core.unmanaged_atomic_io import native_filesystem_path


def _write(path, value):
    physical = Path(native_filesystem_path(path))
    physical.parent.mkdir(parents=True, exist_ok=True)
    physical.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8'))


def _fixture(root, data=None, *, raw=None, pointer='/genres', exceptions=None, source='src/genres.json'):
    if raw is None:
        raw = json.dumps(data if data is not None else {'genres': {'fiction': {'id': 'fiction'}}}, ensure_ascii=False).encode('utf-8')
    _write(root / source, raw)
    contract = {'version': 'v1-literal-json-key-id',
                'source': {'path': source, 'sha256': hashlib.sha256(raw).hexdigest(), 'pointer': pointer},
                'id_field': 'id', 'exceptions': exceptions or []}
    _write(root / 'contracts/genres.json', contract)
    return contract


def _run(root):
    return review.review_declarative_registry(root, 'contracts/genres.json')


def test_only_explicit_selected_map_is_checked_without_target_writes(tmp_path):
    contract = _fixture(tmp_path, {'genres': {'fiction': {'id': 'fiction'}}, 'unrelated': {'x': {'id': 'y'}}})
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    value = _run(tmp_path)
    assert value['status'] == 'checked_within_declared_scope'
    assert value['counts'] == {'matched': 1, 'mismatched': 0, 'excepted': 0, 'unresolved': 0}
    assert value['source_binding'] == 'pinned_bytes_rechecked'
    assert value['input_identity']['source_sha256'] == contract['source']['sha256']
    assert value['mutation_proposed'] is False and value['runtime_proof'] == 'not_performed'
    assert value['atlas_binding'] == 'not_claimed'
    assert before == {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_conflict_has_exact_unicode_escaped_pointer_and_raw_identity(tmp_path):
    raw = b'\xef\xbb\xbf' + '{\r\n "a/b": {"~type": {"\u00f6yk\u00fc/1": {"id": "other"}}}}'.encode('utf-8')
    _fixture(tmp_path, raw=raw, pointer='/a~1b/~0type')
    value = _run(tmp_path)
    assert value['status'] == 'review_required'
    assert value['rows'] == [{'kind': 'mismatched', 'key': 'öykü/1', 'observed_id': 'other', 'pointer': '/a~1b/~0type/öykü~11/id'}]
    assert value['input_identity']['source_sha256'] == hashlib.sha256(raw).hexdigest()


def test_exact_reasoned_separator_exception_is_visible_not_silently_clean(tmp_path):
    exception = {'key': 'separator', 'expected_id': '', 'reason': 'Intentional disabled separator'}
    _fixture(tmp_path, {'genres': {'separator': {'id': ''}, 'fiction': {'id': 'fiction'}}}, exceptions=[exception])
    value = _run(tmp_path)
    assert value['counts']['excepted'] == 1 and value['counts']['matched'] == 1
    assert value['rows'][0]['kind'] == 'excepted' and value['rows'][0]['reason'] == exception['reason']


@pytest.mark.parametrize('exception', [
    {'key': 'missing', 'expected_id': '', 'reason': 'Missing is not an exception'},
    {'key': 'x', 'expected_id': 'changed', 'reason': 'Stale value'},
    {'key': 'x', 'expected_id': 'x', 'reason': 'Redundant exception'},
    {'key': 'x', 'expected_id': 'y', 'reason': ''},
])
def test_stale_invalid_or_redundant_exception_refuses_entire_claim(tmp_path, exception):
    _fixture(tmp_path, {'genres': {'x': {'id': 'x' if exception['expected_id'] == 'x' else 'y'}}}, exceptions=[exception])
    value = _run(tmp_path)
    assert value['status'] == 'unavailable' and value['rows'] == []


def test_duplicate_exception_is_refused(tmp_path):
    row = {'key': 'x', 'expected_id': 'y', 'reason': 'Explicit exception'}
    _fixture(tmp_path, {'genres': {'x': {'id': 'y'}}}, exceptions=[row, row])
    assert _run(tmp_path)['reason'] == 'target_exceptions_invalid'


@pytest.mark.parametrize('entity', [None, [], 3, {}, {'id': 3}, {'id': True}, {'id': None}])
def test_nonliteral_string_id_stays_unresolved_not_mismatch_or_clean(tmp_path, entity):
    _fixture(tmp_path, {'genres': {'x': entity, 'bad': {'id': 'other'}}})
    value = _run(tmp_path)
    assert value['status'] == 'incomplete_evidence'
    assert value['counts']['unresolved'] == value['counts']['mismatched'] == 1
    assert value['rows'][0]['pointer'] == '/genres/x'


@pytest.mark.parametrize('raw', [b'{"genres":{"x":{"id":"x"},"x":{"id":"y"}}}',
    b'{"genres":{"x":{"id":"x","id":"y"}}}', b'{"genres":{},"genres":{}}',
    b'{"genres":{},"unrelated":NaN}', b'{"genres":{},"unrelated":1e999}', b'export const genres = {};', b'\xff'])
def test_ambiguous_malformed_or_non_json_source_is_unavailable(tmp_path, raw):
    _fixture(tmp_path, raw=raw)
    assert _run(tmp_path)['status'] == 'unavailable'


@pytest.mark.parametrize('pointer', ['/missing', '/genres/0', '/genres~2bad', 'genres', '/array/0'])
def test_unknown_or_unsupported_pointer_is_not_observed_empty(tmp_path, pointer):
    _fixture(tmp_path, {'genres': {}, 'array': [{}]}, pointer=pointer)
    assert _run(tmp_path)['status'] == 'unavailable'


def test_root_map_and_empty_scope_are_explicit(tmp_path):
    _fixture(tmp_path, {'x': {'id': 'x'}}, pointer='')
    assert _run(tmp_path)['counts']['matched'] == 1
    _fixture(tmp_path, {}, pointer='')
    value = _run(tmp_path)
    assert value['status'] == 'empty_declared_registry' and value['registry_row_count'] == 0


def test_missing_declaration_never_triggers_object_discovery(tmp_path, monkeypatch):
    _write(tmp_path / 'src/genres.json', {'x': {'id': 'y'}})
    assert _run(tmp_path)['status'] == 'unavailable'
    assert not (tmp_path / 'contracts').exists()


def test_changed_source_is_not_silently_repinned(tmp_path):
    _fixture(tmp_path)
    _write(tmp_path / 'src/genres.json', {'genres': {'x': {'id': 'y'}}})
    assert _run(tmp_path)['reason'] == 'source_identity_mismatch'


@pytest.mark.parametrize('lane', ['source', 'contract', 'policy'])
def test_mid_review_change_withholds_all_observations(tmp_path, monkeypatch, lane):
    _fixture(tmp_path, {'genres': {'x': {'id': 'y'}}})
    original = review._compare
    original_policy = review._policy
    def changed(*args):
        value = original(*args)
        if lane == 'policy':
            policy = deepcopy(original_policy())
            policy['limits']['display_rows'] += 1
            monkeypatch.setattr(review, '_policy', lambda: policy)
        else:
            path = 'src/genres.json' if lane == 'source' else 'contracts/genres.json'
            _write(tmp_path / path, b'{}')
        return value
    monkeypatch.setattr(review, '_compare', changed)
    value = _run(tmp_path)
    assert value['status'] == 'unavailable' and value['reason'] == 'input_changed_during_review'
    assert value['rows'] == [] and value['source_binding'] == 'unavailable'


@pytest.mark.parametrize('path', ['../escape.json', '/abs.json', 'C:/outside.json', 'src\\genres.json', 'src/g.json:stream', 'src/../g.json'])
def test_source_path_refusal_uses_shared_confined_reader(tmp_path, path):
    contract = _fixture(tmp_path)
    contract['source']['path'] = path
    _write(tmp_path / 'contracts/genres.json', contract)
    value = _run(tmp_path)
    assert value['status'] == 'unavailable' and value['reason'] == 'patch_input_path_refused'


def test_hardlinked_file_is_refused(tmp_path):
    _fixture(tmp_path)
    os.link(tmp_path / 'src/genres.json', tmp_path / 'alias.json')
    assert _run(tmp_path)['reason'] == 'patch_input_not_regular_unlinked_file'


@pytest.mark.parametrize('root', ['', '.'])
def test_no_ambient_target_root(root):
    assert review.review_declarative_registry(root, 'contract.json')['reason'] == 'explicit_absolute_target_root_required'


def test_no_target_code_execution_or_typescript_fallback(tmp_path):
    contract = _fixture(tmp_path, raw=b'globalThis.touched = true;', source='src/genres.ts')
    value = _run(tmp_path)
    assert value['reason'] == 'source_format_not_supported'
    assert set(tmp_path.iterdir()) == {tmp_path / 'src', tmp_path / 'contracts'}
    assert contract['source']['path'].endswith('.ts')


def test_all_rows_checked_before_bounded_display(tmp_path):
    _fixture(tmp_path, {'genres': {str(i): {'id': 'wrong'} for i in range(60)}})
    value = _run(tmp_path)
    assert value['counts']['mismatched'] == 60
    assert value['shown'] == 50 and value['display_omitted'] == 10


def test_whole_rows_fit_byte_budget_without_truncating_source_pointers(tmp_path):
    prefix = '😀' * 240
    _fixture(tmp_path, {'genres': {prefix + str(i): {'id': prefix + 'wrong'} for i in range(50)}})
    value = _run(tmp_path)
    assert value['counts']['mismatched'] == 50 and 0 < value['shown'] < 50
    assert value['display_omitted'] == 50 - value['shown']
    assert len(json.dumps(value, ensure_ascii=False).encode('utf-8')) <= review._policy()['limits']['output_bytes']
    assert all(row['pointer'] == '/genres/' + row['key'] + '/id' for row in value['rows'])


@pytest.mark.parametrize('extra', [{'unexpected': True}, {'version': 'future'}, {'id_field': ''}, {'exceptions': None}])
def test_unknown_contract_dialect_cannot_supply_domain_authority(tmp_path, extra):
    contract = _fixture(tmp_path)
    _write(tmp_path / 'contracts/genres.json', {**contract, **extra})
    assert _run(tmp_path)['status'] == 'unavailable'


def test_duplicate_and_non_json_contract_are_refused(tmp_path):
    _fixture(tmp_path)
    _write(tmp_path / 'contracts/genres.json', b'{"version":"v1-literal-json-key-id","version":"future"}')
    assert _run(tmp_path)['reason'] == 'literal_json_invalid_or_too_deep'


@pytest.mark.parametrize('lane', ['rows', 'source_bytes', 'contract_bytes', 'depth', 'nodes'])
def test_operational_bounds_refuse_without_partial_clean_claim(tmp_path, lane):
    policy = review._policy()['limits']
    _fixture(tmp_path)
    if lane == 'rows':
        _fixture(tmp_path, {'genres': {str(i): {'id': str(i)} for i in range(policy['registry_rows'] + 1)}})
    elif lane == 'source_bytes':
        _fixture(tmp_path, raw=b' ' * (policy['source_bytes'] + 1))
    elif lane == 'contract_bytes':
        _write(tmp_path / 'contracts/genres.json', b' ' * (policy['contract_bytes'] + 1))
    elif lane == 'depth':
        _fixture(tmp_path, raw=b'{"genres":{},"deep":' + b'[' * 40 + b'0' + b']' * 40 + b'}')
    else:
        _fixture(tmp_path, {'genres': {}, 'large': [0] * (policy['json_nodes'] + 1)})
    value = _run(tmp_path)
    assert value['status'] == 'unavailable' and value['rows'] == []


def test_long_root_and_target_isolation(tmp_path):
    root = tmp_path / ('deep' * 25) / ('nested' * 18) / ('more' * 15)
    _fixture(root)
    _fixture(tmp_path / 'other', {'genres': {'x': {'id': 'y'}}})
    assert _run(root)['status'] == 'checked_within_declared_scope'
    assert _run(tmp_path / 'other')['status'] == 'review_required'


def test_policy_failure_has_no_fallback(tmp_path, monkeypatch):
    _fixture(tmp_path)
    path = tmp_path / 'policy.json'
    _write(path, {'version': 'wrong', 'limits': {}})
    monkeypatch.setattr(review, 'POLICY_PATH', path)
    assert _run(tmp_path)['reason'] == 'review_policy_invalid'


def test_cli_opt_in_is_read_only_and_legacy_mode_unchanged(tmp_path, monkeypatch, capsys):
    from tools import inspect_target
    _fixture(tmp_path, {'genres': {'x': {'id': 'y'}}})
    monkeypatch.setattr(inspect_target, 'write_inspection', lambda *args: pytest.fail('Registry mode cannot invoke the artifact writer'))
    monkeypatch.setattr(sys, 'argv', ['inspect_target', '--registry-contract', 'contracts/genres.json', '--target-root', str(tmp_path)])
    assert inspect_target.main() == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'review_required'
    monkeypatch.setattr(inspect_target, 'write_inspection', lambda kind, target: {'summary': {'legacy': [kind, target]}})
    monkeypatch.setattr(sys, 'argv', ['inspect_target', '--file', 'MAIN::src/a.ts'])
    assert inspect_target.main() == 0
    assert json.loads(capsys.readouterr().out) == {'legacy': ['file', 'MAIN::src/a.ts']}


@pytest.mark.parametrize('args', [['--registry-contract', 'c.json'], ['--file', 'src/a.ts', '--target-root', '/tmp'],
                                 ['--registry-contract', 'c.json', '--file', 'src/a.ts']])
def test_cli_refuses_missing_or_conflicting_scope(monkeypatch, args):
    from tools import inspect_target
    monkeypatch.setattr(sys, 'argv', ['inspect_target', *args])
    with pytest.raises(SystemExit) as raised:
        inspect_target.main()
    assert raised.value.code == 2

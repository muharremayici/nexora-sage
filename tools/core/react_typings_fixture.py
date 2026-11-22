"""Private shared fixture acquisition. No installation, execution or reuse authority."""
from __future__ import annotations

import base64
from copy import deepcopy
import gzip
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import tarfile
import time

from tools.core.json_syntax import loads_json_strict
from tools.core.owned_text_bridge import canonical


class ReactFixtureInputError(ValueError):
    """A bounded refusal code; never publish raw paths or decoder messages."""


def _require(condition, code):
    if not condition:
        raise ReactFixtureInputError(code)


def _path(root, value):
    _require(isinstance(value, str) and bool(value), 'fixture_path_invalid')
    pure = PurePosixPath(value)
    _require(not pure.is_absolute() and pure.as_posix() == value and '\\' not in value
             and ':' not in value and not set(pure.parts) & {'.', '..'}, 'fixture_path_invalid')
    path = root.joinpath(*pure.parts)
    for parent in [path, *path.parents]:
        if parent == root:
            break
        _require(not parent.is_symlink() and not getattr(parent, 'is_junction', lambda: False)(), 'fixture_path_link')
    _require(path.resolve().is_relative_to(root), 'fixture_path_escape')
    return path


def _read(root, value, limit, inputs, role, deadline):
    _require(deadline is None or time.monotonic() < deadline, 'fixture_deadline')
    path = _path(root, value)
    try:
        info = path.stat()
        _require(stat.S_ISREG(info.st_mode), 'fixture_not_regular')
        _require(info.st_size <= limit, 'fixture_file_budget')
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise ReactFixtureInputError('fixture_file_unavailable') from None
    _require(len(raw) <= limit, 'fixture_file_budget')
    inputs.append({'role': role, 'path': value, 'bytes': len(raw),
                   'sha256': hashlib.sha256(raw).hexdigest()})
    return raw


def _json(raw):
    try:
        value = loads_json_strict(raw.decode('utf-8-sig'))
        _require(isinstance(value, dict), 'fixture_json_object_required')
        return value
    except (ValueError, UnicodeError):
        raise ReactFixtureInputError('fixture_json_invalid') from None


def fixture_declaration(payload):
    """The existing distribution profile owns locators; the package policy owns pins."""
    _require(isinstance(payload, dict) and 'public_projection' not in payload, 'fixture_private_source_required')
    row = payload.get('test_execution', {}).get('fixture_prerequisites', {}).get('react_typings')
    fields = {'consumer_test_path', 'state_path', 'review_path', 'archive_root', 'policy_path',
              'policy_pointer', 'max_manifest_bytes', 'max_archive_bytes', 'max_tar_bytes', 'max_tar_members'}
    _require(isinstance(row, dict) and set(row) == fields, 'fixture_declaration_required')
    _require(all(type(row[k]) is int and row[k] > 0 for k in fields if k.startswith('max_')),
             'fixture_declaration_budget')
    _require(isinstance(row['policy_pointer'], list) and bool(row['policy_pointer'])
             and all(isinstance(k, str) and k for k in row['policy_pointer']), 'fixture_policy_pointer')
    for key in ('consumer_test_path', 'state_path', 'review_path', 'archive_root', 'policy_path'):
        _require(isinstance(row[key], str), 'fixture_declaration_path')
    return deepcopy(row)


def read_react_typings_fixture(root, *, payload=None, materialize=True, deadline=None):
    """Capture bounded input bytes once per call; never borrow an older observation.

    File confinement is checked logically, not a hostile-filesystem snapshot lock.
    The caller's deadline is checked between bounded reads; blocking OS IO is not
    claimed to be physically interruptible by this in-process reader.
    """
    root = Path(root).resolve()
    inputs = []
    if payload is None:
        # The source profile itself supplies the remaining acquisition limits.
        from tools.core.json_io import load_json_object_strict
        payload = load_json_object_strict(root / 'config/distribution_test_profiles.json')
    spec = fixture_declaration(payload)
    limit = spec['max_manifest_bytes']
    try:
        policy = _json(_read(root, spec['policy_path'], limit, inputs, 'package_policy', deadline))
        for key in spec['policy_pointer']:
            policy = policy[key]
        _require(isinstance(policy, dict), 'fixture_policy_invalid')
        pins = policy['pins']
        _require(isinstance(pins, dict) and bool(pins), 'fixture_policy_invalid')
        for key in ('max_files', 'max_bytes', 'max_file_bytes'):
            _require(type(policy[key]) is int and policy[key] > 0, 'fixture_policy_invalid')
        state = _json(_read(root, spec['state_path'], limit, inputs, 'inventory_state', deadline))
        report = _json(_read(root, spec['review_path'], limit, inputs, 'inventory_review', deadline))
        _require(state['pins'] == pins and set(state['packages']) == set(pins), 'fixture_pin_mismatch')
        rows = report['inventory']['files']
        _require(isinstance(rows, list) and len(rows) <= policy['max_files'], 'fixture_inventory_budget')
        inventory_hash = hashlib.sha256(canonical({'pins': state['pins'], 'inventory': rows}).encode()).hexdigest()
        _require(inventory_hash == policy['inventory_sha256'], 'fixture_inventory_mismatch')
        contents, identities = {}, {}
        total = 0
        for package, record in state['packages'].items():
            archive_path = _path(root, record['archive_path'])
            archive_root = _path(root, spec['archive_root'])
            _require(archive_path.is_relative_to(archive_root) and archive_path != archive_root,
                     'fixture_archive_escape')
            raw = _read(root, record['archive_path'], spec['max_archive_bytes'], inputs, 'package_archive', deadline)
            _require(hashlib.sha256(raw).hexdigest() == record['archive_sha256'], 'fixture_archive_hash')
            integrity = 'sha512-' + base64.b64encode(hashlib.sha512(raw).digest()).decode()
            _require(integrity == record['published_integrity'], 'fixture_archive_integrity')
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                tar = stream.read(spec['max_tar_bytes'] + 1)
            _require(len(tar) <= spec['max_tar_bytes'], 'fixture_tar_budget')
            with tarfile.open(fileobj=io.BytesIO(tar), mode='r:') as archive:
                members = archive.getmembers()
                _require(len(members) <= spec['max_tar_members'], 'fixture_member_budget')
                names = [m.name for m in members]
                _require(len(names) == len(set(names)), 'fixture_duplicate_member')
                _require(all((m.isfile() or m.isdir()) and not m.issparse() for m in members),
                         'fixture_nonregular_member')
                declared = record['members']
                _require(isinstance(declared, list) and len(declared) <= spec['max_tar_members'], 'fixture_member_budget')
                prefix = record['summary']['archive_root']
                _require(isinstance(prefix, str) and bool(prefix) and ':' not in prefix and '\\' not in prefix
                         and PurePosixPath(prefix).as_posix() == prefix and not PurePosixPath(prefix).is_absolute()
                         and not set(PurePosixPath(prefix).parts) & {'.', '..'}, 'fixture_member_path')
                expected_names = []
                for row in declared:
                    relative = row['path']
                    _require(isinstance(relative, str) and (not relative or (
                        PurePosixPath(relative).as_posix() == relative and not PurePosixPath(relative).is_absolute()
                        and not set(PurePosixPath(relative).parts) & {'.', '..'} and '\\' not in relative
                        and ':' not in relative)), 'fixture_member_path')
                    member_name = prefix + ('/' + relative if relative else '')
                    expected_names.append(member_name)
                    member = archive.getmember(member_name)
                    if row['kind'] == 'directory':
                        _require(member.isdir(), 'fixture_member_kind')
                        continue
                    _require(row['kind'] == 'regular' and member.isfile(), 'fixture_member_kind')
                    size = row['bytes']
                    _require(type(size) is int and 0 <= size <= policy['max_file_bytes']
                             and member.size == size, 'fixture_member_size')
                    data = archive.extractfile(member).read(size + 1)
                    _require(len(data) == size and hashlib.sha256(data).hexdigest() == row['raw_sha256'],
                             'fixture_member_hash')
                    key = package + '/' + relative
                    _require(key not in identities, 'fixture_duplicate_inventory')
                    identities[key] = (size, row['raw_sha256'])
                    total += size
                    _require(len(identities) <= policy['max_files'] and total <= policy['max_bytes'],
                             'fixture_inventory_budget')
                    if materialize:
                        contents[key] = base64.b64encode(data).decode()
                _require(len(expected_names) == len(set(expected_names)) and set(names) == set(expected_names),
                         'fixture_member_inventory')
        expected = {row['package'] + '/' + row['path']: (row['bytes'], row['raw_sha256']) for row in rows}
        _require(len(expected) == len(rows) and identities == expected, 'fixture_inventory_members')
        wire = None
        if materialize:
            wire_rows = deepcopy(rows)
            for row in wire_rows:
                row['utf8Base64'] = contents[row['package'] + '/' + row['path']]
            wire = {'pins': deepcopy(pins), 'files': wire_rows}
        _require(deadline is None or time.monotonic() < deadline, 'fixture_deadline')
        return {'wire': wire, 'observation': {'status': 'FIXTURE_INPUTS_VERIFIED', 'inputs': inputs,
                'profile_sha256': hashlib.sha256(canonical(spec).encode()).hexdigest(),
                'inventory_sha256': inventory_hash, 'files': len(rows), 'bytes': total,
                'canonical_test_step_reuse': False, 'publication_authority': False}}
    except ReactFixtureInputError as exc:
        exc.inputs = deepcopy(inputs)
        raise
    except (KeyError, TypeError, ValueError, OSError, EOFError, tarfile.TarError):
        error = ReactFixtureInputError('fixture_bundle_invalid')
        error.inputs = deepcopy(inputs)
        raise error from None
